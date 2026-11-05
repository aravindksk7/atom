"""Live job progress for `atom run`.

HTTP-only, like the rest of the CLI. The gate result never depends on this
module: the wait loop only calls `tick`, which cannot raise an `Exception`, and
every request here is short and tried once.
"""
from __future__ import annotations

import sys
import time
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.progress_bar import ProgressBar
from rich.table import Table
from rich.text import Text

from etl_framework.cli import render
from etl_framework.cli.client import AtomAPIError, AtomClient, AtomNotFoundError

DISPLAY_TIMEOUT_SECONDS = 2.0
MIN_FETCH_SECONDS = 1.0
MAX_FETCH_FAILURES = 3

_STATUS_STYLES = {
    "PASSED": "green", "APPROVED": "green", "FAILED": "red", "ERROR": "red",
    "SLOW": "yellow",
}
_STATE_STYLES = {
    "running": "blue", "running_unknown": "blue", "over_estimate": "yellow",
    "held": "magenta",
}
_GREY_STATES = frozenset({"queued", "not_run", "carried_over", "interrupted"})
_FAILED_STATUSES = frozenset({"FAILED", "ERROR", "BLOCKED"})


class _ReplacingStream:
    """A stream whose writes replace what its encoding can't represent, not raise."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        encoding = getattr(self._stream, "encoding", None) or "utf-8"
        return self._stream.write(text.encode(encoding, "replace").decode(encoding))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def safe_print(text: str, flush: bool = False) -> None:
    """print() to stdout, except a job name stdout can't encode doesn't raise."""
    if sys.stdout is not None:
        print(text, file=_ReplacingStream(sys.stdout), flush=flush)


def _state(step: dict) -> Any:
    return (step.get("progress") or {}).get("state")


def _style(step: dict) -> str:
    state = _state(step)
    if state in _GREY_STATES:
        return "bright_black"
    return _STATE_STYLES.get(state) or _STATUS_STYLES.get(step.get("status"), "bright_black")


def _tries(step: dict) -> str:
    """" try N" once a job has been retried; attempt is written when a step settles."""
    attempt = step.get("attempt") or 0
    return f" try {attempt}" if isinstance(attempt, int) and attempt > 1 else ""


def _runtime_text(step: dict) -> str:
    runtime = render.fmt_seconds(step.get("runtime_seconds"))
    if _state(step) in ("running", "over_estimate") and step.get("estimate_seconds") is not None:
        return f"{runtime} / ~{render.fmt_seconds(step['estimate_seconds'])}{_tries(step)}"
    return ("" if step.get("runtime_seconds") is None else runtime) + _tries(step)


def _visible_steps(steps: list, max_rows: int | None) -> list:
    """The rows to draw: failed jobs first in line, then unfinished, in run order."""
    if max_rows is None or len(steps) <= max_rows:
        return steps

    def rank(index: int) -> tuple[int, int]:
        step = steps[index]
        if step.get("status") in _FAILED_STATUSES:
            return (0, index)
        if _state(step) in _STATE_STYLES:
            return (1, index)
        return (2 if _state(step) == "queued" else 3, index)

    return [steps[i] for i in sorted(sorted(range(len(steps)), key=rank)[:max_rows])]


def bars(timing: dict, max_rows: int | None = None) -> Table:
    """One colored bar per job, with its label and runtime, under a run header."""
    table = Table.grid(padding=(0, 1))
    # Not no_wrap: rich only narrows wrappable columns, so a long name gives way
    # here (each name is a no_wrap Text) instead of squeezing the label off.
    table.add_column()
    table.add_column(width=30)
    table.add_column(no_wrap=True)
    table.add_column(no_wrap=True)
    table.title = Text(
        f"Elapsed {render.fmt_seconds(timing.get('elapsed_seconds'))} - "
        f"{timing.get('jobs_done', 0)} of {timing.get('jobs_total', 0)} jobs done"
    )
    table.title_justify = "left"
    steps = timing.get("steps") or []
    visible = _visible_steps(steps, max_rows)
    if len(visible) < len(steps):
        table.caption = Text(f"{len(steps) - len(visible)} more jobs not shown")
        table.caption_justify = "left"
    for step in visible:
        progress = step.get("progress") or {}
        style = _style(step)
        percent = progress.get("percent")
        bar = ProgressBar(
            total=100,
            completed=percent or 0,
            pulse=percent is None,
            complete_style=style,
            finished_style=style,
            pulse_style=style,
            width=30,
        )
        # Text, not plain strings: job names may contain [brackets] rich would parse.
        table.add_row(
            Text(str(step.get("job_name") or "-"), no_wrap=True, overflow="ellipsis"),
            bar,
            Text(str(progress.get("label") or step.get("status") or "-"), style=style),
            _runtime_text(step),
        )
    return table


class _Display:
    def __init__(self, client: AtomClient, run_id: str) -> None:
        self._client = client
        self._run_id = run_id
        self._enabled = True
        self._failures = 0
        self._last_tick: float | None = None

    def tick(self, budget: float | None = None) -> None:
        """The wait loop's only way in: a display failure ends here, not in the gate."""
        if not self._enabled or (budget is not None and budget <= 0):
            return
        now = time.monotonic()
        if self._last_tick is not None and now - self._last_tick < MIN_FETCH_SECONDS:
            return
        self._last_tick = now
        try:
            self.update(budget)
        except Exception as exc:
            self._give_up(type(exc).__name__)

    def update(self, budget: float | None = None) -> None:
        raise NotImplementedError

    def _fetch(self, budget: float | None = None) -> Any:
        if not self._enabled:
            return None
        limit = DISPLAY_TIMEOUT_SECONDS if budget is None else min(DISPLAY_TIMEOUT_SECONDS, budget)
        try:
            timing = self._client.get_json(f"/api/runs/{self._run_id}/timing", single_try_timeout=limit)
        except AtomNotFoundError:
            self._enabled = False  # older server: don't ask again
            self._close()
            return None
        except AtomAPIError as exc:
            self._failures += 1  # temporary, unless it keeps happening
            if self._failures >= MAX_FETCH_FAILURES:
                self._give_up(type(exc).__name__)
            return None
        self._failures = 0
        return timing

    def _give_up(self, reason: str) -> None:
        self._enabled = False
        self._close()
        try:
            print(f"note: progress display off ({reason}); still waiting on run status", file=sys.stderr)
        except Exception:
            pass

    def _close(self) -> None:
        return None

    def __enter__(self) -> "_Display":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._close()


class PlainProgress(_Display):
    """For CI and pipes: one line per job status change, no terminal codes."""

    def __init__(self, client: AtomClient, run_id: str) -> None:
        super().__init__(client, run_id)
        self._seen: dict[Any, str] = {}

    def update(self, budget: float | None = None) -> None:
        timing = self._fetch(budget)
        if timing is None:
            return
        for step in timing.get("steps") or []:
            key = step["step_index"] if step.get("step_id") is None else step["step_id"]
            if step["status"] == self._seen.get(key):
                continue
            first_sighting_queued = key not in self._seen and step["status"] == "PENDING"
            self._seen[key] = step["status"]
            if first_sighting_queued:
                continue
            runtime = render.fmt_seconds(step.get("runtime_seconds"))
            safe_print(f"{render.fmt_time(timing.get('server_time'))} {step['job_name']} "
                       f"{step['status']} runtime={runtime}{_tries(step)}", flush=True)


class LiveProgress(_Display):
    """For an interactive terminal: colored bars redrawn in place."""

    def __init__(self, client: AtomClient, run_id: str, console: Console | None = None) -> None:
        super().__init__(client, run_id)
        self._console = console or Console(file=_ReplacingStream(sys.stdout))
        self._live: Live | None = None

    def update(self, budget: float | None = None) -> None:
        timing = self._fetch(budget)
        if timing is None:
            return
        # Header, caption and the line the cursor rests on.
        rendered = bars(timing, max_rows=max(1, self._console.height - 3))
        if self._live is None:
            # Everything draws here, inside tick: no refresh thread, no stdout swap.
            self._live = Live(rendered, console=self._console, transient=True, auto_refresh=False,
                              redirect_stdout=False, redirect_stderr=False)
            self._live.start(refresh=True)
        else:
            self._live.update(rendered, refresh=True)

    def _close(self) -> None:
        live, self._live = self._live, None
        if live is None:
            return
        try:
            live.stop()
        except Exception:
            pass
        try:
            self._console.show_cursor(True)
        except Exception:
            pass
