# Restart Failed Sequence Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a restart-from-failure action that creates a new run, carries successful prior steps forward, and executes only failed or blocked work.

**Architecture:** Persist restart provenance and carried-over step metadata, seed the DAG executor with copied parent outcomes, and expose a restart endpoint plus detail UI affordances. The service reconstructs the DAG from original `run_steps` rows because the full DAG is not preserved in `config_snapshot` for all launch paths.

**Tech Stack:** Python, FastAPI, SQLAlchemy, existing `RunExecutor`/`DagExecutor`, Alpine.js, pytest.

## Global Constraints

- Restart always creates a new `run_id`; it never mutates the failed run in place.
- Full DAG reconstruction uses original `run_steps` ordered by `step_index`.
- Carried-over statuses are `PASSED`, `SLOW`, `SKIPPED`, and `APPROVED`.
- Restart refuses pending/running runs, fully successful runs, missing runs, and active selection overlap.
- Preconditions are not re-evaluated on restart.

---

### Task 1: Persistence And Schemas

**Files:**
- Modify: `etl_framework/repository/models.py`
- Modify: `etl_framework/repository/database.py`
- Modify: `etl_framework/repository/repository.py`
- Modify: `api/schemas.py`
- Test: `tests/unit/test_repository.py`

**Interfaces:**
- Produces: `TestRun.restarted_from_run_id`, `RunStep.carried_over`, `RunRepository.find_restart_for_run(original_run_id)`, `RunRepository.set_run_batch_id(run_id, batch_id)` remains reserved for batch work.

- [ ] Add failing tests for restart columns and reverse lookup.
- [ ] Run `python -m pytest tests/unit/test_repository.py -q` and confirm failure.
- [ ] Add ORM columns and SQLite `ensure_column` calls.
- [ ] Extend run and step response schemas.
- [ ] Add repository reverse lookup.
- [ ] Re-run `python -m pytest tests/unit/test_repository.py -q`.

### Task 2: DAG And Run Executor Seeding

**Files:**
- Modify: `api/services/dag_executor.py`
- Modify: `api/services/run_executor.py`
- Test: `tests/unit/test_dag_executor.py`

**Interfaces:**
- Consumes: `ParentOutcome` from `api.services.sequence_conditions`.
- Produces: `DagExecutor(..., seeded={step_id: ParentOutcome})`, `RunExecutor(..., carried_over_states=[...], seeded_outcomes={...})`.

- [ ] Add failing DAG test proving seeded parents are not executed but unblock children.
- [ ] Add failing run executor test or focused unit coverage for carried-over state aggregation.
- [ ] Run targeted tests and confirm failures.
- [ ] Implement seeded constructor behavior and run outcome state prepending.
- [ ] Re-run targeted tests.

### Task 3: Restart Service And Route

**Files:**
- Create: `api/services/run_restart.py`
- Modify: `api/routes/runs.py`
- Test: `tests/unit/test_run_restart.py`
- Test: `tests/unit/test_runs_routes.py`

**Interfaces:**
- Produces: `restart_run(db, original_run_id) -> RestartPlan`, `POST /api/runs/{run_id}/restart`.

- [ ] Add failing service tests for refusal cases, copied results, copied mismatches, and provenance.
- [ ] Add failing route tests for 202, 404, and 422.
- [ ] Implement service DAG reconstruction from `run_steps`.
- [ ] Copy `TestResult` and `MismatchDetail` rows for carried-over steps.
- [ ] Materialize new steps, mark carried-over steps, seed outcomes, dispatch `_execute_run` with seed args.
- [ ] Re-run targeted tests.

### Task 4: Restart UI

**Files:**
- Modify: `frontend/features/history.js`
- Modify: `frontend/partials/tab-history.html`

**Interfaces:**
- Consumes: `restarted_from_run_id`, `carried_over`, `POST /api/runs/{run_id}/restart`.

- [ ] Add UI action to show `Restart from failure` on failed/blocking runs.
- [ ] Navigate to the new run detail after 202 response.
- [ ] Show `carried over` tag for copied steps.
- [ ] Show original run restart link when available.

### Task 5: Verification

**Files:**
- No production edits.

- [ ] Run `python -m pytest tests/unit/test_dag_executor.py tests/unit/test_run_restart.py tests/unit/test_runs_routes.py tests/unit/test_repository.py -q`.
- [ ] Run affected frontend smoke or route tests if available.
- [ ] Inspect changed files for unrelated modifications.
