"""Tests for the atom CLI (etl_framework.cli.app)."""
from __future__ import annotations

from typer.testing import CliRunner

runner = CliRunner()


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
