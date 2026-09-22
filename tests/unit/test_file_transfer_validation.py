from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.schemas import JobDefinition
from api.services.job_env_validation import validate_env_requirements
from etl_framework.runner.job_validation import validate_job_definition


def _job(**param_overrides):
    params = {
        "source": {"kind": "sftp", "root": "/in", "pattern": "SALES_*.csv", "credentials_ref": "vendor"},
        "destination": {"kind": "s3", "root": "s3://bkt/out", "credentials_ref": "aws"},
        "on_exists": "skip",
        "recursive": True,
        "preserve_structure": True,
    }
    params.update(param_overrides)
    return {"name": "stage_sales", "job_type": "file_transfer", "params": params}


def test_valid_file_transfer_job_definition_builds():
    job = JobDefinition(**_job())
    assert job.job_type == "file_transfer"


def test_valid_file_transfer_job_has_no_validation_issues():
    assert validate_job_definition(_job()) == []


def test_job_definition_rejects_missing_destination():
    payload = _job()
    del payload["params"]["destination"]
    with pytest.raises(ValueError, match="'destination' object"):
        JobDefinition(**payload)


def test_job_definition_rejects_remote_kind_without_credentials_ref():
    payload = _job(destination={"kind": "s3", "root": "s3://bkt/out"})
    with pytest.raises(ValueError, match="requires 'credentials_ref'"):
        JobDefinition(**payload)


def test_job_definition_rejects_preserve_structure_without_recursive():
    payload = _job(recursive=False)
    with pytest.raises(ValueError, match="requires recursive"):
        JobDefinition(**payload)


def test_validate_job_definition_reports_field_level_issues():
    payload = _job(source={"kind": "local"}, on_exists="merge")
    issues = validate_job_definition(payload)
    fields = {issue.field for issue in issues}
    assert {"params.source.root", "params.source.pattern", "params.on_exists"} <= fields


def test_file_transfer_job_does_not_require_a_target_env():
    job = JobDefinition(**_job())
    validate_env_requirements([{"job_name": "stage_sales"}], {"stage_sales": job}, "")


def test_reconciliation_job_still_requires_a_target_env():
    job = JobDefinition(name="orders", query="SELECT 1", key_columns=["id"])
    with pytest.raises(HTTPException) as exc_info:
        validate_env_requirements([{"job_name": "orders"}], {"orders": job}, "")
    assert exc_info.value.status_code == 422


def test_file_watcher_accepts_smb_location():
    job = JobDefinition(
        name="watch_vendor_share", job_type="file_watcher",
        params={
            "location": {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "SALES_*.csv", "credentials_ref": "vendor-share"},
            "max_tries": 5,
        },
    )
    assert job.job_type == "file_watcher"
    assert validate_job_definition(job) == []


def test_file_watcher_smb_requires_credentials_ref():
    with pytest.raises(ValueError, match="credentials_ref"):
        JobDefinition(
            name="watch_vendor_share_2", job_type="file_watcher",
            params={
                "location": {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "SALES_*.csv"},
                "max_tries": 5,
            },
        )
