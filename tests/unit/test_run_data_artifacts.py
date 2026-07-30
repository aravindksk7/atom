"""Run data artifacts: a bo_report run persists the report it downloaded so the
Compare tab can later row-diff that stored run against a reference file."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import ReconFileCompareRequest, RunSettings
from api.services import upload_store
from api.services.run_executor import RunExecutor
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import JobRepository, RunRepository


_BO_SNAPSHOT = {
    "bo_credentials": {
        "name": "bo",
        "db_host": "bo-host",
        "db_password": "bo-secret",
        "bo_url": "http://bo-server",
        "bo_user": "admin",
    },
}

_CSV = b"id,value\n1,alpha\n2,beta\n"


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _bo_report_run(db, tmp_path, monkeypatch, run_id="r-bo-report", data=_CSV, fmt="csv"):
    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    RunRepository(db).create_run(run_id, "bo", "bo", {})
    JobRepository(db).create({
        "name": "sales_report",
        "description": "",
        "tags": [],
        "job_type": "bo_report",
        "query": "",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"report_id": "101", "bo_report_id": "1", "format": fmt},
        "enabled": True,
    })
    executor = RunExecutor(
        db=db,
        run_id=run_id,
        source_env="bo",
        target_env="bo",
        job_sequence=["sales_report"],
        run_settings=RunSettings(use_live_connections=True, metrics_enabled=False),
        config_snapshot=_BO_SNAPSHOT,
    )
    with patch("api.services.run_executor.BORestClient") as MockBO:
        MockBO.return_value.download_report.return_value = data
        executor.execute()
    return RunRepository(db).get_run(run_id)


# ---------------------------------------------------------------------------
# upload_store helpers
# ---------------------------------------------------------------------------

def test_persist_run_data_artifact_writes_under_upload_root(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())

    path = upload_store.persist_run_data_artifact("run-1", _CSV, "report.csv")

    assert path is not None
    written = Path(path)
    assert written.read_bytes() == _CSV
    assert written.parent == tmp_path.resolve() / "run-1"


def test_persist_run_data_artifact_sanitizes_hostile_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())

    path = upload_store.persist_run_data_artifact("run-1", _CSV, "../../etc/passwd.csv")

    assert Path(path).parent == tmp_path.resolve() / "run-1"


def test_persist_run_data_artifact_skips_oversize_payload(tmp_path, monkeypatch):
    """Report downloads can be huge; on-prem disk must not fill silently."""
    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    monkeypatch.setattr(upload_store, "RUN_DATA_ARTIFACT_MAX_BYTES", 8)

    assert upload_store.persist_run_data_artifact("run-1", _CSV, "report.csv") is None
    assert not (tmp_path / "run-1").exists()


def test_resolve_run_data_artifact_rejects_path_outside_upload_root(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", (tmp_path / "uploads").resolve())
    stray = tmp_path / "elsewhere.csv"
    stray.write_bytes(_CSV)

    assert upload_store.resolve_run_data_artifact(str(stray)) is None


def test_resolve_run_data_artifact_returns_none_when_file_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())

    assert upload_store.resolve_run_data_artifact(str(tmp_path / "run-1" / "gone.csv")) is None


# ---------------------------------------------------------------------------
# bo_report run persists its download
# ---------------------------------------------------------------------------

def test_bo_report_run_persists_downloaded_report_as_artifact(tmp_path, monkeypatch):
    run = _bo_report_run(_session(), tmp_path, monkeypatch)

    result = run.results[0]
    assert result.data_artifact_path
    assert Path(result.data_artifact_path).read_bytes() == _CSV


def test_bo_report_run_survives_artifact_persist_failure(tmp_path, monkeypatch):
    """Persisting the artifact is a convenience — it must never fail the run."""
    monkeypatch.setattr(
        upload_store, "persist_run_data_artifact",
        MagicMock(side_effect=OSError("disk full")),
    )
    run = _bo_report_run(_session(), tmp_path, monkeypatch)

    result = run.results[0]
    assert result.status == "PASSED"
    assert result.source_row_count == 2
    assert result.data_artifact_path is None


# ---------------------------------------------------------------------------
# End-to-end: stored bo_report run vs uploaded file
# ---------------------------------------------------------------------------

def test_stored_bo_report_run_loads_as_frame_for_recon_compare(tmp_path, monkeypatch):
    """The reported 422: Source A = stored bo_report run, Source B = xlsx. Source A
    must now resolve to a DataFrame so the pair is row-diffable."""
    from api.services.compare_service import CompareService
    from etl_framework.repository.repository import ConfigRepository

    db = _session()
    _bo_report_run(db, tmp_path, monkeypatch)
    svc = CompareService(db, ConfigRepository(db))

    req = ReconFileCompareRequest(
        stored_run_id="r-bo-report",
        file_b_name="reference.csv",
        file_b_content_b64="aWQsdmFsdWUKMSxhbHBoYQo=",
    )
    frame = svc._load_recon_source(req, "a")

    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == ["id", "value"]
    assert len(frame) == 2


# ---------------------------------------------------------------------------
# Run status exposes whether the run can be row-diffed, so the UI can guard
# ---------------------------------------------------------------------------

def test_run_status_out_flags_row_diffable_run(tmp_path, monkeypatch):
    from api.routes.runs import _run_status_out

    run = _bo_report_run(_session(), tmp_path, monkeypatch)

    assert _run_status_out(run).has_data_artifact is True


def test_run_status_out_flag_is_false_without_artifact():
    from api.routes.runs import _run_status_out

    db = _session()
    RunRepository(db).create_run("r-plain", "qa", "prod", {})

    assert _run_status_out(RunRepository(db).get_run("r-plain")).has_data_artifact is False
