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
