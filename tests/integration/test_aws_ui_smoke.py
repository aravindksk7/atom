"""Smoke: /api/aws/s3 routes are mounted and auth-guarded, and the AWS tab is served."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"},
                    raise_server_exceptions=False) as c:
        yield c


def test_aws_s3_route_is_mounted(client):
    r = client.post("/api/aws/s3/metadata", json={"config_id": 999, "bucket": "b", "key": "k"})
    assert r.status_code != 404 or r.json().get("detail") != "Not Found"


def test_openapi_lists_aws_routes(client):
    spec = client.get("/openapi.json").json()
    assert "/api/aws/s3/metadata" in spec["paths"]
    assert "/api/aws/s3/validate-format" in spec["paths"]


def test_aws_tab_contains_s3_job_creation_controls():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "data-testid=\"aws-create-row-count-job-btn\"" in html
    assert "data-testid=\"aws-create-format-validation-job-btn\"" in html
    assert "data-testid=\"aws-create-partition-check-job-btn\"" in html
    assert "data-testid=\"aws-job-name-input\"" in html
    assert "data-testid=\"aws-min-rows-input\"" in html
    assert "data-testid=\"aws-expected-columns-input\"" in html
    assert "Type mismatches:" in html


def test_aws_tab_contains_glue_catalog_controls():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "data-testid=\"aws-service-glue\"" in html
    assert "data-testid=\"aws-service-athena\"" in html
    assert "data-testid=\"aws-service-airflow\" @click=\"awsService='airflow'\"" in html
    assert "data-testid=\"aws-glue-source-database-input\"" in html
    assert "data-testid=\"aws-glue-source-table-input\"" in html
    assert "data-testid=\"aws-glue-target-database-input\"" in html
    assert "data-testid=\"aws-glue-target-table-input\"" in html
    assert "data-testid=\"aws-glue-job-name-input\"" in html
    assert "data-testid=\"aws-glue-compare-location-checkbox\"" in html
    assert "data-testid=\"aws-glue-compare-formats-checkbox\"" in html
    assert "data-testid=\"aws-glue-compare-partitions-checkbox\"" in html
    assert "data-testid=\"aws-glue-compare-btn\"" in html
    assert "data-testid=\"aws-glue-create-job-btn\"" in html
    assert "data-testid=\"aws-glue-error\"" in html
    assert "data-testid=\"aws-glue-result\"" in html
    assert ":disabled=\"awsGlueLoading || !awsConfigId || !awsGlueSourceDatabase || !awsGlueSourceTable || !awsGlueTargetDatabase || !awsGlueTargetTable\"" in html


def test_aws_tab_contains_athena_query_controls():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "data-testid=\"aws-service-athena\"" in html
    assert "data-testid=\"aws-service-airflow\" @click=\"awsService='airflow'\"" in html
    assert "data-testid=\"aws-athena-database-input\"" in html
    assert "data-testid=\"aws-athena-workgroup-input\"" in html
    assert "data-testid=\"aws-athena-query-input\"" in html
    assert "data-testid=\"aws-athena-output-location-input\"" in html
    assert "data-testid=\"aws-athena-max-rows-input\"" in html
    assert "data-testid=\"aws-athena-job-name-input\"" in html
    assert "data-testid=\"aws-athena-min-rows-input\"" in html
    assert "data-testid=\"aws-athena-max-rows-assert-input\"" in html
    assert "data-testid=\"aws-athena-assertion-path\"" in html
    assert "data-testid=\"aws-athena-assertion-operator\"" in html
    assert "data-testid=\"aws-athena-assertion-min\"" in html
    assert "data-testid=\"aws-athena-assertion-max\"" in html
    assert "data-testid=\"aws-athena-assertion-value\"" in html
    assert "data-testid=\"aws-athena-assertion-tolerance\"" in html
    assert "data-testid=\"aws-athena-remove-assertion-btn\"" in html
    assert "data-testid=\"aws-athena-add-assertion-btn\"" in html
    assert "data-testid=\"aws-athena-run-query-btn\"" in html
    assert "data-testid=\"aws-athena-create-job-btn\"" in html
    assert "data-testid=\"aws-athena-loading\"" in html
    assert "data-testid=\"aws-athena-error\"" in html
    assert "data-testid=\"aws-athena-result\"" in html
    assert "awsAthenaResult.status.state" in html
    assert "awsAthenaResult.results.rows" in html
    assert "awsAthenaResult.dq_metrics" in html
    assert "Status:" in html
    assert "Rows:" in html
    assert "Execution ms:" in html
    assert "JSON.stringify(awsAthenaResult.dq_metrics, null, 2)" in html
    assert "JSON.stringify(awsAthenaResult.results.rows.slice(0, 10), null, 2)" in html
    assert ":disabled=\"awsAthenaLoading || !awsConfigId || !awsAthenaQuery || !awsAthenaOutputLocation\"" in html


def test_aws_tab_contains_airflow_controls():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert "data-testid=\"aws-service-airflow\" @click=\"awsService='airflow'\"" in html
    assert "data-testid=\"aws-airflow-run-btn\"" in html
    assert "data-testid=\"aws-airflow-create-job-btn\"" in html
    assert "data-testid=\"aws-airflow-load-dags-btn\"" in html
    assert "data-testid=\"aws-airflow-dag-input\"" in html
    assert "data-testid=\"aws-airflow-dag-select\"" in html
    assert "data-testid=\"aws-airflow-conf-input\"" in html
    assert "data-testid=\"aws-airflow-job-name-input\"" in html
    assert "data-testid=\"aws-airflow-poll-interval-input\"" in html
    assert "data-testid=\"aws-airflow-max-attempts-input\"" in html
    assert "data-testid=\"aws-airflow-loading\"" in html
    assert "data-testid=\"aws-airflow-error\"" in html
    assert "data-testid=\"aws-airflow-result\"" in html
    assert "awsAirflowResult.task_instances" in html
    assert "Run DAG to Completion" in html
