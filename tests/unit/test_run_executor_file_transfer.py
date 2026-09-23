from __future__ import annotations

import json
from pathlib import Path
import types

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


def transfer_job(source, destination, name: str = "stage_sales", **params):
    return JobDefinition(
        name=name,
        job_type="file_transfer",
        params={"source": source, "destination": destination, **params},
    )


def local_job(allowed_dir: Path, name: str = "stage_sales", **params):
    return transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
        name=name,
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
    assert result.mismatch_summary["copied"] == 2
    assert sorted(p.name for p in (allowed_dir / "dst").iterdir()) == ["sales_east.csv", "sales_west.csv"]


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
    # Run-level Compare row-diff counts data_artifact_path values, so a
    # transfer must leave it unset and report the destination in the summary.
    assert result.data_artifact_path is None
    assert result.mismatch_summary["destination_root"] == str(allowed_dir / "dst")
    assert set(result.mismatch_summary["files"][0]) == {"source", "destination", "bytes", "action"}
    json.dumps(result.mismatch_summary)
    assert result.mismatches == []


def test_second_run_with_default_on_exists_fails_and_reports_collision(db_session, allowed_dir):
    seed_sources(allowed_dir)
    executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.FAILED
    assert "already contains 2 file(s)" in result.mismatch_summary["error"]
    assert result.mismatch_summary["destination_root"] == str(allowed_dir / "dst")
    assert result.data_artifact_path is None
    assert len(result.mismatches) == 1


def test_mid_transfer_failure_reports_failed_file_and_partial_counts(db_session, allowed_dir, monkeypatch):
    from api.services import file_transfer

    seed_sources(allowed_dir)
    real_copy_one = file_transfer._copy_one
    calls = []

    def flaky_copy_one(entry, source, destination):
        calls.append(entry.source.path)
        if len(calls) == 2:
            raise OSError("boom")
        return real_copy_one(entry, source, destination)

    monkeypatch.setattr(file_transfer, "_copy_one", flaky_copy_one)

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.FAILED
    assert result.data_artifact_path is None
    assert result.mismatch_summary["failed_file"] == calls[1]
    assert result.mismatch_summary["copied"] == 1
    assert result.mismatch_summary["destination_root"] == str(allowed_dir / "dst")
    assert "boom" in result.mismatch_summary["error"]
    assert len(result.mismatches) == 1


def _run_with_second_copy_raising(db_session, allowed_dir, monkeypatch, exc):
    from api.services import file_transfer

    seed_sources(allowed_dir)
    real_copy_one = file_transfer._copy_one
    calls = []

    def flaky_copy_one(entry, source, destination):
        calls.append(entry.source.path)
        if len(calls) == 2:
            raise exc
        return real_copy_one(entry, source, destination)

    monkeypatch.setattr(file_transfer, "_copy_one", flaky_copy_one)
    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))
    return result, calls


def test_transport_failure_mid_transfer_is_an_error_with_partial_summary(db_session, allowed_dir, monkeypatch):
    import paramiko

    result, calls = _run_with_second_copy_raising(
        db_session, allowed_dir, monkeypatch, paramiko.SSHException("dropped"),
    )

    assert result.status == TestStatus.ERROR
    assert result.data_artifact_path is None
    assert result.mismatch_summary["copied"] == 1
    assert result.mismatch_summary["failed_file"] == calls[1]
    assert result.mismatch_summary["destination_root"] == str(allowed_dir / "dst")
    assert "dropped" in result.mismatch_summary["error"]
    assert len(result.mismatches) == 1


def test_credential_failure_mid_transfer_is_an_error(db_session, allowed_dir, monkeypatch):
    import botocore.exceptions

    forbidden = botocore.exceptions.ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "PutObject")
    result, calls = _run_with_second_copy_raising(db_session, allowed_dir, monkeypatch, forbidden)

    assert result.status == TestStatus.ERROR
    assert result.mismatch_summary["copied"] == 1
    assert result.mismatch_summary["failed_file"] == calls[1]


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
    assert result.mismatch_summary["destination_root"] == str(allowed_dir / "dst")
    assert result.data_artifact_path is None


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
    assert result.mismatch_summary["destination_root"] == str(outside)
    assert result.data_artifact_path is None
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


# -- retry inside one run ----------------------------------------------------
#
# A transport failure mid-copy is ERROR, which the DAG executor retries when the
# step sets max_retries. The retry re-plans, so the files the earlier attempt
# already wrote must not trip the on_exists="fail" collision check.

def _fail_second_copy_with(monkeypatch, exc):
    """Patch _copy_one so the n-th copy across the whole test raises. Returns
    the list of source paths it was asked to copy, in order."""
    from api.services import file_transfer

    real_copy_one = file_transfer._copy_one
    calls: list[str] = []

    def flaky_copy_one(entry, source, destination):
        calls.append(entry.source.path)
        if len(calls) == 2:
            raise exc
        return real_copy_one(entry, source, destination)

    monkeypatch.setattr(file_transfer, "_copy_one", flaky_copy_one)
    return calls, real_copy_one


def test_retry_after_a_partial_transfer_resumes_instead_of_colliding(db_session, allowed_dir, monkeypatch):
    import paramiko
    from api.services import file_transfer

    seed_sources(allowed_dir)
    run_executor = executor(db_session)
    calls, real_copy_one = _fail_second_copy_with(monkeypatch, paramiko.SSHException("dropped"))

    first = run_executor._execute_file_transfer(local_job(allowed_dir))
    assert first.status == TestStatus.ERROR
    assert first.mismatch_summary["copied"] == 1

    monkeypatch.setattr(file_transfer, "_copy_one", real_copy_one)
    second = run_executor._execute_file_transfer(local_job(allowed_dir))

    assert second.status == TestStatus.PASSED
    assert "already contains" not in str(second.mismatch_summary.get("error", ""))
    assert second.mismatch_summary["copied"] == 1
    assert second.mismatch_summary["skipped"] == 1
    assert sorted(p.name for p in (allowed_dir / "dst").iterdir()) == [
        "sales_east.csv", "sales_west.csv",
    ]


def test_resume_set_is_not_shared_between_jobs(db_session, allowed_dir, monkeypatch):
    import paramiko
    from api.services import file_transfer

    seed_sources(allowed_dir)
    run_executor = executor(db_session)
    _, real_copy_one = _fail_second_copy_with(monkeypatch, paramiko.SSHException("dropped"))

    assert run_executor._execute_file_transfer(local_job(allowed_dir)).status == TestStatus.ERROR

    monkeypatch.setattr(file_transfer, "_copy_one", real_copy_one)
    other = run_executor._execute_file_transfer(local_job(allowed_dir, name="stage_sales_copy"))

    # A different job did not write that file, so it is still a real collision.
    assert other.status == TestStatus.FAILED
    assert "already contains 1 file(s)" in other.mismatch_summary["error"]


def test_a_new_run_does_not_inherit_the_resume_set(db_session, allowed_dir, monkeypatch):
    import paramiko
    from api.services import file_transfer

    seed_sources(allowed_dir)
    _, real_copy_one = _fail_second_copy_with(monkeypatch, paramiko.SSHException("dropped"))

    assert executor(db_session)._execute_file_transfer(local_job(allowed_dir)).status == TestStatus.ERROR

    monkeypatch.setattr(file_transfer, "_copy_one", real_copy_one)
    # A fresh RunExecutor is what a brand-new run gets: nothing to resume from.
    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.FAILED
    assert "already contains 1 file(s)" in result.mismatch_summary["error"]


def test_dag_retry_of_a_file_transfer_step_ends_passed(db_session, allowed_dir, monkeypatch):
    """The real retry path: DagExecutor re-invokes the step, which builds a new
    worker RunExecutor each attempt, so the resume state has to live on the
    run-level executor that survives both attempts."""
    import paramiko
    from api.routes.jobs import _job_to_data
    from api.schemas import SequenceStepRef
    from etl_framework.repository.repository import JobRepository, RunRepository, RunStepRepository

    seed_sources(allowed_dir)
    JobRepository(db_session).create(_job_to_data(local_job(allowed_dir)))
    RunRepository(db_session).create_run("run-retry", "qa", "", {})
    # The patch fires on the second copy overall, so attempt 1 copies one file
    # and errors, and attempt 2 copies the rest.
    _fail_second_copy_with(monkeypatch, paramiko.SSHException("dropped"))

    RunExecutor(
        db=db_session,
        run_id="run-retry",
        source_env="qa",
        target_env="",
        job_sequence=[SequenceStepRef(step_id="s1", job_name="stage_sales", max_retries=1)],
        run_settings=RunSettings(use_live_connections=True, retry_delay_seconds=0),
        config_snapshot={},
    ).execute()

    row = RunStepRepository(db_session).get_step_by_step_id("run-retry", "s1")
    assert row.status == "PASSED"
    assert row.attempt == 2
    assert sorted(p.name for p in (allowed_dir / "dst").iterdir()) == [
        "sales_east.csv", "sales_west.csv",
    ]


def test_the_same_job_twice_in_one_run_still_collides_on_the_second_step(db_session, allowed_dir):
    """Resume state belongs to the step, not the job: a DAG may legitimately
    run one job twice (see SequenceStepRef), and the second occurrence must
    still see the first occurrence's output as an on_exists="fail" collision."""
    from api.routes.jobs import _job_to_data
    from etl_framework.repository.models import TestResult
    from etl_framework.repository.repository import JobRepository, RunRepository, RunStepRepository

    seed_sources(allowed_dir)
    JobRepository(db_session).create(_job_to_data(local_job(allowed_dir)))
    RunRepository(db_session).create_run("run-twice", "qa", "", {})

    RunExecutor(
        db=db_session,
        run_id="run-twice",
        source_env="qa",
        target_env="",
        job_sequence=["stage_sales", "stage_sales"],
        run_settings=RunSettings(use_live_connections=True),
        config_snapshot={},
    ).execute()

    rows = RunStepRepository(db_session).list_steps("run-twice")
    assert [(row.step_id, row.status) for row in rows] == [
        ("step_0", "PASSED"), ("step_1", "FAILED"),
    ]
    second = (
        db_session.query(TestResult)
        .filter_by(run_id="run-twice").order_by(TestResult.id).all()[1]
    )
    assert "already contains 2 file(s)" in second.mismatch_summary["error"]
    assert second.mismatch_summary["copied"] == 0
    # Nothing new was written, and the first step's copies are untouched.
    assert (allowed_dir / "dst" / "sales_east.csv").read_bytes() == b"id\n1\n"


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


def test_smb_to_local_copies_files_end_to_end(db_session, allowed_dir, tmp_path, monkeypatch):
    from etl_framework.repository.repository import FileServerProfileRepository

    smb_src = tmp_path / "smb_src"
    smb_src.mkdir()
    (smb_src / "sales_east.csv").write_bytes(b"id\n1\n")

    FileServerProfileRepository(db_session).create({
        "name": "vendor-share", "kind": "smb", "host": "fileserver01",
        "username": "svc", "password": "s3cret",
    })
    net_use_calls = []
    monkeypatch.setattr("api.services.multi_file_remote.os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr("api.services.multi_file_remote._smb_host_locks", {})
    monkeypatch.setattr(
        "api.services.multi_file_remote._net_use",
        lambda resource, u, p, port=None: net_use_calls.append((resource, u, p)),
    )
    monkeypatch.setattr("api.services.multi_file_remote._net_use_delete", lambda resource: None)
    monkeypatch.setattr("api.services.multi_file_remote.parse_unc_root", lambda root: ("fileserver01", "share"))
    monkeypatch.setattr("api.services.file_transfer.parse_unc_root", lambda root: ("fileserver01", "share"))

    job = transfer_job(
        {"kind": "smb", "root": str(smb_src), "pattern": "sales_{region}.csv", "credentials_ref": "vendor-share"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED
    assert (allowed_dir / "dst" / "sales_east.csv").read_bytes() == b"id\n1\n"
    assert result.mismatch_summary["copied"] == 1
    assert net_use_calls == [(r"\\fileserver01\share", "svc", "s3cret")]
