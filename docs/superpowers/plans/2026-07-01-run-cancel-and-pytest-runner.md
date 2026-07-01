# Run Cancellation & Pytest Suite Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cooperative cancellation to ETL runs via `POST /runs/{run_id}/cancel`, and expose pytest test suite runs in the existing runs system with live SSE progress and the same cancel endpoint.

**Architecture:** A `cancel_requested` boolean column on `TestRun` acts as the shared cancellation signal. `RunExecutor` checks it between steps; `PytestRunExecutor` checks it between parsed test lines and calls `process.terminate()`. Both executor types run as FastAPI `BackgroundTasks` and surface progress through the existing SSE stream at `GET /runs/{run_id}/stream`.

**Tech Stack:** FastAPI, SQLAlchemy (column type: `Boolean`), Python `subprocess.Popen`, pytest CLI (`python -m pytest`), existing `RunRepository`, existing SSE stream.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `etl_framework/repository/models.py` | Modify | Add `TERMINAL_STATUSES` constant + `cancel_requested` column to `TestRun` |
| `etl_framework/repository/repository.py` | Modify | Add `request_cancel()` and `is_cancel_requested()` to `RunRepository` |
| `api/schemas.py` | Modify | Add `TestSuiteTrigger` schema |
| `api/routes/runs.py` | Modify | Import `TERMINAL_STATUSES`; add `POST /{run_id}/cancel` and `POST /test-suite` endpoints |
| `api/services/run_executor.py` | Modify | Add cancel flag check after each step |
| `api/services/pytest_runner.py` | Create | `PytestRunExecutor` — spawns pytest subprocess, parses output, writes to DB |
| `tests/unit/test_run_cancel.py` | Create | Repository methods, cancel endpoint, executor cancel check |
| `tests/unit/test_pytest_runner.py` | Create | Output parsing, terminal status mapping, cancel signal |
| `tests/integration/test_cancel_flow.py` | Create | End-to-end: run starts → cancel fires → CANCELLED confirmed |

---

## Task 1: Add `TERMINAL_STATUSES` constant and `cancel_requested` column

**Files:**
- Modify: `etl_framework/repository/models.py:46-60`
- Modify: `api/routes/runs.py:46`

- [ ] **Step 1: Add constant and column to the model**

In `etl_framework/repository/models.py`, add the constant at module level (before the `TestRun` class) and the column inside `TestRun`:

```python
# After the imports block, before class TestRun:
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED"}
)
```

Inside `class TestRun`, after the `is_baseline` line (line 60):

```python
    cancel_requested = Column(Boolean, default=False, nullable=False)
```

- [ ] **Step 2: Replace the local constant in `api/routes/runs.py`**

Replace line 46:
```python
_TERMINAL = {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED"}
```
with:
```python
from etl_framework.repository.models import TERMINAL_STATUSES as _TERMINAL
```

Place this import with the other `etl_framework` imports (around line 36-41), not inline at line 46.

- [ ] **Step 3: Verify existing tests still pass**

```bash
pytest tests/unit/test_api.py tests/unit/test_run_executor.py -v
```

Expected: all green. If any test fails because `_TERMINAL` was used as a set (e.g. `in _TERMINAL`), `frozenset` is a drop-in replacement — no changes needed.

- [ ] **Step 4: Commit**

```bash
git add etl_framework/repository/models.py api/routes/runs.py
git commit -m "refactor: extract TERMINAL_STATUSES constant; add cancel_requested column to TestRun"
```

---

## Task 2: Repository — `request_cancel` and `is_cancel_requested`

**Files:**
- Create: `tests/unit/test_run_cancel.py`
- Modify: `etl_framework/repository/repository.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_cancel.py`:

```python
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_run(db: Session, run_id: str = "run-001", status: str = "RUNNING") -> None:
    repo = RunRepository(db)
    repo.create_run(run_id, None, None, run_type="reconciliation")
    repo.update_run_status(run_id, status)


# --- request_cancel ---

def test_request_cancel_sets_flag():
    db = _session()
    _make_run(db)
    repo = RunRepository(db)
    result = repo.request_cancel("run-001")
    assert result is True
    run = repo.get_run("run-001")
    assert run.cancel_requested is True


def test_request_cancel_returns_false_for_missing_run():
    db = _session()
    repo = RunRepository(db)
    assert repo.request_cancel("no-such-run") is False


def test_request_cancel_returns_false_for_terminal_run():
    db = _session()
    _make_run(db, status="PASSED")
    repo = RunRepository(db)
    assert repo.request_cancel("run-001") is False


# --- is_cancel_requested ---

def test_is_cancel_requested_false_by_default():
    db = _session()
    _make_run(db)
    assert RunRepository(db).is_cancel_requested("run-001") is False


def test_is_cancel_requested_true_after_request():
    db = _session()
    _make_run(db)
    repo = RunRepository(db)
    repo.request_cancel("run-001")
    assert repo.is_cancel_requested("run-001") is True
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/unit/test_run_cancel.py -v
```

Expected: FAIL — `RunRepository` has no `request_cancel` or `is_cancel_requested` methods.

- [ ] **Step 3: Implement the methods in `RunRepository`**

In `etl_framework/repository/repository.py`, add after the `update_run_status` method (around line 169):

```python
def request_cancel(self, run_id: str) -> bool:
    """Signal cancellation. Returns False if run not found or already terminal."""
    run = self.get_run(run_id)
    if run is None or run.status in TERMINAL_STATUSES:
        return False
    run.cancel_requested = True
    self._db.commit()
    return True

def is_cancel_requested(self, run_id: str) -> bool:
    """Re-fetch from DB (bypass identity-map cache) and return cancel flag."""
    self._db.expire_all()
    run = self.get_run(run_id)
    return bool(run and run.cancel_requested)
```

Add the import at the top of `repository.py` — it already imports from `models`, so add `TERMINAL_STATUSES` to that import:

```python
from etl_framework.repository.models import (
    ...,          # existing imports
    TERMINAL_STATUSES,
)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/unit/test_run_cancel.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_run_cancel.py
git commit -m "feat(cancel): add request_cancel and is_cancel_requested to RunRepository"
```

---

## Task 3: `POST /runs/{run_id}/cancel` endpoint

**Files:**
- Modify: `tests/unit/test_run_cancel.py`
- Modify: `api/routes/runs.py`

- [ ] **Step 1: Write the failing endpoint tests**

Append to `tests/unit/test_run_cancel.py`:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from api.main import app
from api.routes import runs as runs_module
from etl_framework.repository.database import get_db
from etl_framework.repository import database as _db_module
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_cancel_endpoint_returns_202(client):
    # Trigger a run (mocked executor is a no-op; run stays PENDING in DB)
    resp = client.post("/api/runs", json={
        "source_env": "dev",
        "target_env": "prod",
        "job_names": [],
    })
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    cancel_resp = client.post(f"/api/runs/{run_id}/cancel")
    assert cancel_resp.status_code == 202
    data = cancel_resp.json()
    assert data["run_id"] == run_id
    assert "cancel_requested" in data


def test_cancel_endpoint_404_for_unknown_run(client):
    resp = client.post("/api/runs/no-such-id/cancel")
    assert resp.status_code == 404


def test_cancel_endpoint_idempotent_when_called_twice(client):
    resp = client.post("/api/runs", json={
        "source_env": "dev",
        "target_env": "prod",
        "job_names": [],
    })
    run_id = resp.json()["run_id"]
    # Both calls return 202 — second call re-sets the same flag, no error
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 202
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 202
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/unit/test_run_cancel.py::test_cancel_endpoint_returns_202 \
       tests/unit/test_run_cancel.py::test_cancel_endpoint_404_for_unknown_run \
       tests/unit/test_run_cancel.py::test_cancel_endpoint_idempotent_on_terminal_run -v
```

Expected: FAIL — endpoint does not exist yet.

- [ ] **Step 3: Add the cancel endpoint to `api/routes/runs.py`**

Add after the `get_run_status` endpoint (around line 308):

```python
@router.post("/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    accepted = repo.request_cancel(run_id)
    return {"run_id": run_id, "cancel_requested": accepted}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/unit/test_run_cancel.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/runs.py tests/unit/test_run_cancel.py
git commit -m "feat(cancel): add POST /runs/{run_id}/cancel endpoint"
```

---

## Task 4: Cooperative cancellation in `RunExecutor`

**Files:**
- Modify: `tests/unit/test_run_cancel.py`
- Modify: `api/services/run_executor.py`

- [ ] **Step 1: Write the failing executor test**

Append to `tests/unit/test_run_cancel.py`:

```python
from unittest.mock import MagicMock, patch, call
from api.schemas import RunSettings, SequenceStep
from api.services.run_executor import RunExecutor


def _mock_repo_with_cancel(cancel_after_step: int = 0):
    """Return a mock RunRepository that signals cancel after N steps checked."""
    run_repo = MagicMock()
    run_repo.get_run.return_value = MagicMock(status="RUNNING", cancel_requested=False)
    call_count = {"n": 0}

    def is_cancel_requested(run_id):
        call_count["n"] += 1
        return call_count["n"] > cancel_after_step

    run_repo.is_cancel_requested.side_effect = is_cancel_requested
    return run_repo


def test_executor_stops_after_current_step_when_cancel_requested():
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-x", None, None)
    repo.update_run_status("run-x", "RUNNING")

    # Patch is_cancel_requested on the repo to return True after first check
    original_is_cancel = RunRepository.is_cancel_requested

    call_count = {"n": 0}
    def fake_is_cancel(self, run_id):
        call_count["n"] += 1
        return call_count["n"] >= 1  # True from first call onward

    with patch.object(RunRepository, "is_cancel_requested", fake_is_cancel):
        # Executor needs jobs in DB — create one minimal mock job
        from etl_framework.repository.repository import JobRepository
        JobRepository(db).create({
            "name": "job-a",
            "description": "",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT 1",
            "key_columns": [],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {"source_rows": [], "target_rows": []},
            "enabled": True,
        })
        JobRepository(db).create({
            "name": "job-b",
            "description": "",
            "tags": [],
            "job_type": "reconciliation",
            "query": "SELECT 1",
            "key_columns": [],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {"source_rows": [], "target_rows": []},
            "enabled": True,
        })
        RunExecutor(
            db=db,
            run_id="run-x",
            source_env="dev",
            target_env="prod",
            job_sequence=["job-a", "job-b"],
            run_settings=RunSettings(metrics_enabled=False),
        ).execute()

    run = RunRepository(db).get_run("run-x")
    assert run.status == "CANCELLED"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/unit/test_run_cancel.py::test_executor_stops_after_current_step_when_cancel_requested -v
```

Expected: FAIL — executor never checks cancel flag, run ends PASSED/COMPLETED instead of CANCELLED.

- [ ] **Step 3: Add the cancel check to `RunExecutor.execute()`**

In `api/services/run_executor.py`, after line 212 (`step_repo.update_status(self._run_id, i, job_outcome)`), insert:

```python
                    if self._run_repo.is_cancel_requested(self._run_id):
                        step_repo.cancel_remaining(self._run_id, from_index=i + 1)
                        cancelled = True
                        break
```

This goes **before** the `if seq_step.hold_after:` block that follows, so the full loop body in that region looks like:

```python
                    job_outcome = state.status.value if hasattr(state.status, "value") else str(state.status)
                    step_repo.update_status(self._run_id, i, job_outcome)

                    # Cooperative cancellation: check after each completed step
                    if self._run_repo.is_cancel_requested(self._run_id):
                        step_repo.cancel_remaining(self._run_id, from_index=i + 1)
                        cancelled = True
                        break

                    if seq_step.hold_after:
                        ...
```

- [ ] **Step 4: Run the full cancel test suite**

```bash
pytest tests/unit/test_run_cancel.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_cancel.py
git commit -m "feat(cancel): check cancel_requested flag in RunExecutor after each step"
```

---

## Task 5: `PytestRunExecutor`

**Files:**
- Create: `tests/unit/test_pytest_runner.py`
- Create: `api/services/pytest_runner.py`

- [ ] **Step 1: Write the failing unit tests**

Create `tests/unit/test_pytest_runner.py`:

```python
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch, call

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository
from api.services.pytest_runner import PytestRunExecutor


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _make_run(db: Session, run_id: str = "run-p1") -> None:
    RunRepository(db).create_run(run_id, None, None, run_type="test_suite")


def _executor(db: Session, run_id: str = "run-p1", args: list[str] | None = None) -> PytestRunExecutor:
    return PytestRunExecutor(db=db, run_id=run_id, pytest_args=args or [])


# --- Output parsing ---

def _fake_process(stdout_lines: list[str], exit_code: int = 0):
    proc = MagicMock()
    proc.stdout = iter(stdout_lines)
    proc.wait.return_value = exit_code
    proc.returncode = exit_code
    return proc


def test_parses_collected_items():
    db = _session()
    _make_run(db)
    proc = _fake_process(["collected 7 items\n", ""])

    with patch("subprocess.Popen", return_value=proc):
        _executor(db).execute()

    run = RunRepository(db).get_run("run-p1")
    assert run.total_tests == 7


def test_increments_passed_count():
    db = _session()
    _make_run(db)
    lines = [
        "collected 2 items\n",
        "tests/unit/test_foo.py::test_a PASSED   [ 50%]\n",
        "tests/unit/test_foo.py::test_b PASSED   [100%]\n",
        "",
    ]
    with patch("subprocess.Popen", return_value=_fake_process(lines, exit_code=0)):
        _executor(db).execute()

    run = RunRepository(db).get_run("run-p1")
    assert run.passed == 2
    assert run.failed == 0


def test_increments_failed_count():
    db = _session()
    _make_run(db)
    lines = [
        "collected 2 items\n",
        "tests/unit/test_foo.py::test_a PASSED   [ 50%]\n",
        "tests/unit/test_foo.py::test_b FAILED   [100%]\n",
        "",
    ]
    with patch("subprocess.Popen", return_value=_fake_process(lines, exit_code=1)):
        _executor(db).execute()

    run = RunRepository(db).get_run("run-p1")
    assert run.passed == 1
    assert run.failed == 1


def test_increments_error_count():
    db = _session()
    _make_run(db)
    lines = [
        "collected 1 items\n",
        "tests/unit/test_foo.py::test_a ERROR   [100%]\n",
        "",
    ]
    with patch("subprocess.Popen", return_value=_fake_process(lines, exit_code=1)):
        _executor(db).execute()

    run = RunRepository(db).get_run("run-p1")
    assert run.error == 1


# --- Terminal status mapping ---

def test_exit_0_sets_passed():
    db = _session()
    _make_run(db)
    with patch("subprocess.Popen", return_value=_fake_process([""])):
        _executor(db).execute()
    assert RunRepository(db).get_run("run-p1").status == "PASSED"


def test_exit_1_sets_completed():
    db = _session()
    _make_run(db)
    with patch("subprocess.Popen", return_value=_fake_process([""], exit_code=1)):
        _executor(db).execute()
    assert RunRepository(db).get_run("run-p1").status == "COMPLETED"


def test_exit_2_sets_error():
    db = _session()
    _make_run(db)
    with patch("subprocess.Popen", return_value=_fake_process([""], exit_code=2)):
        _executor(db).execute()
    assert RunRepository(db).get_run("run-p1").status == "ERROR"


# --- Cancellation ---

def test_cancel_terminates_process():
    db = _session()
    _make_run(db)

    lines = [
        "collected 3 items\n",
        "tests/unit/test_foo.py::test_a PASSED   [ 33%]\n",
        "tests/unit/test_foo.py::test_b PASSED   [ 66%]\n",
        "tests/unit/test_foo.py::test_c PASSED   [100%]\n",
        "",
    ]
    proc = _fake_process(lines, exit_code=0)

    call_count = {"n": 0}
    def fake_is_cancel(run_id):
        call_count["n"] += 1
        return call_count["n"] >= 2  # signal cancel after first test line

    with patch("subprocess.Popen", return_value=proc):
        with patch.object(RunRepository, "is_cancel_requested", lambda self, rid: fake_is_cancel(rid)):
            _executor(db).execute()

    proc.terminate.assert_called_once()
    assert RunRepository(db).get_run("run-p1").status == "CANCELLED"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/unit/test_pytest_runner.py -v
```

Expected: FAIL — `api/services/pytest_runner.py` does not exist.

- [ ] **Step 3: Create `api/services/pytest_runner.py`**

```python
from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from etl_framework.repository.repository import RunRepository

_COLLECTED_RE = re.compile(r"collected (\d+) items?")
_RESULT_RE = re.compile(r"\s+(PASSED|FAILED|ERROR)\s+\[")

_BATCH_SIZE = 5  # DB update frequency (every N parsed test lines)

_EXIT_STATUS = {
    0: "PASSED",
    1: "COMPLETED",
}


class PytestRunExecutor:
    def __init__(self, db: Session, run_id: str, pytest_args: list[str]) -> None:
        self._db = db
        self._run_id = run_id
        self._pytest_args = pytest_args
        self._run_repo = RunRepository(db)

    def execute(self) -> None:
        self._run_repo.update_run_status(
            self._run_id, "RUNNING", started_at=datetime.now(timezone.utc)
        )

        cmd = [
            sys.executable, "-m", "pytest",
            "--tb=short", "-v", "--no-header",
        ] + self._pytest_args

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        passed = failed = error = 0
        batch_count = 0

        try:
            for line in proc.stdout:
                collected = _COLLECTED_RE.search(line)
                if collected:
                    self._run_repo.update_run_status(
                        self._run_id, "RUNNING",
                        total_tests=int(collected.group(1)),
                    )
                    continue

                match = _RESULT_RE.search(line)
                if match:
                    outcome = match.group(1)
                    if outcome == "PASSED":
                        passed += 1
                    elif outcome == "FAILED":
                        failed += 1
                    elif outcome == "ERROR":
                        error += 1

                    batch_count += 1
                    if batch_count >= _BATCH_SIZE:
                        self._run_repo.update_run_status(
                            self._run_id, "RUNNING",
                            passed=passed, failed=failed, error=error,
                        )
                        batch_count = 0

                if self._run_repo.is_cancel_requested(self._run_id):
                    proc.terminate()
                    self._run_repo.update_run_status(
                        self._run_id, "CANCELLED",
                        completed_at=datetime.now(timezone.utc),
                        passed=passed, failed=failed, error=error,
                    )
                    return

        finally:
            # Flush any remaining unbatched counts
            pass

        exit_code = proc.wait()
        final_status = _EXIT_STATUS.get(exit_code, "ERROR")
        self._run_repo.update_run_status(
            self._run_id, final_status,
            completed_at=datetime.now(timezone.utc),
            passed=passed, failed=failed, error=error,
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/unit/test_pytest_runner.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/pytest_runner.py tests/unit/test_pytest_runner.py
git commit -m "feat(pytest-runner): add PytestRunExecutor with output parsing and cancel support"
```

---

## Task 6: `TestSuiteTrigger` schema and `POST /runs/test-suite` endpoint

**Files:**
- Modify: `api/schemas.py`
- Modify: `tests/unit/test_pytest_runner.py`
- Modify: `api/routes/runs.py`

- [ ] **Step 1: Add `TestSuiteTrigger` to `api/schemas.py`**

Add after `RunStatusOut` (around line 226):

```python
class TestSuiteTrigger(BaseModel):
    pytest_args: list[str] = []
```

- [ ] **Step 2: Write the failing endpoint test**

Append to `tests/unit/test_pytest_runner.py`:

```python
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from api.main import app
from api.routes import runs as runs_module
from etl_framework.repository.database import get_db
from etl_framework.repository import database as _db_module
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    # Prevent actual pytest subprocess from running
    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(runs_module, "_run_pytest", lambda *args, **kwargs: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_trigger_test_suite_returns_202(client):
    resp = client.post("/api/runs/test-suite", json={"pytest_args": ["tests/unit/"]})
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "PENDING"
    assert data["run_type"] == "test_suite"
    assert "run_id" in data


def test_trigger_test_suite_empty_args(client):
    resp = client.post("/api/runs/test-suite", json={})
    assert resp.status_code == 202
```

- [ ] **Step 3: Run to confirm they fail**

```bash
pytest tests/unit/test_pytest_runner.py::test_trigger_test_suite_returns_202 \
       tests/unit/test_pytest_runner.py::test_trigger_test_suite_empty_args -v
```

Expected: FAIL — endpoint does not exist.

- [ ] **Step 4: Add the endpoint and background helper to `api/routes/runs.py`**

Add the import at the top of `runs.py` alongside the other service imports:

```python
from api.services.pytest_runner import PytestRunExecutor
from api.schemas import TestSuiteTrigger
```

Add the background helper function (alongside `_execute_run`):

```python
def _run_pytest(
    run_id: str,
    pytest_args: list[str],
    session_factory=None,
) -> None:
    from etl_framework.repository.database import SessionLocal
    from etl_framework.utils.context import set_run_id

    set_run_id(run_id)
    db = (session_factory or SessionLocal)()
    try:
        PytestRunExecutor(db=db, run_id=run_id, pytest_args=pytest_args).execute()
    finally:
        db.close()
```

Add the endpoint (place it **before** `GET /{run_id}/status` so it doesn't conflict with path routing):

```python
@router.post("/test-suite", response_model=RunStatusOut, status_code=202)
def trigger_test_suite(
    body: TestSuiteTrigger,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    run_id = str(uuid.uuid4())
    RunRepository(db).create_run(
        run_id=run_id,
        source_env=None,
        target_env=None,
        run_type="test_suite",
    )
    background_tasks.add_task(_run_pytest, run_id, body.pytest_args)
    return RunStatusOut(run_id=run_id, status="PENDING", run_type="test_suite")
```

- [ ] **Step 5: Run all pytest runner tests**

```bash
pytest tests/unit/test_pytest_runner.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run the full unit suite to catch regressions**

```bash
pytest tests/unit/ -v --tb=short
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add api/schemas.py api/routes/runs.py tests/unit/test_pytest_runner.py
git commit -m "feat(pytest-runner): add TestSuiteTrigger schema and POST /runs/test-suite endpoint"
```

---

## Task 7: Integration test for the cancel flow

**Files:**
- Create: `tests/integration/test_cancel_flow.py`

- [ ] **Step 1: Create the integration test**

Create `tests/integration/test_cancel_flow.py`:

```python
"""Integration test for cooperative run cancellation.

Two threads share a temp-file SQLite DB (same pattern as test_hold_polling.py).
Thread A runs RunExecutor; thread B fires request_cancel() mid-run.
"""
from __future__ import annotations

import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from etl_framework.repository.database import Base, _ensure_compare_columns
import etl_framework.repository.models as _models  # noqa: F401
from etl_framework.repository.repository import JobRepository, RunRepository, RunStepRepository
from api.schemas import RunSettings
from api.services.run_executor import RunExecutor


def _make_engine(path: str):
    url = f"sqlite:///{path}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    _ensure_compare_columns(engine)
    return engine


def _session(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "test_cancel.db")


def _seed_jobs(engine):
    """Create two minimal reconciliation jobs."""
    db = _session(engine)
    try:
        repo = JobRepository(db)
        for name in ("step-a", "step-b"):
            repo.create({
                "name": name,
                "description": "",
                "tags": [],
                "job_type": "reconciliation",
                "query": "SELECT 1",
                "key_columns": [],
                "exclude_columns": [],
                "source_env": None,
                "target_env": None,
                "params": {"source_rows": [], "target_rows": []},
                "enabled": True,
            })
    finally:
        db.close()


def _wait_for_status(engine, run_id: str, status: str, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db = _session(engine)
        try:
            run = RunRepository(db).get_run(run_id)
            if run and run.status == status:
                return True
        finally:
            db.close()
        time.sleep(0.1)
    return False


def test_cancel_stops_run_after_current_step(db_path):
    engine = _make_engine(db_path)
    _seed_jobs(engine)

    run_id = str(uuid.uuid4())
    db_setup = _session(engine)
    try:
        RunRepository(db_setup).create_run(run_id, "dev", "prod")
    finally:
        db_setup.close()

    errors: list[Exception] = []

    def run_executor():
        db = _session(engine)
        try:
            RunExecutor(
                db=db,
                run_id=run_id,
                source_env="dev",
                target_env="prod",
                job_sequence=["step-a", "step-b"],
                run_settings=RunSettings(metrics_enabled=False),
            ).execute()
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    t = threading.Thread(target=run_executor, daemon=True)
    t.start()

    # Wait for run to reach RUNNING, then cancel
    assert _wait_for_status(engine, run_id, "RUNNING"), "Run never reached RUNNING"

    db_cancel = _session(engine)
    try:
        RunRepository(db_cancel).request_cancel(run_id)
    finally:
        db_cancel.close()

    t.join(timeout=20)
    assert not t.is_alive(), "Executor thread did not finish"
    assert not errors, f"Executor raised: {errors}"

    db_check = _session(engine)
    try:
        run = RunRepository(db_check).get_run(run_id)
        assert run.status == "CANCELLED", f"Expected CANCELLED, got {run.status}"

        steps = RunStepRepository(db_check).list_steps(run_id)
        running_steps = [s for s in steps if s.status == "RUNNING"]
        assert not running_steps, f"Steps still RUNNING after cancel: {running_steps}"
    finally:
        db_check.close()
```

- [ ] **Step 2: Run the integration test**

```bash
pytest tests/integration/test_cancel_flow.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/ -v --tb=short -q
```

Expected: all green (existing tests unaffected).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_cancel_flow.py
git commit -m "test(cancel): add integration test for cooperative run cancellation"
```

---

## Done

All tasks complete when:
- `pytest tests/unit/test_run_cancel.py tests/unit/test_pytest_runner.py tests/integration/test_cancel_flow.py -v` is all green
- `pytest tests/ -q` shows no regressions
- `POST /api/runs/{run_id}/cancel` returns 202 for any active run
- `POST /api/runs/test-suite` returns 202, run appears in run list, SSE stream shows live progress
