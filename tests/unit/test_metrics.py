import json
import os
import tempfile
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from etl_framework.reporting.metrics import MetricsWriter
from etl_framework.runner.state import TestStatus, TestCaseState
from etl_framework.reconciliation.models import ReconciliationResult


def _make_recon_result(name, status=TestStatus.PASSED, duration=1.0,
                       src_rows=100, tgt_rows=100, mismatches=0):
    from datetime import datetime, timezone
    return ReconciliationResult(
        query_name=name,
        source_env="dev",
        target_env="prod",
        source_row_count=src_rows,
        target_row_count=tgt_rows,
        matched_count=src_rows - mismatches,
        missing_in_target_count=0,
        missing_in_source_count=0,
        value_mismatch_count=mismatches,
        mismatches=[],
        status=status,
        executed_at=datetime.now(timezone.utc),
        duration_seconds=duration,
    )


def test_metrics_writer_creates_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "metrics.json")
        writer = MetricsWriter(output_path=path)
        writer.write(run_id="run-001", results=[])
        assert os.path.exists(path)


def test_metrics_json_is_valid():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "metrics.json")
        writer = MetricsWriter(output_path=path)
        writer.write(run_id="run-001", results=[])
        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)


def test_metrics_contains_run_id():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "metrics.json")
        writer = MetricsWriter(output_path=path)
        writer.write(run_id="run-abc", results=[])
        data = json.load(open(path))
        assert data["run_id"] == "run-abc"


def test_metrics_contains_totals():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "metrics.json")
        results = [
            _make_recon_result("q1", TestStatus.PASSED, duration=1.0),
            _make_recon_result("q2", TestStatus.FAILED, duration=2.0, mismatches=3),
            _make_recon_result("q3", TestStatus.SLOW, duration=5.0),
        ]
        writer = MetricsWriter(output_path=path)
        writer.write(run_id="run-xyz", results=results)
        data = json.load(open(path))
        assert data["total_tests"] == 3
        assert data["passed"] == 1
        assert data["failed"] == 1
        assert data["slow"] == 1


def test_metrics_contains_per_test_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "metrics.json")
        results = [_make_recon_result("orders", TestStatus.PASSED, duration=0.5, src_rows=50)]
        writer = MetricsWriter(output_path=path)
        writer.write(run_id="r1", results=results)
        data = json.load(open(path))
        tests = data["tests"]
        assert len(tests) == 1
        assert tests[0]["name"] == "orders"
        assert tests[0]["status"] == "PASSED"
        assert tests[0]["duration_seconds"] == pytest.approx(0.5)
        assert tests[0]["source_row_count"] == 50


def test_metrics_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "nested", "deep", "metrics.json")
        writer = MetricsWriter(output_path=path)
        writer.write(run_id="r1", results=[])
        assert os.path.exists(path)


def test_metrics_total_duration_is_sum():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "metrics.json")
        results = [
            _make_recon_result("a", duration=1.5),
            _make_recon_result("b", duration=2.5),
        ]
        writer = MetricsWriter(output_path=path)
        writer.write(run_id="r1", results=results)
        data = json.load(open(path))
        assert data["total_duration_seconds"] == pytest.approx(4.0)
