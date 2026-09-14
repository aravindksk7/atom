from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.schemas import SequenceStepRef
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository.database import Base
from etl_framework.repository.models import MismatchDetail, TestResult
from etl_framework.repository.repository import RunRepository, RunStepRepository
from etl_framework.runner.state import TestStatus


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_failed_run(db):
    repo = RunRepository(db)
    repo.create_run("original", "dev", "prod", {"run_settings": {}})
    repo.update_run_status("original", "BLOCKED")
    steps = [
        SequenceStepRef(step_id="extract", job_name="extract"),
        SequenceStepRef(step_id="load", job_name="load", depends_on=["extract"]),
    ]
    step_repo = RunStepRepository(db)
    step_repo.materialize_steps("original", steps)
    step_repo.update_status("original", 0, "PASSED")
    step_repo.update_status("original", 1, "FAILED")
    result = repo.add_test_result("original", ReconciliationResult(
        query_name="extract",
        source_env="dev",
        target_env="prod",
        source_row_count=10,
        target_row_count=10,
        matched_count=10,
        missing_in_target_count=0,
        missing_in_source_count=0,
        value_mismatch_count=0,
        mismatches=[],
        status=TestStatus.PASSED,
        executed_at=datetime(2026, 9, 14, tzinfo=timezone.utc),
        duration_seconds=1.25,
    ))
    repo.add_mismatch_details(result.id, [
        MismatchRecord({"id": 1}, "amount", "10", "11", "value_diff")
    ])


def test_restart_creates_new_run_with_carried_results_and_seeded_outcomes(db):
    from api.services.run_restart import restart_run

    _make_failed_run(db)
    plan = restart_run(db, "original")

    new_run = RunRepository(db).get_run(plan.run_id)
    assert new_run.restarted_from_run_id == "original"
    steps = RunStepRepository(db).list_steps(plan.run_id)
    assert [step.status for step in steps] == ["PASSED", "PENDING"]
    assert [step.carried_over for step in steps] == [True, False]
    copied = db.query(TestResult).filter_by(run_id=plan.run_id, query_name="extract").one()
    assert copied.duration_seconds == 1.25
    assert db.query(MismatchDetail).filter_by(test_result_id=copied.id).count() == 1
    assert set(plan.seeded_outcomes) == {"extract"}
    assert plan.carried_over_states[0].name == "extract"


def test_restart_refuses_running_run(db):
    from api.services.run_restart import RestartError, restart_run

    RunRepository(db).create_run("running", "dev", "prod")

    with pytest.raises(RestartError, match="still in progress"):
        restart_run(db, "running")


def test_restart_refuses_fully_successful_run(db):
    from api.services.run_restart import RestartError, restart_run

    RunRepository(db).create_run("passed", "dev", "prod")
    RunRepository(db).update_run_status("passed", "PASSED")
    RunStepRepository(db).materialize_steps("passed", [SequenceStepRef(step_id="a", job_name="a")])
    RunStepRepository(db).update_status("passed", 0, "PASSED")

    with pytest.raises(RestartError, match="nothing to restart"):
        restart_run(db, "passed")


def test_restart_missing_run_is_not_found(db):
    from api.services.run_restart import RestartNotFound, restart_run

    with pytest.raises(RestartNotFound):
        restart_run(db, "missing")
