"""Tests for JobSelectionRepository and the selection-aware RunRepository.create_run."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import (
    JobSelectionRepository, RunRepository, ScheduleRepository,
)


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_create_makes_version_1():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(
        name="nightly-set", description="desc", tags=["daily"],
        job_sequence=[{"job_name": "orders"}], run_settings={"execution_mode": "parallel"},
    )
    assert sel.id is not None
    latest = repo.latest_version(sel.id)
    assert latest.version_number == 1
    assert latest.job_sequence == [{"job_name": "orders"}]


def test_create_new_version_increments_and_keeps_old():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    v2 = repo.create_new_version(sel.id, job_sequence=["a", "b"], run_settings=None)
    assert v2.version_number == 2
    v1 = repo.get_version(sel.id, 1)
    assert v1.job_sequence == ["a"]
    assert repo.get_version(sel.id, 2).job_sequence == ["a", "b"]


def test_update_metadata_does_not_create_new_version():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    repo.update_metadata(sel.id, name="renamed")
    assert repo.latest_version(sel.id).version_number == 1
    assert repo.get(sel.id).name == "renamed"


def test_archive_blocked_by_enabled_schedule():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    ScheduleRepository(db).create({
        "name": "sched", "cron_expr": "0 6 * * *",
        "selection_id": sel.id, "selection_version": 1,
        "source_env": "dev", "target_env": "prod", "enabled": True,
    })
    assert repo.active_schedule_count(sel.id) == 1
    with pytest.raises(ValueError):
        repo.archive_or_raise(sel.id)


def test_archive_succeeds_when_no_active_schedule():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    repo.archive_or_raise(sel.id)
    assert repo.get(sel.id).archived is True


def test_archive_or_raise_returns_none_for_nonexistent_id():
    db = _session()
    repo = JobSelectionRepository(db)
    assert repo.archive_or_raise(999999) is None


def test_runs_for_selection_filters_by_selection_id():
    db = _session()
    repo = JobSelectionRepository(db)
    sel = repo.create(name="s", description="", tags=[], job_sequence=["a"], run_settings={})
    run_repo = RunRepository(db)
    run_repo.create_run(run_id="r1", source_env="dev", target_env="",
                         selection_id=sel.id, selection_version=1)
    run_repo.create_run(run_id="r2", source_env="qa", target_env="")
    runs = repo.runs_for_selection(sel.id)
    assert [r.run_id for r in runs] == ["r1"]
