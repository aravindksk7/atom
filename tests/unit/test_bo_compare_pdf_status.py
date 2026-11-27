"""Tests for 'as printed' status surfaces across RunStatusOut, webhooks, and email subjects."""
from __future__ import annotations

import threading
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

from api.routes.compare import _status_out
from api.routes.runs import _run_status_out
from api.schemas import RunSettings
from api.services import notifier as notifier_mod
from api.services.run_executor import RunExecutor
from api.services.run_label import run_compared_as


def _make_run(run_id: str, *, with_pdf: bool, config_snapshot: dict | None = None):
    ms = {"pdf_sources": {"a": {"file_name": "a.pdf"}, "b": {"file_name": "b.pdf"}}} if with_pdf else None
    result = types.SimpleNamespace(
        id=1,
        query_name="bo_comparison",
        status="PASSED",
        effective_status="PASSED",
        duration_seconds=0.5,
        source_row_count=10,
        target_row_count=10,
        value_mismatch_count=0,
        missing_in_target_count=0,
        missing_in_source_count=0,
        error_message=None,
        executed_at=datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc),
        source_file_name="a.pdf" if with_pdf else None,
        target_file_name="b.pdf" if with_pdf else None,
        override_reason=None,
        override_by=None,
        override_at=None,
        sample_rows=None,
        segment_summary=None,
        mismatch_summary=ms,
        mismatches=[],
        schema_diff=None,
        total_issues=0,
    )
    return types.SimpleNamespace(
        run_id=run_id,
        status="PASSED",
        started_at=datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 11, 27, 10, 1, tzinfo=timezone.utc),
        source_env="QA",
        target_env="PROD",
        config_snapshot=config_snapshot,
        run_type="bo_comparison" if with_pdf else "reconciliation",
        pair_id=None,
        selection_id=None,
        ci_context=None,
        restarted_from_run_id=None,
        passed=1,
        failed=0,
        slow=0,
        error=0,
        total_tests=1,
        results=[result],
    )


def test_run_status_out_marks_pdf_runs():
    pdf_run = _make_run("run-pdf-1", with_pdf=True)
    plain_run = _make_run("run-plain-1", with_pdf=False)

    assert run_compared_as(pdf_run) == "printed"
    assert run_compared_as(plain_run) is None

    assert _status_out(pdf_run).compared_as == "printed"
    assert _status_out(plain_run).compared_as is None
    assert _run_status_out(pdf_run).compared_as == "printed"
    assert _run_status_out(plain_run).compared_as is None


def test_webhook_payload_has_status_label(monkeypatch):
    captured: list[tuple[tuple, dict]] = []

    def fake_notify(*args, **kwargs):
        captured.append((args, kwargs))

    monkeypatch.setattr(notifier_mod, "notify", fake_notify)

    pdf_run = _make_run("run-pdf-webhook", with_pdf=True)
    db = MagicMock()
    executor = RunExecutor(
        db=db,
        run_id="run-pdf-webhook",
        source_env="QA",
        target_env="PROD",
        job_sequence=[],
        run_settings=RunSettings(),
    )
    executor._run_repo = MagicMock()
    executor._run_repo.get_run.return_value = pdf_run

    executor._fire_webhooks("PASSED", passed=1, failed=0, error=0)

    assert len(captured) == 1
    extra = captured[0][1]["extra"]
    assert extra["compared_as"] == "printed"
    assert extra["status_label"] == "PASSED (as printed)"


def test_default_email_subject_uses_status_label(monkeypatch):
    sent: list[tuple] = []
    done = threading.Event()

    def fake_send_email(recipients, subject, body, config):
        sent.append((recipients, subject, body, config))
        done.set()

    monkeypatch.setattr(notifier_mod, "_send_email", fake_send_email)
    monkeypatch.setattr(notifier_mod, "_resolve_smtp_config", lambda: {"host": "mail.local", "port": 25})

    hook = MagicMock()
    hook.id = 1
    hook.enabled = True
    hook.channel = "email"
    hook.url = "mailto:qa@example.com"
    hook.events = ["run.passed"]
    hook.subject_template = None
    hook.body_template = None

    notifier_mod.notify(
        "run-123",
        "PASSED",
        extra={"compared_as": "printed", "status_label": "PASSED (as printed)"},
        hooks=[hook],
    )
    assert done.wait(timeout=2.0)
    assert sent[0][1] == "ETL run PASSED (as printed): run-123"


def test_mixed_run_is_not_labelled_printed_as_a_whole():
    run = _make_run("run-mixed-1", with_pdf=True)
    sql_result = types.SimpleNamespace(**{**vars(run.results[0]), "mismatch_summary": None})
    run.results = [run.results[0], sql_result]
    assert run_compared_as(run) is None

    run.results = [run.results[0], run.results[0]]
    assert run_compared_as(run) == "printed"

    empty = _make_run("run-empty-1", with_pdf=False)
    empty.results = []
    assert run_compared_as(empty) is None
