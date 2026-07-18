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
