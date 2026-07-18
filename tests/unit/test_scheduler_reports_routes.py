from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import ScheduledRun
from etl_framework.repository.repository import SchedulerTelemetryRepository, TokenRepository


@pytest.fixture
def client(monkeypatch):
    from api.dependencies import get_session
    from api.main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_session] = override_get_db
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test", is_admin=True)
        sched = ScheduledRun(
            name="nightly",
            cron_expr="0 2 * * *",
            selection_id=1,
            selection_version=1,
            source_env="dev",
            target_env="prod",
        )
        db.add(sched)
        db.commit()
        db.refresh(sched)
        SchedulerTelemetryRepository(db).record_event(
            schedule_id=sched.id,
            schedule_name="nightly",
            job_name="orders",
            event_state="completed",
            status="PASSED",
            exit_code=0,
            started_at=datetime(2026, 7, 18, 5, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 7, 18, 5, 2, tzinfo=timezone.utc),
            duration_ms=120000,
            created_at=datetime(2026, 7, 18, 5, 2, tzinfo=timezone.utc),
        )
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_summary_endpoint_returns_scheduler_report(client):
    resp = client.get("/api/scheduler-reports/summary", params={"days": 30})

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["total_events"] == 1
    assert body["summary"]["passed"] == 1


@pytest.mark.parametrize("path,key", [
    ("/api/scheduler-reports/grid", "rows"),
    ("/api/scheduler-reports/timeline", "segments"),
    ("/api/scheduler-reports/metrics", "outcomes"),
])
def test_report_data_endpoints_return_expected_payloads(client, path, key):
    resp = client.get(path, params={"days": 30})

    assert resp.status_code == 200
    assert key in resp.json()


def test_export_endpoint_returns_json_by_default(client):
    resp = client.get("/api/scheduler-reports/export", params={"days": 30})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["rows"][0]["schedule_name"] == "nightly"


def test_export_endpoint_returns_csv(client):
    resp = client.get("/api/scheduler-reports/export", params={"format": "csv", "days": 30})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "schedule_name" in resp.text
    assert "nightly" in resp.text


def test_prune_endpoint_removes_old_telemetry(client):
    resp = client.post("/api/scheduler-reports/prune", params={"retention_days": 30})

    assert resp.status_code == 200
    assert resp.json() == {"retention_days": 30, "deleted": 0}


def test_query_aliases_from_and_to_filter_reports(client):
    resp = client.get(
        "/api/scheduler-reports/summary",
        params={
            "from": "2026-07-18T04:00:00+00:00",
            "to": "2026-07-18T06:00:00+00:00",
            "job": "night",
        },
    )

    assert resp.status_code == 200
    assert resp.json()["summary"]["total_events"] == 1


def test_rejects_invalid_date_range(client):
    resp = client.get(
        "/api/scheduler-reports/summary",
        params={"from": "2026-07-20T00:00:00+00:00", "to": "2026-07-01T00:00:00+00:00"},
    )
    assert resp.status_code == 422


def test_rejects_invalid_export_format(client):
    resp = client.get("/api/scheduler-reports/export", params={"format": "xml"})

    assert resp.status_code == 422
