from __future__ import annotations

import pytest

from api.schemas import JobDefinition
from etl_framework.runner.job_validation import raise_for_validation_issues, validate_job_definition


def test_valid_reconciliation_job_has_no_issues():
    job = JobDefinition(name="orders", query="SELECT * FROM orders", key_columns=["id"])
    assert validate_job_definition(job) == []


def test_invalid_reconciliation_job_reports_missing_fields():
    issues = validate_job_definition({"name": "orders", "job_type": "reconciliation", "query": "", "key_columns": []})
    assert {issue.field for issue in issues} == {"query", "key_columns"}
    with pytest.raises(ValueError, match="reconciliation jobs require"):
        raise_for_validation_issues(issues)


def test_file_backed_reconciliation_does_not_require_key_columns():
    issues = validate_job_definition({
        "name": "files",
        "job_type": "reconciliation",
        "params": {
            "source_mode": "files",
            "source_file_path": r"c:\temp\RMS_FUT_20260601_qa.xml",
            "target_file_path": r"c:\temp\RMS_FUT_20260601_prod.xml",
        },
        "key_columns": [],
    })
    assert issues == []


def test_file_backed_reconciliation_requires_both_files():
    issues = validate_job_definition({
        "name": "files",
        "job_type": "reconciliation",
        "params": {"source_mode": "files", "source_path": "a.csv"},
        "key_columns": ["id"],
    })
    assert any(issue.field == "params" for issue in issues)


def test_file_backed_reconciliation_accepts_job_file_paths():
    issues = validate_job_definition({
        "name": "files",
        "job_type": "reconciliation",
        "params": {
            "source_mode": "files",
            "source_file_path": r"c:\temp\RMS_FUT_20260601_qa.xml",
            "target_file_path": r"c:\temp\RMS_FUT_20260601_prod.xml",
        },
        "key_columns": ["id"],
    })

    assert issues == []


def test_api_reconciliation_requires_endpoint_and_keys():
    issues = validate_job_definition({"name": "api", "job_type": "api_reconciliation", "params": {}, "key_columns": []})
    assert {issue.field for issue in issues} == {"params.source_api_endpoint", "key_columns"}
