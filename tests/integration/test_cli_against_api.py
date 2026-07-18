"""End-to-end: installed atom CLI against a live Atom API.

Skipped unless ATOM_IT_API_URL is set. Bring the stack up first:

    docker compose -f docker-compose.integration.yml up -d
    ATOM_IT_API_URL=http://localhost:8000 ATOM_IT_TOKEN=<token> \
        python -m pytest tests/integration/test_cli_against_api.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

API_URL = os.environ.get("ATOM_IT_API_URL")
TOKEN = os.environ.get("ATOM_IT_TOKEN", "")
SELECTION = os.environ.get("ATOM_IT_SELECTION")

pytestmark = pytest.mark.skipif(
    not API_URL, reason="ATOM_IT_API_URL not set; integration lane disabled"
)


def _atom(*args: str) -> subprocess.CompletedProcess:
    command = [shutil.which("atom") or "atom", "--api-url", API_URL]
    if TOKEN:
        command.extend(["--token", TOKEN])
    command.extend(args)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_selections_lists_without_error():
    proc = _atom("selections")
    assert proc.returncode == 0, proc.stderr


def test_runs_lists_without_error():
    proc = _atom("--output", "json", "runs", "--limit", "5")
    assert proc.returncode == 0, proc.stderr


def test_report_unknown_run_exits_4():
    proc = _atom("report", "run-id-that-does-not-exist")
    assert proc.returncode == 4, proc.stderr


@pytest.mark.skipif(
    not SELECTION,
    reason="ATOM_IT_SELECTION not set; live launch smoke disabled",
)
def test_run_and_report_junit_happy_path(tmp_path: Path):
    junit_out = tmp_path / "atom-junit.xml"
    run_proc = _atom(
        "run", SELECTION,
        "--source-env", os.environ.get("ATOM_IT_SOURCE_ENV", "dev"),
        "--target-env", os.environ.get("ATOM_IT_TARGET_ENV", "qa"),
        "--poll-interval", os.environ.get("ATOM_IT_POLL_INTERVAL", "1"),
        "--timeout", os.environ.get("ATOM_IT_TIMEOUT", "120"),
        "--junit-out", str(junit_out),
    )
    assert run_proc.returncode == 0, run_proc.stderr
    assert junit_out.exists()
    assert "<testsuite" in junit_out.read_text(encoding="utf-8")

    run_id = None
    for token in run_proc.stdout.split():
        if token.startswith("run="):
            run_id = token.split("=", 1)[1]
            break
    assert run_id, run_proc.stdout

    report_out = tmp_path / "report-junit.xml"
    report_proc = _atom("report", run_id, "--format", "junit", "--out", str(report_out))
    assert report_proc.returncode == 0, report_proc.stderr
    assert "<testsuite" in report_out.read_text(encoding="utf-8")
