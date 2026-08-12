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


def test_run_multi_file_compare_supports_xlsx_with_different_dynamic_names(tmp_path, monkeypatch) -> None:
    import pandas as pd

    from api.services import file_source
    from api.services.compare_service import CompareService

    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    source_name = "sales_east_17.xlsx"
    target_name = "financials-east-B17.xlsx"
    frame = pd.DataFrame({"id": [1, 2], "amount": [100.0, 250.5]})
    frame.to_excel(source_dir / source_name, index=False)
    frame.to_excel(target_dir / target_name, index=False)

    db = _make_db()
    try:
        run_id = "test-run-mf-compare-xlsx-dynamic"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="multi_file")

        req = MultiFileCompareRequest(
            key_columns=["id"],
            file_mapping={
                "strategy": "explicit",
                "match_on": ["region", "batch"],
                "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_{region:alpha}_{batch:num}.xlsx"},
                "target": {"kind": "local", "root": str(target_dir), "pattern": "financials-{region:alpha}-B{batch:num}.xlsx"},
            },
        )
        svc = CompareService(db, ConfigRepository(db))
        svc.run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "PASSED"
        assert len(run.results) == 1
        pair_summary = run.results[0].mismatch_summary["file_pairs"][0]
        assert pair_summary["source_files"] == [source_name]
        assert pair_summary["target_files"] == [target_name]
    finally:
        db.close()


def test_full_differences_recompute_includes_ad_hoc_multi_file_rows(tmp_path, monkeypatch) -> None:
    """Regression: write_recomputed_differences (used by the full-differences
    export, "Load all for this test", and the full HTML report) had no
    ``multi_file`` branch, so an ad-hoc multi-file run fell through to
    _write_reconciliation_run -- which only recomputes SAVED jobs -- and wrote
    ZERO rows. That made every "show all differences beyond the cap" surface
    come back empty for multi-file compares. This asserts the recompute now
    reproduces the run's real differences.
    """
    from api.services import file_source
    from api.services.compare_service import CompareService
    from api.services.difference_export import write_recomputed_differences

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
        run_id = "test-run-mf-recompute"
        req = MultiFileCompareRequest(
            key_columns=["id"],
            file_mapping={
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": str(target_dir), "pattern": "financials_{region}.csv"},
            },
        )
        # Mirror what api/routes/compare.py:compare_multi_file persists so the
        # recompute dispatcher can identify this as a multi_file run.
        RunRepository(db).create_run(
            run_id=run_id, source_env="Source A", target_env="Source B", run_type="multi_file",
            config_snapshot={"compare_request_type": "multi_file", "request": req.model_dump(mode="json")},
        )
        CompareService(db, ConfigRepository(db)).run_multi_file_compare(req, run_id)

        run = RunRepository(db).get_run(run_id)
        expected = run.results[0].value_mismatch_count
        assert expected >= 1  # region=west: beta vs BETA

        out = tmp_path / "recompute.jsonl"
        row_count = write_recomputed_differences(db, run, "json", out)
        assert row_count == expected, f"recompute wrote {row_count} rows, expected {expected}"

        body = out.read_text(encoding="utf-8")
        assert "__pair__" in body  # rows carry their file-pair key
        assert "west" in body      # the mismatching pair (region=west)
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


def test_run_multi_file_compare_from_run_reference(tmp_path, monkeypatch) -> None:
    from api.services import file_source, upload_store
    from api.services.compare_service import CompareService
    from etl_framework.reconciliation.models import ReconciliationResult

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    # read_tabular's path allow-list (file_source._UPLOAD_BASES) is separate
    # from upload_store.UPLOAD_ROOT -- both must point at tmp_path, same as
    # every other test in this file that reads persisted artifacts back.
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    source_path = upload_store.persist_run_data_artifact("prior-run", b"id,value\n1,alpha\n", "job_pair0_source.csv")
    target_path = upload_store.persist_run_data_artifact("prior-run", b"id,value\n1,alpha\n", "job_pair0_target.csv")

    db = _make_db()
    try:
        repo = RunRepository(db)
        repo.create_run("prior-run", "source", "target")
        repo.add_test_result("prior-run", ReconciliationResult(
            query_name="regional_sales_recon", source_env="source", target_env="target",
            source_row_count=1, target_row_count=1, matched_count=1,
            missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
            mismatch_summary={
                "file_pairs": [{
                    "key": {"region": "east"},
                    "source_files": ["sales_east.csv"], "target_files": ["fin_east.csv"],
                    "source_artifact_path": source_path, "target_artifact_path": target_path,
                }],
            },
        ))

        svc = CompareService(db, ConfigRepository(db))
        compare_run_id = "compare-run-1"
        repo.create_run(compare_run_id, "Source A", "Source B")
        req = MultiFileCompareRequest(run_id="prior-run", job_name="regional_sales_recon", key_columns=["id"])

        svc.run_multi_file_compare(req, compare_run_id)

        run = repo.get_run(compare_run_id)
        assert run.status == "PASSED"
    finally:
        db.close()


def test_run_multi_file_compare_from_run_reference_404s_on_unknown_job(tmp_path, monkeypatch) -> None:
    from api.services import upload_store
    from api.services.compare_service import CompareService

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())

    db = _make_db()
    try:
        repo = RunRepository(db)
        repo.create_run("prior-run", "source", "target")

        svc = CompareService(db, ConfigRepository(db))
        compare_run_id = "compare-run-1"
        repo.create_run(compare_run_id, "Source A", "Source B")
        req = MultiFileCompareRequest(run_id="prior-run", job_name="no_such_job")

        svc.run_multi_file_compare(req, compare_run_id)

        run = repo.get_run(compare_run_id)
        assert run.status == "ERROR"
    finally:
        db.close()
