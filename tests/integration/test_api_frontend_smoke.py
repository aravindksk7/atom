import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.dependencies import get_session
from api.routes import runs as runs_module
from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch):
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    # Point the auth middleware's SessionLocal at the test DB
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    def session_factory():
        return Session(engine)

    execute_run = runs_module._execute_run

    def fast_execute_run(run_id, job_sequence, source_env, target_env, run_settings, config_snapshot):
        execute_run(
            run_id,
            job_sequence,
            source_env,
            target_env,
            run_settings,
            config_snapshot,
            session_factory=session_factory,
        )

    monkeypatch.setattr(runs_module, "_execute_run", fast_execute_run)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session] = override_get_db

    # Create a token for the smoke test
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("smoke-test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)


def test_frontend_api_run_lifecycle_smoke(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    frontend = client.get("/")
    assert frontend.status_code == 200
    assert "ETL Test Framework" in frontend.text
    assert "Validate Configuration" in frontend.text
    assert "DB Password" in frontend.text
    assert "Run Health Check" in frontend.text
    assert "Add Job" in frontend.text
    assert "Execution Sequence" in frontend.text
    assert "Comparison Backend" in frontend.text
    assert "Pass with actions" in frontend.text
    assert "Users &amp; API Access" in frontend.text
    assert "Create Initial Administrator" in frontend.text
    assert "Standard user" in frontend.text

    app_js = client.get("/app.js")
    assert app_js.status_code == 200
    assert "window.ETL_API_BASE || ''" in app_js.text
    assert "job_sequence" in app_js.text
    assert "run_settings" in app_js.text

    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    assert len(jobs.json()) >= 2

    config = client.post(
        "/api/configs",
        json={
            "name": "smoke-dev",
            "env_name": "dev",
            "config_data": {"db_host": "localhost", "db_port": 1433},
        },
    )
    assert config.status_code == 201
    assert config.json()["config_data"]["db_host"] == "localhost"

    launch = client.post(
        "/api/runs",
        json={
            "source_env": "dev",
            "target_env": "prod",
            "job_names": ["orders_reconciliation", "customers_reconciliation"],
            "job_sequence": ["customers_reconciliation", "orders_reconciliation"],
            "run_settings": {
                "execution_mode": "sequential",
                "max_workers": 1,
                "schema_mismatch_policy": "warn",
                "chunk_size": 0,
                "comparison_backend": "pandas",
                "metrics_enabled": True,
            },
            "config_data": config.json()["config_data"],
        },
    )
    assert launch.status_code == 202
    run_id = launch.json()["run_id"]

    status_data = {}
    for _ in range(100):
        status = client.get(f"/api/runs/{run_id}/status")
        assert status.status_code == 200
        status_data = status.json()
        if status_data["status"] not in {"PENDING", "RUNNING"}:
            break
        time.sleep(0.02)
    assert status_data["status"] in {"PASSED", "FAILED", "SLOW"}
    assert status_data["total_tests"] == 2
    assert status_data["passed"] + status_data["failed"] + status_data["slow"] == 2

    detail = client.get(f"/api/runs/{run_id}")
    assert detail.status_code == 200
    detail_data = detail.json()
    assert detail_data["source_env"] == "dev"
    assert detail_data["target_env"] == "prod"
    assert [s["job_name"] for s in detail_data["config_snapshot"]["job_sequence"]] == [
        "customers_reconciliation",
        "orders_reconciliation",
    ]
    assert detail_data["config_snapshot"]["run_settings"]["execution_mode"] == "sequential"
    assert detail_data["config_snapshot"]["run_settings"]["schema_mismatch_policy"] == "warn"
    assert len(detail_data["results"]) == 2

    metrics = client.get(f"/api/runs/{run_id}/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["run_id"] == run_id
    assert metrics.json()["total_tests"] == 2

    artifacts = client.get(f"/api/runs/{run_id}/artifacts")
    assert artifacts.status_code == 200
    assert any(item["artifact_type"] == "metrics" for item in artifacts.json())

    runs = client.get("/api/runs")
    assert runs.status_code == 200
    assert runs.json()[0]["run_id"] == run_id


def test_failed_result_can_pass_with_agreed_actions(client):
    launch = client.post(
        "/api/runs",
        json={
            "source_env": "dev",
            "target_env": "prod",
            "job_sequence": ["orders_reconciliation"],
            "config_data": {
                "source_rows": [{"id": 1, "value": "source"}],
                "target_rows": [{"id": 1, "value": "target"}],
            },
        },
    )
    assert launch.status_code == 202
    run_id = launch.json()["run_id"]
    result = client.get(f"/api/runs/{run_id}").json()["results"][0]
    assert result["status"] == "FAILED"
    assert result["effective_status"] == "FAILED"

    blank = client.patch(
        f"/api/runs/{run_id}/results/{result['id']}/override",
        json={"status": "PASSED", "reason": "   "},
    )
    assert blank.status_code == 422

    overridden = client.patch(
        f"/api/runs/{run_id}/results/{result['id']}/override",
        json={
            "status": "PASSED",
            "reason": "Correct source mapping before the next release.",
        },
    )
    assert overridden.status_code == 200
    assert overridden.json()["status"] == "FAILED"
    assert overridden.json()["effective_status"] == "PASSED"
    assert overridden.json()["override_reason"] == "Correct source mapping before the next release."
    assert overridden.json()["overridden_by"] == "smoke-test"

    detail = client.get(f"/api/runs/{run_id}").json()["results"][0]
    assert detail["status"] == "FAILED"
    assert detail["effective_status"] == "PASSED"

    removed = client.delete(f"/api/runs/{run_id}/results/{result['id']}/override")
    assert removed.status_code == 200
    assert removed.json()["effective_status"] == "FAILED"
    assert removed.json()["override_reason"] is None


def test_webhook_delivery_is_tracked_with_thread_owned_session(client):
    hook = client.post(
        "/api/notifications",
        json={
            "name": "tracking-test",
            "url": "invalid://webhook",
            "events": ["run.failed"],
        },
    )
    assert hook.status_code == 201
    hook_id = hook.json()["id"]

    launch = client.post(
        "/api/runs",
        json={
            "source_env": "dev",
            "target_env": "prod",
            "job_sequence": ["orders_reconciliation"],
            "config_data": {
                "source_rows": [{"id": 1, "value": "source"}],
                "target_rows": [{"id": 1, "value": "target"}],
            },
        },
    )
    assert launch.status_code == 202

    deliveries = []
    for _ in range(50):
        response = client.get(f"/api/notifications/{hook_id}/deliveries")
        assert response.status_code == 200
        deliveries = response.json()
        if deliveries and deliveries[0]["status"] != "pending":
            break
        time.sleep(0.02)

    assert len(deliveries) == 1
    assert deliveries[0]["event"] == "run.failed"
    assert deliveries[0]["status"] == "failed"
    assert deliveries[0]["attempt_count"] == 1
