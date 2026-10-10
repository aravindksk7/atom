from __future__ import annotations

import io

import pytest

from api.services import file_transfer as ft
from api.services.crushftp_client import CrushFtpConnectError, CrushFtpHttpClient
from etl_framework.reconciliation.file_mapping import DiscoveredFile, FileSourceSpec, discover_crushftp_files
from tests.helpers.fake_crushftp import FakeCrushFtp
from tests.helpers.fake_sftp import FakeSFTP


@pytest.fixture
def server():
    with FakeCrushFtp() as fake:
        yield fake


@pytest.fixture
def client(server):
    c = CrushFtpHttpClient(server.base_url, username="atom", password="atompass")
    c.login()
    yield c
    c.close()


def _leftovers(server, suffix):
    return [path for path in server.files if path.endswith(suffix)]


def test_relative_of_and_destination_for(client):
    endpoint = ft.CrushFtpEndpoint("/data/in/", client, "crush")

    assert endpoint.relative_of(DiscoveredFile("/data/in/sub/a.csv", "a.csv", {})) == "sub/a.csv"
    assert endpoint.destination_for("sub/a.csv") == "/data/in/sub/a.csv"
    with pytest.raises(ft.TransferError):
        endpoint.relative_of(DiscoveredFile("/data/other/a.csv", "a.csv", {}))


@pytest.mark.parametrize("relative", ["../escape.csv", "sub/../../escape.csv", "."])
def test_destination_for_rejects_paths_escaping_the_root(client, relative):
    with pytest.raises(ft.TransferError, match="escapes"):
        ft.CrushFtpEndpoint("/data/in", client, "crush").destination_for(relative)


def test_identity_prefers_location_key(client):
    keyed = ft.CrushFtpEndpoint("/data", client, "a", location_key=("crushftp", "h", 443, "u"))
    other = ft.CrushFtpEndpoint("/data", client, "b", location_key=("crushftp", "h", 443, "u"))
    bare = ft.CrushFtpEndpoint("/data", client, "a")

    assert keyed.identity("/data/x.csv") == other.identity("/data/./x.csv")
    assert bare.identity("/data/x.csv") == ("crushftp", "a", "/data/x.csv")


def test_exists_size_and_exists_many_on_both_branches(server, client):
    server.files["/data/a.csv"] = b"12345"
    server.files["/data/b.csv"] = b"1"
    endpoint = ft.CrushFtpEndpoint("/data", client, "crush")

    assert endpoint.exists("/data/a.csv") and not endpoint.exists("/data/zz.csv")
    assert endpoint.size("/data/a.csv") == 5
    assert endpoint.exists_many(["/data/a.csv", "/data/zz.csv"]) == {"/data/a.csv"}

    many = ["/data/a.csv", "/data/b.csv", "/data/gone/x.csv"] + [f"/data/n{i}.csv" for i in range(ft._SFTP_LISTING_THRESHOLD)]
    before = len(server.requests)
    assert endpoint.exists_many(many) == {"/data/a.csv", "/data/b.csv"}
    assert len(server.requests) - before == 2  # one listing per parent directory


def test_write_creates_parents_and_leaves_no_part_file(server, client):
    endpoint = ft.CrushFtpEndpoint("/data", client, "crush")

    endpoint.write("/data/out/deep/a.csv", io.BytesIO(b"payload"))

    assert server.files["/data/out/deep/a.csv"] == b"payload"
    assert _leftovers(server, ft.PART_SUFFIX) == []


def test_write_replaces_an_existing_file_and_leaves_no_backup(server, client):
    server.files["/data/a.csv"] = b"old"
    endpoint = ft.CrushFtpEndpoint("/data", client, "crush")

    endpoint.write("/data/a.csv", io.BytesIO(b"new contents"))

    assert server.files == {"/data/a.csv": b"new contents"}


def test_write_restores_the_original_when_the_swap_fails(server, client, monkeypatch):
    server.files["/data/a.csv"] = b"old"
    endpoint = ft.CrushFtpEndpoint("/data", client, "crush")
    real_rename = client.rename

    def _rename(source, destination):
        if source.endswith(ft.PART_SUFFIX):
            raise OSError("swap failed")
        real_rename(source, destination)

    monkeypatch.setattr(client, "rename", _rename)

    with pytest.raises(OSError, match="swap failed"):
        endpoint.write("/data/a.csv", io.BytesIO(b"new"))

    assert server.files == {"/data/a.csv": b"old"}


def test_failed_upload_keeps_the_existing_file(server, client):
    server.files["/data/a.csv"] = b"old"
    server.corrupt_md5 = True

    with pytest.raises(OSError, match="checksum"):
        ft.CrushFtpEndpoint("/data", client, "crush").write("/data/a.csv", io.BytesIO(b"new"))

    assert server.files == {"/data/a.csv": b"old"}


def test_write_refuses_a_percent_path_before_any_request(server, client):
    before = len(server.requests)

    with pytest.raises(ft.TransferError, match="%"):
        ft.CrushFtpEndpoint("/data", client, "crush").write("/data/a%b.csv", io.BytesIO(b"x"))

    assert len(server.requests) == before


def test_delete_of_a_missing_file_does_not_raise(client):
    ft.CrushFtpEndpoint("/data", client, "crush").delete("/data/nope.csv")


class _Session:
    """Stands in for RemoteFileSourceSession in build_endpoint."""

    def __init__(self, client, transport):
        self._client, self._transport = client, transport

    def client_for(self, spec):
        return self._client

    def location_key_for(self, spec):
        return ("key",)

    def transport_for(self, spec):
        return self._transport


def test_build_endpoint_picks_the_endpoint_from_the_profile_transport(client):
    spec = FileSourceSpec(kind="crushftp", root="/data", pattern="*", credentials_ref="crush")

    web = ft.build_endpoint(_Session(client, "crushftp"), spec)
    sftp = ft.build_endpoint(_Session(FakeSFTP(), "sftp"), spec)

    assert isinstance(web, ft.CrushFtpEndpoint) and web.location_key == ("key",)
    assert isinstance(sftp, ft.SftpEndpoint)


def test_transfer_between_crushftp_folders_end_to_end(server, client):
    server.dirs.add("/data/in")
    server.dirs.add("/data/in/sub")
    server.files["/data/in/a.csv"] = b"aaa"
    server.files["/data/in/sub/b.csv"] = b"bbbb"
    source = ft.CrushFtpEndpoint("/data/in", client, "crush")
    destination = ft.CrushFtpEndpoint("/data/out", client, "crush")
    files = discover_crushftp_files(client, "/data/in", "*.csv", recursive=True)

    plan = ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=True)
    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.error is None and outcome.bytes_copied == 7
    assert server.files["/data/out/a.csv"] == b"aaa"
    assert server.files["/data/out/sub/b.csv"] == b"bbbb"

    with pytest.raises(ft.TransferError, match="already contains"):
        ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=True)


def test_connect_error_is_a_transport_error_and_a_permission_error_is_not():
    assert ft.is_transport_error(CrushFtpConnectError("x")) is True
    assert ft.is_transport_error(PermissionError("x")) is False
    assert ft.is_transport_error(FileNotFoundError("x")) is False


def test_expired_session_during_transfer_is_reported_as_a_transport_error(server, client):
    server.dirs.add("/data/in")
    server.files["/data/in/a.csv"] = b"aaa"
    source = ft.CrushFtpEndpoint("/data/in", client, "crush")
    destination = ft.CrushFtpEndpoint("/data/out", client, "crush")
    plan = ft.plan_transfer(
        discover_crushftp_files(client, "/data/in", "*.csv"), source, destination,
        on_exists="overwrite", preserve_structure=False,
    )
    server.sessions.clear()

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.failed_file == "/data/in/a.csv"
    assert ft.is_transport_error(outcome.failed_exc)
