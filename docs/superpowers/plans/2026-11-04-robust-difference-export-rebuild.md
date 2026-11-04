# Robust Difference Export Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A difference export survives a failing job, always reaches a finished state the page can see, shows per-job progress, and labels rebuilt data.

**Architecture:** A new `api/services/export_progress.py` owns per-job progress and a heartbeat thread. `difference_export.py` isolates each job's rebuild and expires stale exports. The History page polls with no deadline and renders progress from the export's `metadata_json`.

**Tech Stack:** FastAPI, SQLAlchemy, pandas, Alpine.js, Jinja2, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-04-robust-difference-export-rebuild-design.md`

**Running tests:** use `C:\Python314\python.exe -m pytest` directly, because the repo `.venv` lacks pytest and `rtk pytest` can show a stale summary. Run Playwright with `node node_modules/@playwright/test/cli.js test`.

**Commits:** commit only when the user has approved it. The last task holds the commit.

---

## File structure

- Create `api/services/export_progress.py`: `ExportProgress` and `ExportHeartbeat`.
- Create `tests/unit/test_export_progress.py`.
- Modify `etl_framework/repository/models.py`, `etl_framework/repository/database.py`: `heartbeat_at`.
- Modify `api/services/difference_export.py`: isolation, expiry, reuse, wiring, ad-hoc SQL.
- Modify `api/routes/runs.py`: expire on status polls.
- Modify `api/services/run_report.py`, `etl_framework/reporting/templates/report.html.j2`: rebuilt banner.
- Modify `frontend/features/compare.js`, `frontend/partials/tab-history.html`, `frontend/help-content.js`; regenerate `frontend/index.html`.
- Modify `tests/unit/test_difference_export_jobs.py`, `tests/unit/test_difference_export.py`, `tests/unit/test_full_html_report.py`.
- Create `tests/e2e/44-export-progress.spec.ts`.

---

### Task 1: Progress tracker and heartbeat

**Files:**
- Create: `api/services/export_progress.py`
- Test: `tests/unit/test_export_progress.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Per-job export progress and the heartbeat that publishes it."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.export_progress import ExportHeartbeat, ExportProgress
from etl_framework.repository.database import Base
from etl_framework.repository.models import DifferenceExportJob, TestRun


def _states(progress: ExportProgress) -> list[str]:
    return [job["state"] for job in progress.snapshot()["jobs"]]


def test_jobs_start_waiting():
    progress = ExportProgress()
    progress.set_jobs(["a", "b"])
    assert _states(progress) == ["waiting", "waiting"]


def test_job_moves_through_running_to_done_with_its_rows():
    progress = ExportProgress()
    progress.set_jobs(["a"])
    progress.start("a")
    assert _states(progress) == ["running"]
    assert progress.snapshot()["jobs"][0]["started_at"] is not None

    progress.finish("a", 7)

    job = progress.snapshot()["jobs"][0]
    assert job["state"] == "done"
    assert job["rows"] == 7
    assert job["finished_at"] is not None


def test_failed_job_keeps_its_reason_and_is_listed():
    progress = ExportProgress()
    progress.set_jobs(["a", "b"])
    progress.start("a")
    progress.fail("a", "ORA-12541", rows=2)

    job = progress.snapshot()["jobs"][0]
    assert job["state"] == "failed"
    assert job["error"] == "ORA-12541"
    assert job["rows"] == 2
    assert progress.failed_jobs == ["a"]


def test_same_job_twice_in_a_sequence_tracks_each_step():
    progress = ExportProgress()
    progress.set_jobs(["a", "a"])
    progress.start("a")
    progress.finish("a", 1)
    progress.start("a")
    assert _states(progress) == ["done", "running"]


def test_snapshot_is_a_copy():
    progress = ExportProgress()
    progress.set_jobs(["a"])
    progress.snapshot()["jobs"][0]["state"] = "changed"
    assert _states(progress) == ["waiting"]


def test_every_change_notifies_the_listener():
    calls = []
    progress = ExportProgress()
    progress.on_change = lambda: calls.append(1)
    progress.set_jobs(["a"])
    progress.start("a")
    progress.finish("a", 0)
    assert len(calls) == 3


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'repo.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(TestRun(run_id="run-1", status="COMPLETED"))
        db.add(DifferenceExportJob(
            export_id="exp-1", run_id="run-1", format="csv", status="RUNNING",
            created_at=datetime.now(timezone.utc),
        ))
        db.commit()
    yield factory
    engine.dispose()


def _export(factory) -> DifferenceExportJob:
    with factory() as db:
        return db.query(DifferenceExportJob).filter_by(export_id="exp-1").one()


def _wait_for(condition, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_heartbeat_writes_on_its_interval(session_factory):
    with ExportHeartbeat("exp-1", ExportProgress(), session_factory, interval=0.02):
        assert _wait_for(lambda: _export(session_factory).heartbeat_at is not None)


def test_heartbeat_publishes_a_change_without_waiting_for_the_interval(session_factory):
    progress = ExportProgress()
    with ExportHeartbeat("exp-1", progress, session_factory, interval=60):
        progress.set_jobs(["a"])
        assert _wait_for(lambda: _export(session_factory).metadata_json is not None)
    assert _export(session_factory).metadata_json["progress"]["jobs"][0]["name"] == "a"


def test_heartbeat_does_not_touch_an_export_that_has_finished(session_factory):
    with session_factory() as db:
        db.query(DifferenceExportJob).update({"status": "COMPLETED"})
        db.commit()
    with ExportHeartbeat("exp-1", ExportProgress(), session_factory, interval=0.01):
        time.sleep(0.1)
    assert _export(session_factory).heartbeat_at is None


def test_heartbeat_errors_do_not_reach_the_export():
    def broken_factory():
        raise RuntimeError("database is gone")

    progress = ExportProgress()
    with ExportHeartbeat("exp-1", progress, broken_factory, interval=0.01):
        progress.set_jobs(["a"])
        time.sleep(0.05)
```

- [ ] **Step 2: Run to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_export_progress.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.services.export_progress'`

- [ ] **Step 3: Implement**

```python
"""Per-job progress of a difference export, and the thread that publishes it."""
from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from etl_framework.repository.models import DifferenceExportJob

logger = logging.getLogger("api.services.export_progress")

HEARTBEAT_INTERVAL_SECONDS = 30.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExportProgress:
    """What each job of an export is doing. Safe to read from another thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: list[dict[str, Any]] = []
        self.on_change: Callable[[], None] | None = None

    def set_jobs(self, names: list[str]) -> None:
        with self._lock:
            self._jobs = [
                {
                    "name": name, "state": "waiting", "rows": 0,
                    "started_at": None, "finished_at": None, "error": None,
                }
                for name in names
            ]
        self._changed()

    def start(self, name: str) -> None:
        self._update(name, "waiting", state="running", started_at=_now_iso())

    def finish(self, name: str, rows: int) -> None:
        self._update(name, "running", state="done", rows=rows, finished_at=_now_iso())

    def fail(self, name: str, error: str, rows: int = 0) -> None:
        self._update(name, "running", state="failed", rows=rows, error=error, finished_at=_now_iso())

    @property
    def failed_jobs(self) -> list[str]:
        with self._lock:
            return [job["name"] for job in self._jobs if job["state"] == "failed"]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"jobs": copy.deepcopy(self._jobs)}

    def _update(self, name: str, from_state: str, **fields: Any) -> None:
        # A DAG may run one job as two steps, so match the first entry in the
        # state this change moves on from, not simply the first with this name.
        with self._lock:
            for job in self._jobs:
                if job["name"] == name and job["state"] == from_state:
                    job.update(fields)
                    break
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()


class ExportHeartbeat:
    """Shows an export is alive, and publishes its progress, from its own session.

    Runs on a thread because a single slow query can outlast the staleness limit
    with nothing else happening. A failed write is logged and tried again on the
    next beat: reporting progress must never fail the export it reports on.
    """

    def __init__(
        self,
        export_id: str,
        progress: ExportProgress,
        session_factory: Callable[[], Any],
        interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._export_id = export_id
        self._progress = progress
        self._session_factory = session_factory
        self._interval = interval
        self._wake = threading.Event()
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"export-heartbeat-{export_id}")

    def __enter__(self) -> "ExportHeartbeat":
        self._progress.on_change = self._wake.set
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._progress.on_change = None
        self._stopping = True
        self._wake.set()
        self._thread.join(timeout=5)
        return False

    def _loop(self) -> None:
        while True:
            self._wake.wait(self._interval)
            self._wake.clear()
            if self._stopping:
                return
            self._beat()

    def _beat(self) -> None:
        db = None
        try:
            db = self._session_factory()
            # Only while RUNNING, so a beat that lands late cannot overwrite the
            # final metadata of an export that has just finished.
            db.query(DifferenceExportJob).filter(
                DifferenceExportJob.export_id == self._export_id,
                DifferenceExportJob.status == "RUNNING",
            ).update(
                {
                    "heartbeat_at": datetime.now(timezone.utc),
                    "metadata_json": {"progress": self._progress.snapshot()},
                },
                synchronize_session=False,
            )
            db.commit()
        except Exception:
            logger.warning("Heartbeat failed for export %s", self._export_id, exc_info=True)
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    logger.warning("Rollback after a failed heartbeat also failed", exc_info=True)
        finally:
            if db is not None:
                db.close()
```

- [ ] **Step 4: Add the column so the module's tests can run**

In `etl_framework/repository/models.py`, in `DifferenceExportJob`, after `recomputed_at`:

```python
    recomputed_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
```

In `etl_framework/repository/database.py`, in the `difference_export_jobs` `ensure_table` statement, replace `"recomputed_at DATETIME)"` with:

```python
            "recomputed_at DATETIME, "
            "heartbeat_at DATETIME)"
```

and directly after that `ensure_table(...)` call add:

```python
        ensure_column(conn, "difference_export_jobs", "heartbeat_at", "ALTER TABLE difference_export_jobs ADD COLUMN heartbeat_at DATETIME")
```

- [ ] **Step 5: Run to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_export_progress.py -q`
Expected: PASS

---

### Task 2: Stale exports expire, failed exports retry

**Files:**
- Modify: `api/services/difference_export.py` (`create_or_reuse_export_job`)
- Modify: `api/routes/runs.py:1019-1034`
- Test: `tests/unit/test_difference_export_jobs.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_difference_export_jobs.py`, adding `from datetime import timedelta` to the imports:

```python
def _open_export(db, run_id: str, status: str, *, age_seconds: float, heartbeat_age: float | None = None):
    now = datetime.now(timezone.utc)
    job = DifferenceExportJob(
        export_id=f"open-{status}", run_id=run_id, format="csv", status=status,
        created_at=now - timedelta(seconds=age_seconds),
        heartbeat_at=None if heartbeat_age is None else now - timedelta(seconds=heartbeat_age),
    )
    db.add(job)
    db.commit()
    return job


def test_running_export_with_a_dead_heartbeat_expires():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        job = _open_export(db, run_id, "RUNNING", age_seconds=900, heartbeat_age=301)

        assert de.expire_if_stale(db, job) is True

    assert job.status == "FAILED"
    assert "stopped unexpectedly" in job.error_message


def test_running_export_with_a_fresh_heartbeat_does_not_expire():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        job = _open_export(db, run_id, "RUNNING", age_seconds=9000, heartbeat_age=10)

        assert de.expire_if_stale(db, job) is False
        assert job.status == "RUNNING"


def test_pending_export_that_never_started_expires_from_its_creation_time():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        job = _open_export(db, run_id, "PENDING", age_seconds=301)

        assert de.expire_if_stale(db, job) is True


def test_finished_export_never_expires():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        job = _open_export(db, run_id, "COMPLETED", age_seconds=9000)

        assert de.expire_if_stale(db, job) is False
        assert job.status == "COMPLETED"


def test_new_request_replaces_a_stuck_export():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        stuck = _open_export(db, run_id, "RUNNING", age_seconds=900, heartbeat_age=600)

        job, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is True
    assert job.export_id != stuck.export_id
    assert stuck.status == "FAILED"


def test_new_request_joins_a_live_export():
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        live = _open_export(db, run_id, "RUNNING", age_seconds=900, heartbeat_age=5)

        job, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is False
    assert job.export_id == live.export_id


def test_export_with_a_failed_job_is_rebuilt_on_the_next_request(tmp_path):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=1)
        _completed_export(db, run_id, tmp_path / "old.csv", rows=5)
        old = db.query(DifferenceExportJob).filter_by(export_id="old").one()
        old.metadata_json = {"failed_jobs": ["curves"]}
        db.commit()

        _, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is True


def test_export_noting_only_unrebuildable_jobs_is_reused(tmp_path):
    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=1)
        _completed_export(db, run_id, tmp_path / "old.csv", rows=5)
        old = db.query(DifferenceExportJob).filter_by(export_id="old").one()
        old.metadata_json = {"failed_jobs": []}
        db.commit()

        _, created = de.create_or_reuse_export_job(db, run_id, "csv")

    assert created is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py -q -k "expire or stuck or live_export or rebuilt_on_the_next or unrebuildable"`
Expected: FAIL with `AttributeError: ... has no attribute 'expire_if_stale'`, and the retry test failing on `created`.

- [ ] **Step 3: Implement**

In `api/services/difference_export.py`, add `timedelta` to the datetime import and, under `JOB_NOTE_COLUMN`:

```python
# Several heartbeats long, so a missed write or a busy database does not end an
# export that is still working.
EXPORT_STALE_AFTER = timedelta(minutes=5)
EXPORT_STALE_MESSAGE = "Export stopped unexpectedly, try again."
```

Add above `create_or_reuse_export_job`:

```python
def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def expire_if_stale(db: Session, job: DifferenceExportJob) -> bool:
    """End an unfinished export nothing is working on any more -- its worker was
    restarted, say. Checked on demand, not at startup, because with several workers
    a restarting one must not end exports the others are still running."""
    if job.status not in ("PENDING", "RUNNING"):
        return False
    last_alive = _as_utc(job.heartbeat_at or job.created_at)
    if _utcnow() - last_alive <= EXPORT_STALE_AFTER:
        return False
    job.status = "FAILED"
    job.error_message = EXPORT_STALE_MESSAGE
    job.completed_at = _utcnow()
    db.commit()
    return True
```

In `create_or_reuse_export_job`, replace

```python
    if existing is not None:
        return existing, False
```

with

```python
    if existing is not None and not expire_if_stale(db, existing):
        return existing, False
```

and replace the completed-export condition with

```python
    if (
        completed is not None
        and completed.artifact_path
        and Path(completed.artifact_path).exists()
        and int(completed.row_count or 0) >= _minimum_export_rows(db, run_id)
        and not (completed.metadata_json or {}).get("failed_jobs")
    ):
        return completed, False
```

In `api/routes/runs.py`, add `expire_if_stale` to the `from api.services.difference_export import (...)` list, and in `get_difference_export` replace the final `return export_status_out(job)` with:

```python
    expire_if_stale(db, job)
    return export_status_out(job)
```

- [ ] **Step 4: Run to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py -q`
Expected: PASS

---

### Task 3: One failing job no longer fails the export

**Files:**
- Modify: `api/services/difference_export.py` (`write_recomputed_differences`, `write_full_html_report`, `_write_reconciliation_run`)
- Test: `tests/unit/test_difference_export_jobs.py`, `tests/unit/test_difference_export.py`, `tests/unit/test_full_html_report.py`

- [ ] **Step 1: Write the failing tests**

Replace `test_sql_job_recompute_requires_live_connections` and `test_sql_job_recompute_refuses_a_query_that_is_not_read_only` in `tests/unit/test_difference_export_jobs.py` with:

```python
def test_sql_job_with_live_connections_off_becomes_a_note(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(1))
        _run(db, ["curves"], live=False)

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == [de.EXPORT_INCOMPLETE_TYPE]
    assert "live connections" in rows[0]["source_value"]
    assert _FrameDBEngine.queries == []


def test_sql_job_with_a_query_that_is_not_read_only_becomes_a_note(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(1))
        job = JobRepository(db).get("curves")
        job.params = {
            **job.params,
            "request": {**job.params["request"], "query_b": "DELETE FROM curves"},
        }
        db.commit()
        _run(db, ["curves"])

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == [de.EXPORT_INCOMPLETE_TYPE]
    assert "read-only" in rows[0]["source_value"]
    assert _FrameDBEngine.queries == []
```

Append:

```python
def test_failing_job_uses_its_stored_rows_and_the_next_job_still_exports(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "broken", *_differing_frames(4))
        _save_sql_job(db, "healthy", *_differing_frames(2))
        del _FrameDBEngine.frames["SELECT * FROM broken_a"]
        run_id = _run(db, ["broken", "healthy"])
        _add_result(db, run_id, "broken", issues=4, stored=1)
        _add_result(db, run_id, "healthy", issues=2)
        progress = ExportProgress()

        path = tmp_path / "out.jsonl"
        count = de.write_recomputed_differences(db, _get_run(db), "json", path, progress=progress)

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert count == len(rows)
    broken = [row for row in rows if row["test_name"] == "broken"]
    assert [row["mismatch_type"] for row in broken] == ["value_diff", de.EXPORT_INCOMPLETE_TYPE]
    assert "1 of 4" in broken[-1]["source_value"]
    assert len([row for row in rows if row["test_name"] == "healthy"]) == 2
    assert progress.failed_jobs == ["broken"]
    assert [job["state"] for job in progress.snapshot()["jobs"]] == ["failed", "done"]


def test_job_failing_partway_keeps_its_rows_and_adds_no_stored_duplicates(tmp_path, monkeypatch):
    def write_one_row_then_fail(df_source, df_target, key_columns, exclude_columns, options, test_name, writer):
        writer.write({
            "test_name": test_name, "key_values": {"id": 1}, "column_name": "amount",
            "source_value": "1", "target_value": "2", "mismatch_type": "value_diff",
        })
        raise RuntimeError("pair 2 could not be read")

    monkeypatch.setattr(de, "_write_tabular_differences", write_one_row_then_fail)
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "broken", *_differing_frames(1))
        run_id = _run(db, ["broken"])
        _add_result(db, run_id, "broken", issues=3, stored=3)

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == ["value_diff", de.EXPORT_INCOMPLETE_TYPE]
    assert "pair 2 could not be read" in rows[-1]["source_value"]


def test_long_failure_reason_is_cut_to_500_characters(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("x" * 2000)

    monkeypatch.setattr(de, "_write_tabular_differences", fail)
    progress = ExportProgress()
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "broken", *_differing_frames(1))
        run_id = _run(db, ["broken"])
        _add_result(db, run_id, "broken", issues=1)

        de.write_recomputed_differences(db, _get_run(db), "json", tmp_path / "o.jsonl", progress=progress)

    assert len(progress.snapshot()["jobs"][0]["error"]) == 500


def test_progress_lists_only_jobs_needing_a_rebuild(tmp_path):
    progress = ExportProgress()
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "passed_job", *_differing_frames(1))
        _save_sql_job(db, "errored_job", *_differing_frames(1))
        _save_sql_job(db, "failed_job", *_differing_frames(3))
        run_id = _run(db, ["passed_job", "errored_job", "failed_job"])
        _add_result(db, run_id, "passed_job")
        _add_error(db, run_id, "errored_job", "boom")
        _add_result(db, run_id, "failed_job", issues=3)

        de.write_recomputed_differences(db, _get_run(db), "json", tmp_path / "o.jsonl", progress=progress)

    jobs = progress.snapshot()["jobs"]
    assert [job["name"] for job in jobs] == ["failed_job"]
    assert jobs[0]["state"] == "done"
    assert jobs[0]["rows"] == 3


def test_job_that_can_never_be_rebuilt_is_not_a_failed_job(tmp_path):
    progress = ExportProgress()
    with _db_module.SessionLocal() as db:
        _save_job(db, "api_orders", "api_reconciliation")
        run_id = _run(db, ["api_orders"])
        _add_result(db, run_id, "api_orders", issues=5, stored=2)

        de.write_recomputed_differences(db, _get_run(db), "json", tmp_path / "o.jsonl", progress=progress)

    assert progress.failed_jobs == []
```

Add `from api.services.export_progress import ExportProgress` to the imports.

In `tests/unit/test_difference_export.py`, replace lines 1034-1035 and 1101-1102 respectively with:

```python
        de.write_recomputed_differences(db, run, "json", out_path)
    row = json.loads(out_path.read_text(encoding="utf-8").strip())
    assert row["mismatch_type"] == de.EXPORT_INCOMPLETE_TYPE
    assert "require live connections" in row["source_value"]
```

Update each `_fake_recompute` in `tests/unit/test_full_html_report.py` to accept the new argument: `def _fake_recompute(db, run, fmt, path, progress=None):`.

- [ ] **Step 2: Run to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py -q -k "note or failing or partway or 500 or needing or never_be"`
Expected: FAIL. The note tests raise `ValueError`, and the others raise `TypeError: ... unexpected keyword argument 'progress'`.

- [ ] **Step 3: Implement**

In `api/services/difference_export.py`, add the import `from api.services.export_progress import ExportHeartbeat, ExportProgress`, and under `EXPORT_STALE_MESSAGE`:

```python
REBUILD_ERROR_MAX_CHARS = 500
```

Change the signature and dispatch of `write_recomputed_differences`:

```python
def write_recomputed_differences(
    db: Session,
    run: TestRun,
    fmt: str,
    path: Path,
    progress: ExportProgress | None = None,
) -> int:
```

and its last branch to:

```python
        else:
            _write_reconciliation_run(db, run, writer, progress or ExportProgress())
```

Change `write_full_html_report` to:

```python
def write_full_html_report(
    db: Session,
    run: TestRun,
    path: Path,
    progress: ExportProgress | None = None,
) -> int:
```

and its recompute call to:

```python
    row_count = write_recomputed_differences(db, run, "json", tmp_path, progress=progress)
```

Replace everything in `_write_reconciliation_run` from `def` through the end of the function with:

```python
def _write_reconciliation_run(
    db: Session,
    run: TestRun,
    writer: DifferenceWriter,
    progress: ExportProgress,
) -> None:
    snapshot = run.config_snapshot or {}
    job_names = [
        item.get("job_name") if isinstance(item, dict) else str(item)
        for item in snapshot.get("job_sequence") or [result.query_name for result in run.results]
    ]
    settings_data = snapshot.get("run_settings") or {}
    settings = RunSettings(**settings_data) if isinstance(settings_data, dict) else RunSettings()
    executor = RunExecutor(
        db=db,
        run_id=f"export-{run.run_id}",
        source_env=run.source_env or "source",
        target_env=run.target_env or "target",
        job_sequence=[],
        run_settings=settings,
        config_snapshot=snapshot,
    )
    results_by_job = {result.query_name: result for result in run.results}
    # A job that errored found no differences, so running it now could only report
    # rows the run never did.
    errored_jobs = {result.query_name for result in run.results if result.status == "ERROR"}
    for result in run.results:
        if result.status == "ERROR":
            _write_job_error(result, writer)

    def needs_rebuild(job_name: str) -> bool:
        if job_name in errored_jobs:
            return False
        result = results_by_job.get(job_name)
        # The run found nothing in a job with no issues. Querying again costs time
        # on every source and can only add rows from data that drifted since.
        return result is None or bool(result.total_issues)

    to_rebuild = [job_name for job_name in job_names if needs_rebuild(job_name)]
    progress.set_jobs(to_rebuild)
    for job_name in to_rebuild:
        result = results_by_job.get(job_name)
        progress.start(job_name)
        rows_before = writer.row_count
        try:
            _rebuild_job(db, job_name, result, executor, settings, snapshot, writer)
        except Exception as exc:
            logger.warning("Could not rebuild job '%s' for an export", job_name, exc_info=True)
            db.rollback()
            _record_rebuild_failure(db, job_name, result, exc, writer.row_count - rows_before, writer, progress)
            continue
        progress.finish(job_name, writer.row_count - rows_before)


def _rebuild_job(
    db: Session,
    job_name: str,
    result: TestResult | None,
    executor: RunExecutor,
    settings: RunSettings,
    snapshot: dict[str, Any],
    writer: DifferenceWriter,
) -> None:
    saved = db.query(SavedJob).filter(SavedJob.name == job_name).first()
    if saved is None or saved.job_type not in ("compare", "reconciliation"):
        _write_stored_rows_for_unrebuilt_job(db, result, writer)
        return
    # The same values the run itself used, so the rebuild reads the same data.
    job = substitute_in_job(executor._job_to_definition(saved), snapshot.get("variables") or {})
    if job.job_type == "compare":
        if not _write_compare_job(db, job, writer, settings=settings):
            _write_stored_rows_for_unrebuilt_job(db, result, writer)
        return
    if job.params.get("source_mode") == "bo_live":
        _write_stored_rows_for_unrebuilt_job(db, result, writer)
        return
    if job.params.get("source_mode") == "multi_file":
        _write_multi_file_reconciliation_job(db, job, settings, writer, snapshot)
        return
    src_engine, tgt_engine = executor._build_engines(job)
    df_a = src_engine.execute_query(job.query, job.params)
    df_b = tgt_engine.execute_query(job.query, job.params)
    _write_tabular_differences(
        df_a,
        df_b,
        key_columns=job.key_columns or settings.key_columns,
        exclude_columns=job.exclude_columns or settings.exclude_columns,
        options=settings,
        test_name=job.name,
        writer=writer,
    )


def _record_rebuild_failure(
    db: Session,
    job_name: str,
    result: TestResult | None,
    exc: Exception,
    rows_written: int,
    writer: DifferenceWriter,
    progress: ExportProgress,
) -> None:
    """Keep what the failed job can still show. Rows it already wrote stand, as a
    later file pair can fail after earlier ones; adding its stored rows to them
    would list the same differences twice."""
    reason = _friendly_export_error(exc)[:REBUILD_ERROR_MAX_CHARS]
    if rows_written:
        rows = rows_written
        message = f"Could not finish rebuilding this job: {reason} Kept the {rows_written} rows written before it failed."
    else:
        rows = _write_stored_result_rows(db, result, writer) if result is not None else 0
        total = result.total_issues if result is not None else 0
        message = f"Could not rebuild this job: {reason} Showing {rows} of {total} differences stored by the run."
    _write_job_note(writer, job_name, EXPORT_INCOMPLETE_TYPE, message)
    progress.fail(job_name, reason, rows=rows)
```

- [ ] **Step 4: Run to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py tests/unit/test_difference_export.py tests/unit/test_full_html_report.py -q`
Expected: PASS

---

### Task 4: Run exports under a heartbeat and save the outcome

**Files:**
- Modify: `api/services/difference_export.py` (`run_difference_export_job`)
- Test: `tests/unit/test_difference_export_jobs.py`

- [ ] **Step 1: Switch the test fixture to a file database**

The heartbeat writes from another thread, and a `StaticPool` connection shared between threads would interleave its transactions. In the `_environment` fixture in `tests/unit/test_difference_export_jobs.py`, replace the `create_engine(...)` call with the one below, and drop the `StaticPool` import:

```python
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'repo.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
```

Change the end of the fixture from `yield` to:

```python
    yield
    engine.dispose()
```

- [ ] **Step 2: Write the failing tests**

```python
def _export_run(db, run_id: str) -> str:
    job, _ = de.create_or_reuse_export_job(db, run_id, "csv")
    return job.export_id


def _finished_export(export_id: str) -> DifferenceExportJob:
    with _db_module.SessionLocal() as db:
        return db.query(DifferenceExportJob).filter_by(export_id=export_id).one()


def test_finished_export_saves_its_progress_and_failed_jobs():
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "broken", *_differing_frames(1))
        _save_sql_job(db, "healthy", *_differing_frames(2))
        del _FrameDBEngine.frames["SELECT * FROM broken_a"]
        run_id = _run(db, ["broken", "healthy"])
        _add_result(db, run_id, "broken", issues=1)
        _add_result(db, run_id, "healthy", issues=2)
        export_id = _export_run(db, run_id)

    de.run_difference_export_job(export_id)

    job = _finished_export(export_id)
    assert job.status == "COMPLETED"
    assert job.metadata_json["failed_jobs"] == ["broken"]
    assert [entry["state"] for entry in job.metadata_json["progress"]["jobs"]] == ["failed", "done"]
    assert job.metadata_json["recomputed_at"]
    assert job.heartbeat_at is not None


def test_export_that_starts_gets_a_heartbeat_at_once(monkeypatch):
    seen = {}
    original = de.write_recomputed_differences

    def spy(db, run, fmt, path, progress=None):
        with _db_module.SessionLocal() as other:
            seen["heartbeat"] = other.query(DifferenceExportJob).one().heartbeat_at
        return original(db, run, fmt, path, progress=progress)

    monkeypatch.setattr(de, "write_recomputed_differences", spy)
    with _db_module.SessionLocal() as db:
        run_id = _run(db, [])
        export_id = _export_run(db, run_id)

    de.run_difference_export_job(export_id)

    assert seen["heartbeat"] is not None


def test_export_that_raises_is_failed_and_keeps_its_progress(monkeypatch):
    def fail(db, run, fmt, path, progress=None):
        progress.set_jobs(["a"])
        raise RuntimeError("disk full")

    monkeypatch.setattr(de, "write_recomputed_differences", fail)
    with _db_module.SessionLocal() as db:
        run_id = _run(db, [])
        export_id = _export_run(db, run_id)

    de.run_difference_export_job(export_id)

    job = _finished_export(export_id)
    assert job.status == "FAILED"
    assert job.error_message == "disk full"
    assert job.metadata_json["progress"]["jobs"][0]["name"] == "a"
```

- [ ] **Step 3: Run to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py -q -k "saves_its_progress or heartbeat_at_once or keeps_its_progress"`
Expected: FAIL, as `metadata_json` has no `failed_jobs` and `heartbeat_at` is `None`.

- [ ] **Step 4: Implement**

Replace `run_difference_export_job` with:

```python
def run_difference_export_job(export_id: str) -> None:
    db = SessionLocal()
    progress = ExportProgress()
    try:
        job = db.query(DifferenceExportJob).filter(DifferenceExportJob.export_id == export_id).first()
        if job is None or job.status == "COMPLETED":
            return
        job.status = "RUNNING"
        job.started_at = _utcnow()
        job.heartbeat_at = job.started_at
        job.error_message = None
        db.commit()

        run = db.query(TestRun).filter(TestRun.run_id == job.run_id).first()
        if run is None:
            raise RuntimeError("Run not found")

        path = export_dir(job.run_id) / export_filename(run, job.format, job.export_id)
        with ExportHeartbeat(export_id, progress, SessionLocal):
            if job.format == "html":
                row_count = write_full_html_report(db, run, path, progress=progress)
            else:
                row_count = write_recomputed_differences(db, run, job.format, path, progress=progress)
        job.status = "COMPLETED"
        job.artifact_path = str(path)
        job.row_count = row_count
        job.completed_at = _utcnow()
        job.recomputed_at = job.completed_at
        job.metadata_json = {
            "recomputed_at": job.recomputed_at.isoformat(),
            "warning": "Sources were rebuilt at export time; live/API data may have drifted since the original run.",
            "progress": progress.snapshot(),
            "failed_jobs": progress.failed_jobs,
        }
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(DifferenceExportJob).filter(DifferenceExportJob.export_id == export_id).first()
        if job is not None:
            job.status = "FAILED"
            job.error_message = _friendly_export_error(exc)
            job.completed_at = _utcnow()
            job.metadata_json = {"progress": progress.snapshot()}
            db.commit()
    finally:
        db.close()
```

- [ ] **Step 5: Run to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py tests/unit/test_difference_export.py -q`
Expected: PASS

---

### Task 5: Rebuilt notice in the HTML report

**Files:**
- Modify: `api/services/run_report.py` (`RunReportSnapshot`), `api/services/difference_export.py` (`write_full_html_report`), `etl_framework/reporting/templates/report.html.j2:237-240`
- Test: `tests/unit/test_difference_export_jobs.py`

- [ ] **Step 1: Write the failing test**

```python
def test_full_html_report_says_its_rows_were_rebuilt(tmp_path):
    with _db_module.SessionLocal() as db:
        _save_sql_job(db, "curves", *_differing_frames(1))
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=1)

        path = tmp_path / "report.html"
        de.write_full_html_report(db, _get_run(db), path)

    html = path.read_text(encoding="utf-8")
    assert 'id="rebuilt-notice"' in html
    assert "may differ from the original run" in html


def test_report_of_stored_rows_has_no_rebuilt_notice(tmp_path):
    from api.services.run_report import build_run_report_snapshot
    from etl_framework.reporting.generator import ReportGenerator

    with _db_module.SessionLocal() as db:
        run_id = _run(db, ["curves"])
        _add_result(db, run_id, "curves", issues=1, stored=1)
        snapshot = build_run_report_snapshot(_get_run(db), include_mismatches=True)
        path = ReportGenerator(output_dir=str(tmp_path)).generate(snapshot, filename="stored.html")

    assert 'id="rebuilt-notice"' not in open(path, encoding="utf-8").read()
```

- [ ] **Step 2: Run to verify the first fails**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py -q -k "rebuilt_notice or were_rebuilt"`
Expected: `test_full_html_report_says_its_rows_were_rebuilt` FAILS.

- [ ] **Step 3: Implement**

In `api/services/run_report.py`, in `RunReportSnapshot`, after `file_name_b: str | None = None`:

```python
    rebuilt_at: datetime | None = None
```

In `write_full_html_report`, directly after `snapshot = build_run_report_snapshot(run, include_mismatches=False)`:

```python
    snapshot.rebuilt_at = _utcnow()
```

In `report.html.j2`, between the `{% endif %}` of the Files line and `<hr>`:

```jinja
      {% if suite.rebuilt_at %}
      <p id="rebuilt-notice" style="background:#fef3c7;color:#92400e;padding:8px 12px;border-radius:6px"><strong>Rebuilt from live data</strong> at {{ suite.rebuilt_at | to_local }}; may differ from the original run.</p>
      {% endif %}
```

- [ ] **Step 4: Run to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py tests/unit/test_report_template.py -q`
Expected: PASS

---

### Task 6: One-off SQL compares

**Files:**
- Modify: `api/services/difference_export.py:574-599`
- Test: `tests/unit/test_difference_export_jobs.py`

- [ ] **Step 1: Write the failing tests**

```python
def _adhoc_sql_run(db, source: pd.DataFrame, target: pd.DataFrame, query_b: str = "SELECT * FROM b") -> str:
    _FrameDBEngine.frames["SELECT * FROM a"] = source
    _FrameDBEngine.frames[query_b] = target
    config_id = _config_id(db)
    RunRepository(db).create_run(
        run_id="run-1", source_env="dev", target_env="uat",
        config_snapshot={"compare_request_type": "sql", "request": {
            "config_id_a": config_id, "config_id_b": config_id,
            "query_a": "SELECT * FROM a", "query_b": query_b,
            "key_columns": ["id"],
        }},
    )
    return "run-1"


def test_adhoc_sql_compare_export_aligns_oracle_column_case(tmp_path):
    source = pd.DataFrame({"ID": [1], "AMOUNT": [1.0]})
    target = pd.DataFrame({"id": [1], "amount": [2.0]})
    with _db_module.SessionLocal() as db:
        _adhoc_sql_run(db, source, target)

        rows = _recompute(db, tmp_path)

    assert [row["mismatch_type"] for row in rows] == ["value_diff"]


def test_adhoc_sql_compare_export_refuses_a_query_that_is_not_read_only(tmp_path):
    with _db_module.SessionLocal() as db:
        _adhoc_sql_run(db, *_differing_frames(1), query_b="DELETE FROM b")

        with pytest.raises(ValueError, match="read-only"):
            _recompute(db, tmp_path)

    assert _FrameDBEngine.queries == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py -q -k "adhoc_sql"`
Expected: FAIL. The first raises a key column error, and the second raises no `ValueError`.

- [ ] **Step 3: Implement**

Replace `_write_sql_compare` with:

```python
def _write_sql_compare(db: Session, payload: dict[str, Any], writer: DifferenceWriter) -> None:
    req = SQLCompareRequest(**payload)
    df_a, df_b = CompareService(db, ConfigRepository(db))._load_sql_frames(req)
    _write_tabular_differences(
        df_a,
        df_b,
        key_columns=req.key_columns or [],
        exclude_columns=req.exclude_columns or [],
        options=req.advanced,
        test_name=req.label_a or "file_a",
        writer=writer,
    )
```

Remove the imports this leaves unused, which are `resolve_connection`, `DBEngine` and `_load_in_chunks`. Keep `CompareService`.

- [ ] **Step 4: Run to verify they pass**

Run: `C:\Python314\python.exe -m pytest tests/unit/test_difference_export_jobs.py tests/unit/test_full_differences_export.py tests/unit/test_compare_cores.py -q`
Expected: PASS

---

### Task 7: History page polling and progress panel

**Files:**
- Modify: `frontend/features/compare.js:212`, `:1368-1453`
- Modify: `frontend/partials/tab-history.html:84`
- Modify: `frontend/help-content.js`
- Regenerate: `frontend/index.html`
- Test: `tests/e2e/44-export-progress.spec.ts`

- [ ] **Step 1: Write the failing browser tests**

```typescript
import { test, expect } from './fixtures';
import { createFileJob, triggerRun, waitForTerminal, deleteJob, authedContext } from './api-helpers';

const NOW = '2026-11-04T10:00:00+00:00';

function job(name: string, state: string, extra: Record<string, unknown> = {}) {
  return { name, state, rows: 0, started_at: null, finished_at: null, error: null, ...extra };
}

const RUNNING_JOBS = [
  job('curves', 'done', { rows: 8, started_at: NOW, finished_at: '2026-11-04T10:00:12+00:00' }),
  job('rates', 'failed', { rows: 2, started_at: NOW, finished_at: NOW, error: 'ORA-12541: no listener' }),
  job('prices', 'running', { started_at: NOW }),
  job('volumes', 'waiting'),
];

function exportBody(status: string, metadata: Record<string, unknown>) {
  return {
    export_id: 'exp-1', run_id: 'run', format: 'csv', status, row_count: 0,
    error_message: null, artifact_path: null, created_at: NOW, started_at: NOW,
    completed_at: null, recomputed_at: null, metadata,
  };
}

test.describe('Difference export progress', () => {
  let jobName = '';
  let runId = '';

  test.beforeEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    jobName = `e2e-export-progress-${Date.now()}`;
    await createFileJob(ctx, jobName);
    ({ run_id: runId } = await triggerRun(ctx, [jobName]));
    await waitForTerminal(ctx, runId);
    await ctx.dispose();
  });

  test.afterEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    await deleteJob(ctx, jobName);
    await ctx.dispose();
  });

  async function mockExport(page, finishWhen: () => boolean) {
    let polls = 0;
    await page.route(`**/api/runs/${runId}/differences/download*`, (route) =>
      route.fulfill({ status: 202, json: { requires_export_job: true } }));
    await page.route(`**/api/runs/${runId}/exports`, (route) =>
      route.fulfill({ status: 202, json: exportBody('RUNNING', { progress: { jobs: RUNNING_JOBS } }) }));
    await page.route(`**/api/runs/${runId}/exports/exp-1`, (route) => {
      polls += 1;
      if (!finishWhen()) {
        return route.fulfill({ json: exportBody('RUNNING', { progress: { jobs: RUNNING_JOBS } }) });
      }
      return route.fulfill({
        json: exportBody('COMPLETED', {
          recomputed_at: NOW,
          failed_jobs: ['rates'],
          progress: { jobs: RUNNING_JOBS.map((entry) => (entry.state === 'failed' ? entry : { ...entry, state: 'done' })) },
        }),
      });
    });
    await page.route(`**/api/runs/${runId}/exports/exp-1/download`, (route) =>
      route.fulfill({ contentType: 'text/csv', body: 'test_name\n' }));
    return () => polls;
  }

  async function openRun(page) {
    await page.goto('/');
    await page.locator('[data-testid="nav-tab-history"]').click();
    await page.locator('[data-testid="history-subtab-runs"]').click();
    await page.locator(`[data-testid="history-run-row-${runId}"]`).click();
  }

  test('shows each job while running, then collapses to a summary with its gaps', async ({ authedPage }) => {
    let finished = false;
    await mockExport(authedPage, () => finished);
    await openRun(authedPage);

    await authedPage.getByRole('button', { name: /All differences CSV/ }).click();

    const panel = authedPage.locator('[data-testid="export-panel-csv"]');
    await expect(panel).toBeVisible();
    await expect(panel.locator('[data-testid="export-progress-bar"]')).toHaveAttribute('aria-valuenow', '50');
    const rows = panel.locator('[data-testid="export-job-row"]');
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText('8 rows');
    await expect(rows.nth(1)).toContainText('ORA-12541');
    await expect(rows.nth(2)).toContainText('running for');
    await expect(rows.nth(3)).toContainText('waiting');

    const downloadPromise = authedPage.waitForEvent('download');
    finished = true;
    await downloadPromise;

    await expect(rows.first()).toBeHidden();
    await expect(panel.locator('[data-testid="export-rebuilt-notice"]')).toContainText('may differ from the original run');
    await expect(panel.locator('[data-testid="export-gap-warning"]')).toContainText('1 job');
  });

  test('keeps waiting past the old 8 minute limit', async ({ authedPage }) => {
    test.setTimeout(180_000);
    await authedPage.clock.install();
    let finished = false;
    const polls = await mockExport(authedPage, () => finished);
    await openRun(authedPage);

    await authedPage.getByRole('button', { name: /All differences CSV/ }).click();

    for (let done = 1; done <= 245; done += 1) {
      await expect.poll(polls).toBeGreaterThanOrEqual(done);
      await authedPage.clock.fastForward(2000);
    }
    await expect(authedPage.locator('[data-testid="export-panel-csv"]')).toBeVisible();
    await expect(authedPage.getByText('Export job timed out')).toHaveCount(0);

    const downloadPromise = authedPage.waitForEvent('download');
    finished = true;
    await authedPage.clock.fastForward(2000);
    await downloadPromise;
  });

  test('a brief API outage while polling is not reported as a failure', async ({ authedPage }) => {
    let outage = 3;
    await mockExport(authedPage, () => true);
    await authedPage.route(`**/api/runs/${runId}/exports/exp-1`, (route) => {
      if (outage > 0) {
        outage -= 1;
        return route.abort();
      }
      return route.fallback();
    });
    await openRun(authedPage);

    const downloadPromise = authedPage.waitForEvent('download');
    await authedPage.getByRole('button', { name: /All differences CSV/ }).click();
    await downloadPromise;

    await expect(authedPage.getByText('Full export failed')).toHaveCount(0);
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `node node_modules/@playwright/test/cli.js test tests/e2e/44-export-progress.spec.ts --reporter=line`
Expected: FAIL. `export-panel-csv` is not found.

- [ ] **Step 3: Add the page logic**

In `frontend/features/compare.js`, next to `differenceExports: {},` at line 212:

```javascript
    differenceExports: {},
    exportClock: Date.now(),
```

In the `catch` of `downloadAllDifferences` and of `downloadFullHtmlReport`, keep what the panel already shows by replacing the state assignment:

```javascript
        this.differenceExports = { ...this.differenceExports, [key]: { ...this.differenceExports[key], status: 'FAILED', error_message: e.message } };
```

Replace `pollDifferenceExport` with the block below, keeping the two-line fallback comment where it is, and add the helper methods after it:

```javascript
    async pollDifferenceExport(runId, exportId, format) {
      const key = this.differenceExportKey(runId, format);
      // An export ends in COMPLETED or FAILED, and the server fails one nothing is
      // working on any more, so this loop needs no deadline of its own. A few failed
      // polls in a row are tolerated so a brief API restart does not read as failure.
      const maxFailedPolls = 5;
      const ticker = setInterval(() => { this.exportClock = Date.now(); }, 1000);
      let failedPolls = 0;
      try {
        while (true) {
          let status;
          try {
            status = await api('GET', `/api/runs/${runId}/exports/${exportId}`);
            failedPolls = 0;
          } catch (e) {
            failedPolls += 1;
            if (failedPolls >= maxFailedPolls) throw e;
            await new Promise(resolve => setTimeout(resolve, 2000));
            continue;
          }
          this.differenceExports = { ...this.differenceExports, [key]: status };
          if (status.status === 'COMPLETED') {
            const { blob, disposition } = await apiBlob(`/api/runs/${runId}/exports/${exportId}/download`);
            const ext = format === 'parquet' ? 'parquet' : format === 'html' ? 'html' : 'csv';
            // Rarely hit -- the server always sets Content-Disposition now (see
            // export_filename in api/services/difference_export.py). This fallback
            // can't compute the full report-name convention client-side (it doesn't
            // have the run's config_snapshot/started_at loaded here), so it just
            // avoids the old, now-inconsistent "all_differences_" prefix.
            const fallback = `report_${runId}_${exportId}.${ext}`;
            const filename = disposition.match(/filename="?([^"]+)"?/)?.[1] || fallback;
            triggerDownload(blob, filename);
            this.differenceExports = { ...this.differenceExports, [key]: { ...status, status: 'DOWNLOADED' } };
            return;
          }
          if (status.status === 'FAILED') {
            throw new Error(status.error_message || 'Export job failed');
          }
          await new Promise(resolve => setTimeout(resolve, 2000));
        }
      } finally {
        clearInterval(ticker);
      }
    },

    exportPanels(runId) {
      return ['csv', 'parquet', 'html']
        .map(format => ({ format, state: this.differenceExportState(runId, format) }))
        .filter(panel => panel.state.export_id);
    },

    exportJobs(state) {
      return state.metadata?.progress?.jobs || [];
    },

    exportFinishedCount(state) {
      return this.exportJobs(state).filter(job => job.state === 'done' || job.state === 'failed').length;
    },

    exportPercent(state) {
      const total = this.exportJobs(state).length;
      return total ? Math.round((this.exportFinishedCount(state) / total) * 100) : 0;
    },

    exportPanelTitle(panel) {
      const name = panel.format === 'html' ? 'Full HTML report' : `All differences ${panel.format.toUpperCase()}`;
      const total = this.exportJobs(panel.state).length;
      if (this.isDifferenceExportBusy(panel.state.run_id, panel.format)) {
        if (total) return `${name}: rebuilt ${this.exportFinishedCount(panel.state)} of ${total} jobs`;
        const started = panel.state.started_at ? Date.parse(panel.state.started_at) : this.exportClock;
        return `${name}: rebuilding for ${this.exportDuration(this.exportClock - started)}`;
      }
      if (panel.state.status === 'FAILED') return `${name}: failed`;
      return `${name}: finished`;
    },

    exportJobStatus(job) {
      if (job.state === 'waiting') return 'waiting';
      if (job.state === 'running') {
        return `running for ${this.exportDuration(this.exportClock - Date.parse(job.started_at))}`;
      }
      const took = this.exportDuration(Date.parse(job.finished_at) - Date.parse(job.started_at));
      const rows = `${job.rows} row${job.rows === 1 ? '' : 's'}`;
      if (job.state === 'failed') return `Could not rebuild: ${job.error}. ${rows} exported, ${took}`;
      return `${rows}, ${took}`;
    },

    exportJobIcon(job) {
      return { waiting: '·', running: '⟳', done: '✓', failed: '✗' }[job.state] || '';
    },

    exportDuration(milliseconds) {
      const seconds = Math.max(0, Math.floor((milliseconds || 0) / 1000));
      if (seconds < 60) return `${seconds}s`;
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
      return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    },

    exportRebuiltAt(state) {
      return state.metadata?.recomputed_at || null;
    },

    exportFailedJobs(state) {
      return state.metadata?.failed_jobs || [];
    },
```

- [ ] **Step 4: Add the panel**

In `frontend/partials/tab-history.html`, directly after the `</div>` closing the button row at line 84:

```html
          <template x-for="panel in exportPanels(selectedRun.run_id)" :key="panel.format">
            <div class="pt-2 border-t border-slate-100 space-y-1" :data-testid="'export-panel-' + panel.format">
              <div class="text-xs font-semibold" x-text="exportPanelTitle(panel)"></div>
              <div x-show="exportJobs(panel.state).length" class="h-1.5 rounded bg-slate-200 overflow-hidden"
                   role="progressbar" aria-valuemin="0" aria-valuemax="100"
                   :aria-valuenow="exportPercent(panel.state)" aria-label="Export progress"
                   data-testid="export-progress-bar">
                <div class="h-full bg-indigo-500 transition-all" :style="'width:' + exportPercent(panel.state) + '%'"></div>
              </div>
              <ul x-show="isDifferenceExportBusy(selectedRun.run_id, panel.format)" class="text-xs space-y-0.5 max-h-48 overflow-y-auto">
                <template x-for="(job, index) in exportJobs(panel.state)" :key="index">
                  <li class="flex gap-2" data-testid="export-job-row">
                    <span aria-hidden="true" x-text="exportJobIcon(job)"></span>
                    <span class="font-mono break-all" x-text="job.name"></span>
                    <span class="text-muted ml-auto text-right" x-text="exportJobStatus(job)"></span>
                  </li>
                </template>
              </ul>
              <p x-show="panel.state.status === 'FAILED'" class="text-xs text-rose-600" x-text="panel.state.error_message"></p>
              <p x-show="exportRebuiltAt(panel.state) && panel.state.status !== 'FAILED'" class="text-xs text-amber-600"
                 data-testid="export-rebuilt-notice"
                 x-text="'Rebuilt from live data at ' + fmtDate(exportRebuiltAt(panel.state)) + '; may differ from the original run.'"></p>
              <p x-show="exportFailedJobs(panel.state).length" class="text-xs text-rose-600"
                 data-testid="export-gap-warning"
                 x-text="exportFailedJobs(panel.state).length + (exportFailedJobs(panel.state).length === 1 ? ' job' : ' jobs') + ' could not be rebuilt and show only stored rows. Click the export again to retry.'"></p>
            </div>
          </template>
```

`isDifferenceExportBusy` is false for `DOWNLOADED`, so the list collapses when the export finishes. `exportPanelTitle` passes `panel.state.run_id`, which the status response includes.

- [ ] **Step 5: Regenerate the page and run the browser tests**

Run: `npm run build:html`
Run: `node node_modules/@playwright/test/cli.js test tests/e2e/44-export-progress.spec.ts tests/e2e/04-history.spec.ts tests/e2e/06-reports.spec.ts --reporter=line`
Expected: PASS

- [ ] **Step 6: Update the in-app help**

Find the entry for the History export buttons in `frontend/help-content.js`, and add a sentence in its existing format:

```text
Exports rebuild from live data, skip jobs that passed, and show each job's progress while they run. A job that cannot be rebuilt shows its stored rows with an export_incomplete row, and clicking the export again retries it.
```

Run: `node node_modules/@playwright/test/cli.js test tests/e2e/11-help.spec.ts --reporter=line`
Expected: PASS

---

### Task 8: Final verification

- [ ] **Step 1: Run the whole unit suite**

Run: `C:\Python314\python.exe -m pytest tests/unit -q -W ignore`
Expected: all pass, with the 2 existing skips.

- [ ] **Step 2: Check the committed page matches its partials**

Run: `npm run build:html` and then `git status --short frontend/index.html`
Expected: `frontend/index.html` shows only the changes from this work.

- [ ] **Step 3: Commit, once the user approves**

```bash
git add api etl_framework frontend tests docs/superpowers/plans/2026-11-04-robust-difference-export-rebuild.md
git commit -F <message file>
```

Leave `uv.lock` out. On Windows PowerShell, use `git commit -F` rather than `-m`, because double quotes in `-m` become pathspecs.
