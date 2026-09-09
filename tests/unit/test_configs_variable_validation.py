import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_create_config_rejects_invalid_variable_override(client):
    client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"})
    resp = client.post("/api/configs", json={
        "name": "dev-bad-var", "env_name": "dev",
        "config_data": {"variables": {"run_date": "not-a-date"}},
    })
    assert resp.status_code == 422


def test_create_config_accepts_valid_variable_override(client):
    client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"})
    resp = client.post("/api/configs", json={
        "name": "dev-good-var", "env_name": "dev",
        "config_data": {"variables": {"run_date": "2026-09-08"}},
    })
    assert resp.status_code == 201


def test_create_config_ignores_override_for_unknown_variable(client):
    resp = client.post("/api/configs", json={
        "name": "dev-unknown-var", "env_name": "dev",
        "config_data": {"variables": {"ghost": "anything"}},
    })
    assert resp.status_code == 201


def test_update_config_rejects_invalid_variable_override(client):
    client.post("/api/variables", json={"name": "batch_id", "var_type": "alphanumeric", "default_value": None})
    created = client.post("/api/configs", json={"name": "dev-update-var", "env_name": "dev", "config_data": {}}).json()
    resp = client.put(f"/api/configs/{created['id']}", json={"config_data": {"variables": {"batch_id": "not valid!"}}})
    assert resp.status_code == 422
