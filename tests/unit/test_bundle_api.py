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
from etl_framework.repository.repository import (
    ConfigRepository,
    FileServerProfileRepository,
    JobRepository,
    JobSelectionRepository,
    TokenRepository,
)
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
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
        c.engine = engine
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)
    yield key
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    importlib.reload(secret_store)


def test_list_bundle_items_returns_configs(client):
    with Session(client.engine) as db:
        ConfigRepository(db).create(name="dev", env_name="dev", config_data={})
    resp = client.get("/api/bundle/list")
    assert resp.status_code == 200
    assert {"type": "configs", "name": "dev"} in resp.json()


def test_list_bundle_items_returns_all_entity_types(client):
    with Session(client.engine) as db:
        FileServerProfileRepository(db).create({"name": "fs1", "kind": "sftp"})
        ConfigRepository(db).create(name="dev", env_name="dev", config_data={})
        JobRepository(db).create({"name": "job1"})
        ExecutionSequenceRepository(db).create(name="seq1", description="", tags=[], steps=[])
        JobSelectionRepository(db).create(
            name="sel1", description="", tags=[], job_sequence=[], run_settings={},
        )

    resp = client.get("/api/bundle/list")
    assert resp.status_code == 200
    items = resp.json()
    assert {"type": "file_servers", "name": "fs1"} in items
    assert {"type": "configs", "name": "dev"} in items
    assert {"type": "jobs", "name": "job1"} in items
    assert {"type": "sequences", "name": "seq1"} in items
    assert {"type": "selections", "name": "sel1"} in items


def test_import_into_clean_db_creates_records(client):
    with Session(client.engine) as db:
        ConfigRepository(db).create(name="dev", env_name="dev", config_data={"db_host": "localhost"})

    export_resp = client.post("/api/bundle/export", json={"selection": {"configs": ["dev"]}})
    assert export_resp.status_code == 200
    bundle = export_resp.json()

    with Session(client.engine) as db:
        repo = ConfigRepository(db)
        existing = repo.get_by_name("dev")
        repo.delete(existing.id)

    import_resp = client.post("/api/bundle/import", json=bundle)
    assert import_resp.status_code == 200
    assert import_resp.json() == [{"type": "configs", "name": "dev", "status": "created", "reason": None}]

    with Session(client.engine) as db:
        assert ConfigRepository(db).get_by_name("dev") is not None


def test_export_then_import_round_trip_skips_everything(client):
    with Session(client.engine) as db:
        ConfigRepository(db).create(name="dev", env_name="dev", config_data={"db_host": "localhost"})

    export_resp = client.post("/api/bundle/export", json={"selection": {"configs": ["dev"]}})
    assert export_resp.status_code == 200
    bundle = export_resp.json()
    assert bundle["configs"][0]["name"] == "dev"

    import_resp = client.post("/api/bundle/import", json=bundle)
    assert import_resp.status_code == 200
    assert import_resp.json() == [{"type": "configs", "name": "dev", "status": "skipped", "reason": "already exists"}]


def test_import_rejects_bad_bundle_version(client):
    resp = client.post("/api/bundle/import", json={"bundle_version": 999})
    assert resp.status_code == 400


def test_export_rejects_unknown_entity_type(client):
    resp = client.post("/api/bundle/export", json={"selection": {"not_a_type": ["x"]}})
    assert resp.status_code == 422
