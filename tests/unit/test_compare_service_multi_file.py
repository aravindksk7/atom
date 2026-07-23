# tests/unit/test_compare_service_multi_file.py
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import MultiFileCompareRequest
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository, RunRepository
from etl_framework.runner.state import TestStatus


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_run_multi_file_compare_persists_aggregate_result(tmp_path, monkeypatch) -> None:
    from api.services import file_source
    from api.services.compare_service import CompareService

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (source_dir / "sales_west.csv").write_text("id,value\n2,beta\n", encoding="utf-8")
    (target_dir / "financials_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (target_dir / "financials_west.csv").write_text("id,value\n2,BETA\n", encoding="utf-8")

    db = _make_db()
    try:
        run_id = "test-run-mf-compare"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="multi_file")

        req = MultiFileCompareRequest(
            key_columns=["id"],
            file_mapping={
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": str(target_dir), "pattern": "financials_{region}.csv"},
            },
        )
        svc = CompareService(db, ConfigRepository(db))
        svc.run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "FAILED"  # region=west mismatches
        assert len(run.results) == 1
        result = run.results[0]
        assert result.mismatch_summary["pairs_total"] == 2
        assert result.mismatch_summary["pairs_passed"] == 1
        by_region = {p["key"]["region"]: p for p in result.mismatch_summary["file_pairs"]}
        assert by_region["east"]["status"] == "PASSED"
        assert by_region["west"]["status"] == "FAILED"
    finally:
        db.close()


def test_run_multi_file_compare_ignore_policy_proceeds_with_unmatched(tmp_path, monkeypatch) -> None:
    """Regression test: an earlier draft of run_multi_file_compare had
    `if mapping.unmatched_sources or mapping.unmatched_targets and spec.unmatched_policy == "fail":`
    -- Python's `and` binds tighter than `or`, so that raised on ANY unmatched
    source regardless of policy, meaning `unmatched_policy: "ignore"` was
    silently never honored whenever a source was unmatched. This test only
    passes if that condition is correctly parenthesized as two separate checks.
    """
    from api.services import file_source
    from api.services.compare_service import CompareService

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (source_dir / "sales_north.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")  # no target match
    (target_dir / "financials_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")

    db = _make_db()
    try:
        run_id = "test-run-mf-compare-ignore"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="multi_file")

        req = MultiFileCompareRequest(
            key_columns=["id"],
            file_mapping={
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": str(target_dir), "pattern": "financials_{region}.csv"},
                "unmatched_policy": "ignore",
            },
        )
        svc = CompareService(db, ConfigRepository(db))
        svc.run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "PASSED"  # must NOT be ERROR -- ignore policy must be honored
        result = run.results[0]
        assert result.mismatch_summary["pairs_total"] == 1
        assert len(result.mismatch_summary["unmatched_sources"]) == 1
        assert result.mismatch_summary["unmatched_sources"][0]["key"] == {"region": "north"}
    finally:
        db.close()


def test_run_multi_file_compare_rejects_remote_kinds(tmp_path) -> None:
    from api.services.compare_service import CompareService

    db = _make_db()
    try:
        run_id = "test-run-mf-compare-s3"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="multi_file")

        req = MultiFileCompareRequest(file_mapping={
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        })
        svc = CompareService(db, ConfigRepository(db))
        svc.run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "ERROR"
        assert len(run.results) == 1
        assert "local" in (run.results[0].error_message or "").lower()
    finally:
        db.close()
