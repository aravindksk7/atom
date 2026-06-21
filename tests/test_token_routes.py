"""Integration tests for POST/GET/DELETE /api/tokens routes.

The middleware (BearerTokenMiddleware) uses etl_framework.repository.database.SessionLocal
directly. We patch it at module level so both the middleware and the route dependency
use the same in-memory SQLite database.
"""
import etl_framework.repository.models  # noqa: F401 — registers ORM models with Base
import etl_framework.repository.database as _db_module
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from api.main import app
from api.dependencies import get_session
from fastapi.testclient import TestClient

# --- shared in-memory engine (StaticPool keeps all connections on the same DB) ---
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(engine)
_TestingSession = sessionmaker(bind=engine)

# --- patch the module-level SessionLocal so middleware uses the same in-memory DB ---
_db_module.SessionLocal = _TestingSession

# --- override the FastAPI dependency for route handlers ---
def override_session():
    db = _TestingSession()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_session] = override_session
client = TestClient(app, raise_server_exceptions=False)

_admin_token = None
_regular_token = None


def test_1_bootstrap_creates_admin_token():
    global _admin_token
    resp = client.post("/api/tokens", json={"name": "bootstrap-admin"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_admin"] is True
    assert len(data["token_hint"]) == 8
    assert data["raw_token"].startswith("etl_")
    _admin_token = data["raw_token"]


def test_2_list_requires_auth():
    resp = client.get("/api/tokens")
    assert resp.status_code == 401


def test_3_create_regular_token_with_admin():
    global _regular_token
    resp = client.post(
        "/api/tokens",
        json={"name": "ci-runner", "is_admin": False},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_admin"] is False
    assert len(data["token_hint"]) == 8
    _regular_token = data["raw_token"]


def test_4_list_forbidden_for_regular_token():
    resp = client.get("/api/tokens", headers={"Authorization": f"Bearer {_regular_token}"})
    assert resp.status_code == 403


def test_5_list_allowed_for_admin_token():
    resp = client.get("/api/tokens", headers={"Authorization": f"Bearer {_admin_token}"})
    assert resp.status_code == 200
    tokens = resp.json()
    assert len(tokens) >= 2
    for t in tokens:
        assert "token_hint" in t
        assert "is_admin" in t


def test_6_revoke_requires_admin():
    resp = client.delete("/api/tokens/1", headers={"Authorization": f"Bearer {_regular_token}"})
    assert resp.status_code == 403
