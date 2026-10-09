from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401 -- registers ORM models with Base
from etl_framework.repository.models import FileServerProfile
from etl_framework.repository.repository import FileServerProfileRepository


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    """secret_store.py reads WEBHOOK_ENCRYPTION_KEY at import time and stores
    secrets as plaintext (with a warning) when it's unset -- see its module
    docstring. Without this fixture these tests would assert real encryption
    happened while silently exercising the plaintext fallback instead
    (mirrors the `encryption_key` fixture in
    tests/unit/test_config_secret_encryption.py)."""
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


def test_create_encrypts_secret_fields_at_rest():
    db = _db()
    repo = FileServerProfileRepository(db)
    repo.create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })

    raw = db.query(FileServerProfile).filter_by(name="sftp_inbound").one()
    assert raw.password != "hunter2"  # stored encrypted, not plaintext


def test_get_by_name_returns_decrypted_view():
    db = _db()
    repo = FileServerProfileRepository(db)
    repo.create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })

    profile = repo.get_decrypted_by_name("sftp_inbound")

    assert profile is not None
    assert profile.password == "hunter2"
    assert profile.host == "sftp.internal"


def test_get_decrypted_by_name_returns_none_for_unknown_name():
    db = _db()
    repo = FileServerProfileRepository(db)
    assert repo.get_decrypted_by_name("does_not_exist") is None


def test_update_preserves_unspecified_fields():
    db = _db()
    repo = FileServerProfileRepository(db)
    created = repo.create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })

    repo.update(created.id, {"host": "sftp2.internal"})

    profile = repo.get_decrypted_by_name("sftp_inbound")
    assert profile.host == "sftp2.internal"
    assert profile.password == "hunter2"  # untouched field survives the update


def test_delete_removes_the_row():
    db = _db()
    repo = FileServerProfileRepository(db)
    created = repo.create({"name": "x", "kind": "s3", "aws_access_key_id": "AKIA"})

    assert repo.delete(created.id) is True
    assert repo.get_decrypted_by_name("x") is None
    assert repo.delete(created.id) is False


def test_crushftp_api_key_is_encrypted_at_rest_and_decrypted_on_resolve():
    db = _db()
    repo = FileServerProfileRepository(db)
    created = repo.create({
        "name": "cloud-crush", "kind": "crushftp", "protocol": "https",
        "endpoint_url": "https://files.example.com", "auth_method": "api_key",
        "api_key_header": "X-Api-Key", "api_key": "s3cr3t-key", "tls_ca_bundle": "PEM",
    })

    assert created.api_key != "s3cr3t-key"  # stored encrypted, not plaintext
    resolved = repo.get_decrypted_by_name("cloud-crush")
    assert (resolved.protocol, resolved.api_key_header, resolved.api_key, resolved.tls_ca_bundle) == (
        "https", "X-Api-Key", "s3cr3t-key", "PEM",
    )
    assert "s3cr3t-key" not in repr(resolved)


def test_ensure_file_server_columns_adds_missing_columns_and_is_idempotent(tmp_path):
    from sqlalchemy import inspect, text

    from etl_framework.repository.database import _ensure_file_server_columns

    engine = create_engine(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE file_server_profiles (id INTEGER PRIMARY KEY, name VARCHAR(255))"))

    _ensure_file_server_columns(engine)
    _ensure_file_server_columns(engine)  # second run must not raise

    columns = {c["name"] for c in inspect(engine).get_columns("file_server_profiles")}
    assert {"protocol", "api_key_header", "api_key", "tls_ca_bundle"} <= columns
    engine.dispose()
