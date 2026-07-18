"""Human-readable output formatting for the atom CLI."""
from __future__ import annotations

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
