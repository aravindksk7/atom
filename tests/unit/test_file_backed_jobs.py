from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.runner.state import TestStatus


def test_file_backed_reconciliation_does_not_require_query() -> None:
    job = JobDefinition(
        name="orders_file_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "files",
            "source_file_path": "source.csv",
            "target_file_path": "target.csv",
        },
    )

    assert job.query == ""
    assert job.params["source_mode"] == "files"


def test_file_backed_reconciliation_requires_both_files() -> None:
    with pytest.raises(ValidationError, match="source and target files"):
        JobDefinition(
            name="orders_file_recon",
            job_type="reconciliation",
            query="",
            key_columns=["id"],
            params={
                "source_mode": "files",
                "source_file_path": "source.csv",
            },
        )


def test_run_executor_reconciles_file_backed_csv(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.csv"
    target = tmp_path / "target.csv"
    source.write_text("id,value\n1,alpha\n", encoding="utf-8")
    target.write_text("id,value\n1,beta\n", encoding="utf-8")

    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    job = JobDefinition(
        name="orders_file_recon",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "files",
            "source_file_path": str(source),
            "target_file_path": str(target),
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
    assert result.source_row_count == 1
    assert result.target_row_count == 1
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].column_name == "value"
