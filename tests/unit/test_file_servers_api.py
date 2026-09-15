from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import SavedJob
from etl_framework.repository.repository import FileServerProfileRepository, TokenRepository
from api.main import app


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        # Exposed so tests that need direct DB access (inserting a SavedJob
        # row, reading back through the repository) can open their own
        # Session on the same in-memory engine the app is using -- mirrors
        # the `client, engine = client` convention in
        # tests/integration/test_contracts_testing_api.py, but attached as
        # an attribute so the existing client.post(...)-style calls above
        # don't need to change.
        c.engine = engine
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def encryption_key(monkeypatch):
    """secret_store.py reads WEBHOOK_ENCRYPTION_KEY at import time and stores
    secrets as plaintext (with a warning) when it's unset -- see its module
    docstring. Without this fixture, a test asserting real encryption/decryption
    round-trips would silently exercise the plaintext fallback instead (mirrors
    the `_encryption_key` fixture in tests/unit/test_file_server_profile_repository.py)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)
    yield key
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    importlib.reload(secret_store)


def test_create_file_server_masks_secret_on_response(client):
    resp = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal",
        "port": 22, "username": "svc", "auth_method": "password", "password": "hunter2",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["password"] == "********"
    assert data["host"] == "sftp.internal"


def test_list_file_servers_masks_secrets(client):
    client.post("/api/file-servers", json={"name": "s3_prod", "kind": "s3", "aws_secret_access_key": "s3cr3t"})
    resp = client.get("/api/file-servers")
    assert resp.status_code == 200
    [entry] = resp.json()
    assert entry["aws_secret_access_key"] == "********"


def test_update_preserves_masked_secret(client):
    created = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal",
        "port": 22, "username": "svc", "auth_method": "password", "password": "hunter2",
    }).json()

    resp = client.put(f"/api/file-servers/{created['id']}", json={
        "host": "sftp2.internal", "password": created["password"],  # echoes the mask back, like the real GUI would
    })
    assert resp.status_code == 200
    assert resp.json()["host"] == "sftp2.internal"
    assert resp.json()["password"] == "********"


def test_delete_file_server(client):
    created = client.post("/api/file-servers", json={"name": "s3_prod", "kind": "s3"}).json()
    resp = client.delete(f"/api/file-servers/{created['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/file-servers/{created['id']}").status_code == 404


def test_delete_unknown_file_server_404s(client):
    assert client.delete("/api/file-servers/999").status_code == 404


def test_create_rejects_unknown_kind(client):
    resp = client.post("/api/file-servers", json={"name": "x", "kind": "ftp"})
    assert resp.status_code == 422


def test_get_unknown_file_server_404s(client):
    assert client.get("/api/file-servers/999").status_code == 404


def test_delete_blocked_when_referenced_by_saved_job(client):
    created = client.post("/api/file-servers", json={"name": "sftp_inbound", "kind": "sftp"}).json()

    with Session(client.engine) as db:
        db.add(SavedJob(
            name="job_using_profile",
            query="SELECT 1",
            params={"source": {"credentials_ref": "sftp_inbound"}},
        ))
        db.commit()

    resp = client.delete(f"/api/file-servers/{created['id']}")
    assert resp.status_code == 409
    assert client.get(f"/api/file-servers/{created['id']}").status_code == 200


def test_update_preserving_masked_secret_does_not_corrupt_stored_value(client, encryption_key):
    created = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal",
        "port": 22, "username": "svc", "auth_method": "password", "password": "hunter2",
    }).json()

    resp = client.put(f"/api/file-servers/{created['id']}", json={
        "host": "sftp2.internal", "password": created["password"],  # echoes the mask back
    })
    assert resp.status_code == 200

    with Session(client.engine) as db:
        resolved = FileServerProfileRepository(db).get_decrypted_by_name("sftp_inbound")
    assert resolved is not None
    assert resolved.password == "hunter2"  # not double-encrypted garbage
    assert resolved.host == "sftp2.internal"


def test_create_rejects_duplicate_name(client):
    first = client.post("/api/file-servers", json={"name": "s3_prod", "kind": "s3"})
    assert first.status_code == 201

    resp = client.post("/api/file-servers", json={"name": "s3_prod", "kind": "s3"})
    assert resp.status_code == 409
