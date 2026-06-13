"""Tests for Module 7: RunExecutor live wiring — DBEngine, BORestClient, AutomicClient."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings
from api.services.run_executor import RunExecutor, SQLAlchemyQueryEngine, DataFrameQueryEngine
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobRepository, RunRepository
from etl_framework.runner.state import TestStatus


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


_LIVE_SNAPSHOT = {
    "source_credentials": {
        "name": "dev",
        "db_host": "dev-sql",
        "db_port": 1433,
        "db_name": "etl_db",
        "db_user": "sa",
        "db_password": "secret",
    },
    "target_credentials": {
        "name": "prod",
        "db_host": "prod-sql",
        "db_port": 1433,
        "db_name": "etl_db",
        "db_user": "sa",
        "db_password": "secret",
    },
    "bo_credentials": {
        "name": "bo",
        "db_host": "bo-host",
        "db_password": "bo-secret",
        "bo_url": "http://bo-server",
        "bo_user": "admin",
    },
    "automic_credentials": {
        "name": "ac",
        "db_host": "ac-host",
        "db_password": "ac-secret",
        "automic_url": "http://automic",
        "automic_user": "admin",
        "automic_password": "pass",
    },
}


# ---------------------------------------------------------------------------
# SQLAlchemyQueryEngine
# ---------------------------------------------------------------------------

def test_sqla_engine_delegates_to_db_engine():
    import pandas as pd
    mock_db_engine = MagicMock()
    mock_db_engine.execute_query.return_value = pd.DataFrame({"id": [1, 2]})
    sqa = SQLAlchemyQueryEngine(db_engine=mock_db_engine)
    result = sqa.execute_query("SELECT 1", {})
    mock_db_engine.execute_query.assert_called_once_with("SELECT 1", {})
    assert len(result) == 2


def test_sqla_engine_exposes_env_name():
    mock_db_engine = MagicMock()
    mock_db_engine._env.name = "staging"
    sqa = SQLAlchemyQueryEngine(db_engine=mock_db_engine)
    assert sqa._env.name == "staging"


# ---------------------------------------------------------------------------
# _build_engines: simulation vs live
# ---------------------------------------------------------------------------

def _make_executor(db, run_id, job_seq, settings, snapshot=None):
    return RunExecutor(
        db=db,
        run_id=run_id,
        source_env="dev",
        target_env="prod",
        job_sequence=job_seq,
        run_settings=settings,
        config_snapshot=snapshot,
    )


def test_build_engines_returns_dataframe_when_live_disabled():
    db = _session()
    RunRepository(db).create_run("r1", "dev", "prod", {})
    executor = _make_executor(db, "r1", [], RunSettings(use_live_connections=False, metrics_enabled=False))
    job = MagicMock()
    job.query = "SELECT 1"
    job.key_columns = ["id"]
    job.params = {}
    src, tgt = executor._build_engines(job)
    assert isinstance(src, DataFrameQueryEngine)
    assert isinstance(tgt, DataFrameQueryEngine)


def test_build_engines_returns_sqla_when_live_enabled():
    db = _session()
    RunRepository(db).create_run("r2", "dev", "prod", {})
    executor = _make_executor(
        db, "r2", [],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )
    job = MagicMock()
    job.query = "SELECT 1"
    job.key_columns = ["id"]
    job.params = {}

    with patch("api.services.run_executor.DBEngine") as MockDBEngine:
        mock_instance = MagicMock()
        mock_instance._env.name = "dev"
        MockDBEngine.return_value = mock_instance
        src, tgt = executor._build_engines(job)

    assert isinstance(src, SQLAlchemyQueryEngine)
    assert isinstance(tgt, SQLAlchemyQueryEngine)


def test_build_engines_falls_back_to_dataframe_if_no_live_creds():
    db = _session()
    RunRepository(db).create_run("r3", "dev", "prod", {})
    executor = _make_executor(
        db, "r3", [],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot={},  # no credentials
    )
    job = MagicMock()
    job.query = "SELECT 1"
    job.key_columns = ["id"]
    job.params = {}
    src, tgt = executor._build_engines(job)
    assert isinstance(src, DataFrameQueryEngine)
    assert isinstance(tgt, DataFrameQueryEngine)


# ---------------------------------------------------------------------------
# bo_report dispatch
# ---------------------------------------------------------------------------

def test_bo_report_job_returns_passed_on_success():
    db = _session()
    RunRepository(db).create_run("r-bo", "dev", "prod", {})
    JobRepository(db).create({
        "name": "my_report",
        "description": "",
        "tags": [],
        "job_type": "bo_report",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"report_id": "101", "bo_report_id": "1", "format": "xlsx"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-bo", ["my_report"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.download_report.return_value = b"xlsx data"
        executor.execute()

    run = RunRepository(db).get_run("r-bo")
    assert run.status in ("PASSED", "FAILED", "ERROR")
    assert run.total_tests == 1
    result = run.results[0]
    assert result.status == TestStatus.PASSED.value


def test_bo_report_job_returns_error_on_failure():
    db = _session()
    RunRepository(db).create_run("r-bo-err", "dev", "prod", {})
    JobRepository(db).create({
        "name": "bad_report",
        "description": "",
        "tags": [],
        "job_type": "bo_report",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"report_id": "999", "bo_report_id": "1", "format": "pdf"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-bo-err", ["bad_report"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.download_report.side_effect = RuntimeError("Connection refused")
        executor.execute()

    run = RunRepository(db).get_run("r-bo-err")
    assert run.results[0].status == TestStatus.ERROR.value


# ---------------------------------------------------------------------------
# automic_job dispatch
# ---------------------------------------------------------------------------

def test_automic_job_returns_passed_when_status_passed():
    db = _session()
    RunRepository(db).create_run("r-ac", "dev", "prod", {})
    JobRepository(db).create({
        "name": "nightly_etl",
        "description": "",
        "tags": [],
        "job_type": "automic_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"job_name": "ETL_NIGHTLY"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-ac", ["nightly_etl"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    mock_status = MagicMock()
    mock_status.status = TestStatus.PASSED
    mock_status.identifier = "ETL_NIGHTLY"
    mock_status.environment = "dev"

    with patch("api.services.run_executor.AutomicClient") as MockAC:
        inst = MockAC.return_value
        inst.get_status_by_job_name.return_value = mock_status
        executor.execute()

    run = RunRepository(db).get_run("r-ac")
    assert run.results[0].status == TestStatus.PASSED.value


def test_automic_job_uses_run_id_lookup_when_run_id_param():
    db = _session()
    RunRepository(db).create_run("r-ac2", "dev", "prod", {})
    JobRepository(db).create({
        "name": "ac_by_runid",
        "description": "",
        "tags": [],
        "job_type": "automic_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"run_id": "RUN_42"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-ac2", ["ac_by_runid"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    mock_status = MagicMock()
    mock_status.status = TestStatus.PASSED
    mock_status.identifier = "RUN_42"
    mock_status.environment = "dev"

    with patch("api.services.run_executor.AutomicClient") as MockAC:
        inst = MockAC.return_value
        inst.get_status_by_run_id.return_value = mock_status
        executor.execute()

    MockAC.return_value.get_status_by_run_id.assert_called_once_with("RUN_42")
