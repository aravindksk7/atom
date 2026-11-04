"""The timing document: elapsed time, step runtime and the progress rule."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.services.run_timing import (
    build_run_elapsed,
    build_run_timing,
    estimate_durations,
    job_family,
    load_timing_context,
)

T0 = datetime(2026, 11, 4, 9, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def run(status="RUNNING", started_at=T0, completed_at=None):
    return SimpleNamespace(
        run_id="r1", status=status, started_at=started_at, completed_at=completed_at,
    )


def step(status="PENDING", started=None, finished=None, **kwargs):
    defaults = dict(
        step_id="a", step_index=0, job_name="job_a", status=status, attempt=0,
        wait_seconds=0, carried_over=False, held_at=None, released_at=None,
        started_at=at(started) if started is not None else None,
        finished_at=at(finished) if finished is not None else None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def timing_of(the_step, the_run=None, estimates=None, job_types=None, now_seconds=100):
    return build_run_timing(
        the_run or run(), [the_step], job_types or {}, estimates or {}, at(now_seconds),
    )["steps"][0]


# --- end-to-end time ------------------------------------------------------

def test_elapsed_of_finished_run_uses_completed_at():
    result = build_run_elapsed(run("PASSED", T0, at(600)), [], at(9999))
    assert result == {"elapsed_seconds": 600.0, "held_seconds": 0.0, "active_seconds": 600.0}


def test_elapsed_of_live_run_runs_to_now():
    assert build_run_elapsed(run(), [], at(90))["elapsed_seconds"] == 90.0


def test_elapsed_is_null_before_start():
    result = build_run_elapsed(run("PENDING", None), [], at(90))
    assert result == {"elapsed_seconds": None, "held_seconds": 0.0, "active_seconds": None}


def test_elapsed_is_null_for_finished_run_without_completed_at():
    assert build_run_elapsed(run("ERROR", T0, None), [], at(90))["elapsed_seconds"] is None


def test_naive_times_are_read_as_utc():
    naive = run("PASSED", T0.replace(tzinfo=None), at(60).replace(tzinfo=None))
    assert build_run_elapsed(naive, [], at(90))["elapsed_seconds"] == 60.0


def test_overlapping_parallel_holds_are_merged():
    steps = [
        step(held_at=at(10), released_at=at(40)),
        step(held_at=at(30), released_at=at(50)),
        step(held_at=at(70), released_at=at(80)),
    ]
    result = build_run_elapsed(run("PASSED", T0, at(100)), steps, at(100))
    assert result["held_seconds"] == 50.0
    assert result["active_seconds"] == 50.0


def test_open_hold_runs_to_now():
    steps = [step("HELD", held_at=at(10))]
    assert build_run_elapsed(run(), steps, at(25))["held_seconds"] == 15.0


def test_hold_that_ended_without_release_stops_at_run_end():
    steps = [step("CANCELLED", held_at=at(10))]
    assert build_run_elapsed(run("CANCELLED", T0, at(30)), steps, at(999))["held_seconds"] == 20.0


# --- progress rule --------------------------------------------------------

def test_queued():
    result = timing_of(step("PENDING"))
    assert result["progress"] == {"state": "queued", "percent": 0, "label": "queued"}
    assert result["runtime_seconds"] is None


def test_running_with_estimate():
    result = timing_of(step("RUNNING", started=0), estimates={"job_a": 200.0}, now_seconds=100)
    assert result["progress"] == {"state": "running", "percent": 50, "label": "50%"}
    assert result["runtime_seconds"] == 100.0
    assert result["estimate_seconds"] == 200.0


def test_running_percent_stops_at_95():
    result = timing_of(step("RUNNING", started=0), estimates={"job_a": 100.0}, now_seconds=99)
    assert result["progress"]["percent"] == 95
    assert result["progress"]["state"] == "running"


def test_past_estimate_is_over_estimate():
    result = timing_of(step("RUNNING", started=0), estimates={"job_a": 100.0}, now_seconds=100)
    assert result["progress"] == {"state": "over_estimate", "percent": 95, "label": "over estimate"}


def test_running_without_estimate_has_no_percent():
    result = timing_of(step("RUNNING", started=0))
    assert result["progress"] == {"state": "running_unknown", "percent": None, "label": "running"}
    assert result["estimate_seconds"] is None


def test_estimate_includes_wait_seconds():
    result = timing_of(step("RUNNING", started=0, wait_seconds=30), estimates={"job_a": 70.0})
    assert result["estimate_seconds"] == 100.0


def test_held():
    result = timing_of(step("HELD", started=0, finished=20, held_at=at(20)), now_seconds=50)
    assert result["progress"] == {"state": "held", "percent": 100, "label": "awaiting release"}
    assert result["runtime_seconds"] == 20.0
    assert result["held_seconds"] == 30.0


@pytest.mark.parametrize("status", ["PASSED", "FAILED", "SLOW", "ERROR", "APPROVED"])
def test_finished_with_times(status):
    result = timing_of(step(status, started=0, finished=42))
    assert result["progress"] == {"state": "done", "percent": 100, "label": status}
    assert result["runtime_seconds"] == 42.0


def test_finished_without_times_is_not_recorded():
    finished = run("PASSED", T0, at(60))
    result = timing_of(step("PASSED"), finished)
    assert result["progress"] == {"state": "not_recorded", "percent": 100, "label": "PASSED"}


@pytest.mark.parametrize("status", ["BLOCKED", "CANCELLED", "SKIPPED"])
def test_never_started_is_not_run(status):
    result = timing_of(step(status))
    assert result["progress"] == {"state": "not_run", "percent": 0, "label": status}


def test_pending_in_finished_run_is_not_run():
    result = timing_of(step("PENDING"), run("CANCELLED", T0, at(60)))
    assert result["progress"] == {"state": "not_run", "percent": 0, "label": "not run"}


def test_carried_over():
    result = timing_of(step("PASSED", carried_over=True))
    assert result["progress"] == {"state": "carried_over", "percent": 100, "label": "carried over"}


def test_running_in_finished_run_is_interrupted_and_ends_with_the_run():
    result = timing_of(step("RUNNING", started=10), run("ERROR", T0, at(70)), now_seconds=999)
    assert result["progress"] == {"state": "interrupted", "percent": 100, "label": "interrupted"}
    assert result["runtime_seconds"] == 60.0


def test_held_in_finished_run_is_interrupted():
    result = timing_of(step("HELD", started=0, finished=5), run("ERROR", T0, at(70)))
    assert result["progress"]["state"] == "interrupted"


# --- document -------------------------------------------------------------

def test_document_counts_and_times_are_iso_utc():
    steps = [
        step("PASSED", started=0, finished=10, step_id="a"),
        step("RUNNING", started=10, step_id="b", step_index=1),
        step("PENDING", step_id="c", step_index=2),
    ]
    doc = build_run_timing(run(), steps, {}, {}, at(50))
    assert doc["jobs_total"] == 3
    assert doc["jobs_done"] == 1
    assert doc["timing_recorded"] is True
    assert doc["started_at"] == "2026-11-04T09:00:00+00:00"
    assert doc["server_time"] == "2026-11-04T09:00:50+00:00"
    assert doc["steps"][0]["finished_at"] == "2026-11-04T09:00:10+00:00"


def test_timing_recorded_false_for_old_run():
    doc = build_run_timing(run("PASSED", T0, at(60)), [step("PASSED")], {}, {}, at(60))
    assert doc["timing_recorded"] is False
    assert doc["elapsed_seconds"] == 60.0


def test_hold_end_is_reported_for_the_chart():
    live = timing_of(step("HELD", started=0, finished=5, held_at=at(5)), now_seconds=20)
    assert live["held_at"] == "2026-11-04T09:00:05+00:00"
    assert live["hold_ended_at"] == "2026-11-04T09:00:20+00:00"


@pytest.mark.parametrize("job_type,family", [
    ("reconciliation", "reconciliation"),
    ("file_watcher", "file"),
    ("ds_job", "sap"),
    ("airflow_dag_run", "orchestration"),
    ("freshness", "quality"),
    ("something_new", "other"),
    (None, "other"),
])
def test_job_family(job_type, family):
    assert job_family(job_type) == family


def test_step_reports_job_type_and_family():
    result = timing_of(step("PENDING"), job_types={"job_a": "bo_report"})
    assert result["job_type"] == "bo_report"
    assert result["job_family"] == "sap"


# --- database reads -------------------------------------------------------

def _db():
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _results(db, job, rows):
    from etl_framework.repository.models import TestResult, TestRun

    run_id = f"run-{job}-{len(rows)}"
    db.add(TestRun(run_id=run_id, status="PASSED"))
    for status, duration in rows:
        db.add(TestResult(run_id=run_id, query_name=job, status=status, duration_seconds=duration))
    db.commit()


def test_estimate_is_median_of_counted_results():
    db = _db()
    _results(db, "job", [("PASSED", 10.0), ("FAILED", 20.0), ("SLOW", 90.0), ("ERROR", 1.0), ("PASSED", 0.0)])
    assert estimate_durations(db, ["job"]) == {"job": 20.0}


def test_estimate_needs_three_results():
    db = _db()
    _results(db, "job", [("PASSED", 10.0), ("PASSED", 20.0)])
    assert estimate_durations(db, ["job"]) == {}


def test_estimate_uses_only_the_last_ten():
    db = _db()
    _results(db, "job", [("PASSED", 1000.0)] * 5 + [("PASSED", 10.0)] * 10)
    assert estimate_durations(db, ["job"]) == {"job": 10.0}


def test_load_timing_context_reads_job_types_and_skips_unknown_jobs():
    from etl_framework.repository.models import SavedJob

    db = _db()
    db.add(SavedJob(name="known", job_type="bo_report"))
    db.commit()
    context = load_timing_context(db, ["known", "deleted"])
    assert context.job_types == {"known": "bo_report"}
    assert context.estimates == {}
