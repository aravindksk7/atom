"""Tests for cross_job_assertion executor."""
from unittest.mock import MagicMock
from api.schemas import JobDefinition, RunSettings
from etl_framework.runner.state import TestStatus
from etl_framework.repository.models import TestResult


def _make_executor(db):
    from api.services.run_executor import RunExecutor
    executor = object.__new__(RunExecutor)
    executor._db = db
    executor._run_id = "run-1"
    executor._source_env = "dev"
    executor._target_env = "prod"
    executor._settings = RunSettings()
    executor._config_snapshot = {}
    return executor


def _cja_job(source_metric="count", target_metric="count", tolerance=0.0):
    return JobDefinition.model_validate({
        "name": "revenue_check",
        "job_type": "cross_job_assertion",
        "params": {
            "source_job": "orders_profile",
            "source_metric": source_metric,
            "source_column": "amount",
            "target_job": "payments_profile",
            "target_metric": target_metric,
            "target_column": "total",
            "tolerance": tolerance,
            "tolerance_type": "absolute",
        },
    })


def test_cross_job_count_passes():
    db = MagicMock()
    src_result = MagicMock(spec=TestResult)
    src_result.source_row_count = 100
    tgt_result = MagicMock(spec=TestResult)
    tgt_result.source_row_count = 100
    db.query.return_value.filter.return_value.first.side_effect = [src_result, tgt_result]

    executor = _make_executor(db)
    result = executor._execute_cross_job(_cja_job("count", "count", 0))
    assert result.status == TestStatus.PASSED


def test_cross_job_count_fails():
    db = MagicMock()
    src_result = MagicMock(spec=TestResult)
    src_result.source_row_count = 100
    tgt_result = MagicMock(spec=TestResult)
    tgt_result.source_row_count = 80  # 20 difference > tolerance of 5

    db.query.return_value.filter.return_value.first.side_effect = [src_result, tgt_result]

    executor = _make_executor(db)
    result = executor._execute_cross_job(_cja_job("count", "count", 5))
    assert result.status == TestStatus.FAILED


def test_cross_job_skips_if_upstream_missing():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    executor = _make_executor(db)
    result = executor._execute_cross_job(_cja_job())
    assert result.status == TestStatus.SKIPPED
