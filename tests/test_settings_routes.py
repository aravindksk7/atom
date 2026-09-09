"""Integration tests for GET/PUT /api/settings routes."""
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
def settings_client():
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


def test_1_bootstrap_creates_admin_token(settings_client):
    global _admin_token
    resp = settings_client.post("/api/tokens", json={"name": "bootstrap-admin"})
    assert resp.status_code == 201
    _admin_token = resp.json()["raw_token"]


def test_2_create_regular_token(settings_client):
    global _regular_token
    resp = settings_client.post(
        "/api/tokens",
        json={"name": "regular", "is_admin": False},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 201
    _regular_token = resp.json()["raw_token"]


_SMTP_DEFAULTS = {
    "smtp_host": "", "smtp_port": 587, "smtp_from": "", "smtp_user": "",
    "smtp_password_set": False, "smtp_use_tls": True,
}


def test_3_get_settings_defaults_to_utc(settings_client):
    resp = settings_client.get("/api/settings", headers={"Authorization": f"Bearer {_regular_token}"})
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "UTC", "upload_retention_days": 30, "bo_download_dir": "", **_SMTP_DEFAULTS}


def test_4_put_settings_requires_admin(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={"timezone": "America/New_York"},
        headers={"Authorization": f"Bearer {_regular_token}"},
    )
    assert resp.status_code == 403


def test_5_put_settings_rejects_invalid_zone(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={"timezone": "Not/AZone"},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 422


def test_6_put_settings_persists_as_admin(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={"timezone": "America/New_York"},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "America/New_York", "upload_retention_days": 30, "bo_download_dir": "", **_SMTP_DEFAULTS}

    get_resp = settings_client.get("/api/settings", headers={"Authorization": f"Bearer {_regular_token}"})
    assert get_resp.json() == {"timezone": "America/New_York", "upload_retention_days": 30, "bo_download_dir": "", **_SMTP_DEFAULTS}


def test_7_put_settings_updates_upload_retention(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={"upload_retention_days": 14},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"timezone": "America/New_York", "upload_retention_days": 14, "bo_download_dir": "", **_SMTP_DEFAULTS}


def test_8_put_settings_updates_smtp_config(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={
            "smtp_host": "mail.corp.local", "smtp_port": 2525, "smtp_from": "etl@corp.local",
            "smtp_user": "svc-etl", "smtp_password": "hunter2", "smtp_use_tls": True,
        },
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["smtp_host"] == "mail.corp.local"
    assert body["smtp_port"] == 2525
    assert body["smtp_from"] == "etl@corp.local"
    assert body["smtp_user"] == "svc-etl"
    assert body["smtp_password_set"] is True
    assert "smtp_password" not in body  # never echoed back

    get_resp = settings_client.get("/api/settings", headers={"Authorization": f"Bearer {_regular_token}"})
    assert get_resp.json()["smtp_host"] == "mail.corp.local"


def test_9_put_settings_rejects_bad_smtp_port(settings_client):
    resp = settings_client.put(
        "/api/settings",
        json={"smtp_port": 99999},
        headers={"Authorization": f"Bearer {_admin_token}"},
    )
    assert resp.status_code == 422
