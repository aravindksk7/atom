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


def test_bo_download_dir_defaults_to_empty():
    """Empty means disabled, and it is the default — an upgraded install keeps
    today's browser-only behaviour until someone sets a path."""
    db = _session()
    assert SettingsRepository(db).get_bo_download_dir() == ""


def test_set_bo_download_dir_round_trips(tmp_path):
    db = _session()
    repo = SettingsRepository(db)
    repo.set_bo_download_dir(str(tmp_path))
    assert repo.get_bo_download_dir() == str(tmp_path)


def test_set_bo_download_dir_accepts_empty_to_disable(tmp_path):
    db = _session()
    repo = SettingsRepository(db)
    repo.set_bo_download_dir(str(tmp_path))
    repo.set_bo_download_dir("")
    assert repo.get_bo_download_dir() == ""


def test_set_bo_download_dir_rejects_a_relative_path():
    db = _session()
    with pytest.raises(ValueError, match="absolute"):
        SettingsRepository(db).set_bo_download_dir("reports/out")


def test_set_bo_download_dir_rejects_a_missing_directory(tmp_path):
    db = _session()
    with pytest.raises(ValueError, match="does not exist"):
        SettingsRepository(db).set_bo_download_dir(str(tmp_path / "nope"))


def test_set_bo_download_dir_rejects_a_file(tmp_path):
    target = tmp_path / "a-file.txt"
    target.write_text("x")
    db = _session()
    with pytest.raises(ValueError, match="not a directory"):
        SettingsRepository(db).set_bo_download_dir(str(target))
