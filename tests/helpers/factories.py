from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from api.schemas import JobDefinition, RunSettings
from etl_framework.config.models import EnvironmentConfig
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.runner.state import TestStatus


def make_job_definition(**overrides: Any) -> JobDefinition:
    data = {
        "name": "orders_reconciliation",
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
    }
    data.update(overrides)
    return JobDefinition(**data)


def make_run_settings(**overrides: Any) -> RunSettings:
    return RunSettings(**overrides)


def make_mismatch_record(**overrides: Any) -> MismatchRecord:
    data = {
        "key_values": {"id": 1},
        "column_name": "amount",
        "source_value": 10,
        "target_value": 11,
        "mismatch_type": "value_diff",
    }
    data.update(overrides)
    return MismatchRecord(**data)


def make_reconciliation_result(**overrides: Any) -> ReconciliationResult:
    data = {
        "query_name": "orders",
        "source_env": "dev",
        "target_env": "prod",
        "source_row_count": 1,
        "target_row_count": 1,
        "matched_count": 1,
        "missing_in_target_count": 0,
        "missing_in_source_count": 0,
        "value_mismatch_count": 0,
        "mismatches": [],
        "status": TestStatus.PASSED,
        "executed_at": datetime.now(timezone.utc),
        "duration_seconds": 0.1,
    }
    data.update(overrides)
    return ReconciliationResult(**data)


def make_environment_config(**overrides: Any) -> EnvironmentConfig:
    data = {"name": "dev", "db_host": "localhost", "db_password": ""}
    data.update(overrides)
    return EnvironmentConfig(**data)


def make_source_target_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    return (
        pd.DataFrame({"id": [1, 2], "amount": [10.0, 20.0]}),
        pd.DataFrame({"id": [1, 2], "amount": [10.0, 21.0]}),
    )
