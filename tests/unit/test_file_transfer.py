from __future__ import annotations

import errno
import io
import subprocess
import sys
from pathlib import Path

import boto3
import botocore.exceptions
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
    assert not any(Path(dest).parent.glob(Path(dest).name + ".*" + ft.PART_SUFFIX))

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
    assert not any(Path(dest).parent.glob(Path(dest).name + ".*" + ft.PART_SUFFIX))


def test_local_endpoint_identity_is_stable_for_same_file(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    a = endpoint.identity(str(allowed_dir / "x.csv"))
    b = endpoint.identity(str(allowed_dir / "sub" / ".." / "x.csv"))
    assert a == b


def test_local_endpoint_real_part_named_file_is_not_clobbered_by_temp_files(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    part_dest = endpoint.destination_for("x.csv.part")
    dest = endpoint.destination_for("x.csv")

    endpoint.write(part_dest, io.BytesIO(b"i am a real part-named file"))
    endpoint.write(dest, io.BytesIO(b"data"))

    assert Path(part_dest).read_bytes() == b"i am a real part-named file"
    assert Path(dest).read_bytes() == b"data"


def test_local_endpoint_failed_write_leaves_preexisting_part_named_file_untouched(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    part_file = allowed_dir / "y.csv.part"
    part_file.write_bytes(b"unrelated")
    dest = endpoint.destination_for("y.csv")

    with pytest.raises(OSError, match="connection reset"):
        endpoint.write(dest, _Boom())

    assert part_file.read_bytes() == b"unrelated"
    assert not Path(dest).exists()


@pytest.mark.parametrize("relative", ["../x.csv", "sub/../../x.csv"])
def test_local_endpoint_destination_for_rejects_parent_traversal(allowed_dir, relative):
    endpoint = ft.LocalEndpoint(str(allowed_dir / "out"))
    with pytest.raises(ft.TransferError, match="escapes destination root"):
        endpoint.destination_for(relative)


def test_local_endpoint_destination_for_rejects_absolute_path(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir / "out"))
    with pytest.raises(ft.TransferError, match="escapes destination root"):
        endpoint.destination_for(str(allowed_dir.parent / "evil.csv"))


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")
def test_local_endpoint_destination_for_rejects_junction_escaping_root(allowed_dir, tmp_path):
    outside = tmp_path / "outside_target"
    outside.mkdir()
    out_root = allowed_dir / "out"
    out_root.mkdir()
    junction = out_root / "junc"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip(f"could not create junction: {result.stderr!r}")

    endpoint = ft.LocalEndpoint(str(out_root))
    with pytest.raises(ft.TransferError, match="escapes destination root"):
        endpoint.destination_for("junc/pwned.csv")


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive filesystem semantics")
def test_local_endpoint_identity_ignores_case_on_windows(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    assert endpoint.identity(str(allowed_dir / "X.CSV")) == endpoint.identity(str(allowed_dir / "x.csv"))


# -- S3Endpoint --------------------------------------------------------------

@pytest.fixture
def s3_raw():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="bkt")
        yield raw


def test_s3_endpoint_rejects_non_s3_root(s3_raw):
    with pytest.raises(ValueError, match="s3://bucket/prefix"):
        ft.S3Endpoint("/not/s3", s3_raw, "prof")


def test_s3_endpoint_relative_of_strips_prefix_and_unquotes(s3_raw):
    endpoint = ft.S3Endpoint("s3://bkt/in", s3_raw, "prof")
    file = DiscoveredFile(path="s3://bkt/in/sub/a%20b.csv", file_name="a b.csv", tokens={})
    assert endpoint.relative_of(file) == "sub/a b.csv"


def test_s3_endpoint_destination_for_quotes_key(s3_raw):
    endpoint = ft.S3Endpoint("s3://bkt/out", s3_raw, "prof")
    assert endpoint.destination_for("sub/a b#1.csv") == "s3://bkt/out/sub/a%20b%231.csv"


def test_s3_endpoint_write_read_exists_size_delete(s3_raw):
    endpoint = ft.S3Endpoint("s3://bkt/out", s3_raw, "prof")
    dest = endpoint.destination_for("sub/a b.csv")

    assert not endpoint.exists(dest)
    endpoint.write(dest, io.BytesIO(b"id\n1\n"))

    assert endpoint.exists(dest)
    assert endpoint.size(dest) == 5
    assert s3_raw.get_object(Bucket="bkt", Key="out/sub/a b.csv")["Body"].read() == b"id\n1\n"
    body = endpoint.open_read(dest)
    try:
        assert body.read() == b"id\n1\n"
    finally:
        body.close()

    endpoint.delete(dest)
    assert not endpoint.exists(dest)


def test_s3_endpoint_identity_includes_credentials_ref(s3_raw):
    a = ft.S3Endpoint("s3://bkt/out", s3_raw, "prof-a")
    b = ft.S3Endpoint("s3://bkt/out", s3_raw, "prof-b")
    path = "s3://bkt/out/x.csv"
    assert a.identity(path) != b.identity(path)
    assert a.identity("s3://bkt/out/x%20y.csv") == a.identity("s3://bkt/out/x y.csv")


def test_s3_endpoint_root_with_literal_percent_round_trips(s3_raw):
    from etl_framework.reconciliation.file_mapping import discover_s3_files

    s3_raw.put_object(Bucket="bkt", Key="a%20b/f.csv", Body=b"id\n1\n")
    root = "s3://bkt/a%20b"

    discovered = discover_s3_files(s3_raw, root, "*.csv")
    assert len(discovered) == 1
    endpoint = ft.S3Endpoint(root, s3_raw, "p")

    assert endpoint.relative_of(discovered[0]) == "f.csv"
    assert endpoint._split(endpoint.destination_for("f.csv")) == endpoint._split(discovered[0].path)
    assert endpoint._split(discovered[0].path) == ("bkt", "a%20b/f.csv")


def test_s3_endpoint_relative_of_rejects_key_outside_prefix(s3_raw):
    endpoint = ft.S3Endpoint("s3://bkt/in", s3_raw, "prof")
    file = DiscoveredFile(path="s3://bkt/other/a.csv", file_name="a.csv", tokens={})
    with pytest.raises(ft.TransferError, match="not under source root"):
        endpoint.relative_of(file)


class _HeadFails:
    def __init__(self, code: str) -> None:
        self.code = code

    def head_object(self, **kwargs):
        raise botocore.exceptions.ClientError({"Error": {"Code": self.code}}, "HeadObject")


def test_s3_endpoint_exists_reraises_non_404_client_errors():
    endpoint = ft.S3Endpoint("s3://bkt/out", _HeadFails("403"), "p")
    with pytest.raises(botocore.exceptions.ClientError):
        endpoint.exists("s3://bkt/out/x.csv")


def test_s3_endpoint_exists_returns_false_on_404():
    endpoint = ft.S3Endpoint("s3://bkt/out", _HeadFails("404"), "p")
    assert endpoint.exists("s3://bkt/out/x.csv") is False


# -- SftpEndpoint ------------------------------------------------------------

def test_sftp_endpoint_relative_of_and_destination_for():
    endpoint = ft.SftpEndpoint("/in/", FakeSFTP(), "vendor")
    file = DiscoveredFile(path="/in/sub/a.csv", file_name="a.csv", tokens={})
    assert endpoint.relative_of(file) == "sub/a.csv"
    assert ft.SftpEndpoint("/out", FakeSFTP(), "vendor").destination_for("a/b.csv") == "/out/a/b.csv"


def test_sftp_endpoint_write_creates_parent_dirs_and_renames_part_file():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("a/b/x.csv")

    endpoint.write(dest, io.BytesIO(b"hello"))

    assert fake.files == {"/out/a/b/x.csv": b"hello"}
    assert {"/out", "/out/a", "/out/a/b"} <= fake.dirs
    assert endpoint.exists(dest)
    assert endpoint.size(dest) == 5
    with endpoint.open_read(dest) as fh:
        assert fh.read() == b"hello"


def test_sftp_endpoint_write_overwrites_via_posix_rename():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    endpoint.write(dest, io.BytesIO(b"old"))
    fake.calls.clear()
    endpoint.write(dest, io.BytesIO(b"newer"))
    assert fake.files == {"/out/x.csv": b"newer"}
    assert "posix_rename" in fake.calls
    assert "remove" not in fake.calls


def test_sftp_endpoint_write_falls_back_when_posix_rename_unsupported():
    fake = FakeSFTP()
    fake.posix_rename_supported = False
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    endpoint.write(dest, io.BytesIO(b"old"))
    endpoint.write(dest, io.BytesIO(b"newer"))
    assert fake.files == {"/out/x.csv": b"newer"}


def test_sftp_endpoint_failed_write_removes_part_file_and_keeps_existing():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    endpoint.write(dest, io.BytesIO(b"old"))

    fake.fail_putfo_after_partial = True
    with pytest.raises(IOError, match="connection reset"):
        endpoint.write(dest, io.BytesIO(b"newer"))

    assert fake.files == {"/out/x.csv": b"old"}


def test_sftp_endpoint_write_does_not_clobber_real_dot_part_file():
    fake = FakeSFTP()
    fake.mkdir("/out")
    fake.files["/out/x.csv.part"] = b"keep"
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")

    endpoint.write("/out/x.csv", io.BytesIO(b"new"))

    assert fake.files["/out/x.csv.part"] == b"keep"
    assert fake.files["/out/x.csv"] == b"new"


def test_sftp_endpoint_exists_false_for_missing_and_delete_removes():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    assert not endpoint.exists(dest)
    endpoint.write(dest, io.BytesIO(b"x"))
    endpoint.delete(dest)
    assert not endpoint.exists(dest)


def test_sftp_endpoint_identity_normalizes_path_and_keys_on_credentials_ref():
    a = ft.SftpEndpoint("/out", FakeSFTP(), "vendor")
    b = ft.SftpEndpoint("/out", FakeSFTP(), "other")
    assert a.identity("/out/sub/../x.csv") == a.identity("/out/x.csv")
    assert a.identity("/out/x.csv") != b.identity("/out/x.csv")


def test_sftp_endpoint_typed_posix_rename_error_propagates_and_keeps_destination():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    endpoint.write(dest, io.BytesIO(b"old"))

    fake.posix_rename_error = IOError(errno.ENOENT, "gone")
    fake.calls.clear()
    with pytest.raises(FileNotFoundError):
        endpoint.write(dest, io.BytesIO(b"newer"))

    assert fake.files == {"/out/x.csv": b"old"}
    assert not any(name.endswith(".part") for name in fake.files)
    assert "rename" not in fake.calls


def test_sftp_endpoint_fallback_rename_failure_restores_old_destination():
    class FailingPartRename(FakeSFTP):
        def rename(self, old: str, new: str) -> None:
            if old.endswith(".part"):
                raise IOError("Failure")
            super().rename(old, new)

    fake = FailingPartRename()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    endpoint.write(dest, io.BytesIO(b"old"))

    fake.posix_rename_supported = False
    with pytest.raises(IOError, match="Failure"):
        endpoint.write(dest, io.BytesIO(b"newer"))

    assert fake.files == {"/out/x.csv": b"old"}
    assert not any(name.endswith((".part", ".bak")) for name in fake.files)


def test_sftp_endpoint_fallback_rename_onto_absent_destination_needs_no_backup():
    fake = FakeSFTP()
    fake.posix_rename_supported = False
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    endpoint.write("/out/x.csv", io.BytesIO(b"first"))
    assert fake.files == {"/out/x.csv": b"first"}
    assert "remove" not in fake.calls


def test_sftp_endpoint_delete_of_missing_file_is_idempotent():
    endpoint = ft.SftpEndpoint("/out", FakeSFTP(), "vendor")
    endpoint.delete("/out/missing.csv")


def test_sftp_endpoint_relative_of_rejects_file_outside_root():
    endpoint = ft.SftpEndpoint("/in", FakeSFTP(), "vendor")
    file = DiscoveredFile(path="/elsewhere/a.csv", file_name="a.csv", tokens={})
    with pytest.raises(ft.TransferError, match="not under source root"):
        endpoint.relative_of(file)


def test_sftp_endpoint_write_tolerates_mkdir_race():
    class RacyMkdir(FakeSFTP):
        def mkdir(self, path: str) -> None:
            self.dirs.add(path)
            raise IOError("Failure")

    fake = RacyMkdir()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    endpoint.write(endpoint.destination_for("sub/x.csv"), io.BytesIO(b"hi"))
    assert fake.files == {"/out/sub/x.csv": b"hi"}


@pytest.mark.parametrize("relative", ["../x.csv", "a/../../x.csv", "/etc/passwd"])
def test_sftp_endpoint_destination_for_rejects_escape(relative):
    endpoint = ft.SftpEndpoint("/out", FakeSFTP(), "vendor")
    with pytest.raises(ft.TransferError, match="escapes destination root"):
        endpoint.destination_for(relative)


def test_sftp_endpoint_destination_for_with_slash_root():
    endpoint = ft.SftpEndpoint("/", FakeSFTP(), "vendor")
    assert endpoint.destination_for("a/b.csv") == "/a/b.csv"
