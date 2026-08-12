"""Golden tests locking in the linear executor's observable behaviour.

Written BEFORE the DAG rewrite and re-run unmodified after it. If a test in
this file needs editing to pass, the rewrite changed behaviour -- stop and
work out whether that change was intended.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings, SequenceStep, StepCondition
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository,
    RunRepository,
    RunStepRepository,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _job(db, name, *, matching=True):
    """A reconciliation job that passes (matching) or fails (mismatched)."""
    JobRepository(db).create({
        "name": name, "description": "", "tags": [],
        "job_type": "reconciliation", "query": f"SELECT * FROM {name}",
        "key_columns": ["id"], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {
            "source_rows": [{"id": 1, "amount": 10.0}],
            "target_rows": [{"id": 1, "amount": 10.0 if matching else 9.0}],
        },
        "enabled": True,
    })


def _execute(db, run_id, steps):
    RunExecutor(
        db=db, run_id=run_id, source_env="dev", target_env="prod",
        job_sequence=steps, run_settings=RunSettings(metrics_enabled=False),
    ).execute()


def _step_statuses(db, run_id):
    return [(s.step_index, s.job_name, s.status) for s in RunStepRepository(db).list_steps(run_id)]


def test_passing_chain_marks_every_step_passed():
    db = _session()
    RunRepository(db).create_run("c-1", "dev", "prod", {})
    _job(db, "a")
    _job(db, "b")
    _execute(db, "c-1", ["a", "b"])

    assert _step_statuses(db, "c-1") == [(0, "a", "PASSED"), (1, "b", "PASSED")]
    run = RunRepository(db).get_run("c-1")
    assert (run.status, run.total_tests, run.passed, run.failed) == ("PASSED", 2, 2, 0)


def test_steps_run_in_declared_order():
    db = _session()
    RunRepository(db).create_run("c-2", "dev", "prod", {})
    _job(db, "first")
    _job(db, "second")
    _execute(db, "c-2", ["second", "first"])

    assert [s[1] for s in _step_statuses(db, "c-2")] == ["second", "first"]


def test_failing_step_does_not_stop_an_ungated_chain():
    db = _session()
    RunRepository(db).create_run("c-3", "dev", "prod", {})
    _job(db, "bad", matching=False)
    _job(db, "good")
    _execute(db, "c-3", ["bad", "good"])

    assert _step_statuses(db, "c-3") == [(0, "bad", "FAILED"), (1, "good", "PASSED")]
    run = RunRepository(db).get_run("c-3")
    assert (run.status, run.passed, run.failed) == ("FAILED", 1, 1)


def test_failed_condition_gate_blocks_the_run_and_cancels_the_rest():
    db = _session()
    RunRepository(db).create_run("c-4", "dev", "prod", {})
    _job(db, "bad", matching=False)
    _job(db, "gated")
    _job(db, "after")
    _execute(db, "c-4", [
        SequenceStep(job_name="bad"),
        SequenceStep(job_name="gated", condition=StepCondition(require_status=["PASSED"])),
        SequenceStep(job_name="after"),
    ])

    assert _step_statuses(db, "c-4") == [
        (0, "bad", "FAILED"), (1, "gated", "CANCELLED"), (2, "after", "CANCELLED"),
    ]
    assert RunRepository(db).get_run("c-4").status == "BLOCKED"


def test_satisfied_condition_gate_lets_the_run_continue():
    db = _session()
    RunRepository(db).create_run("c-5", "dev", "prod", {})
    _job(db, "ok")
    _job(db, "gated")
    _execute(db, "c-5", [
        SequenceStep(job_name="ok"),
        SequenceStep(job_name="gated", condition=StepCondition(require_status=["PASSED"])),
    ])

    assert _step_statuses(db, "c-5") == [(0, "ok", "PASSED"), (1, "gated", "PASSED")]
    assert RunRepository(db).get_run("c-5").status == "PASSED"


def test_condition_on_the_first_step_is_ignored():
    # i > 0 guard at run_executor.py:227 -- a condition on step 0 has no
    # predecessor to check, and today it is skipped rather than failing.
    db = _session()
    RunRepository(db).create_run("c-6", "dev", "prod", {})
    _job(db, "only")
    _execute(db, "c-6", [
        SequenceStep(job_name="only", condition=StepCondition(require_status=["NOPE"])),
    ])

    assert _step_statuses(db, "c-6") == [(0, "only", "PASSED")]
    assert RunRepository(db).get_run("c-6").status == "PASSED"


def test_unknown_job_marks_the_step_error_and_continues():
    db = _session()
    RunRepository(db).create_run("c-7", "dev", "prod", {})
    _job(db, "real")
    _execute(db, "c-7", ["ghost", "real"])

    assert _step_statuses(db, "c-7") == [(0, "ghost", "ERROR"), (1, "real", "PASSED")]


def test_cancel_requested_before_the_run_cancels_every_step():
    db = _session()
    RunRepository(db).create_run("c-8", "dev", "prod", {})
    _job(db, "a")
    _job(db, "b")
    RunRepository(db).request_cancel("c-8")
    _execute(db, "c-8", ["a", "b"])

    assert _step_statuses(db, "c-8") == [(0, "a", "CANCELLED"), (1, "b", "CANCELLED")]
    assert RunRepository(db).get_run("c-8").status == "CANCELLED"


def test_run_steps_store_hold_and_condition_metadata():
    db = _session()
    RunRepository(db).create_run("c-9", "dev", "prod", {})
    _job(db, "a")
    _job(db, "b")
    _execute(db, "c-9", [
        SequenceStep(job_name="a"),
        SequenceStep(job_name="b", wait_seconds=0,
                     condition=StepCondition(require_status=["PASSED"], max_mismatch_count=3)),
    ])

    steps = RunStepRepository(db).list_steps("c-9")
    assert steps[0].hold_after is False
    assert steps[1].condition["max_mismatch_count"] == 3
    assert steps[1].wait_seconds == 0


def test_string_dict_and_model_steps_all_normalize():
    db = _session()
    RunRepository(db).create_run("c-10", "dev", "prod", {})
    for name in ("s", "d", "m"):
        _job(db, name)
    _execute(db, "c-10", ["s", {"job_name": "d"}, SequenceStep(job_name="m")])

    assert [x[1] for x in _step_statuses(db, "c-10")] == ["s", "d", "m"]
    assert RunRepository(db).get_run("c-10").total_tests == 3
