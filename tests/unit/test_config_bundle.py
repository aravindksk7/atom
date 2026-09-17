# tests/unit/test_config_bundle.py
from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401 -- registers ORM models with Base
from etl_framework.repository.repository import ConfigRepository, FileServerProfileRepository
from api.services.config_bundle import BUNDLE_VERSION, build_bundle


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)
    yield key
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    importlib.reload(secret_store)


def _db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_build_bundle_sets_version_and_empty_lists_for_unselected_types():
    db = _db()
    bundle = build_bundle(db, {})
    assert bundle["bundle_version"] == BUNDLE_VERSION
    assert bundle["file_servers"] == []
    assert bundle["configs"] == []
    assert bundle["jobs"] == []
    assert bundle["sequences"] == []
    assert bundle["selections"] == []


def test_build_bundle_strips_config_secrets():
    db = _db()
    ConfigRepository(db).create(
        name="dev", env_name="dev",
        config_data={"db_host": "localhost", "db_password": "hunter2"},
    )
    bundle = build_bundle(db, {"configs": ["dev"]})
    assert len(bundle["configs"]) == 1
    entry = bundle["configs"][0]
    assert entry["name"] == "dev"
    assert entry["env_name"] == "dev"
    assert entry["config_data"]["db_host"] == "localhost"
    assert entry["config_data"]["db_password"] is None


def test_build_bundle_strips_file_server_secrets():
    db = _db()
    FileServerProfileRepository(db).create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })
    bundle = build_bundle(db, {"file_servers": ["sftp_inbound"]})
    assert len(bundle["file_servers"]) == 1
    entry = bundle["file_servers"][0]
    assert entry["name"] == "sftp_inbound"
    assert entry["host"] == "sftp.internal"
    assert entry["password"] is None


def test_build_bundle_skips_unknown_names_silently():
    db = _db()
    bundle = build_bundle(db, {"configs": ["does-not-exist"]})
    assert bundle["configs"] == []
