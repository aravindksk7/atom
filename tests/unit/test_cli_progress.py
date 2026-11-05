"""atom CLI: timing tables, new commands and live progress."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from etl_framework.cli import render
from etl_framework.cli.client import AtomConnectionError, AtomNotFoundError

REPO_ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()
BASE_ARGS = ["--api-url", "http://atom.test", "--token", "t0k3n"]


def step(step_id, status, state, percent, label, runtime=None, estimate=None, attempt=1, job_type="bo_report"):
    return {
        "step_id": step_id, "step_index": 0, "job_name": step_id, "job_type": job_type,
        "job_family": "sap", "status": status, "attempt": attempt, "started_at": None,
        "finished_at": None, "held_at": None, "hold_ended_at": None,
        "runtime_seconds": runtime, "estimate_seconds": estimate, "held_seconds": 0.0,
        "progress": {"state": state, "percent": percent, "label": label},
    }


def timing(steps=None, status="PASSED"):
    return {
        "run_id": "r-1", "status": status, "server_time": "2026-11-04T09:10:00+00:00",
        "started_at": "2026-11-04T09:00:00+00:00", "completed_at": "2026-11-04T09:10:00+00:00",
        "elapsed_seconds": 600.0, "held_seconds": 30.0, "active_seconds": 570.0,
        "jobs_total": len(steps or []), "jobs_done": len(steps or []),
        "timing_recorded": True, "steps": steps or [],
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []
        self.kwargs = []

    def get_json(self, path, **kwargs):
        self.calls.append(path)
        self.kwargs.append(kwargs)
        result = self.responses.get(path, AtomNotFoundError(f"no fake for {path}"))
        if isinstance(result, Exception):
            raise result
        if isinstance(result, list) and result and isinstance(result[0], dict) and "__seq__" in result[0]:
            return result.pop(0)["__seq__"] if len(result) > 1 else result[0]["__seq__"]
        return result

    def post_json(self, path, payload):
        self.calls.append(path)
        return self.responses[path]

    def get_bytes(self, path):
        return b"<testsuite/>"


@pytest.fixture
def fake_client(monkeypatch):
    def install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr("etl_framework.cli.app._make_client", lambda api_url, token: client)
        return client
    return install


@pytest.mark.parametrize("seconds,text", [
    (None, "-"), (0, "0s"), (42.4, "42s"), (60, "1m 00s"), (217, "3m 37s"), (3725, "1h 02m 05s"),
])
def test_fmt_seconds(seconds, text):
    assert render.fmt_seconds(seconds) == text


def test_timing_table_shows_job_type_status_and_runtime():
    table = render.timing_table(timing([
        step("recon", "FAILED", "done", 100, "FAILED", runtime=182.0, attempt=2, job_type="reconciliation"),
        step("publish", "BLOCKED", "not_run", 0, "BLOCKED"),
    ]))
    assert "recon" in table and "reconciliation" in table and "FAILED" in table
    assert "3m 02s" in table
    recon_row = next(line for line in table.splitlines() if line.startswith("recon"))
    assert recon_row.split()[-1] == "2"
    assert "publish" in table and "BLOCKED" in table


def test_timing_table_handles_missing_values():
    never_run = step("publish", "BLOCKED", "not_run", 0, "BLOCKED", job_type=None, attempt=0)
    never_run["progress"] = None
    lines = render.timing_table(timing([never_run])).splitlines()
    assert "publish" in lines[-1]
    assert lines[-1].split()[-1] == "-"


def test_timing_summary_has_start_end_elapsed():
    summary = render.timing_summary(timing())
    assert "Started" in summary and "Ended" in summary
    assert "Elapsed: 10m 00s" in summary
    assert "Held: 30s" in summary


def test_timing_summary_of_live_run_has_no_end():
    doc = timing(status="RUNNING")
    doc["completed_at"] = None
    assert "Ended: -" in render.timing_summary(doc)


def test_timing_summary_without_recorded_timing_says_so():
    doc = timing()
    doc.update(timing_recorded=False, started_at=None, completed_at=None,
               elapsed_seconds=None, held_seconds=0.0, active_seconds=None)
    summary = render.timing_summary(doc)
    assert "Elapsed: -" in summary
    assert "not recorded" in summary


def test_timing_summary_reports_jobs_done():
    doc = timing([step("a", "PASSED", "done", 100, "PASSED", runtime=1.0),
                  step("b", "BLOCKED", "not_run", 0, "BLOCKED")])
    doc["jobs_done"] = 1
    assert "Jobs: 1/2" in render.timing_summary(doc)


def test_times_are_shown_in_utc():
    assert render.fmt_time("2026-11-04T09:00:00+00:00") == "2026-11-04 09:00:00Z"
    assert render.fmt_time("2026-11-04T11:00:00+02:00") == "2026-11-04 09:00:00Z"
    assert render.fmt_time("2026-11-04T09:00:00") == "2026-11-04 09:00:00Z"
    assert render.fmt_time(None) == "-"


@pytest.mark.parametrize("seconds,text", [
    (59.9, "1m 00s"), (3599.5, "1h 00m 00s"), (float("nan"), "-"), (float("inf"), "-"),
    (float("-inf"), "-"), ("abc", "-"), (-5, "0s"), (-0.4, "0s"), ("12", "12s"),
])
def test_fmt_seconds_never_raises(seconds, text):
    assert render.fmt_seconds(seconds) == text


@pytest.mark.parametrize("value", ["garbage", "2026-13-45", 12345])
def test_fmt_time_returns_raw_for_unparsable(value):
    assert render.fmt_time(value) == str(value)


def test_tables_keep_numeric_looking_text_exact():
    table = render.timing_table(timing([
        step("001", "PASSED", "done", 100, "PASSED", runtime=1.0),
        step("1e3", "PASSED", "done", 100, "PASSED", runtime=1.0),
    ]))
    rows = [line.split()[0] for line in table.splitlines()[2:]]
    assert rows == ["001", "1e3"]
    runs = render.sequence_runs_table([{"run_id": "0042", "status": "PASSED"}])
    assert runs.splitlines()[2].split()[0] == "0042"


def test_timing_table_tolerates_null_steps():
    doc = timing()
    doc["steps"] = None
    assert "job" in render.timing_table(doc)


def test_timing_and_sequence_runs_quote_path_segments(fake_client):
    from etl_framework.cli.app import app

    client = fake_client({})
    runner.invoke(app, BASE_ARGS + ["timing", "a/b"])
    assert client.calls == ["/api/runs/a%2Fb/timing"]
    client = fake_client({"/api/sequences": [{"id": 7, "name": "a/b"}]})
    runner.invoke(app, BASE_ARGS + ["sequence-runs", "a/b"])
    assert "/api/sequences/7/runs?limit=20" in client.calls


def test_timing_command(fake_client):
    from etl_framework.cli.app import app

    fake_client({"/api/runs/r-1/timing": timing([step("a", "PASSED", "done", 100, "PASSED", runtime=5.0)])})
    result = runner.invoke(app, BASE_ARGS + ["timing", "r-1"])
    assert result.exit_code == 0
    assert "Elapsed: 10m 00s" in result.output


def test_timing_command_json(fake_client):
    from etl_framework.cli.app import app

    fake_client({"/api/runs/r-1/timing": timing()})
    result = runner.invoke(app, BASE_ARGS + ["--output", "json", "timing", "r-1"])
    assert json.loads(result.output)["elapsed_seconds"] == 600.0


def test_timing_command_unknown_run_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({})
    assert runner.invoke(app, BASE_ARGS + ["timing", "nope"]).exit_code == 4


def test_timing_command_connection_error_exits_5(fake_client):
    from etl_framework.cli.app import app

    fake_client({"/api/runs/r-1/timing": AtomConnectionError("down")})
    assert runner.invoke(app, BASE_ARGS + ["timing", "r-1"]).exit_code == 5


def test_sequence_runs_by_name(fake_client):
    from etl_framework.cli.app import app

    client = fake_client({
        "/api/sequences": [{"id": 7, "name": "nightly"}],
        "/api/sequences/7/runs?limit=5": [{
            "run_id": "r-9", "status": "PASSED", "started_at": "2026-11-04T09:00:00+00:00",
            "completed_at": "2026-11-04T09:01:00+00:00", "elapsed_seconds": 60.0,
            "held_seconds": 0.0, "active_seconds": 60.0,
        }],
    })
    result = runner.invoke(app, BASE_ARGS + ["sequence-runs", "nightly", "--limit", "5"])
    assert result.exit_code == 0
    assert "r-9" in result.output and "1m 00s" in result.output
    assert "/api/sequences/7/runs?limit=5" in client.calls


def test_sequence_runs_by_id_skips_lookup(fake_client):
    from etl_framework.cli.app import app

    client = fake_client({"/api/sequences/7/runs?limit=20": []})
    result = runner.invoke(app, BASE_ARGS + ["sequence-runs", "7"])
    assert result.exit_code == 0
    assert client.calls == ["/api/sequences/7/runs?limit=20"]


def test_sequence_runs_unknown_name_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({"/api/sequences": []})
    assert runner.invoke(app, BASE_ARGS + ["sequence-runs", "ghost"]).exit_code == 4


def test_sequence_runs_ambiguous_name_exits_3(fake_client):
    from etl_framework.cli.app import app

    fake_client({"/api/sequences": [{"id": 1, "name": "dup"}, {"id": 2, "name": "dup"}]})
    assert runner.invoke(app, BASE_ARGS + ["sequence-runs", "dup"]).exit_code == 3


def test_sequence_runs_json_output_and_error_path(fake_client, capsys):
    from etl_framework.cli.app import app

    rows = [{"run_id": "r-9", "status": "PASSED", "started_at": None, "completed_at": None,
             "elapsed_seconds": None, "held_seconds": 0.0, "active_seconds": None}]
    fake_client({"/api/sequences/7/runs?limit=20": rows})
    ok = runner.invoke(app, BASE_ARGS + ["--output", "json", "sequence-runs", "7"])
    assert json.loads(ok.output) == rows
    # a run with no recorded times renders dashes, not a crash
    fake_client({"/api/sequences/7/runs?limit=20": rows})
    text = runner.invoke(app, BASE_ARGS + ["sequence-runs", "7"])
    assert text.exit_code == 0 and "r-9" in text.output
    # JSON mode keeps stdout empty on failure; the error is a JSON document on stderr
    fake_client({})
    failed = runner.invoke(app, BASE_ARGS + ["--output", "json", "sequence-runs", "7"])
    assert failed.exit_code == 4
    assert failed.stdout == ""
    assert json.loads(failed.stderr)["exit_code"] == 4


def test_sequence_runs_rejects_limit_out_of_range(fake_client):
    from etl_framework.cli.app import app

    fake_client({})
    assert runner.invoke(app, BASE_ARGS + ["sequence-runs", "7", "--limit", "201"]).exit_code != 0
    assert runner.invoke(app, BASE_ARGS + ["sequence-runs", "7", "--limit", "0"]).exit_code != 0


def test_tables_print_under_cp1252_without_color():
    """Piped output on a Windows console encoding must not raise."""
    code = (
        "from etl_framework.cli import render\n"
        "doc = {'steps': [{'job_name': 'a', 'job_type': 'x', 'attempt': 1, 'runtime_seconds': None,"
        " 'progress': {'label': 'PASSED'}}], 'started_at': '2026-11-04T09:00:00+00:00',"
        " 'completed_at': None, 'elapsed_seconds': None, 'held_seconds': 0.0}\n"
        "print(render.timing_table(doc)); print(render.timing_summary(doc))\n"
        "print(render.sequence_runs_table([{'run_id': 'r', 'status': 'PASSED'}]))\n"
    )
    env = {**__import__("os").environ, "PYTHONIOENCODING": "cp1252", "NO_COLOR": "1"}
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, env=env, timeout=60,
                          cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    proc.stdout.decode("ascii")
    assert b"\x1b[" not in proc.stdout


def test_plain_progress_prints_one_line_per_status_change(capsys):
    from etl_framework.cli.progress import PlainProgress

    docs = [
        timing([step("a", "RUNNING", "running_unknown", None, "running", runtime=1.0)], "RUNNING"),
        timing([step("a", "RUNNING", "running_unknown", None, "running", runtime=2.0)], "RUNNING"),
        timing([step("a", "PASSED", "done", 100, "PASSED", runtime=3.0)], "RUNNING"),
    ]
    client = FakeClient({"/api/runs/r-1/timing": [{"__seq__": d} for d in docs]})
    display = PlainProgress(client, "r-1")
    for _ in docs:
        display.update()
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert "a" in out[0] and "RUNNING" in out[0]
    assert "PASSED" in out[1] and "3s" in out[1]
    assert "\x1b" not in "".join(out)


@pytest.mark.parametrize("error", [AtomNotFoundError("old"), AtomConnectionError("down")])
def test_progress_ignores_timing_errors(capsys, error):
    from etl_framework.cli.progress import PlainProgress

    client = FakeClient({"/api/runs/r-1/timing": error})
    PlainProgress(client, "r-1").update()
    assert capsys.readouterr().out == ""


def test_not_found_stops_asking_for_timing():
    from etl_framework.cli.progress import PlainProgress

    client = FakeClient({})
    display = PlainProgress(client, "r-1")
    display.update()
    display.update()
    assert client.calls == ["/api/runs/r-1/timing"]


def test_live_bars_show_each_state():
    from io import StringIO

    from rich.console import Console

    from etl_framework.cli.progress import bars

    doc = timing([
        step("done_job", "PASSED", "done", 100, "PASSED", runtime=5.0),
        step("run_job", "RUNNING", "running", 62, "62%", runtime=217.0, estimate=350.0),
        step("slow_job", "RUNNING", "over_estimate", 95, "over estimate", runtime=400.0, estimate=240.0),
        step("new_job", "RUNNING", "running_unknown", None, "running", runtime=9.0),
        step("wait_job", "PENDING", "queued", 0, "queued"),
    ], "RUNNING")
    stream = StringIO()
    Console(file=stream, width=120, force_terminal=False).print(bars(doc))
    text = stream.getvalue()
    for expected in ("done_job", "PASSED", "62%", "3m 37s / ~5m 50s", "over estimate", "queued"):
        assert expected in text


def _launch_responses(status="PASSED", timing_doc=None):
    responses = {
        "/api/selections/3/launch": {"run_id": "r-1"},
        "/api/runs/r-1/status": {
            "run_id": "r-1", "status": status, "passed": 1, "failed": 0, "error": 0,
        },
    }
    if timing_doc is not None:
        responses["/api/runs/r-1/timing"] = timing_doc
    return responses


RUN_ARGS = ["run", "3", "--source-env", "dev", "--poll-interval", "0", "--timeout", "1"]


def test_run_prints_table_and_times_when_not_a_terminal(fake_client):
    from etl_framework.cli.app import app

    doc = timing([step("a", "PASSED", "done", 100, "PASSED", runtime=5.0)])
    fake_client(_launch_responses(timing_doc=doc))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0
    assert "Elapsed: 10m 00s" in result.output
    assert "run=r-1" in result.output
    assert "\x1b[" not in result.output


def test_run_against_older_server_still_gates(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses())
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0
    assert "Elapsed" not in result.output
    assert "run=r-1" in result.output


def test_run_json_is_one_document_with_times(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(timing_doc=timing()))
    result = runner.invoke(app, BASE_ARGS + ["--output", "json"] + RUN_ARGS)
    body = json.loads(result.output)
    assert body["elapsed_seconds"] == 600.0
    assert body["held_seconds"] == 30.0
    assert body["started_at"] == "2026-11-04T09:00:00+00:00"
    assert body["verdict"] == "PASSED"


def test_run_json_against_older_server_has_null_times(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses())
    result = runner.invoke(app, BASE_ARGS + ["--output", "json"] + RUN_ARGS)
    assert json.loads(result.output)["elapsed_seconds"] is None


# --- The gate never depends on the display ------------------------------------


def _running_then(final="PASSED", timing_doc=None):
    """Status polls: RUNNING once, then `final`, so the display gets one tick."""
    responses = _launch_responses(status=final, timing_doc=timing_doc)
    terminal = responses["/api/runs/r-1/status"]
    responses["/api/runs/r-1/status"] = [
        {"__seq__": {**terminal, "status": "RUNNING"}},
        {"__seq__": terminal},
    ]
    return responses


def _broken_step(**changes):
    return {**step("a", "RUNNING", "running", 50, "50%", runtime=1.0), **changes}


def _without(key):
    broken = step("a", "RUNNING", "running", 50, "50%", runtime=1.0)
    del broken[key]
    return broken


def _with_doc(**changes):
    return {**timing([step("a", "PASSED", "done", 100, "PASSED", runtime=1.0)], "RUNNING"), **changes}


NOT_JSON = json.JSONDecodeError("Expecting value", "<html>proxy</html>", 0)
MALFORMED = {
    "html_page": NOT_JSON,
    "list": [],
    "text": "x",
    "number": 3,
    "steps_null": _with_doc(steps=None),
    "steps_text": _with_doc(steps="abc"),
    "step_is_number": _with_doc(steps=[1]),
    "step_no_status": _with_doc(steps=[_without("status")]),
    "progress_missing": _with_doc(steps=[_without("progress")]),
    "progress_null": _with_doc(steps=[_broken_step(progress=None)]),
    "percent_text": _with_doc(steps=[_broken_step(progress={"state": "running", "percent": "abc", "label": "x"})]),
    "label_null": _with_doc(steps=[_broken_step(progress={"state": "running", "percent": 5, "label": None})]),
    "name_null": _with_doc(steps=[_broken_step(job_name=None)]),
    "markup_in_jobs_done": _with_doc(jobs_done="[/x]"),
    "step_id_list": _with_doc(steps=[_broken_step(step_id=[1])]),
}
GATE_CODES = {"PASSED": 0, "ERROR": 3, "FAILED": 1}


@pytest.mark.parametrize("name", sorted(MALFORMED))
@pytest.mark.parametrize("final", sorted(GATE_CODES))
@pytest.mark.parametrize("mode", ["plain", "live", "json"])
def test_malformed_timing_never_changes_the_gate(fake_client, monkeypatch, tmp_path, name, final, mode):
    from etl_framework.cli.app import app

    if mode == "live":
        monkeypatch.setattr("etl_framework.cli.app._interactive", lambda: True)
        monkeypatch.setenv("FORCE_COLOR", "1")
    fake_client(_running_then(final, MALFORMED[name]))
    junit = tmp_path / "junit.xml"
    args = BASE_ARGS + (["--output", "json"] if mode == "json" else [])
    result = runner.invoke(app, args + RUN_ARGS + ["--junit-out", str(junit)])
    assert result.exit_code == GATE_CODES[final], result.output
    assert junit.read_bytes() == b"<testsuite/>"
    if mode == "json":
        assert json.loads(result.stdout)["exit_code"] == GATE_CODES[final]
    else:
        assert f"exit={GATE_CODES[final]}" in result.stdout


@pytest.mark.parametrize("payload", [NOT_JSON, [], "x", 3], ids=["html", "list", "text", "number"])
def test_json_output_survives_unusable_final_timing(fake_client, payload):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(timing_doc=payload))
    result = runner.invoke(app, BASE_ARGS + ["--output", "json"] + RUN_ARGS)
    assert result.exit_code == 0
    body = json.loads(result.stdout)
    assert body["verdict"] == "PASSED"
    assert all(body[key] is None for key in (
        "started_at", "completed_at", "elapsed_seconds", "held_seconds", "active_seconds"))


def test_json_output_nulls_values_that_are_not_times(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(timing_doc=_with_doc(
        elapsed_seconds=float("nan"), held_seconds="abc", active_seconds=True, started_at=3)))
    result = runner.invoke(app, BASE_ARGS + ["--output", "json"] + RUN_ARGS)
    body = json.loads(result.stdout, parse_constant=lambda bad: pytest.fail(f"invalid JSON: {bad}"))
    assert body["elapsed_seconds"] is None and body["held_seconds"] is None
    assert body["active_seconds"] is None and body["started_at"] is None
    assert body["completed_at"] == "2026-11-04T09:10:00+00:00"


def test_text_report_falls_back_to_the_summary_line(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(timing_doc=_with_doc(steps=[1])))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0
    assert result.stdout.strip().splitlines() == ["PASSED run=r-1 passed=1 failed=0 error=0 exit=0"]


def test_finish_prints_job_table_then_times_then_summary(fake_client):
    from etl_framework.cli.app import app

    doc = timing([step("job_a", "PASSED", "done", 100, "PASSED", runtime=5.0)])
    fake_client(_launch_responses(timing_doc=doc))
    out = runner.invoke(app, BASE_ARGS + RUN_ARGS).stdout
    assert out.index("job_a") < out.index("Started:") < out.index("Elapsed: 10m 00s") < out.index("run=r-1")


def test_tick_survives_any_display_error_once_and_notes_it_on_stderr(capsys):
    from etl_framework.cli.progress import PlainProgress

    client = FakeClient({"/api/runs/r-1/timing": "x"})
    display = PlainProgress(client, "r-1")
    display.tick()
    display.tick()
    captured = capsys.readouterr()
    assert client.calls == ["/api/runs/r-1/timing"]
    assert captured.out == ""
    assert len(captured.err.strip().splitlines()) == 1
    assert "progress" in captured.err and "status" in captured.err


def test_tick_does_not_swallow_ctrl_c():
    from etl_framework.cli.progress import PlainProgress

    display = PlainProgress(FakeClient({}), "r-1")
    display.update = lambda budget=None: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        display.tick()


def test_older_server_is_not_a_display_failure(capsys):
    from etl_framework.cli.progress import PlainProgress

    PlainProgress(FakeClient({}), "r-1").tick()
    assert capsys.readouterr().err == ""


def test_plain_progress_tolerates_a_closed_stdout_through_the_tick(monkeypatch):
    from etl_framework.cli.progress import PlainProgress

    class Closed(io.StringIO):
        def write(self, text):
            raise OSError(22, "Invalid argument")

    doc = timing([step("a", "RUNNING", "running", 5, "5%")], "RUNNING")
    display = PlainProgress(FakeClient({"/api/runs/r-1/timing": doc}), "r-1")
    monkeypatch.setattr(sys, "stdout", Closed())
    display.tick()
    assert display._enabled is False


# --- Slow /timing --------------------------------------------------------------


def test_display_requests_are_short_and_single_try():
    from etl_framework.cli.progress import DISPLAY_TIMEOUT_SECONDS, PlainProgress

    client = FakeClient({"/api/runs/r-1/timing": timing()})
    PlainProgress(client, "r-1").tick()
    assert client.kwargs == [{"single_try_timeout": DISPLAY_TIMEOUT_SECONDS}]
    assert DISPLAY_TIMEOUT_SECONDS <= 2


def test_display_request_never_runs_past_the_wait_budget():
    from etl_framework.cli.progress import PlainProgress

    client = FakeClient({"/api/runs/r-1/timing": timing()})
    PlainProgress(client, "r-1").tick(0.25)
    assert client.kwargs == [{"single_try_timeout": 0.25}]


def test_display_with_no_budget_left_asks_nothing():
    from etl_framework.cli.progress import PlainProgress

    client = FakeClient({"/api/runs/r-1/timing": timing()})
    PlainProgress(client, "r-1").tick(0.0)
    assert client.calls == []


def test_final_timing_is_short_and_single_try(fake_client):
    from etl_framework.cli.app import app
    from etl_framework.cli.progress import DISPLAY_TIMEOUT_SECONDS

    client = fake_client(_launch_responses(timing_doc=timing()))
    runner.invoke(app, BASE_ARGS + RUN_ARGS)
    gets = [path for path in client.calls if path != "/api/selections/3/launch"]
    timing_kwargs = [kw for path, kw in zip(gets, client.kwargs) if path.endswith("/timing")]
    assert timing_kwargs == [{"single_try_timeout": DISPLAY_TIMEOUT_SECONDS}]


def test_client_single_try_does_not_retry_and_uses_the_given_timeout(monkeypatch):
    import requests

    from etl_framework.cli.client import AtomClient

    seen = []

    def refuse(self, method, url, **kwargs):
        seen.append(kwargs["timeout"])
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(requests.Session, "request", refuse)
    with pytest.raises(AtomConnectionError):
        AtomClient("http://atom.test").get_json("/x", single_try_timeout=1.5)
    assert seen == [1.5]


def test_client_single_try_still_maps_http_errors(monkeypatch):
    import requests

    from etl_framework.cli.client import AtomClient

    response = requests.Response()
    response.status_code = 404
    response._content = b'{"detail": "gone"}'
    monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: response)
    with pytest.raises(AtomNotFoundError):
        AtomClient("http://atom.test").get_json("/x", single_try_timeout=1.5)


def test_repeated_failures_stop_the_asking_and_one_success_resets_the_count(monkeypatch, capsys):
    from etl_framework.cli import progress

    monkeypatch.setattr(progress, "MIN_FETCH_SECONDS", 0)
    down = AtomConnectionError("down")
    seq = [down, down, timing(), down, down, down, down, down]

    class Sequenced(FakeClient):
        def get_json(self, path, **kwargs):
            self.calls.append(path)
            result = seq.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

    client = Sequenced({})
    display = progress.PlainProgress(client, "r-1")
    for _ in range(8):
        display.tick()
    assert len(client.calls) == 3 + progress.MAX_FETCH_FAILURES
    assert progress.MAX_FETCH_FAILURES <= 5
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_display_fetches_at_most_once_a_second(monkeypatch):
    from etl_framework.cli import progress

    clock = [100.0]
    monkeypatch.setattr(progress.time, "monotonic", lambda: clock[0])
    client = FakeClient({"/api/runs/r-1/timing": timing()})
    display = progress.PlainProgress(client, "r-1")
    display.tick()
    clock[0] += 0.5
    display.tick()
    assert len(client.calls) == 1
    clock[0] += 0.5
    display.tick()
    assert len(client.calls) == 2


class _FakeTime:
    """The wait loop's clock: sleeping and slow ticks move it, nothing else does."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class _Statuses:
    def __init__(self, *statuses):
        self.statuses = list(statuses)

    def get_json(self, path, **kwargs):
        return {"status": self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]}


def test_wait_returns_a_terminal_status_without_ticking(monkeypatch):
    from etl_framework.cli import app as cli_app

    monkeypatch.setattr(cli_app, "time", _FakeTime())
    ticks = []
    status = cli_app._wait_for_run(_Statuses("FAILED"), "r-1", 10, 1, ticks.append)
    assert status["status"] == "FAILED"
    assert ticks == []


def test_wait_times_out_without_ticking(monkeypatch):
    from etl_framework.cli import app as cli_app

    monkeypatch.setattr(cli_app, "time", _FakeTime())
    ticks = []
    with pytest.raises(cli_app.WaitTimeoutError):
        cli_app._wait_for_run(_Statuses("RUNNING"), "r-1", 0, 1, ticks.append)
    assert ticks == []


def test_wait_gives_the_tick_only_the_time_left(monkeypatch):
    from etl_framework.cli import app as cli_app

    monkeypatch.setattr(cli_app, "time", _FakeTime())
    budgets = []
    with pytest.raises(cli_app.WaitTimeoutError):
        cli_app._wait_for_run(_Statuses("RUNNING"), "r-1", 3, 1, budgets.append)
    assert budgets == [3, 2, 1]


def test_slow_tick_comes_off_the_poll_interval(monkeypatch):
    from etl_framework.cli import app as cli_app

    clock = _FakeTime()
    monkeypatch.setattr(cli_app, "time", clock)

    def slow_tick(budget):
        clock.now += 2.0

    cli_app._wait_for_run(_Statuses("RUNNING", "RUNNING", "PASSED"), "r-1", 100, 5, slow_tick)
    assert clock.sleeps == [3.0, 3.0]

    clock.sleeps.clear()
    cli_app._wait_for_run(_Statuses("RUNNING", "PASSED"), "r-1", 100, 1, slow_tick)
    assert clock.sleeps == [0.0]


# --- Which display ----------------------------------------------------------


class _Tty(io.StringIO):
    def __init__(self, tty=True):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.mark.parametrize("value,interactive", [
    (None, True), ("", True), ("false", True), ("FALSE", True), ("0", True),
    ("1", False), ("true", False), ("TRUE", False), ("yes", False),
])
def test_ci_env_turns_bars_off_on_a_terminal(monkeypatch, value, interactive):
    from etl_framework.cli import app as cli_app

    monkeypatch.setattr(sys, "stdout", _Tty())
    if value is None:
        monkeypatch.delenv("CI", raising=False)
    else:
        monkeypatch.setenv("CI", value)
    assert cli_app._interactive() is interactive


def test_not_a_terminal_means_no_bars(monkeypatch):
    from etl_framework.cli import app as cli_app

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "stdout", _Tty(tty=False))
    assert cli_app._interactive() is False


def test_closed_stdout_means_no_bars_rather_than_a_failed_gate(monkeypatch):
    from etl_framework.cli import app as cli_app

    closed = io.StringIO()
    closed.close()
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "stdout", closed)
    assert cli_app._interactive() is False


def _spy_wait(monkeypatch):
    from etl_framework.cli import app as cli_app

    seen = {}

    def wait(client, run_id, timeout, poll_interval, on_tick=None):
        seen.update(poll=poll_interval, display=getattr(on_tick, "__self__", None), on_tick=on_tick)
        return {"status": "PASSED"}

    monkeypatch.setattr(cli_app, "_wait_for_run", wait)
    return seen


@pytest.mark.parametrize("poll,expected", [(10.0, 2.0), (2.0, 2.0), (0.5, 0.5)])
def test_terminal_gets_bars_and_a_two_second_redraw(monkeypatch, poll, expected):
    from etl_framework.cli import app as cli_app
    from etl_framework.cli.progress import LiveProgress

    seen = _spy_wait(monkeypatch)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "stdout", _Tty())
    cli_app._wait_with_progress(FakeClient({}), "r-1", 10, poll, "text")
    assert isinstance(seen["display"], LiveProgress)
    assert seen["poll"] == expected


def test_pipe_gets_plain_lines_at_the_asked_interval(monkeypatch):
    from etl_framework.cli import app as cli_app
    from etl_framework.cli.progress import PlainProgress

    seen = _spy_wait(monkeypatch)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "stdout", _Tty(tty=False))
    cli_app._wait_with_progress(FakeClient({}), "r-1", 10, 10.0, "text")
    assert isinstance(seen["display"], PlainProgress)
    assert seen["poll"] == 10.0


def test_json_mode_has_no_display_on_a_terminal(monkeypatch):
    from etl_framework.cli import app as cli_app

    seen = _spy_wait(monkeypatch)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(sys, "stdout", _Tty())
    cli_app._wait_with_progress(FakeClient({}), "r-1", 10, 10.0, "json")
    assert seen["on_tick"] is None
    assert seen["poll"] == 10.0


def test_json_mode_does_not_ask_for_timing_during_the_wait(fake_client):
    from etl_framework.cli.app import app

    doc = timing([step("a", "RUNNING", "running", 5, "5%")], "RUNNING")
    client = fake_client(_running_then("PASSED", doc))
    result = runner.invoke(app, BASE_ARGS + ["--output", "json"] + RUN_ARGS)
    json.loads(result.stdout)
    assert client.calls.count("/api/runs/r-1/timing") == 1


# --- Plain lines ------------------------------------------------------------


def test_first_sighting_of_a_queued_job_prints_nothing(capsys):
    from etl_framework.cli.progress import PlainProgress

    docs = [
        timing([step("a", "PENDING", "queued", 0, "queued")], "RUNNING"),
        timing([step("a", "RUNNING", "running_unknown", None, "running", runtime=1.0)], "RUNNING"),
    ]
    client = FakeClient({"/api/runs/r-1/timing": [{"__seq__": d} for d in docs]})
    display = PlainProgress(client, "r-1")
    display.update()
    assert capsys.readouterr().out == ""
    display.update()
    assert "RUNNING" in capsys.readouterr().out


# --- Bars ----------------------------------------------------------------------


COLORS = {"green": "32", "red": "31", "blue": "34", "yellow": "33", "magenta": "35", "grey": "90"}


def _rendered(doc, width=120, color=False):
    from rich.console import Console

    from etl_framework.cli.progress import bars

    stream = io.StringIO()
    Console(file=stream, width=width, force_terminal=color, color_system="standard" if color else None,
            no_color=False, legacy_windows=False).print(bars(doc))
    return stream.getvalue()


@pytest.mark.parametrize("status,state,expected", [
    ("PASSED", "done", "green"), ("APPROVED", "done", "green"),
    ("FAILED", "done", "red"), ("ERROR", "done", "red"), ("SLOW", "done", "yellow"),
    ("RUNNING", "running", "blue"), ("RUNNING", "running_unknown", "blue"),
    ("RUNNING", "over_estimate", "yellow"), ("HELD", "held", "magenta"),
    ("PENDING", "queued", "grey"), ("BLOCKED", "not_run", "grey"),
    ("CARRIED_OVER", "carried_over", "grey"),
    # grey wins over the status color: this job's status alone would be red
    ("FAILED", "interrupted", "grey"),
])
def test_each_state_draws_its_own_color(status, state, expected):
    percent = None if state == "running_unknown" else 40
    text = _rendered(timing([step("j", status, state, percent, "lbl", runtime=1.0)], "RUNNING"), color=True)
    code = COLORS[expected]
    assert re.search(rf"\x1b\[{code}mlbl", text), repr(text)  # the label
    assert re.search(rf"\x1b\[{code}m[-━╸╺]", text), repr(text)  # the bar's filled part
    # Grey is also the unfilled part of any bar, so it can't count as a wrong color.
    wrong = set(COLORS.values()) - {code, COLORS["grey"]}
    assert not (set(re.findall(r"\x1b\[(?:\d+;)*(\d+)m", text)) & wrong), repr(text)


def test_bars_show_the_run_header():
    text = _rendered(timing([step("a", "PASSED", "done", 100, "PASSED", runtime=5.0)]))
    assert "Elapsed 10m 00s - 1 of 1 jobs done" in text


def test_bars_do_not_parse_server_text_as_markup():
    assert "[/x]" in _rendered(_with_doc(jobs_done="[/x]"))
    assert "[red]job" in _rendered(_with_doc(steps=[_broken_step(job_name="[red]job")]))


def test_bars_tolerate_null_progress_like_the_job_table():
    text = _rendered(_with_doc(steps=[_broken_step(job_name="j", status="BLOCKED", progress=None)]))
    assert "j" in text and "BLOCKED" in text


@pytest.mark.parametrize("label,percent,state,runtime,expected", [
    ("FAILED", 100, "done", 182.0, "3m 02s"),
    ("over estimate", 95, "over_estimate", 400.0, "6m 40s / ~4m 00s"),
])
@pytest.mark.parametrize("length", [38, 45, 90])
def test_long_names_never_hide_the_label_or_runtime(label, percent, state, runtime, expected, length):
    long_step = step("n" * length, "RUNNING", state, percent, label, runtime=runtime, estimate=240.0)
    lines = _rendered(timing([long_step], "RUNNING"), width=80).splitlines()
    row = next(line for line in lines if line.startswith("n"))
    assert label in row and expected in row
    assert len(lines) == 2
    assert all(len(line) <= 80 for line in lines)
    if length == 90:
        assert "…" in row


def test_short_terminal_keeps_failed_and_unfinished_jobs_visible():
    from rich.console import Console

    from etl_framework.cli.progress import bars

    steps = [step(f"done{i:02d}", "PASSED", "done", 100, "PASSED", runtime=1.0) for i in range(20)]
    steps[3:3] = [step("broken", "FAILED", "done", 100, "FAILED", runtime=1.0)]
    steps += [
        step("active", "RUNNING", "running", 50, "50%", runtime=1.0),
        step("parked", "HELD", "held", 50, "held", runtime=1.0),
        step("waiting", "PENDING", "queued", 0, "queued"),
        step("late_bad", "ERROR", "done", 100, "ERROR", runtime=1.0),
    ]
    stream = io.StringIO()
    Console(file=stream, width=100, height=10).print(bars(timing(steps, "RUNNING"), max_rows=6))
    lines = stream.getvalue().splitlines()
    text = "\n".join(lines)
    for name in ("broken", "late_bad", "active", "parked", "waiting"):
        assert name in text
    assert "more" in text
    assert text.index("broken") < text.index("active") < text.index("late_bad")
    assert len(lines) <= 8


def test_rows_that_fit_are_all_shown_in_order():
    from etl_framework.cli.progress import _visible_steps

    steps = [step(f"j{i}", "PASSED", "done", 100, "PASSED") for i in range(3)]
    assert _visible_steps(steps, 3) == steps
    assert _visible_steps(steps, None) == steps


# --- LiveProgress ---------------------------------------------------------------


def _live_console(stream=None, height=25):
    from rich.console import Console

    return Console(file=stream or io.StringIO(), force_terminal=True, color_system="standard",
                   width=100, height=height, legacy_windows=False)


def _live_doc(name="job_a"):
    return timing([step(name, "RUNNING", "running", 50, "50%", runtime=2.0)], "RUNNING")


def test_live_draws_in_place_and_clears_up(monkeypatch):
    from etl_framework.cli import progress

    monkeypatch.setattr(progress, "MIN_FETCH_SECONDS", 0)
    console = _live_console()
    client = FakeClient({"/api/runs/r-1/timing": _live_doc()})
    with progress.LiveProgress(client, "r-1", console=console) as display:
        display.tick()
        display.tick()
    out = console.file.getvalue()
    assert "job_a" in out and "Elapsed 10m 00s" in out
    assert out.index("\x1b[?25l") < out.index("job_a") < out.rindex("\x1b[?25h")
    # The second tick redraws: the name reaches the terminal more than once.
    assert out.count("job_a") >= 2
    assert client.calls == ["/api/runs/r-1/timing"] * 2


def test_live_is_stopped_on_exit(monkeypatch):
    from rich.live import Live

    from etl_framework.cli import progress

    stops = []
    real = Live.stop
    monkeypatch.setattr(Live, "stop", lambda self: (stops.append(self), real(self))[1])
    client = FakeClient({"/api/runs/r-1/timing": _live_doc()})
    with progress.LiveProgress(client, "r-1", console=_live_console()) as display:
        display.tick()
    assert len(stops) == 1


def test_live_draws_from_the_wait_thread_only():
    """A refresh thread would draw outside the guarded tick."""
    from etl_framework.cli import progress

    client = FakeClient({"/api/runs/r-1/timing": _live_doc()})
    before = set(threading.enumerate())
    with progress.LiveProgress(client, "r-1", console=_live_console()) as display:
        display.tick()
        assert set(threading.enumerate()) == before
        assert "FileProxy" not in type(sys.stdout).__name__


def test_live_stop_failure_keeps_the_result_and_restores_the_cursor(monkeypatch):
    from rich.live import Live

    from etl_framework.cli import progress

    monkeypatch.setattr(Live, "stop", lambda self: (_ for _ in ()).throw(OSError(22, "gone")))
    console = _live_console()
    client = FakeClient({"/api/runs/r-1/timing": _live_doc()})

    def wait():
        with progress.LiveProgress(client, "r-1", console=console) as display:
            display.tick()
            return "gate result"

    assert wait() == "gate result"
    out = console.file.getvalue()
    assert out.rindex("\x1b[?25h") > out.index("\x1b[?25l")


def test_live_exit_survives_a_cursor_failure_too(monkeypatch):
    from rich.live import Live

    from etl_framework.cli import progress

    console = _live_console()
    monkeypatch.setattr(Live, "stop", lambda self: (_ for _ in ()).throw(OSError(22, "gone")))
    client = FakeClient({"/api/runs/r-1/timing": _live_doc()})
    display = progress.LiveProgress(client, "r-1", console=console)
    display.tick()
    monkeypatch.setattr(console, "show_cursor", lambda show=True: (_ for _ in ()).throw(OSError(22, "gone")))
    assert display.__exit__(None, None, None) is None


def test_live_exit_lets_the_wait_exceptions_through():
    from etl_framework.cli import progress

    display = progress.LiveProgress(FakeClient({"/api/runs/r-1/timing": _live_doc()}), "r-1", console=_live_console())
    with pytest.raises(KeyboardInterrupt):
        with display:
            display.tick()
            raise KeyboardInterrupt()


def test_bad_tick_closes_the_bars_before_the_note(monkeypatch, capsys):
    from etl_framework.cli import progress

    monkeypatch.setattr(progress, "MIN_FETCH_SECONDS", 0)
    console = _live_console()
    good, bad = _live_doc(), _with_doc(steps=[1])
    client = FakeClient({"/api/runs/r-1/timing": [{"__seq__": good}, {"__seq__": bad}]})
    display = progress.LiveProgress(client, "r-1", console=console)
    display.tick()
    display.tick()
    display.tick()
    assert display._live is None
    assert console.file.getvalue().rindex("\x1b[?25h") > 0
    assert len(client.calls) == 2
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_live_writes_names_the_terminal_cannot_encode(monkeypatch):
    from etl_framework.cli import progress

    class Strict(io.TextIOWrapper):
        def isatty(self):
            return True

    raw = io.BytesIO()
    strict = Strict(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", strict)
    monkeypatch.delenv("NO_COLOR", raising=False)
    client = FakeClient({"/api/runs/r-1/timing": _live_doc("日本語-→-job")})
    display = progress.LiveProgress(client, "r-1")
    display.tick()
    display.__exit__(None, None, None)
    strict.flush()
    assert display._enabled is True
    assert b"-job" in raw.getvalue()


# --- Real processes ------------------------------------------------------------


class _Server:
    """A real API for a real `atom run`: RUNNING until `polls_to_finish`, then `final`."""

    def __init__(self, timing_body, final="PASSED", polls_to_finish=2, finish_at=None, timing_delay=0.0):
        self.status_polls = 0
        self.timing_requests = 0
        self.launched_at = None
        self.last_request_at = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _reply(self, body, code=200):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.launched_at = outer.last_request_at = time.monotonic()
                self._reply({"run_id": "r-1"})

            def do_GET(self):
                if self.path.endswith("/status"):
                    outer.last_request_at = time.monotonic()
                    outer.status_polls += 1
                    if finish_at is not None:
                        done = time.monotonic() - outer.launched_at >= finish_at
                    else:
                        done = outer.status_polls >= polls_to_finish
                    status = final if done else "RUNNING"
                    self._reply({"run_id": "r-1", "status": status, "passed": 1, "failed": 0, "error": 0})
                elif self.path.endswith("/timing"):
                    outer.timing_requests += 1
                    time.sleep(timing_delay)
                    self._reply(timing_body)
                elif self.path.endswith("/junit"):
                    self._reply(b"<testsuite/>")
                else:
                    self._reply({"detail": "nope"}, 404)

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"

    def __enter__(self):
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc_info):
        self._httpd.shutdown()
        self._httpd.server_close()


def _atom_env():
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    for name in ("CI", "PYTHONUTF8", "NO_COLOR", "FORCE_COLOR"):
        env.pop(name, None)
    return env


def _atom_command(url, poll="0.05", timeout="30"):
    return [sys.executable, "-m", "etl_framework.cli.app", "--api-url", url, "--token", "t",
            "run", "3", "--source-env", "dev", "--poll-interval", poll, "--timeout", timeout]


UNENCODABLE = "日本語-→-job"


def test_unencodable_job_names_do_not_fail_the_gate_on_a_cp1252_pipe():
    doc = timing([step(UNENCODABLE, "PASSED", "done", 100, "PASSED", runtime=5.0)])
    with _Server(doc, polls_to_finish=3) as server:
        proc = subprocess.run(_atom_command(server.url), capture_output=True, env=_atom_env(),
                              timeout=60, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    out = proc.stdout.decode("cp1252")
    assert "-job" in out and "run=r-1" in out and "Elapsed: 10m 00s" in out
    assert b"Traceback" not in proc.stderr
    assert server.timing_requests >= 2


def test_unencodable_job_names_do_not_fail_a_red_gate_either():
    doc = timing([step(UNENCODABLE, "FAILED", "done", 100, "FAILED", runtime=5.0)])
    with _Server(doc, final="ERROR", polls_to_finish=3) as server:
        proc = subprocess.run(_atom_command(server.url), capture_output=True, env=_atom_env(),
                              timeout=60, cwd=REPO_ROOT)
    assert proc.returncode == 3, proc.stderr.decode(errors="replace")
    assert b"exit=3" in proc.stdout


def test_slow_timing_keeps_the_timeout_and_its_exit_code():
    """Run fails at t=3; --timeout 1 still wins, as it does with no display at all."""
    with _Server(timing(), final="FAILED", finish_at=3.0, timing_delay=5.0) as server:
        proc = subprocess.run(_atom_command(server.url, poll="0.1", timeout="1"), capture_output=True,
                              env=_atom_env(), timeout=60, cwd=REPO_ROOT)
    assert proc.returncode == 6, proc.stderr.decode(errors="replace")
    assert server.last_request_at - server.launched_at < 2.5


def test_closed_stdout_does_not_end_the_wait_early(tmp_path):
    junit = tmp_path / "junit.xml"
    doc = timing([step("a", "RUNNING", "running", 5, "5%")], "RUNNING")
    with _Server(doc, polls_to_finish=13) as server:
        command = _atom_command(server.url) + ["--junit-out", str(junit)]
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=_atom_env(), cwd=REPO_ROOT)
        proc.stdout.close()  # The reader goes away before the first line is written.
        try:
            stderr = proc.stderr.read()
            proc.wait(timeout=60)
        finally:
            proc.kill()
            proc.stderr.close()
    assert server.status_polls == 13, stderr.decode(errors="replace")
    assert junit.read_bytes() == b"<testsuite/>"
    assert proc.returncode in (0, 120), stderr.decode(errors="replace")
