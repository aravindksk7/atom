from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
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
    return RunExecutor(
        db=db_session,
        run_id="run-1",
        source_env="qa",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(use_live_connections=True),
    )


def job(params=None):
    p = {"config_id": 1, "job_name": "spark_etl", "expected_status": "SUCCEEDED"}
    if params:
        p.update(params)
    return JobDefinition(name="glue_spark_job", job_type="aws_glue_job_run", params=p)


def test_execute_aws_glue_job_run_passes(db_session, monkeypatch):
    mock_service = MagicMock()
    mock_service.run_job_to_completion.return_value = {
        "job_name": "spark_etl",
        "job_run_id": "jr_1",
        "job_run_state": "SUCCEEDED",
        "execution_time": 45,
        "error_message": None,
    }
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: mock_service)
    ex = executor(db_session)
    j = job()
    result = ex._execute_aws_glue_job_run(j)
    assert result.status == TestStatus.PASSED
    assert result.mismatches == []
    assert result.mismatch_summary["glue"]["job_run_id"] == "jr_1"
    assert result.mismatch_summary["glue"]["job_run_state"] == "SUCCEEDED"
    mock_service.run_job_to_completion.assert_called_once_with(
        config_id=1,
        job_name="spark_etl",
        arguments=None,
        poll_interval_seconds=2.0,
        max_attempts=120,
    )


def test_execute_aws_glue_job_run_with_custom_params(db_session, monkeypatch):
    mock_service = MagicMock()
    mock_service.run_job_to_completion.return_value = {
        "job_name": "spark_etl",
        "job_run_id": "jr_2",
        "job_run_state": "STOPPED",
        "execution_time": 10,
        "error_message": None,
    }
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: mock_service)
    ex = executor(db_session)
    j = job({
        "arguments": {"--env": "staging"},
        "poll_interval_seconds": 5.0,
        "max_attempts": 30,
        "expected_status": "STOPPED",
    })
    result = ex._execute_aws_glue_job_run(j)
    assert result.status == TestStatus.PASSED
    assert result.mismatches == []
    mock_service.run_job_to_completion.assert_called_once_with(
        config_id=1,
        job_name="spark_etl",
        arguments={"--env": "staging"},
        poll_interval_seconds=5.0,
        max_attempts=30,
    )


def test_execute_aws_glue_job_run_status_mismatch(db_session, monkeypatch):
    mock_service = MagicMock()
    mock_service.run_job_to_completion.return_value = {
        "job_name": "spark_etl",
        "job_run_id": "jr_1",
        "job_run_state": "FAILED",
        "execution_time": 12,
        "error_message": "OutOfMemoryError",
    }
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: mock_service)
    ex = executor(db_session)
    j = job({"expected_status": "SUCCEEDED"})
    result = ex._execute_aws_glue_job_run(j)
    assert result.status == TestStatus.FAILED
    assert len(result.mismatches) == 1
    mismatch = result.mismatches[0]
    assert mismatch.mismatch_type == "glue_job_status_mismatch"
    assert mismatch.column_name == "job_run_state"
    assert mismatch.source_value == "SUCCEEDED"
    assert mismatch.target_value == "FAILED"
    assert result.mismatch_summary["glue"]["error_message"] == "OutOfMemoryError"


def test_execute_aws_glue_job_run_timeout(db_session, monkeypatch):
    mock_service = MagicMock()
    mock_service.run_job_to_completion.side_effect = TimeoutError("Glue job 'spark_etl' run 'jr_1' timed out after 240.0s")
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: mock_service)
    ex = executor(db_session)
    j = job()
    result = ex._execute_aws_glue_job_run(j)
    assert result.status == TestStatus.ERROR
    assert len(result.mismatches) == 1
    assert result.mismatches[0].mismatch_type == "glue_job_timeout"
    assert "timed out" in result.mismatches[0].target_value
    assert result.mismatch_summary["glue"]["error"] == "Glue job 'spark_etl' run 'jr_1' timed out after 240.0s"


def test_execute_aws_glue_job_run_service_error(db_session, monkeypatch):
    mock_service = MagicMock()
    mock_service.run_job_to_completion.side_effect = RuntimeError("Glue service unavailable")
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: mock_service)
    ex = executor(db_session)
    j = job()
    result = ex._execute_aws_glue_job_run(j)
    assert result.status == TestStatus.ERROR
    assert len(result.mismatches) == 1
    assert result.mismatches[0].mismatch_type == "glue_job_error"
    assert result.mismatches[0].target_value == "Glue service unavailable"
    assert result.mismatch_summary["glue"]["error"] == "Glue service unavailable"


def test_execute_aws_glue_job_run_http_exception(db_session, monkeypatch):
    mock_service = MagicMock()
    mock_service.run_job_to_completion.side_effect = HTTPException(status_code=404, detail="Config not found")
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: mock_service)
    ex = executor(db_session)
    j = job()
    result = ex._execute_aws_glue_job_run(j)
    assert result.status == TestStatus.ERROR
    assert result.mismatches[0].mismatch_type == "glue_job_error"
    assert result.mismatches[0].target_value == "Config not found"
    assert result.mismatch_summary["glue"]["error"] == "Config not found"


def test_build_case_aws_glue_job_run(db_session, monkeypatch):
    mock_service = MagicMock()
    mock_service.run_job_to_completion.return_value = {
        "job_name": "spark_etl",
        "job_run_id": "jr_1",
        "job_run_state": "SUCCEEDED",
    }
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: mock_service)
    ex = executor(db_session)
    j = job()
    case_fn = ex._build_case(j)
    assert callable(case_fn)
    result = case_fn()
    assert result.status == TestStatus.PASSED
