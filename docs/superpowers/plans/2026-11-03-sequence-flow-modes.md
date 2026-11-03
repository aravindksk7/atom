# Sequence Flow Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let saved sequences be declared serial, parallel, or staged hybrid through the API and web UI, make hybrid runs behave correctly, and run all tests in Docker.

**Architecture:** `depends_on` stays the only stored and executed form. A pure compiler in `api/services/sequence_validation.py` turns `stages` or `flow_mode` input into edges, and a matching derivation turns edges back into stages on read. `DagExecutor` keeps its scheduling model and gets four targeted fixes.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy, Alpine.js, Playwright, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-11-03-sequence-flow-modes-design.md`

---

## Working conventions

- Work in the worktree `C:\atom\.claude\worktrees\sequence-flow-modes`, branch `feat/sequence-flow-modes`.
- Run git through the PowerShell tool. The Bash tool's rtk hook refuses git inside worktrees.
- Write each commit message to a file in the scratchpad and run `git commit -F <file>`. Double quotes in `-m` here-strings break on PowerShell 5.1. End every message with this line:

  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

- Run every test in Docker. The commands below use these two forms:

  ```powershell
  docker compose -f docker-compose.test.yml run --rm test-py python -m pytest <path> -v
  docker compose -f docker-compose.test.yml run --rm test-e2e node node_modules/@playwright/test/cli.js test <spec>
  ```

- Subagents cannot wait in the background across turns, so tell them to run verification in the foreground. Full-suite runs that exceed the 10-minute tool limit are run by the controller with `run_in_background`.
- After editing any file in `frontend/partials/`, run `node scripts/build-html.js` in the `test-py` container and commit the regenerated `frontend/index.html`:

  ```powershell
  docker compose -f docker-compose.test.yml run --rm test-py node scripts/build-html.js
  ```

## File map

| File | Responsibility |
|---|---|
| `Dockerfile.test`, `.dockerignore`, `docker-compose.test.yml` | Docker test environment |
| `api/schemas.py` | Stage and flow input models, output fields, launch override |
| `api/services/sequence_validation.py` | Compile, derive, job-dependency rule |
| `api/routes/sequences.py` | Input compilation, derived output, defaults check, launch override |
| `api/services/scheduler.py`, `api/routes/schedules.py` | Sequence-backed selection schedules |
| `api/services/dag_executor.py` | Hold polling, worker slots, crash backstop |
| `api/services/run_executor.py` | Graph-based dependency check, step error handling |
| `frontend/features/sequences.js`, `frontend/partials/tab-sequences.html` | Flow editor, badge, launch override |
| `frontend/features/launch.js`, `frontend/partials/tab-launch.html`, `frontend/app.js` | Remove the Execution Mode control |
| `frontend/help-content.js`, `frontend/app-help.js`, `README.md` | Corrected text |

---

### Task 1: Docker test environment and baseline

**Files:**
- Create: `Dockerfile.test`
- Create: `.dockerignore`
- Create: `docker-compose.test.yml`
- Create: `docs/superpowers/plans/2026-11-03-docker-test-baseline.md`

- [ ] **Step 1: Write `Dockerfile.test`**

```dockerfile
# Test-only image: the repo has no application image. Source is bind-mounted by
# docker-compose.test.yml, so code edits do not need a rebuild.
FROM python:3.12-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends unixodbc nodejs npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt package.json package-lock.json ./
RUN pip install --no-cache-dir -r requirements.txt typer
RUN npm ci
RUN node node_modules/@playwright/test/cli.js install --with-deps chromium
```

- [ ] **Step 2: Write `.dockerignore`**

```
.git
.claude
.kilo
node_modules
**/__pycache__
.pytest_cache
.hypothesis
playwright-report
test-results
logs
reports
*.db
```

- [ ] **Step 3: Write `docker-compose.test.yml`**

```yaml
# All test runs go through this file. Nothing publishes a host port, so runs
# cannot collide with Windows excluded port ranges or a local dev server.
name: atom-test

x-test-base: &test-base
  build:
    context: .
    dockerfile: Dockerfile.test
  image: atom-test:local
  working_dir: /app
  volumes:
    - .:/app
    # Anonymous volume: keeps the image's Linux node_modules over the bind mount.
    - /app/node_modules

services:
  test-py:
    <<: *test-base
    environment:
      PYTHONPATH: /app
      PYTHONDONTWRITEBYTECODE: "1"
      # Keep SQLite off the Windows bind mount, where file locking is unreliable.
      ETL_DATABASE_URL: sqlite:////tmp/etl_test.db
    command: python -m pytest tests/unit tests/integration -q

  test-py-postgres:
    <<: *test-base
    environment:
      PYTHONPATH: /app
      PYTHONDONTWRITEBYTECODE: "1"
      ETL_DATABASE_URL: sqlite:////tmp/etl_test.db
      HYBRID_TEST_DATABASE_URL: postgresql+psycopg://atom:atom_test@postgres:5432/atom_test
    depends_on:
      postgres:
        condition: service_healthy
    command: python -m pytest tests/integration/test_sequence_hybrid_flow.py -v

  test-e2e:
    <<: *test-base
    environment:
      PYTHONPATH: /app
      PYTHONDONTWRITEBYTECODE: "1"
    # playwright.config.ts starts uvicorn inside this container on port 8055 with
    # a throwaway SQLite database.
    command: node node_modules/@playwright/test/cli.js test

  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: atom
      POSTGRES_PASSWORD: atom_test
      POSTGRES_DB: atom_test
    tmpfs:
      - /var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U atom -d atom_test"]
      interval: 3s
      timeout: 3s
      retries: 30
```

- [ ] **Step 4: Build the image**

Run: `docker compose -f docker-compose.test.yml build test-py`
Expected: build succeeds and ends with `Built` for `atom-test:local`.

- [ ] **Step 5: Smoke test one suite and one spec**

Run:
```powershell
docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_dag_executor.py tests/unit/test_sequence_validate_race.py -q
docker compose -f docker-compose.test.yml run --rm test-e2e node node_modules/@playwright/test/cli.js test tests/e2e/17-sequences.spec.ts
```
Expected: pytest reports all passed. Playwright passes the `setup` project and all of `17-sequences.spec.ts`. If either fails because of the image, fix the image and repeat before continuing.

- [ ] **Step 6: Record the Python baseline**

Run in the background, redirecting to the scratchpad:
```powershell
docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit tests/integration -q --tb=line > "$env:TEMP\claude\c--atom\317f6253-78ba-4c99-ba44-f2f3ac514461\scratchpad\baseline-py.txt"
```
Expected: the run finishes. Failures are allowed here and are the point of this step.

- [ ] **Step 7: Record the Playwright baseline**

Run in the background:
```powershell
docker compose -f docker-compose.test.yml run --rm test-e2e node node_modules/@playwright/test/cli.js test --reporter=line > "$env:TEMP\claude\c--atom\317f6253-78ba-4c99-ba44-f2f3ac514461\scratchpad\baseline-e2e.txt"
```
Expected: the run finishes. Live-backend specs skip because `E2E_LIVE_BACKENDS` is unset.

- [ ] **Step 8: Write `docs/superpowers/plans/2026-11-03-docker-test-baseline.md`**

Record the date, the base commit `b21bc68`, the pass/fail/skip totals of both runs, and every failing test ID with a one-line cause. Every later task compares against this list. A test on it is not a regression from this work; any other failure is.

- [ ] **Step 9: Commit**

```powershell
git add Dockerfile.test .dockerignore docker-compose.test.yml docs/superpowers/plans/2026-11-03-docker-test-baseline.md
git commit -F <file>   # test: add Docker test environment and record baseline
```

---

### Task 2: Stage and flow schemas

**Files:**
- Modify: `api/schemas.py:316-334` (`SequenceStepRef`), `:383-392` (`SequenceLaunchRequest`), `:414-470` (sequence bodies and responses)
- Test: `tests/unit/test_sequence_schemas.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_sequence_schemas.py`)

```python
def test_stage_step_rejects_depends_on():
    from pydantic import ValidationError
    from api.schemas import SequenceStage

    with pytest.raises(ValidationError):
        SequenceStage(mode="serial", steps=[
            {"step_id": "a", "job_name": "j", "depends_on": ["x"]},
        ])


def test_stage_requires_a_step():
    from pydantic import ValidationError
    from api.schemas import SequenceStage

    with pytest.raises(ValidationError):
        SequenceStage(mode="parallel", steps=[])


def test_flow_input_rejects_stages_with_steps():
    from pydantic import ValidationError
    from api.schemas import ExecutionSequenceCreate

    with pytest.raises(ValidationError):
        ExecutionSequenceCreate(
            name="n",
            steps=[{"step_id": "a", "job_name": "j"}],
            stages=[{"mode": "serial", "steps": [{"step_id": "b", "job_name": "j"}]}],
        )


def test_flow_input_rejects_stages_with_flow_mode():
    from pydantic import ValidationError
    from api.schemas import ExecutionSequenceVersionCreate

    with pytest.raises(ValidationError):
        ExecutionSequenceVersionCreate(
            flow_mode="serial",
            stages=[{"mode": "serial", "steps": [{"step_id": "b", "job_name": "j"}]}],
        )


def test_flow_input_rejects_empty_stages():
    from pydantic import ValidationError
    from api.schemas import ExecutionSequenceCreate

    with pytest.raises(ValidationError):
        ExecutionSequenceCreate(name="n", stages=[])


def test_flow_mode_rejects_depends_on():
    from pydantic import ValidationError
    from api.schemas import SequenceValidateRequest

    with pytest.raises(ValidationError):
        SequenceValidateRequest(flow_mode="parallel", steps=[
            {"step_id": "a", "job_name": "j"},
            {"step_id": "b", "job_name": "j", "depends_on": ["a"]},
        ])


def test_steps_without_flow_mode_keep_their_edges():
    from api.schemas import ExecutionSequenceCreate

    body = ExecutionSequenceCreate(name="n", steps=[
        {"step_id": "a", "job_name": "j"},
        {"step_id": "b", "job_name": "j", "depends_on": ["a"]},
    ])
    assert body.flow_mode is None and body.stages is None
    assert body.steps[1].depends_on == ["a"]


def test_launch_max_workers_must_be_positive():
    from pydantic import ValidationError
    from api.schemas import SequenceBatchLaunchRequest, SequenceLaunchRequest

    assert SequenceLaunchRequest().max_workers is None
    assert "max_workers" in SequenceBatchLaunchRequest.model_fields
    with pytest.raises(ValidationError):
        SequenceLaunchRequest(max_workers=0)
```

If the file does not import `pytest`, add `import pytest` at the top.

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequence_schemas.py -v`
Expected: the new tests FAIL with `ImportError` for `SequenceStage` or a missing field. Existing tests pass.

- [ ] **Step 3: Replace `SequenceStepRef` in `api/schemas.py` with these models**

```python
class SequenceStageStep(BaseModel):
    """A step as written inside a stage, where the stage supplies the edges."""
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=255)
    job_name: str = Field(min_length=1)
    trigger_rule: Literal["all_success", "all_done", "any_success", "all_failed"] = "all_success"
    hold_after: bool = False
    condition: StepCondition | None = None
    wait_seconds: int = Field(default=0, ge=0)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    retry_delay_seconds: float | None = Field(default=None, ge=0)
    on_failure: Literal["stop", "continue", "skip_downstream"] = "skip_downstream"


class SequenceStepRef(SequenceStageStep):
    """A step inside a saved sequence.

    step_id is separate from job_name because a DAG may run the same job more
    than once (a reconciliation before a load and again after it), so job_name
    cannot anchor a dependency edge.
    """
    depends_on: list[str] = Field(default_factory=list)


class SequenceStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["serial", "parallel"]
    steps: list[SequenceStageStep] = Field(min_length=1)


class SequenceFlowInput(BaseModel):
    """The ways a caller can describe a sequence's steps.

    Stages and flow_mode are compiled to depends_on before anything is stored.
    """
    steps: list[SequenceStepRef] = Field(default_factory=list)
    stages: list[SequenceStage] | None = None
    flow_mode: Literal["serial", "parallel"] | None = None

    @model_validator(mode="after")
    def check_flow_input(self) -> "SequenceFlowInput":
        if self.stages is not None:
            if self.steps or self.flow_mode:
                raise ValueError("Provide either stages or steps/flow_mode, not both")
            if not self.stages:
                raise ValueError("stages must contain at least one stage")
        if self.flow_mode and any(step.depends_on for step in self.steps):
            raise ValueError("depends_on cannot be combined with flow_mode")
        return self
```

- [ ] **Step 4: Update the launch request and the bodies and responses**

In `SequenceLaunchRequest`, add after `variable_overrides`:

```python
    max_workers: int | None = Field(default=None, ge=1)   # overrides the sequence default for this run
```

Replace these classes:

```python
class ExecutionSequenceVersionOut(BaseModel):
    version_number: int
    steps: list[SequenceStepRef]
    preconditions: SequencePrecondition | None = None
    defaults: SequenceDefaults = Field(default_factory=SequenceDefaults)
    created_at: datetime
    flow_mode: Literal["serial", "parallel", "custom"] = "custom"
    stages: list[SequenceStage] | None = None


class ExecutionSequenceCreate(SequenceFlowInput):
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    preconditions: SequencePrecondition | None = None
    defaults: SequenceDefaults = Field(default_factory=SequenceDefaults)


class ExecutionSequenceVersionCreate(SequenceFlowInput):
    preconditions: SequencePrecondition | None = None
    defaults: SequenceDefaults | None = None


class SequenceValidateRequest(SequenceFlowInput):
    preconditions: SequencePrecondition | None = None


class SequenceValidateResponse(BaseModel):
    ok: bool
    errors: list[SequenceValidationIssue] = Field(default_factory=list)
    order: list[str] = Field(default_factory=list)
    steps: list[SequenceStepRef] = Field(default_factory=list)
    flow_mode: Literal["serial", "parallel", "custom"] = "custom"
    stages: list[SequenceStage] | None = None
```

Keep `ExecutionSequenceUpdate` and `ExecutionSequenceOut` where they are. `SequenceStage` and `SequenceFlowInput` are defined earlier in the file, so no forward references are needed. `model_validator` is already imported, because `RunTrigger` uses it.

- [ ] **Step 5: Run to verify they pass, along with sequence callers**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequence_schemas.py tests/unit/test_sequences_routes.py tests/unit/test_sequence_resolver.py -v`
Expected: PASS. `test_create_rejects_empty_steps` still gets `field == "steps"`.

- [ ] **Step 6: Commit**

```powershell
git add api/schemas.py tests/unit/test_sequence_schemas.py
git commit -F <file>   # feat(sequences): add stage and flow mode schemas
```

---

### Task 3: Compile stages and derive flow

**Files:**
- Modify: `api/services/sequence_validation.py`
- Test: `tests/unit/test_sequence_flow.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Compiling stages and flow modes to depends_on, and deriving them back."""
from __future__ import annotations

import pytest

from api.schemas import SequenceFlowInput, SequenceStage, SequenceStepRef
from api.services.sequence_validation import (
    compile_flow_mode,
    compile_stages,
    derive_flow,
    steps_from_input,
)


def _stage(mode, *ids):
    return SequenceStage(mode=mode, steps=[{"step_id": i, "job_name": "j"} for i in ids])


def _step(step_id, *parents):
    return SequenceStepRef(step_id=step_id, job_name="j", depends_on=list(parents))


def _edges(steps):
    return {s.step_id: s.depends_on for s in steps}


def test_serial_stage_is_a_chain():
    assert _edges(compile_stages([_stage("serial", "a", "b", "c")])) == {
        "a": [], "b": ["a"], "c": ["b"],
    }


def test_parallel_stage_has_no_edges_among_members():
    assert _edges(compile_stages([_stage("parallel", "a", "b")])) == {"a": [], "b": []}


@pytest.mark.parametrize("first,second,expected", [
    ("serial", "serial", {"a": [], "b": ["a"], "c": ["b"], "d": ["c"]}),
    ("serial", "parallel", {"a": [], "b": ["a"], "c": ["b"], "d": ["b"]}),
    ("parallel", "serial", {"a": [], "b": [], "c": ["a", "b"], "d": ["c"]}),
    ("parallel", "parallel", {"a": [], "b": [], "c": ["a", "b"], "d": ["a", "b"]}),
])
def test_stage_boundaries(first, second, expected):
    steps = compile_stages([_stage(first, "a", "b"), _stage(second, "c", "d")])
    assert _edges(steps) == expected


def test_hybrid_serial_parallel_serial():
    steps = compile_stages([
        _stage("serial", "a"), _stage("parallel", "b", "c"), _stage("serial", "d", "e"),
    ])
    assert [s.step_id for s in steps] == ["a", "b", "c", "d", "e"]
    assert _edges(steps) == {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"], "e": ["d"]}


def test_compile_keeps_step_fields():
    stage = SequenceStage(mode="serial", steps=[
        {"step_id": "a", "job_name": "j", "hold_after": True, "on_failure": "stop",
         "condition": {"require_status": ["PASSED", "SLOW"]}},
    ])
    step = compile_stages([stage])[0]
    assert step.hold_after is True
    assert step.on_failure == "stop"
    assert step.condition.require_status == ["PASSED", "SLOW"]


def test_compile_flow_mode():
    steps = [_step("a"), _step("b")]
    assert _edges(compile_flow_mode(steps, "serial")) == {"a": [], "b": ["a"]}
    assert _edges(compile_flow_mode(steps, "parallel")) == {"a": [], "b": []}
    assert compile_flow_mode([], "serial") == []


@pytest.mark.parametrize("stages,flow_mode", [
    ([("serial", "a")], "serial"),
    ([("serial", "a", "b", "c")], "serial"),
    ([("parallel", "a", "b")], "parallel"),
    ([("serial", "a"), ("parallel", "b", "c"), ("serial", "d", "e")], "custom"),
    ([("parallel", "a", "b"), ("parallel", "c", "d")], "custom"),
])
def test_compile_derive_round_trip(stages, flow_mode):
    steps = compile_stages([_stage(s[0], *s[1:]) for s in stages])
    derived_mode, derived = derive_flow(steps)
    assert derived_mode == flow_mode
    assert _edges(compile_stages(derived)) == _edges(steps)
    assert [(st.mode, [x.step_id for x in st.steps]) for st in derived] == [
        (s[0], list(s[1:])) for s in stages
    ]


def test_derive_drops_depends_on_from_stage_steps():
    _, stages = derive_flow(compile_stages([_stage("serial", "a", "b")]))
    assert "depends_on" not in stages[0].steps[1].model_dump()


@pytest.mark.parametrize("steps", [
    # Two independent chains: levels look like stages but the edges differ.
    [_step("a"), _step("b", "a"), _step("c"), _step("d", "c")],
    # A step skipping a level.
    [_step("a"), _step("b", "a"), _step("c", "a", "b")],
    # A join on only one group member.
    [_step("a"), _step("b"), _step("c", "a")],
])
def test_graph_that_is_not_stage_shaped_derives_custom_without_stages(steps):
    assert derive_flow(steps) == ("custom", None)


@pytest.mark.parametrize("steps", [
    [_step("a", "b"), _step("b", "a")],     # cycle
    [_step("a", "ghost")],                  # dangling parent
    [_step("a"), _step("a")],               # duplicate id
    [],
])
def test_derive_never_raises_on_invalid_graphs(steps):
    assert derive_flow(steps) == ("custom", None)


def test_steps_from_input_compiles_stages():
    body = SequenceFlowInput(stages=[_stage("serial", "a", "b")])
    steps, issues = steps_from_input(body)
    assert _edges(steps) == {"a": [], "b": ["a"]}
    assert issues == []


def test_steps_from_input_applies_flow_mode():
    body = SequenceFlowInput(flow_mode="serial", steps=[_step("a"), _step("b")])
    steps, _ = steps_from_input(body)
    assert _edges(steps) == {"a": [], "b": ["a"]}


def test_steps_from_input_leaves_raw_steps_alone():
    raw = [_step("a"), _step("b", "a")]
    steps, issues = steps_from_input(SequenceFlowInput(steps=raw))
    assert _edges(steps) == {"a": [], "b": ["a"]}
    assert issues == []


def test_root_step_cannot_use_all_failed_in_compiled_input():
    body = SequenceFlowInput(stages=[SequenceStage(mode="serial", steps=[
        {"step_id": "a", "job_name": "j", "trigger_rule": "all_failed"},
    ])])
    _, issues = steps_from_input(body)
    assert issues == [{
        "step_id": "a", "field": "trigger_rule",
        "message": "Step 'a' has no parents, so 'all_failed' would never run it",
    }]


def test_root_all_failed_is_not_flagged_for_raw_steps():
    raw = SequenceStepRef(step_id="a", job_name="j", trigger_rule="all_failed")
    assert steps_from_input(SequenceFlowInput(steps=[raw]))[1] == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequence_flow.py -v`
Expected: collection FAILS with `ImportError: cannot import name 'compile_flow_mode'`.

- [ ] **Step 3: Refactor `topological_order` to share levels, in `api/services/sequence_validation.py`**

Replace `topological_order` with:

```python
def _topological_levels(steps: list["SequenceStepRef"]) -> list[list[str]]:
    """Group step_ids by depth, each level in declared order.

    Kahn's algorithm, so the same saved sequence always produces the same run
    order. A cycle or a parent that does not exist leaves steps that never
    become ready, which raises SequenceCycleError.
    """
    position = {s.step_id: i for i, s in enumerate(steps)}
    remaining = {s.step_id: set(s.depends_on) for s in steps}
    levels: list[list[str]] = []
    while remaining:
        ready = sorted(
            (sid for sid, deps in remaining.items() if not deps),
            key=lambda sid: position[sid],
        )
        if not ready:
            raise SequenceCycleError(list(remaining))
        levels.append(ready)
        for sid in ready:
            del remaining[sid]
        for deps in remaining.values():
            deps.difference_update(ready)
    return levels


def topological_order(steps: list["SequenceStepRef"]) -> list[str]:
    """Return step_ids parents-first."""
    return [sid for level in _topological_levels(steps) for sid in level]
```

- [ ] **Step 4: Add the compile and derive functions below `topological_order`**

```python
def compile_stages(stages: list["SequenceStage"]) -> list["SequenceStepRef"]:
    """Turn stages into steps with explicit edges, in stage then declared order.

    A stage's entry steps depend on the previous stage's exit steps. In a serial
    stage those are its first and last step; every step of a parallel stage is both.
    """
    from api.schemas import SequenceStepRef

    compiled: list[SequenceStepRef] = []
    exits: list[str] = []
    for stage in stages:
        if stage.mode == "parallel":
            for step in stage.steps:
                compiled.append(SequenceStepRef(**step.model_dump(), depends_on=list(exits)))
            exits = [step.step_id for step in stage.steps]
        else:
            parents = exits
            for step in stage.steps:
                compiled.append(SequenceStepRef(**step.model_dump(), depends_on=list(parents)))
                parents = [step.step_id]
            exits = parents
    return compiled


def _as_stage(mode: str, steps: list) -> "SequenceStage":
    from api.schemas import SequenceStage, SequenceStageStep

    return SequenceStage(
        mode=mode,
        steps=[SequenceStageStep(**s.model_dump(exclude={"depends_on"})) for s in steps],
    )


def compile_flow_mode(steps: list["SequenceStepRef"], mode: str) -> list["SequenceStepRef"]:
    return compile_stages([_as_stage(mode, steps)]) if steps else []


def steps_from_input(body: "SequenceFlowInput") -> tuple[list["SequenceStepRef"], list[dict]]:
    """Resolve a request body to its steps, plus issues only compiled input can have."""
    if body.stages is not None:
        steps = compile_stages(body.stages)
    elif body.flow_mode:
        steps = compile_flow_mode(body.steps, body.flow_mode)
    else:
        return list(body.steps), []
    issues = [
        _issue(s.step_id, "trigger_rule", f"Step '{s.step_id}' has no parents, so 'all_failed' would never run it")
        for s in steps
        if not s.depends_on and s.trigger_rule == "all_failed"
    ]
    return steps, issues


def derive_flow(steps: list["SequenceStepRef"]) -> tuple[str, list["SequenceStage"] | None]:
    """Describe stored steps as a flow mode and stages, for display and editing.

    A graph only counts as stage-shaped if compiling the derived stages gives back
    the same edges. Invalid graphs return ("custom", None): reporting them is
    validate_steps' job.
    """
    by_id = {s.step_id: s for s in steps}
    if not steps or len(by_id) != len(steps):
        return "custom", None
    try:
        levels = _topological_levels(steps)
    except SequenceCycleError:
        return "custom", None

    groups: list[tuple[str, list]] = []
    for level in levels:
        members = [by_id[sid] for sid in level]
        if len(members) > 1:
            groups.append(("parallel", members))
        elif groups and groups[-1][0] == "serial":
            groups[-1][1].extend(members)
        else:
            groups.append(("serial", members))
    stages = [_as_stage(mode, members) for mode, members in groups]

    edges = {s.step_id: set(s.depends_on) for s in steps}
    if {s.step_id: set(s.depends_on) for s in compile_stages(stages)} != edges:
        return "custom", None
    return (stages[0].mode if len(stages) == 1 else "custom"), stages
```

In the `TYPE_CHECKING` block at the top, import the new names:

```python
if TYPE_CHECKING:  # pragma: no cover
    from api.schemas import SequenceFlowInput, SequenceStage, SequenceStepRef
```

- [ ] **Step 5: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequence_flow.py tests/unit/test_sequence_validation.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add api/services/sequence_validation.py tests/unit/test_sequence_flow.py
git commit -F <file>   # feat(sequences): compile stages to edges and derive them back
```

---

### Task 4: Enforce job dependencies on the graph

**Files:**
- Modify: `api/services/sequence_validation.py` (`validate_steps`, new helper)
- Modify: `api/services/run_executor.py:280`, `:465-485`
- Test: `tests/unit/test_sequence_flow.py`, `tests/unit/test_run_executor_gates.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_sequence_flow.py`:

```python
def _job_step(step_id, job, *parents):
    return SequenceStepRef(step_id=step_id, job_name=job, depends_on=list(parents))


def test_job_dependency_between_siblings_is_a_violation():
    from api.services.sequence_validation import job_dependency_violations

    steps = [_job_step("a", "loader"), _job_step("b", "recon")]
    found = job_dependency_violations(steps, {"recon": ["loader"]})
    assert [(step.step_id, dep) for step, dep in found] == [("b", "loader")]


def test_job_dependency_on_a_real_ancestor_is_fine():
    from api.services.sequence_validation import job_dependency_violations

    steps = [_job_step("a", "loader"), _job_step("m", "mid", "a"), _job_step("b", "recon", "m")]
    assert job_dependency_violations(steps, {"recon": ["loader"]}) == []


def test_job_dependency_outside_the_sequence_is_informational():
    from api.services.sequence_validation import job_dependency_violations

    assert job_dependency_violations([_job_step("b", "recon")], {"recon": ["loader"]}) == []


def test_repeated_job_needs_an_earlier_run_of_its_dependency():
    from api.services.sequence_validation import job_dependency_violations

    # [A, B, A] is fine: B has an earlier A. [B, A, B] is not: the first B has none.
    ok = [_job_step("a1", "A"), _job_step("b", "B", "a1"), _job_step("a2", "A", "b")]
    assert job_dependency_violations(ok, {"B": ["A"]}) == []
    bad = [_job_step("b1", "B"), _job_step("a", "A", "b1"), _job_step("b2", "B", "a")]
    assert [s.step_id for s, _ in job_dependency_violations(bad, {"B": ["A"]})] == ["b1"]


def test_validate_steps_reports_job_dependency_issue():
    from api.services.sequence_validation import validate_steps

    steps = [_job_step("a", "loader"), _job_step("b", "recon")]
    errors = validate_steps(steps, ["loader", "recon"], {"recon": ["loader"]})
    assert errors == [{
        "step_id": "b", "field": "depends_on",
        "message": "Job 'recon' depends on 'loader' but step 'b' does not run after it. "
                   "Add the dependency so 'loader' runs first.",
    }]


def test_validate_steps_without_job_dependencies_is_unchanged():
    from api.services.sequence_validation import validate_steps

    steps = [_job_step("a", "loader"), _job_step("b", "recon")]
    assert validate_steps(steps, ["loader", "recon"]) == []
```

Append to `tests/unit/test_run_executor_gates.py`:

```python
def test_job_dependency_between_parallel_siblings_errors_before_any_step():
    from api.schemas import SequenceStepRef

    db = _session()
    RunRepository(db).create_run("run-sib", "dev", "prod", {})
    _make_job_with_deps(db, "loader", [])
    _make_job_with_deps(db, "recon", ["loader"])

    RunExecutor(
        db=db, run_id="run-sib", source_env="dev", target_env="prod",
        job_sequence=[
            SequenceStepRef(step_id="a", job_name="loader"),
            SequenceStepRef(step_id="b", job_name="recon"),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-sib")
    assert run.status == "ERROR"
    assert "depends on 'loader'" in run.results[0].error_message
    assert RunStepRepository(db).list_steps("run-sib") == []


def test_job_dependency_satisfied_by_an_edge_runs():
    from api.schemas import SequenceStepRef

    db = _session()
    RunRepository(db).create_run("run-edge", "dev", "prod", {})
    _make_job_with_deps(db, "loader", [])
    _make_job_with_deps(db, "recon", ["loader"])

    RunExecutor(
        db=db, run_id="run-edge", source_env="dev", target_env="prod",
        job_sequence=[
            SequenceStepRef(step_id="a", job_name="loader"),
            SequenceStepRef(step_id="b", job_name="recon", depends_on=["a"]),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    assert RunRepository(db).get_run("run-edge").status == "PASSED"
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequence_flow.py tests/unit/test_run_executor_gates.py -v`
Expected: new `job_dependency` tests FAIL with `ImportError`, `test_job_dependency_between_parallel_siblings_errors_before_any_step` FAILS because the run ends `PASSED`, and other tests pass.

- [ ] **Step 3: Add the helper and extend `validate_steps`**

Add above `validate_steps` in `api/services/sequence_validation.py`:

```python
def job_dependency_violations(
    steps: list["SequenceStepRef"], job_depends_on: dict[str, list[str]]
) -> list[tuple["SequenceStepRef", str]]:
    """Find (step, dependency) pairs where a job-level dependency has no ancestor.

    Only dependencies that are themselves in the sequence are enforced; others are
    informational. The steps must already be known to form a valid graph.
    """
    by_id = {s.step_id: s for s in steps}
    in_sequence = {s.job_name for s in steps}

    def ancestor_jobs(step) -> set[str]:
        jobs: set[str] = set()
        seen: set[str] = set()
        stack = list(step.depends_on)
        while stack:
            current = stack.pop()
            if current in seen or current not in by_id:
                continue
            seen.add(current)
            jobs.add(by_id[current].job_name)
            stack.extend(by_id[current].depends_on)
        return jobs

    violations = []
    for step in steps:
        wanted = [
            dep for dep in job_depends_on.get(step.job_name, [])
            if dep in in_sequence and dep != step.job_name
        ]
        if wanted:
            ancestors = ancestor_jobs(step)
            violations.extend((step, dep) for dep in wanted if dep not in ancestors)
    return violations
```

Replace the `validate_steps` signature and its final block:

```python
def validate_steps(
    steps: list["SequenceStepRef"],
    known_job_names: Iterable[str],
    job_depends_on: dict[str, list[str]] | None = None,
) -> list[dict]:
```

```python
    if not errors:
        try:
            topological_order(steps)
        except SequenceCycleError as exc:
            errors.append(_issue(None, "depends_on", str(exc).replace("Dependency cycle", "Dependency cycle detected")))

    if not errors and job_depends_on:
        for step, dep in job_dependency_violations(steps, job_depends_on):
            errors.append(_issue(
                step.step_id, "depends_on",
                f"Job '{step.job_name}' depends on '{dep}' but step '{step.step_id}' does not run "
                f"after it. Add the dependency so '{dep}' runs first.",
            ))

    return errors
```

- [ ] **Step 4: Move the executor check to the graph, in `api/services/run_executor.py`**

Change the call at line 280 to `self._validate_dependencies(jobs_index)` and replace the method:

```python
    def _validate_dependencies(self, jobs_index: dict[str, JobDefinition]) -> None:
        """Fail fast when a step could run before a job it depends on.

        Only dependencies that are themselves part of the sequence are
        enforced; external depends_on entries are informational.
        """
        from api.services.sequence_validation import job_dependency_violations

        job_depends_on = {name: list(job.depends_on) for name, job in jobs_index.items()}
        for step, dep in job_dependency_violations(self._dag_steps, job_depends_on):
            raise ValueError(
                f"Job '{step.job_name}' depends on '{dep}' but step '{step.step_id}' does "
                f"not run after it. Add the dependency so '{dep}' runs first."
            )
```

- [ ] **Step 5: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequence_flow.py tests/unit/test_run_executor_gates.py tests/unit/test_run_executor.py -v`
Expected: PASS, including the existing `test_sequence_violating_depends_on_errors_before_any_step`, which asserts `"depends on 'loader'"`.

- [ ] **Step 6: Commit**

```powershell
git add api/services/sequence_validation.py api/services/run_executor.py tests/unit/test_sequence_flow.py tests/unit/test_run_executor_gates.py
git commit -F <file>   # fix(sequences): enforce job dependencies against the graph
```

---

### Task 5: Sequence routes accept stages and return flows

**Files:**
- Modify: `api/routes/sequences.py:32-66`, `:108-143`, `:189-205`
- Test: `tests/unit/test_sequences_routes.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_sequences_routes.py`)

```python
HYBRID_STAGES = [
    {"mode": "serial", "steps": [{"step_id": "a", "job_name": "load_orders"}]},
    {"mode": "parallel", "steps": [
        {"step_id": "b", "job_name": "orders_recon"},
        {"step_id": "c", "job_name": "load_orders"},
    ]},
    {"mode": "serial", "steps": [{"step_id": "d", "job_name": "orders_recon"}]},
]


def _edges(steps):
    return {s["step_id"]: s["depends_on"] for s in steps}


def test_create_with_stages_stores_compiled_edges(client):
    resp = client.post("/api/sequences", json={"name": "hybrid", "stages": HYBRID_STAGES})
    assert resp.status_code == 201, resp.text
    assert resp.json()["step_count"] == 4

    version = client.get(f"/api/sequences/{resp.json()['id']}").json()["versions"][0]
    assert _edges(version["steps"]) == {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
    assert version["flow_mode"] == "custom"
    assert [s["mode"] for s in version["stages"]] == ["serial", "parallel", "serial"]


def test_create_with_flow_mode(client):
    for mode, expected in (("serial", {"a": [], "b": ["a"]}), ("parallel", {"a": [], "b": []})):
        resp = client.post("/api/sequences", json={
            "name": f"flat-{mode}", "flow_mode": mode,
            "steps": [
                {"step_id": "a", "job_name": "load_orders"},
                {"step_id": "b", "job_name": "orders_recon"},
            ],
        })
        assert resp.status_code == 201, resp.text
        version = client.get(f"/api/sequences/{resp.json()['id']}").json()["versions"][0]
        assert _edges(version["steps"]) == expected
        assert version["flow_mode"] == mode


def test_stage_step_with_depends_on_is_rejected(client):
    resp = client.post("/api/sequences", json={"name": "bad", "stages": [
        {"mode": "serial", "steps": [{"step_id": "a", "job_name": "load_orders", "depends_on": []}]},
    ]})
    assert resp.status_code == 422


def test_stage_issue_uses_the_sequence_issue_shape(client):
    resp = client.post("/api/sequences", json={"name": "bad", "stages": [
        {"mode": "serial", "steps": [{"step_id": "a", "job_name": "ghost"}]},
    ]})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "job_name"


def test_root_all_failed_in_stages_is_rejected(client):
    resp = client.post("/api/sequences", json={"name": "bad", "stages": [
        {"mode": "serial", "steps": [
            {"step_id": "a", "job_name": "load_orders", "trigger_rule": "all_failed"},
        ]},
    ]})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "trigger_rule"


def test_new_version_accepts_stages(client):
    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/versions", json={"stages": HYBRID_STAGES})
    assert resp.status_code == 201, resp.text
    assert resp.json()["flow_mode"] == "custom"
    assert _edges(resp.json()["steps"])["d"] == ["b", "c"]


def test_existing_steps_payload_derives_its_flow(client):
    seq_id = _create(client).json()["id"]
    version = client.get(f"/api/sequences/{seq_id}").json()["versions"][0]
    assert version["flow_mode"] == "serial"
    assert [s["mode"] for s in version["stages"]] == ["serial"]


def test_graph_that_is_not_stage_shaped_has_no_stages(client):
    seq_id = _create(client, steps=[
        {"step_id": "a", "job_name": "load_orders", "depends_on": []},
        {"step_id": "b", "job_name": "orders_recon", "depends_on": ["a"]},
        {"step_id": "c", "job_name": "load_orders", "depends_on": []},
        {"step_id": "d", "job_name": "orders_recon", "depends_on": ["c"]},
    ]).json()["id"]
    version = client.get(f"/api/sequences/{seq_id}").json()["versions"][0]
    assert version["flow_mode"] == "custom"
    assert version["stages"] is None


def test_validate_returns_compiled_flow(client):
    body = client.post("/api/sequences/validate", json={"stages": HYBRID_STAGES}).json()
    assert body["ok"] is True
    assert body["order"] == ["a", "b", "c", "d"]
    assert _edges(body["steps"])["d"] == ["b", "c"]
    assert body["flow_mode"] == "custom"
    assert len(body["stages"]) == 3


def test_validate_of_nothing_is_not_ok(client):
    body = client.post("/api/sequences/validate", json={}).json()
    assert body["ok"] is False
    assert body["errors"][0]["field"] == "steps"


def test_invalid_run_settings_are_rejected_on_save(client):
    resp = client.post("/api/sequences", json={
        "name": "bad-settings", "steps": CHAIN,
        "defaults": {"run_settings": {"max_workers": 0}},
    })
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["field"] == "run_settings"

    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/versions", json={
        "steps": CHAIN, "defaults": {"run_settings": {"not_a_setting": 1}},
    })
    assert resp.status_code == 422


def test_job_dependency_between_siblings_is_rejected_on_save(client):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import JobRepository

    with _db_module.SessionLocal() as db:
        JobRepository(db).create({
            "name": "needs_load", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1",
            "key_columns": ["id"], "exclude_columns": [],
            "params": {"depends_on": ["load_orders"]}, "enabled": True,
        })

    resp = client.post("/api/sequences", json={"name": "sib", "stages": [
        {"mode": "parallel", "steps": [
            {"step_id": "a", "job_name": "load_orders"},
            {"step_id": "b", "job_name": "needs_load"},
        ]},
    ]})
    assert resp.status_code == 422
    assert "depends on 'load_orders'" in resp.json()["detail"][0]["message"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequences_routes.py -v`
Expected: the new tests FAIL. For example, `test_create_with_stages_stores_compiled_edges` fails with a 422 from "at least one step". `test_stage_step_with_depends_on_is_rejected` may already pass, which is fine.

- [ ] **Step 3: Update imports and helpers in `api/routes/sequences.py`**

Update the schema and validation imports, and add `ValidationError`:

```python
from pydantic import ValidationError
```

```python
    RunBatchOut,
    RunSettings,
    SequenceDefaults,
    SequenceLaunchRequest,
    SequenceBatchLaunchRequest,
    SequenceRef,
    SequenceStepRef,
    SequenceUsageOut,
```

```python
from api.services.sequence_validation import (
    SequenceCycleError,
    derive_flow,
    steps_from_input,
    topological_order,
    validate_steps,
)
```

Replace `_check_or_422` and `_version_out`:

```python
def _job_depends_on(db: Session) -> dict[str, list[str]]:
    return {j.name: (j.params or {}).get("depends_on") or [] for j in JobRepository(db).list()}


def _issues_for(db: Session, body) -> tuple[list, list[dict]]:
    steps, issues = steps_from_input(body)
    return steps, issues + validate_steps(steps, _known_job_names(db), _job_depends_on(db))


def _steps_or_422(db: Session, body) -> list:
    steps, errors = _issues_for(db, body)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    return steps


def _check_defaults_or_422(defaults: SequenceDefaults | None) -> None:
    # Checked here rather than on SequenceDefaults: that model also parses stored
    # versions, and an old invalid value must not break reading or launching them.
    if defaults is None:
        return
    try:
        RunSettings(**defaults.run_settings)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=[
            {"step_id": None, "field": "run_settings", "message": str(exc)},
        ]) from exc


def _dump(models) -> list:
    return [m.model_dump() for m in models]


def _version_out(version) -> ExecutionSequenceVersionOut:
    steps = [SequenceStepRef.model_validate(s) for s in (version.steps_json or [])]
    flow_mode, stages = derive_flow(steps)
    return ExecutionSequenceVersionOut(
        version_number=version.version_number,
        steps=steps,
        preconditions=version.preconditions_json,
        defaults=version.defaults_json or {},
        created_at=version.created_at,
        flow_mode=flow_mode,
        stages=stages,
    )
```

- [ ] **Step 4: Update the three routes**

In `create_sequence`, replace `_check_or_422(db, body.steps, body.preconditions)` with the two lines below, and use `steps` wherever the route used `body.steps`:

```python
    steps = _steps_or_422(db, body)
    _check_defaults_or_422(body.defaults)
    sequence = repo.create(
        name=body.name, description=body.description, tags=body.tags,
        steps=_dump(steps),
        preconditions=body.preconditions.model_dump() if body.preconditions else None,
        defaults=body.defaults.model_dump(),
    )
    AuditService(db).log(
        request, "sequence.created", "execution_sequence", sequence.id,
        {"name": sequence.name, "step_count": len(steps)},
    )
```

Replace `validate_sequence`:

```python
@router.post("/validate", response_model=SequenceValidateResponse)
def validate_sequence(body: SequenceValidateRequest, db: Session = Depends(get_session)):
    steps, errors = _issues_for(db, body)
    flow_mode, stages = derive_flow(steps)
    if errors:
        return SequenceValidateResponse(
            ok=False, errors=errors, order=[], steps=steps, flow_mode=flow_mode, stages=stages,
        )
    try:
        order = topological_order(steps)
    except SequenceCycleError as exc:  # pragma: no cover — validate_steps catches this first
        return SequenceValidateResponse(
            ok=False, errors=[{"step_id": None, "field": "depends_on", "message": str(exc)}], order=[]
        )
    return SequenceValidateResponse(
        ok=True, errors=[], order=order, steps=steps, flow_mode=flow_mode, stages=stages,
    )
```

In `create_sequence_version`:

```python
    _get_or_404(db, sequence_id)
    steps = _steps_or_422(db, body)
    _check_defaults_or_422(body.defaults)
    version = ExecutionSequenceRepository(db).create_new_version(
        sequence_id, steps=_dump(steps),
        preconditions=body.preconditions.model_dump() if body.preconditions else None,
        defaults=body.defaults.model_dump() if body.defaults is not None else None,
    )
```

- [ ] **Step 5: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequences_routes.py tests/unit/test_sequence_resolver.py tests/unit/test_selections_sequence_ref.py tests/unit/test_schedules_sequence_target.py tests/unit/test_sequence_preconditions.py tests/integration/test_sequence_workflow.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add api/routes/sequences.py tests/unit/test_sequences_routes.py
git commit -F <file>   # feat(sequences): accept stages and flow modes in the API
```

---

### Task 6: Launch override for `max_workers`

**Files:**
- Modify: `api/routes/sequences.py:264`
- Test: `tests/unit/test_sequences_routes.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
def _launched_settings(client, monkeypatch, defaults, launch):
    captured = {}
    monkeypatch.setattr(
        "api.routes.sequences._execute_run",
        lambda run_id, steps, source, target, settings, snapshot: captured.update(
            settings=settings, snapshot=snapshot, steps=steps,
        ),
    )
    seq_id = client.post("/api/sequences", json={
        "name": "workers", "steps": CHAIN, "defaults": defaults,
    }).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/launch", json={
        "source_env": "dev", "target_env": "prod", **launch,
    })
    assert resp.status_code == 202, resp.text
    return captured


def test_sequence_default_max_workers_applies(client, monkeypatch):
    got = _launched_settings(client, monkeypatch, {"run_settings": {"max_workers": 7}}, {})
    assert got["settings"].max_workers == 7


def test_launch_override_wins_and_keeps_other_settings(client, monkeypatch):
    got = _launched_settings(
        client, monkeypatch,
        {"run_settings": {"max_workers": 7, "max_retries": 2}},
        {"max_workers": 1},
    )
    assert got["settings"].max_workers == 1
    assert got["settings"].max_retries == 2
    assert got["snapshot"]["run_settings"]["max_workers"] == 1


def test_launch_override_rejects_zero(client):
    seq_id = _create(client).json()["id"]
    resp = client.post(f"/api/sequences/{seq_id}/launch", json={
        "source_env": "dev", "target_env": "prod", "max_workers": 0,
    })
    assert resp.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequences_routes.py -k "max_workers or launch_override" -v`
Expected: `test_launch_override_wins_and_keeps_other_settings` FAILS with `assert 7 == 1`. The other two pass already; they guard existing behaviour.

- [ ] **Step 3: Apply the override in `_do_launch_sequence`**

Replace `run_settings = resolved.defaults.run_settings or {}` with:

```python
    run_settings = dict(resolved.defaults.run_settings or {})
    if body.max_workers is not None:
        run_settings["max_workers"] = body.max_workers
```

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequences_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add api/routes/sequences.py tests/unit/test_sequences_routes.py
git commit -F <file>   # feat(sequences): allow a max_workers override at launch
```

---

### Task 7: Schedule sequence-backed selections

**Files:**
- Modify: `api/services/scheduler.py:135-193`
- Modify: `api/routes/schedules.py:152-155`
- Test: `tests/unit/test_scheduler_sequence_selection.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Schedules that target a Job Selection backed by a saved sequence."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.models import SchedulerTelemetryEvent, TestRun


@pytest.fixture
def engine():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def client(monkeypatch, engine):
    from api.main import app
    from api.dependencies import get_session
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import get_db
    from etl_framework.repository.repository import JobRepository, TokenRepository

    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(_db_module, "SessionLocal", factory)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session] = override_get_db

    with factory() as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name in ("load_orders", "orders_recon"):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [], "params": {},
                "enabled": True,
            })

    yield TestClient(app, headers={"Authorization": f"Bearer {raw}"})

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_session, None)


def _selection(client, preconditions=None):
    seq_id = client.post("/api/sequences", json={
        "name": "nightly",
        "steps": [
            {"step_id": "load", "job_name": "load_orders", "depends_on": []},
            {"step_id": "recon", "job_name": "orders_recon", "depends_on": ["load"]},
        ],
        "preconditions": preconditions,
    }).json()["id"]
    resp = client.post("/api/selections", json={
        "name": "sel", "sequence_ref": {"sequence_id": seq_id},
        "run_settings": {"max_workers": 7},
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _schedule(client, selection_id):
    return client.post("/api/schedules", json={
        "name": "sel-nightly", "cron_expr": "0 2 * * *",
        "selection_id": selection_id, "source_env": "dev", "target_env": "prod",
    })


def _disable_job(engine, name):
    from etl_framework.repository.repository import JobRepository

    with Session(engine) as db:
        job = next(j for j in JobRepository(db).list() if j.name == name)
        job.enabled = False
        db.commit()


def _fire(schedule_id):
    from api.services.scheduler import _run_schedule
    _run_schedule(schedule_id, "sel-nightly")


def test_schedule_runs_the_sequence_graph_with_selection_settings(client, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda **kw: executed.append(kw))

    resp = _schedule(client, _selection(client))
    assert resp.status_code == 201, resp.text
    _fire(resp.json()["id"])

    assert len(executed) == 1
    steps = executed[0]["job_sequence"]
    assert [(s.step_id, s.depends_on) for s in steps] == [("load", []), ("recon", ["load"])]
    assert executed[0]["run_settings"].max_workers == 7
    assert executed[0]["config_snapshot"]["sequence"]["name"] == "nightly"


def test_unresolvable_sequence_records_a_skip(client, engine, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda **kw: executed.append(kw))

    schedule_id = _schedule(client, _selection(client)).json()["id"]
    _disable_job(engine, "load_orders")
    _fire(schedule_id)

    assert executed == []
    with Session(engine) as db:
        skips = [e for e in db.query(SchedulerTelemetryEvent).all() if e.event_state == "skipped"]
        assert db.query(TestRun).count() == 0
    assert len(skips) == 1
    assert "load_orders" in (skips[0].error_summary or "")


def test_unmet_precondition_records_a_skip(client, engine, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda **kw: executed.append(kw))

    selection_id = _selection(client, {
        "require_run_success": {"job_name": "never_ran", "within_hours": 6},
    })
    _fire(_schedule(client, selection_id).json()["id"])

    assert executed == []
    with Session(engine) as db:
        skips = [e for e in db.query(SchedulerTelemetryEvent).all() if e.event_state == "skipped"]
    assert len(skips) == 1
    assert "never_ran" in (skips[0].error_summary or "")


def test_saving_validates_the_resolved_steps(client, engine):
    selection_id = _selection(client)
    _disable_job(engine, "load_orders")
    resp = _schedule(client, selection_id)
    assert resp.status_code == 422
    assert "load_orders" in str(resp.json()["detail"])


def test_plain_selection_schedule_is_unchanged(client, monkeypatch):
    executed = []
    monkeypatch.setattr("api.routes.runs._execute_run", lambda **kw: executed.append(kw))

    selection_id = client.post("/api/selections", json={
        "name": "plain", "job_sequence": ["orders_recon"],
    }).json()["id"]
    _fire(_schedule(client, selection_id).json()["id"])

    assert [s.job_name for s in executed[0]["job_sequence"]] == ["orders_recon"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_scheduler_sequence_selection.py -v`
Expected: `test_schedule_runs_the_sequence_graph_with_selection_settings` FAILS because `job_sequence` is empty, and the skip and save tests FAIL. `test_plain_selection_schedule_is_unchanged` passes.

- [ ] **Step 3: Add a helper in `api/services/scheduler.py`, above `_run_schedule`**

```python
def _resolve_schedule_sequence(db, sched, name: str, ref):
    """Resolve a schedule's sequence and check its preconditions.

    Returns None after recording the skip when the run should not happen.
    """
    from api.services.scheduler_telemetry import record_scheduler_event
    from api.services.sequence_preconditions import check_for_session
    from api.services.sequence_resolver import SequenceResolutionError, resolve as resolve_sequence

    try:
        resolved = resolve_sequence(db, ref)
    except SequenceResolutionError as exc:
        logger.error("Schedule '%s' could not resolve its sequence: %s", name, exc)
        record_scheduler_event(
            db, sched, "skipped", "ERROR", exit_code=1, error_summary=str(exc)
        )
        return None
    gate = check_for_session(db, resolved.preconditions)
    if not gate.ok:
        logger.info("Schedule '%s' skipped: %s", name, gate.reason)
        record_scheduler_event(
            db, sched, "skipped", "CANCELLED",
            error_summary=f"Precondition not met: {gate.reason}",
        )
        return None
    return resolved
```

- [ ] **Step 4: Use it in both branches of `_run_schedule`**

Replace lines 135-193 with:

```python
        from api.schemas import SequenceRef

        sequence_meta = None
        if sched.sequence_id is not None:
            resolved = _resolve_schedule_sequence(
                db, sched, name,
                SequenceRef(sequence_id=sched.sequence_id, sequence_version=sched.sequence_version),
            )
            if resolved is None:
                return
            job_sequence = resolved.as_linear_steps()
            dag_steps = resolved.steps
            run_settings = resolved.defaults.run_settings or {}
            config_id = resolved.defaults.config_id
            sequence_meta = resolved.snapshot_meta()
            selection_id = None
            selection_version = None
        else:
            sel_repo = JobSelectionRepository(db)
            version = sel_repo.get_version(sched.selection_id, sched.selection_version)
            if version is None:
                logger.error(
                    "Schedule '%s' references missing selection %s v%s; skipping run",
                    name, sched.selection_id, sched.selection_version,
                )
                record_scheduler_event(
                    db, sched, "skipped", "ERROR", exit_code=1,
                    error_summary=(
                        f"Selection version not found: selection {sched.selection_id} "
                        f"v{sched.selection_version}"
                    ),
                )
                return
            job_sequence = version.job_sequence or []
            if version.sequence_ref:
                # Such a selection has no job list of its own; without this the
                # run would have no steps. Selection launch does the same.
                resolved = _resolve_schedule_sequence(
                    db, sched, name, SequenceRef.model_validate(version.sequence_ref),
                )
                if resolved is None:
                    return
                job_sequence = resolved.as_linear_steps()
                dag_steps = resolved.steps
                sequence_meta = resolved.snapshot_meta()
            run_settings = version.run_settings_json or {}
            # Without this, a scheduled run resolves no Saved Config and every
            # live job in it (bo_report, automic_job, compare) runs without
            # credentials. Selection launch already does this -- see
            # api/routes/selections.py's launch handler.
            config_id = version.config_id
            selection_id = sched.selection_id
            selection_version = sched.selection_version
```

Line 237 already passes `dag_steps` when `sequence_meta is not None`, so it needs no change. `logger` is module-level, while `record_scheduler_event` is imported inside each function in this file, which is why the helper imports it itself.

- [ ] **Step 5: Validate on save, in `api/routes/schedules.py`**

Replace lines 152-155:

```python
    version_number = _resolve_selection_version(db, body.selection_id, body.selection_version)
    version = JobSelectionRepository(db).get_version(body.selection_id, version_number)
    job_sequence = version.job_sequence or []
    if version.sequence_ref:
        try:
            job_sequence = resolve_sequence(
                db, SequenceRef.model_validate(version.sequence_ref)
            ).as_linear_steps()
        except SequenceResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    _validate_env_requirements(job_sequence, jobs_by_name, body.target_env)
    return "selection", version_number
```

- [ ] **Step 6: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_scheduler_sequence_selection.py tests/unit/test_precondition_enforcement.py tests/unit/test_schedules_sequence_target.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add api/services/scheduler.py api/routes/schedules.py tests/unit/test_scheduler_sequence_selection.py
git commit -F <file>   # fix(scheduler): run the sequence behind a scheduled selection
```

---

### Task 8: Hold polling and worker slots

**Files:**
- Modify: `api/services/dag_executor.py:128-145`
- Test: `tests/unit/test_dag_executor.py`

- [ ] **Step 1: Write the failing tests** (append)

```python
import time


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_release_takes_effect_while_another_branch_runs():
    from api.services.dag_executor import StepOutcome

    repo = FakeStepRepo()
    child_ran = threading.Event()
    seen = {}

    def run_step(step):
        if step.step_id == "slow":
            # The hold is released only after the coordinator is parked on this
            # step, and this step does not finish until the released child ran.
            assert _wait_until(lambda: repo.status.get("held") == "HELD")
            repo.releases["held"] = "approve"
            seen["child_ran_during_slow"] = child_ran.wait(timeout=3)
        elif step.step_id == "child":
            child_ran.set()
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="held", job_name="j", hold_after=True),
        SequenceStepRef(step_id="slow", job_name="j"),
        SequenceStepRef(step_id="child", job_name="j", depends_on=["held"]),
    ]
    ex, _ = _executor(steps, run_step, step_repo=repo, hold_poll_interval=0.01)
    ex.run()

    assert seen["child_ran_during_slow"] is True


def test_lone_ready_step_does_not_stop_hold_polling():
    from api.services.dag_executor import StepOutcome

    repo = FakeStepRepo()
    child_ran = threading.Event()
    seen = {}

    def run_step(step):
        if step.step_id == "first":
            # Finish only once 'held' is parked, so 'later' becomes the only
            # ready step with nothing in flight.
            assert _wait_until(lambda: repo.status.get("held") == "HELD")
        elif step.step_id == "later":
            repo.releases["held"] = "approve"
            seen["child_ran_during_later"] = child_ran.wait(timeout=3)
        elif step.step_id == "child":
            child_ran.set()
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="first", job_name="j"),
        SequenceStepRef(step_id="held", job_name="j", hold_after=True),
        SequenceStepRef(step_id="later", job_name="j", depends_on=["first"]),
        SequenceStepRef(step_id="child", job_name="j", depends_on=["held"]),
    ]
    ex, _ = _executor(steps, run_step, step_repo=repo, hold_poll_interval=0.01)
    ex.run()

    assert seen["child_ran_during_later"] is True


@pytest.mark.parametrize("max_workers", [1, 2])
def test_steps_are_only_marked_running_when_a_worker_is_free(max_workers):
    from api.services.dag_executor import StepOutcome

    repo = FakeStepRepo()
    running_counts = []

    def run_step(step):
        running_counts.append(sum(1 for s in list(repo.status.values()) if s == "RUNNING"))
        time.sleep(0.02)
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [SequenceStepRef(step_id=f"s{i}", job_name="j") for i in range(5)]
    ex, _ = _executor(steps, run_step, step_repo=repo, max_workers=max_workers)
    ex.run()

    assert len(running_counts) == 5
    assert max(running_counts) <= max_workers
    assert all(status == "PASSED" for status in repo.status.values())


def test_group_runs_concurrently_up_to_max_workers():
    from api.services.dag_executor import StepOutcome

    barrier = threading.Barrier(3)

    def run_step(step):
        barrier.wait(timeout=5)
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [SequenceStepRef(step_id=f"s{i}", job_name="j") for i in range(3)]
    ex, repo = _executor(steps, run_step, max_workers=3)
    ex.run()

    assert set(repo.status.values()) == {"PASSED"}


@pytest.mark.parametrize("rule,child_status", [("all_success", "BLOCKED"), ("all_done", "PASSED")])
def test_slow_parent_only_satisfies_rules_that_ignore_status(rule, child_status):
    from api.services.dag_executor import StepOutcome

    def run_step(step):
        status = "SLOW" if step.step_id == "parent" else "PASSED"
        return StepOutcome(status=status, result=_Result(status), state=None)

    steps = [
        SequenceStepRef(step_id="parent", job_name="j"),
        SequenceStepRef(step_id="child", job_name="j", depends_on=["parent"], trigger_rule=rule),
    ]
    ex, repo = _executor(steps, run_step)
    ex.run()

    assert repo.status["child"] == child_status
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_dag_executor.py -v`
Expected: `test_release_takes_effect_while_another_branch_runs`, `test_lone_ready_step_does_not_stop_hold_polling`, and `test_steps_are_only_marked_running_when_a_worker_is_free` FAIL. The concurrency and `SLOW` tests pass, and they guard existing behaviour.

- [ ] **Step 3: Change the scheduling loop in `DagExecutor.run`**

Replace lines 128-145:

```python
                # A single ready step with nothing in flight runs inline. That is
                # every chain, and it keeps legacy sequences off the pool entirely.
                # A held branch needs polling while this step runs, so it goes to
                # the pool instead.
                if len(ready) == 1 and not in_flight and not self._held:
                    self._finish(ready[0], self._execute(ready[0]))
                    continue

                # Only take what a worker can start now. The rest stay pending, so
                # they are not reported as RUNNING while they wait for a slot.
                for step_id in ready[:self._max_workers - len(in_flight)]:
                    if pool is None:
                        pool = ThreadPoolExecutor(max_workers=self._max_workers)
                    self._pending.remove(step_id)
                    self._mark(step_id, "RUNNING")
                    in_flight[pool.submit(self._run_one, step_id)] = step_id

                if in_flight:
                    # The timeout returns to the loop so holds are polled while
                    # another branch is still running.
                    done, _ = wait(list(in_flight), timeout=self._hold_poll, return_when=FIRST_COMPLETED)
                    for future in done:
                        step_id = in_flight.pop(future)
                        self._finish(step_id, future.result())
```

- [ ] **Step 4: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_dag_executor.py tests/unit/test_dag_retry.py tests/unit/test_run_cancel.py tests/integration/test_dag_branch_hold.py -v`
Expected: PASS. `test_chain_never_uses_the_thread_pool` still passes because chains have no held step.

- [ ] **Step 5: Commit**

```powershell
git add api/services/dag_executor.py tests/unit/test_dag_executor.py
git commit -F <file>   # fix(dag): poll holds during other branches and respect worker slots
```

---

### Task 9: Crashed and missing steps count as errors

**Files:**
- Modify: `api/services/dag_executor.py:39-46`, `:141-146`, `:205-208`, `:319-325`
- Modify: `api/services/run_executor.py:285-295`, `:374-408`
- Test: `tests/unit/test_dag_executor.py`, `tests/unit/test_dag_crash_handling.py` (create)

- [ ] **Step 1: Write the failing `DagExecutor` tests** (append to `tests/unit/test_dag_executor.py`)

```python
def test_pool_step_exception_does_not_lose_other_steps():
    from api.services.dag_executor import StepOutcome

    def run_step(step):
        if step.step_id == "boom":
            raise RuntimeError("boom")
        return StepOutcome(status="PASSED", result=_Result(), state=None)

    steps = [
        SequenceStepRef(step_id="boom", job_name="j"),
        SequenceStepRef(step_id="ok", job_name="j"),
        SequenceStepRef(step_id="after_ok", job_name="j", depends_on=["ok"]),
        SequenceStepRef(step_id="after_boom", job_name="j", depends_on=["boom"]),
    ]
    ex, repo = _executor(steps, run_step)
    outcome = ex.run()

    assert outcome.step_crashed is True
    assert repo.status == {
        "boom": "ERROR", "ok": "PASSED", "after_ok": "PASSED", "after_boom": "BLOCKED",
    }


def test_inline_step_exception_is_recorded():
    def run_step(step):
        raise RuntimeError("boom")

    ex, repo = _executor([SequenceStepRef(step_id="only", job_name="j")], run_step)
    outcome = ex.run()

    assert outcome.step_crashed is True
    assert repo.status == {"only": "ERROR"}


def test_clean_run_is_not_flagged_as_crashed():
    ex, _ = _executor([SequenceStepRef(step_id="only", job_name="j")], _ok())
    assert ex.run().step_crashed is False
```

- [ ] **Step 2: Write the failing `RunExecutor` tests** in `tests/unit/test_dag_crash_handling.py`

```python
"""A step whose job crashes or no longer exists must count against the run."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings, SequenceStepRef
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_job(db, name):
    JobRepository(db).create({
        "name": name, "description": name, "tags": [],
        "job_type": "reconciliation", "query": f"SELECT * FROM {name}",
        "key_columns": ["id"], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"source_rows": [{"id": 1}], "target_rows": [{"id": 1}]},
        "enabled": True,
    })


def _run(db, run_id, steps):
    RunRepository(db).create_run(run_id, "dev", "prod", {})
    RunExecutor(
        db=db, run_id=run_id, source_env="dev", target_env="prod",
        job_sequence=steps, run_settings=RunSettings(metrics_enabled=False),
    ).execute()
    return RunRepository(db).get_run(run_id)


def test_missing_job_makes_the_run_error():
    db = _session()
    run = _run(db, "run-ghost", [SequenceStepRef(step_id="ghost", job_name="ghost")])

    assert run.status == "ERROR"
    assert run.error == 1
    assert "ghost" in run.results[0].error_message
    assert RunStepRepository(db).list_steps("run-ghost")[0].status == "ERROR"


def test_exception_while_building_a_case_makes_the_run_error(monkeypatch):
    db = _session()
    _make_job(db, "job_a")

    def explode(self, job):
        raise RuntimeError("cannot build")

    monkeypatch.setattr(RunExecutor, "_build_case", explode)
    run = _run(db, "run-build", [SequenceStepRef(step_id="a", job_name="job_a")])

    assert run.status == "ERROR"
    assert run.error == 1
    assert "cannot build" in run.results[0].error_message


def test_persistence_failure_still_counts_as_an_error(monkeypatch):
    db = _session()
    _make_job(db, "job_a")

    def explode(self, states):
        raise RuntimeError("database went away")

    monkeypatch.setattr(RunExecutor, "_persist_states", explode)
    run = _run(db, "run-persist", [SequenceStepRef(step_id="a", job_name="job_a")])

    assert run.status == "ERROR"
    assert RunStepRepository(db).list_steps("run-persist")[0].status == "ERROR"


def test_crash_outside_step_handling_makes_the_run_error(monkeypatch):
    db = _session()
    _make_job(db, "job_a")

    def explode(self, step, jobs_index):
        raise RuntimeError("coordinator backstop")

    monkeypatch.setattr(RunExecutor, "_run_dag_step", explode)
    run = _run(db, "run-backstop", [SequenceStepRef(step_id="a", job_name="job_a")])

    assert run.status == "ERROR"
    assert RunStepRepository(db).list_steps("run-backstop")[0].status == "ERROR"
```

- [ ] **Step 3: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_dag_crash_handling.py tests/unit/test_dag_executor.py -k "crash or exception or missing or persistence or clean_run" -v`
Expected: FAIL. `test_missing_job_makes_the_run_error` ends `PASSED`, and the `DagExecutor` tests fail with `RuntimeError: boom` or a missing `step_crashed`.

- [ ] **Step 4: Add the backstop to `api/services/dag_executor.py`**

Add the field to `DagOutcome`:

```python
    step_crashed: bool = False  # a step raised instead of returning an outcome
```

Add below `_execute`, and make `_execute` use it:

```python
    def _execute(self, step_id: str):
        self._pending.remove(step_id)
        self._mark(step_id, "RUNNING")
        try:
            return self._run_one(step_id)
        except Exception:
            return self._crashed()

    def _crashed(self) -> StepOutcome:
        # There is no job here to build a state from, so the caller is told
        # through the flag that the run cannot count as clean.
        self._outcome.step_crashed = True
        return StepOutcome(status="ERROR", result=None, state=None)

    def _result_of(self, future) -> StepOutcome:
        try:
            return future.result()
        except Exception:
            return self._crashed()
```

In `run()`, change the `done` loop to:

```python
                    for future in done:
                        step_id = in_flight.pop(future)
                        self._finish(step_id, self._result_of(future))
```

Replace `_drain`:

```python
    def _drain(self, in_flight: dict) -> None:
        for future in list(in_flight):
            step_id = in_flight.pop(future)
            self._finish(step_id, self._result_of(future))
```

- [ ] **Step 5: Handle step errors in `RunExecutor._run_dag_step`**

Replace the method:

```python
    def _run_dag_step(self, step, jobs_index: dict):
        from api.services.dag_executor import StepOutcome

        if self._worker_session_factory is None:
            self._worker_session_factory = sessionmaker(
                bind=self._db.get_bind(),
                autocommit=False,
                autoflush=False,
            )
        worker_db = self._worker_session_factory()
        worker = RunExecutor(
            db=worker_db,
            run_id=self._run_id,
            source_env=self._source_env,
            target_env=self._target_env,
            job_sequence=[],
            run_settings=self._settings,
            config_snapshot=self._config_snapshot,
        )
        # One worker per attempt, so the resume state has to outlive the
        # worker -- but it is scoped to this step, so a second step running the
        # same job resumes nothing from this one.
        worker._file_transfer_resume = self._file_transfer_resume.setdefault(step.step_id, {})
        try:
            job_def = jobs_index.get(step.job_name)
            if job_def is None:
                raise ValueError(f"Job '{step.job_name}' not found")
            case_fn = worker._build_case(job_def)
            state = TestRunner(max_workers=1).run([(job_def.name, case_fn)])[0]
            results = worker._persist_states([state])
            status = state.status.value if hasattr(state.status, "value") else str(state.status)
            return StepOutcome(status=status, result=results[0] if results else None, state=state)
        except Exception as exc:
            logger.exception("Step '%s' failed outside its job", step.step_id)
            state = TestCaseState(
                name=step.job_name, test_type="reconciliation",
                status=TestStatus.ERROR, error_message=str(exc),
            )
            try:
                worker_db.rollback()
                worker._persist_error(step.job_name, exc)
            except Exception:
                logger.exception("Could not record the error for step '%s'", step.step_id)
            return StepOutcome(status="ERROR", result=None, state=state)
        finally:
            worker_db.close()
```

- [ ] **Step 6: End a crashed run as `ERROR`, in `RunExecutor.execute`**

After the `if outcome.cancelled:` block's body, before `elif outcome.blocked:`, add:

```python
                elif outcome.step_crashed:
                    # Caught below, which records the run as ERROR.
                    raise RuntimeError("A sequence step crashed; see the step statuses")
```

- [ ] **Step 7: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_dag_crash_handling.py tests/unit/test_dag_executor.py tests/unit/test_dag_retry.py tests/unit/test_run_executor.py tests/unit/test_run_executor_gates.py tests/unit/test_run_cancel.py tests/integration/test_dag_branch_hold.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add api/services/dag_executor.py api/services/run_executor.py tests/unit/test_dag_executor.py tests/unit/test_dag_crash_handling.py
git commit -F <file>   # fix(dag): count crashed and missing steps as errors
```

---

### Task 9b: Restart option to re-run downstream steps

**Files:**
- Modify: `api/schemas.py` (add `RunRestartRequest` next to `RunStatusOut`)
- Modify: `api/services/run_restart.py` (`restart_run`, new helper)
- Modify: `api/routes/runs.py` (`restart_failed_run`)
- Modify: `frontend/features/history.js` (`restartRun`), `frontend/partials/tab-history.html`, `frontend/help-content.js`, `frontend/index.html` (generated)
- Test: `tests/unit/test_run_restart.py`, `tests/unit/test_runs_dag_api.py`

- [ ] **Step 1: Write the failing service tests** (append to `tests/unit/test_run_restart.py`)

```python
def _make_group_run(db):
    """a -> (m1 failed, m2 passed) -> next passed -> last passed, plus an unrelated root."""
    repo = RunRepository(db)
    repo.create_run("group", "dev", "prod", {"run_settings": {}})
    repo.update_run_status("group", "FAILED")
    steps = [
        SequenceStepRef(step_id="a", job_name="a"),
        SequenceStepRef(step_id="m1", job_name="m1", depends_on=["a"]),
        SequenceStepRef(step_id="m2", job_name="m2", depends_on=["a"]),
        SequenceStepRef(step_id="next", job_name="next", depends_on=["m1", "m2"], trigger_rule="all_done"),
        SequenceStepRef(step_id="last", job_name="last", depends_on=["next"]),
        SequenceStepRef(step_id="other", job_name="other"),
    ]
    step_repo = RunStepRepository(db)
    step_repo.materialize_steps("group", steps)
    for index, status in enumerate(["PASSED", "FAILED", "PASSED", "PASSED", "PASSED", "PASSED"]):
        step_repo.update_status("group", index, status)


def _statuses_of(db, run_id):
    return {s.step_id: s.status for s in RunStepRepository(db).list_steps(run_id)}


def test_restart_by_default_carries_over_steps_after_the_failed_one(db):
    from api.services.run_restart import restart_run

    _make_group_run(db)
    plan = restart_run(db, "group")

    assert set(plan.seeded_outcomes) == {"a", "m2", "next", "last", "other"}
    assert _statuses_of(db, plan.run_id)["m1"] == "PENDING"
    assert _statuses_of(db, plan.run_id)["next"] == "PASSED"


def test_rerun_downstream_reruns_everything_after_a_rerun_step(db):
    from api.services.run_restart import restart_run

    _make_group_run(db)
    plan = restart_run(db, "group", rerun_downstream=True)

    # m2 and other have no ancestor running again, so they stay carried over.
    assert set(plan.seeded_outcomes) == {"a", "m2", "other"}
    assert [s.name for s in plan.carried_over_states] == ["a", "m2", "other"]
    assert _statuses_of(db, plan.run_id) == {
        "a": "PASSED", "m1": "PENDING", "m2": "PASSED",
        "next": "PENDING", "last": "PENDING", "other": "PASSED",
    }
    carried = {s.step_id: s.carried_over for s in RunStepRepository(db).list_steps(plan.run_id)}
    assert carried["next"] is False and carried["a"] is True


def test_rerun_downstream_on_a_chain_with_a_blocked_tail_changes_nothing(db):
    from api.services.run_restart import restart_run

    _make_failed_run(db)
    plan = restart_run(db, "original", rerun_downstream=True)

    assert set(plan.seeded_outcomes) == {"extract"}
```

- [ ] **Step 2: Write the failing route tests** (append to `tests/unit/test_runs_dag_api.py`, reusing how the file's existing restart test at line 101 builds `failed-run` and its fixtures)

```python
def test_restart_accepts_no_body_and_defaults_to_not_rerunning_downstream(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "api.routes.runs.restart_run",
        lambda db, run_id, rerun_downstream=False: seen.update(flag=rerun_downstream) or _raise_not_found(),
    )
    assert client.post("/api/runs/any/restart").status_code == 404
    assert seen["flag"] is False


def test_restart_passes_rerun_downstream_through(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "api.routes.runs.restart_run",
        lambda db, run_id, rerun_downstream=False: seen.update(flag=rerun_downstream) or _raise_not_found(),
    )
    resp = client.post("/api/runs/any/restart", json={"rerun_downstream": True})
    assert resp.status_code == 404
    assert seen["flag"] is True


def test_restart_rejects_unknown_fields(client):
    assert client.post("/api/runs/any/restart", json={"nope": True}).status_code == 422


def _raise_not_found():
    from api.services.run_restart import RestartNotFound
    raise RestartNotFound("Run not found")
```

- [ ] **Step 3: Run to verify they fail**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_run_restart.py tests/unit/test_runs_dag_api.py -v`
Expected: the `rerun_downstream` tests FAIL with `TypeError` on the unexpected keyword, or get a passing 404 without the flag being seen. `test_restart_by_default_...` passes, and it guards today's behaviour.

- [ ] **Step 4: Add the helper and the parameter in `api/services/run_restart.py`**

Add above `restart_run`:

```python
def _step_ids_to_rerun(rows: list[RunStep], refs: list[SequenceStepRef], rerun_downstream: bool) -> set[str]:
    """Steps the restart runs again: anything unfinished, plus optionally what follows it."""
    rerun = {ref.step_id for row, ref in zip(rows, refs) if row.status not in CARRIED_OVER_STATUSES}
    if not rerun_downstream:
        return rerun
    children: dict[str, list[str]] = {}
    for ref in refs:
        for parent in ref.depends_on:
            children.setdefault(parent, []).append(ref.step_id)
    stack = list(rerun)
    while stack:
        for child in children.get(stack.pop(), []):
            if child not in rerun:
                rerun.add(child)
                stack.append(child)
    return rerun
```

Change the signature:

```python
def restart_run(db: Session, original_run_id: str, rerun_downstream: bool = False) -> RestartPlan:
```

After `steps = _step_refs_from_rows(original_steps)`:

```python
    rerun = _step_ids_to_rerun(original_steps, steps, rerun_downstream)
```

In the carried-over loop, replace the status check:

```python
    for original_step, step_ref in zip(original_steps, steps):
        if step_ref.step_id in rerun:
            continue
```

Leave the "nothing to restart" check as it is: a fully passed run has nothing to start from, with or without the option.

- [ ] **Step 5: Add the request model in `api/schemas.py`, above `class RunStatusOut`**

```python
class RunRestartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Also run passed steps that follow a step running again. Off by default,
    # because it repeats their side effects.
    rerun_downstream: bool = False
```

- [ ] **Step 6: Use it in `restart_failed_run`, in `api/routes/runs.py`**

Add `RunRestartRequest` to the file's `api.schemas` import, then:

```python
@router.post("/{run_id}/restart", response_model=RunStatusOut, status_code=202)
def restart_failed_run(
    run_id: str, background_tasks: BackgroundTasks, request: Request,
    body: RunRestartRequest | None = None, db: Session = Depends(get_session),
):
    rerun_downstream = bool(body and body.rerun_downstream)
    try:
        plan = restart_run(db, run_id, rerun_downstream=rerun_downstream)
```

and add the flag to the audit detail:

```python
        {"restarted_from_run_id": plan.restarted_from_run_id, "rerun_downstream": rerun_downstream},
```

- [ ] **Step 7: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_run_restart.py tests/unit/test_runs_dag_api.py -v`
Expected: PASS.

- [ ] **Step 8: Add the UI control**

In `frontend/features/history.js`, add state next to the other history state:

```javascript
    restartRerunDownstream: false,   // applies to the run on screen; reset when another is viewed
```

Set `this.restartRerunDownstream = false;` as the first line of `viewRunDetail`'s `try` block, and replace the POST in `restartRun`:

```javascript
        const body = this.restartRerunDownstream ? { rerun_downstream: true } : undefined;
        const restarted = await api('POST', `/api/runs/${runId}/restart`, body);
```

In `frontend/partials/tab-history.html`, directly after the `Restart from failure` button:

```html
            <label x-show="canRestartRun(selectedRun)" class="flex items-center gap-1 text-xs text-slate-500 cursor-pointer"
                   title="Passed steps that come after a re-run step run again too. This repeats their side effects, such as file transfers.">
              <input type="checkbox" class="rounded" x-model="restartRerunDownstream"
                     data-testid="history-restart-rerun-downstream" />
              Also re-run later steps
            </label>
```

In `frontend/help-content.js`, append to the `Restart from failure` `text` (line 769):

```
 Passed steps that ran after a failed one stay carried over; tick Also re-run later steps, or send {"rerun_downstream": true}, to run them again, which repeats their side effects.
```

Regenerate: `docker compose -f docker-compose.test.yml run --rm test-py node scripts/build-html.js`

- [ ] **Step 9: Check `history.js` with a unit test**

Create `tests/unit/test_restart_rerun_downstream.js`:

```javascript
'use strict';
// Restart sends the option only when ticked, so the default request stays bodyless.
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const history = fs.readFileSync(path.join(ROOT, 'frontend', 'features', 'history.js'), 'utf8');
const partial = fs.readFileSync(path.join(ROOT, 'frontend', 'partials', 'tab-history.html'), 'utf8');

assert.ok(history.includes('this.restartRerunDownstream ? { rerun_downstream: true } : undefined'));
assert.ok(history.includes('this.restartRerunDownstream = false;'));
assert.ok(partial.includes('data-testid="history-restart-rerun-downstream"'));
console.log('ok - restart option is wired');
```

Create `tests/unit/test_restart_rerun_downstream.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest


SPEC = Path(__file__).with_suffix(".js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_restart_rerun_downstream_wiring():
    proc = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(SPEC.parents[2]),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_restart_rerun_downstream.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```powershell
git add api/schemas.py api/services/run_restart.py api/routes/runs.py frontend/features/history.js frontend/partials/tab-history.html frontend/help-content.js frontend/index.html tests/unit/test_run_restart.py tests/unit/test_runs_dag_api.py tests/unit/test_restart_rerun_downstream.js tests/unit/test_restart_rerun_downstream.py
git commit -F <file>   # feat(restart): add an option to re-run steps after a re-run step
```

Task 10 and Task 14 cover this end to end; see their steps marked "rerun_downstream".

---

### Task 10: Hybrid flow end to end through the API

**Files:**
- Test: `tests/integration/test_sequence_hybrid_flow.py` (create)
- Modify if a lock error appears: `etl_framework/repository/database.py:15-18`

Most of this should pass immediately, because Tasks 2-9 built the behaviour. It is the proof that the pieces work together, on a real file database and on Postgres.

- [ ] **Step 1: Write the tests**

```python
"""Serial -> parallel -> serial runs through the real API and executor.

run_steps has no timestamps, so concurrency is proved with a barrier that can only
pass if group members run at the same time, and ordering with recorded events.
"""
from __future__ import annotations

import os
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base, _ensure_compare_columns
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository, TokenRepository,
)


@pytest.fixture
def engine(tmp_path):
    url = os.environ.get("HYBRID_TEST_DATABASE_URL")
    if url:
        eng = create_engine(url)
        Base.metadata.drop_all(eng)
    else:
        # Deliberately the same connect args as production, so this sees the same
        # locking behaviour under parallel writes.
        eng = create_engine(
            f"sqlite:///{tmp_path / 'hybrid.db'}", connect_args={"check_same_thread": False}
        )
    Base.metadata.create_all(eng)
    _ensure_compare_columns(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture
def client(monkeypatch, factory):
    from api.main import app
    from api.dependencies import get_session
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import get_db

    monkeypatch.setattr(_db_module, "SessionLocal", factory)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session] = override_get_db

    with factory() as db:
        raw, _ = TokenRepository(db).create("test-runner")
        for name, target in (("good", 1.0), ("bad", 2.0), ("boom", 1.0), ("extract", 1.0),
                             ("load_x", 1.0), ("load_y", 1.0), ("reconcile", 1.0), ("publish", 1.0)):
            JobRepository(db).create({
                "name": name, "description": "", "tags": [],
                "job_type": "reconciliation", "query": "SELECT 1",
                "key_columns": ["id"], "exclude_columns": [],
                "source_env": None, "target_env": None,
                "params": {
                    "source_rows": [{"id": 1, "amount": 1.0}],
                    "target_rows": [{"id": 1, "amount": target}],
                },
                "enabled": True,
            })

    yield TestClient(app, headers={"Authorization": f"Bearer {raw}"})

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_session, None)


class Recorder:
    """Wraps each job body to record entry and exit and track concurrency."""

    def __init__(self, barrier_jobs=(), parties=2):
        self.events: list[str] = []
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()
        self._barrier = threading.Barrier(parties)
        self._barrier_jobs = set(barrier_jobs)

    def wrap(self, job, case):
        def run_case():
            with self._lock:
                self.events.append(f"enter:{job.name}")
                self.active += 1
                self.peak = max(self.peak, self.active)
            try:
                if job.name == "boom":
                    raise RuntimeError("boom")
                if job.name in self._barrier_jobs:
                    self._barrier.wait(timeout=20)
                return case()
            finally:
                with self._lock:
                    self.active -= 1
                    self.events.append(f"exit:{job.name}")
        return run_case


@pytest.fixture
def record(monkeypatch):
    def install(**kw) -> Recorder:
        recorder = Recorder(**kw)
        original = RunExecutor._build_case
        monkeypatch.setattr(
            RunExecutor, "_build_case",
            lambda self, job: recorder.wrap(job, original(self, job)),
        )
        return recorder
    return install


HYBRID = [
    {"mode": "serial", "steps": [{"step_id": "extract", "job_name": "extract"}]},
    {"mode": "parallel", "steps": [
        {"step_id": "load_x", "job_name": "load_x"},
        {"step_id": "load_y", "job_name": "load_y"},
    ]},
    {"mode": "serial", "steps": [
        {"step_id": "reconcile", "job_name": "reconcile"},
        {"step_id": "publish", "job_name": "publish"},
    ]},
]


def _launch(client, stages, name="flow", **launch):
    resp = client.post("/api/sequences", json={"name": name, "stages": stages})
    assert resp.status_code == 201, resp.text
    resp = client.post(f"/api/sequences/{resp.json()['id']}/launch", json={
        "source_env": "dev", "target_env": "prod", **launch,
    })
    assert resp.status_code == 202, resp.text
    return resp.json()["run_id"]


def _statuses(factory, run_id):
    with factory() as db:
        return {s.step_id: s.status for s in RunStepRepository(db).list_steps(run_id)}


def _run_status(factory, run_id):
    with factory() as db:
        return RunRepository(db).get_run(run_id).status


def _step(step_id, job, **kw):
    return {"step_id": step_id, "job_name": job, **kw}


def test_group_overlaps_and_neighbouring_stages_do_not(client, factory, record):
    recorder = record(barrier_jobs=("load_x", "load_y"))
    run_id = _launch(client, HYBRID)

    assert _run_status(factory, run_id) == "PASSED"
    assert set(_statuses(factory, run_id).values()) == {"PASSED"}
    assert recorder.peak == 2

    at = recorder.events.index
    assert at("exit:extract") < at("enter:load_x")
    assert at("exit:extract") < at("enter:load_y")
    assert at("exit:load_x") < at("enter:reconcile")
    assert at("exit:load_y") < at("enter:reconcile")
    assert at("exit:reconcile") < at("enter:publish")


def test_max_workers_one_removes_the_overlap(client, factory, record):
    recorder = record()
    run_id = _launch(client, HYBRID, max_workers=1)

    assert _run_status(factory, run_id) == "PASSED"
    assert recorder.peak == 1


@pytest.mark.parametrize("second_member,rule,expected_next,expected_run", [
    ("good", "all_success", "PASSED", "PASSED"),
    ("bad", "all_success", "BLOCKED", "BLOCKED"),
    ("boom", "all_success", "BLOCKED", "BLOCKED"),
    ("bad", "all_done", "PASSED", "FAILED"),
    ("boom", "all_done", "PASSED", "ERROR"),
    ("bad", "any_success", "PASSED", "FAILED"),
])
def test_handover_after_a_group(client, factory, record, second_member, rule, expected_next, expected_run):
    record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "good"), _step("m2", second_member)]},
        {"mode": "serial", "steps": [_step("next", "good", trigger_rule=rule)]},
    ])

    assert _statuses(factory, run_id)["next"] == expected_next
    assert _run_status(factory, run_id) == expected_run


def test_any_success_blocks_when_every_member_failed(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "bad"), _step("m2", "bad")]},
        {"mode": "serial", "steps": [_step("next", "good", trigger_rule="any_success")]},
    ])
    assert _statuses(factory, run_id)["next"] == "BLOCKED"


def test_member_that_never_ran_blocks_the_next_stage_whatever_its_rule(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "serial", "steps": [
            _step("a", "bad"),
            _step("b", "good"),
            _step("c", "good", trigger_rule="all_done"),
        ]},
    ])
    assert _statuses(factory, run_id) == {"a": "FAILED", "b": "BLOCKED", "c": "BLOCKED"}


def test_on_failure_stop_cancels_steps_not_started(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "bad", on_failure="stop"), _step("m2", "good")]},
        {"mode": "serial", "steps": [_step("next", "good")]},
    ])
    statuses = _statuses(factory, run_id)
    assert statuses["m1"] == "FAILED"
    assert statuses["m2"] == "PASSED"
    assert statuses["next"] == "CANCELLED"
    assert _run_status(factory, run_id) == "CANCELLED"


def test_on_failure_continue_still_blocks_all_success(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "bad", on_failure="continue"), _step("m2", "good")]},
        {"mode": "serial", "steps": [_step("next", "good")]},
    ])
    assert _statuses(factory, run_id)["next"] == "BLOCKED"


def test_restart_keeps_edges_and_max_workers(client, factory, record):
    record()
    run_id = _launch(client, [
        {"mode": "serial", "steps": [_step("a", "good")]},
        {"mode": "parallel", "steps": [_step("m1", "bad"), _step("m2", "good")]},
        {"mode": "serial", "steps": [_step("next", "good")]},
    ], max_workers=3)

    resp = client.post(f"/api/runs/{run_id}/restart")
    assert resp.status_code == 202, resp.text
    new_run_id = resp.json()["run_id"]

    with factory() as db:
        steps = {s.step_id: s for s in RunStepRepository(db).list_steps(new_run_id)}
        snapshot = RunRepository(db).get_run(new_run_id).config_snapshot

    assert steps["next"].depends_on == ["m1", "m2"]
    assert steps["m1"].depends_on == ["a"]
    assert steps["a"].carried_over is True
    assert snapshot["run_settings"]["max_workers"] == 3


def test_rerun_downstream_runs_later_steps_again(client, factory, record):
    # rerun_downstream: without the option `next` is carried over and never sees
    # the failed member's new result. With it, `next` runs again and `other` does not.
    recorder = record()
    run_id = _launch(client, [
        {"mode": "parallel", "steps": [_step("m1", "bad"), _step("m2", "good")]},
        {"mode": "serial", "steps": [_step("next", "reconcile", trigger_rule="all_done")]},
    ])
    assert _statuses(factory, run_id)["next"] == "PASSED"

    recorder.events.clear()
    resp = client.post(f"/api/runs/{run_id}/restart")
    assert resp.status_code == 202, resp.text
    assert recorder.events == ["enter:bad", "exit:bad"]

    recorder.events.clear()
    resp = client.post(f"/api/runs/{run_id}/restart", json={"rerun_downstream": True})
    assert resp.status_code == 202, resp.text
    new_run_id = resp.json()["run_id"]

    assert "enter:reconcile" in recorder.events
    assert "enter:good" not in recorder.events
    with factory() as db:
        steps = {s.step_id: s for s in RunStepRepository(db).list_steps(new_run_id)}
    assert steps["m2"].carried_over is True
    assert steps["next"].carried_over is False
```

- [ ] **Step 2: Run on SQLite**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/integration/test_sequence_hybrid_flow.py -v`
Expected: PASS.

If a test fails with `sqlalchemy.exc.OperationalError: database is locked`, that is the defect the spec calls out. Fix it in `etl_framework/repository/database.py` by changing the `connect_args` line to:

```python
    connect_args={"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {},
```

and add `"timeout": 30` to the test's `connect_args`, then rerun. Any other failure is a real bug: use superpowers:systematic-debugging rather than changing the expected values.

- [ ] **Step 3: Run on Postgres**

Run: `docker compose -f docker-compose.test.yml run --rm test-py-postgres`
Expected: PASS. Then stop the service: `docker compose -f docker-compose.test.yml down -v`.

- [ ] **Step 4: Commit**

```powershell
git add tests/integration/test_sequence_hybrid_flow.py
git commit -F <file>   # test(sequences): prove hybrid flows through the API on SQLite and Postgres
```

Add `etl_framework/repository/database.py` only if Step 2 changed it.

---

### Task 11: Flow editor state

**Files:**
- Modify: `frontend/features/sequences.js`
- Test: `tests/unit/test_sequence_flow_editor.js`, `tests/unit/test_sequence_flow_editor.py` (create)

- [ ] **Step 1: Write the Python wrapper `tests/unit/test_sequence_flow_editor.py`**

```python
import shutil
import subprocess
from pathlib import Path

import pytest


SPEC = Path(__file__).with_suffix(".js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_sequence_flow_editor_behaviour():
    proc = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(SPEC.parents[2]),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Write the failing test `tests/unit/test_sequence_flow_editor.js`**

```javascript
'use strict';
// The sequence editor keeps one flat step list and builds whichever input the
// chosen flow needs. These cover that payload and loading a saved version back.
const assert = require('node:assert');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
global.window = global;
global.APP_CONFIG = {};
require(path.join(ROOT, 'frontend', 'features', 'sequences.js'));

function editor(steps) {
  const seq = window.ETL_FEATURE_SEQUENCES();
  seq.sequenceSteps = steps.map((id) => ({ step_id: id, job_name: 'j', depends_on: [], _stage: 0 }));
  return seq;
}

function run(name, fn) {
  fn();
  console.log('ok - ' + name);
}

run('serial and parallel send flow_mode without edges or client fields', () => {
  const seq = editor(['a', 'b']);
  seq.sequenceSteps[1].depends_on = ['a'];
  seq.sequenceFlow = 'parallel';
  assert.deepStrictEqual(seq.buildSequenceFlowInput(), {
    flow_mode: 'parallel',
    steps: [{ step_id: 'a', job_name: 'j' }, { step_id: 'b', job_name: 'j' }],
  });
});

run('custom sends stages in order and skips empty ones', () => {
  const seq = editor(['a', 'b', 'c']);
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'parallel', 'serial'];
  seq.sequenceSteps[1]._stage = 1;
  seq.sequenceSteps[2]._stage = 1;
  assert.deepStrictEqual(seq.buildSequenceFlowInput(), {
    stages: [
      { mode: 'serial', steps: [{ step_id: 'a', job_name: 'j' }] },
      { mode: 'parallel', steps: [{ step_id: 'b', job_name: 'j' }, { step_id: 'c', job_name: 'j' }] },
    ],
  });
});

run('advanced graph sends raw edges', () => {
  const seq = editor(['a', 'b']);
  seq.sequenceAdvancedGraph = true;
  seq.sequenceSteps[1].depends_on = ['a'];
  assert.deepStrictEqual(seq.buildSequenceFlowInput(), {
    steps: [
      { step_id: 'a', job_name: 'j', depends_on: [] },
      { step_id: 'b', job_name: 'j', depends_on: ['a'] },
    ],
  });
});

run('a new step joins the end of its stage, keeping the flat list in stage order', () => {
  const seq = editor(['a', 'b']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'serial'];
  seq.sequenceSteps[1]._stage = 1;
  seq.addSequenceStep(0);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => s._stage), [0, 0, 1]);
});

run('moving a stage keeps each step with its stage', () => {
  const seq = editor(['a', 'b']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'parallel'];
  seq.sequenceSteps[1]._stage = 1;
  seq.moveSequenceStage(1, -1);
  assert.deepStrictEqual(seq.sequenceStageModes, ['parallel', 'serial']);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => s.step_id), ['b', 'a']);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => s._stage), [0, 1]);
});

run('removing a stage renumbers later ones', () => {
  const seq = editor(['a', 'b', 'c']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'parallel', 'serial'];
  seq.sequenceSteps[1]._stage = 1;
  seq.sequenceSteps[2]._stage = 2;
  seq.removeSequenceStage(1);
  assert.deepStrictEqual(seq.sequenceStageModes, ['serial', 'serial']);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => [s.step_id, s._stage]), [['a', 0], ['c', 1]]);
});

run('a stage rule writes to the entry steps only', () => {
  const seq = editor(['a', 'b', 'c', 'd']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceFlow = 'custom';
  seq.sequenceStageModes = ['serial', 'parallel', 'serial'];
  seq.sequenceSteps[1]._stage = 1;
  seq.sequenceSteps[2]._stage = 2;
  seq.sequenceSteps[3]._stage = 2;
  seq.setSequenceStageRule(2, 'all_done');
  assert.strictEqual(seq.sequenceSteps[2].trigger_rule, 'all_done');
  assert.strictEqual(seq.sequenceSteps[3].trigger_rule, undefined);
  assert.strictEqual(seq.sequenceStageRule(2), 'all_done');
});

run('a saved stage-shaped version loads into stages', () => {
  const seq = editor([]);
  seq.loadSequenceFlow({
    flow_mode: 'custom',
    steps: [],
    stages: [
      { mode: 'serial', steps: [{ step_id: 'a', job_name: 'j' }] },
      { mode: 'parallel', steps: [{ step_id: 'b', job_name: 'j' }, { step_id: 'c', job_name: 'j' }] },
    ],
  });
  assert.strictEqual(seq.sequenceFlow, 'custom');
  assert.strictEqual(seq.sequenceAdvancedGraph, false);
  assert.deepStrictEqual(seq.sequenceStageModes, ['serial', 'parallel']);
  assert.deepStrictEqual(seq.sequenceSteps.map((s) => s._stage), [0, 1, 1]);
});

run('a version that is not stage-shaped opens in advanced graph', () => {
  const seq = editor([]);
  seq.loadSequenceFlow({
    flow_mode: 'custom',
    stages: null,
    steps: [{ step_id: 'a', job_name: 'j', depends_on: [] }, { step_id: 'b', job_name: 'j', depends_on: ['a'] }],
  });
  assert.strictEqual(seq.sequenceAdvancedGraph, true);
  assert.deepStrictEqual(seq.sequenceSteps[1].depends_on, ['a']);
});

run('leaving advanced graph on a non-stage graph needs confirmation', () => {
  const seq = editor(['a', 'b']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceAdvancedGraph = true;
  seq.sequenceSteps[1].depends_on = ['a'];
  seq.sequenceDerived = { flow_mode: 'custom', stages: null };

  global.confirm = () => false;
  assert.strictEqual(seq.setSequenceAdvancedGraph(false), false);
  assert.strictEqual(seq.sequenceAdvancedGraph, true);
  assert.deepStrictEqual(seq.sequenceSteps[1].depends_on, ['a']);

  global.confirm = () => true;
  assert.strictEqual(seq.setSequenceAdvancedGraph(false), true);
  assert.strictEqual(seq.sequenceAdvancedGraph, false);
  assert.strictEqual(seq.sequenceFlow, 'serial');
  assert.deepStrictEqual(seq.sequenceSteps[1].depends_on, []);
});

run('entering advanced graph copies the compiled edges', () => {
  const seq = editor(['a', 'b']);
  seq.validateSequenceSteps = () => {};
  seq.sequenceCompiledSteps = [
    { step_id: 'a', depends_on: [] }, { step_id: 'b', depends_on: ['a'] },
  ];
  seq.setSequenceAdvancedGraph(true);
  assert.deepStrictEqual(seq.sequenceSteps[1].depends_on, ['a']);
});

run('saved defaults merge, keeping other settings', () => {
  const seq = editor(['a']);
  seq.sequenceBaseDefaults = { source_env: 'qa', run_settings: { max_retries: 2, max_workers: 9 } };
  seq.sequenceMaxWorkers = 3;
  assert.deepStrictEqual(seq.buildSequenceDefaults(), {
    source_env: 'qa', run_settings: { max_retries: 2, max_workers: 3 },
  });
  seq.sequenceMaxWorkers = '';
  assert.deepStrictEqual(seq.buildSequenceDefaults(), {
    source_env: 'qa', run_settings: { max_retries: 2 },
  });
});

run('the preview reads compiled steps when there are any', () => {
  const seq = editor(['a', 'b']);
  seq.sequenceCompiledSteps = [
    { step_id: 'a', depends_on: [] }, { step_id: 'b', depends_on: [] },
  ];
  assert.strictEqual(seq.sequenceLevels.length, 1);
  assert.strictEqual(seq.sequenceLevels[0].steps.length, 2);
});
```

- [ ] **Step 3: Run to verify it fails**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequence_flow_editor.py -v`
Expected: FAIL with `TypeError: seq.buildSequenceFlowInput is not a function`.

- [ ] **Step 4: Add the state in `frontend/features/sequences.js`, after `sequenceSaving: false,`**

```javascript
      // Flow editing. Steps stay one flat list in stage order; in Custom each
      // carries a client-only _stage index that is stripped before sending.
      sequenceFlow: 'serial',          // 'serial' | 'parallel' | 'custom'
      sequenceAdvancedGraph: false,    // edit raw depends_on instead of a flow
      sequenceStageModes: ['serial'],  // mode of each stage, by index
      sequenceCompiledSteps: [],       // steps with edges, as returned by validate
      sequenceDerived: null,           // { flow_mode, stages } from validate
      sequenceBaseDefaults: {},        // latest version's defaults, merged on save
      sequenceMaxWorkers: '',          // blank means no stored max_workers
```

- [ ] **Step 5: Add derived values, after `sequenceGlobalIssues`, and update `sequenceLevels`**

```javascript
      get sequenceUsesStages() {
        return !this.sequenceAdvancedGraph && this.sequenceFlow === 'custom';
      },

      // The groups the editor renders: one per stage, or one holding every step.
      get sequenceStepGroups() {
        if (!this.sequenceUsesStages) {
          return [{ index: 0, mode: null, steps: this.sequenceSteps }];
        }
        return this.sequenceStageModes.map((mode, index) => ({
          index, mode, steps: this.sequenceSteps.filter((s) => s._stage === index),
        }));
      },

      get sequencePinnedSequential() {
        const settings = this.sequenceBaseDefaults.run_settings || {};
        return settings.execution_mode === 'sequential';
      },

      get selectedSequenceFlowLabel() {
        const versions = (this.selectedSequence && this.selectedSequence.versions) || [];
        const latest = versions[versions.length - 1];
        const labels = { serial: 'Serial', parallel: 'Parallel', custom: 'Custom' };
        return latest ? labels[latest.flow_mode] || 'Custom' : '';
      },

      sequenceStepShowsRule(step) {
        if (this.sequenceAdvancedGraph) return (step.depends_on || []).length > 0;
        if (this.sequenceFlow === 'parallel') return false;
        if (this.sequenceFlow === 'serial') return this.sequenceSteps[0] !== step;
        const group = this.sequenceStepGroups[step._stage];
        return Boolean(group) && group.mode === 'serial' && group.steps[0] !== step;
      },
```

In `sequenceLevels`, change its first lines so the preview reads compiled steps:

```javascript
      get sequenceLevels() {
        const steps = this.sequenceCompiledSteps.length ? this.sequenceCompiledSteps : this.sequenceSteps;
        const depth = {};
        const byId = {};
        for (const s of steps) byId[s.step_id] = s;
```

and its final loop:

```javascript
        const levels = [];
        for (const s of steps) {
          const d = resolveDepth(s.step_id, new Set());
          (levels[d] = levels[d] || []).push(s);
        }
```

- [ ] **Step 6: Update `openSequenceCreate` and `openSequenceVersionEditor`**

```javascript
      openSequenceCreate() {
        this.sequenceEditorMode = 'create';
        this.sequenceMeta = { name: '', description: '', tags_raw: '' };
        this.sequenceFlow = 'serial';
        this.sequenceAdvancedGraph = false;
        this.sequenceStageModes = ['serial'];
        this.sequenceSteps = [this.newSequenceStep()];
        this.sequencePreconditions = this.emptyPreconditions();
        this.sequenceBaseDefaults = {};
        this.sequenceMaxWorkers = '';
        this.sequenceIssues = [];
        this.sequenceOrder = [];
        this.sequenceCompiledSteps = [];
        this.sequenceDerived = null;
        this.sequenceEditorOpen = true;
      },

      openSequenceVersionEditor() {
        if (!this.selectedSequence) return;
        const latest = this.selectedSequence.versions[this.selectedSequence.versions.length - 1];
        this.sequenceEditorMode = 'version';
        this.sequenceMeta = {
          name: this.selectedSequence.name,
          description: this.selectedSequence.description,
          tags_raw: (this.selectedSequence.tags || []).join(', '),
        };
        this.loadSequenceFlow(latest || { steps: [], stages: null });
        this.sequencePreconditions = this.loadPreconditionsForm(latest ? latest.preconditions : null);
        this.sequenceBaseDefaults = (latest && latest.defaults) || {};
        const workers = (this.sequenceBaseDefaults.run_settings || {}).max_workers;
        this.sequenceMaxWorkers = workers === undefined ? '' : workers;
        this.sequenceIssues = [];
        this.sequenceOrder = [];
        this.sequenceCompiledSteps = [];
        this.sequenceDerived = null;
        this.sequenceEditorOpen = true;
      },

      _applySequenceStages(flowMode, stages) {
        this.sequenceFlow = flowMode;
        this.sequenceAdvancedGraph = false;
        this.sequenceStageModes = stages.map((stage) => stage.mode);
        this.sequenceSteps = stages.flatMap((stage, index) => stage.steps.map((step) => ({
          ...JSON.parse(JSON.stringify(step)), depends_on: [], _stage: index,
        })));
      },

      loadSequenceFlow(version) {
        if (version.stages) {
          this._applySequenceStages(version.flow_mode, version.stages);
          return;
        }
        this.sequenceFlow = 'custom';
        this.sequenceAdvancedGraph = true;
        this.sequenceStageModes = ['serial'];
        this.sequenceSteps = JSON.parse(JSON.stringify(version.steps || []));
      },

      setSequenceFlow(flow) {
        if (flow === 'custom' && this.sequenceFlow !== 'custom') {
          this.sequenceStageModes = [this.sequenceFlow === 'parallel' ? 'parallel' : 'serial'];
          for (const step of this.sequenceSteps) step._stage = 0;
        }
        this.sequenceFlow = flow;
        this.validateSequenceSteps();
      },

      // Returns whether the switch happened, so the checkbox can snap back.
      setSequenceAdvancedGraph(on) {
        if (on) {
          const edges = {};
          for (const s of this.sequenceCompiledSteps) edges[s.step_id] = s.depends_on || [];
          for (const step of this.sequenceSteps) step.depends_on = [...(edges[step.step_id] || [])];
          this.sequenceAdvancedGraph = true;
        } else if (this.sequenceDerived && this.sequenceDerived.stages) {
          this._applySequenceStages(this.sequenceDerived.flow_mode, this.sequenceDerived.stages);
        } else {
          if (!confirm('This graph does not fit serial or staged flows. Switching will replace its dependencies with a serial flow. Continue?')) {
            return false;
          }
          this.sequenceAdvancedGraph = false;
          this.sequenceFlow = 'serial';
          this.sequenceStageModes = ['serial'];
          for (const step of this.sequenceSteps) {
            step.depends_on = [];
            step._stage = 0;
          }
        }
        this.validateSequenceSteps();
        return true;
      },

      // ===== STAGES =====
      addSequenceStage() {
        this.sequenceStageModes.push('serial');
        this.addSequenceStep(this.sequenceStageModes.length - 1);
      },

      removeSequenceStage(index) {
        this.sequenceStageModes.splice(index, 1);
        this.sequenceSteps = this.sequenceSteps.filter((s) => s._stage !== index);
        for (const step of this.sequenceSteps) {
          if (step._stage > index) step._stage -= 1;
        }
        this.validateSequenceSteps();
      },

      moveSequenceStage(index, delta) {
        const target = index + delta;
        if (target < 0 || target >= this.sequenceStageModes.length) return;
        const modes = this.sequenceStageModes;
        [modes[index], modes[target]] = [modes[target], modes[index]];
        for (const step of this.sequenceSteps) {
          if (step._stage === index) step._stage = target;
          else if (step._stage === target) step._stage = index;
        }
        // Array.prototype.sort is stable, so order inside a stage is kept.
        this.sequenceSteps.sort((a, b) => a._stage - b._stage);
        this.validateSequenceSteps();
      },

      setSequenceStageMode(index, mode) {
        this.sequenceStageModes[index] = mode;
        this.validateSequenceSteps();
      },

      _sequenceStageEntrySteps(index) {
        const steps = this.sequenceSteps.filter((s) => s._stage === index);
        return this.sequenceStageModes[index] === 'parallel' ? steps : steps.slice(0, 1);
      },

      sequenceStageRule(index) {
        const entry = this._sequenceStageEntrySteps(index)[0];
        return (entry && entry.trigger_rule) || 'all_success';
      },

      setSequenceStageRule(index, rule) {
        for (const step of this._sequenceStageEntrySteps(index)) step.trigger_rule = rule;
        this.validateSequenceSteps();
      },
```

- [ ] **Step 7: Update `addSequenceStep`**

```javascript
      addSequenceStep(stageIndex = 0) {
        const step = this.newSequenceStep();
        step._stage = stageIndex;
        if (this.sequenceUsesStages) {
          // Insert after the stage's last step, to keep the flat list in stage order.
          const before = this.sequenceSteps.filter((s) => s._stage <= stageIndex).length;
          this.sequenceSteps.splice(before, 0, step);
        } else {
          this.sequenceSteps.push(step);
        }
        this.validateSequenceSteps();
      },
```

- [ ] **Step 8: Update `validateSequenceSteps`, and add the payload builders**

Add above `validateSequenceSteps`:

```javascript
      _sequenceStepPayload(step, keepEdges) {
        const { _stage, depends_on, ...rest } = step;
        return keepEdges ? { ...rest, depends_on: depends_on || [] } : rest;
      },

      // The API rejects depends_on alongside a flow, so only raw graphs send it.
      buildSequenceFlowInput() {
        if (this.sequenceAdvancedGraph) {
          return { steps: this.sequenceSteps.map((s) => this._sequenceStepPayload(s, true)) };
        }
        if (this.sequenceFlow === 'custom') {
          return {
            stages: this.sequenceStepGroups
              .filter((group) => group.steps.length)
              .map((group) => ({
                mode: group.mode,
                steps: group.steps.map((s) => this._sequenceStepPayload(s, false)),
              })),
          };
        }
        return {
          flow_mode: this.sequenceFlow,
          steps: this.sequenceSteps.map((s) => this._sequenceStepPayload(s, false)),
        };
      },

      buildSequenceDefaults() {
        const runSettings = { ...(this.sequenceBaseDefaults.run_settings || {}) };
        const workers = this.sequenceMaxWorkers;
        if (workers === '' || workers === null || workers === undefined) delete runSettings.max_workers;
        else runSettings.max_workers = Number(workers);
        return { ...this.sequenceBaseDefaults, run_settings: runSettings };
      },
```

Inside `validateSequenceSteps`, change the request and handlers:

```javascript
          const result = await api('POST', '/api/sequences/validate', this.buildSequenceFlowInput());
          if (token !== this._sequenceValidateToken) return;
          this.sequenceIssues = result.errors || [];
          this.sequenceOrder = result.order || [];
          this.sequenceCompiledSteps = result.steps || [];
          this.sequenceDerived = { flow_mode: result.flow_mode, stages: result.stages };
        } catch {
          if (token !== this._sequenceValidateToken) return;
          this.sequenceIssues = [];
          this.sequenceOrder = [];
          this.sequenceCompiledSteps = [];
          this.sequenceDerived = null;
        }
```

- [ ] **Step 9: Update `saveSequence`**

Replace the two `api` calls:

```javascript
          if (this.sequenceEditorMode === 'create') {
            const created = await api('POST', '/api/sequences', {
              name: this.sequenceMeta.name,
              description: this.sequenceMeta.description,
              tags,
              ...this.buildSequenceFlowInput(),
              preconditions,
              defaults: this.buildSequenceDefaults(),
            });
            await this.loadSequences();
            await this.selectSequence(created);
          } else {
            await api('POST', `/api/sequences/${this.selectedSequence.id}/versions`, {
              ...this.buildSequenceFlowInput(),
              preconditions,
              defaults: this.buildSequenceDefaults(),
            });
            await this.loadSequences();
            await this.selectSequence(this.selectedSequence);
          }
```

- [ ] **Step 10: Add the launch override**

In `openLaunchSequenceModal`, add to the modal object:

```javascript
          max_workers: '',
          default_max_workers: (defaults.run_settings || {}).max_workers || 4,
```

In `launchSequence`, after the `body` line:

```javascript
        if (m.max_workers !== '' && m.max_workers !== null && m.max_workers !== undefined) {
          body.max_workers = Number(m.max_workers);
        }
```

- [ ] **Step 11: Run to verify they pass**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_sequence_flow_editor.py tests/unit/test_sequence_validate_race.py tests/unit/test_selection_sequence_mode_save.py -v`
Expected: PASS.

- [ ] **Step 12: Commit**

```powershell
git add frontend/features/sequences.js tests/unit/test_sequence_flow_editor.js tests/unit/test_sequence_flow_editor.py
git commit -F <file>   # feat(sequences): add flow editor state
```

---

### Task 12: Flow editor template

**Files:**
- Modify: `frontend/partials/tab-sequences.html`
- Modify: `frontend/index.html` (generated)

- [ ] **Step 1: Add the flow badge, after the `card-title` div in the detail header**

Replace `<div class="card-title" x-text="selectedSequence && selectedSequence.name"></div>` with:

```html
        <div class="flex items-center gap-2">
          <div class="card-title" x-text="selectedSequence && selectedSequence.name"></div>
          <span class="chip" data-testid="sequence-flow-badge" x-text="selectedSequenceFlowLabel"></span>
        </div>
```

- [ ] **Step 2: Add the flow controls, directly above `<div class="space-y-3" data-testid="sequence-step-editor">`**

```html
      <div class="card card-compact mb-3" data-testid="sequence-flow-controls">
        <div class="flex flex-wrap items-center gap-4">
          <span class="field-label mb-0">Flow</span>
          <label class="field-inline">
            <input type="radio" name="sequence-flow" value="serial" data-testid="sequence-flow-serial"
                   :checked="sequenceFlow === 'serial'" :disabled="sequenceAdvancedGraph"
                   @change="setSequenceFlow('serial')" />
            <span>Serial</span>
          </label>
          <label class="field-inline">
            <input type="radio" name="sequence-flow" value="parallel" data-testid="sequence-flow-parallel"
                   :checked="sequenceFlow === 'parallel'" :disabled="sequenceAdvancedGraph"
                   @change="setSequenceFlow('parallel')" />
            <span>Parallel</span>
          </label>
          <label class="field-inline">
            <input type="radio" name="sequence-flow" value="custom" data-testid="sequence-flow-custom"
                   :checked="sequenceFlow === 'custom'" :disabled="sequenceAdvancedGraph"
                   @change="setSequenceFlow('custom')" />
            <span>Custom stages</span>
          </label>
          <label class="field-inline">
            <input type="checkbox" data-testid="sequence-advanced-graph"
                   :checked="sequenceAdvancedGraph"
                   @change="$event.target.checked = setSequenceAdvancedGraph($event.target.checked) ? $event.target.checked : !$event.target.checked" />
            <span>Advanced graph</span>
          </label>
          <label class="field mb-0 ml-auto">
            <span class="field-label">Max parallel jobs</span>
            <input type="number" min="1" class="field-input w-24" data-testid="sequence-max-workers"
                   x-model.number="sequenceMaxWorkers" :disabled="sequencePinnedSequential"
                   placeholder="4" aria-label="max parallel jobs" />
          </label>
        </div>
        <p class="text-xs text-muted mt-2" x-show="sequencePinnedSequential"
           data-testid="sequence-pinned-note">
          This sequence's run settings use sequential mode, so runs are pinned to one worker.
        </p>
        <p class="text-xs text-muted mt-2">
          A selection that references this sequence uses its own run settings instead of this value.
        </p>
      </div>
```

- [ ] **Step 3: Replace the step editor block**

Replace everything from `<div class="space-y-3" data-testid="sequence-step-editor">` through its closing `</div>` (the block ending just above `<button class="btn btn-secondary btn-sm mt-3" data-testid="sequence-add-step"`), and that button, with:

```html
      <div class="space-y-3" data-testid="sequence-step-editor">
        <template x-for="group in sequenceStepGroups" :key="group.index">
          <div :class="sequenceUsesStages ? 'card card-compact border border-slate-200' : ''"
               :data-testid="sequenceUsesStages ? 'sequence-stage-' + group.index : null">
            <template x-if="sequenceUsesStages">
              <div class="mb-3">
                <div class="flex flex-wrap items-end gap-3">
                  <span class="font-medium text-sm" x-text="'Stage ' + (group.index + 1)"></span>
                  <label class="field mb-0">
                    <span class="field-label">Run steps</span>
                    <select class="field-input" :data-testid="'sequence-stage-mode-' + group.index"
                            :value="group.mode"
                            @change="setSequenceStageMode(group.index, $event.target.value)"
                            aria-label="stage mode">
                      <option value="serial">One after another</option>
                      <option value="parallel">In parallel</option>
                    </select>
                  </label>
                  <label class="field mb-0" x-show="group.index > 0">
                    <span class="field-label">Start this stage when</span>
                    <select class="field-input" :data-testid="'sequence-stage-rule-' + group.index"
                            :value="sequenceStageRule(group.index)"
                            @change="setSequenceStageRule(group.index, $event.target.value)"
                            aria-label="stage start rule">
                      <option value="all_success">All previous steps passed</option>
                      <option value="all_done">All previous steps finished, whatever the outcome</option>
                      <option value="any_success">At least one previous step passed</option>
                      <option value="all_failed">All previous steps failed</option>
                    </select>
                  </label>
                  <div class="ml-auto flex gap-1">
                    <button class="btn btn-ghost btn-xs" :data-testid="'sequence-stage-up-' + group.index"
                            :disabled="group.index === 0" @click="moveSequenceStage(group.index, -1)">Up</button>
                    <button class="btn btn-ghost btn-xs" :data-testid="'sequence-stage-down-' + group.index"
                            :disabled="group.index === sequenceStageModes.length - 1"
                            @click="moveSequenceStage(group.index, 1)">Down</button>
                    <button class="btn btn-ghost btn-xs" :data-testid="'sequence-stage-remove-' + group.index"
                            :disabled="sequenceStageModes.length === 1"
                            @click="removeSequenceStage(group.index)">Remove stage</button>
                  </div>
                </div>
                <p class="text-xs text-muted mt-2" x-show="group.index > 0">
                  A previous step that never ran (blocked or cancelled) stops this stage under any rule. A slow step does not count as passed, and a failure set to "carry on" still counts as failed.
                </p>
              </div>
            </template>

            <div class="space-y-3">
              <template x-for="step in group.steps" :key="sequenceSteps.indexOf(step)">
                <div class="card card-compact">
                  <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <label class="field">
                      <span class="field-label">Job</span>
                      <select class="field-input" :data-testid="'sequence-step-job-' + sequenceSteps.indexOf(step)"
                              x-model="step.job_name" @change="onSequenceJobPicked(step)"
                              aria-label="step job">
                        <option value="">Select a job…</option>
                        <template x-for="job in jobs" :key="job.name">
                          <option :value="job.name" x-text="job.name"></option>
                        </template>
                      </select>
                    </label>
                    <label class="field">
                      <span class="field-label">Step id</span>
                      <input class="field-input" :data-testid="'sequence-step-id-' + sequenceSteps.indexOf(step)"
                             x-model="step.step_id" @input="validateSequenceSteps()"
                             aria-label="step id" />
                    </label>
                    <div class="field" x-show="sequenceAdvancedGraph">
                      <span class="field-label">Runs after</span>
                      <div class="flex flex-wrap gap-2" :data-testid="'sequence-step-deps-' + sequenceSteps.indexOf(step)">
                        <template x-for="parent in sequenceParentOptions(step)" :key="parent">
                          <label class="chip cursor-pointer">
                            <input type="checkbox" :checked="(step.depends_on || []).includes(parent)"
                                   @change="toggleSequenceDependency(step, parent)"
                                   :aria-label="'depends on ' + parent" />
                            <span x-text="parent"></span>
                          </label>
                        </template>
                        <span class="text-xs text-muted" x-show="sequenceParentOptions(step).length === 0">
                          No other steps yet
                        </span>
                      </div>
                    </div>
                    <label class="field" x-show="sequenceStepShowsRule(step)">
                      <span class="field-label">Run this step when</span>
                      <select class="field-input" :data-testid="'sequence-step-trigger-' + sequenceSteps.indexOf(step)"
                              x-model="step.trigger_rule" @change="validateSequenceSteps()"
                              aria-label="trigger rule">
                        <option value="all_success">All parents succeeded</option>
                        <option value="all_done">All parents finished, whatever the outcome</option>
                        <option value="any_success">At least one parent succeeded</option>
                        <option value="all_failed">All parents failed</option>
                      </select>
                    </label>
                  </div>

                  <details class="mt-2">
                    <summary class="text-sm cursor-pointer">Advanced</summary>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mt-2">
                      <label class="field-inline">
                        <input type="checkbox" x-model="step.hold_after" aria-label="hold after this step" />
                        <span>Hold after this step</span>
                      </label>
                      <label class="field">
                        <span class="field-label">Wait before this step (seconds)</span>
                        <input type="number" min="0" class="field-input" x-model.number="step.wait_seconds"
                               aria-label="wait seconds" />
                      </label>
                      <label class="field">
                        <span class="field-label">Retries on error</span>
                        <input type="number" min="0" max="10" class="field-input"
                               :data-testid="'sequence-step-retries-' + sequenceSteps.indexOf(step)"
                               x-model.number="step.max_retries" placeholder="Inherit from run settings"
                               aria-label="max retries" />
                      </label>
                      <label class="field">
                        <span class="field-label">Delay between retries (seconds)</span>
                        <input type="number" min="0" class="field-input"
                               x-model.number="step.retry_delay_seconds" placeholder="Inherit from run settings"
                               aria-label="retry delay seconds" />
                      </label>
                      <label class="field">
                        <span class="field-label">If this step fails</span>
                        <select class="field-input" :data-testid="'sequence-step-onfailure-' + sequenceSteps.indexOf(step)"
                                x-model="step.on_failure" aria-label="on failure">
                          <option value="skip_downstream">Carry on — dependent steps decide for themselves</option>
                          <option value="continue">Carry on and don't count this failure against the run</option>
                          <option value="stop">Stop the whole run</option>
                        </select>
                      </label>
                    </div>
                    <p class="text-xs text-muted mt-2">
                      Only errors are retried. A failed data comparison is a real result, so re-running it cannot change the outcome.
                    </p>
                  </details>

                  <template x-for="issue in sequenceIssuesFor(step.step_id)" :key="issue.field + issue.message">
                    <div class="text-xs text-danger mt-1" x-text="issue.message"></div>
                  </template>

                  <div class="mt-2 text-right">
                    <button class="btn btn-ghost btn-xs" :data-testid="'sequence-step-remove-' + sequenceSteps.indexOf(step)"
                            @click="removeSequenceStep(sequenceSteps.indexOf(step))">Remove step</button>
                  </div>
                </div>
              </template>
            </div>

            <button class="btn btn-secondary btn-sm mt-3" x-show="sequenceUsesStages"
                    :data-testid="'sequence-stage-add-step-' + group.index"
                    @click="addSequenceStep(group.index)">Add step to stage</button>
          </div>
        </template>
      </div>

      <button class="btn btn-secondary btn-sm mt-3" data-testid="sequence-add-step"
              x-show="!sequenceUsesStages" @click="addSequenceStep()">Add step</button>
      <button class="btn btn-secondary btn-sm mt-3" data-testid="sequence-add-stage"
              x-show="sequenceUsesStages" @click="addSequenceStage()">Add stage</button>
```

- [ ] **Step 4: Label parallel levels in the preview**

Replace the level row content in `sequence-graph-preview`:

```html
              <div class="flex flex-wrap items-center gap-2">
                <template x-for="step in level.steps" :key="step.step_id">
                  <span class="chip" x-text="step.step_id"></span>
                </template>
                <span class="text-xs text-muted" x-show="level.steps.length > 1"
                      :data-testid="'sequence-preview-together-' + level.index">run together</span>
              </div>
```

- [ ] **Step 5: Add the launch modal override, above the "Repeat execution" label**

```html
        <div>
          <label class="field-label">Max parallel jobs (optional)</label>
          <input type="number" min="1" x-model.number="launchSequenceModal.max_workers" class="field-input"
                 :placeholder="launchSequenceModal.default_max_workers" data-testid="launch-sequence-max-workers" />
          <p class="text-xs text-muted mt-1">Applies to this run only. Use 1 to run one step at a time. Leave blank to use the sequence's value.</p>
        </div>
```

- [ ] **Step 6: Regenerate `index.html`**

Run: `docker compose -f docker-compose.test.yml run --rm test-py node scripts/build-html.js`
Expected: exits 0, and `frontend/index.html` changes.

- [ ] **Step 7: Check the existing sequences spec fails only where expected**

Run: `docker compose -f docker-compose.test.yml run --rm test-e2e node node_modules/@playwright/test/cli.js test tests/e2e/17-sequences.spec.ts --reporter=line`
Expected: FAIL only in `build a two-branch sequence…` and `a cycle is rejected before saving`, because the `sequence-step-deps-*` checkboxes are hidden until Advanced graph is on. Task 14 updates them. All other tests pass.

- [ ] **Step 8: Commit**

```powershell
git add frontend/partials/tab-sequences.html frontend/index.html
git commit -F <file>   # feat(sequences): add flow selector and stage cards to the editor
```

---

### Task 13: Remove the Launch tab Execution Mode control

**Files:**
- Modify: `frontend/partials/tab-launch.html:89-95`
- Modify: `frontend/features/launch.js:33`, `:874`
- Modify: `frontend/app.js:1245`
- Modify: `frontend/help-content.js:190`, `frontend/app-help.js:25`
- Modify: `frontend/index.html` (generated)
- Test: `tests/unit/test_launch_settings_payload.js`, `tests/unit/test_launch_settings_payload.py` (create)

- [ ] **Step 1: Write the Python wrapper `tests/unit/test_launch_settings_payload.py`**

```python
import shutil
import subprocess
from pathlib import Path

import pytest


SPEC = Path(__file__).with_suffix(".js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_launch_settings_payload_behaviour():
    proc = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(SPEC.parents[2]),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
```

- [ ] **Step 2: Write the failing test `tests/unit/test_launch_settings_payload.js`**

```javascript
'use strict';
// The Launch tab no longer has an Execution Mode control, so a value left in saved
// session settings must not reach the API, where it could pin a sequence-backed
// selection to one worker with nothing on screen to explain it.
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');
const launch = fs.readFileSync(path.join(ROOT, 'frontend', 'features', 'launch.js'), 'utf8');
const app = fs.readFileSync(path.join(ROOT, 'frontend', 'app.js'), 'utf8');
const partial = fs.readFileSync(path.join(ROOT, 'frontend', 'partials', 'tab-launch.html'), 'utf8');

assert.ok(!launch.includes('execution_mode'), 'launch.js must not send or default execution_mode');
assert.ok(
  /delete settings\.launchSettings\.execution_mode/.test(app),
  'loadSessionSettings must drop a stale execution_mode'
);
assert.ok(!partial.includes('a11y-launch-execution-mode'), 'the dropdown must be gone');
assert.ok(partial.includes('data-testid="launch-order-note"'), 'the note must replace it');
console.log('ok - launch settings no longer carry execution_mode');
```

- [ ] **Step 3: Run to verify it fails**

Run: `docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_launch_settings_payload.py -v`
Expected: FAIL with `launch.js must not send or default execution_mode`.

- [ ] **Step 4: Replace the dropdown in `frontend/partials/tab-launch.html`**

Replace the `<div>` holding `a11y-launch-execution-mode` with:

```html
        <div data-testid="launch-order-note">
          <span class="field-label">Job order</span>
          <p class="text-xs text-muted mt-1">
            Jobs run in the listed order, one at a time. To run jobs in parallel, use a
            <button type="button" class="underline" data-testid="launch-order-sequences-link"
                    @click="currentView='sequences'">saved sequence</button>.
          </p>
        </div>
```

- [ ] **Step 5: Remove the field from `frontend/features/launch.js`**

Delete line 33, `execution_mode: 'parallel',`, and line 874, `execution_mode: s.execution_mode,`.

- [ ] **Step 6: Drop stale values in `frontend/app.js`**

Replace line 1245:

```javascript
        if (settings.launchSettings) {
          // Saved before the Execution Mode control was removed.
          delete settings.launchSettings.execution_mode;
          Object.assign(this.launchSettings, settings.launchSettings);
        }
```

- [ ] **Step 7: Correct the help text**

In `frontend/help-content.js:190`, replace `Pair rule severity and thresholds with parallel/sequential execution, retries,` with `Pair rule severity and thresholds with retries,`.

In `frontend/app-help.js:25`, replace the `content` string with:

```javascript
      content: 'Maximum number of threads used for concurrent work inside a run, such as file pairs in a multi-file job. Jobs in the list still run one at a time. To run jobs in parallel, use a saved sequence, which has its own Max parallel jobs setting.',
```

- [ ] **Step 8: Regenerate and run the test**

Run:
```powershell
docker compose -f docker-compose.test.yml run --rm test-py node scripts/build-html.js
docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit/test_launch_settings_payload.py tests/unit/test_api.py -v
```
Expected: PASS. `tests/unit/test_api.py` still sends `execution_mode` directly and it is accepted, which confirms API compatibility.

- [ ] **Step 9: Commit**

```powershell
git add frontend/partials/tab-launch.html frontend/features/launch.js frontend/app.js frontend/help-content.js frontend/app-help.js frontend/index.html tests/unit/test_launch_settings_payload.js tests/unit/test_launch_settings_payload.py
git commit -F <file>   # fix(launch): remove the Execution Mode control that never changed job order
```

---

### Task 14: End-to-end specs

**Files:**
- Modify: `tests/e2e/17-sequences.spec.ts`, `tests/e2e/51-multi-day-sequence-recovery.spec.ts`, `tests/e2e/52-sequence-validate-race.spec.ts`, `tests/e2e/37-session-settings-persist.spec.ts`
- Create: `tests/e2e/60-sequence-flow-modes.spec.ts`

- [ ] **Step 1: Turn on Advanced graph in specs that wire edges by hand**

These specs test raw dependency editing, which now lives behind Advanced graph. Directly after the named line, insert the line shown.

`tests/e2e/17-sequences.spec.ts`, after line 44 (`fill('e2e-pipeline')`) and line 80 (`fill('e2e-cycle')`):

```typescript
    await page.getByTestId('sequence-advanced-graph').check();
```

`tests/e2e/52-sequence-validate-race.spec.ts`, after line 56 (`fill('e2e-race-pipeline')`) and line 110 (`fill('e2e-race-cycle')`):

```typescript
    await page.getByTestId('sequence-advanced-graph').check();
```

`tests/e2e/51-multi-day-sequence-recovery.spec.ts`, directly after line 43 (`sequence-new-btn` click):

```typescript
    await this.page.getByTestId('sequence-advanced-graph').check();
```

- [ ] **Step 2: Move spec 37 to a control that still exists**

In `tests/e2e/37-session-settings-persist.spec.ts`:

Replace line 24:
```typescript
      await authedPage.locator('#a11y-launch-max-workers').fill('9');
```

Replace lines 27-32:
```typescript
      await expect
        .poll(() => authedPage.evaluate(() => {
          const raw = localStorage.getItem('etl_session_settings');
          return raw ? String(JSON.parse(raw).launchSettings.max_workers) : null;
        }))
        .toBe('9');
```

Replace line 42:
```typescript
      await expect(authedPage.locator('#a11y-launch-max-workers')).toHaveValue('9');
```

- [ ] **Step 3: Write `tests/e2e/60-sequence-flow-modes.spec.ts`**

```typescript
import { test, expect } from './fixtures';
import { authedContext, createFileJob, waitForTerminal } from './api-helpers';

let jobs: string[];

test.beforeAll(async ({ adminToken }) => {
  const ctx = await authedContext(adminToken);
  try {
    jobs = [];
    for (let i = 0; i < 4; i++) {
      jobs.push((await createFileJob(ctx, `e2e-flow-${i}-${Date.now()}`)).name);
    }
  } finally {
    await ctx.dispose();
  }
});

test.describe('Sequence flow modes', () => {
  test('build a hybrid with stages, reopen it, and launch with an override', async ({ authedPage: page, adminToken }) => {
    const name = `e2e-hybrid-${Date.now()}`;
    await page.goto('/');
    await page.getByRole('button', { name: 'Sequences' }).click();
    await page.getByTestId('sequence-new-btn').click();
    await page.getByTestId('sequence-name-input').fill(name);
    await page.getByTestId('sequence-flow-custom').check();

    // Stage 1: one step.
    await page.getByTestId('sequence-step-job-0').selectOption(jobs[0]);
    await page.getByTestId('sequence-step-id-0').fill('extract');

    // Stage 2: two steps in parallel.
    await page.getByTestId('sequence-add-stage').click();
    await page.getByTestId('sequence-stage-mode-1').selectOption('parallel');
    await page.getByTestId('sequence-step-job-1').selectOption(jobs[1]);
    await page.getByTestId('sequence-step-id-1').fill('load_x');
    await page.getByTestId('sequence-stage-add-step-1').click();
    await page.getByTestId('sequence-step-job-2').selectOption(jobs[2]);
    await page.getByTestId('sequence-step-id-2').fill('load_y');

    // Stage 3: hand back to serial.
    await page.getByTestId('sequence-add-stage').click();
    await page.getByTestId('sequence-step-job-3').selectOption(jobs[3]);
    await page.getByTestId('sequence-step-id-3').fill('publish');
    await page.getByTestId('sequence-stage-rule-2').selectOption('all_done');
    await page.getByTestId('sequence-max-workers').fill('3');

    await expect(page.getByTestId('sequence-preview-together-1')).toBeVisible();
    await expect(page.getByTestId('sequence-save-btn')).toBeEnabled();
    await page.getByTestId('sequence-save-btn').click();

    await page.getByTestId(`sequence-row-${name}`).click();
    await expect(page.getByTestId('sequence-flow-badge')).toHaveText('Custom');
    const detail = page.getByTestId('sequence-detail');
    await expect(detail.locator('tbody tr').last()).toContainText('load_x, load_y');

    // Reopen: the stages come back from the saved edges.
    await page.getByTestId('sequence-edit-btn').click();
    await expect(page.getByTestId('sequence-flow-custom')).toBeChecked();
    await expect(page.getByTestId('sequence-stage-mode-1')).toHaveValue('parallel');
    await expect(page.getByTestId('sequence-stage-rule-2')).toHaveValue('all_done');
    await expect(page.getByTestId('sequence-max-workers')).toHaveValue('3');
    await page.getByTestId('sequence-editor').getByRole('button', { name: 'Cancel' }).click();

    await page.getByTestId('sequence-launch-btn').click();
    await page.getByTestId('launch-sequence-target-env').selectOption('dev');
    await page.getByTestId('launch-sequence-max-workers').fill('2');
    const launch = page.waitForResponse(
      (r) => /\/api\/sequences\/\d+\/launch$/.test(r.url()) && r.request().method() === 'POST'
    );
    await page.getByTestId('launch-sequence-submit-btn').click();
    const response = await launch;
    expect(response.ok()).toBeTruthy();
    expect(response.request().postDataJSON().max_workers).toBe(2);

    const ctx = await authedContext(adminToken);
    try {
      const { run_id } = await response.json();
      await waitForTerminal(ctx, run_id);
      const steps = await (await ctx.get(`/api/runs/${run_id}/steps`)).json();
      const byId = Object.fromEntries(steps.map((s: any) => [s.step_id, s]));
      expect(byId.publish.depends_on).toEqual(['load_x', 'load_y']);
      expect(byId.load_x.depends_on).toEqual(['extract']);
    } finally {
      await ctx.dispose();
    }
  });

  test('the monitor shows a parallel group on one level', async ({ authedPage: page, adminToken }) => {
    const ctx = await authedContext(adminToken);
    let runId = '';
    try {
      const created = await ctx.post('/api/sequences', {
        data: {
          name: `e2e-monitor-${Date.now()}`,
          stages: [
            { mode: 'parallel', steps: [
              { step_id: 'left', job_name: jobs[0] },
              // The hold keeps the run active while the monitor is checked.
              { step_id: 'right', job_name: jobs[1], hold_after: true },
            ] },
            { mode: 'serial', steps: [{ step_id: 'join', job_name: jobs[2] }] },
          ],
        },
      });
      expect(created.ok()).toBe(true);
      const { id } = await created.json();
      const launched = await ctx.post(`/api/sequences/${id}/launch`, {
        data: { source_env: 'dev', target_env: 'dev' },
      });
      expect(launched.ok()).toBe(true);
      runId = (await launched.json()).run_id;

      await page.goto('/');
      await page.getByTestId('nav-tab-monitor').click();
      const run = page.getByTestId(`monitor-run-${runId}`);
      await expect(run).toBeVisible({ timeout: 30_000 });
      const timeline = run.getByTestId('monitor-step-timeline');
      await expect(timeline).toBeVisible({ timeout: 30_000 });
      const leftLevel = run.getByTestId('monitor-step-left').locator('xpath=ancestor::div[contains(@class, "py-1")][1]');
      await expect(leftLevel.getByTestId('monitor-step-right')).toBeVisible();
      await expect(leftLevel.getByTestId('monitor-step-join')).toHaveCount(0);
    } finally {
      if (runId) await ctx.post(`/api/runs/${runId}/cancel`);
      await ctx.dispose();
    }
  });

  test('a graph that does not fit stages opens in advanced mode and warns before rewriting', async ({ authedPage: page, adminToken }) => {
    const ctx = await authedContext(adminToken);
    const name = `e2e-graph-${Date.now()}`;
    try {
      const created = await ctx.post('/api/sequences', {
        data: {
          name,
          steps: [
            { step_id: 'a', job_name: jobs[0], depends_on: [] },
            { step_id: 'b', job_name: jobs[1], depends_on: ['a'] },
            { step_id: 'c', job_name: jobs[2], depends_on: [] },
            { step_id: 'd', job_name: jobs[3], depends_on: ['c'] },
          ],
        },
      });
      expect(created.ok()).toBe(true);

      await page.goto('/');
      await page.getByRole('button', { name: 'Sequences' }).click();
      await page.getByTestId(`sequence-row-${name}`).click();
      await expect(page.getByTestId('sequence-flow-badge')).toHaveText('Custom');
      await page.getByTestId('sequence-edit-btn').click();

      const advanced = page.getByTestId('sequence-advanced-graph');
      await expect(advanced).toBeChecked();
      await expect(page.getByTestId('sequence-step-deps-1')).toBeVisible();

      // Dismissing keeps the graph as it is.
      page.once('dialog', (d) => d.dismiss());
      await advanced.click();
      await expect(advanced).toBeChecked();
      await expect(page.getByTestId('sequence-step-deps-1')).toBeVisible();

      // Accepting rewrites it to a serial flow.
      page.once('dialog', (d) => d.accept());
      await advanced.click();
      await expect(advanced).not.toBeChecked();
      await expect(page.getByTestId('sequence-flow-serial')).toBeChecked();
      await expect(page.getByTestId('sequence-step-deps-1')).toBeHidden();
    } finally {
      await ctx.dispose();
    }
  });

  // rerun_downstream: the checkbox reaches the API only when ticked. The run is
  // stubbed because this checks the control, and the behaviour is proved in
  // tests/integration/test_sequence_hybrid_flow.py.
  test('the History restart control sends rerun_downstream only when ticked', async ({ authedPage: page }) => {
    const runId = '00000000-0000-0000-0000-000000000060';
    const bodies: (string | null)[] = [];
    const run = {
      run_id: runId, status: 'FAILED', source_env: 'dev', target_env: 'dev',
      total_tests: 2, passed: 1, failed: 1, slow: 0, error: 0, results: [],
    };
    await page.route(`**/api/runs/${runId}`, (route) => route.fulfill({ json: run }));
    await page.route(`**/api/runs/${runId}/steps`, (route) => route.fulfill({
      json: [{ step_id: 'a', step_index: 0, job_name: 'a', status: 'FAILED', depends_on: [] }],
    }));
    await page.route(`**/api/runs/${runId}/restart`, async (route) => {
      if (route.request().method() === 'GET') return route.fulfill({ json: null });
      bodies.push(route.request().postData());
      return route.fulfill({ status: 404, json: { detail: 'stubbed' } });
    });

    await page.goto('/');
    await page.getByRole('button', { name: 'History' }).click();
    await page.evaluate((id) => {
      const root = document.querySelector('[x-data]') as any;
      return (window as any).Alpine.$data(root).viewRunDetail(id);
    }, runId);

    const restart = page.getByRole('button', { name: 'Restart from failure' });
    await expect(restart).toBeVisible();
    const option = page.getByTestId('history-restart-rerun-downstream');
    await expect(option).not.toBeChecked();

    await restart.click();
    await expect.poll(() => bodies.length).toBe(1);
    expect(bodies[0]).toBeFalsy();

    await option.check();
    await restart.click();
    await expect.poll(() => bodies.length).toBe(2);
    expect(JSON.parse(bodies[1]!)).toEqual({ rerun_downstream: true });
  });

  test('the Launch tab explains job order instead of offering a mode', async ({ authedPage: page }) => {
    await page.goto('/');
    await page.getByTestId('nav-tab-jobs').click();
    await expect(page.getByTestId('launch-order-note')).toBeVisible();
    await expect(page.locator('#a11y-launch-execution-mode')).toHaveCount(0);
    await page.getByTestId('launch-order-sequences-link').click();
    await expect(page.getByTestId('sequences-panel')).toBeVisible();
  });
});
```

- [ ] **Step 4: Run the affected specs**

Run:
```powershell
docker compose -f docker-compose.test.yml run --rm test-e2e node node_modules/@playwright/test/cli.js test tests/e2e/17-sequences.spec.ts tests/e2e/37-session-settings-persist.spec.ts tests/e2e/51-multi-day-sequence-recovery.spec.ts tests/e2e/52-sequence-validate-race.spec.ts tests/e2e/60-sequence-flow-modes.spec.ts --reporter=line
```
Expected: PASS, apart from anything listed in the baseline file. For a failure, use superpowers:systematic-debugging, and treat it as a product bug until shown otherwise.

- [ ] **Step 5: Commit**

```powershell
git add tests/e2e/17-sequences.spec.ts tests/e2e/37-session-settings-persist.spec.ts tests/e2e/51-multi-day-sequence-recovery.spec.ts tests/e2e/52-sequence-validate-race.spec.ts tests/e2e/60-sequence-flow-modes.spec.ts
git commit -F <file>   # test(e2e): cover sequence flow modes in the browser
```

---

### Task 15: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `frontend/help-content.js`

- [ ] **Step 1: Add a README section**

Add this under the sequences documentation. Find the location by searching `README.md` for `Execution Sequence`:

````markdown
### Serial, parallel, and hybrid sequences

A saved sequence runs as serial, parallel, or a mix of stages. In the editor, choose **Serial**, **Parallel**, or **Custom stages**. **Advanced graph** edits the dependencies directly for any shape.

The API takes the same choices, and everything is stored as `depends_on`:

```json
{
  "name": "nightly",
  "stages": [
    {"mode": "serial",   "steps": [{"step_id": "extract", "job_name": "extract"}]},
    {"mode": "parallel", "steps": [{"step_id": "load_x", "job_name": "load_x"},
                                   {"step_id": "load_y", "job_name": "load_y"}]},
    {"mode": "serial",   "steps": [{"step_id": "publish", "job_name": "publish"}]}
  ],
  "defaults": {"run_settings": {"max_workers": 4}}
}
```

- For a flat list, send `steps` with `"flow_mode": "serial"` or `"parallel"`. Without `flow_mode`, `steps` keep their own `depends_on`.
- GET and validate responses return the derived `flow_mode` and `stages`. `stages` is `null` for a graph that does not fit stages.
- `POST /api/sequences/{id}/launch` accepts `max_workers` for one run. `1` runs one step at a time.
- A selection that references a sequence uses its own run settings, not the sequence's.

The next stage starts according to its first steps' `trigger_rule`, by default `all_success`:

| Previous stage | `all_success` | `all_done` | `any_success` |
|---|---|---|---|
| All `PASSED` | Runs | Runs | Runs |
| One `FAILED` or `ERROR` | Blocked | Runs | Runs if another `PASSED` |
| One `SLOW` or hold-skipped | Blocked | Runs | Runs if another `PASSED` |
| One never ran (blocked or cancelled) | Blocked | Blocked | Blocked |

**Restart from failure** skips steps that passed and runs the rest. A passed step that ran after a failed one, under `all_done` or `any_success`, stays carried over, so it does not see the new result. To run those again too, tick **Also re-run later steps**, or send `{"rerun_downstream": true}` to `POST /api/runs/{id}/restart`. This repeats their side effects, such as file transfers, so it is off by default.

A plain job list on the Launch tab always runs in order, one job at a time, and carries on after a failure. A serial sequence defaults to stopping at the first failure.

If a job lists another job in the sequence under `depends_on`, the step running it must come after a step running that job. This is checked when saving and at launch.
````

- [ ] **Step 2: Document Docker testing in the README**

Replace nothing; add this section directly above the `Run the live SQL Server integration test with Docker Compose:` line:

````markdown
Run the test suites in Docker:

```powershell
docker compose -f docker-compose.test.yml build test-py
docker compose -f docker-compose.test.yml run --rm test-py
docker compose -f docker-compose.test.yml run --rm test-e2e
docker compose -f docker-compose.test.yml run --rm test-py-postgres
docker compose -f docker-compose.test.yml down -v
```

The source is bind-mounted, so code changes need no rebuild. Rebuild after changing `requirements.txt` or `package-lock.json`. Tests known to fail in this environment are listed in `docs/superpowers/plans/2026-11-03-docker-test-baseline.md`.

````

- [ ] **Step 3: Update in-app help**

In `frontend/help-content.js:379`, replace the `intro` string with:

```javascript
      intro: 'Run jobs serially, in parallel, or in stages that hand over from one to the next. Choose a flow in the sequence editor, or use Advanced graph to model any dependencies. Validate acyclic topology and recover without rerunning passed nodes.',
```

- [ ] **Step 4: Regenerate `index.html` in case help is inlined, and check for a diff**

Run: `docker compose -f docker-compose.test.yml run --rm test-py node scripts/build-html.js`
Expected: exits 0.

- [ ] **Step 5: Run the full Python suite**

Run in the background:
```powershell
docker compose -f docker-compose.test.yml run --rm test-py python -m pytest tests/unit tests/integration -q --tb=short
```
Expected: every failure is on the baseline list, and the new tests pass.

- [ ] **Step 6: Run the full Playwright suite**

Run in the background:
```powershell
docker compose -f docker-compose.test.yml run --rm test-e2e node node_modules/@playwright/test/cli.js test --reporter=line
```
Expected: every failure is on the baseline list.

- [ ] **Step 7: Run the Postgres hybrid test**

Run:
```powershell
docker compose -f docker-compose.test.yml run --rm test-py-postgres
docker compose -f docker-compose.test.yml down -v
```
Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add README.md frontend/help-content.js frontend/index.html
git commit -F <file>   # docs: describe sequence flow modes and running tests in Docker
```

- [ ] **Step 9: Finish the branch**

Use superpowers:verification-before-completion, then superpowers:finishing-a-development-branch.
