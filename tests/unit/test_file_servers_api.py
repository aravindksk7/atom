from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository
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
        yield c
    app.dependency_overrides.clear()


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
