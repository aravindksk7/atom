from __future__ import annotations

import io
from pathlib import Path

import boto3
import pytest
from fastapi import HTTPException
from moto import mock_aws

from api.services import file_transfer as ft
from etl_framework.reconciliation.file_mapping import DiscoveredFile
from tests.helpers.fake_sftp import FakeSFTP


@pytest.fixture
def allowed_dir(tmp_path, monkeypatch):
    """Restrict the server-side file allowlist to <tmp>/allowed."""
    from api.services import file_source

    base = tmp_path / "allowed"
    base.mkdir()
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", base.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (base.resolve(),))
    return base.resolve()


class _Boom:
    """Stream that yields one chunk, then fails like a dropped connection."""

    def __init__(self) -> None:
        self.calls = 0

    def read(self, size: int = -1) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return b"partial"
        raise OSError("connection reset")


# -- LocalEndpoint -----------------------------------------------------------

def test_local_endpoint_rejects_root_outside_allowlist(allowed_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(HTTPException) as excinfo:
        ft.LocalEndpoint(str(outside))
    assert "Invalid file path" in str(excinfo.value.detail)


def test_local_endpoint_relative_of_is_posix_relative_to_root(allowed_dir):
    (allowed_dir / "src" / "sub").mkdir(parents=True)
    endpoint = ft.LocalEndpoint(str(allowed_dir / "src"))
    file = DiscoveredFile(path=str(allowed_dir / "src" / "sub" / "a.csv"), file_name="a.csv", tokens={})
    assert endpoint.relative_of(file) == "sub/a.csv"


def test_local_endpoint_relative_of_rejects_file_outside_root(allowed_dir):
    (allowed_dir / "src").mkdir()
    endpoint = ft.LocalEndpoint(str(allowed_dir / "src"))
    other = DiscoveredFile(path=str(allowed_dir / "elsewhere.csv"), file_name="elsewhere.csv", tokens={})
    with pytest.raises(ft.TransferError, match="not under source root"):
        endpoint.relative_of(other)


def test_local_endpoint_destination_for_stays_inside_root(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir / "out"))
    assert Path(endpoint.destination_for("sub/x.csv")) == allowed_dir / "out" / "sub" / "x.csv"


def test_local_endpoint_write_read_exists_size_delete(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir / "out"))
    dest = endpoint.destination_for("sub/x.csv")

    assert not endpoint.exists(dest)
    endpoint.write(dest, io.BytesIO(b"id\n1\n"))

    assert endpoint.exists(dest)
    assert endpoint.size(dest) == 5
    with endpoint.open_read(dest) as fh:
        assert fh.read() == b"id\n1\n"
    assert not Path(dest + ".part").exists()

    endpoint.delete(dest)
    assert not endpoint.exists(dest)


def test_local_endpoint_write_replaces_existing_file(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    dest = endpoint.destination_for("a.csv")
    endpoint.write(dest, io.BytesIO(b"old"))
    endpoint.write(dest, io.BytesIO(b"newer"))
    assert Path(dest).read_bytes() == b"newer"


def test_local_endpoint_failed_write_keeps_existing_and_leaves_no_part_file(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    dest = endpoint.destination_for("a.csv")
    endpoint.write(dest, io.BytesIO(b"old"))

    with pytest.raises(OSError, match="connection reset"):
        endpoint.write(dest, _Boom())

    assert Path(dest).read_bytes() == b"old"
    assert not Path(dest + ".part").exists()


def test_local_endpoint_identity_is_stable_for_same_file(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    a = endpoint.identity(str(allowed_dir / "x.csv"))
    b = endpoint.identity(str(allowed_dir / "sub" / ".." / "x.csv"))
    assert a == b
