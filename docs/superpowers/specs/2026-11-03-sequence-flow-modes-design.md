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

- A schedule that targets a Job Selection backed by a sequence ignores the
  selection's `sequence_ref`. `api/services/scheduler.py` reads only
  `version.job_sequence`, which is empty for those selections, so the run has no
  steps. Selection launch handles this correctly.

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
- Any database migration. This leaves two existing limits in place:
  - `run_steps` has no start or end timestamps, so the UI cannot show step timing.
  - Restart loses a step's `retry_delay_seconds`, because `run_steps` has no column
    for it. The restarted step uses the run-level delay. This needs a separate fix.

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
- In `stages` or `flow_mode` input, a step with no parents cannot use
  `trigger_rule: all_failed`, because `trigger_fires` would never run it. This
  returns a validation issue. Raw `steps` input without `flow_mode` is not changed.

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

- `defaults.run_settings` is validated against `RunSettings` in the create and
  new-version routes, returning 422, instead of failing first at launch. The check
  must not go on the `SequenceDefaults` model, because that model also parses stored
  versions on read and at launch, and an old invalid value would then break them.
- `SequenceLaunchRequest` adds `max_workers: int | None` (minimum 1). When set, it
  overrides the sequence default for that run, and 1 means one step at a time.
  `SequenceBatchLaunchRequest` inherits it. The effective value is written to the
  run's `config_snapshot["run_settings"]`, so restart preserves it.
- The `execution_mode` field stays in `RunSettings` for API compatibility, and
  `sequential` still pins a run to one worker. It takes precedence over any
  `max_workers`, including the launch override.

Which settings apply depends on how the sequence is started:

| Started by | Run settings used | Launch override |
|---|---|---|
| Direct launch or batch | Sequence `defaults.run_settings` | `max_workers` |
| Schedule on the sequence | Sequence `defaults.run_settings` | None |
| Selection launch with `sequence_ref` | The selection's own `run_settings` | None |
| Schedule on that selection | The selection's own `run_settings` | None |

Selection settings win because selection launch already works that way and a
selection stores a full settings object, so its values cannot be told apart from
defaults.

### Scheduling sequence-backed selections

The selection branch of `api/services/scheduler.py` resolves `version.sequence_ref`
the same way selection launch does: resolve the sequence, check its preconditions,
and execute the resolved graph. A resolution failure or unmet precondition records
the same skipped events as a schedule on a sequence. The selection's active-run
guard is kept. `_resolve_and_validate` in `api/routes/schedules.py` validates the
resolved steps for these selections when the schedule is saved.

### Web UI

Sequence editor (`frontend/features/sequences.js`, `frontend/partials/tab-sequences.html`):

- A Flow selector offers Serial, Parallel, and Custom.
- Serial and Parallel show the flat step list. Serial shows each step's trigger rule
  except the first. The current template hides that rule when `depends_on` is empty,
  which would hide it for every step before compilation, so the condition changes.
- In these modes and stage cards, the editor removes `depends_on` from the steps it
  sends, because the API rejects it there. The preview reads the compiled `steps`
  returned by validate, not the editor's draft.
- Custom shows ordered stage cards, each with a serial/parallel toggle, its steps,
  add/remove/reorder controls, and a "Start this stage when" rule. The rule writes
  `trigger_rule` to the stage's entry steps. The first stage has no rule.
- An "Advanced graph" switch reveals the existing "Runs after" checkboxes and
  per-step trigger rule.
- A version with `stages: null` opens in Advanced graph. Moving it to stages shows
  a confirmation that its edges will be rewritten.
- A "Max parallel jobs" field saves to `defaults.run_settings.max_workers`. The
  save merges into the latest version's `defaults`, keeping `source_env`,
  `target_env`, `config_id`, and other run settings. If the stored `execution_mode`
  is `sequential`, the field is disabled with a note that the run is pinned to one
  worker.
- The preview labels each level with more than one step as running together.
- Validation keeps the existing token guard against out-of-order responses.

Other views:

- The sequence detail view shows a flow badge.
- The launch modal adds an optional "Max parallel jobs" override. Left blank, it
  sends nothing and the sequence default applies. It applies to single and batch
  launches.
- The Launch tab's Execution Mode dropdown (`frontend/partials/tab-launch.html`) is
  replaced by a note that jobs run in listed order, with a link to Sequences. Max
  Workers remains, because it controls file-pair concurrency inside a job.
- `frontend/features/launch.js` stops sending `execution_mode` and stops restoring it
  from saved session settings. Otherwise a stale `sequential` value, with no visible
  control left, could be saved into a selection and silently pin a sequence-backed
  run to one worker. Selections that already store a value keep it.
- `help-content.js` and `app-help.js` text is corrected to match.
- Run `node scripts/build-html.js` after any partial edit, because the server serves
  the generated `frontend/index.html`.

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
     `TestCaseState`. It attempts to persist the result, and a persistence failure
     does not lose the state.
   - A job missing from the index gets the same treatment. Today it returns
     `state=None`, which leaves it out of the pass/fail count, so a run could finish
     `PASSED`.
   - As a backstop, the coordinator converts an exception from a step, on the pool or
     inline, into an `ERROR` outcome and sets a new `DagOutcome.step_crashed` flag.
     `DagExecutor` knows nothing about jobs, so it cannot build a state.
     `RunExecutor` ends a run with that flag as `ERROR`.
4. **Job-level dependencies are not enforced between siblings.**
   `_validate_dependencies` compares flat positions, so siblings pass and run
   concurrently. The new rule: when job B lists job A in `depends_on` and A is in
   the sequence, every step running B must have a step running A among its
   ancestors. Plain-list runs are chains, so every ordering valid today stays valid.
   The rule is looser in one case: a list such as `[A, B, A]` was rejected and is now
   accepted, because B does have an earlier A.
   - The launch check works on the graph (`self._dag_steps`) rather than flat
     positions. It raises as it does today, and the run ends `ERROR`.
   - `validate_steps` gains an optional job-dependency input, so the editor and save
     endpoints report the same issue before launch. External dependencies stay
     informational.

### Failure semantics

The next stage's entry steps use their existing `trigger_rule`, defaulting to
`all_success`. No new field is added.

| Group outcome | Next stage with `all_success` | With `all_done` | With `any_success` |
|---|---|---|---|
| All members `PASSED` | Runs | Runs | Runs |
| A member ended `FAILED` or `ERROR` | Blocked | Runs | Runs if another member `PASSED` |
| A member ended `SLOW` or hold-skipped | Blocked | Runs | Runs if another member `PASSED` |
| A member was blocked or cancelled (never ran) | Blocked | Blocked | Blocked |

All of this is existing behaviour and is kept:

- With no `condition`, a parent satisfies a rule only when its status is `PASSED`, so
  a `SLOW` member blocks `all_success`. A step can accept `SLOW` by setting
  `condition.require_status`. A step's `condition` applies to each of its parents.
- A never-ran member blocks the next stage whatever its rule (`DagExecutor._decide`).
  The whole subtree is marked immediately, even while other members are running.
- A held member stops the next stage until it is released.
- `on_failure` is separate from the rules above:
  - `stop` cancels the run. Members already running finish, and steps not yet started
    become `CANCELLED`.
  - `continue` keeps the failure out of the run's totals, but the member's status is
    still `FAILED`, so `all_success` still blocks the next stage.

The editor explains the `SLOW`, never-ran, and `continue` cases next to the "Start
this stage when" rule.

Serial flows do not behave like plain job lists after a failure. A plain list step
without a `condition` gets `all_done` and carries on, while a serial flow defaults to
`all_success`, so later steps are blocked after the first failed step. A serial step
can select `all_done` to carry on.

## Behaviour changes

1. An existing sequence with job-level dependent jobs as parallel siblings now fails
   at launch instead of running them concurrently. Fixing it requires a new version
   with the edge added.
2. The Launch tab loses a dropdown that never changed job order.
3. Steps waiting behind `max_workers` show `PENDING` rather than `RUNNING`.
4. A step whose job crashes or no longer exists now counts as `ERROR` in the run's
   totals.
5. A schedule on a sequence-backed selection now runs the sequence, where before it
   ran no steps.
6. The UI no longer sends `execution_mode` from the Launch tab. A plain job list runs
   the same either way.

## Testing

All tests run in Docker. The repo has no application or test image today, and
`docker-compose.integration.yml` only provides backends.

New files:

- `Dockerfile.test`: one image with Python 3.11 or later, the repo's dependencies
  including the system libraries `pyodbc` needs, Node, and Playwright's Chromium. Node
  packages install with `npm ci`, so the Playwright version comes from
  `package-lock.json`.
- `.dockerignore`: excludes `node_modules`, `.claude`, `.kilo`, caches, and reports.
- `docker-compose.test.yml`:
  - `test-py` runs pytest.
  - `test-e2e` runs `node node_modules/@playwright/test/cli.js test` in the same
    image.
  - `test-py-postgres` runs the hybrid integration test against a `postgres` service.

`test-e2e` needs no separate app service or `playwright.config.ts` change. The
existing `webServer` block starts uvicorn inside the container on port 8055, with its
throwaway SQLite database, and the first auth-setup spec gets the fresh database it
requires.

The existing suite runs in Docker first to record a baseline, because the repo has
only run on a Windows host until now. Tests that fail in that baseline, such as
Windows-specific file transfer tests, are listed in the plan and not attributed to
this work. Suites needing live backends (`E2E_LIVE_BACKENDS` and the
`docker-compose.integration.yml` services) are out of scope, because this work does
not touch those integrations.

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
- **Scheduler (unit):** a schedule on a sequence-backed selection executes the
  resolved graph with the selection's run settings; an unresolvable sequence and an
  unmet precondition record skipped events; saving such a schedule validates the
  resolved steps.
- **End to end through the API (integration):** a serial -> parallel -> serial run
  using a stubbed step body, because `run_steps` has no timestamps. Parallel members
  wait on a `threading.Barrier` with a timeout, which can only pass if they run
  concurrently. Recorded enter and exit order proves neither neighbouring stage
  overlaps them. Also: `max_workers=1` removing the overlap; each failure table row;
  `on_failure` `stop` and `continue`; restart of a failed hybrid keeping its edges and
  `max_workers`. The test uses a file-backed SQLite database by default, and
  `test-py-postgres` repeats it on Postgres. No SQLite busy timeout is configured
  today, so a lock error under parallel writes is treated as a defect to fix here.
- **Web UI (Playwright):** build a hybrid with stage cards, save, reopen with the
  stages intact, launch with an override, and check the monitor; a non-stage graph
  opening in Advanced graph with the rewrite confirmation; the Launch tab note.
  `tests/e2e/37-session-settings-persist.spec.ts` uses the removed dropdown and moves
  to `#a11y-launch-max-workers`.

## Files affected

- `api/schemas.py`
- `api/services/sequence_validation.py`
- `api/services/dag_executor.py`
- `api/services/run_executor.py`
- `api/services/scheduler.py`
- `api/routes/sequences.py`, `api/routes/schedules.py`
- `frontend/features/sequences.js`, `frontend/features/launch.js`
- `frontend/partials/tab-sequences.html`, `frontend/partials/tab-launch.html`, plus
  regenerated `frontend/index.html`
- `frontend/help-content.js`, `frontend/app-help.js`
- `tests/e2e/37-session-settings-persist.spec.ts`
- New: `Dockerfile.test`, `.dockerignore`, `docker-compose.test.yml`, and test files
- `README.md`: sequence flow modes and running tests in Docker
