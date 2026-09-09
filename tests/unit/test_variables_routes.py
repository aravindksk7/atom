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


def test_create_and_list(client):
    resp = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today-1"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "run_date"

    listed = client.get("/api/variables")
    assert listed.status_code == 200
    assert any(v["name"] == "run_date" for v in listed.json())


def test_create_duplicate_name_rejected(client):
    client.post("/api/variables", json={"name": "batch_id", "var_type": "text", "default_value": None})
    resp = client.post("/api/variables", json={"name": "batch_id", "var_type": "text", "default_value": None})
    assert resp.status_code == 409


def test_create_invalid_value_rejected(client):
    resp = client.post("/api/variables", json={"name": "batch_id", "var_type": "number", "default_value": "abc"})
    assert resp.status_code == 422


def test_update(client):
    created = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"}).json()
    resp = client.put(f"/api/variables/{created['id']}", json={"default_value": "today-1"})
    assert resp.status_code == 200
    assert resp.json()["default_value"] == "today-1"


def test_update_default_value_only_validated_against_existing_var_type(client):
    created = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"}).json()
    resp = client.put(f"/api/variables/{created['id']}", json={"default_value": "not-a-date"})
    assert resp.status_code == 422


def test_update_var_type_alone_revalidated_against_existing_default_value(client):
    created = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"}).json()
    resp = client.put(f"/api/variables/{created['id']}", json={"var_type": "number"})
    assert resp.status_code == 422


def test_update_both_var_type_and_default_value_together(client):
    created = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"}).json()
    resp = client.put(f"/api/variables/{created['id']}", json={"var_type": "date", "default_value": "today"})
    assert resp.status_code == 200


def test_update_race_deleted_between_get_and_update_returns_404(client, monkeypatch):
    from etl_framework.repository.repository import CustomVariableRepository

    created = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"}).json()
    # Simulate another request deleting the row between our existence check
    # (repo.get) and the actual update (repo.update, which re-fetches
    # internally) -- repo.update should be treated as authoritative on
    # whether the row still exists.
    monkeypatch.setattr(CustomVariableRepository, "update", lambda self, *a, **k: None)
    resp = client.put(f"/api/variables/{created['id']}", json={"description": "x"})
    assert resp.status_code == 404


def test_update_missing_404(client):
    resp = client.put("/api/variables/999999", json={"description": "x"})
    assert resp.status_code == 404


def test_delete(client):
    created = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"}).json()
    resp = client.delete(f"/api/variables/{created['id']}")
    assert resp.status_code == 204
    assert client.get("/api/variables").json() == [] or all(v["id"] != created["id"] for v in client.get("/api/variables").json())


def test_delete_missing_404(client):
    resp = client.delete("/api/variables/999999")
    assert resp.status_code == 404
