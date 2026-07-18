"""Tests for etl_framework.cli.render."""
from __future__ import annotations

from etl_framework.cli import render


def test_selections_table_shows_id_name_jobs():
    text = render.selections_table([
        {"id": 3, "name": "Nightly Regression", "job_count": 12,
         "updated_at": "2026-07-17T22:00:00+00:00", "archived": False},
    ])
    assert "3" in text
    assert "Nightly Regression" in text
    assert "12" in text


def test_runs_table_shows_run_id_status_counts():
    text = render.runs_table([
        {"run_id": "r-abc", "status": "FAILED", "passed": 10, "failed": 2,
         "error": 0, "started_at": "2026-07-18T09:00:00+00:00"},
    ])
    assert "r-abc" in text
    assert "FAILED" in text
    assert "2" in text


def test_run_summary_line_contains_verdict_and_counts():
    line = render.run_summary(
        {"run_id": "r-abc", "status": "PASSED", "passed": 12, "failed": 0, "error": 0},
        exit_code=0,
    )
    assert "PASSED" in line
    assert "run=r-abc" in line
    assert "exit=0" in line
