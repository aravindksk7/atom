"""Tests for the DifferenceWriter json (NDJSON) export format and related helpers."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def test_writer_json_format_writes_one_object_per_line(tmp_path):
    from api.services.difference_export import DifferenceWriter, DIFFERENCE_FIELDS

    path = tmp_path / "diffs.jsonl"
    with DifferenceWriter(path, "json") as writer:
        writer.write({
            "test_name": "orders", "key_values": {"id": 1}, "column_name": "amount",
            "source_value": "10", "target_value": "12", "mismatch_type": "value_diff",
            "delta": 2.0, "relative_delta": 0.2,
        })
        writer.write({
            "test_name": "orders", "key_values": {"id": 2}, "column_name": "amount",
            "source_value": "20", "target_value": "21", "mismatch_type": "value_diff",
            "delta": 1.0, "relative_delta": 0.05,
        })

    assert writer.row_count == 2
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert set(row.keys()) == set(DIFFERENCE_FIELDS)
    first = json.loads(lines[0])
    assert first["test_name"] == "orders"
    assert first["key_values"] == '{"id": 1}'
    assert first["source_value"] == "10"


def test_validate_difference_format_accepts_json():
    from api.services.difference_export import validate_difference_format

    assert validate_difference_format("json") == "json"
    assert validate_difference_format("JSON") == "json"


def test_validate_difference_format_still_rejects_unknown():
    import pytest
    from fastapi import HTTPException
    from api.services.difference_export import validate_difference_format

    with pytest.raises(HTTPException):
        validate_difference_format("xlsx")


def test_media_type_for_json():
    from api.services.difference_export import media_type_for

    assert media_type_for("json") == "application/x-ndjson"


def test_export_filename_json_uses_jsonl_suffix():
    from api.services.difference_export import export_filename

    name = export_filename("run-1", "json", "exp-1")
    assert name.endswith(".jsonl")
    assert "run-1" in name and "exp-1" in name


def test_export_filename_csv_and_parquet_unaffected():
    from api.services.difference_export import export_filename

    assert export_filename("run-1", "csv").endswith(".csv")
    assert export_filename("run-1", "parquet").endswith(".parquet")


def test_create_export_job_accepts_json_format(monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    from etl_framework.repository.database import Base, get_db
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import RunRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.runs.run_difference_export_job", lambda export_id: None)

    def override_get_db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    try:
        with Session(engine) as db:
            raw, _ = TokenRepository(db).create("test-runner")
            run_id = str(uuid.uuid4())
            RunRepository(db).create_run(run_id, "dev", "qa", {})

        client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
        resp = client.post(f"/api/runs/{run_id}/exports", json={"format": "json"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["format"] == "json"
        assert data["status"] in ("PENDING", "RUNNING", "COMPLETED", "FAILED")
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_write_reconciliation_run_skips_bo_live_job_instead_of_self_comparing(tmp_path, monkeypatch):
    """A bo_live reconciliation job has no source file -- only a live BO pull and a
    target file. _write_reconciliation_run (used by both the differences export
    and the full HTML report) must exclude it entirely, mirroring the existing
    skip for non-reconciliation job types, rather than falling through to
    _build_engines -- which would self-compare the target file against a copy of
    itself and silently produce a spurious zero-mismatch entry, even for a run
    whose real (live) execution found genuine mismatches."""
    from datetime import datetime, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from api.services import difference_export as de
    from api.services import file_source
    from api.services.run_executor import RunExecutor
    from etl_framework.reconciliation.models import ReconciliationResult
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import Base
    from etl_framework.repository.models import TestRun
    from etl_framework.repository.repository import JobRepository, RunRepository
    from etl_framework.runner.state import TestStatus

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    # A real file pair for a normal file-backed reconciliation job, to prove
    # that job is still recomputed and included while the bo_live job is not.
    src = tmp_path / "src.csv"
    tgt = tmp_path / "tgt.csv"
    src.write_text("id,value\n1,alpha\n", encoding="utf-8")
    tgt.write_text("id,value\n1,beta\n", encoding="utf-8")
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))

    calls: list[str] = []
    original_build_engines = RunExecutor._build_engines

    def _spy_build_engines(self, job):
        calls.append(job.name)
        return original_build_engines(self, job)

    monkeypatch.setattr(RunExecutor, "_build_engines", _spy_build_engines)

    with _db_module.SessionLocal() as db:
        JobRepository(db).create({
            "name": "bo_live_job",
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
                "target_file_path": str(tgt),
            },
            "enabled": True,
        })
        JobRepository(db).create({
            "name": "normal_job",
            "description": "",
            "tags": [],
            "job_type": "reconciliation",
            "query": "",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None, "target_env": None,
            "params": {
                "source_file_path": str(src),
                "target_file_path": str(tgt),
            },
            "enabled": True,
        })

        repo = RunRepository(db)
        run = repo.create_run(
            run_id="run-bo-live-export",
            source_env="qa",
            target_env="prod",
            config_snapshot={
                "compare_request_type": "unknown",
                "request": {},
                "job_sequence": ["bo_live_job", "normal_job"],
            },
        )
        # The run's own real (live) execution found a genuine mismatch for the
        # bo_live job -- re-exporting must not contradict that with a bogus 0.
        repo.add_test_result(run.run_id, ReconciliationResult(
            query_name="bo_live_job",
            source_env="qa",
            target_env="prod",
            source_row_count=1,
            target_row_count=1,
            matched_count=0,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=1,
            mismatches=[],
            status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc),
            duration_seconds=0.1,
        ))
        run_id = run.run_id

    out_path = tmp_path / "diffs.jsonl"
    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        row_count = de.write_recomputed_differences(db, run, "json", out_path)

    # The bo_live job must never reach _build_engines (which would self-compare
    # the target file against a copy of itself), while the normal job still does.
    assert "bo_live_job" not in calls
    assert "normal_job" in calls

    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert row_count == len(lines) == 1
    row = json.loads(lines[0])
    assert row["test_name"] == "normal_job"


def test_media_type_for_html():
    from api.services.difference_export import media_type_for

    assert media_type_for("html") == "text/html"


def test_export_filename_html_uses_html_suffix():
    from api.services.difference_export import export_filename

    name = export_filename("run-1", "html", "exp-1")
    assert name.endswith(".html")
    assert "run-1" in name and "exp-1" in name


def test_create_export_job_accepts_html_format(monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    from etl_framework.repository.database import Base, get_db
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import RunRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.runs.run_difference_export_job", lambda export_id: None)

    def override_get_db():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    try:
        with Session(engine) as db:
            raw, _ = TokenRepository(db).create("test-runner")
            run_id = str(uuid.uuid4())
            RunRepository(db).create_run(run_id, "dev", "qa", {})

        client = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
        resp = client.post(f"/api/runs/{run_id}/exports", json={"format": "html"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["format"] == "html"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_recomputed_difference_export_handles_multi_file_jobs(tmp_path, monkeypatch):
    from api.services import difference_export as de
    from api.services import file_source
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import Base
    from etl_framework.repository.models import TestRun
    from etl_framework.repository.repository import JobRepository, RunRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())

    source_dir = tmp_path / "source"
    target_dir = tmp_path / "target"
    source_dir.mkdir()
    target_dir.mkdir()
    (source_dir / "sales_east.csv").write_text("id,amount,note\n1,10,draft\n", encoding="utf-8")
    (target_dir / "financials_east.csv").write_text("id,amount,note\n1,11,final\n", encoding="utf-8")
    (source_dir / "sales_west.csv").write_text("id,amount,note\n2,20,draft\n", encoding="utf-8")
    (target_dir / "financials_west.csv").write_text("id,amount,note\n2,22,final\n", encoding="utf-8")

    with _db_module.SessionLocal() as db:
        JobRepository(db).create({
            "name": "regional_sales_recon",
            "description": "",
            "tags": [],
            "job_type": "reconciliation",
            "query": "",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_mode": "multi_file",
                "file_mapping": {
                    "strategy": "explicit",
                    "match_on": ["region"],
                    "source": {"kind": "local", "root": str(source_dir), "pattern": "sales_{region}.csv"},
                    "target": {"kind": "local", "root": str(target_dir), "pattern": "financials_{region}.csv"},
                },
            },
            "enabled": True,
        })
        run = RunRepository(db).create_run(
            run_id="run-multi-file-export",
            source_env="qa",
            target_env="prod",
            config_snapshot={
                "compare_request_type": "unknown",
                "request": {},
                "job_sequence": ["regional_sales_recon"],
                "run_settings": {"exclude_columns": ["note"]},
            },
        )
        run_id = run.run_id

    out_path = tmp_path / "diffs.jsonl"
    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        row_count = de.write_recomputed_differences(db, run, "json", out_path)

    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert row_count == len(lines) == 2
    rows = [json.loads(line) for line in lines]
    pairs = {json.loads(row["key_values"])["__pair__"]["region"] for row in rows}
    assert pairs == {"east", "west"}
    assert {row["test_name"] for row in rows} == {"regional_sales_recon"}
    assert {row["column_name"] for row in rows} == {"amount"}


def test_recomputed_difference_export_handles_s3_multi_file_jobs(tmp_path, monkeypatch):
    from api.services import difference_export as de
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import Base
    from etl_framework.repository.models import TestRun
    from etl_framework.repository.repository import JobRepository, RunRepository

    class FakeBody:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def read(self) -> bytes:
            return self._raw

    class FakeS3Client:
        objects = {
            "source/sales_east.csv": b"id,amount\n1,10\n",
            "target/financials_east.csv": b"id,amount\n1,11\n",
        }

        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return self

        def paginate(self, **kwargs):
            prefix = kwargs["Prefix"]
            return [{"Contents": [{"Key": key} for key in self.objects if key.startswith(prefix)]}]

        def get_object(self, **kwargs):
            return {"Body": FakeBody(self.objects[kwargs["Key"]])}

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", lambda _snapshot, _spec: FakeS3Client())

    with _db_module.SessionLocal() as db:
        JobRepository(db).create({
            "name": "regional_sales_recon",
            "description": "",
            "tags": [],
            "job_type": "reconciliation",
            "query": "",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_mode": "multi_file",
                "file_mapping": {
                    "strategy": "explicit",
                    "match_on": ["region"],
                    "source": {"kind": "s3", "root": "s3://finance/source", "pattern": "sales_{region}.csv"},
                    "target": {"kind": "s3", "root": "s3://finance/target", "pattern": "financials_{region}.csv"},
                },
            },
            "enabled": True,
        })
        run = RunRepository(db).create_run(
            run_id="run-s3-multi-file-export",
            source_env="qa",
            target_env="prod",
            config_snapshot={"compare_request_type": "unknown", "request": {}, "job_sequence": ["regional_sales_recon"]},
        )
        run_id = run.run_id

    out_path = tmp_path / "diffs.jsonl"
    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        row_count = de.write_recomputed_differences(db, run, "json", out_path)

    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").strip().splitlines()]
    assert row_count == 1
    assert json.loads(rows[0]["key_values"])["__pair__"] == {"region": "east"}
    assert rows[0]["column_name"] == "amount"


def test_recomputed_difference_export_handles_sftp_multi_file_jobs(tmp_path, monkeypatch):
    """Mirrors test_recomputed_difference_export_handles_s3_multi_file_jobs above --
    the sftp kind had no coverage of _write_multi_file_reconciliation_job's own
    _build_sftp_client/_read_file dispatch before this test was added."""
    from api.services import difference_export as de
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.database import Base
    from etl_framework.repository.models import TestRun
    from etl_framework.repository.repository import JobRepository, RunRepository

    class FakeSFTPFile:
        def __init__(self, raw: bytes) -> None:
            self._raw = raw

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self) -> bytes:
            return self._raw

    class FakeSFTPClient:
        objects = {
            "/source/sales_east.csv": b"id,amount\n1,10\n",
            "/target/financials_east.csv": b"id,amount\n1,11\n",
        }

        def listdir_attr(self, path):
            prefix = path.rstrip("/") + "/"
            names = sorted(
                key[len(prefix):] for key in self.objects
                if key.startswith(prefix) and "/" not in key[len(prefix):]
            )
            return [type("Attr", (), {"filename": name, "st_mode": 0o100644})() for name in names]

        def open(self, path, mode):
            return FakeSFTPFile(self.objects[path])

        def close(self):
            pass

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.services.multi_file_remote.build_sftp_client", lambda _snapshot, _spec: FakeSFTPClient())

    with _db_module.SessionLocal() as db:
        JobRepository(db).create({
            "name": "regional_sales_recon_sftp",
            "description": "",
            "tags": [],
            "job_type": "reconciliation",
            "query": "",
            "key_columns": ["id"],
            "exclude_columns": [],
            "source_env": None,
            "target_env": None,
            "params": {
                "source_mode": "multi_file",
                "file_mapping": {
                    "strategy": "explicit",
                    "match_on": ["region"],
                    "source": {"kind": "sftp", "root": "/source", "pattern": "sales_{region}.csv"},
                    "target": {"kind": "sftp", "root": "/target", "pattern": "financials_{region}.csv"},
                },
            },
            "enabled": True,
        })
        run = RunRepository(db).create_run(
            run_id="run-sftp-multi-file-export",
            source_env="qa",
            target_env="prod",
            config_snapshot={"compare_request_type": "unknown", "request": {}, "job_sequence": ["regional_sales_recon_sftp"]},
        )
        run_id = run.run_id

    out_path = tmp_path / "diffs.jsonl"
    with _db_module.SessionLocal() as db:
        run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
        row_count = de.write_recomputed_differences(db, run, "json", out_path)

    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").strip().splitlines()]
    assert row_count == 1
    assert json.loads(rows[0]["key_values"])["__pair__"] == {"region": "east"}
    assert rows[0]["column_name"] == "amount"
