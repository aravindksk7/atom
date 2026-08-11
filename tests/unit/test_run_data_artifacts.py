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


def _bo_live_recon_run(db, tmp_path, monkeypatch, run_id="r-bo-live", data=_CSV):
    """A reconciliation job whose source is pulled live from BO and whose target is
    a local file — the other shape of "an already-passed SAP BO run"."""
    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    target = tmp_path / "prod_snapshot.csv"
    target.write_bytes(data)
    from api.services import file_source
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    RunRepository(db).create_run(run_id, "qa", "prod", {})
    JobRepository(db).create({
        "name": "qa_vs_prod",
        "description": "",
        "tags": [],
        "job_type": "reconciliation",
        "query": "",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {
            "source_mode": "bo_live",
            "report_id": "101",
            "bo_report_id": "1",
            "format": "csv",
            "target_file_path": str(target),
        },
        "enabled": True,
    })
    executor = RunExecutor(
        db=db,
        run_id=run_id,
        source_env="qa",
        target_env="prod",
        job_sequence=["qa_vs_prod"],
        run_settings=RunSettings(use_live_connections=True, metrics_enabled=False),
        config_snapshot=_BO_SNAPSHOT,
    )
    with patch("api.services.run_executor.BORestClient") as MockBO:
        MockBO.return_value.download_report.return_value = data
        executor.execute()
    return RunRepository(db).get_run(run_id)


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


def test_bo_live_recon_run_persists_its_live_pull_as_artifact(tmp_path, monkeypatch):
    """A bo_live recon is equally "a SAP BO run" a user may later want as Source A,
    so it must keep the data it pulled from BO — its source side."""
    run = _bo_live_recon_run(_session(), tmp_path, monkeypatch)

    result = run.results[0]
    assert result.data_artifact_path
    assert Path(result.data_artifact_path).read_bytes() == _CSV


def test_stored_bo_live_recon_run_loads_as_frame_for_recon_compare(tmp_path, monkeypatch):
    from api.services.compare_service import CompareService
    from etl_framework.repository.repository import ConfigRepository

    db = _session()
    _bo_live_recon_run(db, tmp_path, monkeypatch)
    svc = CompareService(db, ConfigRepository(db))

    frame = svc._load_recon_source(
        ReconFileCompareRequest(
            stored_run_id="r-bo-live",
            file_b_name="reference.csv",
            file_b_content_b64="aWQsdmFsdWUKMSxhbHBoYQo=",
        ),
        "a",
    )

    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == ["id", "value"]


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


# ---------------------------------------------------------------------------
# Job-scoped artifact resolution (multi-job runs)
# ---------------------------------------------------------------------------

def test_resolve_job_result_artifact_finds_the_named_jobs_result(tmp_path, monkeypatch):
    from api.services import upload_store
    from api.services.run_data_artifact import resolve_job_result_artifact
    from etl_framework.repository.repository import RunRepository
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    artifact_path = upload_store.persist_run_data_artifact("run-1", _CSV, "report.csv")

    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-1", "dev", "prod")
    repo.add_test_result("run-1", ReconciliationResult(
        query_name="my_bo_job", source_env="dev", target_env="prod",
        source_row_count=1, target_row_count=1, matched_count=1,
        missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
        mismatches=[], status=TestStatus.PASSED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        data_artifact_path=artifact_path,
    ))

    resolved = resolve_job_result_artifact(repo, "run-1", "my_bo_job")

    assert resolved is not None
    assert resolved.read_bytes() == _CSV


def test_resolve_job_result_artifact_returns_none_for_unknown_job(tmp_path, monkeypatch):
    from api.services import upload_store
    from api.services.run_data_artifact import resolve_job_result_artifact
    from etl_framework.repository.repository import RunRepository

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-1", "dev", "prod")

    assert resolve_job_result_artifact(repo, "run-1", "no_such_job") is None


def test_load_job_result_frame_reads_the_artifact_as_a_dataframe(tmp_path, monkeypatch):
    from api.services import upload_store
    from api.services.run_data_artifact import load_job_result_frame
    from etl_framework.repository.repository import RunRepository
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone

    monkeypatch.setattr(upload_store, "UPLOAD_ROOT", tmp_path.resolve())
    artifact_path = upload_store.persist_run_data_artifact("run-1", _CSV, "report.csv")

    db = _session()
    repo = RunRepository(db)
    repo.create_run("run-1", "dev", "prod")
    repo.add_test_result("run-1", ReconciliationResult(
        query_name="my_bo_job", source_env="dev", target_env="prod",
        source_row_count=1, target_row_count=1, matched_count=1,
        missing_in_target_count=0, missing_in_source_count=0, value_mismatch_count=0,
        mismatches=[], status=TestStatus.PASSED,
        executed_at=datetime.now(timezone.utc), duration_seconds=0.1,
        data_artifact_path=artifact_path,
    ))

    frame = load_job_result_frame(repo, "run-1", "my_bo_job")

    assert frame is not None
    assert list(frame["value"]) == ["alpha", "beta"]
