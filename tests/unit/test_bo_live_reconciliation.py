from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import JobDefinition


def test_bo_live_reconciliation_requires_report_id() -> None:
    with pytest.raises(ValidationError, match="report_id"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "bo_report_id": "1",
                "target_file_path": "prod_snapshot.xlsx",
            },
        )


def test_bo_live_reconciliation_requires_bo_report_id() -> None:
    with pytest.raises(ValidationError, match="bo_report_id"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "target_file_path": "prod_snapshot.xlsx",
            },
        )


def test_bo_live_reconciliation_requires_target_file() -> None:
    with pytest.raises(ValidationError, match="target file"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "bo_report_id": "1",
            },
        )


def test_bo_live_reconciliation_accepts_valid_config() -> None:
    job = JobDefinition(
        name="qa_vs_prod",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "bo_live",
            "report_id": "101",
            "bo_report_id": "1",
            "format": "csv",
            "target_file_path": "prod_snapshot.csv",
        },
    )
    assert job.params["source_mode"] == "bo_live"


def test_bo_live_reconciliation_accepts_uploaded_target_file() -> None:
    job = JobDefinition(
        name="qa_vs_prod",
        job_type="reconciliation",
        query="",
        params={
            "source_mode": "bo_live",
            "report_id": "101",
            "bo_report_id": "1",
            "target_file_content_b64": "aWQsdmFsdWUKMSxhbHBoYQo=",
            "target_file_name": "prod_snapshot.csv",
        },
    )
    assert job.params["target_file_name"] == "prod_snapshot.csv"


def test_bo_live_reconciliation_upload_without_name_rejected() -> None:
    with pytest.raises(ValidationError, match="file name"):
        JobDefinition(
            name="qa_vs_prod",
            job_type="reconciliation",
            query="",
            params={
                "source_mode": "bo_live",
                "report_id": "101",
                "bo_report_id": "1",
                "target_file_content_b64": "aWQsdmFsdWUKMSxhbHBoYQo=",
            },
        )


from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import RunSettings
from api.services.run_executor import RunExecutor
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


_BO_SNAPSHOT = {
    "bo_credentials": {
        "name": "bo",
        "db_host": "bo-host",
        "db_password": "bo-secret",
        "bo_url": "http://bo-server",
        "bo_user": "admin",
    },
}


def test_bo_live_recon_diffs_live_pull_against_target_file(tmp_path, monkeypatch):
    target = tmp_path / "prod_snapshot.csv"
    target.write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")

    from api.services import file_source
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    db = _session()
    RunRepository(db).create_run("r-bo-live", "qa", "prod", {})
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
        run_id="r-bo-live",
        source_env="qa",
        target_env="prod",
        job_sequence=["qa_vs_prod"],
        run_settings=RunSettings(use_live_connections=True, metrics_enabled=False),
        config_snapshot=_BO_SNAPSHOT,
    )

    csv_bytes = b"id,value\n1,alpha\n2,gamma\n"
    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.download_report.return_value = csv_bytes
        executor.execute()

    run = RunRepository(db).get_run("r-bo-live")
    result = run.results[0]
    assert result.source_row_count == 2
    assert result.target_row_count == 2
    assert result.value_mismatch_count == 1
    assert result.target_file_name == "prod_snapshot.csv"
    assert result.source_file_name is None
    assert result.status == TestStatus.FAILED.value


def test_bo_live_recon_raises_without_target_file():
    from api.services.run_executor import RunExecutor
    from api.schemas import JobDefinition

    job = JobDefinition(
        name="qa_vs_prod",
        job_type="reconciliation",
        query="",
        key_columns=["id"],
        params={
            "source_mode": "files",  # bypass model validator; exercise executor guard directly
            "source_file_path": "x.csv",
            "target_file_path": "y.csv",
        },
    )
    job = job.model_copy(update={
        "params": {
            k: v for k, v in job.params.items() if k != "target_file_path"
        } | {"source_mode": "bo_live"},
    })
    executor = RunExecutor(
        db=None,
        run_id="test-run",
        source_env="qa",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(use_live_connections=True, metrics_enabled=False),
        config_snapshot=_BO_SNAPSHOT,
    )
    executor._resolve_segment_columns = lambda _job: []

    with pytest.raises(ValueError, match="target file"):
        executor._build_case_bo_live_recon(job)()


def test_bo_live_recon_errors_loudly_when_live_connections_disabled(tmp_path, monkeypatch):
    """A bo_live job must never fall through to the generic file-reconciliation
    path when live connections are off: it must fail loudly instead of
    silently diffing the target file against a copy of itself (false PASS)."""
    target = tmp_path / "prod_snapshot.csv"
    target.write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")

    from api.services import file_source
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    db = _session()
    RunRepository(db).create_run("r-bo-live-disabled", "qa", "prod", {})
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
        run_id="r-bo-live-disabled",
        source_env="qa",
        target_env="prod",
        job_sequence=["qa_vs_prod"],
        run_settings=RunSettings(use_live_connections=False, metrics_enabled=False),
        config_snapshot=_BO_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        executor.execute()
        MockBO.assert_not_called()

    run = RunRepository(db).get_run("r-bo-live-disabled")
    assert run.status == "ERROR"
    result = run.results[0]
    assert result.status == TestStatus.ERROR.value
    assert "live connections" in (result.error_message or "")
    # Must not be a spurious clean PASS from self-comparing the target file.
    assert result.status != TestStatus.PASSED.value
    assert (result.value_mismatch_count or 0) == 0
    assert result.source_row_count in (None, 0)


def test_uses_file_sources_false_for_bo_live_job_even_with_target_file():
    executor = RunExecutor(
        db=None,
        run_id="test-run",
        source_env="qa",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(use_live_connections=False, metrics_enabled=False),
        config_snapshot=_BO_SNAPSHOT,
    )

    job = MagicMock()
    job.params = {
        "source_mode": "bo_live",
        "report_id": "101",
        "bo_report_id": "1",
        "target_file_path": "prod_snapshot.csv",
    }

    # bo_live jobs must not be routed to file-backed reconciliation just
    # because target file params are present, even when live connections
    # are disabled: that would raise a misleading "source and target
    # files" error instead of falling through to the generic run path.
    assert executor._uses_file_sources(job) is False
