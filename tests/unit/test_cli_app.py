"""Tests for the atom CLI (etl_framework.cli.app)."""
from __future__ import annotations

import re

from typer.testing import CliRunner

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _plain(output: str) -> str:
    """Strip ANSI SGR codes from CLI output.

    Typer's error/usage rendering goes through rich, which colors option
    flags token-by-token -- e.g. "--out" can render as a "-" span followed by
    a separately-colored "-out" span, so a literal substring check across
    styled output can fail even though the plain text is present.
    """
    return _ANSI_RE.sub("", output)


def test_help_lists_commands():
    from etl_framework.cli.app import app

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "report", "selections", "runs"):
        assert command in result.output


def test_missing_api_url_fails():
    from etl_framework.cli.app import app

    result = runner.invoke(app, ["selections"], env={"ATOM_API_URL": ""})
    assert result.exit_code != 0


import json
from unittest.mock import MagicMock, patch

import pytest

from etl_framework.cli.client import (
    AtomAPIError,
    AtomAuthError,
    AtomConnectionError,
    AtomNotFoundError,
)


class FakeClient:
    """Routes (method, path) to canned responses; records calls."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def _lookup(self, method, path):
        self.calls.append((method, path))
        try:
            result = self.responses[(method, path)]
        except KeyError:
            raise AtomNotFoundError(f"no fake response for {method} {path}")
        if isinstance(result, Exception):
            raise result
        if isinstance(result, list):  # sequence of responses for repeated polling
            result = result.pop(0) if len(result) > 1 else result[0]
        return result

    def get_json(self, path, **kwargs):
        return self._lookup("GET", path)

    def post_json(self, path, payload):
        self.calls.append(("PAYLOAD", payload))
        return self._lookup("POST", path)

    def get_bytes(self, path):
        return self._lookup("GET-BYTES", path)


@pytest.fixture
def fake_client(monkeypatch):
    def install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr("etl_framework.cli.app._make_client",
                            lambda api_url, token: client)
        return client

    return install


BASE_ARGS = ["--api-url", "http://atom.test", "--token", "t0k3n"]


def test_selections_lists_names(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/selections"): [
        [{"id": 3, "name": "Nightly Regression", "job_count": 12,
          "archived": False, "updated_at": "2026-07-17T22:00:00+00:00"}],
    ]})
    result = runner.invoke(app, BASE_ARGS + ["selections"])
    assert result.exit_code == 0
    assert "Nightly Regression" in result.output


def test_selections_json_output(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/selections"): [
        [{"id": 3, "name": "Nightly Regression", "job_count": 12,
          "archived": False, "updated_at": "2026-07-17T22:00:00+00:00"}],
    ]})
    result = runner.invoke(app, BASE_ARGS + ["--output", "json", "selections"])
    assert result.exit_code == 0
    assert json.loads(result.output)[0]["id"] == 3


def test_runs_respects_limit(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/runs"): [
        [{"run_id": f"r-{i}", "status": "PASSED", "passed": 1, "failed": 0,
          "error": 0, "started_at": None} for i in range(30)],
    ]})
    result = runner.invoke(app, BASE_ARGS + ["runs", "--limit", "5"])
    assert result.exit_code == 0
    assert "r-4" in result.output
    assert "r-5" not in result.output


def test_selections_connection_error_exits_5(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/selections"): AtomConnectionError("refused")})
    result = runner.invoke(app, BASE_ARGS + ["selections"])
    assert result.exit_code == 5


def test_report_junit_writes_file(tmp_path, fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET-BYTES", "/api/runs/r-1/junit"): b"<testsuites/>"})
    out = tmp_path / "junit.xml"
    result = runner.invoke(
        app, BASE_ARGS + ["report", "r-1", "--format", "junit", "--out", str(out)]
    )
    assert result.exit_code == 0
    assert out.read_bytes() == b"<testsuites/>"


def test_report_json_defaults_to_stdout(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/runs/r-1"): {"run_id": "r-1", "status": "PASSED"}})
    result = runner.invoke(app, BASE_ARGS + ["report", "r-1"])
    assert result.exit_code == 0
    assert json.loads(result.output)["run_id"] == "r-1"


def test_report_html_requires_out(fake_client):
    from etl_framework.cli.app import app

    fake_client({})
    result = runner.invoke(app, BASE_ARGS + ["report", "r-1", "--format", "html"])
    assert result.exit_code != 0
    assert "--out" in _plain(result.output)


def test_report_unknown_run_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/runs/nope"): AtomNotFoundError("Run not found")})
    result = runner.invoke(app, BASE_ARGS + ["report", "nope"])
    assert result.exit_code == 4


RUN_ARGS = ["run", "3", "--source-env", "dev", "--target-env", "qa",
            "--poll-interval", "0"]


def _launch_responses(final_status, extra=None):
    responses = {
        ("POST", "/api/selections/3/launch"): {"run_id": "r-9", "status": "PENDING"},
        ("GET", "/api/runs/r-9/status"): [
            {"run_id": "r-9", "status": "RUNNING", "passed": 0, "failed": 0, "error": 0},
            final_status,
        ],
    }
    responses.update(extra or {})
    return responses


def test_run_passed_exits_0(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 5, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0
    assert "PASSED" in result.output


def test_run_failed_exits_1(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "FAILED", "passed": 4, "failed": 1, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 1


def test_run_cancelled_exits_2(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "CANCELLED", "passed": 0, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 2


def test_run_error_exits_3(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "COMPLETED", "passed": 4, "failed": 0, "error": 1}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 3


def test_run_no_wait_prints_run_id_and_exits_0(fake_client):
    from etl_framework.cli.app import app

    client = fake_client({
        ("POST", "/api/selections/3/launch"): {"run_id": "r-9", "status": "PENDING"},
    })
    result = runner.invoke(app, BASE_ARGS + ["run", "3", "--source-env", "dev",
                                             "--no-wait"])
    assert result.exit_code == 0
    assert "r-9" in result.output
    assert ("GET", "/api/runs/r-9/status") not in client.calls


def test_run_resolves_selection_by_name(fake_client):
    from etl_framework.cli.app import app

    responses = _launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0})
    responses[("GET", "/api/selections")] = [
        [{"id": 3, "name": "Nightly Regression", "job_count": 1,
          "archived": False, "updated_at": None}],
    ]
    fake_client(responses)
    result = runner.invoke(app, BASE_ARGS + ["run", "Nightly Regression",
                                             "--source-env", "dev",
                                             "--poll-interval", "0"])
    assert result.exit_code == 0


def test_run_unknown_selection_name_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/selections"): [[]]})
    result = runner.invoke(app, BASE_ARGS + ["run", "Ghost", "--source-env", "dev"])
    assert result.exit_code == 4


def test_run_passes_ci_context_to_launch(fake_client):
    from etl_framework.cli.app import app

    client = fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS + [
        "--ci-commit-sha", "a1b2c3", "--ci-pipeline-url", "https://gl/p/1",
        "--ci-ref", "main"])
    assert result.exit_code == 0
    payload = next(c[1] for c in client.calls if c[0] == "PAYLOAD")
    assert payload["ci_context"] == {
        "commit_sha": "a1b2c3", "pipeline_url": "https://gl/p/1", "ref": "main"}


def test_run_writes_junit_artifact(tmp_path, fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0},
        extra={("GET-BYTES", "/api/runs/r-9/junit"): b"<testsuites/>"}))
    out = tmp_path / "junit.xml"
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS + ["--junit-out", str(out)])
    assert result.exit_code == 0
    assert out.read_bytes() == b"<testsuites/>"


def test_run_writes_json_and_html_artifacts(tmp_path, fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0},
        extra={
            ("GET", "/api/runs/r-9"): {"run_id": "r-9", "status": "PASSED"},
            ("GET-BYTES", "/api/runs/r-9/report"): b"<html>ok</html>",
        }))
    json_out = tmp_path / "run.json"
    html_out = tmp_path / "run.html"
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS + [
        "--json-out", str(json_out), "--html-out", str(html_out)])
    assert result.exit_code == 0
    assert json.loads(json_out.read_text(encoding="utf-8"))["run_id"] == "r-9"
    assert html_out.read_bytes() == b"<html>ok</html>"


def test_run_requires_source_env(fake_client):
    from etl_framework.cli.app import app

    fake_client({})
    result = runner.invoke(app, BASE_ARGS + ["run", "3"])
    assert result.exit_code != 0
    assert "source-env" in _plain(result.output)


def test_run_timeout_exits_6_and_prints_run_id(fake_client):
    from etl_framework.cli.app import app

    fake_client({
        ("POST", "/api/selections/3/launch"): {"run_id": "r-9", "status": "PENDING"},
        ("GET", "/api/runs/r-9/status"): [
            {"run_id": "r-9", "status": "RUNNING", "passed": 0, "failed": 0, "error": 0},
        ],
    })
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS + ["--timeout", "0"])
    assert result.exit_code == 6
    assert "r-9" in result.output


def test_run_json_output_emits_machine_readable_verdict(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "FAILED", "passed": 4, "failed": 1, "error": 0}))
    result = runner.invoke(app, BASE_ARGS[:4] + ["--output", "json"] + RUN_ARGS)
    assert result.exit_code == 1
    verdict = json.loads(result.output)
    assert verdict["run_id"] == "r-9"
    assert verdict["exit_code"] == 1


SEQUENCE_RUN_ARGS = ["run", "7", "--target-type", "sequence",
                     "--source-env", "dev", "--target-env", "qa",
                     "--poll-interval", "0"]


def _sequence_launch_responses(final_status):
    return {
        ("POST", "/api/sequences/7/launch"): {"run_id": "r-9", "status": "PENDING"},
        ("GET", "/api/runs/r-9/status"): [
            {"run_id": "r-9", "status": "RUNNING", "passed": 0, "failed": 0, "error": 0},
            final_status,
        ],
    }


def test_run_target_type_sequence_calls_sequence_endpoint(fake_client):
    from etl_framework.cli.app import app

    # _sequence_launch_responses only registers a fake response for
    # POST /api/sequences/7/launch. If the CLI called the selections endpoint
    # instead, FakeClient would raise AtomNotFoundError and exit_code would be
    # 4, not 0 -- so reaching 0 already proves the sequence path was hit.
    fake_client(_sequence_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + SEQUENCE_RUN_ARGS)
    assert result.exit_code == 0


def test_run_default_target_type_is_selection(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0


def test_run_resolves_sequence_by_name(fake_client):
    from etl_framework.cli.app import app

    responses = _sequence_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0})
    responses[("GET", "/api/sequences")] = [
        [{"id": 7, "name": "Nightly DAG", "step_count": 2,
          "archived": False, "latest_version": 1}],
    ]
    fake_client(responses)
    result = runner.invoke(app, BASE_ARGS + ["run", "Nightly DAG", "--target-type", "sequence",
                                             "--source-env", "dev", "--poll-interval", "0"])
    assert result.exit_code == 0


def test_run_unknown_sequence_name_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/sequences"): [[]]})
    result = runner.invoke(app, BASE_ARGS + ["run", "Ghost", "--target-type", "sequence",
                                             "--source-env", "dev"])
    assert result.exit_code == 4


def test_run_multiple_sequence_matches_exits_3(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/sequences"): [
        [{"id": 7, "name": "dup", "step_count": 1, "archived": False, "latest_version": 1},
         {"id": 8, "name": "dup", "step_count": 1, "archived": False, "latest_version": 1}],
    ]})
    result = runner.invoke(app, BASE_ARGS + ["run", "dup", "--target-type", "sequence",
                                             "--source-env", "dev"])
    assert result.exit_code == 3


def test_run_invalid_target_type_fails(fake_client):
    from etl_framework.cli.app import app

    fake_client({})
    result = runner.invoke(app, BASE_ARGS + ["run", "7", "--target-type", "bogus",
                                             "--source-env", "dev"])
    assert result.exit_code != 0


def test_run_forwards_var_overrides_in_launch_payload():
    from etl_framework.cli.app import app

    with patch("etl_framework.cli.app._make_client") as make_client:
        client = MagicMock()
        client.get_json.return_value = [{"id": 1, "name": "my-selection"}]
        client.post_json.return_value = {"run_id": "abc123"}
        client.get_json.side_effect = [
            [{"id": 1, "name": "my-selection"}],  # _resolve_target lookup
            {"status": "PASSED", "passed": 1, "failed": 0, "error": 0},  # _wait_for_run
        ]
        make_client.return_value = client

        result = runner.invoke(app, [
            "--api-url", "http://atom.test", "run", "my-selection",
            "--source-env", "dev",
            "--var", "run_date=2026-09-08",
            "--var", "batch_id=B1",
            "--no-wait",
        ])

    assert result.exit_code == 0
    payload = client.post_json.call_args[0][1]
    assert payload["variable_overrides"] == {"run_date": "2026-09-08", "batch_id": "B1"}


def test_run_rejects_malformed_var():
    from etl_framework.cli.app import app

    with patch("etl_framework.cli.app._make_client") as make_client:
        client = MagicMock()
        make_client.return_value = client

        # Numeric selection id so _resolve_target's isdigit() branch short-circuits
        # and never calls client.get_json -- this guarantees the command actually
        # reaches the --var parsing code instead of failing earlier for an
        # unrelated reason (an unconfigured MagicMock iterated as an empty match
        # list would otherwise raise AtomNotFoundError first and mask this test).
        result = runner.invoke(app, [
            "--api-url", "http://atom.test", "run", "3",
            "--source-env", "dev", "--var", "no-equals-sign", "--no-wait",
        ])
    assert client.get_json.call_count == 0
    assert result.exit_code == 2
