from __future__ import annotations

import pytest

from api.schemas import JobDefinition
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
