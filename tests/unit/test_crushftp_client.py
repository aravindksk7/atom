from __future__ import annotations

import datetime
import io
import ipaddress
from types import SimpleNamespace

import pytest

from api.services.crushftp_client import (
    CrushFtpConnectError,
    CrushFtpHttpClient,
    build_crushftp_client,
)
from tests.helpers.fake_crushftp import FakeCrushFtp


@pytest.fixture
def server():
    with FakeCrushFtp() as fake:
        fake.files["/data/hello.txt"] = b"hello crushftp"
        fake.files["/data/empty.txt"] = b""
        fake.dirs.add("/data/sub")
        fake.files["/data/sub/nested.csv"] = b"a,b\n1,2\n"
        yield fake


@pytest.fixture
def client(server):
    c = CrushFtpHttpClient(server.base_url, username="atom", password="atompass")
    c.login()
    yield c
    c.close()


def test_password_login_then_list(client):
    entries = {e.name: e for e in client.list("/data")}

    assert set(entries) == {"hello.txt", "empty.txt", "sub"}
    hello = entries["hello.txt"]
    assert (hello.path, hello.size, hello.is_dir) == ("/data/hello.txt", 14, False)
    assert hello.modified == pytest.approx(1791589698.0)
    assert entries["sub"].is_dir is True


def test_wrong_password_raises_connect_error_without_the_password(server):
    c = CrushFtpHttpClient(server.base_url, username="atom", password="wr0ng-secret")
    with pytest.raises(CrushFtpConnectError) as excinfo:
        c.login()
    c.close()

    assert "wr0ng-secret" not in str(excinfo.value)
    assert "atom" in str(excinfo.value)


def test_list_uses_response_path_for_single_virtual_folder(server, client):
    server.single_root = "/data"

    paths = {e.path for e in client.list("/")}

    assert "/data/hello.txt" in paths


def test_list_missing_directory_raises_file_not_found(client):
    with pytest.raises(FileNotFoundError):
        client.list("/data/nope")


def test_size_and_exists(client):
    assert client.size("/data/hello.txt") == 14
    assert client.exists("/data/hello.txt") is True
    assert client.exists("/data/nope.txt") is False
    with pytest.raises(FileNotFoundError):
        client.size("/data/nope.txt")


def test_open_read_streams_and_honours_limit(client):
    with client.open_read("/data/hello.txt") as reader:
        assert reader.read() == b"hello crushftp"
    with client.open_read("/data/hello.txt", limit=5) as reader:
        assert reader.read() == b"hello"
    with client.open_read("/data/hello.txt") as reader:
        assert reader.read(5) + reader.read(4) == b"hello cru"
    with client.open_read("/data/empty.txt", limit=10) as reader:
        assert reader.read() == b""
    with pytest.raises(FileNotFoundError):
        client.open_read("/data/nope.txt")


def test_upload_round_trip_and_md5_check(server, client):
    payload = bytes(range(256)) * 600  # spans several upload blocks

    client.upload("/data/up.bin", io.BytesIO(payload))

    assert server.files["/data/up.bin"] == payload
    server.corrupt_md5 = True
    with pytest.raises(OSError, match="checksum"):
        client.upload("/data/up2.bin", io.BytesIO(b"abc"))


def test_upload_into_missing_directory_raises_permission_error(client):
    with pytest.raises(PermissionError):
        client.upload("/data/missing/x.txt", io.BytesIO(b"x"))


def test_names_with_spaces_and_plus_survive_mkdir_upload_rename_delete(server, client):
    client.mkdir("/data/my dir/in+ner")
    client.upload("/data/my dir/in+ner/a b.txt.part", io.BytesIO(b"body"))
    client.rename("/data/my dir/in+ner/a b.txt.part", "/data/my dir/in+ner/a b.txt")

    assert server.files == {**server.files, "/data/my dir/in+ner/a b.txt": b"body"}
    assert "/data/my dir/in+ner/a b.txt.part" not in server.files
    assert [e.name for e in client.list("/data/my dir/in+ner")] == ["a b.txt"]

    client.delete("/data/my dir/in+ner/a b.txt")
    assert "/data/my dir/in+ner/a b.txt" not in server.files


def test_rename_onto_existing_raises_and_changes_nothing(server, client):
    server.files["/data/a.txt"] = b"a"
    server.files["/data/b.txt"] = b"b"

    with pytest.raises(OSError, match="rename"):
        client.rename("/data/a.txt", "/data/b.txt")

    assert (server.files["/data/a.txt"], server.files["/data/b.txt"]) == (b"a", b"b")


def test_percent_in_listing_name_is_unescaped_and_writes_are_refused(server, client):
    server.files["/data/a%b.txt"] = b"x"

    assert "a%b.txt" in [e.name for e in client.list("/data")]
    with client.open_read("/data/a%b.txt") as reader:
        assert reader.read() == b"x"
    with pytest.raises(OSError, match="%"):
        client.upload("/data/c%d.txt", io.BytesIO(b"x"))
    with pytest.raises(OSError, match="%"):
        client.rename("/data/a%b.txt", "/data/plain.txt")


def test_delete_refuses_the_root(client):
    with pytest.raises(OSError):
        client.delete("/")


def test_api_key_header_sent_on_every_request_and_no_login_without_username():
    with FakeCrushFtp(api_key_header="X-Api-Key", api_key="k3y-value") as fake:
        fake.files["/data/hello.txt"] = b"hi"
        c = CrushFtpHttpClient(fake.base_url, api_key_header="X-Api-Key", api_key="k3y-value")
        c.login()
        assert [e.name for e in c.list("/data")] == ["hello.txt"]
        with c.open_read("/data/hello.txt") as reader:
            assert reader.read() == b"hi"
        c.upload("/data/up.txt", io.BytesIO(b"up"))
        c.close()

        assert fake.requests and all(h.get("x-api-key") == "k3y-value" for _, _, h, _ in fake.requests)
        assert "login" not in fake.commands() and "logout" not in fake.commands()


def test_api_key_with_username_also_logs_in():
    with FakeCrushFtp(api_key_header="X-Api-Key", api_key="k3y-value") as fake:
        c = CrushFtpHttpClient(
            fake.base_url, username="atom", password="atompass", api_key_header="X-Api-Key", api_key="k3y-value",
        )
        c.login()
        c.mkdir("/data/made")
        c.close()

        assert fake.commands() == ["login", "makedir", "logout"]
        assert all(h.get("x-api-key") == "k3y-value" for _, _, h, _ in fake.requests)
        assert "/data/made" in fake.dirs


def test_wrong_api_key_raises_connect_error_without_the_key():
    with FakeCrushFtp(api_key_header="X-Api-Key", api_key="right") as fake:
        c = CrushFtpHttpClient(fake.base_url, api_key_header="X-Api-Key", api_key="wr0ng-key")
        with pytest.raises(CrushFtpConnectError) as excinfo:
            c.list("/data")
        c.close()

        assert "wr0ng-key" not in str(excinfo.value)


def test_redirect_is_never_followed(server, client):
    with FakeCrushFtp() as other:
        server.redirect_all_to = other.base_url + "/steal"

        with pytest.raises(CrushFtpConnectError, match="redirect"):
            client.list("/data")
        with pytest.raises(CrushFtpConnectError, match="redirect"):
            client.open_read("/data/hello.txt")
        with pytest.raises(CrushFtpConnectError, match="redirect"):
            client.upload("/data/x.txt", io.BytesIO(b"x"))

        assert other.requests == []
    server.redirect_all_to = None


def test_expired_session_raises_connect_error(server, client):
    server.sessions.clear()

    with pytest.raises(CrushFtpConnectError, match="expired"):
        client.list("/data")
    with pytest.raises(CrushFtpConnectError):
        client.open_read("/data/hello.txt")


def test_unreachable_host_raises_connect_error():
    c = CrushFtpHttpClient("http://127.0.0.1:9", username="atom", password="s3cret", timeout=(2, 2))
    with pytest.raises(CrushFtpConnectError) as excinfo:
        c.login()
    c.close()

    assert "s3cret" not in str(excinfo.value)


def test_close_logs_out_once_and_is_idempotent(server):
    c = CrushFtpHttpClient(server.base_url, username="atom", password="atompass")
    c.login()

    c.close()
    c.close()

    assert server.commands().count("logout") == 1
    assert server.sessions == set()


def _profile(**overrides):
    base = dict(
        name="crush", kind="crushftp", protocol="https", endpoint_url="", username="atom", password="atompass",
        auth_method="password", api_key_header=None, api_key=None, tls_ca_bundle=None,
    )
    return SimpleNamespace(**{**base, **overrides})


_SPEC = SimpleNamespace(kind="crushftp", root="/data", pattern="*", credentials_ref="crush")


def test_build_client_refuses_http_without_env_and_allows_it_with(server, monkeypatch):
    monkeypatch.delenv("CRUSHFTP_ALLOW_INSECURE_HTTP", raising=False)
    with pytest.raises(CrushFtpConnectError, match="https://"):
        build_crushftp_client(_profile(endpoint_url=server.base_url), _SPEC)

    monkeypatch.setenv("CRUSHFTP_ALLOW_INSECURE_HTTP", "1")
    built = build_crushftp_client(_profile(endpoint_url=server.base_url), _SPEC)
    assert built.exists("/data/hello.txt")
    built.close()


def test_build_client_requires_a_profile():
    with pytest.raises(ValueError, match="credentials_ref"):
        build_crushftp_client(None, _SPEC)


def test_build_client_ignores_api_key_fields_unless_auth_method_is_api_key(server, monkeypatch):
    monkeypatch.setenv("CRUSHFTP_ALLOW_INSECURE_HTTP", "1")
    built = build_crushftp_client(
        _profile(endpoint_url=server.base_url, api_key_header="X-Api-Key", api_key="stale"), _SPEC,
    )
    built.close()

    assert all("x-api-key" not in h for _, _, h, _ in server.requests)


def _self_signed(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    certfile = tmp_path / "cert.pem"
    keyfile = tmp_path / "key.pem"
    certfile.write_bytes(cert_pem)
    keyfile.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    return str(certfile), str(keyfile), cert_pem.decode()


def test_tls_ca_bundle_verifies_and_missing_bundle_rejects(tmp_path):
    certfile, keyfile, pem = _self_signed(tmp_path)
    with FakeCrushFtp(certfile=certfile, keyfile=keyfile) as fake:
        fake.files["/data/hello.txt"] = b"tls"
        assert fake.base_url.startswith("https://")

        trusted = CrushFtpHttpClient(fake.base_url, username="atom", password="atompass", ca_bundle_pem=pem)
        trusted.login()
        assert trusted.size("/data/hello.txt") == 3
        trusted.close()

        untrusted = CrushFtpHttpClient(fake.base_url, username="atom", password="atompass")
        with pytest.raises(CrushFtpConnectError):
            untrusted.login()
        untrusted.close()
