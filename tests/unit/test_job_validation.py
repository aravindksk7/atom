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


def test_bo_live_reconciliation_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "bo-live",
        "job_type": "reconciliation",
        "params": {
            "source_mode": "bo_live",
            "report_id": "rep-1",
            "bo_report_id": "bo-rep-1",
            "target_file_path": r"c:\temp\RMS_FUT_20260601_prod.xml",
        },
        "key_columns": [],
    })
    assert issues == []


def test_bo_live_reconciliation_requires_report_id():
    issues = validate_job_definition({
        "name": "bo-live",
        "job_type": "reconciliation",
        "params": {
            "source_mode": "bo_live",
            "bo_report_id": "bo-rep-1",
            "target_file_path": r"c:\temp\RMS_FUT_20260601_prod.xml",
        },
        "key_columns": [],
    })
    assert any(issue.field == "params.report_id" for issue in issues)


def test_bo_live_reconciliation_requires_target_file():
    issues = validate_job_definition({
        "name": "bo-live",
        "job_type": "reconciliation",
        "params": {
            "source_mode": "bo_live",
            "report_id": "rep-1",
            "bo_report_id": "bo-rep-1",
        },
        "key_columns": [],
    })
    assert any(issue.field == "params" for issue in issues)


def test_api_reconciliation_requires_endpoint_and_keys():
    issues = validate_job_definition({"name": "api", "job_type": "api_reconciliation", "params": {}, "key_columns": []})
    assert {issue.field for issue in issues} == {"params.source_api_endpoint", "key_columns"}


def test_bo_job_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "refresh_sales",
        "job_type": "bo_job",
        "params": {"object_id": "3001"},
    })
    assert issues == []


def test_bo_job_requires_object_id():
    issues = validate_job_definition({
        "name": "refresh_sales",
        "job_type": "bo_job",
        "params": {},
    })
    assert any(issue.field == "params.object_id" for issue in issues)


def test_ds_job_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "nightly_load",
        "job_type": "ds_job",
        "params": {"job_name": "DS_NIGHTLY_LOAD"},
    })
    assert issues == []


def test_ds_job_requires_job_name():
    issues = validate_job_definition({
        "name": "nightly_load",
        "job_type": "ds_job",
        "params": {},
    })
    assert any(issue.field == "params.job_name" for issue in issues)


def test_s3_row_count_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "orders_rows",
        "job_type": "s3_row_count",
        "params": {"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "min_rows": 1, "max_rows": 10},
    })
    assert issues == []


def test_s3_row_count_requires_identity_and_valid_bounds():
    issues = validate_job_definition({
        "name": "orders_rows",
        "job_type": "s3_row_count",
        "params": {"config_id": 1, "bucket": "", "key": "", "fmt": "xml", "min_rows": 10, "max_rows": 1},
    })
    fields = {issue.field for issue in issues}
    assert fields == {"params.bucket", "params.key", "params.fmt", "params.min_rows"}


def test_s3_format_validation_requires_schema_mapping_when_present():
    issues = validate_job_definition({
        "name": "orders_schema",
        "job_type": "s3_format_validation",
        "params": {"config": "qa", "bucket": "b", "key": "orders.csv", "fmt": "csv", "expected_schema": ["id"]},
    })
    assert any(issue.field == "params.expected_schema" for issue in issues)


def test_s3_partition_check_validates_columns_and_minimum():
    issues = validate_job_definition({
        "name": "orders_partitions",
        "job_type": "s3_partition_check",
        "params": {"config_id": 1, "bucket": "b", "prefix": "orders/", "expected_columns": ["dt", "region"], "min_partitions": 1},
    })
    assert issues == []

    bad = validate_job_definition({
        "name": "orders_partitions",
        "job_type": "s3_partition_check",
        "params": {"config_id": 1, "bucket": "b", "prefix": "", "expected_columns": ["dt", 3], "min_partitions": -1},
    })
    fields = {issue.field for issue in bad}
    assert fields == {"params.prefix", "params.expected_columns", "params.min_partitions"}


def test_job_definition_accepts_s3_job_types():
    for job_type in ("s3_row_count", "s3_format_validation", "s3_partition_check"):
        job = JobDefinition(
            name=job_type,
            job_type=job_type,
            params={"config_id": 1, "bucket": "b", "key": "k", "prefix": "p", "fmt": "csv"},
        )
        assert job.job_type == job_type


def test_aws_glue_catalog_compare_valid_job_has_no_issues():
    issues = validate_job_definition({"name": "glue_orders", "job_type": "aws_glue_catalog_compare", "params": {"config_id": 1, "source_database": "raw", "source_table": "orders", "target_database": "curated", "target_table": "orders"}})
    assert issues == []


def test_aws_glue_catalog_compare_requires_catalog_fields():
    issues = validate_job_definition({"name": "glue_orders", "job_type": "aws_glue_catalog_compare", "params": {"config_id": 1, "source_database": "", "source_table": "", "target_database": "", "target_table": "", "compare_location": "yes"}})
    fields = {issue.field for issue in issues}
    assert fields == {"params.source_database", "params.source_table", "params.target_database", "params.target_table", "params.compare_location"}
    messages = {issue.field: issue.message for issue in issues}
    assert messages["params.source_database"] == "aws_glue_catalog_compare jobs require 'source_database' in params"
    assert messages["params.source_table"] == "aws_glue_catalog_compare jobs require 'source_table' in params"


def test_aws_athena_query_valid_job_has_no_issues():
    issues = validate_job_definition({"name": "athena_orders", "job_type": "aws_athena_query", "params": {"config_id": 1, "database": "curated", "query": "select 1", "output_location": "s3://out/", "min_rows": 1, "max_rows_assert": 10, "expected_status": "SUCCEEDED"}})
    assert issues == []


def test_aws_athena_query_requires_query_output_and_valid_options():
    issues = validate_job_definition({"name": "athena_orders", "job_type": "aws_athena_query", "params": {"config_id": 1, "query": "", "output_location": "", "min_rows": 10, "max_rows_assert": 1, "expected_status": "DONE", "metric_assertions": []}})
    fields = {issue.field for issue in issues}
    assert fields == {"params.query", "params.output_location", "params.min_rows", "params.expected_status", "params.metric_assertions"}


def test_aws_athena_query_requires_positive_max_attempts():
    issues = validate_job_definition({"name": "athena_orders", "job_type": "aws_athena_query", "params": {"config_id": 1, "database": "curated", "query": "select 1", "output_location": "s3://out/", "max_attempts": 0}})
    assert any(issue.field == "params.max_attempts" for issue in issues)
