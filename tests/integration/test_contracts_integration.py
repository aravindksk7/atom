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


# ---------------------------------------------------------------------------
# Manual breach resolve
# ---------------------------------------------------------------------------

def _seed_contract_with_breach(c, engine, name="resolve_c"):
    from etl_framework.repository.contract_repository import ContractRepository
    resp = c.post("/api/contracts", json={
        "name": name, "source_job": f"{name}_job", "owner": "o@co.com", "sla_hours": 4.0,
    })
    assert resp.status_code == 201, resp.text
    with Session(engine) as db:
        breach = ContractRepository(db).open_breach(resp.json()["id"], "run-x", "dq_violation")
        return breach.id


def test_resolve_breach_route(client):
    c, engine = client
    breach_id = _seed_contract_with_breach(c, engine)

    resp = c.post(f"/api/contracts/resolve_c/breaches/{breach_id}/resolve",
                  json={"note": "upstream fixed", "resolved_by": "alice"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved_at"] is not None
    assert body["resolved_by"] == "alice"
    assert body["resolution_note"] == "upstream fixed"
    assert c.get("/api/contracts/resolve_c/status").json()["status"] == "OK"

    again = c.post(f"/api/contracts/resolve_c/breaches/{breach_id}/resolve", json={"note": "x"})
    assert again.status_code == 409


def test_resolve_breach_route_blank_note_422(client):
    c, engine = client
    breach_id = _seed_contract_with_breach(c, engine)
    resp = c.post(f"/api/contracts/resolve_c/breaches/{breach_id}/resolve", json={"note": "   "})
    assert resp.status_code == 422


def test_resolve_breach_route_404s(client):
    c, engine = client
    _seed_contract_with_breach(c, engine)
    assert c.post("/api/contracts/resolve_c/breaches/99999/resolve", json={"note": "x"}).status_code == 404
    assert c.post("/api/contracts/missing/breaches/1/resolve", json={"note": "x"}).status_code == 404


def test_resolve_all_route(client):
    c, engine = client
    _seed_contract_with_breach(c, engine)
    resp = c.post("/api/contracts/resolve_c/breaches/resolve-all", json={"note": "bulk"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"resolved": 1}
    assert c.post("/api/contracts/resolve_c/breaches/resolve-all", json={"note": "bulk"}).json() == {"resolved": 0}
    assert c.post("/api/contracts/missing/breaches/resolve-all", json={"note": "x"}).status_code == 404


# ---------------------------------------------------------------------------
# YAML export / import
# ---------------------------------------------------------------------------

def _create(c, name, source_job):
    resp = c.post("/api/contracts", json={
        "name": name, "source_job": source_job, "owner": "o@co.com", "sla_hours": 4.0, "consumers": ["bi"],
    })
    assert resp.status_code == 201, resp.text


def test_export_all_and_by_name(client):
    import yaml
    c, _ = client
    _create(c, "exp_a", "job_a")
    _create(c, "exp_b", "job_b")

    resp = c.get("/api/contracts/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-yaml")
    assert 'filename="contracts.yaml"' in resp.headers["content-disposition"]
    assert [i["name"] for i in yaml.safe_load(resp.text)["contracts"]] == ["exp_a", "exp_b"]

    one = c.get("/api/contracts/export", params={"name": "exp_b"})
    assert [i["name"] for i in yaml.safe_load(one.text)["contracts"]] == ["exp_b"]

    assert c.get("/api/contracts/export", params={"name": "missing"}).status_code == 404


def test_import_round_trip_and_apply(client):
    import yaml
    c, _ = client
    _create(c, "imp_a", "job_a")
    exported = c.get("/api/contracts/export").text

    preview = c.post("/api/contracts/import", json={"yaml": exported})
    assert preview.status_code == 200, preview.text
    assert preview.json() == {"dry_run": True, "applied": False,
                              "rows": [{"name": "imp_a", "action": "unchanged"}]}

    parsed = yaml.safe_load(exported)
    parsed["contracts"].append({"name": "imp_new", "source_job": "job_new", "owner": "n@co.com", "sla_hours": 2})
    doc = yaml.safe_dump(parsed)
    applied = c.post("/api/contracts/import", json={"yaml": doc, "dry_run": False})
    assert applied.json()["applied"] is True
    assert c.get("/api/contracts/imp_new").status_code == 200


def test_import_apply_blocked_by_error_row(client):
    c, _ = client
    _create(c, "imp_b", "job_b")
    doc = ("contracts:\n"
           "  - {name: imp_b, source_job: other, owner: o, sla_hours: 1}\n"
           "  - {name: imp_c, source_job: job_c, owner: o, sla_hours: 1}\n")
    resp = c.post("/api/contracts/import", json={"yaml": doc, "dry_run": False})
    assert resp.status_code == 200
    assert resp.json()["applied"] is False
    assert resp.json()["rows"][0]["action"] == "error"
    assert c.get("/api/contracts/imp_c").status_code == 404


def test_import_invalid_yaml_422(client):
    c, _ = client
    resp = c.post("/api/contracts/import", json={"yaml": "contracts: ["})
    assert resp.status_code == 422
    assert "Invalid YAML" in resp.json()["detail"]
