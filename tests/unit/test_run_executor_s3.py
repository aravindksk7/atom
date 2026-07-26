from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.exceptions import SchemaValidationError
from etl_framework.repository.database import Base
from etl_framework.runner.state import TestStatus


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def executor(db_session):
    return RunExecutor(
        db=db_session,
        run_id="run-1",
        source_env="qa",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(use_live_connections=True),
    )


def test_execute_s3_row_count_passes_within_bounds(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object(), filesystem=lambda config_id: object()))
    monkeypatch.setattr("api.services.run_executor.select_row_count", lambda client, bucket, key, fmt: 5)

    result = ex._execute_s3_row_count(JobDefinition(
        name="orders_rows",
        job_type="s3_row_count",
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "min_rows": 1, "max_rows": 10},
    ))

    assert result.status == TestStatus.PASSED
    assert result.source_row_count == 5
    assert result.target_row_count == 5
    assert result.mismatch_summary["metrics"]["row_count"] == 5
    assert result.mismatch_summary["metrics"]["engine"] == "s3_select"
    assert result.mismatch_summary["metrics"]["by_type"] == {}
    assert result.mismatch_summary["by_type"] == {}
    assert result.matched_count == 5
    assert result.executed_at is not None
    assert result.duration_seconds > 0


@pytest.mark.parametrize(
    ("job_type", "method_name"),
    [
        ("s3_row_count", "_execute_s3_row_count"),
        ("s3_format_validation", "_execute_s3_format_validation"),
        ("s3_partition_check", "_execute_s3_partition_check"),
    ],
)
def test_build_case_dispatches_s3_job_types(monkeypatch, db_session, job_type, method_name):
    ex = executor(db_session)
    called = []

    def execute(job):
        called.append(job.name)
        return SimpleNamespace(status=TestStatus.PASSED)

    monkeypatch.setattr(ex, method_name, execute)
    run_case = ex._build_case(JobDefinition(
        name="s3_job",
        job_type=job_type,
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "prefix": "orders/", "fmt": "csv"},
    ))

    result = run_case()

    assert result.status == TestStatus.PASSED
    assert called == ["s3_job"]


def test_execute_s3_row_count_fails_outside_bounds(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object(), filesystem=lambda config_id: object()))
    monkeypatch.setattr("api.services.run_executor.select_row_count", lambda client, bucket, key, fmt: 0)

    result = ex._execute_s3_row_count(JobDefinition(
        name="orders_rows",
        job_type="s3_row_count",
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "min_rows": 1},
    ))

    assert result.status == TestStatus.FAILED
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].mismatch_type == "row_count_below_min"
    assert result.mismatch_summary["by_type"] == {"row_count_below_min": 1}
    assert result.mismatch_summary["metrics"]["by_type"] == {"row_count_below_min": 1}
    assert result.matched_count == 0
    assert result.executed_at is not None
    assert result.duration_seconds > 0


def test_execute_s3_format_validation_fails_schema_drift(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object()))

    def drift(client, bucket, key, fmt, expected_schema):
        raise SchemaValidationError(
            "s3://b/orders.csv",
            missing_in_target=["email"],
            extra_in_target=["name"],
            type_mismatches=[{"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}],
        )

    monkeypatch.setattr("api.services.run_executor.validate_format", drift)

    result = ex._execute_s3_format_validation(JobDefinition(
        name="orders_schema",
        job_type="s3_format_validation",
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "expected_schema": {"amount": "decimal(12,2)"}},
    ))

    assert result.status == TestStatus.FAILED
    assert {m.mismatch_type for m in result.mismatches} == {"missing_columns", "extra_columns", "type_mismatch"}
    assert result.mismatch_summary["by_type"] == {"missing_columns": 1, "extra_columns": 1, "type_mismatch": 1}
    assert result.mismatch_summary["metrics"]["by_type"] == {"missing_columns": 1, "extra_columns": 1, "type_mismatch": 1}
    assert result.mismatch_summary["metrics"]["parsed"] is False
    assert result.mismatch_summary["metrics"]["schema_ok"] is False
    assert result.mismatch_summary["schema_diff"]["type_mismatches"] == [
        {"column": "amount", "expected_type": "decimal(12,2)", "actual_type": "string"}
    ]
    assert result.matched_count == 0
    assert result.executed_at is not None
    assert result.duration_seconds > 0


def test_execute_s3_format_validation_passes_with_tracked_summary(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object()))
    monkeypatch.setattr(
        "api.services.run_executor.validate_format",
        lambda client, bucket, key, fmt, expected_schema: SimpleNamespace(parsed=True, schema_ok=True),
    )

    result = ex._execute_s3_format_validation(JobDefinition(
        name="orders_schema",
        job_type="s3_format_validation",
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv", "expected_schema": {"id": "int"}},
    ))

    assert result.status == TestStatus.PASSED
    assert result.matched_count == 1
    assert result.value_mismatch_count == 0
    assert result.mismatch_summary["metrics"]["parsed"] is True
    assert result.mismatch_summary["metrics"]["schema_ok"] is True
    assert result.mismatch_summary["metrics"]["by_type"] == {}
    assert result.mismatch_summary["by_type"] == {}
    assert result.executed_at is not None
    assert result.duration_seconds > 0


def test_execute_s3_format_validation_generic_exception_is_error(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object()))

    def explode(client, bucket, key, fmt, expected_schema):
        raise RuntimeError("parser unavailable")

    monkeypatch.setattr("api.services.run_executor.validate_format", explode)

    result = ex._execute_s3_format_validation(JobDefinition(
        name="orders_schema",
        job_type="s3_format_validation",
        params={"config_id": 1, "bucket": "b", "key": "orders.csv", "fmt": "csv"},
    ))

    assert result.status == TestStatus.ERROR
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].mismatch_type == "s3_error"
    assert result.mismatch_summary["by_type"] == {"s3_error": 1}
    assert result.mismatch_summary["metrics"]["by_type"] == {"s3_error": 1}
    assert result.mismatch_summary["metrics"]["error"] == "parser unavailable"
    assert result.executed_at is not None
    assert result.duration_seconds > 0


def test_execute_s3_partition_check_fails_column_and_count(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object()))
    monkeypatch.setattr("api.services.run_executor.discover_partitions", lambda client, bucket, prefix: SimpleNamespace(
        columns=["dt"],
        entries=[SimpleNamespace(object_count=1)],
    ))

    result = ex._execute_s3_partition_check(JobDefinition(
        name="orders_partitions",
        job_type="s3_partition_check",
        params={"config_id": 1, "bucket": "b", "prefix": "orders/", "expected_columns": ["dt", "region"], "min_partitions": 2},
    ))

    assert result.status == TestStatus.FAILED
    assert {m.mismatch_type for m in result.mismatches} == {"partition_columns_mismatch", "partition_count_below_min"}
    assert result.mismatch_summary["by_type"] == {"partition_columns_mismatch": 1, "partition_count_below_min": 1}
    assert result.mismatch_summary["metrics"]["by_type"] == {"partition_columns_mismatch": 1, "partition_count_below_min": 1}
    assert result.mismatch_summary["metrics"]["partition_count"] == 1
    assert result.matched_count == 0
    assert result.executed_at is not None
    assert result.duration_seconds > 0


def test_execute_s3_partition_check_passes_with_tracked_summary(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object()))
    monkeypatch.setattr("api.services.run_executor.discover_partitions", lambda client, bucket, prefix: SimpleNamespace(
        columns=["dt"],
        entries=[SimpleNamespace(object_count=2), SimpleNamespace(object_count=3)],
    ))

    result = ex._execute_s3_partition_check(JobDefinition(
        name="orders_partitions",
        job_type="s3_partition_check",
        params={"config_id": 1, "bucket": "b", "prefix": "orders/", "expected_columns": ["dt"], "min_partitions": 2},
    ))

    assert result.status == TestStatus.PASSED
    assert result.source_row_count == 2
    assert result.target_row_count == 2
    assert result.matched_count == 1
    assert result.mismatch_summary["metrics"]["partition_count"] == 2
    assert result.mismatch_summary["metrics"]["object_count"] == 5
    assert result.mismatch_summary["metrics"]["by_type"] == {}
    assert result.mismatch_summary["by_type"] == {}
    assert result.executed_at is not None
    assert result.duration_seconds > 0


def test_execute_s3_partition_check_generic_exception_is_error(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsS3Runtime", lambda repo: SimpleNamespace(client=lambda config_id: object()))

    def explode(client, bucket, prefix):
        raise RuntimeError("list failed")

    monkeypatch.setattr("api.services.run_executor.discover_partitions", explode)

    result = ex._execute_s3_partition_check(JobDefinition(
        name="orders_partitions",
        job_type="s3_partition_check",
        params={"config_id": 1, "bucket": "b", "prefix": "orders/"},
    ))

    assert result.status == TestStatus.ERROR
    assert result.value_mismatch_count == 1
    assert result.mismatches[0].mismatch_type == "s3_error"
    assert result.mismatch_summary["by_type"] == {"s3_error": 1}
    assert result.mismatch_summary["metrics"]["by_type"] == {"s3_error": 1}
    assert result.mismatch_summary["metrics"]["error"] == "list failed"
    assert result.executed_at is not None
    assert result.duration_seconds > 0
