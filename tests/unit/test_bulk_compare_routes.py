"""/api/jobs/bulk-compare/{template,preview,commit}."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from api.routes import runs as runs_module
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    ConfigRepository, JobRepository, JobSelectionRepository, TokenRepository,
)

HEADER = "job_name,source_config,source_table,target_config,target_table,key_columns"


@pytest.fixture
def engine(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(runs_module, "_execute_run", lambda *args, **kwargs: None)
    with Session(engine) as db:
        base = {"db_host": "h", "db_name": "d", "db_user": "u", "db_password": "p"}
        ConfigRepository(db).create("SQL_PROD", "dev", {**base, "db_type": "mssql"})
        ConfigRepository(db).create("PG_PROD", "dev", {**base, "db_type": "postgresql"})
    return engine


@pytest.fixture
def client(engine):
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("admin", is_admin=True)
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c


def _upload(text: str, filename: str = "map.csv") -> dict:
    return {"filename": filename, "content_b64": base64.b64encode(text.encode()).decode()}


VALID = HEADER + "\ncust_cmp,SQL_PROD,dbo.customer,PG_PROD,public.customer,id\n"


def test_template_downloads_csv(client):
    resp = client.get("/api/jobs/bulk-compare/template")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.text.startswith("job_name,source_config,")


def test_preview_returns_rows_and_saves_nothing(client, engine):
    resp = client.post("/api/jobs/bulk-compare/preview", json=_upload(VALID))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid_count"] == 1 and body["invalid_count"] == 0
    assert body["rows"][0]["job"]["params"]["compare_type"] == "sql"
    with Session(engine) as db:
        assert JobRepository(db).get("cust_cmp") is None


def test_preview_flags_existing_job_as_update(client, engine):
    with Session(engine) as db:
        JobRepository(db).create({"name": "cust_cmp", "job_type": "reconciliation", "query": "SELECT 1",
                                  "key_columns": ["id"], "params": {}})
    rows = client.post("/api/jobs/bulk-compare/preview", json=_upload(VALID)).json()["rows"]
    assert rows[0]["will_update"] is True


def test_preview_file_level_error_is_422(client):
    resp = client.post("/api/jobs/bulk-compare/preview", json=_upload("colour\nred\n"))
    assert resp.status_code == 422
    assert "Unknown column" in resp.json()["detail"]


def test_preview_bad_base64_is_422(client):
    resp = client.post("/api/jobs/bulk-compare/preview", json={"filename": "m.csv", "content_b64": "%%%"})
    assert resp.status_code == 422


def _preview_jobs(client, text=VALID) -> list[dict]:
    return [r["job"] for r in client.post("/api/jobs/bulk-compare/preview", json=_upload(text)).json()["rows"]]


def test_commit_saves_jobs_and_creates_selection(client, engine):
    two = VALID + "ord_cmp,SQL_PROD,dbo.orders,PG_PROD,public.orders,id\n"
    resp = client.post("/api/jobs/bulk-compare/commit",
                       json={"jobs": _preview_jobs(client, two), "selection_name": "nightly_compares"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert [j["name"] for j in body["jobs"]] == ["cust_cmp", "ord_cmp"]
    assert body["selection"]["name"] == "nightly_compares"
    with Session(engine) as db:
        assert JobRepository(db).get("ord_cmp").job_type == "compare"
        selection = JobSelectionRepository(db).get_by_name("nightly_compares")
        version = JobSelectionRepository(db).latest_version(selection.id)
        assert version.job_sequence == ["cust_cmp", "ord_cmp"]
        assert version.run_settings_json["use_live_connections"] is True


def test_commit_without_selection(client):
    resp = client.post("/api/jobs/bulk-compare/commit", json={"jobs": _preview_jobs(client)})
    assert resp.status_code == 201, resp.text
    assert resp.json()["selection"] is None


def test_commit_existing_selection_is_409_and_saves_nothing(client, engine):
    with Session(engine) as db:
        JobSelectionRepository(db).create(name="taken", description="", tags=[], job_sequence=[], run_settings={})
    resp = client.post("/api/jobs/bulk-compare/commit",
                       json={"jobs": _preview_jobs(client), "selection_name": "taken"})
    assert resp.status_code == 409
    with Session(engine) as db:
        assert JobRepository(db).get("cust_cmp") is None


def test_commit_rejects_non_sql_compare_jobs(client):
    job = {"name": "x", "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"]}
    resp = client.post("/api/jobs/bulk-compare/commit", json={"jobs": [job]})
    assert resp.status_code == 422


def test_commit_rejects_duplicate_names(client):
    jobs = _preview_jobs(client)
    resp = client.post("/api/jobs/bulk-compare/commit", json={"jobs": jobs + jobs})
    assert resp.status_code == 422
    assert "Duplicate" in resp.text


def test_commit_rechecks_configs_and_saves_nothing(client, engine):
    jobs = _preview_jobs(client)
    jobs[0]["params"]["request"]["config_id_b"] = 999
    resp = client.post("/api/jobs/bulk-compare/commit", json={"jobs": jobs})
    assert resp.status_code == 422
    assert "999" in resp.text
    with Session(engine) as db:
        assert JobRepository(db).get("cust_cmp") is None
