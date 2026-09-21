from __future__ import annotations

import pytest
from fastapi import HTTPException

from api.schemas import JobDefinition
from api.services.job_env_validation import validate_env_requirements

# Job types whose execution touches one environment or none at all: launching
# them without a target_env must be allowed.
SINGLE_ENV_JOBS: dict[str, dict] = {
    "bo_report": {"params": {"report_id": "42"}},
    "freshness": {"query": "SELECT 1", "params": {"timestamp_column": "ts"}},
    "profile": {"query": "SELECT 1"},
    "automic_job": {"params": {"job_name": "NIGHTLY"}},
    "dbt_artifact": {"params": {"run_results_path": "/artifacts/run_results.json"}},
    "schema_snapshot": {"query": "SELECT 1"},
    "bo_job": {"params": {"object_id": "7"}},
    "ds_job": {"params": {"job_name": "Job_Load"}},
    "file_transfer": {"params": {
        "source": {"kind": "sftp", "root": "/in", "pattern": "*.csv", "credentials_ref": "vendor"},
        "destination": {"kind": "s3", "root": "s3://bkt/out", "credentials_ref": "aws"},
    }},
    "s3_row_count": {"params": {"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv"}},
    "s3_format_validation": {"params": {"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv"}},
    "s3_partition_check": {"params": {"config_id": 1, "bucket": "b", "prefix": "orders/"}},
    "aws_glue_catalog_compare": {"params": {
        "config_id": 1,
        "source_database": "raw", "source_table": "orders",
        "target_database": "curated", "target_table": "orders",
    }},
    "aws_glue_job_run": {"params": {"config_id": 1, "job_name": "nightly_etl"}},
    "aws_athena_query": {"params": {
        "config_id": 1, "query": "select 1", "output_location": "s3://out/",
    }},
    "airflow_dag_run": {"params": {"config_id": 1, "dag_id": "nightly_etl"}},
    "file_watcher": {"params": {
        "location": {"kind": "local", "root": "/in", "pattern": "*.csv"},
        "max_tries": 3,
    }},
    "cross_job_assertion": {"params": {"source_job": "a", "target_job": "b"}},
}

# Job types that compare a source against a target at run time: omitting
# target_env has to stay a 422.
DUAL_ENV_JOBS: dict[str, dict] = {
    "reconciliation": {"query": "SELECT 1", "key_columns": ["id"]},
    "api_reconciliation": {
        "key_columns": ["id"],
        "params": {"source_api_endpoint": "https://api.example.com/orders"},
    },
    "compare": {"params": {"compare_type": "recon_file", "request": {
        "file_a_path": "/data/a.csv",
        "file_b_path": "/data/b.csv",
    }}},
    "health_check": {"query": "SELECT 1"},
}


def _job(job_type: str, spec: dict) -> JobDefinition:
    return JobDefinition(name="x", job_type=job_type, **spec)


@pytest.mark.parametrize("job_type", sorted(SINGLE_ENV_JOBS))
def test_single_environment_job_types_launch_without_a_target_env(job_type):
    job = _job(job_type, SINGLE_ENV_JOBS[job_type])
    validate_env_requirements([{"job_name": "x"}], {"x": job}, "")


@pytest.mark.parametrize("job_type", sorted(DUAL_ENV_JOBS))
def test_dual_environment_job_types_still_require_a_target_env(job_type):
    job = _job(job_type, DUAL_ENV_JOBS[job_type])
    with pytest.raises(HTTPException) as exc_info:
        validate_env_requirements([{"job_name": "x"}], {"x": job}, "")
    assert exc_info.value.status_code == 422
    assert "requires a target_env" in exc_info.value.detail


@pytest.mark.parametrize("job_type", sorted({**SINGLE_ENV_JOBS, **DUAL_ENV_JOBS}))
def test_a_non_empty_target_env_is_always_accepted(job_type):
    spec = {**SINGLE_ENV_JOBS, **DUAL_ENV_JOBS}[job_type]
    job = _job(job_type, spec)
    validate_env_requirements([{"job_name": "x"}], {"x": job}, "prod")
