"""Tests for freshness job executor."""
import pandas as pd
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from api.schemas import JobDefinition, RunSettings
from etl_framework.runner.state import TestStatus


def _make_executor():
    from api.services.run_executor import RunExecutor
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    executor = object.__new__(RunExecutor)
    executor._db = db
    executor._run_id = "test-run"
    executor._source_env = "dev"
    executor._target_env = "prod"
    executor._settings = RunSettings()
    executor._config_snapshot = {}
    return executor


def _freshness_job(max_age_hours=24, ts_col="ts"):
    return JobDefinition.model_validate({
        "name": "orders_freshness",
        "job_type": "freshness",
        "query": "SELECT MAX(created_at) as ts FROM orders",
        "params": {"timestamp_column": ts_col, "max_age_hours": max_age_hours},
    })


def test_freshness_passes_when_data_is_recent():
    recent_ts = datetime.now(timezone.utc) - timedelta(hours=1)
    mock_engine = MagicMock()
    mock_engine._env = MagicMock(name="src")
    mock_engine.execute_query.return_value = pd.DataFrame({"ts": [recent_ts]})

    executor = _make_executor()
    result = executor._execute_freshness(_freshness_job(), mock_engine)
    assert result.status == TestStatus.PASSED


def test_freshness_fails_when_data_is_stale():
    stale_ts = datetime.now(timezone.utc) - timedelta(hours=48)
    mock_engine = MagicMock()
    mock_engine._env = MagicMock(name="src")
    mock_engine.execute_query.return_value = pd.DataFrame({"ts": [stale_ts]})

    executor = _make_executor()
    result = executor._execute_freshness(_freshness_job(max_age_hours=24), mock_engine)
    assert result.status == TestStatus.FAILED
    assert len(result.mismatches) == 1


def test_freshness_passes_in_simulation_mode_empty_df():
    mock_engine = MagicMock()
    mock_engine._env = MagicMock(name="src")
    mock_engine.execute_query.return_value = pd.DataFrame()
    executor = _make_executor()
    result = executor._execute_freshness(_freshness_job(), mock_engine)
    assert result.status == TestStatus.PASSED
