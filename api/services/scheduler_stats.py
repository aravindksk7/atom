"""Scheduler statistics aggregation for API, UI, CLI, and CI gates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from api.services.scheduler import get_scheduler_runtime_snapshot
from etl_framework.repository.models import ScheduledRun, TestRun


OUTCOME_KEYS = ("passed", "failed", "error", "cancelled", "blocked")


@dataclass(frozen=True)
class GateOptions:
    fail_on_stopped: bool = False
    min_success_rate: float | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _utc(value)
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _duration_seconds(run: TestRun) -> float | None:
    started = _utc(run.started_at)
    completed = _utc(run.completed_at)
    if started is None or completed is None:
        return None
    seconds = (completed - started).total_seconds()
    return seconds if seconds >= 0 else None


def _empty_counts() -> dict[str, Any]:
    return {
        "runs_triggered": 0,
        "passed": 0,
        "failed": 0,
        "error": 0,
        "cancelled": 0,
        "blocked": 0,
        "durations": [],
        "last_run_at": None,
        "last_status": None,
    }


def _status_key(status: str | None) -> str | None:
    normalized = (status or "").upper()
    if normalized in {"PASSED", "COMPLETED", "SLOW"}:
        return "passed"
    if normalized == "FAILED":
        return "failed"
    if normalized == "ERROR":
        return "error"
    if normalized == "CANCELLED":
        return "cancelled"
    if normalized == "BLOCKED":
        return "blocked"
    return None


def _success_rate(counts: dict[str, Any]) -> float | None:
    total = counts["runs_triggered"]
    if total == 0:
        return None
    return round((counts["passed"] / total) * 100, 2)


def _average_duration(counts: dict[str, Any]) -> float | None:
    durations = counts["durations"]
    if not durations:
        return None
    return round(sum(durations) / len(durations), 2)


def _public_counts(counts: dict[str, Any]) -> dict[str, Any]:
    return {
        "runs_triggered": counts["runs_triggered"],
        "passed": counts["passed"],
        "failed": counts["failed"],
        "error": counts["error"],
        "cancelled": counts["cancelled"],
        "blocked": counts["blocked"],
        "success_rate": _success_rate(counts),
        "average_duration_seconds": _average_duration(counts),
    }


def _schedule_key(schedule: ScheduledRun) -> int:
    return schedule.selection_id or schedule.id


def _evaluate_gate(summary: dict[str, Any], scheduler: dict[str, Any], options: GateOptions | None) -> dict[str, Any]:
    reasons: list[str] = []
    if options and options.fail_on_stopped and not scheduler.get("running"):
        reasons.append("scheduler is not running")
    if options and options.min_success_rate is not None:
        rate = summary.get("success_rate")
        if rate is None:
            reasons.append(f"success rate is unavailable, below {options.min_success_rate}")
        elif rate < options.min_success_rate:
            reasons.append(f"success rate {rate} is below {options.min_success_rate}")
    return {
        "status": "failed" if reasons else "passed",
        "exit_code": 1 if reasons else 0,
        "reasons": reasons,
    }


def build_scheduler_stats(
    db: Session,
    days: int = 30,
    now: datetime | None = None,
    runtime_snapshot: dict | None = None,
    gate_options: GateOptions | None = None,
) -> dict[str, Any]:
    if days < 1 or days > 365:
        raise ValueError("days must be between 1 and 365")
    generated_at = _utc(now) or datetime.now(timezone.utc)
    since = generated_at - timedelta(days=days)
    scheduler = runtime_snapshot or get_scheduler_runtime_snapshot()
    scheduler.setdefault("jobs", {})

    schedules = db.query(ScheduledRun).order_by(ScheduledRun.name).all()
    counts_by_key = {_schedule_key(schedule): _empty_counts() for schedule in schedules}
    runs = (
        db.query(TestRun)
        .filter(TestRun.run_type == "scheduled")
        .filter(TestRun.started_at >= since)
        .all()
    )
    for run in runs:
        key = run.selection_id
        if key not in counts_by_key:
            continue
        counts = counts_by_key[key]
        counts["runs_triggered"] += 1
        outcome = _status_key(run.status)
        if outcome:
            counts[outcome] += 1
        duration = _duration_seconds(run)
        if duration is not None:
            counts["durations"].append(duration)
        started = _utc(run.started_at)
        last = _utc(counts["last_run_at"])
        if started is not None and (last is None or started > last):
            counts["last_run_at"] = started
            counts["last_status"] = run.status

    summary_counts = _empty_counts()
    schedule_payloads = []
    runtime_jobs = scheduler.get("jobs", {})
    for schedule in schedules:
        counts = counts_by_key[_schedule_key(schedule)]
        public = _public_counts(counts)
        runtime_job = runtime_jobs.get(schedule.id) or runtime_jobs.get(str(schedule.id)) or {}
        registered = bool(runtime_job) if schedule.enabled else False
        for key in ("runs_triggered", *OUTCOME_KEYS):
            summary_counts[key] += public[key]
        summary_counts["durations"].extend(counts["durations"])
        schedule_payloads.append({
            "id": schedule.id,
            "name": schedule.name,
            "enabled": schedule.enabled,
            "cron_expr": schedule.cron_expr,
            "registered": registered,
            "next_run_at": runtime_job.get("next_run_at") or _iso(schedule.next_run_at),
            "last_run_at": _iso(counts["last_run_at"] or schedule.last_run_at),
            "last_status": counts["last_status"],
            **public,
        })

    summary = {
        "total_schedules": len(schedules),
        "enabled_schedules": sum(1 for schedule in schedules if schedule.enabled),
        "disabled_schedules": sum(1 for schedule in schedules if not schedule.enabled),
        **_public_counts(summary_counts),
    }
    return {
        "window_days": days,
        "generated_at": _iso(generated_at),
        "scheduler": {
            "available": bool(scheduler.get("available")),
            "running": bool(scheduler.get("running")),
            "job_count": int(scheduler.get("job_count") or 0),
            "timezone": scheduler.get("timezone") or "UTC",
        },
        "summary": summary,
        "schedules": schedule_payloads,
        "gate": _evaluate_gate(summary, scheduler, gate_options),
    }
