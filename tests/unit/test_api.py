import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models  # noqa: F401 — registers ORM models with Base
from api.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- Config endpoints ---

def test_list_configs_empty(client):
    resp = client.get("/api/configs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_config(client):
    payload = {"name": "dev", "env_name": "dev", "config_data": {"db_host": "localhost", "db_port": 1433}}
    resp = client.post("/api/configs", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "dev"
    assert data["id"] is not None


def test_get_config(client):
    resp = client.post("/api/configs", json={"name": "qa", "env_name": "qa", "config_data": {}})
    cfg_id = resp.json()["id"]
    resp2 = client.get(f"/api/configs/{cfg_id}")
    assert resp2.status_code == 200
    assert resp2.json()["name"] == "qa"


def test_get_config_not_found(client):
    resp = client.get("/api/configs/9999")
    assert resp.status_code == 404


def test_update_config(client):
    resp = client.post("/api/configs", json={"name": "stage", "env_name": "stage", "config_data": {"timeout": 30}})
    cfg_id = resp.json()["id"]
    resp2 = client.put(f"/api/configs/{cfg_id}", json={"config_data": {"timeout": 60}})
    assert resp2.status_code == 200
    assert resp2.json()["config_data"]["timeout"] == 60


def test_delete_config(client):
    resp = client.post("/api/configs", json={"name": "tmp", "env_name": "dev", "config_data": {}})
    cfg_id = resp.json()["id"]
    resp2 = client.delete(f"/api/configs/{cfg_id}")
    assert resp2.status_code == 204
    resp3 = client.get(f"/api/configs/{cfg_id}")
    assert resp3.status_code == 404


# --- Runs endpoints ---

def test_list_runs_empty(client):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trigger_run(client):
    payload = {"source_env": "dev", "target_env": "prod", "job_names": ["orders_query"]}
    resp = client.post("/api/runs", json=payload)
    assert resp.status_code == 202
    data = resp.json()
    assert "run_id" in data
    assert data["status"] == "PENDING"


def test_get_run_status(client):
    resp = client.post("/api/runs", json={"source_env": "dev", "target_env": "prod", "job_names": []})
    run_id = resp.json()["run_id"]
    resp2 = client.get(f"/api/runs/{run_id}/status")
    assert resp2.status_code == 200
    assert resp2.json()["run_id"] == run_id


def test_get_run_detail(client):
    resp = client.post("/api/runs", json={"source_env": "dev", "target_env": "prod", "job_names": []})
    run_id = resp.json()["run_id"]
    resp2 = client.get(f"/api/runs/{run_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["run_id"] == run_id
    assert "results" in data


def test_get_run_not_found(client):
    resp = client.get("/api/runs/nonexistent-run-id")
    assert resp.status_code == 404


# --- Jobs endpoints ---

def test_list_jobs(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
