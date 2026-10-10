"""Live test of CrushFTP support against a real server.

Skipped unless LIVE_CRUSHFTP_URL is set. Start the container with

    docker compose -f docker-compose.integration.yml up -d crushftp

and run with:

    LIVE_CRUSHFTP_URL=http://127.0.0.1:28080
    LIVE_CRUSHFTP_SFTP_PORT=22222             (optional: also test the sftp protocol)
    LIVE_CRUSHFTP_ADMIN_USER=crushadmin       (optional: create the test user)
    LIVE_CRUSHFTP_ADMIN_PASSWORD=password
    LIVE_CRUSHFTP_USER=atom                   (default)
    LIVE_CRUSHFTP_PASSWORD=atompass           (default)
    LIVE_CRUSHFTP_ROOT=/data                  (default: a folder the user can write to)
    LIVE_CRUSHFTP_DATA_URL=FILE://srv/atomdata/  (default: where the admin maps that folder)
    CRUSHFTP_ALLOW_INSECURE_HTTP=1            (the container is reached over plain http)

Everything is created under a unique sub-folder of LIVE_CRUSHFTP_ROOT and
removed afterwards.
"""
from __future__ import annotations

import hashlib
import io
import os
import time
import uuid
from urllib.parse import urlsplit

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.services import file_transfer as ft
from api.services.crushftp_client import CrushFtpConnectError, CrushFtpHttpClient
from etl_framework.reconciliation.file_mapping import FileSourceSpec, discover_crushftp_files

URL = os.environ.get("LIVE_CRUSHFTP_URL", "")
USER = os.environ.get("LIVE_CRUSHFTP_USER", "atom")
PASSWORD = os.environ.get("LIVE_CRUSHFTP_PASSWORD", "atompass")
ROOT = os.environ.get("LIVE_CRUSHFTP_ROOT", "/data").rstrip("/")
SFTP_PORT = os.environ.get("LIVE_CRUSHFTP_SFTP_PORT", "")

pytestmark = pytest.mark.skipif(not URL, reason="live CrushFTP tests disabled (set LIVE_CRUSHFTP_URL to enable)")

_XML = '<?xml version="1.0" encoding="UTF-8"?>'


def _wait_until_up() -> None:
    deadline = time.monotonic() + 90
    while True:
        try:
            requests.get(URL + "/WebInterface/login.html", timeout=5, allow_redirects=False)
            return
        except requests.RequestException:
            if time.monotonic() > deadline:
                raise
            time.sleep(2)


def _ensure_test_user() -> None:
    """Create (or replace) the test user through the admin API, with one
    writable virtual folder named after LIVE_CRUSHFTP_ROOT."""
    admin_user = os.environ.get("LIVE_CRUSHFTP_ADMIN_USER")
    if not admin_user:
        return
    folder = ROOT.strip("/")
    data_url = os.environ.get("LIVE_CRUSHFTP_DATA_URL", "FILE://srv/atomdata/")
    session = requests.Session()
    function = URL + "/WebInterface/function/"
    login = session.post(function, data={
        "command": "login", "username": admin_user, "password": os.environ.get("LIVE_CRUSHFTP_ADMIN_PASSWORD", ""),
    }, timeout=20)
    assert "<response>success</response>" in login.text, "CrushFTP admin login failed"
    c2f = session.cookies.get("CrushAuth")[-4:]
    result = session.post(function, timeout=20, data={
        "command": "setUserItem", "data_action": "replace", "serverGroup": "MainUsers", "username": USER,
        "xmlItem": "user", "c2f": c2f,
        "user": (
            f'{_XML}<user type="properties"><username>{USER}</username><password>{PASSWORD}</password>'
            "<max_logins>0</max_logins><root_dir>/</root_dir><version>1.0</version></user>"
        ),
        "vfs_items": (
            f'{_XML}<vfs_items type="vector"><vfs_items_subitem type="properties"><name>{folder}</name><path>/</path>'
            f'<vfs_item type="vector"><vfs_item_subitem type="properties"><type>DIR</type><url>{data_url}</url>'
            "</vfs_item_subitem></vfs_item></vfs_items_subitem></vfs_items>"
        ),
        "permissions": (
            f'{_XML}<VFS type="properties"><item name="/">(read)(view)(resume)</item>'
            f'<item name="/{folder.upper()}/">(read)(write)(view)(delete)(deletedir)(makedir)(rename)(resume)</item></VFS>'
        ),
    })
    assert "<response_status>OK</response_status>" in result.text, result.text[:300]
    session.post(function, data={"command": "logout", "c2f": c2f}, timeout=20)


@pytest.fixture(scope="module", autouse=True)
def _server_ready():
    os.environ.setdefault("CRUSHFTP_ALLOW_INSECURE_HTTP", "1")
    _wait_until_up()
    _ensure_test_user()


@pytest.fixture
def client():
    c = CrushFtpHttpClient(URL, username=USER, password=PASSWORD)
    c.login()
    yield c
    c.close()


@pytest.fixture
def workdir(client):
    path = f"{ROOT}/atom-live-{uuid.uuid4().hex[:12]}"
    client.mkdir(path)
    yield path
    client.delete(path)


def test_login_and_list(client, workdir):
    assert client.list(workdir) == []
    assert workdir.rsplit("/", 1)[1] in [e.name for e in client.list(ROOT) if e.is_dir]


def test_wrong_password_is_a_connect_error():
    c = CrushFtpHttpClient(URL, username=USER, password="definitely-wrong-" + uuid.uuid4().hex)
    with pytest.raises(CrushFtpConnectError):
        c.login()
    c.close()


def test_large_upload_download_round_trip(client, workdir):
    payload = os.urandom(5 * 1024 * 1024)
    path = f"{workdir}/big.bin"

    client.upload(path, io.BytesIO(payload))

    assert client.size(path) == len(payload)
    with client.open_read(path) as reader:
        assert hashlib.sha256(reader.read()).hexdigest() == hashlib.sha256(payload).hexdigest()
    with client.open_read(path, limit=1000) as reader:
        assert reader.read() == payload[:1000]
    entry = next(e for e in client.list(workdir) if e.name == "big.bin")
    assert entry.size == len(payload) and not entry.is_dir
    assert abs(entry.modified - time.time()) < 3600


def test_empty_file_and_missing_file(client, workdir):
    client.upload(f"{workdir}/empty.txt", io.BytesIO(b""))

    assert client.size(f"{workdir}/empty.txt") == 0
    with client.open_read(f"{workdir}/empty.txt", limit=100) as reader:
        assert reader.read() == b""
    assert client.exists(f"{workdir}/nope.txt") is False
    with pytest.raises(FileNotFoundError):
        client.open_read(f"{workdir}/nope.txt")
    with pytest.raises(FileNotFoundError):
        client.list(f"{workdir}/nope")


def test_names_with_spaces_plus_and_unicode(client, workdir):
    directory = f"{workdir}/my dir/in+ner"
    client.mkdir(directory)
    client.upload(f"{directory}/a b é.txt.part", io.BytesIO(b"body"))
    client.rename(f"{directory}/a b é.txt.part", f"{directory}/a b é.txt")

    assert [e.name for e in client.list(directory)] == ["a b é.txt"]
    with client.open_read(f"{directory}/a b é.txt") as reader:
        assert reader.read() == b"body"
    client.delete(f"{directory}/a b é.txt")
    assert client.list(directory) == []


def test_rename_onto_existing_is_refused_and_upload_into_missing_dir_is_a_permission_error(client, workdir):
    client.upload(f"{workdir}/a.txt", io.BytesIO(b"a"))
    client.upload(f"{workdir}/b.txt", io.BytesIO(b"b"))

    with pytest.raises(OSError):
        client.rename(f"{workdir}/a.txt", f"{workdir}/b.txt")
    with client.open_read(f"{workdir}/b.txt") as reader:
        assert reader.read() == b"b"
    with pytest.raises(PermissionError):
        client.upload(f"{workdir}/missing/x.txt", io.BytesIO(b"x"))


def test_endpoint_transfer_with_overwrite_and_recursive_discovery(client, workdir):
    client.mkdir(f"{workdir}/in/sub")
    client.upload(f"{workdir}/in/a.csv", io.BytesIO(b"id\n1\n"))
    client.upload(f"{workdir}/in/sub/b.csv", io.BytesIO(b"id\n2\n"))
    client.mkdir(f"{workdir}/out")
    client.upload(f"{workdir}/out/a.csv", io.BytesIO(b"stale"))
    source = ft.CrushFtpEndpoint(f"{workdir}/in", client, "live")
    destination = ft.CrushFtpEndpoint(f"{workdir}/out", client, "live")

    files = discover_crushftp_files(client, f"{workdir}/in", "*.csv", recursive=True)
    assert [f.path for f in files] == [f"{workdir}/in/a.csv", f"{workdir}/in/sub/b.csv"]
    plan = ft.plan_transfer(files, source, destination, on_exists="overwrite", preserve_structure=True)
    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.error is None, outcome.error
    with client.open_read(f"{workdir}/out/a.csv") as reader:
        assert reader.read() == b"id\n1\n"
    with client.open_read(f"{workdir}/out/sub/b.csv") as reader:
        assert reader.read() == b"id\n2\n"
    # no temp or backup files left beside the results
    assert sorted(e.name for e in client.list(f"{workdir}/out")) == ["a.csv", "sub"]


def test_expired_session_is_a_connect_error(workdir):
    stale = CrushFtpHttpClient(URL, username=USER, password=PASSWORD)
    stale.login()
    stale._session.cookies.clear()  # what the server sees once a session has gone

    with pytest.raises(CrushFtpConnectError):
        stale.list(workdir)
    with pytest.raises(CrushFtpConnectError):
        stale.open_read(f"{workdir}/x.txt")
    stale._logged_in = False  # nothing left to log out of
    stale.close()


@pytest.fixture
def profile_db(monkeypatch):
    from etl_framework.repository import database as db_module
    from etl_framework.repository.database import Base
    import etl_framework.repository.models  # noqa: F401

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as session:
        yield session


def test_file_source_session_over_https(profile_db, client, workdir):
    from api.services.multi_file_remote import RemoteFileSourceSession
    from etl_framework.repository.repository import FileServerProfileRepository

    client.upload(f"{workdir}/sales_eu.csv", io.BytesIO(b"id,amount\n1,10\n2,20\n"))
    FileServerProfileRepository(profile_db).create({
        "name": "live-web", "kind": "crushftp", "protocol": "https", "endpoint_url": URL,
        "auth_method": "password", "username": USER, "password": PASSWORD,
    })
    spec = FileSourceSpec(kind="crushftp", root=workdir, pattern="sales_{region}.csv", credentials_ref="live-web")

    with RemoteFileSourceSession(profile_db) as session:
        found = session.discover(spec)
        frame = session.read_file(found[0], spec)
        text = session.read_text(found[0], spec)

    assert [(f.file_name, f.tokens) for f in found] == [("sales_eu.csv", {"region": "eu"})]
    assert frame.to_dict("list") == {"id": [1, 2], "amount": [10, 20]}
    assert text.startswith("id,amount")


@pytest.mark.skipif(not SFTP_PORT, reason="set LIVE_CRUSHFTP_SFTP_PORT to test the sftp protocol")
def test_file_source_session_and_transfer_over_sftp(profile_db, client, workdir):
    import paramiko

    from api.services.multi_file_remote import RemoteFileSourceSession
    from etl_framework.repository.repository import FileServerProfileRepository

    host = urlsplit(URL).hostname
    transport = paramiko.Transport((host, int(SFTP_PORT)))
    transport.start_client(timeout=20)
    fingerprint = hashlib.sha256(transport.get_remote_server_key().asbytes()).hexdigest()
    transport.close()

    client.upload(f"{workdir}/sales_eu.csv", io.BytesIO(b"id,amount\n1,10\n"))
    FileServerProfileRepository(profile_db).create({
        "name": "live-sftp", "kind": "crushftp", "protocol": "sftp", "host": host, "port": int(SFTP_PORT),
        "username": USER, "auth_method": "password", "password": PASSWORD, "host_key_fingerprint": fingerprint,
    })
    source_spec = FileSourceSpec(kind="crushftp", root=workdir, pattern="*.csv", credentials_ref="live-sftp")
    destination_spec = FileSourceSpec(kind="crushftp", root=f"{workdir}/copied", pattern="*", credentials_ref="live-sftp")

    with RemoteFileSourceSession(profile_db) as session:
        assert session.transport_for(source_spec) == "sftp"
        files = session.discover(source_spec)
        source = ft.build_endpoint(session, source_spec)
        destination = ft.build_endpoint(session, destination_spec)
        assert isinstance(source, ft.SftpEndpoint)
        plan = ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=False)
        outcome = ft.run_transfer(plan, source, destination)

    assert outcome.error is None, outcome.error
    # written over sftp, read back over the HTTP API: both protocols see one file system
    with client.open_read(f"{workdir}/copied/sales_eu.csv") as reader:
        assert reader.read() == b"id,amount\n1,10\n"
