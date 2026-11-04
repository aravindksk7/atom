"""Per-job export progress and the heartbeat that publishes it."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from api.services.export_progress import ExportHeartbeat, ExportProgress
from etl_framework.repository.database import (
    Base,
    _ensure_export_job_columns,
    _export_heartbeat_column_ddl,
)
from etl_framework.repository.models import DifferenceExportJob
from etl_framework.repository.repository import RunRepository


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
        RunRepository(db).create_run(run_id="run-1", source_env="dev", target_env="uat", config_snapshot={})
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


def _old_export_table_engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE difference_export_jobs (id INTEGER PRIMARY KEY, export_id VARCHAR(36))"))
    return engine


def test_existing_export_table_gains_the_heartbeat_column_and_upgrading_twice_is_safe(tmp_path):
    engine = _old_export_table_engine(tmp_path)

    _ensure_export_job_columns(engine)
    _ensure_export_job_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("difference_export_jobs")}
    engine.dispose()
    assert "heartbeat_at" in columns


def test_heartbeat_column_is_added_with_a_type_postgres_has():
    ddl = _export_heartbeat_column_ddl(postgresql.dialect())
    assert ddl == "ALTER TABLE difference_export_jobs ADD COLUMN heartbeat_at TIMESTAMP WITH TIME ZONE"
