# Restart From Failed Job — Design

**Goal:** When a run (ad-hoc job list, selection, or saved sequence) stops partway through — some steps FAILED/ERROR, others never ran because they were BLOCKED/CANCELLED behind the failure — let the user restart it without re-running the steps that already passed. Restart creates a **new run** that references the original: already-successful steps are carried forward as-is, and only the failed/never-run steps (and anything downstream of them) actually execute again.

**Tech stack:** Python (FastAPI, SQLAlchemy), existing `RunExecutor`/`DagExecutor` machinery, Alpine.js frontend.

---

## 1. Why a new run, not in-place

The alternative (reset the failed steps on the *same* `run_id` back to PENDING and re-run) was considered and rejected: it would mean overwriting `test_results` rows in place, losing the fact that the run failed at all, and would fight the existing `RunStep.attempt` column (already used for automatic per-step retries within one run). A new run_id keeps the original failure on the record as history, gives the restart its own timeline/webhooks/report, and composes cleanly with everything that already keys off `run_id` (reports, exports, the Compare tab, CI dashboards).

## 2. Where the DAG shape comes from

`config_snapshot["job_sequence"]` is **not** usable as the source of truth — for saved-sequence launches it only stores the flattened legacy view (`resolved.as_linear_steps()`, dropping `step_id`/`depends_on`/`trigger_rule`); the real DAG passed to `RunExecutor` is `resolved.steps` and is never written back to `config_snapshot`. The one place the full DAG shape survives is the **original run's own `run_steps` rows** — `materialize_steps()` already persists `step_id`, `depends_on`, `trigger_rule`, `on_failure`, `max_retries`, `hold_after`, `condition`, `wait_seconds` per step, for every launch path (ad-hoc, selection, sequence — `normalize_to_dag()` guarantees every run's steps carry these fields, even a plain chain). Restart reconstructs the `SequenceStepRef` list directly from the original run's `run_steps`, ordered by `step_index`.

(Gap: per-step `retry_delay_seconds` isn't persisted on `RunStep`, only the run-level `RunSettings.retry_delay_seconds` default is. A restarted step that had a per-step override falls back to the run-level default. Accepted as a minor, pre-existing fidelity gap — out of scope to fix here.)

## 3. Restart flow

New service module `api/services/run_restart.py`:

```python
def restart_run(db: Session, original_run_id: str) -> str:
    """Returns the new run_id. Raises RestartError for anything unrestartable."""
```

1. Load the original `TestRun` + its `run_steps` (ordered by `step_index`). 404 if the run doesn't exist.
2. Refuse (422) if:
   - original run status is PENDING or RUNNING (nothing to restart — it's still going),
   - no step is in a non-terminal-success state (FAILED, ERROR, BLOCKED, CANCELLED) — restarting a fully-passed run is a no-op the caller shouldn't be offered,
   - the original run has a `selection_id` and `RunRepository.has_active_run_for_selection(selection_id)` is true (mirrors the scheduler's existing overlap guard).
3. Reconstruct `steps: list[SequenceStepRef]` from the original `run_steps` rows.
4. New `run_id = uuid4()`. New `TestRun` row via `RunRepository.create_run(...)`, copying `source_env`/`target_env`/`config_snapshot`/`selection_id`/`selection_version` from the original, plus a new column `restarted_from_run_id = original_run_id`.
5. `RunStepRepository.materialize_steps(new_run_id, steps)` — all rows start PENDING, same as any run.
6. Classify each original step by its stored status:
   - **Carried over**: `PASSED`, `SLOW`, `SKIPPED`, `APPROVED` (a step that was HELD and released without re-running downstream logic).
   - **Re-run**: everything else (`FAILED`, `ERROR`, `BLOCKED`, `CANCELLED`, and — pragmatically — anything not in the carried-over set).
7. For each carried-over step:
   - Copy its `TestResult` row (and child `MismatchDetail` rows) to the new `run_id` (new `TestResult.id`, all other fields copied verbatim, `executed_at` unchanged so the UI can show it happened earlier). Matched to the original step by `job_name`, honoring `step_index` order when a job name repeats in the sequence (first not-yet-claimed match), same ambiguity the rest of the codebase already accepts for repeated job names in one run.
   - Update the new run's matching `RunStep` row directly (`RunStepRepository.update_status`) to the original status, plus a new `carried_over=True` flag — bypassing `DagExecutor` entirely for this step.
   - Build a `ParentOutcome(status=..., result=<reconstructed minimal result or None>)` and a `TestCaseState(name=job_name, status=TestStatus(status))` for it — needed so the executor's dependency/condition checks and final pass/fail counts treat it as done (§4).
8. Call `RunExecutor(..., job_sequence=steps, config_snapshot=copied_snapshot, carried_over_states=[...], seeded_outcomes={step_id: ParentOutcome}).execute()` via `background_tasks.add_task`, same dispatch pattern as `_execute_run`.
9. Preconditions (`sequence_preconditions.check_for_session`) are **not** re-evaluated — restart is an explicit manual recovery action, not a fresh scheduled/launch attempt.

## 4. `RunExecutor` / `DagExecutor` changes

Both gain small, backward-compatible additions (existing callers pass nothing and behave exactly as today):

- `DagExecutor.__init__(..., seeded: dict[str, ParentOutcome] | None = None)`: for each `step_id` in `seeded`, pre-populate `self._final[step_id] = outcome.status` and `self._outcomes[step_id] = outcome`, and exclude it from `self._pending`. This is enough for `_ready_steps()`/`_decide()` to evaluate downstream `depends_on`/`trigger_rule`/`condition` against carried-over parents exactly as if they'd just finished.
- `RunExecutor.execute(...)` gains `carried_over_states: list[TestCaseState] | None = None`. After `DagExecutor.run()` returns, `outcome.states = carried_over_states + outcome.states` before `_complete_run`/webhook firing, so `total_tests`/`passed`/`failed`/`slow`/`error` on the new run reflect the *whole* step set, not just what actually re-executed. (`outcome.results`, and therefore the metrics JSON log, only covers freshly-executed steps — carried-over `ReconciliationResult` objects aren't reconstructed for metrics; this is a best-effort log file, not the run record, so the gap is accepted.)

## 5. Data model

```python
# TestRun
restarted_from_run_id = Column(String(36), nullable=True, index=True)

# RunStep
carried_over = Column(Boolean, nullable=False, default=False)
```

No new table. Both columns nullable/defaulted, so existing rows are unaffected — plain `Base.metadata.create_all` / a lightweight `ensure_column` shim (matching how similar single-column additions were handled in prior specs, e.g. contract escalation columns) picks them up.

## 6. API

`POST /api/runs/{run_id}/restart` → `202`, body `{run_id: <new_id>, status: "PENDING", restarted_from_run_id: <original>}` (extends `RunStatusOut`). Mirrors `POST /api/runs/{run_id}/cancel`'s shape. 404 if original run not found; 422 with a clear reason for the refusal cases in §3 step 2.

`RunStatusOut`/`RunDetailOut` gain `restarted_from_run_id: str | None`. `RunStepOut` gains `carried_over: bool`.

`GET /api/runs/{run_id}` (run detail) response is otherwise unchanged — carried-over steps just show up as normal `PASSED` steps with a `carried_over` badge in the UI.

## 7. Frontend

Run Detail page: when `run.status` is `ERROR`/`BLOCKED`, or any step is `FAILED`/`CANCELLED`, show a **"Restart from failure"** button. Click → `POST .../restart` → navigate to the new run's detail page. Carried-over steps in the new run's step list get a small "carried over" tag instead of a duration, distinguishing them from freshly re-executed ones. The original run's detail page gains a "Restarted as `<new_run_id>`" link once a restart exists (reverse lookup by `restarted_from_run_id`).

## 8. Error handling

- Restart requested on a still-running/pending run → 422, "run is still in progress."
- Restart requested on a fully-passed run → 422, "nothing to restart."
- Restart requested while the same selection has an active run → 422, matching the scheduler's existing overlap message style.
- A step's job was deleted/disabled since the original run → same behavior as today's fresh launch: `_build_case`/`_build_jobs_index` yields no job, step outcome is `ERROR`. Not special-cased for restart.
- Restart is not guarded against a double-click/race creating two restarts of the same original run — both would succeed independently, each a normal (if redundant) recovery attempt. Accepted as harmless; not worth the extra locking for v1.

## 9. Testing

- `tests/unit/test_dag_executor.py` (or a new `_seeded` section): seeded steps are excluded from execution, still gate downstream `_decide()`/condition checks correctly, and DagOutcome states are unaffected by them (since carried-over states are added outside DagExecutor).
- `tests/unit/test_run_restart.py`: classification of carried-over vs re-run steps from a fixture `run_steps` set; TestResult/MismatchDetail copy correctness; refusal cases (still running, nothing to restart, active-selection overlap); `restarted_from_run_id` stamped correctly.
- `tests/unit/test_runs_routes.py`: new `POST /{run_id}/restart` route — happy path (202, new run_id, background task dispatched), 404, 422 cases.
- `tests/e2e/`: a new spec exercising "fail a 2-step sequence on step 2 → restart → step 1 shows carried-over, step 2 re-executes and passes."

## 10. Out of scope (explicitly deferred)

- Restarting from an arbitrary *specific* step chosen by the user (v1 always restarts "everything not successful"; picking a different resume point is a natural follow-up but not requested here).
- Re-checking preconditions on restart.
- Reconstructing full `ReconciliationResult` objects for carried-over steps in the metrics JSON log.
- A restart limit/cap on how many times a run can be restarted (each restart just points at its immediate parent; chained restarts are allowed, unbounded).
