# Sequence Flow Modes (Serial, Parallel, Hybrid) Design

**Date:** 2026-11-03
**Status:** Approved in brainstorming, pending spec review

## Problem

The DAG engine can already run a saved sequence serially, in parallel, or as a
hybrid such as `A -> (B, C, D) -> E -> F`, because scheduling follows each step's
`depends_on`. Operators cannot select any of this as an option:

- The sequence editor has no flow mode or group concept. A hybrid means ticking
  "Runs after" checkboxes on every step.
- The API requires callers to wire `depends_on` by hand.
- The sequence UI cannot set concurrency. `saveSequence` sends no `defaults`, and
  neither the launch modal nor `SequenceLaunchRequest` accepts run settings, so
  runs get `parallel` with 4 workers unless `defaults.run_settings` was set through
  the API.
- The Launch tab's Execution Mode dropdown does not change job order.
  `normalize_to_dag` turns a plain job list into a chain, so "Parallel" still runs
  jobs one at a time.

Hybrid flows also expose defects in the executor, listed under Engine fixes.

## Goals

- Saved sequences can be declared as serial, parallel, or staged hybrid through
  both the web UI and the API.
- Concurrency for a saved sequence can be set at save time and overridden at launch.
- A hybrid run behaves correctly during holds, failures, and worker crashes.
- All tests for this work run in Docker.

## Non-goals

- Flow modes on the Launch tab or plain Job Selections. Their job lists keep
  running in listed order. A selection can already reference a saved sequence.
- Per-group concurrency limits or per-group failure policy. Groups do not exist at
  runtime, and `max_workers` applies to the whole run.
- Changing how blocked steps propagate (see Failure semantics).
- Any database migration.

## Design

### Principle

`depends_on` remains the only stored and executed form. Stages and flow modes are
an input and display layer, compiled on the server and derived back on read. The
scheduler, restart, schedules, and monitor continue to consume the same step list.

### API input

`ExecutionSequenceCreate`, `ExecutionSequenceVersionCreate`, and
`SequenceValidateRequest` accept exactly one of `steps` or `stages`. Sending both
or neither returns 422.

```json
{
  "name": "nightly",
  "stages": [
    {"mode": "serial",   "steps": [{"step_id": "a", "job_name": "extract"}]},
    {"mode": "parallel", "steps": [{"step_id": "b", "job_name": "load_x"},
                                   {"step_id": "c", "job_name": "load_y"}]},
    {"mode": "serial",   "steps": [{"step_id": "d", "job_name": "reconcile"},
                                   {"step_id": "e", "job_name": "publish"}]}
  ],
  "defaults": {"run_settings": {"max_workers": 4}}
}
```

- `steps` gains a sibling optional `flow_mode` of `serial` or `parallel`, which
  generates the edges. Any step that also sets a non-empty `depends_on` returns
  422. When `flow_mode` is omitted, edges are kept as given, so existing clients
  are unaffected.
- `flow_mode` and `stages` are mutually exclusive.
- A stage step has every `SequenceStepRef` field except `depends_on`. Sending
  `depends_on` in a stage step fails validation (`extra="forbid"`).
- A stage must contain at least one step.

### Compilation

New pure functions live in `api/services/sequence_validation.py`, keeping its
"no database, no HTTP" rule.

`compile_stages(stages) -> list[SequenceStepRef]`:

- Within a serial stage, each step depends on the step before it.
- Within a parallel stage, all steps share the same parents.
- A stage's entry steps depend on the previous stage's exit steps.
  - The entry step of a serial stage is its first step. Its exit step is its last.
  - Every step of a parallel stage is both an entry and an exit step.
- Output order is stage order, then declared order.

`compile_flow_mode(steps, mode)` is the same operation with one stage.

Example above compiles to: `b, c -> a`; `d -> b, c`; `e -> d`.

### Derivation

`derive_flow(steps) -> (flow_mode, stages | None)`:

1. Group steps by topological level, in declared order.
2. Build candidate stages: a level with more than one step is a parallel stage, and
   consecutive single-step levels merge into one serial stage.
3. Compile the candidates and compare edge sets with the stored steps. A mismatch
   means the graph is not stage-shaped, so `stages` is `null`.
4. Set `flow_mode`:
   - `serial` when the result is one serial stage. A single step is serial.
   - `parallel` when the result is one parallel stage.
   - `custom` otherwise, including every graph that is not stage-shaped.

`derive_flow` must not raise on a cyclic or dangling graph. It returns
`("custom", None)`, and `validate_steps` remains responsible for reporting errors.

### API output

- `ExecutionSequenceVersionOut` adds derived `flow_mode` and `stages`.
- `SequenceValidateResponse` adds compiled `steps`, `flow_mode`, and `stages`, so
  the editor renders server results and holds no compile logic of its own.
- Validation errors from compiled input use the same
  `{step_id, field, message}` shape.

### Concurrency settings

- `SequenceDefaults.run_settings` is validated against `RunSettings` when saved,
  returning 422, instead of failing first at launch.
- `SequenceLaunchRequest` adds `max_workers: int | None` (minimum 1). When set, it
  overrides the sequence default for that run, and 1 means one step at a time. The
  batch launch request accepts it too. The effective value is written to the run's
  `config_snapshot["run_settings"]`, so restart preserves it.
- The `execution_mode` field stays in `RunSettings` for API compatibility, and
  `sequential` still pins a run to one worker.

### Web UI

Sequence editor (`frontend/features/sequences.js` and its template):

- A Flow selector offers Serial, Parallel, and Custom.
- Serial and Parallel show the flat step list.
- Custom shows ordered stage cards, each with a serial/parallel toggle, its steps,
  add/remove/reorder controls, and a "Start this stage when" rule. The rule writes
  `trigger_rule` to the stage's entry steps. The first stage has no rule.
- An "Advanced graph" switch reveals the existing "Runs after" checkboxes and
  per-step trigger rule.
- A version with `stages: null` opens in Advanced graph. Moving it to stages shows
  a confirmation that its edges will be rewritten.
- A "Max parallel jobs" field saves to `defaults.run_settings.max_workers`. The
  save merges into the latest version's `defaults`, keeping `source_env`,
  `target_env`, `config_id`, and other run settings.
- The preview labels each level with more than one step as running together.
- Validation keeps the existing token guard against out-of-order responses.

Other views:

- The sequence detail view shows a flow badge.
- The launch modal adds an optional "Max parallel jobs" override, prefilled from
  the sequence default and sent for single and batch launches.
- The Launch tab's Execution Mode dropdown is replaced by a note that jobs run in
  listed order, with a link to Sequences. The launch payload is unchanged:
  `launchSettings.execution_mode` keeps its current value and is still sent. Max
  Workers remains, because it controls file-pair concurrency inside a job.
- `help-content.js` and `app-help.js` text is corrected to match.
- `frontend/index.html` is rebuilt after any partial edit, as the repo requires.

### Engine fixes

All in `api/services/dag_executor.py` and `api/services/run_executor.py`.

1. **Hold release is delayed by other branches.** `wait()` has no timeout, and a
   lone ready step runs inline even when another branch is held, so `_poll_holds`
   does not run until that step finishes.
   - `wait()` uses `hold_poll_interval` as its timeout. A timeout with nothing done
     returns to the loop.
   - A lone ready step runs inline only when `self._held` is also empty.
2. **Queued steps report RUNNING.** The loop marks every ready step `RUNNING` when
   submitting it, even if it is queued behind `max_workers`. It now submits at most
   `max_workers - len(in_flight)` steps per pass. The rest stay pending and are not
   marked. Marking stays on the coordinator thread, so no session crosses threads.
3. **A worker exception aborts the coordinator.** `future.result()` raises out of
   `run()`, which leaves other in-flight steps recorded as `RUNNING`.
   - `_run_dag_step` catches exceptions and returns an `ERROR` outcome with a real
     `TestCaseState`, persisted like any other result.
   - A job missing from the index gets the same treatment. Today it returns
     `state=None`, which leaves it out of the pass/fail count, so a run could finish
     `PASSED`.
   - The coordinator also converts any exception from `future.result()` into an
     `ERROR` outcome as a backstop. The backstop must leave the run non-`PASSED`.
4. **Job-level dependencies are not enforced between siblings.**
   `_validate_dependencies` compares flat positions, so siblings pass and run
   concurrently. The new rule: when job B lists job A in `depends_on` and A is in
   the sequence, every step running B must have a step running A among its
   ancestors. Plain-list runs are chains, so every ordering valid today stays valid.
   The rule is looser in one case: a list such as `[A, B, A]` was rejected and is now
   accepted, because B does have an earlier A.
   - The launch check raises as it does today, and the run ends `ERROR`.
   - `validate_steps` gains an optional job-dependency input, so the editor and save
     endpoints report the same issue before launch. External dependencies stay
     informational.

### Failure semantics

The next stage's entry steps use their existing `trigger_rule`, defaulting to
`all_success`. No new field is added.

| Group outcome | Next stage with `all_success` | With `all_done` | With `any_success` |
|---|---|---|---|
| All members passed | Runs | Runs | Runs |
| A member ran and failed | Blocked | Runs | Runs if another member passed |
| A member was blocked or cancelled (never ran) | Blocked | Blocked | Blocked |

The last row is existing, deliberate behaviour (`DagExecutor._decide`) and is kept.
The editor explains it next to the "Start this stage when" rule. A held member
stops the next stage until it is released, which is also existing behaviour.

## Behaviour changes

1. An existing sequence with job-level dependent jobs as parallel siblings now fails
   at launch instead of running them concurrently. Fixing it requires a new version
   with the edge added.
2. The Launch tab loses a dropdown that never changed job order.
3. Steps waiting behind `max_workers` show `PENDING` rather than `RUNNING`.
4. A step whose job crashes or no longer exists now counts as `ERROR` in the run's
   totals.

## Testing

All tests run in Docker. The repo has no application or test image today, and
`docker-compose.integration.yml` only provides backends.

New files:

- `Dockerfile.test`: Python image with the repo's dependencies and pytest.
- `docker-compose.test.yml`:
  - `test-unit` runs pytest.
  - `app` serves the API and frontend.
  - `test-e2e` uses the official Playwright image matching the repo's
    `@playwright/test` version, targeting `app` over the Compose network.

The Playwright config must be able to skip its local `webServer` and take a base
URL from the environment. The existing suite is run in Docker first to record a
baseline, so container-only failures are not attributed to this work.

Tests:

- **Compiler and derivation (unit):** each mode; stage boundaries in all four
  serial/parallel combinations; compile-derive round trips; non-stage-shaped graphs;
  cyclic and dangling input not raising.
- **Schema and routes (unit):** `steps`/`stages`/`flow_mode` exclusivity;
  `depends_on` rejected in stages and with `flow_mode`; invalid `run_settings`
  rejected at save; `max_workers` override and its snapshot; existing `steps`-only
  payloads unchanged; `stages` response fields.
- **Engine (unit, written failing first):** a release takes effect while another
  branch runs; a lone ready step does not block hold polling; submissions never
  exceed `max_workers`; waiting steps are not marked `RUNNING`; a worker exception
  leaves other steps recorded and the run non-`PASSED`; a missing job counts as
  `ERROR`; job dependency between siblings is rejected and a real ancestor accepted.
- **End to end through the API (integration):** a serial -> parallel -> serial run
  with step timestamps proving parallel members overlap and neither neighbouring
  stage does; `max_workers=1` removing the overlap; each row of the failure table;
  restart of a failed hybrid keeping its edges and `max_workers`.
- **Web UI (Playwright):** build a hybrid with stage cards, save, reopen with the
  stages intact, launch with an override, and check the monitor; a non-stage graph
  opening in Advanced graph with the rewrite confirmation; the Launch tab note.

## Files affected

- `api/schemas.py`
- `api/services/sequence_validation.py`
- `api/services/dag_executor.py`
- `api/services/run_executor.py`
- `api/routes/sequences.py`
- `frontend/features/sequences.js`
- Sequence and launch templates, plus rebuilt `frontend/index.html`
- `frontend/help-content.js`, `frontend/app-help.js`
- `playwright.config.*`
- New: `Dockerfile.test`, `docker-compose.test.yml`, and test files
- `README.md`: sequence flow modes and running tests in Docker
