from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import JobDefinition


def test_bo_live_reconciliation_requires_report_id() -> None:
    with pytest.raises(ValidationError, match="report_id"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "bo_report_id": "1",
                "target_file_path": "prod_snapshot.xlsx",
            },
        )


def test_bo_live_reconciliation_requires_bo_report_id() -> None:
    with pytest.raises(ValidationError, match="bo_report_id"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "target_file_path": "prod_snapshot.xlsx",
            },
        )


def test_bo_live_reconciliation_requires_target_file() -> None:
    with pytest.raises(ValidationError, match="target file"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "bo_report_id": "1",
            },
        )


def test_bo_live_reconciliation_accepts_valid_config() -> None:
    job = JobDefinition(
        name="qa_vs_prod",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "bo_live",
            "report_id": "101",
            "bo_report_id": "1",
            "format": "csv",
            "target_file_path": "prod_snapshot.csv",
        },
    )
    assert job.params["source_mode"] == "bo_live"


def test_bo_live_reconciliation_accepts_uploaded_target_file() -> None:
    job = JobDefinition(
        name="qa_vs_prod",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "bo_live",
            "report_id": "101",
            "bo_report_id": "1",
            "target_file_content_b64": "aWQsdmFsdWUKMSxhbHBoYQo=",
            "target_file_name": "prod_snapshot.csv",
        },
    )
    assert job.params["target_file_name"] == "prod_snapshot.csv"


def test_bo_live_reconciliation_upload_without_name_rejected() -> None:
    with pytest.raises(ValidationError, match="file name"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "bo_report_id": "1",
                "target_file_content_b64": "aWQsdmFsdWUKMSxhbHBoYQo=",
            },
        )
