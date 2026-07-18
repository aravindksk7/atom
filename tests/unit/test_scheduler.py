"""Tests for ScheduleRepository and scheduler service."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ScheduleRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


# ---------------------------------------------------------------------------
# ScheduleRepository
# ---------------------------------------------------------------------------

def _sched_data(**overrides) -> dict:
    base = {
        "name": "nightly",
        "cron_expr": "0 2 * * *",
        "job_sequence": ["orders", "customers"],
        "source_env": "dev",
        "target_env": "prod",
        "run_settings_json": {},
        "enabled": True,
    }
    return {**base, **overrides}


def test_create_and_get():
    db = _session()
    repo = ScheduleRepository(db)
    s = repo.create(_sched_data())
    assert s.id is not None
    assert s.name == "nightly"
    assert repo.get(s.id).cron_expr == "0 2 * * *"


def test_list_returns_all():
    db = _session()
    repo = ScheduleRepository(db)
    repo.create(_sched_data(name="a"))
    repo.create(_sched_data(name="b"))
    assert len(repo.list()) == 2


def test_list_enabled_filters_disabled():
    db = _session()
    repo = ScheduleRepository(db)
    repo.create(_sched_data(name="on", enabled=True))
    repo.create(_sched_data(name="off", enabled=False))
    enabled = repo.list_enabled()
    assert len(enabled) == 1
    assert enabled[0].name == "on"


def test_update_cron_expr():
    db = _session()
    repo = ScheduleRepository(db)
    s = repo.create(_sched_data())
    updated = repo.update(s.id, {"cron_expr": "0 6 * * 1"})
    assert updated.cron_expr == "0 6 * * 1"


def test_delete():
    db = _session()
    repo = ScheduleRepository(db)
    s = repo.create(_sched_data())
    assert repo.delete(s.id) is True
    assert repo.get(s.id) is None


def test_delete_nonexistent_returns_false():
    db = _session()
    assert ScheduleRepository(db).delete(9999) is False


def test_touch_updates_last_run_at():
    from datetime import datetime, timezone
    db = _session()
    repo = ScheduleRepository(db)
    s = repo.create(_sched_data())
    now = datetime.now(timezone.utc)
    repo.touch(s.id, last_run_at=now)
    db.refresh(s)
    assert s.last_run_at is not None


def test_get_by_name():
    db = _session()
    repo = ScheduleRepository(db)
    repo.create(_sched_data())
    found = repo.get_by_name("nightly")
    assert found is not None
    assert found.name == "nightly"


def test_get_by_name_missing_returns_none():
    db = _session()
    assert ScheduleRepository(db).get_by_name("missing") is None


# ---------------------------------------------------------------------------
# Cron validation (via croniter if available)
# ---------------------------------------------------------------------------

def test_valid_cron_expression_passes():
    try:
        from croniter import croniter
    except ImportError:
        pytest.skip("croniter not installed")
    from api.routes.schedules import _validate_cron
    assert _validate_cron("0 6 * * *") == "0 6 * * *"


def test_invalid_cron_expression_raises():
    try:
        from croniter import croniter
    except ImportError:
        pytest.skip("croniter not installed")
    from api.routes.schedules import _validate_cron
    with pytest.raises(ValueError):
        _validate_cron("not a cron expression 999 999")


# ---------------------------------------------------------------------------
# Scheduler service: graceful no-op when APScheduler not available
# ---------------------------------------------------------------------------

def test_is_available_reflects_apscheduler_presence():
    from api.services import scheduler as svc
    import importlib
    available = importlib.util.find_spec("apscheduler") is not None
    assert svc.is_available() == available


def test_add_and_remove_job_noop_when_not_started():
    from api.services import scheduler as svc
    # Should not raise even if scheduler isn't started
    svc.remove_job(999)


# ---------------------------------------------------------------------------
# App-wide timezone integration
# ---------------------------------------------------------------------------

def test_add_job_uses_current_app_timezone(monkeypatch):
    pytest.importorskip("apscheduler")
    from api.services import scheduler as svc

    added = {}

    class FakeScheduler:
        def add_job(self, func, trigger=None, id=None, args=None,
                     replace_existing=None, misfire_grace_time=None):
            added["trigger"] = trigger
            added["id"] = id

    monkeypatch.setattr(svc, "_scheduler", FakeScheduler())
    monkeypatch.setattr(svc, "_current_timezone", lambda: "America/New_York")

    class FakeSched:
        id = 1
        name = "nightly"
        cron_expr = "0 9 * * *"

    svc._add_job(FakeSched())
    assert added["id"] == "etl_schedule_1"
    assert str(added["trigger"].timezone) == "America/New_York"


def test_refresh_all_timezones_readds_enabled_schedules(monkeypatch):
    pytest.importorskip("apscheduler")
    from api.services import scheduler as svc
    from etl_framework.repository.database import Base
    import etl_framework.repository.database as _db_module

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine)
    previous = _db_module.SessionLocal
    _db_module.SessionLocal = testing_session
    try:
        db = testing_session()
        ScheduleRepository(db).create(_sched_data(name="nightly", enabled=True))
        ScheduleRepository(db).create(_sched_data(name="off", enabled=False))
        db.close()

        calls = []
        monkeypatch.setattr(svc, "_add_job", lambda sched: calls.append(sched.name))
        monkeypatch.setattr(svc, "_scheduler", object())  # truthy; only _add_job matters here
        svc.refresh_all_timezones()
        assert calls == ["nightly"]
    finally:
        _db_module.SessionLocal = previous


def test_refresh_all_timezones_noop_when_not_started():
    from api.services import scheduler as svc
    svc._scheduler = None
    svc.refresh_all_timezones()  # must not raise


def test_schedule_stats_route_returns_payload(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from api.dependencies import get_session
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import TokenRepository

    db = _session()
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=db.get_bind()))
    raw, _ = TokenRepository(db).create("test-scheduler-stats")

    def override_session():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
        response = client.get("/api/schedules/stats?days=30")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    body = response.json()
    assert body["window_days"] == 30
    assert "scheduler" in body
    assert "summary" in body
    assert "schedules" in body


def test_schedule_stats_route_validates_days(monkeypatch):
    from fastapi.testclient import TestClient
    from api.main import app
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import TokenRepository

    db = _session()
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=db.get_bind()))
    raw, _ = TokenRepository(db).create("test-scheduler-stats-validation")

    client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    response = client.get("/api/schedules/stats?days=0")

    assert response.status_code == 422
