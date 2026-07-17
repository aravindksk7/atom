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
