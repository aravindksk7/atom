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


def _make_job(db, name, source_rows, target_rows):
    JobRepository(db).create({
        "name": name,
        "description": name,
        "tags": [],
        "job_type": "reconciliation",
        "query": f"SELECT * FROM {name}",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {"source_rows": source_rows, "target_rows": target_rows},
        "enabled": True,
    })


def _make_job_with_deps(db, name, depends_on):
    JobRepository(db).create({
        "name": name,
        "description": name,
        "tags": [],
        "job_type": "reconciliation",
        "query": f"SELECT * FROM {name}",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {
            "depends_on": depends_on,
            "source_rows": [{"id": 1}],
            "target_rows": [{"id": 1}],
        },
        "enabled": True,
    })


def test_condition_blocks_after_resultless_error_step():
    """Step A errors without a result (schema policy error). Step B's condition
    must NOT be evaluated against a stale result or skipped - it must block."""
    db = _session()
    RunRepository(db).create_run("run-g1", "dev", "prod", {})
    # schema mismatch + policy=error -> case raises -> ERROR state, no result
    _make_job(db, "job_a", [{"id": 1, "amount": 1.0}], [{"id": 1}])
    _make_job(db, "job_b", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])

    RunExecutor(
        db=db,
        run_id="run-g1",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="job_a"),
            SequenceStep(job_name="job_b",
                         condition=StepCondition(require_status=["PASSED"])),
        ],
        run_settings=RunSettings(schema_mismatch_policy="error",
                                 metrics_enabled=False),
    ).execute()

    steps = RunStepRepository(db).list_steps("run-g1")
    assert steps[0].status == "ERROR"
    assert steps[1].status == "CANCELLED"
    run = RunRepository(db).get_run("run-g1")
    assert run.status == "BLOCKED"


def test_condition_break_sets_blocked_not_passed():
    """Step A FAILS (value mismatch); step B requires PASSED. Run must end
    BLOCKED, with A's failure still counted."""
    db = _session()
    RunRepository(db).create_run("run-g2", "dev", "prod", {})
    _make_job(db, "job_a", [{"id": 1, "amount": 10.0}], [{"id": 1, "amount": 9.0}])
    _make_job(db, "job_b", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])

    RunExecutor(
        db=db,
        run_id="run-g2",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="job_a"),
            SequenceStep(job_name="job_b",
                         condition=StepCondition(require_status=["PASSED"])),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-g2")
    assert run.status == "BLOCKED"
    assert run.failed == 1
    assert run.completed_at is not None
    steps = RunStepRepository(db).list_steps("run-g2")
    assert steps[1].status == "CANCELLED"


def test_condition_on_first_step_is_ignored():
    """A condition on step 0 has nothing to evaluate; step must still run."""
    db = _session()
    RunRepository(db).create_run("run-g3", "dev", "prod", {})
    _make_job(db, "job_a", [{"id": 1, "v": 1}], [{"id": 1, "v": 1}])

    RunExecutor(
        db=db,
        run_id="run-g3",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="job_a",
                         condition=StepCondition(require_status=["PASSED"])),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-g3")
    assert run.status == "PASSED"


def test_max_compare_rows_marks_step_error():
    db = _session()
    RunRepository(db).create_run("run-mr1", "dev", "prod", {})
    _make_job(
        db, "big_job",
        [{"id": i, "v": i} for i in range(6)],
        [{"id": i, "v": i} for i in range(6)],
    )

    RunExecutor(
        db=db,
        run_id="run-mr1",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="big_job")],
        run_settings=RunSettings(metrics_enabled=False, max_compare_rows=5),
    ).execute()

    run = RunRepository(db).get_run("run-mr1")
    assert run.status == "ERROR"
    assert "max_compare_rows=5" in run.results[0].error_message


def test_sequence_violating_depends_on_errors_before_any_step():
    db = _session()
    RunRepository(db).create_run("run-d1", "dev", "prod", {})
    _make_job_with_deps(db, "loader", [])
    _make_job_with_deps(db, "reconciler", ["loader"])

    RunExecutor(
        db=db,
        run_id="run-d1",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="reconciler"),
            SequenceStep(job_name="loader"),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-d1")
    steps = RunStepRepository(db).list_steps("run-d1")
    assert run.status == "ERROR"
    assert steps == []
    assert "depends on 'loader'" in run.results[0].error_message


def test_sequence_respecting_depends_on_passes():
    db = _session()
    RunRepository(db).create_run("run-d2", "dev", "prod", {})
    _make_job_with_deps(db, "loader", [])
    _make_job_with_deps(db, "reconciler", ["loader"])

    RunExecutor(
        db=db,
        run_id="run-d2",
        source_env="dev",
        target_env="prod",
        job_sequence=[
            SequenceStep(job_name="loader"),
            SequenceStep(job_name="reconciler"),
        ],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-d2")
    assert run.status == "PASSED"


def test_sequence_ignores_depends_on_absent_from_sequence():
    db = _session()
    RunRepository(db).create_run("run-d3", "dev", "prod", {})
    _make_job_with_deps(db, "reconciler", ["external_loader"])

    RunExecutor(
        db=db,
        run_id="run-d3",
        source_env="dev",
        target_env="prod",
        job_sequence=[SequenceStep(job_name="reconciler")],
        run_settings=RunSettings(metrics_enabled=False),
    ).execute()

    run = RunRepository(db).get_run("run-d3")
    assert run.status == "PASSED"
