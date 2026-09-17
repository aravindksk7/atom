# tests/unit/test_config_bundle.py
from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401 -- registers ORM models with Base
from etl_framework.repository.repository import ConfigRepository, FileServerProfileRepository
from api.services.config_bundle import BUNDLE_VERSION, build_bundle
from etl_framework.repository.repository import JobRepository, JobSelectionRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
from api.services.config_bundle import apply_bundle


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)
    yield key
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    importlib.reload(secret_store)


def _db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_build_bundle_sets_version_and_empty_lists_for_unselected_types():
    db = _db()
    bundle = build_bundle(db, {})
    assert bundle["bundle_version"] == BUNDLE_VERSION
    assert bundle["file_servers"] == []
    assert bundle["configs"] == []
    assert bundle["jobs"] == []
    assert bundle["sequences"] == []
    assert bundle["selections"] == []


def test_build_bundle_strips_config_secrets():
    db = _db()
    ConfigRepository(db).create(
        name="dev", env_name="dev",
        config_data={"db_host": "localhost", "db_password": "hunter2"},
    )
    bundle = build_bundle(db, {"configs": ["dev"]})
    assert len(bundle["configs"]) == 1
    entry = bundle["configs"][0]
    assert entry["name"] == "dev"
    assert entry["env_name"] == "dev"
    assert entry["config_data"]["db_host"] == "localhost"
    assert entry["config_data"]["db_password"] is None


def test_build_bundle_strips_file_server_secrets():
    db = _db()
    FileServerProfileRepository(db).create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })
    bundle = build_bundle(db, {"file_servers": ["sftp_inbound"]})
    assert len(bundle["file_servers"]) == 1
    entry = bundle["file_servers"][0]
    assert entry["name"] == "sftp_inbound"
    assert entry["host"] == "sftp.internal"
    assert entry["password"] is None


def test_build_bundle_skips_unknown_names_silently():
    db = _db()
    bundle = build_bundle(db, {"configs": ["does-not-exist"]})
    assert bundle["configs"] == []


def test_apply_bundle_rejects_unsupported_version():
    db = _db()
    with pytest.raises(ValueError, match="bundle_version"):
        apply_bundle(db, {"bundle_version": 999})


def test_apply_bundle_creates_file_server_and_config_with_blank_secrets():
    db = _db()
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "file_servers": [{
            "name": "sftp_inbound", "kind": "sftp", "description": "", "host": "sftp.internal",
            "port": 22, "username": "svc", "auth_method": "password", "password": None,
            "private_key": None, "key_passphrase": None, "host_key_fingerprint": None,
            "aws_access_key_id": None, "aws_secret_access_key": None, "aws_session_token": None,
            "region_name": None, "endpoint_url": None,
        }],
        "configs": [{"name": "dev", "env_name": "dev", "config_data": {"db_host": "localhost", "db_password": None}}],
        "jobs": [], "sequences": [], "selections": [],
    }
    results = apply_bundle(db, bundle)
    statuses = {(r.type, r.name): r.status for r in results}
    assert statuses[("file_servers", "sftp_inbound")] == "created"
    assert statuses[("configs", "dev")] == "created"
    assert FileServerProfileRepository(db).get_by_name("sftp_inbound").password is None
    assert ConfigRepository(db).get_by_name("dev").config_json["db_password"] is None


def test_apply_bundle_skips_existing_by_name():
    db = _db()
    ConfigRepository(db).create(name="dev", env_name="dev", config_data={})
    bundle = {
        "bundle_version": BUNDLE_VERSION, "file_servers": [], "jobs": [], "sequences": [], "selections": [],
        "configs": [{"name": "dev", "env_name": "dev", "config_data": {}}],
    }
    results = apply_bundle(db, bundle)
    assert results[0].status == "skipped"
    assert results[0].reason == "already exists"


def test_apply_bundle_creates_job():
    db = _db()
    bundle = {
        "bundle_version": BUNDLE_VERSION, "file_servers": [], "configs": [], "sequences": [], "selections": [],
        "jobs": [{
            "name": "orders_check", "description": "", "tags": [], "job_type": "reconciliation",
            "query": "SELECT 1", "key_columns": ["id"], "exclude_columns": [], "source_env": None,
            "target_env": None, "params": {}, "enabled": True, "rules": [], "depends_on": [], "pass_condition": None,
        }],
    }
    results = apply_bundle(db, bundle)
    assert results[0].status == "created"
    assert JobRepository(db).get("orders_check") is not None


def test_apply_bundle_resolves_sequence_config_name_and_errors_if_missing():
    db = _db()
    ConfigRepository(db).create(name="dev", env_name="dev", config_data={})
    bundle = {
        "bundle_version": BUNDLE_VERSION, "file_servers": [], "configs": [], "jobs": [], "selections": [],
        "sequences": [
            {"name": "seq_ok", "description": "", "tags": [], "steps": [], "preconditions": None,
             "defaults": {"config_name": "dev"}},
            {"name": "seq_missing_config", "description": "", "tags": [], "steps": [], "preconditions": None,
             "defaults": {"config_name": "nope"}},
        ],
    }
    results = apply_bundle(db, bundle)
    by_name = {r.name: r for r in results}
    assert by_name["seq_ok"].status == "created"
    dev_id = ConfigRepository(db).get_by_name("dev").id
    seq = ExecutionSequenceRepository(db).get_by_name("seq_ok")
    version = ExecutionSequenceRepository(db).latest_version(seq.id)
    assert version.defaults_json["config_id"] == dev_id
    assert by_name["seq_missing_config"].status == "error"


def test_apply_bundle_resolves_selection_sequence_ref_by_name():
    db = _db()
    ExecutionSequenceRepository(db).create(name="seq1", description="", tags=[], steps=[])
    bundle = {
        "bundle_version": BUNDLE_VERSION, "file_servers": [], "configs": [], "jobs": [], "sequences": [],
        "selections": [{
            "name": "sel1", "description": "", "tags": [], "job_sequence": [], "run_settings": {},
            "config_name": None, "sequence_ref": {"sequence_name": "seq1"},
        }],
    }
    results = apply_bundle(db, bundle)
    assert results[0].status == "created"
    seq1_id = ExecutionSequenceRepository(db).get_by_name("seq1").id
    sel = JobSelectionRepository(db).get_by_name("sel1")
    version = JobSelectionRepository(db).latest_version(sel.id)
    assert version.sequence_ref == {"sequence_id": seq1_id, "sequence_version": None}
