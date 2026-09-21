"""Creating or editing a file_transfer job requires the admin role.

file_transfer jobs write to file-server destinations and to allowlisted local
folders, unlike every other saved job type, which only reads. A plain
(non-admin) "full" token can still read, launch and delete them; it just
cannot author one.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import runs as runs_module
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401 -- registers ORM models with Base
from etl_framework.repository.repository import JobRepository, TokenRepository

DENIED = "requires the admin role"

FILE_TRANSFER_PARAMS = {
    "source": {"kind": "local", "root": "/data/in", "pattern": "SALES_*.csv"},
    "destination": {"kind": "local", "root": "/data/out"},
    "on_exists": "skip",
}


def _file_transfer_payload(name: str = "stage_sales") -> dict:
    return {
        "name": name,
        "job_type": "file_transfer",
        "query": "",
        "key_columns": [],
        "params": dict(FILE_TRANSFER_PARAMS),
    }


def _watcher_payload(name: str = "watch_sales") -> dict:
    return {
        "name": name,
        "job_type": "file_watcher",
        "query": "",
        "key_columns": [],
        "params": {
            "location": {"kind": "local", "root": "/data/in", "pattern": "SALES_*.csv"},
            "max_tries": 5,
            "poll_interval_seconds": 10,
        },
    }


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    return engine


def _client(engine, *, is_admin: bool) -> TestClient:
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create(
            "admin" if is_admin else "plain", is_admin=is_admin,
        )
    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


@pytest.fixture
def admin_client(engine):
    with _client(engine, is_admin=True) as c:
        yield c


@pytest.fixture
def plain_client(engine):
    with _client(engine, is_admin=False) as c:
        yield c


# -- create ------------------------------------------------------------------

def test_non_admin_cannot_create_a_file_transfer_job(plain_client):
    resp = plain_client.post("/api/jobs", json=_file_transfer_payload())
    assert resp.status_code == 403, resp.text
    assert DENIED in resp.json()["detail"]


def test_admin_can_create_a_file_transfer_job(admin_client):
    resp = admin_client.post("/api/jobs", json=_file_transfer_payload())
    assert resp.status_code == 201, resp.text


def test_non_admin_can_still_create_other_job_types(plain_client):
    assert plain_client.post("/api/jobs", json=_watcher_payload()).status_code == 201
    resp = plain_client.post(
        "/api/jobs",
        json={"name": "orders", "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"]},
    )
    assert resp.status_code == 201, resp.text


# -- update ------------------------------------------------------------------

def test_non_admin_cannot_edit_an_existing_file_transfer_job(engine, admin_client, plain_client):
    assert admin_client.post("/api/jobs", json=_file_transfer_payload()).status_code == 201

    payload = _file_transfer_payload()
    payload["description"] = "edited"
    resp = plain_client.put("/api/jobs/stage_sales", json=payload)

    assert resp.status_code == 403, resp.text
    assert DENIED in resp.json()["detail"]


def test_non_admin_cannot_turn_an_existing_job_into_a_file_transfer_job(plain_client):
    assert plain_client.post("/api/jobs", json=_watcher_payload("becomes_transfer")).status_code == 201

    resp = plain_client.put(
        "/api/jobs/becomes_transfer", json=_file_transfer_payload("becomes_transfer"),
    )

    assert resp.status_code == 403, resp.text
    assert DENIED in resp.json()["detail"]


def test_admin_can_edit_a_file_transfer_job(admin_client):
    assert admin_client.post("/api/jobs", json=_file_transfer_payload()).status_code == 201

    payload = _file_transfer_payload()
    payload["description"] = "edited"
    resp = admin_client.put("/api/jobs/stage_sales", json=payload)

    assert resp.status_code == 200, resp.text
    assert resp.json()["description"] == "edited"


def test_non_admin_can_still_edit_other_job_types(plain_client):
    assert plain_client.post("/api/jobs", json=_watcher_payload()).status_code == 201

    payload = _watcher_payload()
    payload["description"] = "edited"
    resp = plain_client.put("/api/jobs/watch_sales", json=payload)

    assert resp.status_code == 200, resp.text


# -- reading and launching are unchanged -------------------------------------

def test_non_admin_can_read_and_launch_an_existing_file_transfer_job(admin_client, plain_client):
    assert admin_client.post("/api/jobs", json=_file_transfer_payload()).status_code == 201

    listed = plain_client.get("/api/jobs")
    assert listed.status_code == 200
    assert "stage_sales" in [job["name"] for job in listed.json()]

    launched = plain_client.post(
        "/api/runs", json={"source_env": "dev", "job_names": ["stage_sales"]},
    )
    assert launched.status_code == 202, launched.text


# -- bulk import -------------------------------------------------------------

def test_non_admin_cannot_bulk_import_a_file_transfer_job(plain_client, engine):
    resp = plain_client.post("/api/jobs/import", json=[_watcher_payload(), _file_transfer_payload()])

    assert resp.status_code == 403, resp.text
    assert DENIED in resp.json()["detail"]
    with Session(engine) as db:
        assert JobRepository(db).get("stage_sales") is None


def test_admin_can_bulk_import_a_file_transfer_job(admin_client):
    resp = admin_client.post("/api/jobs/import", json=[_file_transfer_payload()])
    assert resp.status_code == 201, resp.text


def test_non_admin_bulk_import_of_other_job_types_still_works(plain_client):
    resp = plain_client.post("/api/jobs/import", json=[_watcher_payload()])
    assert resp.status_code == 201, resp.text


# -- config bundle import ----------------------------------------------------
#
# The bundle route is not admin-only, and reports per-item outcomes, so a
# file_transfer job inside a bundle fails as one item while the rest apply.

def _bundle(*jobs: dict) -> dict:
    from api.services.config_bundle import BUNDLE_VERSION

    return {
        "bundle_version": BUNDLE_VERSION,
        "file_servers": [], "configs": [], "sequences": [], "selections": [],
        "jobs": list(jobs),
    }


def test_non_admin_bundle_import_fails_only_the_file_transfer_item(plain_client, engine):
    resp = plain_client.post(
        "/api/bundle/import", json=_bundle(_watcher_payload(), _file_transfer_payload()),
    )

    assert resp.status_code == 200, resp.text
    by_name = {item["name"]: item for item in resp.json()}
    assert by_name["stage_sales"]["status"] == "error"
    assert DENIED in by_name["stage_sales"]["reason"]
    assert by_name["watch_sales"]["status"] == "created"
    with Session(engine) as db:
        assert JobRepository(db).get("stage_sales") is None
        assert JobRepository(db).get("watch_sales") is not None


def test_admin_bundle_import_creates_the_file_transfer_job(admin_client, engine):
    resp = admin_client.post("/api/bundle/import", json=_bundle(_file_transfer_payload()))

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["status"] == "created"
    with Session(engine) as db:
        assert JobRepository(db).get("stage_sales") is not None
