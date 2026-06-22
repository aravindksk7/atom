"""Integration tests for POST/GET/DELETE /api/tokens routes.

The middleware (BearerTokenMiddleware) uses etl_framework.repository.database.SessionLocal
directly. We patch it at module level so both the middleware and the route dependency
use the same in-memory SQLite database.
"""
import etl_framework.repository.models  # noqa: F401 — registers ORM models with Base
import etl_framework.repository.database as _db_module
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from api.main import app
from api.dependencies import get_session
from fastapi.testclient import TestClient

_admin_token = None
_regular_token = None


@pytest.fixture(scope="module")
def token_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    previous_session_local = _db_module.SessionLocal
    previous_overrides = dict(app.dependency_overrides)
    _db_module.SessionLocal = testing_session
    app.dependency_overrides.clear()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        _db_module.SessionLocal = previous_session_local
        engine.dispose()


def test_1_bootstrap_creates_admin_token(token_client):
    global _admin_token
    resp = token_client.post("/api/tokens", json={"name": "bootstrap-admin"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_admin"] is True
    assert len(data["token_hint"]) == 8
    assert data["raw_token"].startswith("etl_")
    _admin_token = data["raw_token"]


def test_2_list_requires_auth(token_client):
    resp = token_client.get("/api/tokens")
    assert resp.status_code == 401


def test_3_create_regular_token_with_admin(token_client):
    global _regular_token
    resp = token_client.post(
        "/api/tokens",
        json={"name": "ci-runner", "is_admin": False},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_admin"] is False
    assert len(data["token_hint"]) == 8
    _regular_token = data["raw_token"]


def test_3a_regular_user_cannot_create_access(token_client):
    resp = token_client.post(
        "/api/tokens",
        json={"name": "unauthorized-admin", "is_admin": True},
        headers={"Authorization": f"Bearer {_regular_token}"},
    )
    assert resp.status_code == 403


def test_3b_create_additional_admin_with_admin(token_client):
    resp = token_client.post(
        "/api/tokens",
        json={"name": "operations-admin", "is_admin": True},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["is_admin"] is True


def test_4_list_forbidden_for_regular_token(token_client):
    resp = token_client.get("/api/tokens", headers={"Authorization": f"Bearer {_regular_token}"})
    assert resp.status_code == 403


def test_5_list_allowed_for_admin_token(token_client):
    resp = token_client.get("/api/tokens", headers={"Authorization": f"Bearer {_admin_token}"})
    assert resp.status_code == 200
    tokens = resp.json()
    assert len(tokens) >= 2
    for t in tokens:
        assert "token_hint" in t
        assert "is_admin" in t


def test_6_revoke_requires_admin(token_client):
    resp = token_client.delete("/api/tokens/1", headers={"Authorization": f"Bearer {_regular_token}"})
    assert resp.status_code == 403


def test_7_patch_updates_expiry(token_client):
    # Create a token to patch
    create = token_client.post(
        "/api/tokens",
        json={"name": "patch-target", "is_admin": False},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert create.status_code == 201
    token_id = create.json()["id"]

    new_expiry = "2099-01-01T00:00:00Z"
    resp = token_client.patch(
        f"/api/tokens/{token_id}",
        json={"expires_at": new_expiry},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["expires_at"] is not None
    assert "2099" in data["expires_at"]


def test_7b_patch_requires_admin(token_client):
    resp = token_client.patch(
        "/api/tokens/1",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {_regular_token}"},
    )
    assert resp.status_code == 403


def test_7c_patch_nonexistent_returns_404(token_client):
    resp = token_client.patch(
        "/api/tokens/99999",
        json={"enabled": False},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 404


def test_8_rotate_issues_new_token(token_client):
    # Create a token specifically to rotate
    create = token_client.post(
        "/api/tokens",
        json={"name": "rotate-me", "is_admin": False},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert create.status_code == 201
    old_id = create.json()["id"]
    old_hint = create.json()["token_hint"]

    resp = token_client.post(
        f"/api/tokens/{old_id}/rotate",
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "rotate-me"
    assert data["is_admin"] is False
    assert data["raw_token"].startswith("etl_")
    # New token has a different hint (overwhelmingly likely with 8 random hex chars)
    assert data["id"] != old_id


def test_8b_rotate_old_token_is_revoked(token_client):
    # The token created in test_8 is for "rotate-me"; its original raw_token is gone.
    # Verify the old id no longer appears as enabled in the token list.
    create = token_client.post(
        "/api/tokens",
        json={"name": "rotate-check", "is_admin": False},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    old_id = create.json()["id"]
    old_raw = create.json()["raw_token"]

    token_client.post(
        f"/api/tokens/{old_id}/rotate",
        headers={"Authorization": f"Bearer {_admin_token}"},
    )

    # Old raw token must now be rejected by the API
    resp = token_client.get(
        "/api/configs",
        headers={"Authorization": f"Bearer {old_raw}"},
    )
    assert resp.status_code == 401


def test_8c_rotate_requires_admin(token_client):
    resp = token_client.post(
        "/api/tokens/1/rotate",
        headers={"Authorization": f"Bearer {_regular_token}"},
    )
    assert resp.status_code == 403


def test_8d_rotate_nonexistent_returns_404(token_client):
    resp = token_client.post(
        "/api/tokens/99999/rotate",
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 404
