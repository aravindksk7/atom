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


def test_aws_tab_contains_glue_job_controls():
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    assert 'data-testid="aws-glue-job-input"' in html
    assert 'data-testid="aws-glue-load-jobs-btn"' in html
    assert 'data-testid="aws-glue-job-select"' in html
    assert 'data-testid="aws-glue-job-run-name-input"' in html
    assert 'data-testid="aws-glue-job-args-input"' in html
    assert 'data-testid="aws-glue-job-expected-status-select"' in html
    assert 'data-testid="aws-glue-job-poll-interval-input"' in html
    assert 'data-testid="aws-glue-job-max-attempts-input"' in html
    assert 'data-testid="aws-glue-job-run-btn"' in html
    assert 'data-testid="aws-glue-create-job-run-btn"' in html
    assert 'data-testid="aws-glue-job-run-result"' in html
    assert 'data-testid="aws-glue-job-loading"' in html
    assert 'data-testid="aws-glue-job-error"' in html


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


def _div_depths(lines: list[str]) -> tuple[int, dict[str, int]]:
    """Return (final depth, {service: depth after its panel opening tag})."""
    depth = 0
    panel_depths: dict[str, int] = {}
    for line in lines:
        depth += line.count("<div") - line.count("</div>")
        for service in ("s3", "glue", "athena", "airflow"):
            if f"x-show=\"awsService === '{service}'\"" in line:
                panel_depths[service] = depth
    return depth, panel_depths


def test_aws_tab_partial_has_balanced_divs():
    """A stray </div> silently closes the AWS tab container and hides later panels."""
    lines = Path("frontend/partials/tab-aws.html").read_text(encoding="utf-8").splitlines()
    final_depth, panel_depths = _div_depths(lines)
    assert final_depth == 0, f"tab-aws.html has unbalanced <div> tags (final depth {final_depth})"
    assert set(panel_depths) == {"s3", "glue", "athena", "airflow"}
    assert len(set(panel_depths.values())) == 1, (
        f"AWS service panels must be siblings at the same nesting depth, got {panel_depths}"
    )


def test_all_aws_service_panels_render_inside_the_aws_tab():
    """Athena/Airflow panels must live inside the currentView === 'aws' container."""
    lines = Path("frontend/index.html").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if "currentView === 'aws'" in line)

    depth = 0
    panel_depths: dict[str, int] = {}
    closed_at: int | None = None
    for offset, line in enumerate(lines[start:]):
        depth += line.count("<div") - line.count("</div>")
        for service in ("s3", "glue", "athena", "airflow"):
            if f"x-show=\"awsService === '{service}'\"" in line:
                panel_depths[service] = start + offset
        if depth == 0 and offset > 0:
            closed_at = start + offset
            break

    assert closed_at is not None, "AWS tab container is never closed"
    for service in ("s3", "glue", "athena", "airflow"):
        assert service in panel_depths, f"{service} panel missing from index.html"
        assert panel_depths[service] < closed_at, (
            f"{service} panel at line {panel_depths[service] + 1} renders outside the AWS tab "
            f"container, which closes at line {closed_at + 1}"
        )


def _panel_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Map each AWS service to the [start, end) line range of its x-show panel."""
    starts: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        for service in ("s3", "glue", "athena", "airflow"):
            if f"x-show=\"awsService === '{service}'\"" in line:
                starts.append((service, i))
    starts.sort(key=lambda pair: pair[1])

    spans: dict[str, tuple[int, int]] = {}
    for idx, (service, start) in enumerate(starts):
        end = starts[idx + 1][1] if idx + 1 < len(starts) else len(lines)
        spans[service] = (start, end)
    return spans


# Each control must live in the panel it belongs to. Substring-only assertions pass
# even when markup is nested in the wrong panel or falls outside the AWS tab entirely,
# which is exactly how the Athena and Airflow panels once shipped unreachable.
AWS_PANEL_CONTROLS = {
    "athena": [
        "aws-athena-run-query-btn",
        "aws-athena-create-job-btn",
        "aws-athena-add-assertion-btn",
        "aws-athena-assertion-operator",
        "aws-athena-result",
    ],
    "airflow": [
        "aws-airflow-load-dags-btn",
        "aws-airflow-trigger-btn",
        "aws-airflow-run-btn",
        "aws-airflow-create-job-btn",
        "aws-airflow-result",
    ],
    "glue": [
        "aws-glue-compare-btn",
        "aws-glue-load-jobs-btn",
        "aws-glue-job-run-btn",
        "aws-glue-create-job-run-btn",
        "aws-glue-job-run-result",
    ],
}


@pytest.mark.parametrize("service", sorted(AWS_PANEL_CONTROLS))
def test_aws_controls_live_in_their_own_panel(service):
    lines = Path("frontend/index.html").read_text(encoding="utf-8").splitlines()
    spans = _panel_spans(lines)
    assert service in spans, f"{service} panel missing from index.html"
    start, end = spans[service]

    for testid in AWS_PANEL_CONTROLS[service]:
        needle = f'data-testid="{testid}"'
        hits = [i for i, line in enumerate(lines) if needle in line]
        assert hits, f"{testid} not present in index.html at all"
        assert any(start <= hit < end for hit in hits), (
            f"{testid} exists but renders outside the '{service}' panel "
            f"(panel spans lines {start + 1}-{end}); found at line(s) "
            f"{[h + 1 for h in hits]}"
        )
