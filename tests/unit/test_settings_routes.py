"""The settings endpoint's contract for the BO download directory."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def admin_client(monkeypatch):
    from api.main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test", is_admin=True)
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


@pytest.fixture
def plain_client(monkeypatch):
    """A non-admin token — PUT /api/settings must refuse it."""
    from api.main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("plain")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


def test_get_settings_reports_the_bo_download_dir(admin_client):
    resp = admin_client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["bo_download_dir"] == ""


def test_put_settings_sets_the_bo_download_dir(admin_client, tmp_path):
    resp = admin_client.put("/api/settings", json={"bo_download_dir": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["bo_download_dir"] == str(tmp_path)


def test_put_settings_rejects_a_bad_bo_download_dir(admin_client, tmp_path):
    """A ValueError from the repository must surface as 422, the same as an
    unknown timezone already does."""
    resp = admin_client.put(
        "/api/settings", json={"bo_download_dir": str(tmp_path / "nope")})
    assert resp.status_code == 422
    assert "does not exist" in resp.text


def test_put_settings_leaves_the_timezone_alone(admin_client, tmp_path):
    """Sending only bo_download_dir must not disturb the other settings —
    update_settings has a branch that reads the row when timezone is absent."""
    admin_client.put("/api/settings", json={"timezone": "Australia/Sydney"})
    resp = admin_client.put("/api/settings", json={"bo_download_dir": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "Australia/Sydney"
    assert resp.json()["bo_download_dir"] == str(tmp_path)


def test_put_settings_clears_the_bo_download_dir(admin_client, tmp_path):
    """Empty is the documented off-switch, and the one branch that skips
    validation entirely — `if body.bo_download_dir:` instead of `is not None`
    would break it with every other test still green."""
    admin_client.put("/api/settings", json={"bo_download_dir": str(tmp_path)})
    resp = admin_client.put("/api/settings", json={"bo_download_dir": ""})
    assert resp.status_code == 200
    assert resp.json()["bo_download_dir"] == ""
    assert admin_client.get("/api/settings").json()["bo_download_dir"] == ""


def test_put_settings_requires_an_admin_token(plain_client, tmp_path):
    resp = plain_client.put("/api/settings", json={"bo_download_dir": str(tmp_path)})
    assert resp.status_code == 403
