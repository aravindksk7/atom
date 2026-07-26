from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
from etl_framework.runner.state import TestStatus


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def executor(db_session):
    return RunExecutor(db=db_session, run_id="run-1", source_env="qa", target_env="prod", job_sequence=[], run_settings=RunSettings(use_live_connections=True))


def job():
    return JobDefinition(name="glue_orders", job_type="aws_glue_catalog_compare", params={"config_id": 1, "source_database": "raw", "source_table": "orders", "target_database": "curated", "target_table": "orders"})


def test_execute_glue_catalog_compare_passes(monkeypatch, db_session):
    ex = executor(db_session)
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: SimpleNamespace(compare_tables=lambda *args, **kwargs: SimpleNamespace(match=True, source={"columns": [{"name": "id"}], "partition_keys": []}, target={"columns": [{"name": "id"}], "partition_keys": []}, diff={"missing_columns": [], "extra_columns": [], "type_mismatches": [], "partition_key_mismatches": [], "location_mismatch": None, "format_mismatch": None})))
    result = ex._execute_aws_glue_catalog_compare(job())
    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["catalog_diff"]["missing_columns"] == []


def test_execute_glue_catalog_compare_fails_on_drift(monkeypatch, db_session):
    ex = executor(db_session)
    diff = {"missing_columns": ["amount"], "extra_columns": ["status"], "type_mismatches": [{"column": "id", "expected_type": "int64", "actual_type": "string"}], "partition_key_mismatches": [{"source": [], "target": [{"name": "dt", "type": "string"}]}], "location_mismatch": {"source": "s3://raw", "target": "s3://curated"}, "format_mismatch": {"input_format": {"source": "csv", "target": "parquet"}}}
    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: SimpleNamespace(compare_tables=lambda *args, **kwargs: SimpleNamespace(match=False, source={"columns": [{"name": "id"}, {"name": "amount"}], "partition_keys": []}, target={"columns": [{"name": "id"}, {"name": "status"}], "partition_keys": [{"name": "dt"}]}, diff=diff)))
    result = ex._execute_aws_glue_catalog_compare(job())
    assert result.status == TestStatus.FAILED
    assert {m.mismatch_type for m in result.mismatches} == {"missing_columns", "extra_columns", "type_mismatch", "partition_key_mismatch", "location_mismatch", "format_mismatch"}
    format_mismatch = next(m for m in result.mismatches if m.mismatch_type == "format_mismatch")
    assert format_mismatch.source_value == diff["format_mismatch"]
    assert format_mismatch.target_value is None
    assert result.mismatch_summary["catalog_diff"] == diff


def test_execute_glue_catalog_compare_errors(monkeypatch, db_session):
    ex = executor(db_session)

    def fail(*args, **kwargs):
        raise RuntimeError("glue unavailable")

    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: SimpleNamespace(compare_tables=fail))
    result = ex._execute_aws_glue_catalog_compare(job())
    assert result.status == TestStatus.ERROR
    assert result.mismatches[0].mismatch_type == "glue_error"


def test_execute_glue_catalog_compare_preserves_http_exception_detail(monkeypatch, db_session):
    ex = executor(db_session)

    def fail(*args, **kwargs):
        raise HTTPException(status_code=404, detail="Config not found")

    monkeypatch.setattr("api.services.run_executor.AwsGlueService", lambda repo: SimpleNamespace(compare_tables=fail))
    result = ex._execute_aws_glue_catalog_compare(job())
    assert result.status == TestStatus.ERROR
    assert result.mismatches[0].target_value == "Config not found"
    assert result.mismatch_summary["metrics"]["error"] == "Config not found"
