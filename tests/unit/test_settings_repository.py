"""Tests for SettingsRepository (app-wide timezone setting)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import SettingsRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_get_timezone_defaults_to_utc_on_fresh_db():
    db = _session()
    assert SettingsRepository(db).get_timezone() == "UTC"


def test_set_timezone_persists_and_round_trips():
    db = _session()
    repo = SettingsRepository(db)
    repo.set_timezone("America/New_York")
    assert repo.get_timezone() == "America/New_York"


def test_set_timezone_rejects_unknown_zone():
    db = _session()
    with pytest.raises(ValueError):
        SettingsRepository(db).set_timezone("Not/AZone")


def test_set_timezone_updates_updated_at():
    db = _session()
    repo = SettingsRepository(db)
    row = repo.set_timezone("Europe/London")
    assert row.updated_at is not None
