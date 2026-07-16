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
def engine():
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def client(engine, monkeypatch):
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


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def test_gate_endpoint_returns_verdict(client, db):
    from datetime import datetime, timezone
    from etl_framework.repository.models import TestResult, TestRun
    db.add(TestRun(run_id="r1", status="COMPLETED"))
    db.add(TestResult(run_id="r1", query_name="orders_reconciliation",
                      status="PASSED", executed_at=datetime.now(timezone.utc)))
    db.commit()
    resp = client.post("/api/gates/orders_reconciliation/evaluate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "PROMOTE"
    assert body["job"] == "orders_reconciliation"


def test_gate_endpoint_holds_unknown_job(client):
    resp = client.post("/api/gates/ghost_job/evaluate")
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "HOLD"
