# tests/unit/test_task2.py
from datetime import datetime
from etl_framework.exceptions import SchemaValidationError, RepositoryError
from etl_framework.runner.state import TestStatus
from etl_framework.reconciliation.models import ReconciliationResult, MismatchRecord


def test_schema_validation_error_stores_fields():
    exc = SchemaValidationError("my_query", ["col_a"], ["col_b"])
    assert exc.query_name == "my_query"
    assert exc.missing_in_target == ["col_a"]
    assert exc.extra_in_target == ["col_b"]
    assert "my_query" in str(exc)
    assert "col_a" in str(exc)


def test_schema_validation_error_is_etl_framework_error():
    from etl_framework.exceptions import ETLFrameworkError
    exc = SchemaValidationError("q", [], [])
    assert isinstance(exc, ETLFrameworkError)


def test_repository_error_stores_fields():
    original = ValueError("disk full")
    exc = RepositoryError("sqlite", "save", original)
    assert exc.backend == "sqlite"
    assert exc.operation == "save"
    assert exc.original_error is original
    assert "sqlite" in str(exc)


def test_slow_status_in_enum():
    assert TestStatus.SLOW == "SLOW"
    assert TestStatus.SLOW in list(TestStatus)


def test_all_expected_statuses_present():
    statuses = {s.value for s in TestStatus}
    assert statuses == {"PENDING", "RUNNING", "PASSED", "FAILED", "ERROR", "SKIPPED", "SLOW"}


def test_reconciliation_result_has_schema_diff_field():
    result = ReconciliationResult(
        query_name="q",
        source_env="dev",
        target_env="qa",
        source_row_count=5,
        target_row_count=4,
        matched_count=4,
        missing_in_target_count=1,
        missing_in_source_count=0,
        value_mismatch_count=0,
        mismatches=[],
        status=TestStatus.FAILED,
        executed_at=datetime.now(),
        duration_seconds=0.5,
        schema_diff={"missing_in_target": ["col_x"], "extra_in_target": []},
    )
    assert result.schema_diff["missing_in_target"] == ["col_x"]


def test_reconciliation_result_schema_diff_defaults_to_none():
    result = ReconciliationResult(
        query_name="q", source_env="dev", target_env="qa",
        source_row_count=1, target_row_count=1, matched_count=1,
        missing_in_target_count=0, missing_in_source_count=0,
        value_mismatch_count=0, mismatches=[],
        status=TestStatus.PASSED,
        executed_at=datetime.now(), duration_seconds=0.1,
    )
    assert result.schema_diff is None
