from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
from etl_framework.repository.repository import TokenRepository
import etl_framework.repository.models  # noqa: F401


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


@pytest.fixture(autouse=True)
def mock_service():
    from api.main import app
    from api.routes.aws_airflow import get_aws_airflow_service
    svc = MagicMock()
    svc.list_dags.return_value = [
        {"dag_id": "a", "description": "A", "is_paused": False, "schedule_interval": "@daily"},
    ]
    svc.get_dag_details.return_value = {"dag_id": "a", "description": "A", "is_paused": False, "schedule_interval": "@daily"}
    svc.trigger_dag_run.return_value = {"dag_run_id": "run_1", "dag_id": "a", "state": "queued", "logical_date": None}
    svc.get_dag_run_status.return_value = {
        "dag_run_id": "run_1",
        "dag_id": "a",
        "state": "success",
        "task_instances": [{"task_id": "t1", "state": "success", "duration": 1.0}],
    }
    svc.run_dag_to_completion.return_value = {
        "dag_run_id": "run_1",
        "dag_id": "a",
        "state": "success",
        "task_instances": [{"task_id": "t1", "state": "success", "duration": 1.0}],
    }
    app.dependency_overrides[get_aws_airflow_service] = lambda: svc
    yield svc
    app.dependency_overrides.pop(get_aws_airflow_service, None)


def test_list_dags_route(client, mock_service):
    r = client.get("/api/aws/airflow/dags?config_id=1")
    assert r.status_code == 200
    assert r.json() == {"dags": [{"dag_id": "a", "description": "A", "is_paused": False, "schedule_interval": "@daily"}]}
    mock_service.list_dags.assert_called_once_with(1)


def test_dag_details_route(client, mock_service):
    r = client.get("/api/aws/airflow/dags/a?config_id=1")
    assert r.status_code == 200
    assert r.json()["dag_id"] == "a"
    mock_service.get_dag_details.assert_called_once_with(1, "a")


def test_trigger_route(client, mock_service):
    r = client.post(
        "/api/aws/airflow/dags/a/trigger",
        json={"config_id": 1, "conf": {"batch": "1"}},
    )
    assert r.status_code == 200
    assert r.json() == {"dag_run_id": "run_1", "dag_id": "a", "state": "queued", "logical_date": None}
    mock_service.trigger_dag_run.assert_called_once_with(1, "a", {"batch": "1"})


def test_trigger_route_without_conf(client, mock_service):
    r = client.post("/api/aws/airflow/dags/a/trigger", json={"config_id": 1})
    assert r.status_code == 200
    mock_service.trigger_dag_run.assert_called_once_with(1, "a", None)


def test_run_status_route(client, mock_service):
    r = client.get("/api/aws/airflow/dags/a/runs/run_1?config_id=1")
    assert r.status_code == 200
    data = r.json()
    assert data["dag_run_id"] == "run_1"
    assert data["state"] == "success"
    assert data["task_instances"] == [{"task_id": "t1", "state": "success", "duration": 1.0}]
    mock_service.get_dag_run_status.assert_called_once_with(1, "a", "run_1")


def test_run_route(client, mock_service):
    r = client.post("/api/aws/airflow/dags/a/run", json={"config_id": 1, "conf": {"batch": "1"}})
    assert r.status_code == 200
    assert r.json()["task_instances"] == [{"task_id": "t1", "state": "success", "duration": 1.0}]
    mock_service.run_dag_to_completion.assert_called_once_with(1, "a", {"batch": "1"}, 1.0, 60)


def test_run_route_uses_custom_poll_and_attempts(client, mock_service):
    r = client.post(
        "/api/aws/airflow/dags/a/run",
        json={"config_id": 1, "poll_interval_seconds": 0.5, "max_attempts": 10},
    )
    assert r.status_code == 200
    mock_service.run_dag_to_completion.assert_called_once_with(1, "a", None, 0.5, 10)


def test_route_maps_generic_exception_to_structured_400(client, mock_service):
    mock_service.list_dags.side_effect = RuntimeError("Airflow unreachable")
    r = client.get("/api/aws/airflow/dags?config_id=1")
    assert r.status_code == 400
    assert r.json()["detail"] == {"error_type": "RuntimeError", "message": "Airflow unreachable"}


def test_route_maps_timeout_exception_to_structured_400(client, mock_service):
    mock_service.run_dag_to_completion.side_effect = TimeoutError("did not reach a terminal state")
    r = client.post("/api/aws/airflow/dags/a/run", json={"config_id": 1})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error_type"] == "TimeoutError"
    assert "terminal state" in detail["message"]


def test_route_preserves_missing_config_http_exception(client, mock_service):
    mock_service.list_dags.side_effect = HTTPException(status_code=404, detail="Config not found")
    r = client.get("/api/aws/airflow/dags?config_id=404")
    assert r.status_code == 404
    assert r.json()["detail"] == "Config not found"


def test_audit_entry_written(client, mock_service):
    with patch("api.routes.aws_airflow.AuditService") as MockAudit:
        r = client.post(
            "/api/aws/airflow/dags/a/trigger",
            json={"config_id": 1, "conf": {"batch": "1"}},
        )
    assert r.status_code == 200
    MockAudit.return_value.log.assert_called_once()
    call_kwargs = MockAudit.return_value.log.call_args
    assert call_kwargs.args[0] is not None
    assert call_kwargs.args[1] == "aws_airflow.check"
    assert call_kwargs.args[2] == "aws_airflow"
    assert call_kwargs.args[3] == "a"
    assert call_kwargs.args[4] == {"op": "trigger", "config_id": 1}