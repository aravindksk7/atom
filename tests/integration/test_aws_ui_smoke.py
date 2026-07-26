"""Smoke: /api/aws/s3 routes are mounted and auth-guarded, and the AWS tab is served."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"},
                    raise_server_exceptions=False) as c:
        yield c


def test_aws_s3_route_is_mounted(client):
    r = client.post("/api/aws/s3/metadata", json={"config_id": 999, "bucket": "b", "key": "k"})
    assert r.status_code != 404 or r.json().get("detail") != "Not Found"


def test_openapi_lists_aws_routes(client):
    spec = client.get("/openapi.json").json()
    assert "/api/aws/s3/metadata" in spec["paths"]
    assert "/api/aws/s3/validate-format" in spec["paths"]


def test_aws_tab_contains_s3_job_creation_controls():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "data-testid=\"aws-create-row-count-job-btn\"" in html
    assert "data-testid=\"aws-create-format-validation-job-btn\"" in html
    assert "data-testid=\"aws-create-partition-check-job-btn\"" in html
    assert "data-testid=\"aws-job-name-input\"" in html
    assert "data-testid=\"aws-min-rows-input\"" in html
    assert "data-testid=\"aws-expected-columns-input\"" in html
    assert "Type mismatches:" in html


def test_aws_tab_contains_glue_catalog_controls():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "data-testid=\"aws-service-glue\"" in html
    assert "disabled title=\"Coming soon\" class=\"sub-tab\">Athena" in html
    assert "disabled title=\"Coming soon\" class=\"sub-tab\">Airflow" in html
    assert "data-testid=\"aws-glue-source-database-input\"" in html
    assert "data-testid=\"aws-glue-compare-btn\"" in html
    assert "data-testid=\"aws-glue-create-job-btn\"" in html
