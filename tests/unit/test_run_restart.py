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


def _add_result(db, run_id, job_name, status):
    return RunRepository(db).add_test_result(run_id, ReconciliationResult(
        query_name=job_name,
        source_env="dev",
        target_env="prod",
        source_row_count=0,
        target_row_count=0,
        matched_count=0,
        missing_in_target_count=0,
        missing_in_source_count=0,
        value_mismatch_count=0,
        mismatches=[],
        status=status,
        executed_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        duration_seconds=0.5,
    ))


def _make_timed_out_sequence(db):
    """extract PASSED, ds_load ERROR (poll read timeout), publish BLOCKED."""
    repo = RunRepository(db)
    repo.create_run("seq", "dev", "prod", {"run_settings": {}})
    repo.update_run_status("seq", "BLOCKED")
    step_repo = RunStepRepository(db)
    step_repo.materialize_steps("seq", [
        SequenceStepRef(step_id="extract", job_name="extract"),
        SequenceStepRef(step_id="ds_load", job_name="ds_load", depends_on=["extract"]),
        SequenceStepRef(step_id="publish", job_name="publish", depends_on=["ds_load"]),
    ])
    step_repo.update_status("seq", 0, "PASSED")
    step_repo.update_status("seq", 1, "ERROR")
    step_repo.update_status("seq", 2, "BLOCKED")
    _add_result(db, "seq", "extract", TestStatus.PASSED)
    _add_result(db, "seq", "ds_load", TestStatus.ERROR)


def test_mark_passed_approves_step_and_overrides_its_result(db):
    from api.services.run_restart import mark_step_passed_and_restart

    _make_timed_out_sequence(db)
    mark_step_passed_and_restart(db, "seq", "ds_load", "passed in DS console", "alice")

    step = RunStepRepository(db).get_step_by_step_id("seq", "ds_load")
    assert step.status == "APPROVED"
    assert step.release_action == "mark_passed"
    assert step.release_note == "passed in DS console"
    assert step.released_by == "alice"
    assert step.released_at is not None
    result = db.query(TestResult).filter_by(run_id="seq", query_name="ds_load").one()
    assert result.override_status == "PASSED"
    assert result.override_reason == "passed in DS console"
    assert result.override_by == "alice"


def test_mark_passed_restart_carries_step_and_reruns_only_downstream(db):
    from api.services.run_restart import mark_step_passed_and_restart

    _make_timed_out_sequence(db)
    plan = mark_step_passed_and_restart(db, "seq", "ds_load", "ok", "alice")

    assert plan.restarted_from_run_id == "seq"
    steps = RunStepRepository(db).list_steps(plan.run_id)
    assert [s.status for s in steps] == ["PASSED", "APPROVED", "PENDING"]
    assert [s.carried_over for s in steps] == [True, True, False]
    assert plan.seeded_outcomes["ds_load"].status == "PASSED"
    copied = db.query(TestResult).filter_by(run_id=plan.run_id, query_name="ds_load").one()
    assert copied.override_status == "PASSED"


def test_mark_passed_restart_executes_downstream_but_not_marked_step(db):
    from api.services.dag_executor import DagExecutor, StepOutcome
    from api.services.run_restart import mark_step_passed_and_restart

    _make_timed_out_sequence(db)
    plan = mark_step_passed_and_restart(db, "seq", "ds_load", "ok", "alice")

    executed = []

    class _Repo:
        def set_status(self, step_id, status, **kw):
            pass

        def get_release(self, step_id):
            return None

    def run_step(step):
        executed.append(step.step_id)
        return StepOutcome(status="PASSED")

    DagExecutor(
        steps=plan.steps, step_repo=_Repo(), run_step=run_step,
        is_cancel_requested=lambda: False, max_workers=1,
        sleep=lambda s: None, clock=lambda: 0.0, seeded=plan.seeded_outcomes,
    ).run()
    assert executed == ["publish"]


@pytest.mark.parametrize("status", ["PASSED", "BLOCKED", "PENDING"])
def test_mark_passed_rejects_steps_that_did_not_fail(db, status):
    from api.services.run_restart import StepNotFailed, mark_step_passed_and_restart

    _make_timed_out_sequence(db)
    RunStepRepository(db).set_status_by_step_id("seq", "ds_load", status)
    with pytest.raises(StepNotFailed):
        mark_step_passed_and_restart(db, "seq", "ds_load", "ok", "alice")


def test_mark_passed_rejects_run_in_progress(db):
    from api.services.run_restart import RunInProgress, mark_step_passed_and_restart

    _make_timed_out_sequence(db)
    RunRepository(db).update_run_status("seq", "RUNNING")
    with pytest.raises(RunInProgress):
        mark_step_passed_and_restart(db, "seq", "ds_load", "ok", "alice")
    assert RunStepRepository(db).get_step_by_step_id("seq", "ds_load").status == "ERROR"


def test_mark_passed_unknown_step_is_not_found(db):
    from api.services.run_restart import RestartNotFound, mark_step_passed_and_restart

    _make_timed_out_sequence(db)
    with pytest.raises(RestartNotFound):
        mark_step_passed_and_restart(db, "seq", "ghost", "ok", "alice")


def test_mark_passed_picks_result_by_occurrence_for_repeated_job(db):
    from api.services.run_restart import mark_step_passed_and_restart

    repo = RunRepository(db)
    repo.create_run("rep", "dev", "prod", {"run_settings": {}})
    repo.update_run_status("rep", "BLOCKED")
    step_repo = RunStepRepository(db)
    step_repo.materialize_steps("rep", [
        SequenceStepRef(step_id="first", job_name="ds_load"),
        SequenceStepRef(step_id="second", job_name="ds_load", depends_on=["first"]),
        SequenceStepRef(step_id="publish", job_name="publish", depends_on=["second"]),
    ])
    step_repo.update_status("rep", 0, "PASSED")
    step_repo.update_status("rep", 1, "ERROR")
    step_repo.update_status("rep", 2, "BLOCKED")
    first = _add_result(db, "rep", "ds_load", TestStatus.PASSED)
    second = _add_result(db, "rep", "ds_load", TestStatus.ERROR)

    mark_step_passed_and_restart(db, "rep", "second", "ok", "alice")

    db.refresh(first)
    db.refresh(second)
    assert first.override_status is None
    assert second.override_status == "PASSED"


def test_mark_passed_on_last_failed_step_marks_without_restarting(db):
    from api.services.run_restart import mark_step_passed_and_restart

    _make_timed_out_sequence(db)
    RunStepRepository(db).set_status_by_step_id("seq", "publish", "PASSED")
    plan = mark_step_passed_and_restart(db, "seq", "ds_load", "ok", "alice")

    assert plan is None
    assert RunStepRepository(db).get_step_by_step_id("seq", "ds_load").status == "APPROVED"
    assert RunRepository(db).find_restart_for_run("seq") is None
