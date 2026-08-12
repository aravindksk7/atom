"""Single-vs-dual environment requirements for a job sequence.

Moved out of api/routes/selections.py so the selections route, the schedules
route, and the sequences route can all use it without importing from a route
module.
"""
from __future__ import annotations

from fastapi import HTTPException

# Job types whose execution only touches one environment (per the approved
# design spec); everything else needs a target_env to compare against.
SINGLE_ENV_JOB_TYPES = {
    "bo_report", "freshness", "profile", "automic_job",
    "dbt_artifact", "schema_snapshot", "bo_job", "ds_job",
}


def job_name_of(step) -> str:
    if isinstance(step, dict):
        return step.get("job_name", "")
    if hasattr(step, "job_name"):
        return step.job_name
    return str(step)


def validate_env_requirements(job_sequence: list, jobs_by_name: dict, target_env: str) -> None:
    if target_env:
        return
    for step in job_sequence:
        job_name = job_name_of(step)
        job = jobs_by_name.get(job_name)
        if job is not None and job.job_type not in SINGLE_ENV_JOB_TYPES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Job '{job_name}' (type '{job.job_type}') requires a target_env; "
                    "only single-environment job types can run with target_env omitted"
                ),
            )
