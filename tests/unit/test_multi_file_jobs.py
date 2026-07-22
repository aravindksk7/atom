# tests/unit/test_multi_file_jobs.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import JobDefinition


def test_multi_file_job_requires_file_mapping() -> None:
    with pytest.raises(ValidationError, match="require a 'file_mapping' object"):
        JobDefinition(
            name="regional_sales_recon",
            job_type="reconciliation",
            query="",
            key_columns=["id"],
            params={"source_mode": "multi_file"},
        )


def test_multi_file_job_accepts_valid_file_mapping() -> None:
    job = JobDefinition(
        name="regional_sales_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
            },
        },
    )

    assert job.params["source_mode"] == "multi_file"


from etl_framework.runner.job_validation import validate_job_definition


def test_validate_job_definition_flags_missing_file_mapping() -> None:
    issues = validate_job_definition({
        "name": "regional_sales_recon",
        "job_type": "reconciliation",
        "params": {"source_mode": "multi_file"},
    })

    assert any("file_mapping" in issue.field for issue in issues)


def test_validate_job_definition_accepts_valid_multi_file_config() -> None:
    issues = validate_job_definition({
        "name": "regional_sales_recon",
        "job_type": "reconciliation",
        "params": {
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region"],
                "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
                "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
            },
        },
    })

    assert issues == []


def test_resolve_allowed_path_is_publicly_importable(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    resolved = file_source.resolve_allowed_path(str(tmp_path / "sub"))

    assert resolved == (tmp_path / "sub").resolve()


from api.schemas import RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.runner.state import TestStatus


def test_run_executor_multi_file_reconciliation_two_pairs(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n2,bravo\n", encoding="utf-8")
    (source_dir / "sales_data_west_20260101.csv").write_text("id,value\n1,charlie\n", encoding="utf-8")
    (target_dir / "financials_east_20260101.dat").write_text("id,value\n1,alpha\n2,bravo\n", encoding="utf-8")
    (target_dir / "financials_west_20260101.dat").write_text("id,value\n1,zulu\n", encoding="utf-8")

    job = JobDefinition(
        name="regional_sales_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region", "date"],
                "source": {
                    "kind": "local",
                    "root": str(source_dir),
                    "pattern": "sales_data_{region}_{date:%Y%m%d}.csv",
                },
                "target": {
                    "kind": "local",
                    "root": str(target_dir),
                    "pattern": "financials_{region}_{date:%Y%m%d}.dat",
                },
                "unmatched_policy": "fail",
            },
        },
    )
    executor = RunExecutor(
        db=None,
        run_id="test-run",
        source_env="source",
        target_env="target",
        job_sequence=[],
        run_settings=RunSettings(chunk_size=100, use_hash_precheck=True),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    result = executor._build_case(job)()

    assert result.status == TestStatus.FAILED
    assert result.mismatch_summary["pairs_total"] == 2
    assert result.mismatch_summary["pairs_passed"] == 1
    assert result.mismatch_summary["pairs_failed"] == 1
    by_region = {p["key"]["region"]: p for p in result.mismatch_summary["file_pairs"]}
    assert by_region["east"]["status"] == "PASSED"
    assert by_region["west"]["status"] == "FAILED"
    assert result.source_file_name == "2 file(s) across 2 pair(s)"


def test_run_executor_multi_file_reconciliation_fails_fast_on_unmatched_by_default(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_data_east_20260101.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    # No matching target file for "east" -- unmatched source group.

    job = JobDefinition(
        name="regional_sales_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "multi_file",
            "file_mapping": {
                "strategy": "explicit",
                "match_on": ["region", "date"],
                "source": {
                    "kind": "local",
                    "root": str(source_dir),
                    "pattern": "sales_data_{region}_{date:%Y%m%d}.csv",
                },
                "target": {
                    "kind": "local",
                    "root": str(target_dir),
                    "pattern": "financials_{region}_{date:%Y%m%d}.dat",
                },
            },
        },
    )
    executor = RunExecutor(
        db=None,
        run_id="test-run",
        source_env="source",
        target_env="target",
        job_sequence=[],
        run_settings=RunSettings(chunk_size=100, use_hash_precheck=True),
        config_snapshot={},
    )
    executor._resolve_segment_columns = lambda _job: []

    with pytest.raises(ValueError, match="unmatched source group"):
        executor._build_case(job)()
