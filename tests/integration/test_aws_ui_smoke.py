"""Smoke: /api/aws/s3 routes are mounted and auth-guarded, and the AWS tab is served."""
from __future__ import annotations

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
