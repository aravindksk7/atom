from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
from etl_framework.runner.state import TestStatus


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def executor(db_session):
    return RunExecutor(db=db_session, run_id="run-1", source_env="qa", target_env="prod", job_sequence=[], run_settings=RunSettings(use_live_connections=True))


def job(params=None):
    p = {"config_id": 1, "database": "curated", "query": "select 1", "output_location": "s3://out/"}
    if params:
        p.update(params)
    return JobDefinition(name="athena_orders", job_type="aws_athena_query", params=p)


def run_response(row_count=2, state="SUCCEEDED"):
    return SimpleNamespace(query_execution_id="qid-1", status=SimpleNamespace(state=state, engine_execution_time_ms=42, data_scanned_bytes=1024), results=SimpleNamespace(columns=["id"], rows=[{"id": "1"}, {"id": "2"}]), dq_metrics={"row_count": row_count, "columns": ["id"], "null_counts": {"id": 0}, "distinct_counts": {"id": row_count}, "numeric": {"id": {"min": 1.0, "max": float(row_count), "avg": 1.5}}})


def test_execute_athena_query_passes(monkeypatch, db_session):
    monkeypatch.setattr("api.services.run_executor.AwsAthenaService", lambda repo: SimpleNamespace(run_query=lambda *a, **k: run_response()))
    result = executor(db_session)._execute_aws_athena_query(job({"min_rows": 1, "max_rows_assert": 3, "metric_assertions": {"null_counts.id": 0}}))
    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["athena"]["query_execution_id"] == "qid-1"


def test_execute_athena_query_fails_assertions(monkeypatch, db_session):
    monkeypatch.setattr("api.services.run_executor.AwsAthenaService", lambda repo: SimpleNamespace(run_query=lambda *a, **k: run_response(row_count=0)))
    result = executor(db_session)._execute_aws_athena_query(job({"min_rows": 1, "metric_assertions": {"null_counts.id": 1}}))
    assert result.status == TestStatus.FAILED
    assert {m.mismatch_type for m in result.mismatches} == {"athena_row_count_below_min", "athena_metric_mismatch"}


def test_execute_athena_query_errors(monkeypatch, db_session):
    def fail(*a, **k):
        raise RuntimeError("athena unavailable")

    monkeypatch.setattr("api.services.run_executor.AwsAthenaService", lambda repo: SimpleNamespace(run_query=fail))
    result = executor(db_session)._execute_aws_athena_query(job())
    assert result.status == TestStatus.ERROR
    assert result.mismatches[0].mismatch_type == "athena_error"
