from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from api.services.run_report import build_run_report_snapshot


def _run(**overrides):
    base = dict(
        run_id="run-1",
        status="PASSED",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        source_env="Source A",
        target_env="Production Report",
        config_snapshot=None,
        run_type="recon_file",
        pair_id=None,
        total_tests=0,
        passed=0,
        failed=0,
        slow=0,
        error=0,
        results=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_recon_file_upload_populates_file_names_from_config_snapshot():
    run = _run(config_snapshot={
        "compare_request_type": "recon_file",
        "request": {"file_a_name": "march_invoices.xml", "file_b_name": "march_invoices_prod.xml"},
    })
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a == "march_invoices.xml"
    assert snapshot.file_name_b == "march_invoices_prod.xml"


def test_recon_file_path_falls_back_to_basename():
    run = _run(config_snapshot={
        "compare_request_type": "recon_file",
        "request": {"file_a_path": r"c:\uploads\run-1\source.csv", "file_b_path": r"c:\uploads\run-1\target.csv"},
    })
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a == "source.csv"
    assert snapshot.file_name_b == "target.csv"


def test_bo_report_reads_file_names_from_source_configs():
    run = _run(config_snapshot={
        "compare_request_type": "bo_report",
        "request": {
            "source_a": {"file_name": "sales_a.xlsx"},
            "source_b": {"file_path": r"c:\uploads\run-1\sales_b.xlsx"},
        },
    })
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a == "sales_a.xlsx"
    assert snapshot.file_name_b == "sales_b.xlsx"


def test_no_file_names_when_config_snapshot_has_none():
    run = _run(config_snapshot={"compare_request_type": "sql", "request": {"query_a": "SELECT 1"}})
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a is None
    assert snapshot.file_name_b is None


def test_missing_config_snapshot_yields_no_file_names():
    run = _run(config_snapshot=None)
    snapshot = build_run_report_snapshot(run)
    assert snapshot.file_name_a is None
    assert snapshot.file_name_b is None


def test_snapshot_preserves_multi_file_pair_breakdown():
    result = SimpleNamespace(
        id=1,
        query_name="regional_sales_recon",
        status="FAILED",
        effective_status="FAILED",
        duration_seconds=0.5,
        source_row_count=2,
        target_row_count=2,
        value_mismatch_count=1,
        missing_in_target_count=0,
        missing_in_source_count=0,
        error_message=None,
        executed_at=None,
        source_file_name="2 file(s) across 2 pair(s)",
        target_file_name="2 file(s) across 2 pair(s)",
        override_reason=None,
        override_by=None,
        override_at=None,
        sample_rows=None,
        segment_summary=None,
        mismatch_summary={
            "file_pairs": [
                {"key": {"region": "east"}, "status": "PASSED", "source_files": ["sales_east.csv"], "target_files": ["financials_east.csv"]},
                {"key": {"region": "west"}, "status": "FAILED", "source_files": ["sales_west.csv"], "target_files": ["financials_west.csv"]},
            ],
            "pairs_total": 2,
            "pairs_passed": 1,
            "pairs_failed": 1,
            "pairs_errored": 0,
        },
        mismatches=[],
        schema_diff=None,
        total_issues=1,
    )
    snapshot = build_run_report_snapshot(_run(results=[result]))

    assert snapshot.results[0].file_pairs[1]["key"] == {"region": "west"}
    assert snapshot.results[0].file_pairs[1]["status"] == "FAILED"
