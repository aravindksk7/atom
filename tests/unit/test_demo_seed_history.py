"""The demo's earlier nightly runs, which give the first recorded run's bars real estimates."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.run_timing import estimate_durations
from etl_framework.repository.database import Base
from etl_framework.repository.models import TestResult, TestRun

SCRIPT = Path(__file__).parents[2] / "demo" / "seed" / "seed_history.py"
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
PLAN = {
    "sequence_id": 7,
    "sequence_name": "Nightly Regression",
    "nights": 5,
    "run_seconds": 200,
    "jobs": {"ETL_A": 15, "ETL_B": 4, "WATCHER": 0},
}


def load_script():
    spec = importlib.util.spec_from_file_location("seed_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def seeded_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    load_script().seed_history(session, PLAN, now=NOW)
    return session


def test_each_night_is_a_finished_run_of_the_sequence():
    session = seeded_session()
    runs = session.query(TestRun).order_by(TestRun.started_at).all()
    assert len(runs) == PLAN["nights"]
    for run in runs:
        assert run.status == "PASSED"
        assert run.sequence_id == 7
        assert run.config_snapshot["sequence"]["name"] == "Nightly Regression"
        assert (run.total_tests, run.passed) == (3, 3)
        assert run.started_at < run.completed_at
    # All in the past, so the recorded run is the newest in every list.
    assert runs[-1].completed_at.replace(tzinfo=timezone.utc) < NOW


def test_nights_are_inserted_oldest_first():
    # Recent runs lists newest first by id, so the nights must read in date order there.
    runs = seeded_session().query(TestRun).order_by(TestRun.id).all()
    started = [run.started_at for run in runs]
    assert started == sorted(started)


def test_jobs_with_a_duration_get_an_estimate_near_it():
    estimates = estimate_durations(seeded_session(), PLAN["jobs"].keys())
    assert 12 <= estimates["ETL_A"] <= 18
    assert 3 <= estimates["ETL_B"] <= 5


def test_a_job_with_no_duration_has_no_estimate():
    # The file watcher waits for the presenter, so history can't predict it.
    assert "WATCHER" not in estimate_durations(seeded_session(), PLAN["jobs"].keys())


def test_durations_vary_from_night_to_night():
    session = seeded_session()
    durations = {
        row.duration_seconds
        for row in session.query(TestResult).filter(TestResult.query_name == "ETL_A")
    }
    assert len(durations) > 1


def test_seeding_twice_gives_the_same_durations():
    first = seeded_session().query(TestResult.duration_seconds).order_by(TestResult.id).all()
    second = seeded_session().query(TestResult.duration_seconds).order_by(TestResult.id).all()
    assert first == second
