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


def test_aws_athena_query_rejects_invalid_operator_assertions():
    issues = validate_job_definition({
        "name": "athena_orders",
        "job_type": "aws_athena_query",
        "params": {
            "config_id": 1,
            "query": "select 1",
            "output_location": "s3://out/",
            "metric_assertions": {"row_count": {"operator": "invalid"}},
        },
    })
    invalid_issues = [i for i in issues if i.field == "params.metric_assertions.row_count"]
    assert len(invalid_issues) == 1
    assert "unsupported operator" in invalid_issues[0].message


def test_aws_athena_query_accepts_valid_operator_assertions():
    issues = validate_job_definition({
        "name": "athena_orders",
        "job_type": "aws_athena_query",
        "params": {
            "config_id": 1,
            "query": "select 1",
            "output_location": "s3://out/",
            "metric_assertions": {
                "row_count": {"operator": "between", "min": 2, "max": 5},
                "null_counts.id": {"operator": "<=", "value": 0},
            },
        },
    })
    metric_issues = [i for i in issues if i.field.startswith("params.metric_assertions")]
    assert metric_issues == []


def test_aws_athena_query_legacy_scalar_assertions_accepted():
    issues = validate_job_definition({
        "name": "athena_orders",
        "job_type": "aws_athena_query",
        "params": {
            "config_id": 1,
            "query": "select 1",
            "output_location": "s3://out/",
            "metric_assertions": {"row_count": 10},
        },
    })
    metric_issues = [i for i in issues if i.field.startswith("params.metric_assertions")]
    assert metric_issues == []


def test_airflow_dag_run_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "airflow_etl",
        "job_type": "airflow_dag_run",
        "params": {
            "config_id": 1,
            "dag_id": "nightly_etl",
            "expected_status": "success",
            "conf": {"region": "eu-west-1"},
            "poll_interval_seconds": 1.0,
            "max_attempts": 60,
            "task_assertions": {"extract": "success", "load": "skipped"},
        },
    })
    assert issues == []


def test_airflow_dag_run_requires_dag_id():
    issues = validate_job_definition({
        "name": "airflow_etl",
        "job_type": "airflow_dag_run",
        "params": {"config_id": 1},
    })
    assert any(issue.field == "params.dag_id" for issue in issues)


def test_airflow_dag_run_rejects_bad_expected_status():
    issues = validate_job_definition({
        "name": "airflow_etl",
        "job_type": "airflow_dag_run",
        "params": {"config_id": 1, "dag_id": "nightly_etl", "expected_status": "SUCCESS"},
    })
    assert any(issue.field == "params.expected_status" for issue in issues)


def test_airflow_dag_run_rejects_bad_conf():
    issues = validate_job_definition({
        "name": "airflow_etl",
        "job_type": "airflow_dag_run",
        "params": {"config_id": 1, "dag_id": "nightly_etl", "conf": ["not", "a", "dict"]},
    })
    assert any(issue.field == "params.conf" for issue in issues)


def test_airflow_dag_run_requires_positive_poll_and_attempts():
    issues = validate_job_definition({
        "name": "airflow_etl",
        "job_type": "airflow_dag_run",
        "params": {"config_id": 1, "dag_id": "nightly_etl", "poll_interval_seconds": 0, "max_attempts": 0},
    })
    fields = {issue.field for issue in issues}
    assert fields == {"params.poll_interval_seconds", "params.max_attempts"}


def test_airflow_dag_run_rejects_invalid_task_assertions():
    issues = validate_job_definition({
        "name": "airflow_etl",
        "job_type": "airflow_dag_run",
        "params": {"config_id": 1, "dag_id": "nightly_etl", "task_assertions": {"extract": "SUCCESS", "load": "queued"}},
    })
    assert any(issue.field == "params.task_assertions.extract" for issue in issues)


def test_compare_job_without_compare_type_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {"request": {}},
    })

    assert any(
        i.field == "params.compare_type" and i.severity == ValidationSeverity.ERROR
        for i in issues
    )


def test_compare_job_without_a_request_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {"compare_type": "bo"},
    })

    assert any(
        i.field == "params.request" and i.severity == ValidationSeverity.ERROR
        for i in issues
    )


def test_compare_job_warns_that_rules_are_ignored():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {
            "compare_type": "bo",
            "request": {"source_a": {}, "source_b": {}},
            "rules": [{"rule_type": "not_null", "column": "id"}],
        },
    })

    warnings = [i for i in issues if i.severity == ValidationSeverity.WARNING]
    assert any("rules" in i.field for i in warnings)


def test_a_valid_compare_job_reports_no_errors():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {
            "compare_type": "bo",
            "request": {
                "source_a": {"source_type": "path", "file_path": "/data/a.csv"},
                "source_b": {"source_type": "path", "file_path": "/data/b.csv"},
            },
        },
    })

    assert [i for i in issues if i.severity == ValidationSeverity.ERROR] == []


def test_compare_bo_job_with_invalid_request_contents_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {
            "compare_type": "bo",
            "request": {
                "source_a": {"source_type": "path"},
                "source_b": {"source_type": "path", "file_path": "/data/b.csv"},
            },
        },
    })

    errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
    assert any(i.field == "params.request" and "file_path required for path source" in i.message for i in errors)


def test_compare_bo_job_with_upload_source_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {
            "compare_type": "bo",
            "request": {
                "source_a": {"source_type": "path", "file_path": "/data/a.csv"},
                "source_b": {"source_type": "upload", "file_content_b64": "aWQK", "file_name": "b.csv"},
            },
        },
    })

    errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
    assert any(i.field == "params.request.source_b" and "file upload" in i.message for i in errors)


def test_compare_bo_job_with_live_source_without_doc_id_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_compare",
        "job_type": "compare",
        "params": {
            "compare_type": "bo",
            "request": {
                "source_a": {"source_type": "live", "config_id": 1},
                "source_b": {"source_type": "path", "file_path": "/data/b.csv"},
                "key_columns": ["id"],
            },
        },
    })

    errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
    assert any(i.field == "params.request.source_a" and "doc_id" in i.message for i in errors)


def test_compare_recon_file_job_with_invalid_request_contents_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_file_diff",
        "job_type": "compare",
        "params": {
            "compare_type": "recon_file",
            "request": {
                "file_a_path": "/data/a.csv",
            },
        },
    })

    errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
    assert any(i.field == "params.request" and "Source B requires exactly one" in i.message for i in errors)


def test_compare_recon_file_job_with_stored_run_source_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_file_diff",
        "job_type": "compare",
        "params": {
            "compare_type": "recon_file",
            "request": {
                "stored_run_id": "run-1",
                "file_b_path": "/data/b.csv",
            },
        },
    })

    errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
    assert any(i.field == "params.request.source_a" and "stored run" in i.message for i in errors)


def test_compare_matrix_job_reports_no_errors_for_repeatable_sources():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_matrix",
        "job_type": "compare",
        "params": {
            "compare_type": "matrix",
            "request": {
                "source_a": {"source_type": "file", "file_path": "/data/a.csv"},
                "source_b": {"source_type": "sql", "config_id": 1, "query_or_table": "SELECT * FROM t"},
            },
        },
    })

    assert [i for i in issues if i.severity == ValidationSeverity.ERROR] == []


def test_compare_matrix_job_with_upload_source_reports_an_error():
    from etl_framework.runner.job_validation import validate_job_definition, ValidationSeverity

    issues = validate_job_definition({
        "name": "nightly_matrix",
        "job_type": "compare",
        "params": {
            "compare_type": "matrix",
            "request": {
                "source_a": {"source_type": "file", "file_path": "/data/a.csv"},
                "source_b": {"source_type": "file", "file_b64": "aWQK", "file_name": "b.csv"},
            },
        },
    })

    errors = [i for i in issues if i.severity == ValidationSeverity.ERROR]
    assert any(i.field == "params.request.source_b" and "upload" in i.message for i in errors)


def test_aws_glue_job_run_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "spark_etl",
        "job_type": "aws_glue_job_run",
        "params": {
            "config_id": 1,
            "job_name": "daily_spark_job",
            "expected_status": "SUCCEEDED",
            "arguments": {"--env": "qa"},
            "poll_interval_seconds": 1.0,
            "max_attempts": 60,
        },
    })
    assert issues == []


def test_aws_glue_job_run_requires_config_and_job_name():
    issues = validate_job_definition({
        "name": "spark_etl",
        "job_type": "aws_glue_job_run",
        "params": {},
    })
    fields = {issue.field for issue in issues}
    assert "params.config_id" in fields
    assert "params.job_name" in fields


def test_aws_glue_job_run_rejects_invalid_expected_status():
    issues = validate_job_definition({
        "name": "spark_etl",
        "job_type": "aws_glue_job_run",
        "params": {"config_id": 1, "job_name": "daily_job", "expected_status": "COMPLETED"},
    })
    assert any(issue.field == "params.expected_status" for issue in issues)


def test_aws_glue_job_run_rejects_non_dict_arguments():
    issues = validate_job_definition({
        "name": "spark_etl",
        "job_type": "aws_glue_job_run",
        "params": {"config_id": 1, "job_name": "daily_job", "arguments": ["--env", "qa"]},
    })
    assert any(issue.field == "params.arguments" for issue in issues)


def test_aws_glue_job_run_requires_positive_poll_and_attempts():
    issues = validate_job_definition({
        "name": "spark_etl",
        "job_type": "aws_glue_job_run",
        "params": {"config_id": 1, "job_name": "daily_job", "poll_interval_seconds": 0, "max_attempts": 0},
    })
    fields = {issue.field for issue in issues}
    assert fields == {"params.poll_interval_seconds", "params.max_attempts"}


def test_job_definition_schema_validates_aws_glue_job_run():
    job = JobDefinition(
        name="spark_etl",
        job_type="aws_glue_job_run",
        params={"config_id": 1, "job_name": "daily_spark_job"},
    )
    assert job.job_type == "aws_glue_job_run"

    with pytest.raises(ValueError, match="aws_glue_job_run jobs require 'config_id' or 'config'"):
        JobDefinition(
            name="spark_etl",
            job_type="aws_glue_job_run",
            params={"job_name": "daily_spark_job"},
        )

    with pytest.raises(ValueError, match="aws_glue_job_run jobs require 'job_name'"):
        JobDefinition(
            name="spark_etl",
            job_type="aws_glue_job_run",
            params={"config_id": 1},
        )

