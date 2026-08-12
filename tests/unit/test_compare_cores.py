"""Pure compare cores: return a result, touch no run bookkeeping."""
from __future__ import annotations

import base64

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ConfigRepository, RunRepository


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_compare_bo_returns_a_result_and_writes_no_run():
    from api.schemas import BOCompareRequest, SourceConfig
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    req = BOCompareRequest(
        source_a=SourceConfig(
            source_type="upload", file_content_b64=_b64("id,value\n1,alpha\n"), file_name="a.csv",
        ),
        source_b=SourceConfig(
            source_type="upload", file_content_b64=_b64("id,value\n1,beta\n"), file_name="b.csv",
        ),
        key_columns=["id"],
    )

    result = svc.compare_bo(req, None)

    assert result.value_mismatch_count == 1
    assert RunRepository(db).list_runs() == []


def test_compare_bo_falls_back_to_positional_keys_when_no_shared_id_column():
    from api.schemas import BOCompareRequest, SourceConfig
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    req = BOCompareRequest(
        source_a=SourceConfig(
            source_type="upload", file_content_b64=_b64("value\nalpha\n"), file_name="a.csv",
        ),
        source_b=SourceConfig(
            source_type="upload", file_content_b64=_b64("value\nalpha\n"), file_name="b.csv",
        ),
    )

    result = svc.compare_bo(req, None)

    assert result.value_mismatch_count == 0


def test_compare_report_stats_returns_one_result_per_test_name():
    from api.services.compare_service import _compare_report_stats

    stats_a = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
        "customers": {"status": "PASSED", "source_row_count": 5, "target_row_count": 5, "total_issues": 0},
    }
    stats_b = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
        "customers": {"status": "FAILED", "source_row_count": 5, "target_row_count": 4, "total_issues": 1},
    }

    pairs = _compare_report_stats(stats_a, stats_b, "Run A", "Report B")

    assert [result.query_name for result, _ in pairs] == ["customers", "orders"]
    by_name = {result.query_name: (result, records) for result, records in pairs}
    assert by_name["orders"][0].status.value == "PASSED"
    assert by_name["orders"][1] == []
    assert by_name["customers"][0].status.value == "FAILED"
    assert {r.column_name for r in by_name["customers"][1]} == {
        "status", "target_row_count", "total_issues",
    }


def test_compare_report_stats_marks_a_test_present_on_only_one_side_as_failed():
    from api.services.compare_service import _compare_report_stats

    pairs = _compare_report_stats(
        {"only_a": {"status": "PASSED", "source_row_count": 1, "target_row_count": 1, "total_issues": 0}},
        {},
        "A",
        "B",
    )

    assert [result.status.value for result, _ in pairs] == ["FAILED"]


def test_aggregate_stat_results_folds_per_test_results_into_one():
    from api.services.compare_service import _compare_report_stats, aggregate_stat_results

    stats_a = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
        "customers": {"status": "PASSED", "source_row_count": 5, "target_row_count": 5, "total_issues": 0},
    }
    stats_b = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
        "customers": {"status": "FAILED", "source_row_count": 5, "target_row_count": 4, "total_issues": 1},
    }
    results = [result for result, _ in _compare_report_stats(stats_a, stats_b, "A", "B")]

    aggregate = aggregate_stat_results("nightly_report_diff", results, "A", "B")

    assert aggregate.query_name == "nightly_report_diff"
    assert aggregate.status.value == "FAILED"
    assert aggregate.matched_count == 1
    tests = aggregate.mismatch_summary["report_tests"]
    assert [t["test_name"] for t in tests] == ["customers", "orders"]
    assert tests[0]["differing_metrics"] == ["status", "target_row_count", "total_issues"]


def test_aggregate_stat_results_passes_when_every_test_matched():
    from api.services.compare_service import _compare_report_stats, aggregate_stat_results

    stats = {"orders": {"status": "PASSED", "source_row_count": 1, "target_row_count": 1, "total_issues": 0}}
    results = [result for result, _ in _compare_report_stats(stats, stats, "A", "B")]

    aggregate = aggregate_stat_results("job", results, "A", "B")

    assert aggregate.status.value == "PASSED"
    assert aggregate.value_mismatch_count == 0


def test_aggregate_stat_results_fails_when_neither_side_had_any_tests():
    from api.services.compare_service import aggregate_stat_results

    aggregate = aggregate_stat_results("job", [], "A", "B")

    assert aggregate.status.value == "FAILED"
    assert aggregate.mismatch_summary["report_tests"] == []


def test_tabular_file_result_returns_a_result_and_writes_no_run():
    import pandas as pd
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    req = ReconFileCompareRequest(
        file_a_path="/allowed/a.csv",
        file_b_path="/allowed/b.csv",
        key_columns=["id"],
    )
    df_a = pd.DataFrame({"id": [1, 2], "value": ["alpha", "beta"]})
    df_b = pd.DataFrame({"id": [1, 2], "value": ["alpha", "GAMMA"]})

    result = svc._tabular_file_result(req, df_a, df_b)

    assert result.value_mismatch_count == 1
    assert RunRepository(db).list_runs() == []


def test_compare_recon_file_returns_one_result_for_tabular_sources(tmp_path, monkeypatch):
    from api.services import file_source
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,beta\n", encoding="utf-8")

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    req = ReconFileCompareRequest(
        file_a_path=str(tmp_path / "a.csv"),
        file_b_path=str(tmp_path / "b.csv"),
        key_columns=["id"],
    )

    result = svc.compare_recon_file(req, job_name="nightly_file_diff")

    assert result.value_mismatch_count == 1
    assert RunRepository(db).list_runs() == []


def test_compare_recon_file_aggregates_report_sources_into_one_result(monkeypatch):
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    stats_a = {
        "orders": {"status": "PASSED", "source_row_count": 10, "target_row_count": 10, "total_issues": 0},
    }
    stats_b = {
        "orders": {"status": "FAILED", "source_row_count": 10, "target_row_count": 9, "total_issues": 1},
    }
    monkeypatch.setattr(
        CompareService, "_load_recon_source",
        lambda self, req, side: stats_a if side == "a" else stats_b,
    )
    req = ReconFileCompareRequest(file_a_path="/x/a.html", file_b_path="/x/b.html")

    result = svc.compare_recon_file(req, job_name="nightly_report_diff")

    assert result.query_name == "nightly_report_diff"
    assert result.status.value == "FAILED"
    assert [t["test_name"] for t in result.mismatch_summary["report_tests"]] == ["orders"]


def test_recon_html_path_uses_allowed_path_resolver(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from api.services import file_source
    from api.services.compare_service import CompareService

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    outside = tmp_path.parent / "outside-report.html"
    outside.write_text("<table><tr><td>orders</td><td>PASSED</td></tr></table>", encoding="utf-8")

    try:
        with pytest.raises(HTTPException) as exc_info:
            CompareService._load_recon_html(str(outside), None)

        assert exc_info.value.status_code == 400
    finally:
        outside.unlink(missing_ok=True)


def test_compare_recon_file_rejects_mixed_source_kinds(monkeypatch):
    import pandas as pd
    import pytest
    from fastapi import HTTPException
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    db = _session()
    svc = CompareService(db, ConfigRepository(db))
    monkeypatch.setattr(
        CompareService, "_load_recon_source",
        lambda self, req, side: pd.DataFrame({"id": [1]}) if side == "a" else {"orders": {}},
    )
    req = ReconFileCompareRequest(file_a_path="/x/a.csv", file_b_path="/x/b.html")

    with pytest.raises(HTTPException) as exc_info:
        svc.compare_recon_file(req, job_name="job")

    assert exc_info.value.status_code == 422
