from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from api.services.scheduler import get_scheduler_runtime_snapshot
from etl_framework.repository.models import SchedulerTelemetryEvent
from etl_framework.repository.repository import (
    ScheduleRepository,
    SchedulerTelemetryQuery,
    SchedulerTelemetryRepository,
)

TERMINAL_SUCCESS = {"PASSED", "COMPLETED"}
TERMINAL_FAILURE = {"FAILED", "ERROR", "CANCELLED", "BLOCKED"}


@dataclass(frozen=True)
class SchedulerReportFilters:
    from_dt: datetime | None = None
    to_dt: datetime | None = None
    days: int | None = 7
    schedule_id: int | None = None
    job: str | None = None
    status: str | None = None
    exit_code: int | None = None

    def resolved(self, now: datetime | None = None) -> "SchedulerReportFilters":
        now = now or datetime.now(timezone.utc)
        if self.from_dt is None and self.days is not None:
            return SchedulerReportFilters(
                from_dt=now - timedelta(days=self.days),
                to_dt=self.to_dt or now,
                days=self.days,
                schedule_id=self.schedule_id,
                job=self.job,
                status=self.status,
                exit_code=self.exit_code,
            )
        return self

    def telemetry_query(self, now: datetime | None = None) -> SchedulerTelemetryQuery:
        resolved = self.resolved(now=now)
        return SchedulerTelemetryQuery(
            from_dt=resolved.from_dt,
            to_dt=resolved.to_dt,
            schedule_id=resolved.schedule_id,
            job=resolved.job,
            status=resolved.status,
            exit_code=resolved.exit_code,
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _duration_seconds(duration_ms: int | None) -> float | None:
    return round(duration_ms / 1000, 3) if duration_ms is not None else None


class SchedulerReportingService:
    def __init__(self, db: Session, runtime_snapshot: dict | None = None) -> None:
        self._db = db
        self._runtime_snapshot = runtime_snapshot

    def _runtime(self) -> dict:
        return self._runtime_snapshot if self._runtime_snapshot is not None else get_scheduler_runtime_snapshot()

    def _events(self, filters: SchedulerReportFilters) -> list[SchedulerTelemetryEvent]:
        return SchedulerTelemetryRepository(self._db).query_events(filters.telemetry_query())

    def _warnings(self, events: list[SchedulerTelemetryEvent]) -> list[str]:
        warnings: list[str] = []
        if not events:
            warnings.append("No scheduler telemetry found for the selected filters")
        runtime = self._runtime()
        if not runtime.get("available", False):
            warnings.append("Scheduler runtime is unavailable")
        elif not runtime.get("running", False):
            warnings.append("Scheduler runtime is not running")
        return warnings

    def summary(self, filters: SchedulerReportFilters) -> dict:
        start = time.perf_counter()
        events = self._events(filters)
        counts = {"passed": 0, "failed": 0, "error": 0, "cancelled": 0, "blocked": 0}
        durations = [event.duration_ms for event in events if event.duration_ms is not None]
        for event in events:
            status = (event.status or "").upper()
            if status in TERMINAL_SUCCESS:
                counts["passed"] += 1
            elif status == "ERROR":
                counts["error"] += 1
            elif status == "CANCELLED":
                counts["cancelled"] += 1
            elif status == "BLOCKED":
                counts["blocked"] += 1
            elif status == "FAILED":
                counts["failed"] += 1
        total_terminal = sum(counts.values())
        success_rate = round((counts["passed"] / total_terminal) * 100, 1) if total_terminal else None
        query_ms = round((time.perf_counter() - start) * 1000, 3)
        return {
            "filters": self._filters_payload(filters),
            "generated_at": _iso(datetime.now(timezone.utc)),
            "scheduler": self._runtime(),
            "summary": {
                "total_events": len(events),
                "success_rate": success_rate,
                "avg_duration_seconds": _duration_seconds(int(sum(durations) / len(durations))) if durations else None,
                **counts,
            },
            "performance": {"report_query_ms": query_ms},
            "warnings": self._warnings(events),
        }

    def grid(self, filters: SchedulerReportFilters) -> dict:
        events = self._events(filters)
        latest = SchedulerTelemetryRepository(self._db).latest_by_schedule()
        runtime = self._runtime()
        runtime_jobs = runtime.get("jobs", {}) or {}
        rows = []
        for schedule in ScheduleRepository(self._db).list():
            if filters.schedule_id is not None and schedule.id != filters.schedule_id:
                continue
            if filters.job and filters.job.lower() not in schedule.name.lower():
                continue
            last = latest.get(schedule.id)
            next_run_at = (runtime_jobs.get(schedule.id) or runtime_jobs.get(str(schedule.id)) or {}).get("next_run_at")
            rows.append({
                "schedule_id": schedule.id,
                "schedule_name": schedule.name,
                "enabled": bool(schedule.enabled),
                "cron_expr": schedule.cron_expr,
                "source_env": schedule.source_env,
                "target_env": schedule.target_env,
                "selection_id": schedule.selection_id,
                "selection_version": schedule.selection_version,
                "next_run_at": next_run_at or _iso(schedule.next_run_at),
                "last_run_at": _iso(schedule.last_run_at),
                "last_status": last.status if last else None,
                "last_event_state": last.event_state if last else None,
                "last_duration_seconds": _duration_seconds(last.duration_ms if last else None),
                "last_exit_code": last.exit_code if last else None,
                "last_error_summary": last.error_summary if last else None,
            })
        return {"rows": rows, "warnings": self._warnings(events)}

    def timeline(self, filters: SchedulerReportFilters) -> dict:
        events = self._events(filters)
        segments = []
        for event in events:
            if event.started_at is None:
                continue
            segments.append({
                "schedule_id": event.schedule_id,
                "schedule_name": event.schedule_name,
                "run_id": event.run_id,
                "status": event.status,
                "event_state": event.event_state,
                "started_at": _iso(event.started_at),
                "finished_at": _iso(event.finished_at),
                "duration_seconds": _duration_seconds(event.duration_ms),
                "exit_code": event.exit_code,
                "error_summary": event.error_summary,
            })
        return {"segments": segments, "warnings": self._warnings(events)}

    def metrics(self, filters: SchedulerReportFilters) -> dict:
        events = self._events(filters)
        outcomes: dict[str, int] = {}
        durations = []
        for event in events:
            outcomes[event.status] = outcomes.get(event.status, 0) + 1
            if event.duration_ms is not None:
                durations.append(event.duration_ms)
        durations.sort()
        p95 = durations[int((len(durations) - 1) * 0.95)] if durations else None
        return {
            "outcomes": [{"status": status, "count": count} for status, count in sorted(outcomes.items())],
            "runtime": {
                "count": len(durations),
                "avg_seconds": _duration_seconds(int(sum(durations) / len(durations))) if durations else None,
                "p95_seconds": _duration_seconds(p95),
            },
            "warnings": self._warnings(events),
        }

    def export_rows(self, filters: SchedulerReportFilters) -> list[dict]:
        return [
            {
                "schedule_id": event.schedule_id,
                "schedule_name": event.schedule_name,
                "job_name": event.job_name,
                "selection_id": event.selection_id,
                "selection_version": event.selection_version,
                "run_id": event.run_id,
                "event_state": event.event_state,
                "status": event.status,
                "exit_code": event.exit_code,
                "started_at": _iso(event.started_at),
                "finished_at": _iso(event.finished_at),
                "duration_seconds": _duration_seconds(event.duration_ms),
                "error_summary": event.error_summary,
                "created_at": _iso(event.created_at),
            }
            for event in self._events(filters)
        ]

    def export_csv(self, filters: SchedulerReportFilters) -> str:
        rows = self.export_rows(filters)
        output = io.StringIO()
        fieldnames = [
            "schedule_id", "schedule_name", "job_name", "selection_id", "selection_version",
            "run_id", "event_state", "status", "exit_code", "started_at", "finished_at",
            "duration_seconds", "error_summary", "created_at",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    def prune(self, retention_days: int = 30, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        deleted = SchedulerTelemetryRepository(self._db).prune_older_than(now - timedelta(days=retention_days))
        return {"retention_days": retention_days, "deleted": deleted}

    def _filters_payload(self, filters: SchedulerReportFilters) -> dict:
        resolved = filters.resolved()
        return {
            "from": _iso(resolved.from_dt),
            "to": _iso(resolved.to_dt),
            "days": resolved.days,
            "schedule_id": resolved.schedule_id,
            "job": resolved.job,
            "status": resolved.status,
            "exit_code": resolved.exit_code,
        }
