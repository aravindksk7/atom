from __future__ import annotations
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.reconciliation.models import ReconciliationResult
from etl_framework.runner.state import TestStatus


def _executor(config_snapshot: dict, use_live_connections: bool = True) -> RunExecutor:
    return RunExecutor(
        db=MagicMock(),
        run_id="run-api-1",
        source_env="src",
        target_env="tgt",
        job_sequence=[],
        run_settings=RunSettings(use_live_connections=use_live_connections),
        config_snapshot=config_snapshot,
    )


def _job(**overrides) -> JobDefinition:
    base = dict(
        name="api_orders_check",
        job_type="api_reconciliation",
        query="",
        key_columns=["id"],
        params={"source_api_endpoint": "orders_a", "target_api_endpoint": "orders_b"},
    )
    base.update(overrides)
    return JobDefinition(**base)


def test_build_case_api_reconciliation_flags_row_mismatch():
    snapshot = {
        "api_endpoints": {
            "orders_a": {"base_url": "https://a.example.com/orders"},
            "orders_b": {"base_url": "https://b.example.com/orders"},
        }
    }
    ex = _executor(snapshot)
    job = _job()

    df_a = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    df_b = pd.DataFrame({"id": [1, 2], "amount": [10, 25]})  # row 2 mismatches

    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        MockClient.return_value.fetch_dataframe.side_effect = [df_a, df_b]
        case_fn = ex._build_case(job)
        result = case_fn()

    assert isinstance(result, ReconciliationResult)
    assert result.source_row_count == 2
    assert result.status == TestStatus.FAILED
    assert result.value_mismatch_count == 1


def test_build_case_api_reconciliation_passes_when_identical():
    snapshot = {
        "api_endpoints": {
            "orders_a": {"base_url": "https://a.example.com/orders"},
            "orders_b": {"base_url": "https://b.example.com/orders"},
        }
    }
    ex = _executor(snapshot)
    job = _job()

    df = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})

    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        MockClient.return_value.fetch_dataframe.side_effect = [df.copy(), df.copy()]
        case_fn = ex._build_case(job)
        result = case_fn()

    # Proves the API path actually ran (not the simulated DB fallback, which
    # never touches APIEndpointClient and would trivially also report PASSED).
    assert MockClient.return_value.fetch_dataframe.call_count == 2
    assert result.status == TestStatus.PASSED
    assert result.source_row_count == 2


def test_build_case_api_reconciliation_skips_without_target():
    snapshot = {"api_endpoints": {"orders_a": {"base_url": "https://a.example.com/orders"}}}
    ex = _executor(snapshot)
    job = _job(params={"source_api_endpoint": "orders_a"})

    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        case_fn = ex._build_case(job)
        result = case_fn()

    MockClient.assert_not_called()
    assert result.status == TestStatus.SKIPPED


def test_build_case_api_reconciliation_not_used_without_live_connections():
    ex = _executor({}, use_live_connections=False)
    job = _job()
    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        case_fn = ex._build_case(job)
        result = case_fn()
    # The live-endpoint path is never taken, so the client is never constructed
    MockClient.assert_not_called()
    assert isinstance(result, ReconciliationResult)
