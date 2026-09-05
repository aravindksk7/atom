from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.services.aws_airflow_runtime import AwsAirflowRuntime
from api.services.aws_airflow_service import AwsAirflowService
from etl_framework.airflow.models import AirflowDag, AirflowDagRun, AirflowTaskInstance
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository


def _run(dag_run_id: str, dag_id: str, state: str, logical_date: str | None = None) -> AirflowDagRun:
    return AirflowDagRun(dag_run_id=dag_run_id, dag_id=dag_id, state=state, logical_date=logical_date)


@pytest.fixture
def config_repo():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield ConfigRepository(db)


# ---------------------------------------------------------------------------
# run_dag_to_completion
# ---------------------------------------------------------------------------


def test_run_dag_to_completion_polls_running_to_success(monkeypatch):
    fake_client = MagicMock()
    fake_client.trigger_dag_run_sync.return_value = _run("run_1", "test_dag", "queued")
    fake_client.get_dag_run_sync.side_effect = [
        _run("run_1", "test_dag", "running"),
        _run("run_1", "test_dag", "success"),
    ]
    fake_client.list_task_instances_sync.return_value = [
        AirflowTaskInstance(task_id="task_1", dag_id="test_dag", state="success", duration=10.0),
    ]
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsAirflowService(runtime=runtime)
    sleeps: list[float] = []
    monkeypatch.setattr("api.services.aws_airflow_service.time.sleep", sleeps.append)

    result = service.run_dag_to_completion(
        1, "test_dag", conf={"batch": "1"}, poll_interval_seconds=0.01, max_attempts=5
    )

    assert result["dag_run_id"] == "run_1"
    assert result["dag_id"] == "test_dag"
    assert result["state"] == "success"
    assert result["task_instances"] == [{"task_id": "task_1", "state": "success", "duration": 10.0}]
    assert sleeps == [0.01]
    assert fake_client.get_dag_run_sync.call_count == 2
    fake_client.trigger_dag_run_sync.assert_called_once_with("test_dag", conf={"batch": "1"})


def test_run_dag_to_completion_times_out_after_max_attempts(monkeypatch):
    fake_client = MagicMock()
    fake_client.trigger_dag_run_sync.return_value = _run("run_1", "test_dag", "queued")
    fake_client.get_dag_run_sync.return_value = _run("run_1", "test_dag", "running")
    fake_client.list_task_instances_sync.return_value = []
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsAirflowService(runtime=runtime)
    sleeps: list[float] = []
    monkeypatch.setattr("api.services.aws_airflow_service.time.sleep", sleeps.append)

    with pytest.raises(TimeoutError, match="run_1"):
        service.run_dag_to_completion(1, "test_dag", poll_interval_seconds=0.01, max_attempts=2)

    assert sleeps == [0.01]
    assert fake_client.get_dag_run_sync.call_count == 2


@pytest.mark.asyncio
async def test_run_dag_to_completion_async_success():
    fake_client = AsyncMock()
    fake_client.trigger_dag_run.return_value = _run("run_1", "test_dag", "queued")
    fake_client.get_dag_run.side_effect = [
        _run("run_1", "test_dag", "running"),
        _run("run_1", "test_dag", "failed"),
    ]
    fake_client.list_task_instances.return_value = [
        AirflowTaskInstance(task_id="task_1", dag_id="test_dag", state="failed", duration=5.0),
        AirflowTaskInstance(task_id="task_2", dag_id="test_dag", state="success", duration=2.0),
    ]
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsAirflowService(runtime=runtime)

    result = await service.run_dag_to_completion_async(1, "test_dag", poll_interval_seconds=0.01, max_attempts=5)

    assert result["dag_run_id"] == "run_1"
    assert result["dag_id"] == "test_dag"
    assert result["state"] == "failed"
    assert [t["task_id"] for t in result["task_instances"]] == ["task_1", "task_2"]
    assert result["task_instances"][0] == {"task_id": "task_1", "state": "failed", "duration": 5.0}


@pytest.mark.asyncio
async def test_run_dag_to_completion_async_times_out():
    fake_client = AsyncMock()
    fake_client.trigger_dag_run.return_value = _run("run_1", "test_dag", "queued")
    fake_client.get_dag_run.return_value = _run("run_1", "test_dag", "queued")
    fake_client.list_task_instances.return_value = []
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsAirflowService(runtime=runtime)

    with pytest.raises(TimeoutError, match="run_1"):
        await service.run_dag_to_completion_async(1, "test_dag", poll_interval_seconds=0, max_attempts=3)

    assert fake_client.get_dag_run.call_count == 3


# ---------------------------------------------------------------------------
# trigger_dag_run / get_dag_run_status
# ---------------------------------------------------------------------------


def test_trigger_dag_run_returns_expected_dict():
    fake_client = MagicMock()
    fake_client.trigger_dag_run_sync.return_value = _run(
        "run_7", "etl_dag", "queued", logical_date="2026-09-05T00:00:00+00:00"
    )
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsAirflowService(runtime=runtime)

    result = service.trigger_dag_run(1, "etl_dag", conf={"x": 1})

    assert result == {
        "dag_run_id": "run_7",
        "dag_id": "etl_dag",
        "state": "queued",
        "logical_date": "2026-09-05T00:00:00+00:00",
    }
    fake_client.trigger_dag_run_sync.assert_called_once_with("etl_dag", conf={"x": 1})


def test_get_dag_run_status_returns_run_and_task_instances():
    fake_client = MagicMock()
    fake_client.get_dag_run_sync.return_value = _run("run_9", "etl_dag", "running")
    fake_client.list_task_instances_sync.return_value = [
        AirflowTaskInstance(task_id="extract", dag_id="etl_dag", state="success", duration=12.5),
        AirflowTaskInstance(task_id="load", dag_id="etl_dag", state="running"),
    ]
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsAirflowService(runtime=runtime)

    result = service.get_dag_run_status(1, "etl_dag", "run_9")

    assert result["dag_run_id"] == "run_9"
    assert result["dag_id"] == "etl_dag"
    assert result["state"] == "running"
    assert result["task_instances"] == [
        {"task_id": "extract", "state": "success", "duration": 12.5},
        {"task_id": "load", "state": "running", "duration": None},
    ]
    fake_client.get_dag_run_sync.assert_called_once_with("etl_dag", "run_9")
    fake_client.list_task_instances_sync.assert_called_once_with("etl_dag", "run_9")


def test_list_dags_and_get_dag_details():
    fake_client = MagicMock()
    fake_client.list_dags_sync.return_value = [
        AirflowDag(dag_id="a", description="A", is_paused=False, schedule_interval="@daily"),
        AirflowDag(dag_id="b"),
    ]
    runtime = MagicMock()
    runtime.client.return_value = fake_client
    service = AwsAirflowService(runtime=runtime)

    dags = service.list_dags(1)
    assert dags == [
        {"dag_id": "a", "description": "A", "is_paused": False, "schedule_interval": "@daily"},
        {"dag_id": "b", "description": None, "is_paused": False, "schedule_interval": None},
    ]
    assert service.get_dag_details(1, "a") == dags[0]
    with pytest.raises(ValueError):
        service.get_dag_details(1, "missing")


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


def test_airflow_runtime_resolves_named_config_and_standalone_client(config_repo):
    cfg = config_repo.create(
        "airflow-dev",
        "dev",
        {
            "db_host": "localhost",
            "db_password": "unused",
            "airflow_url": "https://airflow.example.com",
            "airflow_username": "admin",
            "airflow_password": "secret",
        },
    )
    runtime = AwsAirflowRuntime(config_repo)
    assert runtime.config_id("airflow-dev") == cfg.id
    assert runtime.env("airflow-dev").name == "dev"

    client = runtime.client(cfg.id)
    assert client.base_url == "https://airflow.example.com"
    assert client.username == "admin"
    assert client.password == "secret"
    assert client.token is None


def test_airflow_runtime_standalone_requires_airflow_url(config_repo):
    cfg = config_repo.create("airflow-bad", "dev", {"db_host": "localhost", "db_password": "unused"})
    with pytest.raises(HTTPException) as err:
        AwsAirflowRuntime(config_repo).client(cfg.id)
    assert err.value.status_code == 400


def test_airflow_runtime_mwaa_client_resolves_web_login_token(config_repo, monkeypatch):
    cfg = config_repo.create(
        "mwaa-prod",
        "prod",
        {"aws_region": "us-east-1", "aws_access_key_id": "AKIA", "aws_secret_access_key": "SK", "mwaa_environment": "my-mwaa"},
    )

    class FakeMWAA:
        def create_web_login_token(self, Name):
            assert Name == "my-mwaa"
            return {"WebToken": "jwt-token", "WebServerHostname": "host.example.com"}

    class FakeSession:
        def __init__(self, cfg):
            self._cfg = cfg

        def client(self, service):
            assert service == "mwaa"
            return FakeMWAA()

    monkeypatch.setattr("api.services.aws_airflow_runtime.AWSSession", FakeSession)
    client = AwsAirflowRuntime(config_repo).client(cfg.id)
    assert client.base_url == "https://host.example.com"
    assert client.token == "jwt-token"
    assert client.username is None
    assert client.password is None


def test_airflow_runtime_missing_config_maps_to_404(config_repo):
    with pytest.raises(HTTPException) as err:
        AwsAirflowRuntime(config_repo).client(999)
    assert err.value.status_code == 404