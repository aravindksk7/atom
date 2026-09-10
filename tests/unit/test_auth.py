"""Tests for API token auth — TokenRepository and BearerTokenMiddleware."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


# ---------------------------------------------------------------------------
# TokenRepository unit tests
# ---------------------------------------------------------------------------

def test_create_returns_raw_token_and_record():
    db = _session()
    raw, token = TokenRepository(db).create("ci-bot")
    assert raw.startswith("etl_")
    assert len(raw) > 10
    assert token.id is not None
    assert token.name == "ci-bot"
    assert token.enabled is True
    assert token.token_hash != raw  # hash stored, not plain


def test_verify_valid_token_returns_record():
    db = _session()
    raw, _ = TokenRepository(db).create("test")
    found = TokenRepository(db).verify(raw)
    assert found is not None
    assert found.name == "test"


def test_verify_wrong_token_returns_none():
    db = _session()
    TokenRepository(db).create("test")
    assert TokenRepository(db).verify("etl_wrongtoken") is None


def test_verify_updates_last_used_at():
    db = _session()
    raw, token = TokenRepository(db).create("test")
    assert token.last_used_at is None
    TokenRepository(db).verify(raw)
    db.refresh(token)
    assert token.last_used_at is not None


def test_revoke_disables_token():
    db = _session()
    raw, token = TokenRepository(db).create("test")
    TokenRepository(db).revoke(token.id)
    assert TokenRepository(db).verify(raw) is None


def test_expired_token_is_rejected():
    db = _session()
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    raw, _ = TokenRepository(db).create("expired", expires_at=past)
    assert TokenRepository(db).verify(raw) is None


def test_future_expiry_is_accepted():
    db = _session()
    future = datetime.now(timezone.utc) + timedelta(days=365)
    raw, _ = TokenRepository(db).create("future", expires_at=future)
    assert TokenRepository(db).verify(raw) is not None


def test_list_returns_all_tokens():
    db = _session()
    TokenRepository(db).create("a")
    TokenRepository(db).create("b")
    tokens = TokenRepository(db).list()
    assert len(tokens) == 2


def test_create_defaults_role_to_full():
    db = _session()
    _, token = TokenRepository(db).create("test")
    assert token.role == "full"


def test_create_accepts_ci_trigger_role():
    db = _session()
    _, token = TokenRepository(db).create("ci-bot", role="ci_trigger")
    assert token.role == "ci_trigger"


# ---------------------------------------------------------------------------
# Middleware integration via TestClient
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with a fresh in-memory DB and auth middleware active."""
    import api.main as main_module
    from etl_framework.repository import database as db_mod

    # Point to temp SQLite
    db_url = f"sqlite:///{tmp_path}/test.db"
    monkeypatch.setenv("ETL_DATABASE_URL", db_url)

    # Rebuild engine with temp DB
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_mod, "engine", engine)
    from sqlalchemy.orm import sessionmaker
    monkeypatch.setattr(db_mod, "SessionLocal", sessionmaker(bind=engine))
    Base.metadata.create_all(engine)

    previous_overrides = dict(main_module.app.dependency_overrides)
    main_module.app.dependency_overrides.clear()

    from api.middleware.auth import _is_exempt  # noqa — import to validate
    try:
        with TestClient(main_module.app, raise_server_exceptions=False) as c:
            yield c, engine
    finally:
        main_module.app.dependency_overrides.clear()
        main_module.app.dependency_overrides.update(previous_overrides)
        engine.dispose()


def test_protected_endpoint_requires_token(client):
    c, _ = client
    resp = c.get("/api/configs")
    assert resp.status_code == 401


def test_health_endpoint_is_exempt(client):
    c, _ = client
    resp = c.get("/api/health")
    assert resp.status_code == 200


def test_valid_token_grants_access(client):
    c, engine = client
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-ci")
    resp = c.get("/api/configs", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200


def test_auth_verify_returns_current_token_actor(client):
    c, engine = client
    with Session(engine) as db:
        raw, token = TokenRepository(db).create("test-ci")
    resp = c.get("/api/auth/verify", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "actor": "test-ci",
        "token_id": token.id,
        "is_admin": False,
    }


def test_setup_status_is_public_and_tracks_bootstrap(client):
    c, _ = client
    before = c.get("/api/auth/setup-status")
    assert before.status_code == 200
    assert before.json() == {"initialized": False}

    created = c.post("/api/tokens", json={"name": "initial-admin"})
    assert created.status_code == 201
    after = c.get("/api/auth/setup-status")
    assert after.status_code == 200
    assert after.json() == {"initialized": True}


def test_auth_verify_rejects_invalid_token(client):
    c, _ = client
    resp = c.get("/api/auth/verify", headers={"Authorization": "Bearer etl_bogus"})
    assert resp.status_code == 401


def test_invalid_token_returns_401(client):
    c, _ = client
    resp = c.get("/api/configs", headers={"Authorization": "Bearer etl_bogus"})
    assert resp.status_code == 401


def test_token_creation_endpoint_is_exempt(client):
    """POST /api/tokens must work without auth (bootstrap)."""
    c, _ = client
    resp = c.post("/api/tokens", json={"name": "bootstrap"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["raw_token"].startswith("etl_")


def test_bootstrap_token_rejects_ci_trigger_role(client):
    c, _ = client
    resp = c.post("/api/tokens", json={"name": "bootstrap", "role": "ci_trigger"})
    assert resp.status_code == 422


def test_token_create_rejects_admin_and_ci_trigger_combo(client):
    c, _ = client
    resp = c.post("/api/tokens", json={"name": "bootstrap"})  # bootstrap admin token
    admin_raw = resp.json()["raw_token"]
    resp = c.post(
        "/api/tokens",
        json={"name": "bad-combo", "is_admin": True, "role": "ci_trigger"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert resp.status_code == 422


def test_token_create_defaults_role_full_in_response(client):
    c, _ = client
    resp = c.post("/api/tokens", json={"name": "bootstrap"})
    assert resp.json()["role"] == "full"


def test_token_create_ci_trigger_role_in_response(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    resp = c.post(
        "/api/tokens",
        json={"name": "ci-bot", "role": "ci_trigger"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "ci_trigger"
    assert resp.json()["is_admin"] is False


def test_rotate_preserves_ci_trigger_role(client):
    c, engine = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    created = c.post(
        "/api/tokens",
        json={"name": "ci-bot", "role": "ci_trigger"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    ).json()
    resp = c.post(
        f"/api/tokens/{created['id']}/rotate",
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "ci_trigger"


def _create_ci_trigger_token(c, admin_raw: str) -> str:
    resp = c.post(
        "/api/tokens",
        json={"name": "ci-bot", "role": "ci_trigger"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    return resp.json()["raw_token"]


def test_ci_trigger_token_allowed_on_selections_list(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    resp = c.get("/api/selections", headers={"Authorization": f"Bearer {ci_raw}"})
    assert resp.status_code == 200


def test_ci_trigger_token_allowed_on_sequences_list(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    resp = c.get("/api/sequences", headers={"Authorization": f"Bearer {ci_raw}"})
    assert resp.status_code == 200


def test_ci_trigger_token_allowed_on_run_status(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    resp = c.get("/api/runs/nonexistent-run-id/status", headers={"Authorization": f"Bearer {ci_raw}"})
    # 404 (route reached, run not found) proves the scope check let it through --
    # a scope denial would be 403, never 404.
    assert resp.status_code == 404


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/configs"),
    ("GET", "/api/jobs"),
    ("GET", "/api/tokens"),
    ("POST", "/api/schedules"),
    ("GET", "/api/runs"),  # bare list -- deliberately excluded, see spec
    ("DELETE", "/api/runs/some-run-id"),
])
def test_ci_trigger_token_denied_outside_allowlist(client, method, path):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    resp = c.request(method, path, headers={"Authorization": f"Bearer {ci_raw}"})
    assert resp.status_code == 403


def test_full_token_unaffected_by_scope_check(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    full_raw = c.post(
        "/api/tokens", json={"name": "full-user"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    ).json()["raw_token"]
    resp = c.get("/api/configs", headers={"Authorization": f"Bearer {full_raw}"})
    assert resp.status_code == 200


def test_ci_trigger_scope_denial_is_audit_logged(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    c.get("/api/configs", headers={"Authorization": f"Bearer {ci_raw}"})
    resp = c.get("/api/audit", headers={"Authorization": f"Bearer {admin_raw}"})
    assert resp.status_code == 200
    reasons = [e.get("diff", {}).get("reason") for e in resp.json() if e.get("action") == "token.auth_failed"]
    assert "ci_trigger_scope_denied" in reasons
