import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from api.services.batch_launch import batch_out, run_batch
from etl_framework.repository.repository import RunBatchRepository, RunRepository
@pytest.fixture
def db_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _batch(db, **overrides):
    data = {
        "batch_id": "batch-1",
        "target_type": "sequence",
        "target_id": 1,
        "variable_name": "business_date",
        "start_value": "2026-09-11",
        "iterations": 3,
        "step_days": 1,
        "weekend_policy": "skip",
        "stop_on_failure": False,
    }
    data.update(overrides)
    return RunBatchRepository(db).create(**data)


def test_batch_out_reports_launched_and_pending_iterations(db_session_factory):
    with db_session_factory() as db:
        batch = _batch(db)
        for index, status in enumerate(("PASSED", "RUNNING"), start=1):
            run_id = f"run-{index}"
            RunRepository(db).create_run(run_id, "dev", "qa", run_batch_id="batch-1")
            RunRepository(db).update_run_status(run_id, status)

        result = batch_out(db, batch)

    assert len(result.runs) == 3
    assert [member.iteration_index for member in result.runs] == [1, 2, 3]
    assert [member.business_date for member in result.runs] == [
        "2026-09-11", "2026-09-14", "2026-09-15",
    ]
    assert [member.run_id for member in result.runs] == ["run-1", "run-2", None]
    assert [member.status for member in result.runs] == ["PASSED", "RUNNING", "PENDING"]


def test_batch_out_reports_pending_iterations_after_stopping_early(db_session_factory):
    with db_session_factory() as db:
        batch = _batch(db, iterations=4, stop_on_failure=True)
        RunRepository(db).create_run("run-failed", "dev", "qa", run_batch_id="batch-1")
        RunRepository(db).update_run_status("run-failed", "FAILED", failed=1)
        batch = RunBatchRepository(db).complete("batch-1", "STOPPED")

        result = batch_out(db, batch)

    assert len(result.runs) == 4
    assert [member.iteration_index for member in result.runs] == [1, 2, 3, 4]
    assert [member.business_date for member in result.runs] == [
        "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16",
    ]
    assert result.runs[0].run_id == "run-failed"
    assert result.runs[0].status == "FAILED"
    assert all(member.run_id is None for member in result.runs[1:])
    assert all(member.status == "PENDING" for member in result.runs[1:])


def test_run_batch_launches_iterations_in_date_order(db_session_factory):
    with db_session_factory() as db:
        _batch(db)
    launched = []

    def launch(db, target_id, overrides):
        run_id = f"run-{len(launched) + 1}"
        launched.append((target_id, overrides.copy(), run_id))
        RunRepository(db).create_run(run_id, "dev", "qa", run_batch_id="batch-1")
        RunRepository(db).update_run_status(run_id, "PASSED")
        return run_id

    run_batch("batch-1", db_session_factory, launch)

    assert [item[1]["business_date"] for item in launched] == [
        "2026-09-11", "2026-09-14", "2026-09-15",
    ]
    with db_session_factory() as db:
        batch = RunBatchRepository(db).get("batch-1")
        assert batch.status == "COMPLETED"
        assert batch.completed == 3


def test_run_batch_stops_on_failure_when_configured(db_session_factory):
    with db_session_factory() as db:
        _batch(db, stop_on_failure=True)
    statuses = ["FAILED", "PASSED", "PASSED"]

    def launch(db, target_id, overrides):
        run_id = f"run-{len(statuses)}"
        status = statuses.pop(0)
        RunRepository(db).create_run(run_id, "dev", "qa", run_batch_id="batch-1")
        RunRepository(db).update_run_status(run_id, status, failed=1 if status == "FAILED" else 0)
        return run_id

    run_batch("batch-1", db_session_factory, launch)

    with db_session_factory() as db:
        batch = RunBatchRepository(db).get("batch-1")
        assert batch.status == "STOPPED"
        assert batch.completed == 1
        assert len(RunBatchRepository(db).member_runs("batch-1")) == 1


def test_run_batch_honors_cancel_before_next_iteration(db_session_factory):
    with db_session_factory() as db:
        _batch(db)
    launched = []

    def launch(db, target_id, overrides):
        run_id = f"run-{len(launched) + 1}"
        launched.append(run_id)
        RunRepository(db).create_run(run_id, "dev", "qa", run_batch_id="batch-1")
        RunRepository(db).update_run_status(run_id, "PASSED")
        if len(launched) == 1:
            RunBatchRepository(db).stop("batch-1")
        return run_id

    run_batch("batch-1", db_session_factory, launch)

    assert launched == ["run-1"]
    with db_session_factory() as db:
        batch = RunBatchRepository(db).get("batch-1")
        assert batch.status == "STOPPED"
        assert batch.completed == 1


def test_run_batch_stamps_member_run_when_launcher_does_not(db_session_factory):
    with db_session_factory() as db:
        _batch(db, iterations=1)

    def launch(db, target_id, overrides):
        RunRepository(db).create_run("run-unstamped", "dev", "qa")
        RunRepository(db).update_run_status("run-unstamped", "PASSED")
        return "run-unstamped"

    run_batch("batch-1", db_session_factory, launch)

    with db_session_factory() as db:
        run = RunRepository(db).get_run("run-unstamped")
        assert run.run_batch_id == "batch-1"
