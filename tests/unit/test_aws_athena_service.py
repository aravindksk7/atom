from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.services.aws_athena_runtime import AwsAthenaRuntime
from api.services.aws_athena_service import AthenaQueryFailedError, AwsAthenaService, compute_dq_metrics
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository


class FakeAthenaClient:
    def __init__(self, terminal_state: str = "SUCCEEDED", states: list[str] | None = None) -> None:
        self.terminal_state = terminal_state
        self.states = states
        self.started = None
        self.status_calls = 0
        self.result_max_results: list[int] = []
        self.result_next_tokens: list[str | None] = []
        self.result_pages: list[dict] | None = None

    def start_query_execution(self, **kwargs):
        self.started = kwargs
        return {"QueryExecutionId": "qid-1"}

    def get_query_execution(self, QueryExecutionId: str):
        self.status_calls += 1
        if self.states:
            state = self.states[min(self.status_calls - 1, len(self.states) - 1)]
        else:
            state = self.terminal_state
        return {
            "QueryExecution": {
                "QueryExecutionId": QueryExecutionId,
                "Status": {"State": state, "StateChangeReason": "done"},
                "Statistics": {"EngineExecutionTimeInMillis": 42, "DataScannedInBytes": 1024},
            }
        }

    def get_query_results(self, QueryExecutionId: str, MaxResults: int = 100, NextToken: str | None = None):
        self.result_max_results.append(MaxResults)
        self.result_next_tokens.append(NextToken)
        if self.result_pages is not None:
            page_idx = int(NextToken or "0")
            return self.result_pages[page_idx]
        return {
            "ResultSet": {
                "Rows": [
                    {"Data": [{"VarCharValue": "id"}, {"VarCharValue": "amount"}, {"VarCharValue": "region"}]},
                    {"Data": [{"VarCharValue": "1"}, {"VarCharValue": "10.5"}, {"VarCharValue": "east"}]},
                    {"Data": [{"VarCharValue": "2"}, {}, {"VarCharValue": "west"}]},
                ]
            }
        }


@pytest.fixture
def config_repo():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield ConfigRepository(db)


def test_athena_runtime_resolves_named_config(config_repo):
    cfg = config_repo.create("aws-dev", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    runtime = AwsAthenaRuntime(config_repo)
    assert runtime.config_id("aws-dev") == cfg.id
    assert runtime.env("aws-dev").name == "dev"


def test_athena_runtime_missing_config_maps_to_404(config_repo):
    with pytest.raises(HTTPException) as err:
        AwsAthenaRuntime(config_repo).env(999)
    assert err.value.status_code == 404


def test_compute_dq_metrics_counts_rows_nulls_distincts_and_numeric_stats():
    metrics = compute_dq_metrics([
        {"id": "1", "amount": "10.5", "region": "east"},
        {"id": "2", "amount": "", "region": "west"},
    ])
    assert metrics["row_count"] == 2
    assert metrics["columns"] == ["id", "amount", "region"]
    assert metrics["null_counts"]["amount"] == 1
    assert metrics["distinct_counts"]["region"] == 2
    assert metrics["numeric"]["id"] == {"min": 1.0, "max": 2.0, "avg": 1.5}
    assert metrics["numeric"]["amount"] == {"min": 10.5, "max": 10.5, "avg": 10.5}


def test_athena_service_starts_statuses_and_results(config_repo):
    cfg = config_repo.create("aws", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    fake = FakeAthenaClient()
    service = AwsAthenaService(config_repo)
    service._athena_client_override = fake
    started = service.start_query(cfg.id, "curated", "select * from orders", "s3://out/", "primary")
    assert started.query_execution_id == "qid-1"
    assert fake.started["QueryExecutionContext"] == {"Database": "curated"}
    assert fake.started["ResultConfiguration"] == {"OutputLocation": "s3://out/"}
    status = service.get_query_status(cfg.id, "qid-1")
    assert status.state in {"RUNNING", "SUCCEEDED"}
    results = service.get_query_results(cfg.id, "qid-1")
    assert results.columns == ["id", "amount", "region"]
    assert results.rows[1]["amount"] is None
    assert fake.result_max_results == [101]


def test_athena_service_run_query_polls_running_to_succeeded(config_repo):
    cfg = config_repo.create("aws", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    fake = FakeAthenaClient(states=["RUNNING", "SUCCEEDED"])
    service = AwsAthenaService(config_repo)
    service._athena_client_override = fake

    run = service.run_query(cfg.id, "curated", "select * from orders", "s3://out/", poll_interval_seconds=0, max_attempts=3)

    assert run.status.state == "SUCCEEDED"
    assert fake.status_calls == 2
    assert run.dq_metrics["row_count"] == 2


def test_athena_service_run_query_failed_state_raises(config_repo):
    cfg = config_repo.create("aws", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    service = AwsAthenaService(config_repo)
    service._athena_client_override = FakeAthenaClient("FAILED")
    with pytest.raises(AthenaQueryFailedError) as err:
        service.run_query(cfg.id, "curated", "select 1", "s3://out/", max_attempts=1)
    assert "FAILED" in str(err.value)
    assert err.value.status.state == "FAILED"


def test_athena_service_run_query_cancelled_state_raises_structured_error(config_repo):
    cfg = config_repo.create("aws", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    service = AwsAthenaService(config_repo)
    service._athena_client_override = FakeAthenaClient("CANCELLED")

    with pytest.raises(AthenaQueryFailedError) as err:
        service.run_query(cfg.id, "curated", "select 1", "s3://out/", max_attempts=1)

    assert "CANCELLED" in str(err.value)
    assert err.value.status.state == "CANCELLED"


def test_athena_service_run_query_times_out_without_sleep_after_final_attempt(config_repo, monkeypatch):
    cfg = config_repo.create("aws", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    service = AwsAthenaService(config_repo)
    service._athena_client_override = FakeAthenaClient(states=["RUNNING", "RUNNING"])
    sleeps: list[float] = []
    monkeypatch.setattr("api.services.aws_athena_service.time.sleep", sleeps.append)

    with pytest.raises(TimeoutError):
        service.run_query(cfg.id, "curated", "select 1", "s3://out/", poll_interval_seconds=0.01, max_attempts=2)

    assert sleeps == [0.01]


def test_athena_service_get_query_results_clamps_api_max_results_and_limits_rows(config_repo):
    cfg = config_repo.create("aws", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    fake = FakeAthenaClient()
    fake.result_pages = [
        {
            "ResultSet": {
                "Rows": [{"Data": [{"VarCharValue": "id"}]}]
                + [{"Data": [{"VarCharValue": str(idx)}]} for idx in range(999)]
            },
            "NextToken": "1",
        },
        {
            "ResultSet": {"Rows": [{"Data": [{"VarCharValue": str(idx)}]} for idx in range(999, 1201)]},
        },
    ]
    service = AwsAthenaService(config_repo)
    service._athena_client_override = fake

    results = service.get_query_results(cfg.id, "qid-1", max_rows=1200)

    assert fake.result_max_results == [1000, 201]
    assert fake.result_next_tokens == [None, "1"]
    assert len(results.rows) == 1200
    assert results.rows[-1] == {"id": "1199"}


def test_athena_service_get_query_results_handles_sparse_result_cells(config_repo):
    cfg = config_repo.create("aws", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    service = AwsAthenaService(config_repo)
    service._athena_client_override = FakeAthenaClient()

    results = service.get_query_results(cfg.id, "qid-1")

    assert results.rows[1] == {"id": "2", "amount": None, "region": "west"}
