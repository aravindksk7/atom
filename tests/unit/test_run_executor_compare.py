"""RunExecutor dispatch for the `compare` job type."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _executor(db) -> RunExecutor:
    return RunExecutor(
        db=db,
        run_id="run-1",
        source_env="Source A",
        target_env="Source B",
        job_sequence=[],
        run_settings=RunSettings(),
        config_snapshot={},
    )


def _allow(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))


def test_compare_job_runs_a_bo_compare_and_names_the_result_after_the_job(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": {
            "source_a": {"source_type": "path", "file_path": str(tmp_path / "a.csv")},
            "source_b": {"source_type": "path", "file_path": str(tmp_path / "b.csv")},
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_compare"
    assert result.value_mismatch_count == 1


def test_compare_job_runs_a_recon_file_compare(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_file_diff",
        job_type="compare",
        params={"compare_type": "recon_file", "request": {
            "file_a_path": str(tmp_path / "a.csv"),
            "file_b_path": str(tmp_path / "b.csv"),
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_file_diff"
    assert result.status.value == "PASSED"


def test_compare_job_runs_a_matrix_compare_and_names_the_result_after_the_job(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_matrix",
        job_type="compare",
        params={"compare_type": "matrix", "request": {
            "source_a": {"source_type": "file", "file_path": str(tmp_path / "a.csv")},
            "source_b": {"source_type": "file", "file_path": str(tmp_path / "b.csv")},
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_matrix"
    assert result.value_mismatch_count == 1


def test_compare_job_with_an_unknown_compare_type_raises():
    job = JobDefinition.model_construct(
        name="broken",
        job_type="compare",
        description="",
        tags=[],
        query="",
        key_columns=[],
        exclude_columns=[],
        source_env=None,
        target_env=None,
        params={"compare_type": "sql", "request": {}},
        enabled=True,
        rules=[],
        depends_on=[],
        pass_condition=None,
    )

    with pytest.raises(ValueError, match="unknown compare_type"):
        _executor(_session())._build_case(job)()


def test_compare_job_with_live_source_respects_live_connections_setting():
    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": {
            "source_a": {"source_type": "live", "config_id": 1, "doc_id": "doc-1"},
            "source_b": {"source_type": "path", "file_path": "/data/b.csv"},
            "key_columns": ["id"],
        }},
    )

    with pytest.raises(ValueError, match="live connections"):
        _executor(_session())._build_case(job)()
