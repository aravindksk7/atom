# tests/unit/test_multi_file_remote.py
from __future__ import annotations

import pandas as pd
import pytest

from api.services.multi_file_remote import (
    RemoteFileSourceSession,
    resolve_file_source_credentials,
)
from etl_framework.reconciliation.file_mapping import FileSourceSpec


def test_resolve_file_source_credentials_from_config_snapshot_by_ref() -> None:
    config_snapshot = {
        "file_source_credentials": {
            "sftp_source": {"host": "sftp.internal", "port": 22, "username": "svc", "password": "secret"},
        },
    }
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="sftp_source")

    creds = resolve_file_source_credentials(config_snapshot, spec)

    assert creds == {"host": "sftp.internal", "port": 22, "username": "svc", "password": "secret"}


def test_resolve_file_source_credentials_returns_empty_without_ref() -> None:
    spec = FileSourceSpec(kind="local", root="/source", pattern="*.csv")
    assert resolve_file_source_credentials({"file_source_credentials": {"x": {"a": 1}}}, spec) == {}


def test_resolve_file_source_credentials_returns_empty_for_unknown_ref() -> None:
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="does_not_exist")
    assert resolve_file_source_credentials({"file_source_credentials": {}}, spec) == {}


class _FakeS3Client:
    build_calls = 0

    def __init__(self) -> None:
        self.objects = {"prefix/sales_east.csv": b"id,value\n1,alpha\n"}
        self.closed = False

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        prefix = kwargs["Prefix"]
        return [{"Contents": [{"Key": key} for key in self.objects if key.startswith(prefix)]}]

    def get_object(self, **kwargs):
        class _Body:
            def __init__(self, raw: bytes) -> None:
                self._raw = raw

            def read(self) -> bytes:
                return self._raw

        return {"Body": _Body(self.objects[kwargs["Key"]])}

    def close(self) -> None:
        self.closed = True


def test_remote_file_source_session_reuses_one_s3_client_across_discover_and_reads(monkeypatch) -> None:
    """The whole point of RemoteFileSourceSession: N file reads against the
    same source spec must not open N connections."""
    built_clients: list[_FakeS3Client] = []

    def _fake_build_s3_client(config_snapshot, spec):
        client = _FakeS3Client()
        built_clients.append(client)
        return client

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="sales_{region}.csv")
    session = RemoteFileSourceSession({})

    discovered = session.discover(spec)
    assert len(discovered) == 1
    df = session.read_file(discovered[0], spec)
    assert isinstance(df, pd.DataFrame)
    # A second read against the same spec must reuse the cached client too.
    session.read_file(discovered[0], spec)

    assert len(built_clients) == 1  # exactly one client built for this (kind, credentials_ref)
    session.close()
    assert built_clients[0].closed is True


def test_remote_file_source_session_builds_separate_clients_per_credentials_ref(monkeypatch) -> None:
    built = []

    def _fake_build_s3_client(config_snapshot, spec):
        client = _FakeS3Client()
        built.append((spec.credentials_ref, client))
        return client

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    session = RemoteFileSourceSession({})
    spec_a = FileSourceSpec(kind="s3", root="s3://bucket/a", pattern="*.csv", credentials_ref="ref_a")
    spec_b = FileSourceSpec(kind="s3", root="s3://bucket/b", pattern="*.csv", credentials_ref="ref_b")

    session.discover(spec_a)
    session.discover(spec_b)
    session.discover(spec_a)  # reuses ref_a's client, not a third build

    assert len(built) == 2
    assert {ref for ref, _ in built} == {"ref_a", "ref_b"}


def test_remote_file_source_session_local_kind_needs_no_client(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "sales_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")

    session = RemoteFileSourceSession({})
    spec = FileSourceSpec(kind="local", root=str(tmp_path), pattern="sales_{region}.csv")

    discovered = session.discover(spec)
    assert len(discovered) == 1
    df = session.read_file(discovered[0], spec)
    assert list(df.columns) == ["id", "value"]

    session.close()  # no-op: no remote clients were ever built


def test_remote_file_source_session_rejects_unknown_kind() -> None:
    # FileSourceSpec's own constructor doesn't validate `kind` (that check
    # lives in _parse_file_source, the config-parsing entry point) -- this
    # exercises RemoteFileSourceSession's own defense-in-depth check.
    session = RemoteFileSourceSession({})
    spec = FileSourceSpec(kind="ftp", root="ftp://x", pattern="*.csv")

    with pytest.raises(ValueError, match="Unsupported multi_file source kind"):
        session.discover(spec)


def test_remote_file_source_session_context_manager_closes_clients(monkeypatch) -> None:
    built_clients: list[_FakeS3Client] = []

    def _fake_build_s3_client(config_snapshot, spec):
        client = _FakeS3Client()
        built_clients.append(client)
        return client

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="sales_{region}.csv")
    with RemoteFileSourceSession({}) as session:
        session.discover(spec)

    assert built_clients[0].closed is True


def test_build_s3_client_forces_path_style_addressing_for_custom_endpoint(monkeypatch) -> None:
    """A custom endpoint_url means a non-AWS S3-compatible target (MinIO, on-prem
    object storage) -- these commonly reject virtual-hosted-style bucket addressing,
    which boto3 otherwise defaults to whenever endpoint_url is set. Real AWS never
    sets endpoint_url, so this must not fire for the existing real-AWS path."""
    import boto3
    from api.services.multi_file_remote import build_s3_client
    from etl_framework.reconciliation.file_mapping import FileSourceSpec

    captured_kwargs: dict = {}

    def _capture(service_name, **kwargs):
        captured_kwargs.update(kwargs)
        return object()  # build_s3_client only returns this; no real network call happens

    monkeypatch.setattr(boto3, "client", _capture)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="*.csv", credentials_ref="minio")
    config_snapshot = {
        "file_source_credentials": {
            "minio": {
                "aws_access_key_id": "minioadmin",
                "aws_secret_access_key": "minioadmin",
                "endpoint_url": "http://127.0.0.1:19000",
                "region_name": "us-east-1",
            },
        },
    }

    build_s3_client(config_snapshot, spec)

    assert captured_kwargs["endpoint_url"] == "http://127.0.0.1:19000"
    assert captured_kwargs["config"].s3["addressing_style"] == "path"


def test_build_s3_client_does_not_force_path_style_without_custom_endpoint(monkeypatch) -> None:
    """Real AWS (no endpoint_url set) must keep boto3's default addressing --
    this is the existing, already-working production path; it must not regress."""
    import boto3
    from api.services.multi_file_remote import build_s3_client
    from etl_framework.reconciliation.file_mapping import FileSourceSpec

    captured_kwargs: dict = {}

    def _capture(service_name, **kwargs):
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", _capture)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="*.csv", credentials_ref="aws_prod")
    config_snapshot = {
        "file_source_credentials": {
            "aws_prod": {"aws_access_key_id": "AKIA...", "aws_secret_access_key": "s3cr3t"},
        },
    }

    build_s3_client(config_snapshot, spec)

    assert captured_kwargs.get("endpoint_url") is None
    assert "config" not in captured_kwargs
