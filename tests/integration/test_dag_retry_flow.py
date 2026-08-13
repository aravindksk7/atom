"""Retry through a real run, with the attempt count persisted."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings, SequenceStepRef
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobRepository, RunRepository, RunStepRepository,
)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _erroring_job(db, name):
    """Mismatched source/target columns, which raise under an 'error' policy.

    This is the same recipe tests/unit/test_run_executor.py:375 uses to provoke
    an ERROR status -- source has col_a, target has col_b, and the run settings
    below set schema_mismatch_policy="error".
    """
    JobRepository(db).create({
        "name": name, "description": "", "tags": [],
        "job_type": "reconciliation", "query": f"SELECT * FROM {name}",
        "key_columns": ["id"], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {
            "source_rows": [{"id": 1, "col_a": "x"}],
            "target_rows": [{"id": 1, "col_b": "x"}],
        },
        "enabled": True,
    })


_ERROR_SETTINGS = RunSettings(schema_mismatch_policy="error", metrics_enabled=False)


def test_step_retry_records_attempts_and_final_error():
    db = _session()
    RunRepository(db).create_run("retry-1", "dev", "prod", {})
    _erroring_job(db, "flaky")

    from api.services.run_executor import RunExecutor
    RunExecutor(
        db=db, run_id="retry-1", source_env="dev", target_env="prod",
        job_sequence=[SequenceStepRef(
            step_id="a", job_name="flaky", max_retries=2, retry_delay_seconds=0,
        )],
        run_settings=_ERROR_SETTINGS,
    ).execute()

    step = RunStepRepository(db).get_step_by_step_id("retry-1", "a")
    assert step.status == "ERROR"
    assert step.attempt == 3          # initial + 2 retries


def test_default_run_does_not_retry():
    db = _session()
    RunRepository(db).create_run("retry-2", "dev", "prod", {})
    _erroring_job(db, "flaky")

    from api.services.run_executor import RunExecutor
    RunExecutor(
        db=db, run_id="retry-2", source_env="dev", target_env="prod",
        job_sequence=["flaky"],
        run_settings=_ERROR_SETTINGS,
    ).execute()

    step = RunStepRepository(db).list_steps("retry-2")[0]
    assert step.status == "ERROR"
    assert step.attempt == 1          # run-level max_retries defaults to 0
