"""``crushftp`` locations through the real job executors (file_transfer and
file_watcher), against the in-process fake server."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
from etl_framework.repository.repository import FileServerProfileRepository
from etl_framework.runner.state import TestStatus
from tests.helpers.fake_crushftp import FakeCrushFtp


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # RemoteFileSourceSession resolves profiles on its own SessionLocal().
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setenv("CRUSHFTP_ALLOW_INSECURE_HTTP", "1")
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


@pytest.fixture
def server():
    with FakeCrushFtp() as fake:
        fake.dirs.add("/data/in")
        fake.files["/data/in/sales_east.csv"] = b"id\n1\n"
        fake.files["/data/in/sales_west.csv"] = b"id\n2\n22\n"
        fake.files["/data/in/ignore.txt"] = b"nope"
        yield fake


def _profile(db, server, name="crush", password="atompass"):
    FileServerProfileRepository(db).create({
        "name": name, "kind": "crushftp", "protocol": "https", "endpoint_url": server.base_url,
        "auth_method": "password", "username": "atom", "password": password,
    })


def _executor(db):
    return RunExecutor(
        db=db, run_id="run-1", source_env="qa", target_env="prod",
        job_sequence=[], run_settings=RunSettings(use_live_connections=True),
        config_snapshot={},
    )


def test_file_transfer_from_crushftp_to_local(db_session, allowed_dir, server):
    _profile(db_session, server)
    job = JobDefinition(name="pull_sales", job_type="file_transfer", params={
        "source": {"kind": "crushftp", "root": "/data/in", "pattern": "sales_{region}.csv", "credentials_ref": "crush"},
        "destination": {"kind": "local", "root": str(allowed_dir / "dst")},
    })

    result = _executor(db_session)._build_case(job)()

    assert result.status == TestStatus.PASSED, result.mismatch_summary
    assert result.mismatch_summary["copied"] == 2
    assert (allowed_dir / "dst" / "sales_west.csv").read_bytes() == b"id\n2\n22\n"
    assert sorted(p.name for p in (allowed_dir / "dst").iterdir()) == ["sales_east.csv", "sales_west.csv"]


def test_file_transfer_from_local_to_crushftp(db_session, allowed_dir, server):
    _profile(db_session, server)
    (allowed_dir / "src").mkdir()
    (allowed_dir / "src" / "out_a.csv").write_bytes(b"a,b\n1,2\n")
    job = JobDefinition(name="push_out", job_type="file_transfer", params={
        "source": {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "out_*.csv"},
        "destination": {"kind": "crushftp", "root": "/data/outbound/today", "credentials_ref": "crush"},
    })

    result = _executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED, result.mismatch_summary
    assert server.files["/data/outbound/today/out_a.csv"] == b"a,b\n1,2\n"
    assert [path for path in server.files if path.endswith(".part")] == []


def test_file_transfer_with_a_rejected_login_is_an_error_without_the_password(db_session, allowed_dir, server):
    _profile(db_session, server, password="wr0ng-secret")
    job = JobDefinition(name="pull_sales", job_type="file_transfer", params={
        "source": {"kind": "crushftp", "root": "/data/in", "pattern": "*.csv", "credentials_ref": "crush"},
        "destination": {"kind": "local", "root": str(allowed_dir / "dst")},
    })

    result = _executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.ERROR
    assert "wr0ng-secret" not in json.dumps(result.mismatch_summary)
    assert "login" in json.dumps(result.mismatch_summary)


def test_file_watcher_finds_a_file_on_crushftp(db_session, server):
    _profile(db_session, server)
    job = JobDefinition(name="wait_sales", job_type="file_watcher", params={
        "location": {"kind": "crushftp", "root": "/data/in", "pattern": "sales_east.csv", "credentials_ref": "crush"},
        "poll_interval_seconds": 0.01, "max_tries": 2,
    })

    result = _executor(db_session)._execute_file_watcher(job)

    assert result.status == TestStatus.PASSED, result.mismatch_summary
    assert result.source_file_name == "sales_east.csv"


def test_file_watcher_times_out_when_the_file_never_arrives(db_session, server):
    _profile(db_session, server)
    job = JobDefinition(name="wait_missing", job_type="file_watcher", params={
        "location": {"kind": "crushftp", "root": "/data/in", "pattern": "never_*.csv", "credentials_ref": "crush"},
        "poll_interval_seconds": 0.01, "max_tries": 2,
    })

    result = _executor(db_session)._execute_file_watcher(job)

    assert result.status != TestStatus.PASSED
