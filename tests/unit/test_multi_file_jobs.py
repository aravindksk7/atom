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
