from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import api.services.compare_service as cs_mod
from api.schemas import BOCompareRequest, JobDefinition, RunSettings
from api.services.compare_service import CompareService
from api.services.difference_export import write_recomputed_differences
from api.services.run_executor import RunExecutor
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.database import Base
from etl_framework.repository.models import MismatchDetail, TestResult
from etl_framework.repository.repository import ConfigRepository, JobRepository, RunRepository
from tests.helpers.pdf_fixtures import build_bo_pdf

pytestmark = pytest.mark.usefixtures("pdf_env")


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_export_rows_equal_stored_rows_for_pdf_vs_pdf(tmp_path, monkeypatch):
    pdf_a = build_bo_pdf(
        tmp_path / "a.pdf",
        col_widths=[60.0, 200.0, 80.0],
        tables=[{
            "header": ["Key", "Description", "Amount"],
            "rows": [
                ["K1", "AlphaBetaGamma", "100.00"],
                ["K2", "DeltaEpsilon", "200.00"],
            ],
        }],
    )
    pdf_b = build_bo_pdf(
        tmp_path / "b.pdf",
        col_widths=[60.0, 75.0, 80.0],
        tables=[{
            "header": ["Key", "Description", "Amount"],
            "rows": [
                ["K1", "AlphaBeta\nGamma", "100.00"],
                ["K2", "DeltaEpsilon", "205.00"],
            ],
        }],
    )
    monkeypatch.setattr(cs_mod, "resolve_allowed_path", lambda p: Path(p))

    db = _session()
    run_id = "pdf-export-run-1"
    req = BOCompareRequest(
        source_a={"source_type": "path", "file_path": str(pdf_a)},
        source_b={"source_type": "path", "file_path": str(pdf_b)},
        key_columns=["Key"],
    )
    RunRepository(db).create_run(
        run_id=run_id,
        source_env="Source A",
        target_env="Source B",
        config_snapshot={
            "compare_request_type": "bo_report",
            "request": req.model_dump(mode="json"),
        },
        run_type="bo_comparison",
    )

    svc = CompareService(db, ConfigRepository(db))
    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        svc.run_bo_comparison(req, run_id)

    stored_rows = (
        db.query(MismatchDetail)
        .join(TestResult, TestResult.id == MismatchDetail.test_result_id)
        .filter(TestResult.run_id == run_id)
        .order_by(MismatchDetail.id)
        .all()
    )
    stored = [
        (row.column_name, row.key_values, row.source_value, row.target_value)
        for row in stored_rows
    ]

    run = RunRepository(db).get_run(run_id)
    csv_path = tmp_path / "export.csv"
    write_recomputed_differences(db, run, "csv", csv_path)

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        exported_rows = list(csv.DictReader(fh))
    exported = [
        (
            row["column_name"],
            json.loads(row["key_values"]),
            row["source_value"],
            row["target_value"],
        )
        for row in exported_rows
    ]

    assert len(stored) == 1
    assert exported == stored


def test_saved_bo_compare_job_with_path_pdfs_runs(monkeypatch, real_pdf, bo_expected):
    p = real_pdf("sample3.pdf")
    monkeypatch.setattr(cs_mod, "resolve_allowed_path", lambda path: Path(path))

    db = _session()
    job = JobDefinition(
        name="nightly_pdf_compare",
        job_type="compare",
        params={
            "compare_type": "bo",
            "request": {
                "source_a": {"source_type": "path", "file_path": str(p)},
                "source_b": {"source_type": "path", "file_path": str(p)},
                "key_columns": bo_expected["sample3"]["key"],
            },
        },
    )
    executor = RunExecutor(
        db=db,
        run_id="run-pdf-job-1",
        source_env="Source A",
        target_env="Source B",
        job_sequence=[],
        run_settings=RunSettings(),
        config_snapshot={},
    )

    result = executor._build_case_compare(job)()

    assert result.mismatch_summary["pdf_sources"]["b"]["pages"] == 1


def test_saved_job_pdf_error_is_readable(monkeypatch, real_pdf, bo_expected):
    p = real_pdf("sample3.pdf")
    monkeypatch.setattr(cs_mod, "resolve_allowed_path", lambda path: Path(path))

    db = _session()
    JobRepository(db).create({
        "name": "nightly_pdf_no_key",
        "description": "",
        "tags": [],
        "job_type": "compare",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "enabled": True,
        "params": {
            "compare_type": "bo",
            "request": {
                "source_a": {"source_type": "path", "file_path": str(p)},
                "source_b": {"source_type": "path", "file_path": str(p)},
            },
        },
    })
    RunRepository(db).create_run("run-pdf-err-1", "Source A", "Source B", {})

    RunExecutor(
        db=db,
        run_id="run-pdf-err-1",
        source_env="Source A",
        target_env="Source B",
        job_sequence=["nightly_pdf_no_key"],
        run_settings=RunSettings(metrics_enabled=False),
        config_snapshot={},
    ).execute()

    run = RunRepository(db).get_run("run-pdf-err-1")
    assert run.results[0].error_message == (
        f"set key columns for a PDF compare (suggested key: {bo_expected['sample3']['key'][0]})"
    )


def test_saved_bo_compare_job_export_recomputes_with_pdf_alignment(tmp_path, monkeypatch):
    pdf_a = build_bo_pdf(
        tmp_path / "job_a.pdf",
        col_widths=[60.0, 200.0, 80.0],
        tables=[{
            "header": ["Key", "Description", "Amount"],
            "rows": [
                ["K1", "AlphaBetaGamma", "100.00"],
                ["K2", "DeltaEpsilon", "200.00"],
            ],
        }],
    )
    pdf_b = build_bo_pdf(
        tmp_path / "job_b.pdf",
        col_widths=[60.0, 75.0, 80.0],
        tables=[{
            "header": ["Key", "Description", "Amount"],
            "rows": [
                ["K1", "AlphaBeta\nGamma", "100.00"],
                ["K2", "DeltaEpsilon", "205.00"],
            ],
        }],
    )
    monkeypatch.setattr(cs_mod, "resolve_allowed_path", lambda path: Path(path))

    db = _session()
    JobRepository(db).create({
        "name": "saved_pdf_cmp",
        "description": "",
        "tags": [],
        "job_type": "compare",
        "query": "",
        "key_columns": ["Key"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "enabled": True,
        "params": {
            "compare_type": "bo",
            "request": {
                "source_a": {"source_type": "path", "file_path": str(pdf_a)},
                "source_b": {"source_type": "path", "file_path": str(pdf_b)},
                "key_columns": ["Key"],
            },
        },
    })
    run_id = "run-saved-pdf-export-1"
    RunRepository(db).create_run(
        run_id=run_id,
        source_env="Source A",
        target_env="Source B",
        config_snapshot={
            "job_sequence": [{"job_name": "saved_pdf_cmp"}],
            "run_settings": RunSettings(metrics_enabled=False).model_dump(),
        },
    )
    RunExecutor(
        db=db,
        run_id=run_id,
        source_env="Source A",
        target_env="Source B",
        job_sequence=["saved_pdf_cmp"],
        run_settings=RunSettings(metrics_enabled=False),
        config_snapshot={},
    ).execute()

    run = RunRepository(db).get_run(run_id)
    csv_path = tmp_path / "saved_job_export.csv"
    write_recomputed_differences(db, run, "csv", csv_path)

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        exported = [
            (r["test_name"], r["column_name"], json.loads(r["key_values"]), r["source_value"], r["target_value"])
            for r in csv.DictReader(fh)
        ]

    assert exported == [("saved_pdf_cmp", "Amount", {"Key": "K2"}, "200.0", "205.0")]


def test_export_error_for_pdf_missing_key_is_readable():
    from fastapi import HTTPException
    from api.services.difference_export import _friendly_export_error

    msg = _friendly_export_error(
        HTTPException(422, {"message": "set key columns for a PDF compare", "suggested_key": "Region"})
    )
    assert msg == "set key columns for a PDF compare (suggested key: Region)"
