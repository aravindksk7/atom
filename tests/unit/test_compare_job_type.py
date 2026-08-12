"""Validation for the `compare` job type."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import JobDefinition


def _bo_request(**overrides) -> dict:
    request = {
        "source_a": {"source_type": "path", "file_path": "/data/a.csv"},
        "source_b": {"source_type": "path", "file_path": "/data/b.csv"},
        "key_columns": ["id"],
    }
    return {**request, **overrides}


def test_compare_job_accepts_a_bo_request_with_repeatable_sources():
    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": _bo_request()},
    )

    assert job.params["compare_type"] == "bo"


def test_compare_job_requires_a_known_compare_type():
    with pytest.raises(ValidationError, match="compare_type"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "sql", "request": _bo_request()},
        )


def test_compare_job_requires_a_request_body():
    with pytest.raises(ValidationError, match="params.request"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "bo"},
        )


def test_compare_job_rejects_an_upload_source():
    with pytest.raises(ValidationError, match="Source B"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "bo", "request": _bo_request(
                source_b={"source_type": "upload", "file_content_b64": "aWQK", "file_name": "b.csv"},
            )},
        )


def test_compare_job_rejects_a_past_run_source():
    with pytest.raises(ValidationError, match="Source A"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "bo", "request": _bo_request(
                source_a={"source_type": "run", "run_id": "run-1", "job_name": "prior"},
            )},
        )


def test_compare_job_rejects_live_bo_source_without_document_id():
    with pytest.raises(ValidationError, match="doc_id"):
        JobDefinition(
            name="nightly_compare",
            job_type="compare",
            params={"compare_type": "bo", "request": _bo_request(
                source_a={"source_type": "live", "config_id": 1},
            )},
        )


def test_compare_job_rejects_a_recon_file_stored_run_source():
    with pytest.raises(ValidationError, match="Source A"):
        JobDefinition(
            name="nightly_file_diff",
            job_type="compare",
            params={"compare_type": "recon_file", "request": {
                "stored_run_id": "run-1",
                "file_b_path": "/data/b.csv",
            }},
        )


def test_compare_job_accepts_two_recon_file_paths():
    job = JobDefinition(
        name="nightly_file_diff",
        job_type="compare",
        params={"compare_type": "recon_file", "request": {
            "file_a_path": "/data/a.csv",
            "file_b_path": "/data/b.csv",
        }},
    )

    assert job.params["compare_type"] == "recon_file"


def test_compare_job_mirrors_key_and_exclude_columns_from_the_request():
    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": _bo_request(
            key_columns=["region", "product"],
            exclude_columns=["loaded_at"],
        )},
    )

    assert job.key_columns == ["region", "product"]
    assert job.exclude_columns == ["loaded_at"]


def test_compare_job_mirroring_clears_stale_top_level_columns():
    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        key_columns=["stale"],
        params={"compare_type": "bo", "request": _bo_request(key_columns=["id"])},
    )

    assert job.key_columns == ["id"]
