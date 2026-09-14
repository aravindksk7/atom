# tests/unit/test_run_executor_file_watcher.py
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.reconciliation.file_mapping import DiscoveredFile, FileWatchTimeout
from etl_framework.repository.database import Base
from etl_framework.runner.state import TestStatus


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def executor(db_session, config_snapshot=None):
    return RunExecutor(
        db=db_session, run_id="run-1", source_env="qa", target_env="prod",
        job_sequence=[], run_settings=RunSettings(use_live_connections=True),
        config_snapshot=config_snapshot or {},
    )


def job(params=None):
    p = {"location": {"kind": "local", "root": "/data/inbound", "pattern": "SALES_*.csv"}, "max_tries": 5}
    if params:
        p.update(params)
    return JobDefinition(name="watch_sales_drop", job_type="file_watcher", params=p)


class _FakeWatchResult:
    def __init__(self, file, tries=2, elapsed_seconds=4.0, matched_snippet=None):
        self.file = file
        self.tries = tries
        self.elapsed_seconds = elapsed_seconds
        self.matched_snippet = matched_snippet


def test_build_case_dispatches_file_watcher_job_type(db_session, monkeypatch):
    matched = DiscoveredFile(path="/data/inbound/SALES_20260914.csv", file_name="SALES_20260914.csv", tokens={})
    monkeypatch.setattr(
        "etl_framework.reconciliation.file_mapping.wait_for_watched_file",
        lambda *a, **k: _FakeWatchResult(matched, tries=1, elapsed_seconds=0.0),
    )
    run_job = executor(db_session)._build_case(job())
    result = run_job()
    assert result.status == TestStatus.PASSED


def test_execute_file_watcher_passes_on_match(db_session, monkeypatch):
    matched = DiscoveredFile(path="/data/inbound/SALES_20260914.csv", file_name="SALES_20260914.csv", tokens={})
    monkeypatch.setattr(
        "etl_framework.reconciliation.file_mapping.wait_for_watched_file",
        lambda *a, **k: _FakeWatchResult(matched, tries=3, elapsed_seconds=6.0),
    )
    result = executor(db_session)._execute_file_watcher(job())
    assert result.status == TestStatus.PASSED
    assert result.source_file_name == "SALES_20260914.csv"
    assert result.data_artifact_path == "/data/inbound/SALES_20260914.csv"
    assert result.mismatch_summary["tries"] == 3
    assert result.mismatch_summary["elapsed_seconds"] == 6.0
    assert result.mismatches == []


def test_execute_file_watcher_includes_matched_snippet_when_content_matched(db_session, monkeypatch):
    matched = DiscoveredFile(path="/inbound/DONE.flag", file_name="DONE.flag", tokens={})
    monkeypatch.setattr(
        "etl_framework.reconciliation.file_mapping.wait_for_watched_file",
        lambda *a, **k: _FakeWatchResult(matched, tries=1, elapsed_seconds=0.0, matched_snippet="STATUS=COMPLETE"),
    )
    result = executor(db_session)._execute_file_watcher(job({"content_match": {"text": "STATUS=COMPLETE"}}))
    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["matched_text"] == "STATUS=COMPLETE"


def test_execute_file_watcher_fails_on_timeout(db_session, monkeypatch):
    def _raise(*a, **k):
        raise FileWatchTimeout("no matching file found after 5 attempt(s) (max_tries=5)", tries=5, elapsed_seconds=150.0)

    monkeypatch.setattr("etl_framework.reconciliation.file_mapping.wait_for_watched_file", _raise)
    result = executor(db_session)._execute_file_watcher(job())
    assert result.status == TestStatus.FAILED
    assert result.mismatch_summary["tries"] == 5
    assert result.mismatch_summary["elapsed_seconds"] == 150.0
    assert result.source_file_name is None
    assert result.data_artifact_path is None


def test_execute_file_watcher_errors_on_unexpected_exception(db_session, monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("sftp host unreachable")

    monkeypatch.setattr("etl_framework.reconciliation.file_mapping.wait_for_watched_file", _raise)
    result = executor(db_session)._execute_file_watcher(job())
    assert result.status == TestStatus.ERROR
    assert result.mismatches[0].mismatch_type == "file_watcher_error"
    assert "sftp host unreachable" in result.mismatches[0].target_value


def test_execute_file_watcher_normalizes_scp_kind_to_sftp(db_session, monkeypatch):
    captured_specs = []

    def _fake_discover(self, spec):
        captured_specs.append(spec)
        return []

    monkeypatch.setattr("api.services.multi_file_remote.RemoteFileSourceSession.discover", _fake_discover)
    matched = DiscoveredFile(path="/inbound/a.csv", file_name="a.csv", tokens={})
    monkeypatch.setattr(
        "etl_framework.reconciliation.file_mapping.wait_for_watched_file",
        lambda discover, spec, **k: (discover(), _FakeWatchResult(matched))[1],
    )
    executor(
        db_session,
        config_snapshot={"file_source_credentials": {"scp_host": {"host": "h", "username": "u", "password": "p"}}},
    )._execute_file_watcher(job({
        "location": {"kind": "scp", "root": "/inbound", "pattern": "*.csv", "credentials_ref": "scp_host"},
    }))
    assert captured_specs[0].kind == "sftp"
