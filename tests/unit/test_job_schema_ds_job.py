from __future__ import annotations

import pytest

from api.schemas import JobDefinition


def test_ds_job_requires_job_name():
    with pytest.raises(ValueError, match="ds_job jobs require 'job_name' in params"):
        JobDefinition(name="nightly_load", job_type="ds_job", params={})


def test_ds_job_valid_with_job_name():
    job = JobDefinition(name="nightly_load", job_type="ds_job", params={"job_name": "DS_NIGHTLY_LOAD"})
    assert job.params["job_name"] == "DS_NIGHTLY_LOAD"


def test_ds_job_accepts_optional_repository_and_params():
    job = JobDefinition(
        name="nightly_load",
        job_type="ds_job",
        params={
            "job_name": "DS_NIGHTLY_LOAD",
            "repository": "DS_REPO_2",
            "job_params": {"$G_RUN_DATE": "2026-07-24"},
            "poll_interval_s": 2,
            "timeout_s": 120,
        },
    )
    assert job.params["repository"] == "DS_REPO_2"
    assert job.params["job_params"] == {"$G_RUN_DATE": "2026-07-24"}
    assert job.params["poll_interval_s"] == 2
    assert job.params["timeout_s"] == 120
