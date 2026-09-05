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
    p = {"config_id": 1, "dag_id": "nightly_etl"}
    if params:
        p.update(params)
    return JobDefinition(name="airflow_etl", job_type="airflow_dag_run", params=p)


def run_result(state="success", task_instances=None, dag_run_id="dag-run-1"):
    return {
        "dag_run_id": dag_run_id,
        "dag_id": "nightly_etl",
        "state": state,
        "task_instances": task_instances or [],
    }


def test_execute_airflow_dag_run_passes(monkeypatch, db_session):
    monkeypatch.setattr(
        "api.services.run_executor.AwsAirflowService",
        lambda repo: SimpleNamespace(run_dag_to_completion=lambda *a, **k: run_result()),
    )
    result = executor(db_session)._execute_airflow_dag_run(job())
    assert result.status == TestStatus.PASSED
    assert result.mismatches == []
    assert result.mismatch_summary["airflow"]["dag_run_id"] == "dag-run-1"


def test_execute_airflow_dag_run_failing_state(monkeypatch, db_session):
    monkeypatch.setattr(
        "api.services.run_executor.AwsAirflowService",
        lambda repo: SimpleNamespace(run_dag_to_completion=lambda *a, **k: run_result(state="failed")),
    )
    result = executor(db_session)._execute_airflow_dag_run(job())
    assert result.status == TestStatus.FAILED
    assert result.mismatches[0].mismatch_type == "airflow_dag_run_status_mismatch"
    assert result.mismatches[0].column_name == "dag_run_state"


def test_execute_airflow_dag_run_task_assertion_mismatch(monkeypatch, db_session):
    monkeypatch.setattr(
        "api.services.run_executor.AwsAirflowService",
        lambda repo: SimpleNamespace(run_dag_to_completion=lambda *a, **k: run_result(task_instances=[{"task_id": "extract", "state": "failed", "duration": 1.0}])),
    )
    result = executor(db_session)._execute_airflow_dag_run(job({"task_assertions": {"extract": "success"}}))
    assert result.status == TestStatus.FAILED
    assert result.mismatches[0].mismatch_type == "airflow_task_status_mismatch"


def test_execute_airflow_dag_run_missing_task(monkeypatch, db_session):
    monkeypatch.setattr(
        "api.services.run_executor.AwsAirflowService",
        lambda repo: SimpleNamespace(run_dag_to_completion=lambda *a, **k: run_result(task_instances=[{"task_id": "extract", "state": "success", "duration": 1.0}])),
    )
    result = executor(db_session)._execute_airflow_dag_run(job({"task_assertions": {"missing_task": "success"}}))
    assert result.status == TestStatus.FAILED
    assert result.mismatches[0].mismatch_type == "airflow_task_status_mismatch"
    assert result.mismatches[0].target_value == "missing"


def test_execute_airflow_dag_run_service_error(monkeypatch, db_session):
    def fail(*a, **k):
        raise RuntimeError("airflow unavailable")

    monkeypatch.setattr("api.services.run_executor.AwsAirflowService", lambda repo: SimpleNamespace(run_dag_to_completion=fail))
    result = executor(db_session)._execute_airflow_dag_run(job())
    assert result.status == TestStatus.ERROR
    assert result.mismatches[0].mismatch_type == "airflow_error"


def test_execute_airflow_dag_run_timeout(monkeypatch, db_session):
    def fail(*a, **k):
        raise TimeoutError("dag run did not reach a terminal state")

    monkeypatch.setattr("api.services.run_executor.AwsAirflowService", lambda repo: SimpleNamespace(run_dag_to_completion=fail))
    result = executor(db_session)._execute_airflow_dag_run(job())
    assert result.status == TestStatus.ERROR
    assert result.mismatches[0].mismatch_type == "airflow_timeout"