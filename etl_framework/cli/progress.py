"""Live job progress for `atom run`.

HTTP-only, like the rest of the CLI. A failure here must never affect the gate
result, so every timing request is allowed to fail quietly.
"""
from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from etl_framework.cli import render
from etl_framework.cli.client import (
    AtomAPIError,
    AtomAuthError,
    AtomClient,
    AtomConnectionError,
    AtomNotFoundError,
)

_STATUS_STYLES = {
    "PASSED": "green", "APPROVED": "green", "FAILED": "red", "ERROR": "red",
    "SLOW": "yellow",
}
_STYLE_STYLES = {
    "running": "blue", "running_unknown": "blue", "over_estimate": "yellow",
    "held": "magenta",
}
_GREY_STATES = frozenset({"queued", "not_run", "carried_over", "interrupted"})


def _style(step: dict) -> str:
    state = step["progress"]["state"]
    if state in _GREY_STATES:
        return "bright_black"
    return _STYLE_STYLES.get(state) or _STATUS_STYLES.get(step["status"], "bright_black")


def _runtime_text(step: dict) -> str:
    runtime = render.fmt_seconds(step.get("runtime_seconds"))
    state = step["progress"]["state"]
    if state in ("running", "over_estimate") and step.get("estimate_seconds") is not None:
        return f"{runtime} / ~{render.fmt_seconds(step['estimate_seconds'])}"
    return "" if step.get("runtime_seconds") is None else runtime


def bars(timing: dict) -> Table:
    """One colored bar per job, with its label and runtime, under a run header."""
    table = Table.grid(padding=(0, 1))
    table.add_column(no_wrap=True)
    table.add_column(width=30)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.title = (
        f"Elapsed {render.fmt_seconds(timing.get('elapsed_seconds'))} - "
        f"{timing.get('jobs_done', 0)} of {timing.get('jobs_total', 0)} jobs done"
    )
    table.title_justify = "left"
    for step in timing.get("steps", []):
        progress = step["progress"]
        style = _style(step)
        bar = ProgressBar(
            total=100,
            completed=progress["percent"] or 0,
            pulse=progress["percent"] is None,
            complete_style=style,
            finished_style=style,
            pulse_style=style,
            width=30,
        )
        # Text, not plain strings: job names may contain [brackets] rich would parse.
        table.add_row(Text(step["job_name"]), bar, Text(progress["label"], style=style), _runtime_text(step))
    return table


class _Display:
    def __init__(self, client: AtomClient, run_id: str) -> None:
        self._client = client
        self._run_id = run_id
        self._enabled = True

    def _fetch(self) -> dict | None:
        if not self._enabled:
            return None
        try:
            return self._client.get_json(f"/api/runs/{self._run_id}/timing")
        except AtomNotFoundError:
            self._enabled = False  # older server: don't ask again
        except (AtomAPIError, AtomAuthError, AtomConnectionError):
            pass  # temporary: try again on the next tick
        return None

    def __enter__(self) -> "_Display":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None


class PlainProgress(_Display):
    """For CI and pipes: one line per job status change, no terminal codes."""

    def __init__(self, client: AtomClient, run_id: str) -> None:
        super().__init__(client, run_id)
        self._seen: dict[int, str] = {}

    def update(self) -> None:
        timing = self._fetch()
        if timing is None:
            return
        for step in timing.get("steps", []):
            key = step["step_index"] if step.get("step_id") is None else step["step_id"]
            if step["status"] == self._seen.get(key):
                continue
            first_sighting_queued = key not in self._seen and step["status"] == "PENDING"
            self._seen[key] = step["status"]
            if first_sighting_queued:
                continue
            runtime = render.fmt_seconds(step.get("runtime_seconds"))
            print(f"{render.fmt_time(timing.get('server_time'))} {step['job_name']} "
                  f"{step['status']} runtime={runtime}", flush=True)


class LiveProgress(_Display):
    """For an interactive terminal: colored bars redrawn in place."""

    def __init__(self, client: AtomClient, run_id: str, console: Console | None = None) -> None:
        super().__init__(client, run_id)
        self._console = console or Console()
        self._live: Live | None = None

    def update(self) -> None:
        timing = self._fetch()
        if timing is None:
            return
        if self._live is None:
            self._live = Live(bars(timing), console=self._console, transient=True)
            self._live.start()
        else:
            self._live.update(bars(timing))

    def __exit__(self, *exc_info: Any) -> None:
        if self._live is not None:
            self._live.stop()