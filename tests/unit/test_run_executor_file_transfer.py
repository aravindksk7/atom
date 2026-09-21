from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
from etl_framework.runner.state import TestStatus
from tests.helpers.fake_sftp import FakeSFTP


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # RemoteFileSourceSession resolves profiles on its own SessionLocal().
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        yield db


@pytest.fixture
def allowed_dir(tmp_path, monkeypatch):
    from api.services import file_source

    base = tmp_path / "allowed"
    base.mkdir()
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", base.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (base.resolve(),))
    return base.resolve()


def executor(db_session):
    return RunExecutor(
        db=db_session, run_id="run-1", source_env="qa", target_env="prod",
        job_sequence=[], run_settings=RunSettings(use_live_connections=True),
        config_snapshot={},
    )


def transfer_job(source, destination, **params):
    return JobDefinition(
        name="stage_sales",
        job_type="file_transfer",
        params={"source": source, "destination": destination, **params},
    )


def local_job(allowed_dir: Path, **params):
    return transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
        **params,
    )


def seed_sources(allowed_dir: Path):
    src = allowed_dir / "src"
    src.mkdir()
    (src / "sales_east.csv").write_bytes(b"id\n1\n")
    (src / "sales_west.csv").write_bytes(b"id\n2\n22\n")
    (src / "ignore.txt").write_bytes(b"nope")


def test_build_case_dispatches_file_transfer_job_type(db_session, allowed_dir):
    seed_sources(allowed_dir)
    result = executor(db_session)._build_case(local_job(allowed_dir))()
    assert result.status == TestStatus.PASSED


def test_local_to_local_copies_matching_files_and_reports_summary(db_session, allowed_dir):
    seed_sources(allowed_dir)

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.PASSED
    assert sorted(p.name for p in (allowed_dir / "dst").iterdir()) == ["sales_east.csv", "sales_west.csv"]
    assert (allowed_dir / "dst" / "sales_west.csv").read_bytes() == b"id\n2\n22\n"
    assert result.mismatch_summary["copied"] == 2
    assert result.mismatch_summary["skipped"] == 0
    assert result.mismatch_summary["bytes"] == 13
    assert result.mismatch_summary["files_truncated"] is False
    assert result.source_row_count == 2 and result.matched_count == 2
    assert result.data_artifact_path == str(allowed_dir / "dst")
    assert result.mismatches == []


def test_second_run_with_default_on_exists_fails_and_reports_collision(db_session, allowed_dir):
    seed_sources(allowed_dir)
    executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.FAILED
    assert "already contains 2 file(s)" in result.mismatch_summary["error"]
    assert result.data_artifact_path is None
    assert len(result.mismatches) == 1


def test_second_run_with_skip_is_idempotent(db_session, allowed_dir):
    seed_sources(allowed_dir)
    executor(db_session)._execute_file_transfer(local_job(allowed_dir, on_exists="skip"))

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir, on_exists="skip"))

    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["copied"] == 0
    assert result.mismatch_summary["skipped"] == 2


def test_overwrite_replaces_changed_destination_file(db_session, allowed_dir):
    seed_sources(allowed_dir)
    executor(db_session)._execute_file_transfer(local_job(allowed_dir))
    (allowed_dir / "src" / "sales_east.csv").write_bytes(b"id\n1\n99\n")

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir, on_exists="overwrite"))

    assert result.status == TestStatus.PASSED
    assert (allowed_dir / "dst" / "sales_east.csv").read_bytes() == b"id\n1\n99\n"


def test_no_matching_files_fails_with_clear_message(db_session, allowed_dir):
    (allowed_dir / "src").mkdir()

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.FAILED
    assert "no files matching 'sales_{region}.csv'" in result.mismatch_summary["error"]


def test_recursive_with_preserve_structure_keeps_subfolders(db_session, allowed_dir):
    src = allowed_dir / "src"
    (src / "2026" / "09").mkdir(parents=True)
    (src / "top.csv").write_bytes(b"1")
    (src / "2026" / "09" / "deep.csv").write_bytes(b"22")
    job = transfer_job(
        {"kind": "local", "root": str(src), "pattern": "*.csv"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
        recursive=True, preserve_structure=True,
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED
    assert (allowed_dir / "dst" / "top.csv").read_bytes() == b"1"
    assert (allowed_dir / "dst" / "2026" / "09" / "deep.csv").read_bytes() == b"22"


def test_destination_outside_allowlist_is_an_error_not_a_failure(db_session, allowed_dir, tmp_path):
    seed_sources(allowed_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    job = transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "local", "root": str(outside)},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.ERROR
    assert "Invalid file path" in result.mismatch_summary["error"]
    assert list(outside.iterdir()) == []


def test_local_to_s3_uploads_objects(db_session, allowed_dir, monkeypatch):
    seed_sources(allowed_dir)
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="bkt")
        monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db, spec: None)
        monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", lambda profile, spec: raw)
        job = transfer_job(
            {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
            {"kind": "s3", "root": "s3://bkt/staged", "credentials_ref": "aws"},
        )

        result = executor(db_session)._execute_file_transfer(job)

        assert result.status == TestStatus.PASSED
        keys = sorted(o["Key"] for o in raw.list_objects_v2(Bucket="bkt")["Contents"])
        assert keys == ["staged/sales_east.csv", "staged/sales_west.csv"]
        assert raw.get_object(Bucket="bkt", Key="staged/sales_east.csv")["Body"].read() == b"id\n1\n"


def test_s3_to_local_downloads_objects(db_session, allowed_dir, monkeypatch):
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="bkt")
        raw.put_object(Bucket="bkt", Key="in/sales_east.csv", Body=b"id\n1\n")
        raw.put_object(Bucket="bkt", Key="in/readme.txt", Body=b"skip")
        monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db, spec: None)
        monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", lambda profile, spec: raw)
        job = transfer_job(
            {"kind": "s3", "root": "s3://bkt/in", "pattern": "sales_{region}.csv", "credentials_ref": "aws"},
            {"kind": "local", "root": str(allowed_dir / "dst")},
        )

        result = executor(db_session)._execute_file_transfer(job)

        assert result.status == TestStatus.PASSED
        assert (allowed_dir / "dst" / "sales_east.csv").read_bytes() == b"id\n1\n"


def test_sftp_to_s3_remote_to_remote(db_session, monkeypatch):
    fake = FakeSFTP()
    fake.dirs.add("/exports")
    fake.files["/exports/sales_east.csv"] = b"id\n1\n"
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="bkt")
        monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db, spec: None)
        monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", lambda profile, spec: raw)
        monkeypatch.setattr("api.services.multi_file_remote.build_sftp_client", lambda profile, spec: fake)
        job = transfer_job(
            {"kind": "scp", "root": "/exports", "pattern": "sales_{region}.csv", "credentials_ref": "vendor"},
            {"kind": "s3", "root": "s3://bkt/mirror", "credentials_ref": "aws"},
        )

        result = executor(db_session)._execute_file_transfer(job)

        assert result.status == TestStatus.PASSED
        assert raw.get_object(Bucket="bkt", Key="mirror/sales_east.csv")["Body"].read() == b"id\n1\n"


def test_local_to_sftp_writes_through_part_file(db_session, allowed_dir, monkeypatch):
    seed_sources(allowed_dir)
    fake = FakeSFTP()
    monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db, spec: None)
    monkeypatch.setattr("api.services.multi_file_remote.build_sftp_client", lambda profile, spec: fake)
    job = transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "sftp", "root": "/inbound", "credentials_ref": "vendor"},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED
    assert fake.files == {
        "/inbound/sales_east.csv": b"id\n1\n",
        "/inbound/sales_west.csv": b"id\n2\n22\n",
    }


def test_missing_profile_reference_is_an_error(db_session, allowed_dir):
    seed_sources(allowed_dir)
    job = transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "sftp", "root": "/inbound", "credentials_ref": "does-not-exist"},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.ERROR
    assert "No file server profile named 'does-not-exist'" in result.mismatch_summary["error"]


def test_summary_caps_file_list_but_reports_true_counts(db_session, allowed_dir):
    src = allowed_dir / "src"
    src.mkdir()
    for index in range(105):
        (src / f"sales_{index:03d}.csv").write_bytes(b"x")
    job = transfer_job(
        {"kind": "local", "root": str(src), "pattern": "sales_{n}.csv"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["copied"] == 105
    assert len(result.mismatch_summary["files"]) == 100
    assert result.mismatch_summary["files_truncated"] is True
