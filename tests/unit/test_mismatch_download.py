from __future__ import annotations
import csv, io
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository, TokenRepository
from api.main import app
from api.routes import runs as runs_module


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SL = sessionmaker(bind=engine)
    monkeypatch.setattr(_db_module, "SessionLocal", SL)

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *a, **kw: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def run_with_mismatches(client):
    from datetime import datetime, timezone
    from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
    from etl_framework.runner.state import TestStatus

    # Create run via API
    resp = client.post("/api/runs", json={
        "source_env": "dev",
        "target_env": "prod",
        "job_names": [],
    })
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    # Write test result + mismatch directly to the shared in-memory DB
    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        result = ReconciliationResult(
            query_name="orders_recon",
            source_env="dev", target_env="prod",
            source_row_count=100, target_row_count=95,
            matched_count=95, missing_in_target_count=5,
            missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc), duration_seconds=1.0,
        )
        tr = repo.add_test_result(run_id, result)
        repo.add_mismatch_details(tr.id, [
            MismatchRecord(
                key_values={"id": 42},
                column_name="amount",
                source_value="100",
                target_value="110",
                mismatch_type="value_diff",
            )
        ])
        db.commit()

    return run_id


def test_download_csv_returns_csv(client, run_with_mismatches):
    resp = client.get(f"/api/runs/{run_with_mismatches}/mismatches/download?format=csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) >= 1
    assert rows[0]["column_name"] == "amount"


def test_download_xlsx_returns_xlsx(client, run_with_mismatches):
    resp = client.get(f"/api/runs/{run_with_mismatches}/mismatches/download?format=xlsx")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    import pandas as pd
    df = pd.read_excel(io.BytesIO(resp.content))
    assert "column_name" in df.columns
    assert df["column_name"].iloc[0] == "amount"


def test_download_unknown_run_returns_404(client):
    resp = client.get("/api/runs/no-such-run/mismatches/download?format=csv")
    assert resp.status_code == 404
