"""Tests for new API schema types."""
from __future__ import annotations
import pytest
from pydantic import ValidationError


def test_run_settings_has_use_live_connections_default_false():
    from api.schemas import RunSettings
    s = RunSettings()
    assert s.use_live_connections is False


def test_run_settings_use_live_connections_can_be_true():
    from api.schemas import RunSettings
    s = RunSettings(use_live_connections=True)
    assert s.use_live_connections is True


def test_run_progress_out_defaults():
    from api.schemas import RunProgressOut
    p = RunProgressOut(run_id="abc", status="RUNNING")
    assert p.total_tests == 0
    assert p.completed_tests == 0
    assert p.percent_complete == 0
    assert p.current_job is None


def test_run_progress_out_percent_clamped():
    from api.schemas import RunProgressOut
    with pytest.raises(ValidationError):
        RunProgressOut(run_id="x", status="RUNNING", percent_complete=101)


def test_bo_doc_out_fields():
    from api.schemas import BODocOut
    d = BODocOut(id="101", name="Sales Report", folder="/Finance")
    assert d.id == "101"
    assert d.folder == "/Finance"


def test_bo_doc_out_folder_defaults_empty():
    from api.schemas import BODocOut
    d = BODocOut(id="1", name="Report")
    assert d.folder == ""


def test_bo_report_out_fields():
    from api.schemas import BOReportOut
    r = BOReportOut(id="2", name="Page 1", report_index=0)
    assert r.id == "2"
    assert r.report_index == 0


def test_adapter_test_out_ok():
    from api.schemas import AdapterTestOut
    a = AdapterTestOut(ok=True, message="Connected", latency_ms=42)
    assert a.ok is True
    assert a.latency_ms == 42


def test_adapter_test_out_failure():
    from api.schemas import AdapterTestOut
    a = AdapterTestOut(ok=False, message="Auth failed")
    assert a.ok is False
    assert a.latency_ms == 0


def test_automic_job_status_out_fields():
    from api.schemas import AutomicJobStatusOut
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    s = AutomicJobStatusOut(
        identifier="JOB_ETL",
        identifier_type="job_name",
        status="PASSED",
        environment="prod",
        checked_at=now,
    )
    assert s.status == "PASSED"
    assert s.environment == "prod"


def test_bo_test_request_requires_config_id():
    from api.schemas import BOTestRequest
    with pytest.raises(ValidationError):
        BOTestRequest()


def test_automic_lookup_request_defaults_to_job_name():
    from api.schemas import AutomicLookupRequest
    r = AutomicLookupRequest(config_id=1, identifier="MY_JOB")
    assert r.id_type == "job_name"


def test_bo_job_create_request_fields():
    from api.schemas import BOJobCreateRequest
    r = BOJobCreateRequest(
        name="my_bo_job",
        title="Sales Report",
        doc_id="101",
        report_id="1",
        key_columns=["region"],
    )
    assert r.format == "xlsx"
    assert r.key_columns == ["region"]


def test_automic_job_create_request_fields():
    from api.schemas import AutomicJobCreateRequest
    r = AutomicJobCreateRequest(name="nightly", job_name="ETL_NIGHTLY")
    assert r.job_name == "ETL_NIGHTLY"


def test_automic_job_create_request_accepts_run_id():
    from api.schemas import AutomicJobCreateRequest
    r = AutomicJobCreateRequest(name="nightly", run_id="RUN_42")
    assert r.run_id == "RUN_42"


def test_automic_job_create_request_requires_identifier():
    from api.schemas import AutomicJobCreateRequest
    with pytest.raises(ValidationError):
        AutomicJobCreateRequest(name="nightly")


# ---------------------------------------------------------------------------
# New DQRule types (Task 1)
# ---------------------------------------------------------------------------

def test_dq_rule_completeness_ratio():
    from api.schemas import DQRule
    r = DQRule.model_validate({"type": "completeness_ratio", "column": "amount", "min_value": 0.9})
    assert r.type == "completeness_ratio"
    assert r.min_value == 0.9


def test_dq_rule_column_percentile():
    from api.schemas import DQRule
    r = DQRule.model_validate({"type": "column_percentile", "column": "price", "percentile": 95, "max_value": 1000.0})
    assert r.percentile == 95


def test_dq_rule_cross_column_consistency():
    from api.schemas import DQRule
    r = DQRule.model_validate({"type": "cross_column_consistency", "column": "start_date", "column_b": "end_date", "operator": "<="})
    assert r.column_b == "end_date"
    assert r.operator == "<="


def test_dq_rule_referential_check():
    from api.schemas import DQRule
    r = DQRule.model_validate({"type": "referential_check", "column": "customer_id", "lookup_query": "SELECT id FROM customers"})
    assert r.lookup_query == "SELECT id FROM customers"


def test_dq_rule_column_type_check():
    from api.schemas import DQRule
    r = DQRule.model_validate({"type": "column_type_check", "column": "order_date", "expected_type": "date"})
    assert r.expected_type == "date"


# ---------------------------------------------------------------------------
# New JobDefinition job_types (Task 1)
# ---------------------------------------------------------------------------

def test_job_definition_freshness():
    from api.schemas import JobDefinition
    j = JobDefinition.model_validate({
        "name": "orders_freshness",
        "job_type": "freshness",
        "query": "SELECT MAX(created_at) as ts FROM orders",
        "params": {"timestamp_column": "ts", "max_age_hours": 24},
    })
    assert j.job_type == "freshness"


def test_job_definition_profile():
    from api.schemas import JobDefinition
    j = JobDefinition.model_validate({
        "name": "orders_profile",
        "job_type": "profile",
        "query": "SELECT * FROM orders",
        "params": {},
    })
    assert j.job_type == "profile"


def test_job_definition_schema_snapshot():
    from api.schemas import JobDefinition
    j = JobDefinition.model_validate({
        "name": "orders_schema",
        "job_type": "schema_snapshot",
        "query": "SELECT * FROM orders",
        "params": {"environment": "source"},
    })
    assert j.job_type == "schema_snapshot"


def test_job_definition_cross_job_assertion():
    from api.schemas import JobDefinition
    j = JobDefinition.model_validate({
        "name": "revenue_check",
        "job_type": "cross_job_assertion",
        "params": {
            "source_job": "orders_profile",
            "source_metric": "sum",
            "source_column": "amount",
            "target_job": "payments_profile",
            "target_metric": "sum",
            "target_column": "total",
        },
    })
    assert j.job_type == "cross_job_assertion"


def test_job_definition_accepts_dbt_artifact():
    from api.schemas import JobDefinition
    job = JobDefinition(
        name="dbt_orders",
        job_type="dbt_artifact",
        query="",
        key_columns=[],
        params={"run_results_path": "target/run_results.json"},
    )
    assert job.params["run_results_path"] == "target/run_results.json"


def test_job_definition_rejects_dbt_manifest_without_run_results():
    from api.schemas import JobDefinition
    with pytest.raises(ValidationError):
        JobDefinition(
            name="dbt_orders",
            job_type="dbt_artifact",
            query="",
            key_columns=[],
            params={"manifest_path": "target/manifest.json"},
        )
