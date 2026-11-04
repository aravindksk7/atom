"""Timing of a run and its steps, and the progress rule for each step.

The UI and the CLI only display what this module computes, so the rule lives
in one place. build_run_timing and build_run_elapsed are pure: the clock is an
argument and they never touch the database.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from etl_framework.repository.models import SavedJob, TERMINAL_STATUSES, TestResult

_ESTIMATE_SAMPLE = 10
_ESTIMATE_MIN_RESULTS = 3
# ERROR is left out: crashes end early and would pull the estimate down.
_ESTIMATE_STATUSES = ("PASSED", "FAILED", "SLOW")
_MAX_RUNNING_PERCENT = 95
_NOT_RUN_STATUSES = frozenset({"BLOCKED", "CANCELLED", "SKIPPED"})
_UNFINISHED_STATES = frozenset({
    "queued", "running", "over_estimate", "running_unknown", "held",
})

_JOB_FAMILIES = {
    "reconciliation": "reconciliation",
    "api_reconciliation": "reconciliation",
    "compare": "reconciliation",
    "cross_job_assertion": "reconciliation",
    "file_transfer": "file",
    "file_watcher": "file",
    "s3_row_count": "file",
    "s3_format_validation": "file",
    "s3_partition_check": "file",
    "bo_report": "sap",
    "bo_job": "sap",
    "ds_job": "sap",
    "automic_job": "orchestration",
    "airflow_dag_run": "orchestration",
    "aws_glue_job_run": "orchestration",
    "aws_athena_query": "orchestration",
    "freshness": "quality",
    "schema_snapshot": "quality",
    "profile": "quality",
    "dbt_artifact": "quality",
    "aws_glue_catalog_compare": "quality",
}


@dataclass(frozen=True)
class TimingContext:
    """What a run's timing needs that doesn't change while it runs."""
    job_types: dict[str, str] = field(default_factory=dict)
    estimates: dict[str, float] = field(default_factory=dict)


def job_family(job_type: str | None) -> str:
    return _JOB_FAMILIES.get(job_type or "", "other")


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _iso(value: datetime | None) -> str | None:
    value = _utc(value)
    return value.isoformat() if value is not None else None


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max((end - start).total_seconds(), 0.0), 1)


def _hold_interval(step: Any, run: Any, now: datetime) -> tuple[datetime, datetime] | None:
    start = _utc(step.held_at)
    if start is None:
        return None
    end = _utc(step.released_at) or _utc(run.completed_at) or now
    return start, max(start, end)


def _merged_seconds(intervals: Iterable[tuple[datetime, datetime]]) -> float:
    total = 0.0
    current_start = current_end = None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            if current_end is not None:
                total += (current_end - current_start).total_seconds()
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += (current_end - current_start).total_seconds()
    return round(total, 1)


def build_run_elapsed(run: Any, steps: Iterable[Any], now: datetime) -> dict[str, float | None]:
    """A run's end-to-end time: elapsed, held on holds, and active."""
    now = _utc(now)
    started = _utc(run.started_at)
    end = _utc(run.completed_at)
    if end is None and run.status not in TERMINAL_STATUSES:
        end = now
    elapsed = _seconds(started, end)
    held = _merged_seconds(
        interval for interval in (_hold_interval(s, run, now) for s in steps) if interval
    )
    active = round(max(elapsed - held, 0.0), 1) if elapsed is not None else None
    return {"elapsed_seconds": elapsed, "held_seconds": held, "active_seconds": active}


def _step_runtime(step: Any, run: Any, now: datetime) -> float | None:
    started = _utc(step.started_at)
    end = _utc(step.finished_at)
    if end is None:
        end = _utc(run.completed_at) if run.status in TERMINAL_STATUSES else now
    return _seconds(started, end)


def _state(step: Any, run_finished: bool, runtime: float | None, estimate: float | None) -> str:
    if step.carried_over:
        return "carried_over"
    if step.status in ("RUNNING", "HELD"):
        if run_finished:
            return "interrupted"
        if step.status == "HELD":
            return "held"
        if estimate is None:
            return "running_unknown"
        return "over_estimate" if (runtime or 0.0) >= estimate else "running"
    if step.status == "PENDING":
        return "not_run" if run_finished else "queued"
    if step.started_at is not None:
        return "done"
    return "not_run" if step.status in _NOT_RUN_STATUSES else "not_recorded"


def _progress(state: str, status: str, runtime: float | None, estimate: float | None) -> dict:
    if state == "running":
        percent = min(_MAX_RUNNING_PERCENT, int((runtime or 0.0) / estimate * 100))
        return {"state": state, "percent": percent, "label": f"{percent}%"}
    if state == "not_run":
        label = "not run" if status == "PENDING" else status
        return {"state": state, "percent": 0, "label": label}
    fixed = {
        "queued": (0, "queued"),
        "over_estimate": (_MAX_RUNNING_PERCENT, "over estimate"),
        "running_unknown": (None, "running"),
        "held": (100, "awaiting release"),
        "carried_over": (100, "carried over"),
        "interrupted": (100, "interrupted"),
    }
    percent, label = fixed.get(state, (100, status))
    return {"state": state, "percent": percent, "label": label}


def _step_timing(
    step: Any, run: Any, job_types: dict, estimates: dict, now: datetime, run_finished: bool,
) -> dict:
    runtime = _step_runtime(step, run, now)
    history = estimates.get(step.job_name)
    estimate = round(history + (step.wait_seconds or 0), 1) if history is not None else None
    hold = _hold_interval(step, run, now)
    job_type = job_types.get(step.job_name)
    return {
        "step_id": step.step_id,
        "step_index": step.step_index,
        "job_name": step.job_name,
        "job_type": job_type,
        "job_family": job_family(job_type),
        "status": step.status,
        "attempt": step.attempt or 0,
        "started_at": _iso(step.started_at),
        "finished_at": _iso(step.finished_at),
        "held_at": _iso(step.held_at),
        "hold_ended_at": _iso(hold[1]) if hold else None,
        "runtime_seconds": runtime,
        "estimate_seconds": estimate,
        "held_seconds": _seconds(*hold) if hold else 0.0,
        "progress": _progress(
            _state(step, run_finished, runtime, estimate), step.status, runtime, estimate,
        ),
    }


def build_run_timing(
    run: Any, steps: list, job_types: dict[str, str], estimates: dict[str, float], now: datetime,
) -> dict[str, Any]:
    """The full timing document for one run, live or finished."""
    now = _utc(now)
    run_finished = run.status in TERMINAL_STATUSES
    step_docs = [_step_timing(s, run, job_types, estimates, now, run_finished) for s in steps]
    return {
        "run_id": run.run_id,
        "status": run.status,
        "server_time": _iso(now),
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        **build_run_elapsed(run, steps, now),
        "jobs_total": len(step_docs),
        "jobs_done": sum(1 for s in step_docs if s["progress"]["state"] not in _UNFINISHED_STATES),
        "timing_recorded": any(s.started_at is not None for s in steps),
        "steps": step_docs,
    }


def estimate_durations(db: Session, job_names: Iterable[str]) -> dict[str, float]:
    """Median duration of each job's recent results, where there are enough of them."""
    estimates: dict[str, float] = {}
    for name in set(job_names):
        rows = (
            db.query(TestResult.duration_seconds)
            .filter(
                TestResult.query_name == name,
                TestResult.status.in_(_ESTIMATE_STATUSES),
                TestResult.duration_seconds > 0,
            )
            .order_by(TestResult.id.desc())
            .limit(_ESTIMATE_SAMPLE)
            .all()
        )
        if len(rows) >= _ESTIMATE_MIN_RESULTS:
            estimates[name] = round(statistics.median(row[0] for row in rows), 1)
    return estimates


def load_timing_context(db: Session, job_names: Iterable[str]) -> TimingContext:
    names = set(job_names)
    if not names:
        return TimingContext()
    rows = db.query(SavedJob.name, SavedJob.job_type).filter(SavedJob.name.in_(names)).all()
    return TimingContext(
        job_types={name: job_type for name, job_type in rows},
        estimates=estimate_durations(db, names),
    )


def timing_for_run(
    db: Session, run: Any, steps: list, context: TimingContext | None = None,
) -> dict[str, Any]:
    if context is None:
        context = load_timing_context(db, [s.job_name for s in steps])
    return build_run_timing(
        run, steps, context.job_types, context.estimates, datetime.now(timezone.utc),
    )
