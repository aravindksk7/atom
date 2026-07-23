"""Tests for api.services.junit_export.render_junit_xml."""
from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from etl_framework.repository.models import TestResult, TestRun


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_run(db, run_id="run-junit-1", results=()):
    run = TestRun(
        run_id=run_id, status="FAILED", source_env="dev", target_env="qa",
        started_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 18, 10, 5, tzinfo=timezone.utc),
        total_tests=len(results),
    )
    db.add(run)
    for r in results:
        db.add(TestResult(run_id=run_id, **r))
    db.commit()
    db.refresh(run)
    return run


def _parse(xml_text: str) -> ET.Element:
    root = ET.fromstring(xml_text)
    assert root.tag == "testsuites"
    return root.find("testsuite")


def test_passing_run_renders_testcases_without_failure_nodes(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="orders_recon", status="PASSED", duration_seconds=12.4),
        dict(query_name="customer_feed", status="PASSED", duration_seconds=3.2),
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("name") == "atom-run-run-junit-1"
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "0"
    assert suite.get("errors") == "0"
    cases = suite.findall("testcase")
    assert [c.get("name") for c in cases] == ["orders_recon", "customer_feed"]
    assert cases[0].find("failure") is None
    assert cases[0].get("time") == "12.400"


def test_failed_result_gets_failure_node_with_mismatch_counts(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="customer_feed", status="FAILED", duration_seconds=3.2,
             value_mismatch_count=5, missing_in_target_count=1, missing_in_source_count=0),
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("failures") == "1"
    failure = suite.find("testcase").find("failure")
    assert failure is not None
    assert "value_mismatches=5" in failure.get("message")
    assert "missing_in_target=1" in failure.get("message")


def test_error_result_gets_error_node_with_message(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="broken_job", status="ERROR", duration_seconds=0.1,
             error_message="ORA-00942: table or view does not exist"),
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("errors") == "1"
    error = suite.find("testcase").find("error")
    assert error is not None
    assert "ORA-00942" in error.get("message")


def test_multi_file_result_adds_pair_rollup_to_failure_text(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(
            query_name="regional_sales_recon",
            status="FAILED",
            duration_seconds=0.1,
            value_mismatch_count=1,
            mismatch_summary={
                "pairs_total": 2,
                "pairs_passed": 1,
                "pairs_failed": 1,
                "pairs_errored": 0,
                "file_pairs": [
                    {"key": {"region": "east"}, "status": "PASSED", "source_files": ["sales_east.csv"], "target_files": ["financials_east.csv"], "value_mismatch_count": 0},
                    {"key": {"region": "west"}, "status": "FAILED", "source_files": ["sales_west.csv"], "target_files": ["financials_west.csv"], "value_mismatch_count": 1},
                ],
            },
        ),
    ])

    suite = _parse(render_junit_xml(run))
    failure = suite.find("testcase").find("failure")
    assert failure is not None
    assert "pairs: 1 passed, 1 failed, 0 errored" in (failure.text or "")
    assert "region=west" in (failure.text or "")
    assert "sales_west.csv -> financials_west.csv" in (failure.text or "")


def test_overridden_failure_counts_as_pass(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="agreed_gap", status="FAILED", duration_seconds=1.0,
             override_status="PASSED", override_reason="known gap"),
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("failures") == "0"
    assert suite.find("testcase").find("failure") is None


def test_empty_run_renders_empty_suite(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[])
    suite = _parse(render_junit_xml(run))
    assert suite.get("tests") == "0"
    assert suite.findall("testcase") == []


def test_timestamp_and_classname_present(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="orders_recon", status="PASSED", duration_seconds=1.0)
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("timestamp") == "2026-07-18T10:00:00+00:00"
    assert suite.find("testcase").get("classname") == "orders_recon"


def test_control_chars_in_error_message_are_sanitized():
    """error_message may contain raw control chars (e.g. from a DB driver
    exception). ElementTree does not sanitize XML-illegal chars, so they
    must be stripped before being written into an attribute/text node or
    the resulting document is not parseable XML."""
    from api.services.junit_export import render_junit_xml

    run = TestRun(
        run_id="run-junit-ctrl", status="FAILED", source_env="dev", target_env="qa",
        started_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        total_tests=1,
    )
    run.results = [
        TestResult(run_id="run-junit-ctrl", query_name="broken_job", status="ERROR",
                   duration_seconds=0.1, error_message="bad\x00\x1bchar"),
    ]
    xml_text = render_junit_xml(run)
    root = ET.fromstring(xml_text)  # raises ParseError if XML is invalid
    suite = root.find("testsuite")
    error = suite.find("testcase").find("error")
    assert error is not None
    assert "badchar" in error.get("message")


def test_naive_started_at_is_rendered_as_utc():
    """SQLite round-trips DateTime(timezone=True) columns as naive, but the
    rendered JUnit timestamp should still be offset-qualified. Construct the
    TestRun directly (bypassing the DB) to pin a naive started_at."""
    from api.services.junit_export import render_junit_xml

    run = TestRun(
        run_id="run-junit-naive", status="PASSED", source_env="dev", target_env="qa",
        started_at=datetime(2026, 7, 18, 10, 0),  # naive, no tzinfo
        total_tests=0,
    )
    run.results = []
    suite = _parse(render_junit_xml(run))
    assert suite.get("timestamp") == "2026-07-18T10:00:00+00:00"
