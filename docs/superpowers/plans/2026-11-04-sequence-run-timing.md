# Sequence Run Timing, Job Progress Bars and Gantt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a colored progress bar per job in UI and CLI, give each run a complete end-to-end elapsed time with start and end in UI, API and CLI, and add a colored Gantt chart in the UI.

**Architecture:** `run_steps` gains start and end times, set in `RunStepRepository.update_status`. One new service, `api/services/run_timing.py`, turns a run and its steps into a timing document. `/timing`, `/stream`, the sequence history endpoint, the CLI and the UI all consume that document, so the progress rule exists only in Python.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, Typer, rich, Alpine.js, plain CSS, Playwright, pytest.

**Spec:** `docs/superpowers/specs/2026-11-04-sequence-run-timing-design.md`

**Conventions for every task:**

- Run Python tests with `C:\Python314\python.exe -m pytest <path> -q -p no:cacheprovider`. The repo `.venv` has no pytest. Don't trust `rtk pytest` summaries, which can be stale.
- Run `node scripts/build-html.js` after any partial or template edit. `frontend/index.html` is generated and committed.
- Run Playwright with `node node_modules/@playwright/test/cli.js test <spec>`, not `npx playwright`.
- Use the PowerShell tool for `git` inside a worktree.
- The working tree may hold unrelated changes (`demo/`, `docker-compose.demo.yml`, `uv.lock`). Stage only the files each task names.
- End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

## File map

| File | Change |
|---|---|
| `etl_framework/repository/models.py` | `BLOCKED` terminal; `RunStep.started_at`, `finished_at`; `TestRun.sequence_id` |
| `etl_framework/repository/database.py` | Add columns on existing databases; backfill `sequence_id` |
| `etl_framework/repository/repository.py` | Stamp step times; set `sequence_id`; `list_runs_for_sequence` |
| `api/services/run_timing.py` | New: timing and progress logic |
| `api/schemas.py` | `RunTimingOut`, `StepTimingOut`, `SequenceRunTimingOut`; new `RunStepOut` fields |
| `api/routes/runs.py` | `GET /{run_id}/timing`; `timing` in `/stream` |
| `api/routes/sequences.py` | `GET /{sequence_id}/runs` |
| `etl_framework/cli/app.py` | `BLOCKED` handling, displays in `atom run`, new commands |
| `etl_framework/cli/render.py` | Timing tables and duration formatting |
| `etl_framework/cli/progress.py` | New: live bars and plain status lines |
| `pyproject.toml`, `requirements.txt` | Explicit `rich` dependency |
| `scripts/build-html.js` | One level of shared partial includes |
| `frontend/features/run-timing.js` | New: Gantt layout, formatting, tab and toggle state |
| `frontend/partials/shared/run-gantt.html` | New: Timeline markup used in two tabs |
| `frontend/partials/tab-monitor.html` | New run card |
| `frontend/partials/tab-history.html` | Timeline card, elapsed and held |
| `frontend/partials/tab-sequences.html` | Recent runs table |
| `frontend/features/monitor.js` | Receive timing; remove old step helpers |
| `frontend/features/history.js`, `frontend/features/sequences.js` | Load timing and recent runs |
| `frontend/app.js`, `frontend/app-config.js`, `frontend/index.template.html`, `frontend/styles.css` | Register slice, `BLOCKED`, styles |
| `tests/unit/test_run_timing.py`, `test_run_timing_repository.py`, `test_run_timing_api.py`, `test_cli_progress.py`, `test_run_timing_frontend.py`, `test_run_timing_frontend.js` | New tests |
| `tests/e2e/60-sequence-flow-modes.spec.ts` | Monitor test updated; timing test added |

---

## Task 1: Treat BLOCKED as a finished run

`RunExecutor` writes `BLOCKED` with `completed_at`, but no terminal list includes it. Streams never end for these runs, and `atom run` waits until timeout.

**Files:**
- Modify: `etl_framework/repository/models.py:13-15`
- Modify: `etl_framework/cli/app.py:32-36`, `etl_framework/cli/app.py:102-109`
- Modify: `frontend/app-config.js:2`, `frontend/app.js:5-6`
- Test: `tests/unit/test_run_timing_api.py`, `tests/unit/test_cli_app.py`

- [x] **Step 1: Write the failing tests**

Create `tests/unit/test_run_timing_api.py`:

```python
"""API tests for run timing, sequence history and the stream."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine, monkeypatch):
    from api.main import app
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import TokenRepository

    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")
    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _blocked_run(engine, run_id="blocked-run"):
    from etl_framework.repository.repository import RunRepository, RunStepRepository

    with Session(engine) as db:
        RunRepository(db).create_run(run_id, "dev", "prod", {})
        RunStepRepository(db).materialize_steps(run_id, [
            SequenceStepRef(step_id="a", job_name="job_a"),
        ])
        RunStepRepository(db).set_status_by_step_id(run_id, "a", "BLOCKED")
        RunRepository(db).update_run_status(
            run_id, "BLOCKED", started_at=T0, completed_at=T0 + timedelta(seconds=30),
        )


def test_blocked_run_ends_the_stream(client, engine):
    _blocked_run(engine)
    with client.stream("GET", "/api/runs/blocked-run/stream") as response:
        body = response.read().decode()
    assert "event: done" in body
```

Append to `tests/unit/test_cli_app.py`:

```python
def test_run_stops_waiting_for_a_blocked_run_and_exits_failed(fake_client):
    from etl_framework.cli.app import app

    fake_client({
        ("POST", "/api/selections/3/launch"): {"run_id": "r-1"},
        ("GET", "/api/runs/r-1/status"): {
            "run_id": "r-1", "status": "BLOCKED", "passed": 1, "failed": 0, "error": 0,
        },
    })
    result = runner.invoke(app, BASE_ARGS + [
        "run", "3", "--source-env", "dev", "--poll-interval", "0", "--timeout", "1",
    ])
    assert result.exit_code == 1
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_cli_app.py::test_run_stops_waiting_for_a_blocked_run_and_exits_failed -q -p no:cacheprovider`
Expected: FAIL, exit code 6 (timeout) instead of 1.

Don't run the stream test unfixed. It loops forever while the bug exists. The fix in Step 3 is what makes it end.

- [x] **Step 3: Add BLOCKED everywhere**

In `etl_framework/repository/models.py`:

```python
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED", "BLOCKED"}
)
```

In `etl_framework/cli/app.py`:

```python
TERMINAL_STATUSES = frozenset(
    {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED", "BLOCKED"}
)
```

Also in `etl_framework/cli/app.py`, replace `_gate_exit_code`:

```python
def _gate_exit_code(status: dict) -> int:
    if status.get("status") == "CANCELLED":
        return EXIT_CANCELLED
    if (status.get("error") or 0) > 0 or status.get("status") == "ERROR":
        return EXIT_ERROR
    # A blocked run did not run everything it was asked to, so it can't pass a gate.
    if (status.get("failed") or 0) > 0 or status.get("status") in ("FAILED", "BLOCKED"):
        return EXIT_FAILED
    return EXIT_PASSED
```

In `frontend/app-config.js`:

```js
  const TERMINAL_STATUSES = ['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED', 'BLOCKED'];
```

In `frontend/app.js`, lines 5-6:

```js
const isTerminalStatusValue = APP_CONFIG.isTerminalStatusValue || ((status) =>
  ['PASSED', 'FAILED', 'SLOW', 'ERROR', 'COMPLETED', 'CANCELLED', 'BLOCKED'].includes(String(status || '').toUpperCase()));
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_api.py tests/unit/test_cli_app.py -q -p no:cacheprovider --timeout=120`
Expected: PASS. If `--timeout` is rejected because `pytest-timeout` isn't installed, drop that flag.

- [x] **Step 5: Run the full unit suite to catch side effects**

`TERMINAL_STATUSES` is read by `batch_launch`, `run_report` and `request_cancel`.

Run: `C:\Python314\python.exe -m pytest tests/unit -q -p no:cacheprovider -n 4`
Expected: no new failures. If a test asserts the old behavior (for example, that a blocked run isn't terminal), confirm it describes the bug rather than a requirement, and update it. Stop and report if a test shows `BLOCKED` runs were deliberately treated as unfinished.

- [x] **Step 6: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/cli/app.py frontend/app-config.js frontend/app.js tests/unit/test_run_timing_api.py tests/unit/test_cli_app.py
git commit -m "fix(runs): treat BLOCKED as a finished run"
```

---

## Task 2: Record step times

**Files:**
- Modify: `etl_framework/repository/models.py` (`RunStep`, `TestRun`)
- Modify: `etl_framework/repository/repository.py:407-434`, `etl_framework/repository/repository.py:1728-1739`
- Modify: `api/schemas.py:518-540`
- Test: `tests/unit/test_run_timing_repository.py`

- [x] **Step 1: Write the failing tests**

Create `tests/unit/test_run_timing_repository.py`:

```python
"""Step timestamps, sequence_id and the upgrade of existing databases."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository, RunStepRepository


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _step(db, run_id="r1"):
    RunRepository(db).create_run(run_id, "dev", "prod", {})
    RunStepRepository(db).materialize_steps(run_id, [
        SequenceStepRef(step_id="a", job_name="job_a"),
    ])
    return RunStepRepository(db)


def test_new_step_has_no_times():
    db = _session()
    repo = _step(db)
    step = repo.get_step_by_step_id("r1", "a")
    assert step.started_at is None
    assert step.finished_at is None


def test_running_sets_start_and_leaving_running_sets_end():
    db = _session()
    repo = _step(db)
    repo.set_status_by_step_id("r1", "a", "RUNNING")
    step = repo.get_step_by_step_id("r1", "a")
    assert step.started_at is not None
    assert step.finished_at is None

    repo.set_status_by_step_id("r1", "a", "PASSED")
    step = repo.get_step_by_step_id("r1", "a")
    assert step.finished_at is not None
    assert step.finished_at >= step.started_at


def test_running_again_does_not_reset_start():
    db = _session()
    repo = _step(db)
    repo.set_status_by_step_id("r1", "a", "RUNNING")
    first = repo.get_step_by_step_id("r1", "a").started_at
    repo.set_status_by_step_id("r1", "a", "RUNNING")
    assert repo.get_step_by_step_id("r1", "a").started_at == first


def test_later_status_changes_do_not_move_the_end():
    db = _session()
    repo = _step(db)
    repo.set_status_by_step_id("r1", "a", "RUNNING")
    repo.set_status_by_step_id("r1", "a", "HELD")
    held_end = repo.get_step_by_step_id("r1", "a").finished_at
    assert held_end is not None

    repo.set_status_by_step_id("r1", "a", "APPROVED")
    assert repo.get_step_by_step_id("r1", "a").finished_at == held_end


def test_never_started_step_gets_no_times():
    db = _session()
    repo = _step(db)
    repo.set_status_by_step_id("r1", "a", "BLOCKED")
    step = repo.get_step_by_step_id("r1", "a")
    assert step.started_at is None
    assert step.finished_at is None


def test_create_run_sets_sequence_id_from_snapshot():
    db = _session()
    run = RunRepository(db).create_run("r2", "dev", "prod", {"sequence": {"id": 7, "name": "s"}})
    assert run.sequence_id == 7


def test_create_run_without_sequence_leaves_it_empty():
    db = _session()
    assert RunRepository(db).create_run("r3", "dev", "prod", {}).sequence_id is None
    assert RunRepository(db).create_run("r4", "dev", "prod", None).sequence_id is None
    assert RunRepository(db).create_run("r5", "dev", "prod", {"sequence": "odd"}).sequence_id is None


def test_list_runs_for_sequence_is_newest_first_and_pages():
    db = _session()
    repo = RunRepository(db)
    for index in range(3):
        repo.create_run(f"s-{index}", "dev", "prod", {"sequence": {"id": 7}})
    repo.create_run("other", "dev", "prod", {"sequence": {"id": 8}})

    assert [r.run_id for r in repo.list_runs_for_sequence(7, limit=2, offset=0)] == ["s-2", "s-1"]
    assert [r.run_id for r in repo.list_runs_for_sequence(7, limit=2, offset=2)] == ["s-0"]


def _old_database():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE test_runs (id INTEGER PRIMARY KEY, run_id VARCHAR(36), config_snapshot JSON)"
        ))
        conn.execute(text("CREATE TABLE run_steps (id INTEGER PRIMARY KEY, run_id VARCHAR(36))"))
        conn.execute(text(
            "INSERT INTO test_runs (run_id, config_snapshot) VALUES "
            "('seq', '{\"sequence\": {\"id\": 5}}'), ('plain', '{}'), ('bad', 'not json'), ('none', NULL)"
        ))
    return engine


def test_upgrade_adds_columns_and_backfills_once():
    from etl_framework.repository.database import _ensure_run_timing_columns

    engine = _old_database()
    _ensure_run_timing_columns(engine)

    inspector = inspect(engine)
    assert {"started_at", "finished_at"} <= {c["name"] for c in inspector.get_columns("run_steps")}
    assert "sequence_id" in {c["name"] for c in inspector.get_columns("test_runs")}
    assert "ix_test_runs_sequence_id" in {i["name"] for i in inspector.get_indexes("test_runs")}

    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT run_id, sequence_id FROM test_runs")).fetchall())
    assert rows == {"seq": 5, "plain": None, "bad": None, "none": None}

    # Second start: nothing to add, and no backfill overwriting current values.
    with engine.begin() as conn:
        conn.execute(text("UPDATE test_runs SET sequence_id = 99 WHERE run_id = 'seq'"))
    _ensure_run_timing_columns(engine)
    with engine.connect() as conn:
        value = conn.execute(text("SELECT sequence_id FROM test_runs WHERE run_id = 'seq'")).scalar()
    assert value == 99
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_repository.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError` on `started_at`, and an import error for `_ensure_run_timing_columns`.

- [x] **Step 3: Add the model columns**

In `etl_framework/repository/models.py`, after `run_batch_id` on `TestRun`:

```python
    sequence_id = Column(Integer, nullable=True, index=True)
```

After `carried_over` on `RunStep`:

```python
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
```

- [x] **Step 4: Stamp times and set sequence_id**

In `etl_framework/repository/repository.py`, replace `update_status`:

```python
    def update_status(
        self, run_id: str, step_index: int, status: str, **kwargs
    ) -> RunStep | None:
        step = self.get_step(run_id, step_index)
        if step is None:
            return None
        now = datetime.now(timezone.utc)
        if status == "RUNNING" and step.started_at is None:
            step.started_at = now
        if step.status == "RUNNING" and status != "RUNNING" and step.finished_at is None:
            step.finished_at = now
        step.status = status
        for k, v in kwargs.items():
            setattr(step, k, v)
        self._db.commit()
        self._db.refresh(step)
        return step
```

In `RunRepository.create_run`, add this argument to the `TestRun(...)` call:

```python
            sequence_id=_sequence_id_from_snapshot(config_snapshot),
```

Add this module-level helper above `class RunRepository`:

```python
def _sequence_id_from_snapshot(config_snapshot: dict | None) -> int | None:
    sequence = (config_snapshot or {}).get("sequence")
    return sequence.get("id") if isinstance(sequence, dict) else None
```

Add this method to `RunRepository`, after `get_run`:

```python
    def list_runs_for_sequence(
        self, sequence_id: int, limit: int = 20, offset: int = 0
    ) -> list[TestRun]:
        return (
            self._db.query(TestRun)
            .filter(TestRun.sequence_id == sequence_id)
            .order_by(TestRun.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
```

- [x] **Step 5: Upgrade existing databases**

In `etl_framework/repository/database.py`, change the top imports:

```python
from __future__ import annotations
import json
import os
```

In `init_db`, add after `_ensure_export_job_columns(engine)`:

```python
    _ensure_run_timing_columns(engine)
```

Add after `_ensure_export_job_columns`:

```python
def _ensure_run_timing_columns(bind) -> None:
    """Add step times and sequence_id on existing databases.

    Not SQLite-gated: /timing and the stream read these columns on every
    dialect. The datetime type comes from the dialect because Postgres has no
    DATETIME. Old runs get sequence_id from their snapshot only when the column
    is first added, so later starts never overwrite anything.
    """
    tables = set(inspect(bind).get_table_names())
    if not {"test_runs", "run_steps"}.issubset(tables):
        return
    datetime_type = DateTime(timezone=True).compile(dialect=bind.dialect)
    with bind.begin() as conn:
        ensure_column(conn, "run_steps", "started_at", f"ALTER TABLE run_steps ADD COLUMN started_at {datetime_type}")
        ensure_column(conn, "run_steps", "finished_at", f"ALTER TABLE run_steps ADD COLUMN finished_at {datetime_type}")
        added = ensure_column(conn, "test_runs", "sequence_id", "ALTER TABLE test_runs ADD COLUMN sequence_id INTEGER")
        ensure_index(conn, "ix_test_runs_sequence_id", "CREATE INDEX ix_test_runs_sequence_id ON test_runs (sequence_id)")
        if added:
            _backfill_run_sequence_ids(conn)


def _backfill_run_sequence_ids(conn) -> None:
    rows = conn.execute(
        text("SELECT id, config_snapshot FROM test_runs WHERE config_snapshot IS NOT NULL")
    ).fetchall()
    for row_id, snapshot in rows:
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except ValueError:
                continue
        sequence = snapshot.get("sequence") if isinstance(snapshot, dict) else None
        sequence_id = sequence.get("id") if isinstance(sequence, dict) else None
        if sequence_id is not None:
            conn.execute(
                text("UPDATE test_runs SET sequence_id = :sequence_id WHERE id = :row_id"),
                {"sequence_id": sequence_id, "row_id": row_id},
            )
```

Change the SQLAlchemy import in the same file:

```python
from sqlalchemy import DateTime, create_engine, inspect, text
```

- [x] **Step 6: Expose the times on RunStepOut**

In `api/schemas.py`, after `carried_over: bool = False` in `RunStepOut`:

```python
    started_at: datetime | None = None
    finished_at: datetime | None = None
```

- [x] **Step 7: Run tests to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_repository.py tests/unit/test_run_steps.py tests/unit/test_dag_run_steps.py tests/unit/test_dag_executor.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/repository.py etl_framework/repository/database.py api/schemas.py tests/unit/test_run_timing_repository.py
git commit -m "feat(runs): record when each sequence step starts and ends"
```

---

## Task 3: Timing and progress service

**Files:**
- Create: `api/services/run_timing.py`
- Test: `tests/unit/test_run_timing.py`

- [x] **Step 1: Write the failing tests**

Create `tests/unit/test_run_timing.py`:

```python
"""The timing document: elapsed time, step runtime and the progress rule."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.services.run_timing import (
    build_run_elapsed,
    build_run_timing,
    estimate_durations,
    job_family,
    load_timing_context,
)

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def run(status="RUNNING", started_at=T0, completed_at=None):
    return SimpleNamespace(
        run_id="r1", status=status, started_at=started_at, completed_at=completed_at,
    )


def step(status="PENDING", started=None, finished=None, **kwargs):
    defaults = dict(
        step_id="a", step_index=0, job_name="job_a", status=status, attempt=0,
        wait_seconds=0, carried_over=False, held_at=None, released_at=None,
        started_at=at(started) if started is not None else None,
        finished_at=at(finished) if finished is not None else None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def timing_of(the_step, the_run=None, estimates=None, job_types=None, now_seconds=100):
    return build_run_timing(
        the_run or run(), [the_step], job_types or {}, estimates or {}, at(now_seconds),
    )["steps"][0]


# --- end-to-end time ------------------------------------------------------

def test_elapsed_of_finished_run_uses_completed_at():
    result = build_run_elapsed(run("PASSED", T0, at(600)), [], at(9999))
    assert result == {"elapsed_seconds": 600.0, "held_seconds": 0.0, "active_seconds": 600.0}


def test_elapsed_of_live_run_runs_to_now():
    assert build_run_elapsed(run(), [], at(90))["elapsed_seconds"] == 90.0


def test_elapsed_is_null_before_start():
    result = build_run_elapsed(run("PENDING", None), [], at(90))
    assert result == {"elapsed_seconds": None, "held_seconds": 0.0, "active_seconds": None}


def test_elapsed_is_null_for_finished_run_without_completed_at():
    assert build_run_elapsed(run("ERROR", T0, None), [], at(90))["elapsed_seconds"] is None


def test_naive_times_are_read_as_utc():
    naive = run("PASSED", T0.replace(tzinfo=None), at(60).replace(tzinfo=None))
    assert build_run_elapsed(naive, [], at(90))["elapsed_seconds"] == 60.0


def test_overlapping_parallel_holds_are_merged():
    steps = [
        step(held_at=at(10), released_at=at(40)),
        step(held_at=at(30), released_at=at(50)),
        step(held_at=at(70), released_at=at(80)),
    ]
    result = build_run_elapsed(run("PASSED", T0, at(100)), steps, at(100))
    assert result["held_seconds"] == 50.0
    assert result["active_seconds"] == 50.0


def test_open_hold_runs_to_now():
    steps = [step("HELD", held_at=at(10))]
    assert build_run_elapsed(run(), steps, at(25))["held_seconds"] == 15.0


def test_hold_that_ended_without_release_stops_at_run_end():
    steps = [step("CANCELLED", held_at=at(10))]
    assert build_run_elapsed(run("CANCELLED", T0, at(30)), steps, at(999))["held_seconds"] == 20.0


# --- progress rule --------------------------------------------------------

def test_queued():
    result = timing_of(step("PENDING"))
    assert result["progress"] == {"state": "queued", "percent": 0, "label": "queued"}
    assert result["runtime_seconds"] is None


def test_running_with_estimate():
    result = timing_of(step("RUNNING", started=0), estimates={"job_a": 200.0}, now_seconds=100)
    assert result["progress"] == {"state": "running", "percent": 50, "label": "50%"}
    assert result["runtime_seconds"] == 100.0
    assert result["estimate_seconds"] == 200.0


def test_running_percent_stops_at_95():
    result = timing_of(step("RUNNING", started=0), estimates={"job_a": 100.0}, now_seconds=99)
    assert result["progress"]["percent"] == 95
    assert result["progress"]["state"] == "running"


def test_past_estimate_is_over_estimate():
    result = timing_of(step("RUNNING", started=0), estimates={"job_a": 100.0}, now_seconds=100)
    assert result["progress"] == {"state": "over_estimate", "percent": 95, "label": "over estimate"}


def test_running_without_estimate_has_no_percent():
    result = timing_of(step("RUNNING", started=0))
    assert result["progress"] == {"state": "running_unknown", "percent": None, "label": "running"}
    assert result["estimate_seconds"] is None


def test_estimate_includes_wait_seconds():
    result = timing_of(step("RUNNING", started=0, wait_seconds=30), estimates={"job_a": 70.0})
    assert result["estimate_seconds"] == 100.0


def test_held():
    result = timing_of(step("HELD", started=0, finished=20, held_at=at(20)), now_seconds=50)
    assert result["progress"] == {"state": "held", "percent": 100, "label": "awaiting release"}
    assert result["runtime_seconds"] == 20.0
    assert result["held_seconds"] == 30.0


@pytest.mark.parametrize("status", ["PASSED", "FAILED", "SLOW", "ERROR", "APPROVED"])
def test_finished_with_times(status):
    result = timing_of(step(status, started=0, finished=42))
    assert result["progress"] == {"state": "done", "percent": 100, "label": status}
    assert result["runtime_seconds"] == 42.0


def test_finished_without_times_is_not_recorded():
    finished = run("PASSED", T0, at(60))
    result = timing_of(step("PASSED"), finished)
    assert result["progress"] == {"state": "not_recorded", "percent": 100, "label": "PASSED"}


@pytest.mark.parametrize("status", ["BLOCKED", "CANCELLED", "SKIPPED"])
def test_never_started_is_not_run(status):
    result = timing_of(step(status))
    assert result["progress"] == {"state": "not_run", "percent": 0, "label": status}


def test_pending_in_finished_run_is_not_run():
    result = timing_of(step("PENDING"), run("CANCELLED", T0, at(60)))
    assert result["progress"] == {"state": "not_run", "percent": 0, "label": "not run"}


def test_carried_over():
    result = timing_of(step("PASSED", carried_over=True))
    assert result["progress"] == {"state": "carried_over", "percent": 100, "label": "carried over"}


def test_running_in_finished_run_is_interrupted_and_ends_with_the_run():
    result = timing_of(step("RUNNING", started=10), run("ERROR", T0, at(70)), now_seconds=999)
    assert result["progress"] == {"state": "interrupted", "percent": 100, "label": "interrupted"}
    assert result["runtime_seconds"] == 60.0


def test_held_in_finished_run_is_interrupted():
    result = timing_of(step("HELD", started=0, finished=5), run("ERROR", T0, at(70)))
    assert result["progress"]["state"] == "interrupted"


# --- document -------------------------------------------------------------

def test_document_counts_and_times_are_iso_utc():
    steps = [
        step("PASSED", started=0, finished=10, step_id="a"),
        step("RUNNING", started=10, step_id="b", step_index=1),
        step("PENDING", step_id="c", step_index=2),
    ]
    doc = build_run_timing(run(), steps, {}, {}, at(50))
    assert doc["jobs_total"] == 3
    assert doc["jobs_done"] == 1
    assert doc["timing_recorded"] is True
    assert doc["started_at"] == "2026-11-04T09:00:00+00:00"
    assert doc["server_time"] == "2026-11-04T09:00:50+00:00"
    assert doc["steps"][0]["finished_at"] == "2026-11-04T09:00:10+00:00"


def test_timing_recorded_false_for_old_run():
    doc = build_run_timing(run("PASSED", T0, at(60)), [step("PASSED")], {}, {}, at(60))
    assert doc["timing_recorded"] is False
    assert doc["elapsed_seconds"] == 60.0


def test_hold_end_is_reported_for_the_chart():
    live = timing_of(step("HELD", started=0, finished=5, held_at=at(5)), now_seconds=20)
    assert live["held_at"] == "2026-11-04T09:00:05+00:00"
    assert live["hold_ended_at"] == "2026-11-04T09:00:20+00:00"


@pytest.mark.parametrize("job_type,family", [
    ("reconciliation", "reconciliation"),
    ("file_watcher", "file"),
    ("ds_job", "sap"),
    ("airflow_dag_run", "orchestration"),
    ("freshness", "quality"),
    ("something_new", "other"),
    (None, "other"),
])
def test_job_family(job_type, family):
    assert job_family(job_type) == family


def test_step_reports_job_type_and_family():
    result = timing_of(step("PENDING"), job_types={"job_a": "bo_report"})
    assert result["job_type"] == "bo_report"
    assert result["job_family"] == "sap"


# --- database reads -------------------------------------------------------

def _db():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _results(db, job, rows):
    from etl_framework.repository.models import TestResult, TestRun

    run_id = f"run-{job}-{len(rows)}"
    db.add(TestRun(run_id=run_id, status="PASSED"))
    for status, duration in rows:
        db.add(TestResult(run_id=run_id, query_name=job, status=status, duration_seconds=duration))
    db.commit()


def test_estimate_is_median_of_counted_results():
    db = _db()
    _results(db, "job", [("PASSED", 10.0), ("FAILED", 20.0), ("SLOW", 90.0), ("ERROR", 1.0), ("PASSED", 0.0)])
    assert estimate_durations(db, ["job"]) == {"job": 20.0}


def test_estimate_needs_three_results():
    db = _db()
    _results(db, "job", [("PASSED", 10.0), ("PASSED", 20.0)])
    assert estimate_durations(db, ["job"]) == {}


def test_estimate_uses_only_the_last_ten():
    db = _db()
    _results(db, "job", [("PASSED", 1000.0)] * 5 + [("PASSED", 10.0)] * 10)
    assert estimate_durations(db, ["job"]) == {"job": 10.0}


def test_load_timing_context_reads_job_types_and_skips_unknown_jobs():
    from etl_framework.repository.models import SavedJob

    db = _db()
    db.add(SavedJob(name="known", job_type="bo_report"))
    db.commit()
    context = load_timing_context(db, ["known", "deleted"])
    assert context.job_types == {"known": "bo_report"}
    assert context.estimates == {}
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.services.run_timing'`.

- [x] **Step 3: Write the service**

Create `api/services/run_timing.py`:

```python
"""Timing of a run and its steps, and the progress rule for each step.

The UI and the CLI only display what this module computes, so the rule lives
in one place. build_run_timing and build_run_elapsed are pure: the clock is an
argument and they never touch the database.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from etl_framework.repository.models import SavedJob, TERMINAL_STATUSES, TestResult

_ESTIMATE_SAMPLE = 10
_ESTIMATE_MIN_RESULTS = 3
# ERROR is left out: crashes end early and would pull the estimate down.
_ESTIMATE_STATUSES = ("PASSED", "FAILED", "SLOW")
_MAX_RUNNING_PERCENT = 95
_NOT_RUN_STATUSES = frozenset({"BLOCKED", "CANCELLED", "SKIPPED"})
_UNFINISHED_STATES = frozenset({
    "queued", "running", "over_estimate", "running_unknown", "held",
})

_JOB_FAMILIES = {
    "reconciliation": "reconciliation",
    "api_reconciliation": "reconciliation",
    "compare": "reconciliation",
    "cross_job_assertion": "reconciliation",
    "file_transfer": "file",
    "file_watcher": "file",
    "s3_row_count": "file",
    "s3_format_validation": "file",
    "s3_partition_check": "file",
    "bo_report": "sap",
    "bo_job": "sap",
    "ds_job": "sap",
    "automic_job": "orchestration",
    "airflow_dag_run": "orchestration",
    "aws_glue_job_run": "orchestration",
    "aws_athena_query": "orchestration",
    "freshness": "quality",
    "schema_snapshot": "quality",
    "profile": "quality",
    "dbt_artifact": "quality",
    "aws_glue_catalog_compare": "quality",
}


@dataclass(frozen=True)
class TimingContext:
    """What a run's timing needs that doesn't change while it runs."""
    job_types: dict[str, str] = field(default_factory=dict)
    estimates: dict[str, float] = field(default_factory=dict)


def job_family(job_type: str | None) -> str:
    return _JOB_FAMILIES.get(job_type or "", "other")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    value = _utc(value)
    return value.isoformat() if value is not None else None


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max((end - start).total_seconds(), 0.0), 1)


def _hold_interval(step: Any, run: Any, now: datetime) -> tuple[datetime, datetime] | None:
    start = _utc(step.held_at)
    if start is None:
        return None
    end = _utc(step.released_at) or _utc(run.completed_at) or now
    return start, max(start, end)


def _merged_seconds(intervals: Iterable[tuple[datetime, datetime]]) -> float:
    total = 0.0
    current_start = current_end = None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            if current_end is not None:
                total += (current_end - current_start).total_seconds()
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += (current_end - current_start).total_seconds()
    return round(total, 1)


def build_run_elapsed(run: Any, steps: Iterable[Any], now: datetime) -> dict[str, float | None]:
    """A run's end-to-end time: elapsed, held on holds, and active."""
    now = _utc(now)
    started = _utc(run.started_at)
    end = _utc(run.completed_at)
    if end is None and run.status not in TERMINAL_STATUSES:
        end = now
    elapsed = _seconds(started, end)
    held = _merged_seconds(
        interval for interval in (_hold_interval(s, run, now) for s in steps) if interval
    )
    active = round(max(elapsed - held, 0.0), 1) if elapsed is not None else None
    return {"elapsed_seconds": elapsed, "held_seconds": held, "active_seconds": active}


def _step_runtime(step: Any, run: Any, now: datetime) -> float | None:
    started = _utc(step.started_at)
    end = _utc(step.finished_at)
    if end is None:
        end = _utc(run.completed_at) if run.status in TERMINAL_STATUSES else now
    return _seconds(started, end)


def _state(step: Any, run_finished: bool, runtime: float | None, estimate: float | None) -> str:
    if step.carried_over:
        return "carried_over"
    if step.status in ("RUNNING", "HELD"):
        if run_finished:
            return "interrupted"
        if step.status == "HELD":
            return "held"
        if estimate is None:
            return "running_unknown"
        return "over_estimate" if (runtime or 0.0) >= estimate else "running"
    if step.status == "PENDING":
        return "not_run" if run_finished else "queued"
    if step.started_at is not None:
        return "done"
    return "not_run" if step.status in _NOT_RUN_STATUSES else "not_recorded"


def _progress(state: str, status: str, runtime: float | None, estimate: float | None) -> dict:
    if state == "running":
        percent = min(_MAX_RUNNING_PERCENT, int((runtime or 0.0) / estimate * 100))
        return {"state": state, "percent": percent, "label": f"{percent}%"}
    if state == "not_run":
        label = "not run" if status == "PENDING" else status
        return {"state": state, "percent": 0, "label": label}
    fixed = {
        "queued": (0, "queued"),
        "over_estimate": (_MAX_RUNNING_PERCENT, "over estimate"),
        "running_unknown": (None, "running"),
        "held": (100, "awaiting release"),
        "carried_over": (100, "carried over"),
        "interrupted": (100, "interrupted"),
    }
    percent, label = fixed.get(state, (100, status))
    return {"state": state, "percent": percent, "label": label}


def _step_timing(
    step: Any, run: Any, job_types: dict, estimates: dict, now: datetime, run_finished: bool,
) -> dict:
    runtime = _step_runtime(step, run, now)
    history = estimates.get(step.job_name)
    estimate = round(history + (step.wait_seconds or 0), 1) if history is not None else None
    hold = _hold_interval(step, run, now)
    job_type = job_types.get(step.job_name)
    return {
        "step_id": step.step_id,
        "step_index": step.step_index,
        "job_name": step.job_name,
        "job_type": job_type,
        "job_family": job_family(job_type),
        "status": step.status,
        "attempt": step.attempt or 0,
        "started_at": _iso(step.started_at),
        "finished_at": _iso(step.finished_at),
        "held_at": _iso(step.held_at),
        "hold_ended_at": _iso(hold[1]) if hold else None,
        "runtime_seconds": runtime,
        "estimate_seconds": estimate,
        "held_seconds": _seconds(*hold) if hold else 0.0,
        "progress": _progress(
            _state(step, run_finished, runtime, estimate), step.status, runtime, estimate,
        ),
    }


def build_run_timing(
    run: Any, steps: list, job_types: dict[str, str], estimates: dict[str, float], now: datetime,
) -> dict[str, Any]:
    """The full timing document for one run, live or finished."""
    now = _utc(now)
    run_finished = run.status in TERMINAL_STATUSES
    step_docs = [_step_timing(s, run, job_types, estimates, now, run_finished) for s in steps]
    return {
        "run_id": run.run_id,
        "status": run.status,
        "server_time": _iso(now),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        **build_run_elapsed(run, steps, now),
        "jobs_total": len(step_docs),
        "jobs_done": sum(1 for s in step_docs if s["progress"]["state"] not in _UNFINISHED_STATES),
        "timing_recorded": any(s.started_at is not None for s in steps),
        "steps": step_docs,
    }


def estimate_durations(db: Session, job_names: Iterable[str]) -> dict[str, float]:
    """Median duration of each job's recent results, where there are enough of them."""
    estimates: dict[str, float] = {}
    for name in set(job_names):
        rows = (
            db.query(TestResult.duration_seconds)
            .filter(
                TestResult.query_name == name,
                TestResult.status.in_(_ESTIMATE_STATUSES),
                TestResult.duration_seconds > 0,
            )
            .order_by(TestResult.id.desc())
            .limit(_ESTIMATE_SAMPLE)
            .all()
        )
        if len(rows) >= _ESTIMATE_MIN_RESULTS:
            estimates[name] = round(statistics.median(row[0] for row in rows), 1)
    return estimates


def load_timing_context(db: Session, job_names: Iterable[str]) -> TimingContext:
    names = set(job_names)
    if not names:
        return TimingContext()
    rows = db.query(SavedJob.name, SavedJob.job_type).filter(SavedJob.name.in_(names)).all()
    return TimingContext(
        job_types={name: job_type for name, job_type in rows},
        estimates=estimate_durations(db, names),
    )


def timing_for_run(
    db: Session, run: Any, steps: list, context: TimingContext | None = None,
) -> dict[str, Any]:
    if context is None:
        context = load_timing_context(db, [s.job_name for s in steps])
    return build_run_timing(
        run, steps, context.job_types, context.estimates, datetime.now(timezone.utc),
    )
```

- [x] **Step 4: Run tests to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 5: Commit**

```bash
git add api/services/run_timing.py tests/unit/test_run_timing.py
git commit -m "feat(runs): add run timing and the job progress rule"
```

---

## Task 4: Timing endpoints and stream

**Files:**
- Modify: `api/schemas.py`
- Modify: `api/routes/runs.py:791-807`, `api/routes/runs.py:1464-1518`
- Modify: `api/routes/sequences.py`
- Test: `tests/unit/test_run_timing_api.py`

- [x] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_timing_api.py`:

```python
def _live_run(engine):
    from etl_framework.repository.repository import RunRepository, RunStepRepository

    with Session(engine) as db:
        RunRepository(db).create_run("live", "dev", "prod", {"sequence": {"id": 7, "name": "s"}})
        RunStepRepository(db).materialize_steps("live", [
            SequenceStepRef(step_id="a", job_name="job_a"),
            SequenceStepRef(step_id="b", job_name="job_b", depends_on=["a"]),
        ])
        RunStepRepository(db).set_status_by_step_id("live", "a", "RUNNING")
        RunRepository(db).update_run_status("live", "RUNNING", started_at=T0)


def test_timing_returns_the_document(client, engine):
    _live_run(engine)
    body = client.get("/api/runs/live/timing").json()
    assert body["run_id"] == "live"
    assert body["jobs_total"] == 2
    assert body["jobs_done"] == 0
    assert body["elapsed_seconds"] > 0
    assert body["steps"][0]["progress"]["state"] == "running_unknown"
    assert body["steps"][1]["progress"] == {"state": "queued", "percent": 0, "label": "queued"}
    assert body["steps"][0]["job_family"] == "other"


def test_timing_unknown_run_is_404(client):
    assert client.get("/api/runs/nope/timing").status_code == 404


def test_timing_of_run_without_steps_has_empty_steps(client, engine):
    from etl_framework.repository.repository import RunRepository

    with Session(engine) as db:
        RunRepository(db).create_run("suite", "dev", "prod", {})
    body = client.get("/api/runs/suite/timing").json()
    assert body["steps"] == []
    assert body["elapsed_seconds"] is None


def test_stream_includes_timing_and_keeps_existing_keys(client, engine):
    _blocked_run(engine, "with-timing")
    with client.stream("GET", "/api/runs/with-timing/stream") as response:
        body = response.read().decode()
    data_line = next(line for line in body.splitlines() if line.startswith("data: "))
    import json
    payload = json.loads(data_line[len("data: "):])
    assert payload["timing"]["elapsed_seconds"] == 30.0
    assert payload["steps"][0]["step_id"] == "a"
    assert "percent_complete" in payload


def test_stream_survives_a_timing_failure(client, engine, monkeypatch):
    from api.routes import runs as runs_module

    def boom(*args, **kwargs):
        raise RuntimeError("no timing")

    monkeypatch.setattr(runs_module, "load_timing_context", boom)
    _blocked_run(engine, "no-timing")
    with client.stream("GET", "/api/runs/no-timing/stream") as response:
        body = response.read().decode()
    assert '"timing": null' in body
    assert "event: done" in body


def _sequence(engine):
    from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
    from etl_framework.repository.models import ExecutionSequence

    with Session(engine) as db:
        sequence = ExecutionSequence(name="timing-seq")
        db.add(sequence)
        db.commit()
        return sequence.id


def test_sequence_runs_are_newest_first_with_elapsed(client, engine):
    from etl_framework.repository.repository import RunRepository

    sequence_id = _sequence(engine)
    with Session(engine) as db:
        repo = RunRepository(db)
        repo.create_run("old", "dev", "prod", {"sequence": {"id": sequence_id}})
        repo.update_run_status("old", "PASSED", started_at=T0, completed_at=T0 + timedelta(seconds=60))
        repo.create_run("new", "dev", "prod", {"sequence": {"id": sequence_id}})
        repo.update_run_status("new", "FAILED", started_at=T0, completed_at=T0 + timedelta(seconds=90))
        repo.create_run("elsewhere", "dev", "prod", {"sequence": {"id": sequence_id + 1}})

    rows = client.get(f"/api/sequences/{sequence_id}/runs").json()
    assert [r["run_id"] for r in rows] == ["new", "old"]
    assert rows[0]["elapsed_seconds"] == 90.0
    assert rows[0]["status"] == "FAILED"
    assert rows[1]["active_seconds"] == 60.0

    paged = client.get(f"/api/sequences/{sequence_id}/runs?limit=1&offset=1").json()
    assert [r["run_id"] for r in paged] == ["old"]


def test_sequence_runs_rejects_bad_limit_and_unknown_sequence(client, engine):
    sequence_id = _sequence(engine)
    assert client.get(f"/api/sequences/{sequence_id}/runs?limit=201").status_code == 422
    assert client.get("/api/sequences/999999/runs").status_code == 404
```

- [x] **Step 2: Check the sequence test helper against the model**

Run: `C:\Python314\python.exe -c "from etl_framework.repository.models import ExecutionSequence as E; print([c.name for c in E.__table__.columns if not c.nullable and c.default is None and c.server_default is None])"`

If this prints columns other than `id` and `name`, set them in `_sequence`. If the class lives elsewhere, the import fails: find it with `Grep` for `class ExecutionSequence\b` and fix the import.

- [x] **Step 3: Run tests to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_api.py -q -p no:cacheprovider`
Expected: new tests FAIL with 404s or `AttributeError` on `load_timing_context`. `test_blocked_run_ends_the_stream` still passes.

- [x] **Step 4: Add the schemas**

In `api/schemas.py`, after `RunProgressOut`:

```python
class StepProgressOut(BaseModel):
    state: Literal[
        "queued", "running", "over_estimate", "running_unknown", "held",
        "done", "not_recorded", "not_run", "carried_over", "interrupted",
    ]
    percent: int | None = None
    label: str


class StepTimingOut(BaseModel):
    step_id: str | None = None
    step_index: int
    job_name: str
    job_type: str | None = None
    job_family: str = "other"
    status: str
    attempt: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    held_at: str | None = None
    hold_ended_at: str | None = None
    runtime_seconds: float | None = None
    estimate_seconds: float | None = None
    held_seconds: float = 0.0
    progress: StepProgressOut


class RunTimingOut(BaseModel):
    run_id: str
    status: str
    server_time: str
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float | None = None
    held_seconds: float = 0.0
    active_seconds: float | None = None
    jobs_total: int = 0
    jobs_done: int = 0
    timing_recorded: bool = False
    steps: list[StepTimingOut] = Field(default_factory=list)


class SequenceRunTimingOut(BaseModel):
    run_id: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    elapsed_seconds: float | None = None
    held_seconds: float = 0.0
    active_seconds: float | None = None
```

- [x] **Step 5: Add `/timing` and extend `/stream`**

In `api/routes/runs.py`, add `RunTimingOut` to the existing `from api.schemas import (...)` block, and add:

```python
from api.services.run_timing import load_timing_context, timing_for_run
```

Add after `get_run_progress`:

```python
@router.get("/{run_id}/timing", response_model=RunTimingOut)
def get_run_timing(run_id: str, db: Session = Depends(get_session)):
    run = RunRepository(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return timing_for_run(db, run, RunStepRepository(db).list_steps(run_id))
```

In `stream_run`, change the start of `events()`:

```python
    async def events():
        last_payload = ""
        step_repo = RunStepRepository(db)
        timing_context = None  # job types and estimates don't change during a run
```

Add above the `payload = {` line:

```python
            timing = None
            try:
                if timing_context is None:
                    timing_context = load_timing_context(db, [s.job_name for s in steps])
                timing = timing_for_run(db, run, steps, timing_context)
            except Exception:
                # Progress display must never take the stream down.
                logger.exception("Could not build timing for run %s", run_id)
```

Add this key to `payload`, after `"steps"`:

```python
                "timing": timing,
```

Confirm `logger` exists at module level in `runs.py`. If it doesn't, add `logger = logging.getLogger(__name__)` with `import logging`.

- [x] **Step 6: Add the sequence history endpoint**

In `api/routes/sequences.py`, add `SequenceRunTimingOut` to the `from api.schemas import (...)` block, and add:

```python
from datetime import datetime, timezone

from api.services.run_timing import build_run_elapsed
```

Add after `get_sequence_usage`:

```python
@router.get("/{sequence_id}/runs", response_model=list[SequenceRunTimingOut])
def list_sequence_runs(
    sequence_id: int,
    limit: int = Query(default=20, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_session),
):
    _get_or_404(db, sequence_id)
    now = datetime.now(timezone.utc)
    return [
        SequenceRunTimingOut(
            run_id=run.run_id,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            **build_run_elapsed(run, run.steps, now),
        )
        for run in RunRepository(db).list_runs_for_sequence(sequence_id, limit, offset)
    ]
```

- [x] **Step 7: Run tests to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_api.py tests/unit/test_runs_dag_api.py tests/unit/test_runs_extensions.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 8: Prove real execution records overlapping times**

Append to `tests/unit/test_run_timing_repository.py`:

```python
def test_dag_executor_records_overlapping_times_for_parallel_steps():
    import threading

    from api.services.dag_executor import DagExecutor, StepOutcome

    db = _session()
    RunRepository(db).create_run("par", "dev", "prod", {})
    steps = [
        SequenceStepRef(step_id="left", job_name="a"),
        SequenceStepRef(step_id="right", job_name="b"),
    ]
    step_repo = RunStepRepository(db)
    step_repo.materialize_steps("par", steps)

    both_running = threading.Barrier(2, timeout=10)
    lock = threading.Lock()

    class Adapter:
        def set_status(self, step_id, status, **kwargs):
            with lock:
                step_repo.set_status_by_step_id("par", step_id, status, **kwargs)

        def get_release(self, step_id):
            return "approve"

    def run_step(step):
        both_running.wait()
        return StepOutcome(status="PASSED")

    DagExecutor(
        steps=steps, step_repo=Adapter(), run_step=run_step,
        is_cancel_requested=lambda: False, max_workers=2,
    ).run()

    left = step_repo.get_step_by_step_id("par", "left")
    right = step_repo.get_step_by_step_id("par", "right")
    assert left.started_at <= right.finished_at
    assert right.started_at <= left.finished_at
```

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_repository.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 9: Commit**

```bash
git add api/schemas.py api/routes/runs.py api/routes/sequences.py tests/unit/test_run_timing_api.py tests/unit/test_run_timing_repository.py
git commit -m "feat(api): expose run timing and each sequence's run history"
```

---

## Task 5: CLI timing tables and commands

**Files:**
- Modify: `etl_framework/cli/render.py`, `etl_framework/cli/app.py`
- Modify: `pyproject.toml`, `requirements.txt`
- Test: `tests/unit/test_cli_progress.py`

- [x] **Step 1: Write the failing tests**

Create `tests/unit/test_cli_progress.py`:

```python
"""atom CLI: timing tables, new commands and live progress."""
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from etl_framework.cli import render
from etl_framework.cli.client import AtomConnectionError, AtomNotFoundError

runner = CliRunner()
BASE_ARGS = ["--api-url", "http://atom.test", "--token", "t0k3n"]


def step(step_id, status, state, percent, label, runtime=None, estimate=None, attempt=1, job_type="bo_report"):
    return {
        "step_id": step_id, "step_index": 0, "job_name": step_id, "job_type": job_type,
        "job_family": "sap", "status": status, "attempt": attempt, "started_at": None,
        "finished_at": None, "held_at": None, "hold_ended_at": None,
        "runtime_seconds": runtime, "estimate_seconds": estimate, "held_seconds": 0.0,
        "progress": {"state": state, "percent": percent, "label": label},
    }


def timing(steps=None, status="PASSED"):
    return {
        "run_id": "r-1", "status": status, "server_time": "2026-11-04T09:10:00+00:00",
        "started_at": "2026-11-04T09:00:00+00:00", "completed_at": "2026-11-04T09:10:00+00:00",
        "elapsed_seconds": 600.0, "held_seconds": 30.0, "active_seconds": 570.0,
        "jobs_total": len(steps or []), "jobs_done": len(steps or []),
        "timing_recorded": True, "steps": steps or [],
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_json(self, path, **kwargs):
        self.calls.append(path)
        result = self.responses.get(path, AtomNotFoundError(f"no fake for {path}"))
        if isinstance(result, Exception):
            raise result
        if isinstance(result, list) and result and isinstance(result[0], dict) and "__seq__" in result[0]:
            return result.pop(0)["__seq__"] if len(result) > 1 else result[0]["__seq__"]
        return result

    def post_json(self, path, payload):
        self.calls.append(path)
        return self.responses[path]

    def get_bytes(self, path):
        return b""


@pytest.fixture
def fake_client(monkeypatch):
    def install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr("etl_framework.cli.app._make_client", lambda api_url, token: client)
        return client
    return install


@pytest.mark.parametrize("seconds,text", [
    (None, "-"), (0, "0s"), (42.4, "42s"), (60, "1m 00s"), (217, "3m 37s"), (3725, "1h 02m 05s"),
])
def test_fmt_seconds(seconds, text):
    assert render.fmt_seconds(seconds) == text


def test_timing_table_shows_job_type_status_and_runtime():
    table = render.timing_table(timing([
        step("recon", "FAILED", "done", 100, "FAILED", runtime=182.0, attempt=2, job_type="reconciliation"),
        step("publish", "BLOCKED", "not_run", 0, "BLOCKED"),
    ]))
    assert "recon" in table and "reconciliation" in table and "FAILED" in table
    assert "3m 02s" in table
    assert "2" in table
    assert "publish" in table and "BLOCKED" in table


def test_timing_summary_has_start_end_elapsed():
    summary = render.timing_summary(timing())
    assert "Started" in summary and "Ended" in summary
    assert "Elapsed: 10m 00s" in summary
    assert "Held: 30s" in summary


def test_timing_summary_of_live_run_has_no_end():
    doc = timing(status="RUNNING")
    doc["completed_at"] = None
    assert "Ended: -" in render.timing_summary(doc)


def test_timing_command(fake_client):
    from etl_framework.cli.app import app

    fake_client({"/api/runs/r-1/timing": timing([step("a", "PASSED", "done", 100, "PASSED", runtime=5.0)])})
    result = runner.invoke(app, BASE_ARGS + ["timing", "r-1"])
    assert result.exit_code == 0
    assert "Elapsed: 10m 00s" in result.output


def test_timing_command_json(fake_client):
    from etl_framework.cli.app import app

    fake_client({"/api/runs/r-1/timing": timing()})
    result = runner.invoke(app, BASE_ARGS + ["--output", "json", "timing", "r-1"])
    assert json.loads(result.output)["elapsed_seconds"] == 600.0


def test_timing_command_unknown_run_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({})
    assert runner.invoke(app, BASE_ARGS + ["timing", "nope"]).exit_code == 4


def test_sequence_runs_by_name(fake_client):
    from etl_framework.cli.app import app

    client = fake_client({
        "/api/sequences": [{"__seq__": [{"id": 7, "name": "nightly"}]}],
        "/api/sequences/7/runs?limit=5": [{"__seq__": [{
            "run_id": "r-9", "status": "PASSED", "started_at": "2026-11-04T09:00:00+00:00",
            "completed_at": "2026-11-04T09:01:00+00:00", "elapsed_seconds": 60.0,
            "held_seconds": 0.0, "active_seconds": 60.0,
        }]}],
    })
    result = runner.invoke(app, BASE_ARGS + ["sequence-runs", "nightly", "--limit", "5"])
    assert result.exit_code == 0
    assert "r-9" in result.output and "1m 00s" in result.output
    assert "/api/sequences/7/runs?limit=5" in client.calls
```

- [x] **Step 2: Run tests to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_cli_progress.py -q -p no:cacheprovider`
Expected: FAIL with `AttributeError: module 'etl_framework.cli.render' has no attribute 'fmt_seconds'`.

- [x] **Step 3: Add the rendering functions**

In `etl_framework/cli/render.py`, change the imports:

```python
from __future__ import annotations

from datetime import datetime

from tabulate import tabulate
```

Append:

```python
def fmt_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def fmt_time(value: str | None) -> str:
    if not value:
        return "-"
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def timing_table(timing: dict) -> str:
    rows = [
        [s.get("job_name"), s.get("job_type") or "-", s["progress"]["label"],
         fmt_seconds(s.get("runtime_seconds")), s.get("attempt") or "-"]
        for s in timing.get("steps", [])
    ]
    return tabulate(rows, headers=["job", "type", "status", "runtime", "tries"],
                    tablefmt="simple")


def timing_summary(timing: dict) -> str:
    return (
        f"Started: {fmt_time(timing.get('started_at'))}  "
        f"Ended: {fmt_time(timing.get('completed_at'))}  "
        f"Elapsed: {fmt_seconds(timing.get('elapsed_seconds'))}  "
        f"Held: {fmt_seconds(timing.get('held_seconds'))}"
    )


def sequence_runs_table(items: list[dict]) -> str:
    rows = [
        [r.get("run_id"), r.get("status"), fmt_time(r.get("started_at")),
         fmt_time(r.get("completed_at")), fmt_seconds(r.get("elapsed_seconds")),
         fmt_seconds(r.get("held_seconds"))]
        for r in items
    ]
    return tabulate(rows, headers=["run_id", "status", "started", "ended",
                                   "elapsed", "held"], tablefmt="simple")
```

- [x] **Step 4: Add the commands**

In `etl_framework/cli/app.py`, add after the `report` command, before the next `@app.command()`:

```python
@app.command()
def timing(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run id to show"),
) -> None:
    """Show start, end, elapsed time and each job's runtime for a run."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    try:
        document = client.get_json(f"/api/runs/{run_id}/timing")
    except AtomNotFoundError as exc:
        raise _fail(output, EXIT_NOT_FOUND, str(exc))
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
    if output == "json":
        print(json.dumps(document))
    else:
        print(render.timing_table(document))
        print(render.timing_summary(document))


@app.command("sequence-runs")
def sequence_runs(
    ctx: typer.Context,
    sequence: str = typer.Argument(..., help="Sequence id or exact name"),
    limit: int = typer.Option(20, "--limit", help="Maximum runs to show (1-200)"),
) -> None:
    """Show start, end and elapsed time for each run of a sequence."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    try:
        sequence_id = _resolve_target(client, "sequence", sequence)
        rows = client.get_json(f"/api/sequences/{sequence_id}/runs?limit={limit}")
    except AtomNotFoundError as exc:
        raise _fail(output, EXIT_NOT_FOUND, str(exc))
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
    print(json.dumps(rows) if output == "json" else render.sequence_runs_table(rows))
```

- [x] **Step 5: Add rich as an explicit dependency**

In `pyproject.toml`, after `"typer>=0.12",`:

```toml
    "rich>=13",
```

In `requirements.txt`, after `tabulate>=0.9`:

```text
rich>=13
```

- [x] **Step 6: Run tests to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_cli_progress.py tests/unit/test_cli_render.py tests/unit/test_cli_app.py -q -p no:cacheprovider`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add etl_framework/cli/render.py etl_framework/cli/app.py pyproject.toml requirements.txt tests/unit/test_cli_progress.py
git commit -m "feat(cli): add run timing and sequence history commands"
```

---

## Task 6: Live bars in `atom run`

**Files:**
- Create: `etl_framework/cli/progress.py`
- Modify: `etl_framework/cli/app.py:90-99`, `etl_framework/cli/app.py:194-205`
- Test: `tests/unit/test_cli_progress.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli_progress.py`:

```python
def test_plain_progress_prints_one_line_per_status_change(capsys):
    from etl_framework.cli.progress import PlainProgress

    docs = [
        timing([step("a", "RUNNING", "running_unknown", None, "running", runtime=1.0)], "RUNNING"),
        timing([step("a", "RUNNING", "running_unknown", None, "running", runtime=2.0)], "RUNNING"),
        timing([step("a", "PASSED", "done", 100, "PASSED", runtime=3.0)], "RUNNING"),
    ]
    client = FakeClient({"/api/runs/r-1/timing": [{"__seq__": d} for d in docs]})
    display = PlainProgress(client, "r-1")
    for _ in docs:
        display.update()
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert "a" in out[0] and "RUNNING" in out[0]
    assert "PASSED" in out[1] and "3s" in out[1]
    assert "\x1b" not in "".join(out)


@pytest.mark.parametrize("error", [AtomNotFoundError("old"), AtomConnectionError("down")])
def test_progress_ignores_timing_errors(capsys, error):
    from etl_framework.cli.progress import PlainProgress

    client = FakeClient({"/api/runs/r-1/timing": error})
    PlainProgress(client, "r-1").update()
    assert capsys.readouterr().out == ""


def test_not_found_stops_asking_for_timing():
    from etl_framework.cli.progress import PlainProgress

    client = FakeClient({})
    display = PlainProgress(client, "r-1")
    display.update()
    display.update()
    assert client.calls == ["/api/runs/r-1/timing"]


def test_live_bars_show_each_state():
    from io import StringIO

    from rich.console import Console

    from etl_framework.cli.progress import bars

    doc = timing([
        step("done_job", "PASSED", "done", 100, "PASSED", runtime=5.0),
        step("run_job", "RUNNING", "running", 62, "62%", runtime=217.0, estimate=350.0),
        step("slow_job", "RUNNING", "over_estimate", 95, "over estimate", runtime=400.0, estimate=240.0),
        step("new_job", "RUNNING", "running_unknown", None, "running", runtime=9.0),
        step("wait_job", "PENDING", "queued", 0, "queued"),
    ], "RUNNING")
    stream = StringIO()
    Console(file=stream, width=120, force_terminal=False).print(bars(doc))
    text = stream.getvalue()
    for expected in ("done_job", "PASSED", "62%", "3m 37s / ~5m 50s", "over estimate", "queued"):
        assert expected in text


def _launch_responses(status="PASSED", timing_doc=None):
    responses = {
        "/api/selections/3/launch": {"run_id": "r-1"},
        "/api/runs/r-1/status": {
            "run_id": "r-1", "status": status, "passed": 1, "failed": 0, "error": 0,
        },
    }
    if timing_doc is not None:
        responses["/api/runs/r-1/timing"] = timing_doc
    return responses


RUN_ARGS = ["run", "3", "--source-env", "dev", "--poll-interval", "0", "--timeout", "1"]


def test_run_prints_table_and_times_when_not_a_terminal(fake_client):
    from etl_framework.cli.app import app

    doc = timing([step("a", "PASSED", "done", 100, "PASSED", runtime=5.0)])
    fake_client(_launch_responses(timing_doc=doc))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0
    assert "Elapsed: 10m 00s" in result.output
    assert "run=r-1" in result.output
    assert "\x1b[" not in result.output


def test_run_against_older_server_still_gates(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses())
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0
    assert "Elapsed" not in result.output
    assert "run=r-1" in result.output


def test_run_json_is_one_document_with_times(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(timing_doc=timing()))
    result = runner.invoke(app, BASE_ARGS + ["--output", "json"] + RUN_ARGS)
    body = json.loads(result.output)
    assert body["elapsed_seconds"] == 600.0
    assert body["held_seconds"] == 30.0
    assert body["started_at"] == "2026-11-04T09:00:00+00:00"
    assert body["verdict"] == "PASSED"


def test_run_json_against_older_server_has_null_times(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses())
    result = runner.invoke(app, BASE_ARGS + ["--output", "json"] + RUN_ARGS)
    assert json.loads(result.output)["elapsed_seconds"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_cli_progress.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'etl_framework.cli.progress'`.

- [ ] **Step 3: Write the displays**

Create `etl_framework/cli/progress.py`:

```python
"""Live job progress for `atom run`.

HTTP-only, like the rest of the CLI. A failure here must never affect the gate
result, so every timing request is allowed to fail quietly.
"""
from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from etl_framework.cli import render
from etl_framework.cli.client import (
    AtomAPIError,
    AtomAuthError,
    AtomClient,
    AtomConnectionError,
    AtomNotFoundError,
)

_STATUS_STYLES = {
    "PASSED": "green", "APPROVED": "green", "FAILED": "red", "ERROR": "red",
    "SLOW": "yellow",
}
_STATE_STYLES = {
    "running": "blue", "running_unknown": "blue", "over_estimate": "yellow",
    "held": "magenta",
}
_GREY_STATES = frozenset({"queued", "not_run", "carried_over", "interrupted"})


def _style(step: dict) -> str:
    state = step["progress"]["state"]
    if state in _GREY_STATES:
        return "bright_black"
    return _STATE_STYLES.get(state) or _STATUS_STYLES.get(step["status"], "bright_black")


def _runtime_text(step: dict) -> str:
    runtime = render.fmt_seconds(step.get("runtime_seconds"))
    state = step["progress"]["state"]
    if state in ("running", "over_estimate") and step.get("estimate_seconds") is not None:
        return f"{runtime} / ~{render.fmt_seconds(step['estimate_seconds'])}"
    return "" if step.get("runtime_seconds") is None else runtime


def bars(timing: dict) -> Table:
    """One colored bar per job, with its label and runtime, under a run header."""
    table = Table.grid(padding=(0, 1))
    table.add_column(no_wrap=True)
    table.add_column(width=30)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.title = (
        f"Elapsed {render.fmt_seconds(timing.get('elapsed_seconds'))} - "
        f"{timing.get('jobs_done', 0)} of {timing.get('jobs_total', 0)} jobs done"
    )
    table.title_justify = "left"
    for step in timing.get("steps", []):
        progress = step["progress"]
        style = _style(step)
        bar = ProgressBar(
            total=100,
            completed=progress["percent"] or 0,
            pulse=progress["percent"] is None,
            complete_style=style,
            finished_style=style,
            pulse_style=style,
            width=30,
        )
        # Text, not plain strings: job names may contain [brackets] rich would parse.
        table.add_row(Text(step["job_name"]), bar, Text(progress["label"], style=style), _runtime_text(step))
    return table


class _Display:
    def __init__(self, client: AtomClient, run_id: str) -> None:
        self._client = client
        self._run_id = run_id
        self._enabled = True

    def _fetch(self) -> dict | None:
        if not self._enabled:
            return None
        try:
            return self._client.get_json(f"/api/runs/{self._run_id}/timing")
        except AtomNotFoundError:
            self._enabled = False  # older server: don't ask again
        except (AtomAPIError, AtomAuthError, AtomConnectionError):
            pass  # temporary: try again on the next tick
        return None

    def __enter__(self) -> "_Display":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None


class PlainProgress(_Display):
    """For CI and pipes: one line per job status change, no terminal codes."""

    def __init__(self, client: AtomClient, run_id: str) -> None:
        super().__init__(client, run_id)
        self._seen: dict[int, str] = {}

    def update(self) -> None:
        timing = self._fetch()
        if timing is None:
            return
        for step in timing.get("steps", []):
            key = step["step_index"] if step.get("step_id") is None else step["step_id"]
            if step["status"] == self._seen.get(key):
                continue
            first_sighting_queued = key not in self._seen and step["status"] == "PENDING"
            self._seen[key] = step["status"]
            if first_sighting_queued:
                continue
            runtime = render.fmt_seconds(step.get("runtime_seconds"))
            print(f"{render.fmt_time(timing.get('server_time'))} {step['job_name']} "
                  f"{step['status']} runtime={runtime}", flush=True)


class LiveProgress(_Display):
    """For an interactive terminal: colored bars redrawn in place."""

    def __init__(self, client: AtomClient, run_id: str, console: Console | None = None) -> None:
        super().__init__(client, run_id)
        self._console = console or Console()
        self._live: Live | None = None

    def update(self) -> None:
        timing = self._fetch()
        if timing is None:
            return
        if self._live is None:
            self._live = Live(bars(timing), console=self._console, transient=True)
            self._live.start()
        else:
            self._live.update(bars(timing))

    def __exit__(self, *exc_info: Any) -> None:
        if self._live is not None:
            self._live.stop()
```

- [ ] **Step 4: Use the displays in `atom run`**

In `etl_framework/cli/app.py`, add imports:

```python
from typing import Callable, Optional
```

```python
from etl_framework.cli.progress import LiveProgress, PlainProgress
```

Add after `TERMINAL_STATUSES`:

```python
# Bars need to move, so an interactive terminal polls at least this often.
LIVE_POLL_SECONDS = 2.0
```

Replace `_wait_for_run`:

```python
def _wait_for_run(client: AtomClient, run_id: str,
                  timeout: float, poll_interval: float,
                  on_tick: Optional[Callable[[], None]] = None) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        if on_tick is not None:
            on_tick()
        status = client.get_json(f"/api/runs/{run_id}/status")
        if status.get("status") in TERMINAL_STATUSES:
            return status
        if time.monotonic() >= deadline:
            raise WaitTimeoutError(run_id)
        time.sleep(poll_interval)
```

Add after it:

```python
def _wait_with_progress(client: AtomClient, run_id: str, timeout: float,
                        poll_interval: float, output: str) -> dict:
    if output == "json":
        return _wait_for_run(client, run_id, timeout, poll_interval)
    if sys.stdout.isatty():
        with LiveProgress(client, run_id) as display:
            return _wait_for_run(client, run_id, timeout,
                                 min(poll_interval, LIVE_POLL_SECONDS), display.update)
    with PlainProgress(client, run_id) as display:
        return _wait_for_run(client, run_id, timeout, poll_interval, display.update)


def _final_timing(client: AtomClient, run_id: str) -> Optional[dict]:
    """Timing for the final report, or None. Never affects the gate result."""
    try:
        return client.get_json(f"/api/runs/{run_id}/timing")
    except (AtomAPIError, AtomAuthError, AtomConnectionError, AtomNotFoundError):
        return None
```

In the `run` command, replace the block from `status = _wait_for_run(...)` through the `else: print(render.run_summary(status, code))` lines:

```python
        status = _wait_with_progress(client, run_id, timeout, poll_interval, output)
        _write_artifacts(client, run_id, junit_out, json_out, html_out)
        code = _gate_exit_code(status)
        final_timing = _final_timing(client, run_id)
        if output == "json":
            times = final_timing or {}
            print(json.dumps({
                "run_id": run_id, "verdict": status.get("status"),
                "exit_code": code, "passed": status.get("passed"),
                "failed": status.get("failed"), "error": status.get("error"),
                "started_at": times.get("started_at"),
                "completed_at": times.get("completed_at"),
                "elapsed_seconds": times.get("elapsed_seconds"),
                "held_seconds": times.get("held_seconds"),
                "active_seconds": times.get("active_seconds"),
            }))
        else:
            if final_timing is not None:
                print(render.timing_table(final_timing))
                print(render.timing_summary(final_timing))
            print(render.run_summary(status, code))
```

Leave the `raise typer.Exit(code)` line that follows in place.

- [ ] **Step 5: Run tests to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_cli_progress.py tests/unit/test_cli_app.py tests/unit/test_cli_render.py -q -p no:cacheprovider`
Expected: PASS. If an existing `test_cli_app.py` test asserts an exact `calls` list, add `/api/runs/.../timing` to the expected calls only where the assertion is about request order, not about behavior.

- [ ] **Step 6: Look at the bars in a real terminal**

Run in a real terminal against a local API:

```powershell
C:\Python314\python.exe -m etl_framework.cli.app --api-url http://localhost:8000 --token $env:ATOM_API_TOKEN run <sequence-name> --target-type sequence --source-env dev
```

Expected: colored bars redraw in place, then disappear and leave the table, `Started / Ended / Elapsed` line and summary line. If the module has no `__main__` entry, use the `atom` script defined in `pyproject.toml`. Then set `$env:NO_COLOR = '1'`, rerun, and confirm the bars have no color.

- [ ] **Step 7: Commit**

```bash
git add etl_framework/cli/progress.py etl_framework/cli/app.py tests/unit/test_cli_progress.py
git commit -m "feat(cli): show live job progress and timing in atom run"
```

---

## Task 7: Frontend timing logic

**Files:**
- Create: `frontend/features/run-timing.js`
- Modify: `frontend/app.js` (`FEATURE_SLICES`), `frontend/index.template.html:556`
- Test: `tests/unit/test_run_timing_frontend.js`, `tests/unit/test_run_timing_frontend.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_timing_frontend.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = Path(__file__).with_suffix(".js")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_run_timing_frontend_behaviour():
    proc = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_run_timing_slice_is_registered():
    assert "ETL_FEATURE_RUN_TIMING()" in (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    template = (ROOT / "frontend" / "index.template.html").read_text(encoding="utf-8")
    assert '<script src="features/run-timing.js"></script>' in template
```

Create `tests/unit/test_run_timing_frontend.js`:

```js
'use strict';
const assert = require('node:assert');
const path = require('node:path');

global.window = global;
const storage = new Map();
global.localStorage = {
  getItem(key) { return storage.has(key) ? storage.get(key) : null; },
  setItem(key, value) { storage.set(key, String(value)); },
};
require(path.join(__dirname, '..', '..', 'frontend', 'features', 'run-timing.js'));
const { fmtSeconds, ganttLayout } = window.ETL_RUN_TIMING;

const T = (seconds) => new Date(Date.UTC(2026, 10, 4, 9, 0, seconds)).toISOString();

function step(overrides) {
  return Object.assign({
    step_index: 0, step_id: 'a', job_name: 'a', job_type: 'bo_report', job_family: 'sap',
    status: 'PASSED', attempt: 1, started_at: null, finished_at: null, held_at: null,
    hold_ended_at: null, runtime_seconds: null, estimate_seconds: null,
    progress: { state: 'done', percent: 100, label: 'PASSED' },
  }, overrides);
}

// --- formatting -----------------------------------------------------------
assert.strictEqual(fmtSeconds(null), '—');
assert.strictEqual(fmtSeconds(0), '0s');
assert.strictEqual(fmtSeconds(42.4), '42s');
assert.strictEqual(fmtSeconds(217), '3m 37s');
assert.strictEqual(fmtSeconds(3725), '1h 02m 05s');

// --- Gantt layout ---------------------------------------------------------
const finished = ganttLayout({
  started_at: T(0), completed_at: T(40), server_time: T(50),
  steps: [
    step({ step_id: 'a', started_at: T(0), finished_at: T(10) }),
    step({ step_id: 'b', started_at: T(10), finished_at: T(20), held_at: T(20), hold_ended_at: T(30) }),
    step({ step_id: 'c', status: 'BLOCKED' }),
  ],
});
assert.deepStrictEqual(finished.rows[0].bar, { left: 0, width: 25 });
assert.deepStrictEqual(finished.rows[1].bar, { left: 25, width: 25 });
assert.deepStrictEqual(finished.rows[1].hold, { left: 50, width: 25 });
assert.strictEqual(finished.rows[2].bar, null);
assert.strictEqual(finished.ticks.length, 4);
assert.strictEqual(finished.ticks[0].percent, 0);
assert.strictEqual(finished.ticks[3].percent, 100);

// A live run's axis ends at server time, and a running step's bar reaches it.
const live = ganttLayout({
  started_at: T(0), completed_at: null, server_time: T(20),
  steps: [step({ status: 'RUNNING', started_at: T(10) })],
});
assert.deepStrictEqual(live.rows[0].bar, { left: 50, width: 50 });

// Parallel steps overlap on the axis.
const parallel = ganttLayout({
  started_at: T(0), completed_at: T(10), server_time: T(10),
  steps: [
    step({ step_id: 'l', started_at: T(0), finished_at: T(10) }),
    step({ step_id: 'r', started_at: T(0), finished_at: T(5) }),
  ],
});
assert.strictEqual(parallel.rows[0].bar.left, parallel.rows[1].bar.left);

// A run that hasn't started has no axis, and a zero-length run doesn't divide by zero.
assert.deepStrictEqual(ganttLayout({ started_at: null, steps: [step({})] }).ticks, []);
const instant = ganttLayout({
  started_at: T(0), completed_at: T(0), server_time: T(0),
  steps: [step({ started_at: T(0), finished_at: T(0) })],
});
assert.ok(Number.isFinite(instant.rows[0].bar.width));

// --- component ------------------------------------------------------------
function component() {
  return Object.assign(window.ETL_FEATURE_RUN_TIMING(), {
    isTerminalStatus: (status) => ['PASSED', 'FAILED', 'BLOCKED'].includes(status),
  });
}

async function main() {
  storage.clear();
  const c = component();

  // Tabs: live runs open on Progress, finished runs on Timeline, a click wins.
  assert.strictEqual(c.runTimingTab({ run_id: 'x', status: 'RUNNING' }), 'progress');
  assert.strictEqual(c.runTimingTab({ run_id: 'x', status: 'PASSED' }), 'timeline');
  c.setRunTimingTab('x', 'progress');
  assert.strictEqual(c.runTimingTab({ run_id: 'x', status: 'PASSED' }), 'progress');

  // Bars.
  const bar = (state, status, percent) => step({ status, progress: { state, percent, label: '' } });
  assert.strictEqual(c.progressBarClass(bar('running', 'RUNNING', 50)), 'rt-s-running rt-stripe');
  assert.strictEqual(c.progressBarClass(bar('over_estimate', 'RUNNING', 95)), 'rt-s-slow rt-stripe');
  assert.strictEqual(c.progressBarClass(bar('running_unknown', 'RUNNING', null)), 'rt-s-running rt-stripe');
  assert.strictEqual(c.progressBarClass(bar('held', 'HELD', 100)), 'rt-s-held');
  assert.strictEqual(c.progressBarClass(bar('done', 'FAILED', 100)), 'rt-s-failed');
  assert.strictEqual(c.progressBarClass(bar('done', 'ERROR', 100)), 'rt-s-failed');
  assert.strictEqual(c.progressBarClass(bar('done', 'SLOW', 100)), 'rt-s-slow');
  assert.strictEqual(c.progressBarClass(bar('done', 'APPROVED', 100)), 'rt-s-passed');
  assert.strictEqual(c.progressBarClass(bar('done', 'CANCELLED', 100)), 'rt-s-idle');
  assert.strictEqual(c.progressBarWidth(bar('running', 'RUNNING', 62)), '62%');
  assert.strictEqual(c.progressBarWidth(bar('running_unknown', 'RUNNING', null)), '100%');

  // Runtime text.
  assert.strictEqual(c.runtimeText(step({
    runtime_seconds: 217, estimate_seconds: 350, status: 'RUNNING',
    progress: { state: 'running', percent: 62, label: '62%' },
  })), '3m 37s / ~5m 50s');
  assert.strictEqual(c.runtimeText(step({ runtime_seconds: 60, attempt: 2 })), '1m 00s · try 2');
  assert.strictEqual(c.runtimeText(step({
    status: 'PENDING', progress: { state: 'queued', percent: 0, label: 'queued' },
  })), 'queued');
  assert.strictEqual(c.runtimeText(step({
    status: 'BLOCKED', progress: { state: 'not_run', percent: 0, label: 'BLOCKED' },
  })), 'not run');
  assert.strictEqual(c.runtimeText(step({
    progress: { state: 'carried_over', percent: 100, label: 'carried over' },
  })), 'carried over');

  // Color toggle: status by default, remembered, and bad values ignored.
  assert.strictEqual(c.ganttColorBy, 'status');
  const failed = step({ status: 'FAILED', job_family: 'sap' });
  assert.strictEqual(c.ganttBarClass(failed), 'rt-s-failed');
  c.setGanttColorBy('family');
  assert.strictEqual(c.ganttBarClass(failed), 'rt-f-sap');
  assert.strictEqual(component().ganttColorBy, 'family');
  storage.set('atom_gantt_color_by', 'nonsense');
  assert.strictEqual(component().ganttColorBy, 'status');

  // Storage that throws must not break the toggle.
  const realStorage = global.localStorage;
  global.localStorage = {
    getItem() { throw new Error('blocked'); },
    setItem() { throw new Error('blocked'); },
  };
  const blocked = component();
  assert.strictEqual(blocked.ganttColorBy, 'status');
  blocked.setGanttColorBy('family');
  assert.strictEqual(blocked.ganttColorBy, 'family');
  global.localStorage = realStorage;

  // Legends match the toggle.
  storage.clear();
  const legend = component();
  assert.ok(legend.ganttLegend().some((item) => item.label === 'Failed'));
  legend.setGanttColorBy('family');
  assert.ok(legend.ganttLegend().some((item) => item.label === 'SAP'));

  // Tooltip carries type, status, runtime and tries.
  const tip = c.ganttTooltip(step({
    job_name: 'recon', job_type: 'reconciliation', status: 'FAILED', attempt: 2,
    started_at: T(0), finished_at: T(30), runtime_seconds: 30,
  }));
  for (const part of ['recon', 'reconciliation', 'FAILED', '30s', 'try 2']) {
    assert.ok(tip.includes(part), `${part} missing from ${tip}`);
  }

  // Load: a failed request leaves nothing cached, and a new timing replaces the old.
  global.api = async () => ({ run_id: 'r', steps: [] });
  await c.loadRunTiming('r');
  assert.strictEqual(c.runTimings.r.run_id, 'r');
  global.api = async () => { throw new Error('nope'); };
  await c.loadRunTiming('other');
  assert.strictEqual(c.runTimings.other, undefined);
  c.setRunTiming('r', { run_id: 'r', status: 'PASSED' });
  assert.strictEqual(c.runTimings.r.status, 'PASSED');
  c.setRunTiming('r', null);
  assert.strictEqual(c.runTimings.r.status, 'PASSED');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_frontend.py -q -p no:cacheprovider`
Expected: FAIL with `Cannot find module` for `run-timing.js`.

- [ ] **Step 3: Write the slice**

Create `frontend/features/run-timing.js`:

```js
(function (global) {
  'use strict';
  // Run timing feature slice: formatting and Gantt layout for the timing
  // document from /api/runs/{id}/timing and /stream. The server decides state
  // and percent; this file only turns them into positions, text and classes.
  // Merged into the Alpine component via the FEATURE_SLICES reduce in app.js.
  const COLOR_BY_KEY = 'atom_gantt_color_by';
  const COLOR_BY_VALUES = ['status', 'family'];
  const TICK_COUNT = 4;
  const STATUS_CLASSES = {
    PASSED: 'passed', APPROVED: 'passed', FAILED: 'failed', ERROR: 'failed',
    SLOW: 'slow', RUNNING: 'running', HELD: 'held',
  };
  const STATUS_LEGEND = [
    ['passed', 'Passed'], ['failed', 'Failed'], ['slow', 'Slow or over estimate'],
    ['running', 'Running'], ['held', 'Held'], ['idle', 'Other'],
  ];
  const FAMILY_LEGEND = [
    ['reconciliation', 'Reconciliation'], ['file', 'File'], ['sap', 'SAP'],
    ['orchestration', 'Orchestration'], ['quality', 'Quality'], ['other', 'Other'],
  ];

  function fmtSeconds(value) {
    if (value === null || value === undefined) return '—';
    const total = Math.round(value);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const seconds = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    if (hours) return `${hours}h ${pad(minutes)}m ${pad(seconds)}s`;
    if (minutes) return `${minutes}m ${pad(seconds)}s`;
    return `${seconds}s`;
  }

  function clockTime(ms) {
    return new Date(ms).toLocaleTimeString(undefined, {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
  }

  function ganttLayout(timing) {
    const start = Date.parse(timing.started_at);
    const rowsWithoutBars = () => (timing.steps || []).map((step) => ({ step, bar: null, hold: null }));
    if (!timing.started_at || Number.isNaN(start)) return { ticks: [], rows: rowsWithoutBars() };

    const end = Date.parse(timing.completed_at || timing.server_time);
    const span = Math.max(end - start, 1);
    const percentOf = (ms) => Math.min(Math.max(((ms - start) / span) * 100, 0), 100);
    const segment = (from, to) => {
      const left = percentOf(Date.parse(from));
      return { left, width: Math.max(percentOf(Date.parse(to)) - left, 0) };
    };

    const ticks = [];
    for (let i = 0; i < TICK_COUNT; i++) {
      const fraction = i / (TICK_COUNT - 1);
      ticks.push({ percent: fraction * 100, label: clockTime(start + span * fraction) });
    }
    const rows = (timing.steps || []).map((step) => ({
      step,
      bar: step.started_at
        ? segment(step.started_at, step.finished_at || timing.completed_at || timing.server_time)
        : null,
      hold: step.held_at && step.hold_ended_at ? segment(step.held_at, step.hold_ended_at) : null,
    }));
    return { ticks, rows };
  }

  function statusClass(step) {
    return `rt-s-${STATUS_CLASSES[step.status] || 'idle'}`;
  }

  function readColorBy() {
    try {
      const stored = global.localStorage.getItem(COLOR_BY_KEY);
      return COLOR_BY_VALUES.includes(stored) ? stored : 'status';
    } catch {
      return 'status';
    }
  }

  global.ETL_RUN_TIMING = { fmtSeconds, ganttLayout };

  global.ETL_FEATURE_RUN_TIMING = function () {
    return {
      runTimings: {},       // { run_id: timing document }
      runTimingTabs: {},    // { run_id: 'progress' | 'timeline' }, only after a click
      ganttColorBy: readColorBy(),

      fmtSeconds,
      ganttLayout,

      setRunTiming(runId, timing) {
        if (timing) this.runTimings = { ...this.runTimings, [runId]: timing };
      },

      async loadRunTiming(runId) {
        try {
          this.setRunTiming(runId, await api('GET', `/api/runs/${runId}/timing`));
        } catch {
          // Older data or a passing failure: the card falls back to what it has.
        }
      },

      runTimingTab(run) {
        return this.runTimingTabs[run.run_id]
          || (this.isTerminalStatus(run.status) ? 'timeline' : 'progress');
      },

      setRunTimingTab(runId, tab) {
        this.runTimingTabs = { ...this.runTimingTabs, [runId]: tab };
      },

      progressBarClass(step) {
        const state = step.progress.state;
        if (state === 'running' || state === 'running_unknown') return 'rt-s-running rt-stripe';
        if (state === 'over_estimate') return 'rt-s-slow rt-stripe';
        if (state === 'held') return 'rt-s-held';
        if (state === 'carried_over' || state === 'interrupted') return 'rt-s-idle';
        return statusClass(step);
      },

      progressBarWidth(step) {
        const percent = step.progress.percent;
        return `${percent === null || percent === undefined ? 100 : percent}%`;
      },

      runtimeText(step) {
        const state = step.progress.state;
        if (state === 'queued') return 'queued';
        if (state === 'not_run') return 'not run';
        if (state === 'carried_over') return 'carried over';
        if (step.runtime_seconds === null || step.runtime_seconds === undefined) return '—';
        let text = fmtSeconds(step.runtime_seconds);
        if ((state === 'running' || state === 'over_estimate') && step.estimate_seconds) {
          text += ` / ~${fmtSeconds(step.estimate_seconds)}`;
        }
        if ((step.attempt || 0) > 1) text += ` · try ${step.attempt}`;
        return text;
      },

      setGanttColorBy(value) {
        if (!COLOR_BY_VALUES.includes(value)) return;
        this.ganttColorBy = value;
        try {
          global.localStorage.setItem(COLOR_BY_KEY, value);
        } catch {
          // Blocked storage only means the choice isn't remembered.
        }
      },

      ganttBarClass(step) {
        return this.ganttColorBy === 'family' ? `rt-f-${step.job_family}` : statusClass(step);
      },

      ganttLegend() {
        return this.ganttColorBy === 'family'
          ? FAMILY_LEGEND.map(([key, label]) => ({ cls: `rt-f-${key}`, label }))
          : STATUS_LEGEND.map(([key, label]) => ({ cls: `rt-s-${key}`, label }));
      },

      ganttTooltip(step) {
        const time = (iso) => (iso ? clockTime(Date.parse(iso)) : '—');
        const tries = (step.attempt || 0) > 1 ? ` · try ${step.attempt}` : '';
        return `${step.job_name} (${step.job_type || 'unknown type'}) · ${step.status} · `
          + `${time(step.started_at)} to ${time(step.finished_at)} · `
          + `${fmtSeconds(step.runtime_seconds)}${tries}`;
      },
    };
  };
})(window);
```

- [ ] **Step 4: Register the slice**

In `frontend/app.js`, add `ETL_FEATURE_RUN_TIMING()` to the `FEATURE_SLICES` array, directly after `ETL_FEATURE_MONITOR()`.

In `frontend/index.template.html`, after line 556 (`<script src="features/monitor.js"></script>`):

```html
    <script src="features/run-timing.js"></script>
```

- [ ] **Step 5: Build and run tests**

Run: `node scripts/build-html.js`
Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_frontend.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/run-timing.js frontend/app.js frontend/index.template.html frontend/index.html tests/unit/test_run_timing_frontend.py tests/unit/test_run_timing_frontend.js
git commit -m "feat(ui): add run timing and Gantt layout logic"
```

---

## Task 8: Monitor run card and Gantt markup

**Files:**
- Modify: `scripts/build-html.js:64-78`
- Create: `frontend/partials/shared/run-gantt.html`
- Modify: `frontend/partials/tab-monitor.html`, `frontend/features/monitor.js`, `frontend/styles.css`
- Modify: `tests/e2e/60-sequence-flow-modes.spec.ts:91-129`

- [ ] **Step 1: Add shared partial support to the build**

In `scripts/build-html.js`, after `const INCLUDE_SNIFF_RE = /^<!-- INCLUDE:/;`:

```js
// One level of reuse: a tab partial can pull in markup shared with another tab.
// These live in partials/shared/, outside the count check below, which only
// looks at top-level partials.
const SHARED_RE = /^[ \t]*<!-- SHARED: (shared\/[\w.-]+\.html) -->\r?$/gm;

function expandShared(text) {
  return text.replace(SHARED_RE, (_, file) =>
    fs.readFileSync(path.join(ROOT, 'frontend', 'partials', file), 'utf8').replace(/\r?\n$/, ''));
}
```

Change the partial read inside `build()`:

```js
    return expandShared(fs.readFileSync(partialPath, 'utf8')).replace(/\r?\n$/, '');
```

- [ ] **Step 2: Write the shared Gantt markup**

Create `frontend/partials/shared/run-gantt.html`. It expects a `timing` variable in Alpine scope:

```html
<div data-testid="run-gantt">
  <template x-if="!timing.timing_recorded">
    <div class="text-sm text-muted" data-testid="run-gantt-not-recorded">Step timing not recorded for this run.</div>
  </template>
  <template x-if="timing.timing_recorded">
    <div>
      <div class="rt-toggle" role="group" aria-label="Gantt color">
        <span class="text-xs text-muted">Color by:</span>
        <button type="button" class="rt-toggle-btn" :class="ganttColorBy === 'status' && 'is-on'"
                :aria-pressed="ganttColorBy === 'status'" @click="setGanttColorBy('status')"
                data-testid="gantt-color-status">Status</button>
        <button type="button" class="rt-toggle-btn" :class="ganttColorBy === 'family' && 'is-on'"
                :aria-pressed="ganttColorBy === 'family'" @click="setGanttColorBy('family')"
                data-testid="gantt-color-family">Job type</button>
      </div>
      <div class="rt-gantt">
        <div class="rt-gantt-axis">
          <span></span>
          <div class="rt-gantt-ticks">
            <template x-for="tick in ganttLayout(timing).ticks" :key="tick.percent">
              <span x-text="tick.label"></span>
            </template>
          </div>
          <span class="text-right">Runtime</span>
        </div>
        <template x-for="row in ganttLayout(timing).rows" :key="row.step.step_index">
          <div class="rt-gantt-row" :data-testid="'run-gantt-row-' + (row.step.step_id || row.step.step_index)">
            <div class="rt-job-name">
              <span class="rt-job-title" x-text="row.step.job_name"></span>
              <span class="rt-job-sub" x-text="(row.step.job_type || 'unknown') + ' · ' + row.step.status"></span>
            </div>
            <div class="rt-gantt-track">
              <template x-if="row.bar">
                <div class="rt-gantt-bar" :class="ganttBarClass(row.step)" tabindex="0"
                     :style="'left:' + row.bar.left + '%;width:' + row.bar.width + '%'"
                     :title="ganttTooltip(row.step)" :aria-label="ganttTooltip(row.step)"
                     data-testid="run-gantt-bar"></div>
              </template>
              <template x-if="row.hold">
                <div class="rt-gantt-hold" :style="'left:' + row.hold.left + '%;width:' + row.hold.width + '%'"
                     title="Held, waiting for release" data-testid="run-gantt-hold"></div>
              </template>
            </div>
            <div class="rt-runtime" x-text="runtimeText(row.step)"></div>
          </div>
        </template>
      </div>
      <div class="rt-legend" data-testid="run-gantt-legend">
        <template x-for="item in ganttLegend()" :key="item.cls">
          <span><i :class="item.cls"></i><span x-text="item.label"></span></span>
        </template>
      </div>
    </div>
  </template>
</div>
```

- [ ] **Step 3: Replace the run card contents**

In `frontend/partials/tab-monitor.html`, add `x-init` to the card element on line 25:

```html
      <div class="card" :data-testid="'monitor-run-' + run.run_id" x-init="loadRunTiming(run.run_id)">
```

Replace lines 54-57 (the `<!-- Progress bar -->` block) with:

```html
        <!-- Runs with steps get per-job progress; others keep the single bar. -->
        <template x-if="!runTimings[run.run_id] || !runTimings[run.run_id].steps.length">
          <div class="progress-track mb-4">
            <div class="progress-fill" :style="'width:' + runProgress(run) + '%'"></div>
          </div>
        </template>
        <template x-for="timing in [runTimings[run.run_id]].filter((t) => t && t.steps.length)" :key="run.run_id">
          <div class="mb-4" data-testid="run-timing">
            <div class="rt-summary">
              <div class="text-xs text-muted">
                <span>Started </span><span data-testid="run-started" x-text="fmtDate(timing.started_at)"></span>
                <span> · Ended </span><span data-testid="run-ended" x-text="fmtDate(timing.completed_at)"></span>
                <span x-show="timing.held_seconds > 0" x-text="' · Held ' + fmtSeconds(timing.held_seconds)"></span>
              </div>
              <div class="text-right">
                <div class="rt-elapsed" data-testid="run-elapsed" x-text="fmtSeconds(timing.elapsed_seconds)"></div>
                <div class="text-xs text-muted" x-text="timing.jobs_done + ' of ' + timing.jobs_total + ' jobs done'"></div>
              </div>
            </div>
            <div class="rt-tabs" role="tablist">
              <button type="button" role="tab" class="rt-tab" :class="runTimingTab(run) === 'progress' && 'is-on'"
                      :aria-selected="runTimingTab(run) === 'progress'" @click="setRunTimingTab(run.run_id, 'progress')"
                      data-testid="run-tab-progress">Progress</button>
              <button type="button" role="tab" class="rt-tab" :class="runTimingTab(run) === 'timeline' && 'is-on'"
                      :aria-selected="runTimingTab(run) === 'timeline'" @click="setRunTimingTab(run.run_id, 'timeline')"
                      data-testid="run-tab-timeline">Timeline</button>
            </div>
            <div x-show="runTimingTab(run) === 'progress'" data-testid="run-progress-rows">
              <template x-for="step in timing.steps" :key="step.step_index">
                <div class="rt-progress-row" :data-testid="'monitor-step-' + (step.step_id || step.step_index)">
                  <div class="rt-job-name">
                    <span class="rt-job-title" x-text="step.job_name"></span>
                    <span class="rt-job-sub" x-text="step.job_type || 'unknown'"></span>
                  </div>
                  <div class="rt-bar-track" role="progressbar" aria-valuemin="0" aria-valuemax="100"
                       :aria-valuenow="step.progress.percent === null ? null : step.progress.percent"
                       :aria-label="step.job_name + ': ' + step.progress.label">
                    <template x-if="step.progress.state !== 'queued' && step.progress.state !== 'not_run'">
                      <div class="rt-bar-fill" :class="progressBarClass(step)" :style="'width:' + progressBarWidth(step)"
                           x-text="step.progress.percent === null ? '' : step.progress.label"></div>
                    </template>
                  </div>
                  <div class="rt-runtime" x-text="runtimeText(step)"></div>
                  <template x-if="step.progress.state === 'held'">
                    <button @click="openStepRelease(run.run_id, step.step_index)" class="btn-primary btn-sm text-xs py-1 px-3">Release</button>
                  </template>
                </div>
              </template>
            </div>
            <div x-show="runTimingTab(run) === 'timeline'">
              <!-- SHARED: shared/run-gantt.html -->
            </div>
          </div>
        </template>
```

Delete lines 79-115 (the `<!-- Step timeline (shown when run has step data) -->` block). Its contents are now in the Progress and Timeline tabs. Keep the `<!-- HELD alert -->` block.

- [ ] **Step 4: Feed timing into the card**

In `frontend/features/monitor.js`, in the `progress` listener, after `const progress = JSON.parse(event.data);`:

```js
        this.setRunTiming(progress.run_id, progress.timing);
```

In the `done` listener, after its `const progress = JSON.parse(event.data);`:

```js
        this.setRunTiming(progress.run_id, progress.timing);
```

In `pollActiveRuns`, add to the `Promise.all` array, after `this.loadRunSteps(run.run_id),`:

```js
            this.loadRunTiming(run.run_id),
```

Delete `monitorStepLevels` and `monitorStepBadgeClass` with the comment above them (lines 169-199). The partial no longer uses them.

Keep `runProgress`: runs without steps still use it.

- [ ] **Step 5: Add the styles**

Append to `frontend/styles.css`:

```css
/* Run timing: per-job progress bars and Gantt */
.rt-summary { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; margin-bottom: 0.5rem; }
.rt-elapsed { font-size: 1.25rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.rt-tabs { display: flex; gap: 0.25rem; margin-bottom: 0.75rem; border-bottom: 1px solid rgba(148, 163, 184, 0.3); }
.rt-tab { padding: 0.25rem 0.75rem; font-size: 0.75rem; color: inherit; opacity: 0.7; border-bottom: 2px solid transparent; }
.rt-tab.is-on { opacity: 1; font-weight: 700; color: var(--accent); border-bottom-color: var(--accent); }
.rt-progress-row { display: grid; grid-template-columns: minmax(8rem, 12rem) 1fr minmax(6rem, auto) auto; align-items: center; gap: 0.75rem; padding: 0.25rem 0; }
.rt-job-name { min-width: 0; }
.rt-job-title { display: block; font-size: 0.8125rem; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rt-job-sub { display: block; font-size: 0.6875rem; opacity: 0.7; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rt-bar-track { height: 1rem; border-radius: 9999px; overflow: hidden; background: rgba(148, 163, 184, 0.2); }
.rt-bar-fill { height: 100%; min-width: 2.25rem; border-radius: 9999px; color: #fff; font-size: 0.625rem; line-height: 1rem; text-align: center; white-space: nowrap; overflow: hidden; transition: width 0.8s linear; }
.rt-runtime { font-size: 0.75rem; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.rt-stripe { background-image: repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.28) 0 6px, transparent 6px 12px); background-size: 17px 17px; animation: rt-stripe-move 1s linear infinite; }
@keyframes rt-stripe-move { to { background-position: 17px 0; } }
@media (prefers-reduced-motion: reduce) { .rt-stripe { animation: none; } .rt-bar-fill { transition: none; } }

.rt-toggle { display: flex; align-items: center; gap: 0.25rem; margin-bottom: 0.5rem; }
.rt-toggle-btn { padding: 0.125rem 0.5rem; font-size: 0.6875rem; border-radius: 0.25rem; background: rgba(148, 163, 184, 0.2); color: inherit; }
.rt-toggle-btn.is-on { background: var(--accent); color: #fff; }
.rt-gantt-axis, .rt-gantt-row { display: grid; grid-template-columns: minmax(8rem, 12rem) 1fr minmax(6rem, auto); align-items: center; gap: 0.75rem; }
.rt-gantt-axis { font-size: 0.6875rem; opacity: 0.7; padding-bottom: 0.25rem; border-bottom: 1px solid rgba(148, 163, 184, 0.3); margin-bottom: 0.25rem; }
.rt-gantt-ticks { display: flex; justify-content: space-between; }
.rt-gantt-row { padding: 0.1875rem 0; }
.rt-gantt-track { position: relative; height: 1.25rem; border-radius: 0.25rem; background: rgba(148, 163, 184, 0.15); }
.rt-gantt-bar, .rt-gantt-hold { position: absolute; top: 2px; bottom: 2px; min-width: 2px; border-radius: 2px; }
.rt-gantt-hold { background-color: transparent; background-image: repeating-linear-gradient(45deg, #7c3aed 0 3px, transparent 3px 6px); }
.rt-legend { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 0.5rem; padding-top: 0.5rem; border-top: 1px solid rgba(148, 163, 184, 0.3); font-size: 0.6875rem; }
.rt-legend i { display: inline-block; width: 0.625rem; height: 0.625rem; margin-right: 0.25rem; border-radius: 2px; }

.rt-s-passed { background-color: #059669; }
.rt-s-failed { background-color: #e11d48; }
.rt-s-slow { background-color: #d97706; }
.rt-s-running { background-color: #4f46e5; }
.rt-s-held { background-color: #7c3aed; }
.rt-s-idle { background-color: #94a3b8; }
.rt-f-reconciliation { background-color: #4f46e5; }
.rt-f-file { background-color: #0d9488; }
.rt-f-sap { background-color: #c2410c; }
.rt-f-orchestration { background-color: #7c3aed; }
.rt-f-quality { background-color: #be185d; }
.rt-f-other { background-color: #64748b; }

@media (max-width: 640px) {
  .rt-progress-row, .rt-gantt-axis, .rt-gantt-row { grid-template-columns: 1fr; gap: 0.25rem; }
  .rt-runtime { text-align: left; }
}
```

- [ ] **Step 6: Update the Monitor e2e test**

In `tests/e2e/60-sequence-flow-modes.spec.ts`, rename the test on line 91 to `'the monitor shows each job's progress and a timeline'` using double quotes for the string, and replace lines 120-124:

```ts
      // Progress tab: every job has a row, and the held job offers Release.
      await expect(run.getByTestId('run-progress-rows')).toBeVisible({ timeout: 30_000 });
      await expect(run.getByTestId('monitor-step-left')).toBeVisible();
      const right = run.getByTestId('monitor-step-right');
      await expect(right.getByRole('button', { name: 'Release' })).toBeVisible({ timeout: 30_000 });
      await expect(run.getByTestId('monitor-step-join')).toContainText('queued');
      await expect(run.getByTestId('run-elapsed')).not.toHaveText('—');

      // Timeline tab: jobs that ran have bars, and the waiting job has none yet.
      await run.getByTestId('run-tab-timeline').click();
      await expect(run.getByTestId('run-gantt-row-left').getByTestId('run-gantt-bar')).toBeVisible();
      await expect(run.getByTestId('run-gantt-row-right').getByTestId('run-gantt-bar')).toBeVisible();
      await expect(run.getByTestId('run-gantt-row-right').getByTestId('run-gantt-hold')).toBeVisible();
      await expect(run.getByTestId('run-gantt-row-join').getByTestId('run-gantt-bar')).toHaveCount(0);

      // The color toggle swaps the legend and survives a reload.
      const legend = run.getByTestId('run-gantt-legend');
      await expect(legend).toContainText('Passed');
      await run.getByTestId('gantt-color-family').click();
      await expect(legend).toContainText('Reconciliation');
      await page.reload();
      await page.getByTestId('nav-tab-monitor').click();
      const reloaded = page.getByTestId(`monitor-run-${runId}`);
      await expect(reloaded).toBeVisible({ timeout: 30_000 });
      await reloaded.getByTestId('run-tab-timeline').click();
      await expect(reloaded.getByTestId('run-gantt-legend')).toContainText('Reconciliation');
```

`createFileJob` jobs might have a type outside the reconciliation family, but the legend always lists all families, so the assertion holds.

- [ ] **Step 7: Build and run the tests**

Run: `node scripts/build-html.js`
Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_frontend.py -q -p no:cacheprovider`
Expected: PASS.

Run: `node node_modules/@playwright/test/cli.js test tests/e2e/60-sequence-flow-modes.spec.ts -g "progress and a timeline"`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add scripts/build-html.js frontend/partials/shared/run-gantt.html frontend/partials/tab-monitor.html frontend/features/monitor.js frontend/styles.css frontend/index.html tests/e2e/60-sequence-flow-modes.spec.ts
git commit -m "feat(ui): show job progress bars and a Gantt in the Monitor"
```

---

## Task 9: History timeline and sequence Recent runs

**Files:**
- Modify: `frontend/partials/tab-history.html`, `frontend/features/history.js:145-156`
- Modify: `frontend/partials/tab-sequences.html:79-85`, `frontend/features/sequences.js:9-12`, `frontend/features/sequences.js:128-136`
- Modify: `tests/e2e/60-sequence-flow-modes.spec.ts`
- Test: `tests/unit/test_run_timing_frontend.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_timing_frontend.py`:

```python
def test_built_page_includes_the_shared_gantt_in_monitor_and_history():
    subprocess.run([shutil.which("node"), "scripts/build-html.js"], check=True, cwd=str(ROOT))
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    assert "<!-- SHARED:" not in html
    assert html.count('data-testid="run-gantt"') == 2


def test_sequence_detail_has_recent_runs():
    markup = (ROOT / "frontend" / "partials" / "tab-sequences.html").read_text(encoding="utf-8")
    assert 'data-testid="sequence-recent-runs"' in markup
    script = (ROOT / "frontend" / "features" / "sequences.js").read_text(encoding="utf-8")
    assert "/runs?limit=20" in script


def test_history_detail_shows_elapsed_and_timeline():
    markup = (ROOT / "frontend" / "partials" / "tab-history.html").read_text(encoding="utf-8")
    assert 'data-testid="history-run-elapsed"' in markup
    assert "<!-- SHARED: shared/run-gantt.html -->" in markup
    script = (ROOT / "frontend" / "features" / "history.js").read_text(encoding="utf-8")
    assert "this.loadRunTiming(runId)" in script
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_frontend.py -q -p no:cacheprovider`
Expected: FAIL on the two new tests and the shared Gantt count.

- [ ] **Step 3: Add the History markup**

In `frontend/partials/tab-history.html`, after line 52 (the `Completed` row):

```html
          <template x-if="runTimings[selectedRun.run_id]">
            <div data-testid="history-run-elapsed">
              <span class="text-muted">Elapsed</span><br>
              <span x-text="fmtSeconds(runTimings[selectedRun.run_id].elapsed_seconds)"></span>
              <span class="text-xs text-muted" x-show="runTimings[selectedRun.run_id].held_seconds > 0"
                    x-text="'(held ' + fmtSeconds(runTimings[selectedRun.run_id].held_seconds) + ')'"></span>
            </div>
          </template>
```

Find the closing `</div>` of the `<!-- Chart + meta -->` `grid-2 mb-4` block, and add directly after it:

```html
      <template x-for="timing in [runTimings[selectedRun.run_id]].filter((t) => t && t.steps.length)" :key="selectedRun.run_id">
        <div class="card mb-4" data-testid="history-timeline">
          <div class="card-title mb-2">Timeline</div>
          <!-- SHARED: shared/run-gantt.html -->
        </div>
      </template>
```

- [ ] **Step 4: Load the timing in History**

In `frontend/features/history.js`, in `viewRunDetail`, after the `this.selectedRun.steps = ...` line:

```js
        await this.loadRunTiming(runId);
```

- [ ] **Step 5: Add Recent runs**

In `frontend/features/sequences.js`, add after `sequenceUsage: { selections: [], schedules: [] },`:

```js
      sequenceRuns: [],                // recent runs with elapsed time, from GET /api/sequences/{id}/runs
```

Replace `selectSequence`:

```js
      async selectSequence(sequence) {
        try {
          this.selectedSequence = await api('GET', `/api/sequences/${sequence.id}`);
          this.sequenceUsage = await api('GET', `/api/sequences/${sequence.id}/usage`);
        } catch {
          this.selectedSequence = null;
          this.sequenceUsage = { selections: [], schedules: [] };
        }
        try {
          this.sequenceRuns = await api('GET', `/api/sequences/${sequence.id}/runs?limit=20`);
        } catch {
          this.sequenceRuns = [];   // the detail is still useful without its history
        }
      },

      openSequenceRun(runId) {
        this.currentView = 'history';
        this.viewRunDetail(runId);
      },
```

In `frontend/partials/tab-sequences.html`, after the closing `</div>` of the `sequence-usage` block on line 85:

```html
      <div class="mt-4 text-sm" data-testid="sequence-recent-runs">
        <div class="font-medium mb-1">Recent runs</div>
        <div x-show="sequenceRuns.length === 0" class="text-muted">No runs yet.</div>
        <div class="overflow-x-auto" x-show="sequenceRuns.length > 0">
          <table class="w-full text-xs">
            <thead>
              <tr class="text-left text-muted">
                <th class="py-1 pr-3">Started</th>
                <th class="py-1 pr-3">Ended</th>
                <th class="py-1 pr-3">Elapsed</th>
                <th class="py-1 pr-3">Held</th>
                <th class="py-1">Status</th>
              </tr>
            </thead>
            <tbody>
              <template x-for="seqRun in sequenceRuns" :key="seqRun.run_id">
                <tr class="cursor-pointer hover:bg-subtle" tabindex="0"
                    :data-testid="'sequence-run-' + seqRun.run_id"
                    @click="openSequenceRun(seqRun.run_id)"
                    @keydown.enter="openSequenceRun(seqRun.run_id)">
                  <td class="py-1 pr-3" x-text="fmtDate(seqRun.started_at)"></td>
                  <td class="py-1 pr-3" x-text="fmtDate(seqRun.completed_at)"></td>
                  <td class="py-1 pr-3" x-text="fmtSeconds(seqRun.elapsed_seconds)"></td>
                  <td class="py-1 pr-3" x-text="fmtSeconds(seqRun.held_seconds)"></td>
                  <td class="py-1"><span class="badge" :class="statusBadgeClass(seqRun.status)" x-text="seqRun.status"></span></td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>
```

- [ ] **Step 6: Add the end-to-end browser test**

In `tests/e2e/60-sequence-flow-modes.spec.ts`, add after the Monitor test from Task 8, inside the same `describe`:

```ts
  test('a finished sequence shows its time in Recent runs and a timeline in History', async ({ authedPage: page, adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      const name = `e2e-timing-${Date.now()}`;
      const created = await ctx.post('/api/sequences', {
        data: {
          name,
          stages: [{ mode: 'serial', steps: [{ step_id: 'only', job_name: jobs[3] }] }],
        },
      });
      expect(created.ok()).toBe(true);
      const { id } = await created.json();
      const launched = await ctx.post(`/api/sequences/${id}/launch`, {
        data: { source_env: 'dev', target_env: 'dev' },
      });
      expect(launched.ok()).toBe(true);
      const { run_id: runId } = await launched.json();

      await expect.poll(async () => {
        const timing = await (await ctx.get(`/api/runs/${runId}/timing`)).json();
        return timing.completed_at;
      }, { timeout: 60_000 }).not.toBeNull();

      const history = await (await ctx.get(`/api/sequences/${id}/runs`)).json();
      expect(history[0].run_id).toBe(runId);
      expect(history[0].elapsed_seconds).not.toBeNull();

      await page.goto('/');
      await page.getByRole('button', { name: 'Sequences' }).click();
      await page.getByTestId(`sequence-row-${name}`).click();
      const row = page.getByTestId(`sequence-run-${runId}`);
      await expect(row).toBeVisible();
      await row.click();

      await expect(page.getByTestId('history-run-elapsed')).toBeVisible();
      const timeline = page.getByTestId('history-timeline');
      await expect(timeline.getByTestId('run-gantt-row-only').getByTestId('run-gantt-bar')).toBeVisible();
      await expect(timeline.getByTestId('gantt-color-status')).toHaveAttribute('aria-pressed', 'true');
    } finally {
      await ctx.dispose();
    }
  });
```

- [ ] **Step 7: Build and run all tests for this work**

Run: `node scripts/build-html.js`
Run: `C:\Python314\python.exe -m pytest tests/unit/test_run_timing_frontend.py tests/unit/test_batch_progress_frontend.py -q -p no:cacheprovider`
Expected: PASS.

Run: `node node_modules/@playwright/test/cli.js test tests/e2e/60-sequence-flow-modes.spec.ts tests/e2e/03-monitor.spec.ts`
Expected: PASS. See memory notes on `PORT 8055` if the Playwright web server can't bind. Use `rtk proxy` if the output comes through mangled.

- [ ] **Step 8: Commit**

```bash
git add frontend/partials/tab-history.html frontend/partials/tab-sequences.html frontend/features/history.js frontend/features/sequences.js frontend/index.html tests/unit/test_run_timing_frontend.py tests/e2e/60-sequence-flow-modes.spec.ts
git commit -m "feat(ui): add a run timeline to History and recent runs to Sequences"
```

---

## Task 10: Final check and docs

**Files:**
- Modify: `README.md`, `frontend/help-content.js`

- [ ] **Step 1: Document the feature**

In `README.md`, in the section that lists `atom` CLI commands, add:

```markdown
- `atom timing <run_id>`: start, end, elapsed time and each job's runtime for a run.
- `atom sequence-runs <id or name> [--limit 20]`: start, end and elapsed time for each run of a sequence.

`atom run` shows live colored job bars in a terminal, and plain status lines in CI or when piped. A blocked run now ends the wait and exits with code 1.
```

In `frontend/help-content.js`, in the Monitor entry, add this text in the same format as its neighbors:

```text
Each job shows a progress bar. The percent is an estimate from that job's last 10 results and stops at 95% until the job finishes. Amber means the job has passed its estimate. Moving stripes with no percent mean there isn't enough history yet. Timeline shows the jobs as a Gantt chart, colored by status or job type.
```

- [ ] **Step 2: Run the full unit suite**

Run: `C:\Python314\python.exe -m pytest tests/unit -q -p no:cacheprovider -n 4`
Expected: PASS, or only failures also present on `master` before this work. Compare any failure against `master` before dismissing it.

- [ ] **Step 3: Check the generated page is current**

Run: `node scripts/build-html.js`
Run: `git status --short frontend/index.html`
Expected: no change after Task 9's commit. If it shows modified, stage it in Step 5.

- [ ] **Step 4: Check it by eye in the browser**

Start the app, launch a sequence with a hold step, and confirm on the Monitor:

- Bars move about once a second.
- Elapsed keeps counting while held.
- Timeline shows hatched hold time.
- Colors read well in both themes.
- The Gantt is usable at phone width.

Open the finished run in History and Sequences. Note anything visibly wrong in the final report rather than claiming success from tests alone.

- [ ] **Step 5: Commit**

```bash
git add README.md frontend/help-content.js frontend/index.html
git commit -m "docs: describe job progress, run timing and the Gantt"
```

---

## Self-review notes

- **Spec coverage:** BLOCKED fix (Task 1). Data and timing rules (Tasks 2, 3). Progress rule and families (Task 3). API and stream (Task 4). CLI (Tasks 5, 6). Monitor and Gantt (Tasks 7, 8). History and Recent runs (Task 9). Docs (Task 10).
- **Known limit:** `_ensure_run_timing_columns` uses `ADD COLUMN`, which SQL Server rejects. Existing migrations share this limit, as the comment in `database.py` notes.
- **Task 1 risk:** adding `BLOCKED` to `TERMINAL_STATUSES` also changes `batch_launch`, `run_report` and `request_cancel`. Step 5 of that task runs the full suite to expose this. A blocked run can no longer be cancelled, which is correct because it has already ended.
