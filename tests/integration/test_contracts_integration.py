"""Integration tests: Data Contracts API lifecycle via HTTP."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.main import app
from api.dependencies import get_session
from etl_framework.repository.database import Base, get_db
import etl_framework.repository.models  # noqa: F401
import etl_framework.repository.contract_models  # noqa: F401 — register contract tables
from etl_framework.repository.repository import TokenRepository
import etl_framework.repository.database as _db_module


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

    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_session
    app.dependency_overrides[get_session] = override_session

    with Session(engine) as db:
        raw_token, _ = TokenRepository(db).create("test-token")

    with TestClient(app, headers={"Authorization": f"Bearer {raw_token}"}, raise_server_exceptions=True) as c:
        yield c, engine

    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)


# ---------------------------------------------------------------------------
# Test 1: CRUD lifecycle
# ---------------------------------------------------------------------------

def test_contract_crud_lifecycle(client):
    c, _ = client

    # Create
    resp = c.post("/api/contracts", json={
        "name": "orders_contract",
        "source_job": "orders_etl",
        "owner": "data@co.com",
        "sla_hours": 4.0,
        "consumers": ["finance", "ops"],
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "orders_contract"
    assert data["consumers"] == ["finance", "ops"]
    assert data["version"] == "1.0"

    # Get
    resp = c.get("/api/contracts/orders_contract")
    assert resp.status_code == 200
    assert resp.json()["owner"] == "data@co.com"

    # List
    resp = c.get("/api/contracts")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Update
    resp = c.put("/api/contracts/orders_contract", json={"owner": "new@co.com", "sla_hours": 8.0})
    assert resp.status_code == 200
    assert resp.json()["owner"] == "new@co.com"
    assert resp.json()["sla_hours"] == 8.0

    # Delete
    resp = c.delete("/api/contracts/orders_contract")
    assert resp.status_code == 204

    # Confirm gone
    resp = c.get("/api/contracts/orders_contract")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 2: Breach status lifecycle via API
# ---------------------------------------------------------------------------

def test_contract_breach_status_lifecycle(client):
    c, engine = client
    from etl_framework.repository.contract_repository import ContractRepository

    # Create contract via API
    resp = c.post("/api/contracts", json={
        "name": "payments_contract",
        "source_job": "payments_etl",
        "owner": "team@co.com",
        "sla_hours": 2.0,
    })
    assert resp.status_code == 201

    # Initially OK
    resp = c.get("/api/contracts/payments_contract/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "OK"

    # Open a breach directly via repo (simulating a failed run)
    with Session(engine) as db:
        repo = ContractRepository(db)
        contract = repo.get("payments_contract")
        repo.open_breach(contract.id, "run-abc-001", "dq_violation")

    # Now status should be BREACHED
    resp = c.get("/api/contracts/payments_contract/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "BREACHED"
    assert resp.json()["open_breach"]["breach_type"] == "dq_violation"

    # Breach history
    resp = c.get("/api/contracts/payments_contract/breaches")
    assert resp.status_code == 200
    breaches = resp.json()
    assert len(breaches) == 1
    assert breaches[0]["run_id"] == "run-abc-001"

    # Resolve breach via repo (simulating a passing run)
    with Session(engine) as db:
        ContractRepository(db).resolve_breaches_for_job("payments_etl", "run-abc-002")

    # Now OK again
    resp = c.get("/api/contracts/payments_contract/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "OK"

    # Breach history now shows resolved breach with duration
    resp = c.get("/api/contracts/payments_contract/breaches")
    assert resp.json()[0]["resolved_at"] is not None
    assert resp.json()[0]["resolution_run_id"] == "run-abc-002"


# ---------------------------------------------------------------------------
# Test 3: Version bump lifecycle
# ---------------------------------------------------------------------------

def test_contract_version_bump_lifecycle(client):
    c, _ = client

    c.post("/api/contracts", json={
        "name": "inventory_contract",
        "source_job": "inventory_etl",
        "owner": "team@co.com",
        "sla_hours": 6.0,
    })

    # Bump minor
    resp = c.post("/api/contracts/inventory_contract/bump", json={"bump_type": "minor", "note": "added freshness rule"})
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.1"
    assert resp.json()["bump_type"] == "minor"

    # Bump major
    resp = c.post("/api/contracts/inventory_contract/bump", json={"bump_type": "major"})
    assert resp.status_code == 200
    assert resp.json()["version"] == "2.0"

    # Version history
    resp = c.get("/api/contracts/inventory_contract/versions")
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) == 2
    assert versions[0]["version"] == "2.0"  # ordered desc
