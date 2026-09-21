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


# -- plan_transfer -----------------------------------------------------------

def _make_files(root: Path, *relatives: str) -> list[DiscoveredFile]:
    files = []
    for relative in relatives:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data-" + relative.encode())
        files.append(DiscoveredFile(path=str(path), file_name=path.name, tokens={}))
    return files


def _local_pair(allowed_dir: Path):
    return ft.LocalEndpoint(str(allowed_dir / "src")), ft.LocalEndpoint(str(allowed_dir / "dst"))


def _relative_destinations(entries, dst_root: Path) -> list[str]:
    return [Path(entry.destination).relative_to(dst_root).as_posix() for entry in entries]


def test_plan_flattens_into_destination_root_by_default(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "sub/b.csv")

    plan = ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=False)

    assert _relative_destinations(plan.to_copy, allowed_dir / "dst") == ["a.csv", "b.csv"]
    assert plan.skipped == []


def test_plan_preserves_relative_structure_when_asked(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "sub/b.csv")

    plan = ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=True)

    assert _relative_destinations(plan.to_copy, allowed_dir / "dst") == ["a.csv", "sub/b.csv"]


@pytest.mark.parametrize("on_exists", ["fail", "skip", "overwrite"])
def test_plan_rejects_duplicate_destination_when_flattening(allowed_dir, on_exists):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "x/a.csv", "y/a.csv")

    with pytest.raises(ft.TransferError, match="duplicate destination"):
        ft.plan_transfer(files, source, destination, on_exists=on_exists, preserve_structure=False)


def test_plan_rejects_source_and_destination_being_the_same_object(allowed_dir):
    source = ft.LocalEndpoint(str(allowed_dir / "src"))
    files = _make_files(allowed_dir / "src", "a.csv")

    with pytest.raises(ft.TransferError, match="same object"):
        ft.plan_transfer(files, source, source, on_exists="overwrite", preserve_structure=False)


@pytest.mark.parametrize("bad_name", ["..", ".", "a\\b.csv", "/abs.csv", ""])
def test_plan_rejects_unsafe_relative_paths(allowed_dir, bad_name):
    source, destination = _local_pair(allowed_dir)
    files = [DiscoveredFile(path=str(allowed_dir / "src" / "x.csv"), file_name=bad_name, tokens={})]

    with pytest.raises(ft.TransferError, match="unsafe"):
        ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=False)


def test_plan_on_exists_fail_aborts_before_anything_is_planned_for_writing(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "b.csv")
    (allowed_dir / "dst").mkdir()
    (allowed_dir / "dst" / "b.csv").write_bytes(b"already here")

    with pytest.raises(ft.TransferError, match=r"already contains 1 file\(s\)") as excinfo:
        ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=False)

    assert "b.csv" in str(excinfo.value)
    assert "a.csv" not in str(excinfo.value)


def test_plan_on_exists_skip_drops_existing_files(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "b.csv")
    (allowed_dir / "dst").mkdir()
    (allowed_dir / "dst" / "b.csv").write_bytes(b"already here")

    plan = ft.plan_transfer(files, source, destination, on_exists="skip", preserve_structure=False)

    assert _relative_destinations(plan.to_copy, allowed_dir / "dst") == ["a.csv"]
    assert _relative_destinations(plan.skipped, allowed_dir / "dst") == ["b.csv"]


def test_plan_on_exists_overwrite_keeps_every_file(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "b.csv")
    (allowed_dir / "dst").mkdir()
    (allowed_dir / "dst" / "b.csv").write_bytes(b"already here")

    plan = ft.plan_transfer(files, source, destination, on_exists="overwrite", preserve_structure=False)

    assert _relative_destinations(plan.to_copy, allowed_dir / "dst") == ["a.csv", "b.csv"]
    assert plan.skipped == []


def test_plan_on_exists_fail_truncates_the_collision_list(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    names = [f"f{i}.csv" for i in range(7)]
    files = _make_files(allowed_dir / "src", *names)
    (allowed_dir / "dst").mkdir()
    for name in names:
        (allowed_dir / "dst" / name).write_bytes(b"already here")

    with pytest.raises(ft.TransferError) as excinfo:
        ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=False)

    assert "already contains 7 file(s)" in str(excinfo.value)
    assert "and 2 more" in str(excinfo.value)


# -- Windows-illegal destination names ---------------------------------------

@pytest.mark.parametrize("name", [
    "data.csv:hidden",
    "a<b.csv",
    "x\x00.csv",
    "rep.csv.",
    "rep.csv ",
    "nul",
    "CON.txt",
    "com1",
])
def test_windows_illegal_name_flags_unsafe_components(name):
    assert ft._windows_illegal_name(name) is not None


@pytest.mark.parametrize("name", ["report.csv", "a b.csv", "x-1_2.csv", "console.csv", ".hidden"])
def test_windows_illegal_name_accepts_normal_components(name):
    assert ft._windows_illegal_name(name) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only destination name rules")
def test_local_endpoint_destination_for_rejects_windows_illegal_names(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir / "dst"))

    with pytest.raises(ft.TransferError, match="destination name"):
        endpoint.destination_for("data.csv:hidden")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only destination name rules")
def test_plan_rejects_names_that_collapse_on_windows(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    src = allowed_dir / "src"
    src.mkdir()
    files = [
        DiscoveredFile(path=str(src / "rep.csv"), file_name="rep.csv", tokens={}),
        DiscoveredFile(path=str(src / "rep.csv."), file_name="rep.csv.", tokens={}),
    ]

    with pytest.raises(ft.TransferError):
        ft.plan_transfer(files, source, destination, on_exists="overwrite", preserve_structure=False)


# -- run_transfer ------------------------------------------------------------

class _MemorySource:
    def __init__(self, contents: dict[str, bytes], fail_on: str | None = None) -> None:
        self.contents = contents
        self.fail_on = fail_on
        self.opened: list[str] = []

    def open_read(self, path: str):
        self.opened.append(path)
        if path == self.fail_on:
            raise OSError(f"cannot read {path}")
        return io.BytesIO(self.contents[path])


class _MemoryDestination:
    def __init__(self, size_override: int | None = None) -> None:
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.size_override = size_override

    def write(self, path: str, stream) -> None:
        data = b""
        while True:
            chunk = stream.read(4)
            if not chunk:
                break
            data += chunk
        self.files[path] = data

    def size(self, path: str) -> int:
        return self.size_override if self.size_override is not None else len(self.files[path])

    def delete(self, path: str) -> None:
        self.deleted.append(path)
        self.files.pop(path, None)


def _entry(name: str) -> ft.PlannedCopy:
    return ft.PlannedCopy(
        source=DiscoveredFile(path=f"/src/{name}", file_name=name, tokens={}),
        destination=f"/dst/{name}",
        relative=name,
    )


def test_run_transfer_copies_every_planned_file_and_counts_bytes():
    source = _MemorySource({"/src/a.csv": b"aaaa", "/src/b.csv": b"bb"})
    destination = _MemoryDestination()
    plan = ft.TransferPlan(to_copy=[_entry("a.csv"), _entry("b.csv")], skipped=[_entry("c.csv")])

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.error is None and outcome.failed_file is None
    assert destination.files == {"/dst/a.csv": b"aaaa", "/dst/b.csv": b"bb"}
    assert outcome.bytes_copied == 6
    assert [item["action"] for item in outcome.copied] == ["copied", "copied"]
    assert outcome.copied[0] == {"source": "/src/a.csv", "destination": "/dst/a.csv", "bytes": 4, "action": "copied"}
    assert outcome.skipped == [{"source": "/src/c.csv", "destination": "/dst/c.csv", "bytes": 0, "action": "skipped"}]


def test_run_transfer_stops_at_first_failure_and_keeps_earlier_copies():
    source = _MemorySource({"/src/a.csv": b"a", "/src/b.csv": b"b", "/src/c.csv": b"c"}, fail_on="/src/b.csv")
    destination = _MemoryDestination()
    plan = ft.TransferPlan(to_copy=[_entry("a.csv"), _entry("b.csv"), _entry("c.csv")])

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.failed_file == "/src/b.csv"
    assert "cannot read /src/b.csv" in outcome.error
    assert [item["source"] for item in outcome.copied] == ["/src/a.csv"]
    assert "/dst/c.csv" not in destination.files
    assert "/src/c.csv" not in source.opened


def test_run_transfer_deletes_destination_and_fails_on_size_mismatch():
    source = _MemorySource({"/src/a.csv": b"aaaa"})
    destination = _MemoryDestination(size_override=3)
    plan = ft.TransferPlan(to_copy=[_entry("a.csv")])

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.failed_file == "/src/a.csv"
    assert "size mismatch" in outcome.error and "4 bytes" in outcome.error
    assert destination.deleted == ["/dst/a.csv"]
    assert outcome.copied == []


def test_run_transfer_end_to_end_local_to_local(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "sub/b.csv")
    plan = ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=True)

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.error is None
    assert (allowed_dir / "dst" / "a.csv").read_bytes() == b"data-a.csv"
    assert (allowed_dir / "dst" / "sub" / "b.csv").read_bytes() == b"data-sub/b.csv"
    assert list((allowed_dir / "dst").rglob("*.part")) == []
