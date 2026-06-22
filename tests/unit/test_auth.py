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
