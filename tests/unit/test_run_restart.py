from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, null
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import SequenceStepRef
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository.database import Base
from etl_framework.repository.models import MismatchDetail, RunStep, TestResult
from etl_framework.repository.repository import JobRepository, RunRepository, RunStepRepository
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


def _make_recorded_run(db, rows):
    """A finished run whose rows are written directly, as a migrated database holds them."""
    RunRepository(db).create_run("old", "dev", "prod", {"run_settings": {"metrics_enabled": False}})
    RunRepository(db).update_run_status("old", "BLOCKED")
    for index, (step_id, job_name, status, depends_on) in enumerate(rows):
        db.add(RunStep(
            run_id="old", job_name=job_name, step_index=index, status=status,
            step_id=step_id, depends_on=depends_on,
        ))
    db.commit()


def _make_job(db, name, depends_on=()):
    JobRepository(db).create({
        "name": name, "description": name, "tags": [],
        "job_type": "reconciliation", "query": f"SELECT * FROM {name}",
        "key_columns": ["id"], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {
            "source_rows": [{"id": 1}], "target_rows": [{"id": 1}],
            "depends_on": list(depends_on),
        },
        "enabled": True,
    })


def _execute(db, plan):
    from api.services.run_executor import RunExecutor

    RunExecutor(
        db=db, run_id=plan.run_id, source_env="dev", target_env="prod",
        job_sequence=plan.steps, run_settings=plan.run_settings,
        config_snapshot=plan.config_snapshot,
        carried_over_states=plan.carried_over_states,
        seeded_outcomes=plan.seeded_outcomes,
    ).execute()
    db.expire_all()


@pytest.fixture
def shared_db():
    """For tests that execute a run: jobs run on their own thread, and the default
    in-memory pool would give that thread a separate, empty database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_restart_restores_the_chain_for_rows_without_edges(shared_db):
    from api.services.run_restart import restart_run

    db = shared_db
    _make_job(db, "ja")
    _make_job(db, "jb", depends_on=["ja"])
    # null() writes SQL NULL, which is what the column holds after the migration.
    _make_recorded_run(db, [
        ("step_a", "ja", "PASSED", null()),
        ("step_b", "jb", "FAILED", null()),
    ])

    plan = restart_run(db, "old")

    _execute(db, plan)
    run = RunRepository(db).get_run(plan.run_id)
    messages = [r.error_message or "" for r in run.results]
    assert not any("does not run after" in m for m in messages), messages
    assert run.status == "PASSED"
    assert [(s.step_id, s.depends_on) for s in plan.steps] == [
        ("step_a", []), ("step_b", ["step_a"]),
    ]


def test_restart_chains_to_the_generated_id_when_step_id_is_also_missing(db):
    from api.services.run_restart import restart_run

    _make_recorded_run(db, [
        (None, "ja", "PASSED", null()),
        (None, "jb", "FAILED", null()),
    ])

    plan = restart_run(db, "old")
    assert [(s.step_id, s.depends_on) for s in plan.steps] == [
        ("step-0", []), ("step-1", ["step-0"]),
    ]


def test_restart_seeds_rows_without_a_step_id_by_their_generated_id(shared_db, monkeypatch):
    from api.services.run_executor import RunExecutor
    from api.services.run_restart import restart_run

    db = shared_db
    _make_job(db, "ja")
    _make_job(db, "jb", depends_on=["ja"])
    _make_recorded_run(db, [
        (None, "ja", "PASSED", null()),
        (None, "jb", "FAILED", null()),
    ])

    plan = restart_run(db, "old")

    built = []
    real_build_case = RunExecutor._build_case

    def recording_build_case(self, job):
        built.append(job.name)
        return real_build_case(self, job)

    monkeypatch.setattr(RunExecutor, "_build_case", recording_build_case)
    _execute(db, plan)

    assert built == ["jb"]
    assert RunRepository(db).get_run(plan.run_id).status == "PASSED"
    assert set(plan.seeded_outcomes) == {"step-0"}


def test_restart_of_an_old_chain_runs_the_step_after_a_slow_one(shared_db):
    from api.services.run_restart import restart_run

    db = shared_db
    _make_job(db, "ja")
    _make_job(db, "jb")
    _make_recorded_run(db, [
        ("step_a", "ja", "SLOW", null()),
        ("step_b", "jb", "FAILED", null()),
    ])

    plan = restart_run(db, "old")
    _execute(db, plan)

    assert RunStepRepository(db).get_step_by_step_id(plan.run_id, "step_b").status == "PASSED"


def test_restart_of_an_old_chain_keeps_the_rule_of_a_step_with_a_condition(db):
    from api.services.run_restart import restart_run

    RunRepository(db).create_run("old", "dev", "prod", {"run_settings": {}})
    RunRepository(db).update_run_status("old", "BLOCKED")
    db.add(RunStep(run_id="old", job_name="ja", step_index=0, status="PASSED", step_id="a", depends_on=null()))
    db.add(RunStep(
        run_id="old", job_name="jb", step_index=1, status="FAILED", step_id="b", depends_on=null(),
        condition={"require_status": ["PASSED"]},
    ))
    db.commit()

    plan = restart_run(db, "old")
    assert plan.steps[1].trigger_rule == "all_success"


def test_restart_keeps_explicit_empty_edges_as_roots(db):
    from api.services.run_restart import restart_run

    _make_recorded_run(db, [
        ("step_a", "ja", "PASSED", []),
        ("step_b", "jb", "FAILED", []),
    ])

    plan = restart_run(db, "old")
    assert [(s.step_id, s.depends_on) for s in plan.steps] == [
        ("step_a", []), ("step_b", []),
    ]


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


def test_rerun_downstream_follows_the_restored_chain_of_an_old_run(db):
    from api.services.run_restart import restart_run

    _make_recorded_run(db, [
        (None, "ja", "PASSED", null()),
        (None, "jb", "FAILED", null()),
        (None, "jc", "PASSED", null()),
    ])

    assert set(restart_run(db, "old").seeded_outcomes) == {"step-0", "step-2"}
    assert set(restart_run(db, "old", rerun_downstream=True).seeded_outcomes) == {"step-0"}


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
