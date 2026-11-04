"""atom CLI: timing tables, new commands and live progress."""
from __future__ import annotations

import json
import subprocess
import sys
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

    def get_json(self, path, **kwargs):
        self.calls.append(path)
        result = self.responses.get(path, AtomNotFoundError(f"no fake for {path}"))
        if isinstance(result, Exception):
            raise result
        return result

    def post_json(self, path, payload):
        self.calls.append(path)
        return self.responses[path]

    def get_bytes(self, path):
        return b""


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
