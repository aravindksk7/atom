"""Human-readable output formatting for the atom CLI."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from tabulate import tabulate


def selections_table(items: list[dict]) -> str:
    rows = [
        [s.get("id"), s.get("name"), s.get("job_count"),
         "yes" if s.get("archived") else "no", s.get("updated_at")]
        for s in items
    ]
    return tabulate(rows, headers=["id", "name", "jobs", "archived", "updated"],
                    tablefmt="simple")


def runs_table(items: list[dict]) -> str:
    rows = [
        [r.get("run_id"), r.get("status"), r.get("passed"), r.get("failed"),
         r.get("error"), r.get("started_at")]
        for r in items
    ]
    return tabulate(rows, headers=["run_id", "status", "passed", "failed",
                                   "error", "started"], tablefmt="simple")


def run_summary(status: dict, exit_code: int) -> str:
    return (
        f"{status.get('status')} run={status.get('run_id')} "
        f"passed={status.get('passed')} failed={status.get('failed')} "
        f"error={status.get('error')} exit={exit_code}"
    )


def fmt_seconds(seconds: float | None) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(value):
        return "-"
    total = max(0, int(round(value)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def fmt_time(value: str | None) -> str:
    """Show an API timestamp in UTC; the API stores and returns UTC."""
    if not value:
        return "-"
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    except (TypeError, ValueError, OverflowError):
        return str(value)


def timing_table(timing: dict) -> str:
    rows = [
        [s.get("job_name"), s.get("job_type") or "-",
         (s.get("progress") or {}).get("label") or s.get("status") or "-",
         fmt_seconds(s.get("runtime_seconds")), s.get("attempt") or "-"]
        for s in timing.get("steps") or []
    ]
    return tabulate(rows, headers=["job", "type", "status", "runtime", "tries"],
                    tablefmt="simple", disable_numparse=True)


def timing_summary(timing: dict) -> str:
    line = (
        f"Started: {fmt_time(timing.get('started_at'))}  "
        f"Ended: {fmt_time(timing.get('completed_at'))}  "
        f"Elapsed: {fmt_seconds(timing.get('elapsed_seconds'))}  "
        f"Held: {fmt_seconds(timing.get('held_seconds'))}"
    )
    if timing.get("jobs_total"):
        line += f"  Jobs: {timing.get('jobs_done', 0)}/{timing['jobs_total']}"
    if timing.get("timing_recorded") is False:
        line += "  (timing not recorded for this run)"
    return line


def sequence_runs_table(items: list[dict]) -> str:
    rows = [
        [r.get("run_id"), r.get("status"), fmt_time(r.get("started_at")),
         fmt_time(r.get("completed_at")), fmt_seconds(r.get("elapsed_seconds")),
         fmt_seconds(r.get("held_seconds"))]
        for r in items
    ]
    return tabulate(rows, headers=["run_id", "status", "started", "ended",
                                   "elapsed", "held"], tablefmt="simple",
                   disable_numparse=True)
