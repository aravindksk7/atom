from __future__ import annotations

import pytest

from api.schemas import JobDefinition


def test_bo_job_requires_object_id():
    with pytest.raises(ValueError, match="bo_job jobs require 'object_id' in params"):
        JobDefinition(name="refresh_sales", job_type="bo_job", params={})


def test_bo_job_valid_with_object_id():
    job = JobDefinition(
        name="refresh_sales",
        job_type="bo_job",
        params={"object_id": "3001"},
    )
    assert job.params["object_id"] == "3001"


def test_bo_job_accepts_optional_schedule_params_and_polling_overrides():
    job = JobDefinition(
        name="refresh_sales",
        job_type="bo_job",
        params={
            "object_id": "3001",
            "schedule_params": {"prompt_values": {"region": "EMEA"}},
            "poll_interval_s": 2,
            "timeout_s": 120,
        },
    )
    assert job.params["schedule_params"] == {"prompt_values": {"region": "EMEA"}}
    assert job.params["poll_interval_s"] == 2
    assert job.params["timeout_s"] == 120
