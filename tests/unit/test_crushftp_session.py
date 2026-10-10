"""``crushftp`` locations through ``RemoteFileSourceSession``: the profile's
protocol picks the transport."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.services.multi_file_remote import RemoteFileSourceSession, resolve_file_server_profile
from etl_framework.reconciliation.file_mapping import FileSourceSpec
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import FileServerProfileRepository
from tests.helpers.fake_crushftp import FakeCrushFtp
from tests.helpers.fake_sftp import FakeSFTP


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # RemoteFileSourceSession resolves profiles on its own SessionLocal()
    # session -- point that at this in-memory engine (see test_multi_file_remote.py).
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setenv("CRUSHFTP_ALLOW_INSECURE_HTTP", "1")
    with Session(engine) as session:
        yield session


@pytest.fixture
def server():
    with FakeCrushFtp() as fake:
        fake.files["/data/sales_eu.csv"] = b"id,amount\n1,10\n2,20\n"
        fake.files["/data/notes.txt"] = b"READY\n" + b"x" * 100
        fake.dirs.add("/data/sub")
        fake.files["/data/sub/sales_us.csv"] = b"id,amount\n3,30\n"
        yield fake


def _https_profile(db, server, name="crush-web", **overrides):
    data = {
        "name": name, "kind": "crushftp", "protocol": "https", "endpoint_url": server.base_url,
        "auth_method": "password", "username": "atom", "password": "atompass",
    }
    return FileServerProfileRepository(db).create({**data, **overrides})


def _sftp_profile(db, name="crush-sftp"):
    return FileServerProfileRepository(db).create({
        "name": name, "kind": "crushftp", "protocol": "sftp", "host": "Files.Example.com", "port": 2222,
        "username": "atom", "auth_method": "password", "password": "atompass",
    })


def _spec(ref, root="/data", pattern="*.csv", kind="crushftp"):
    return FileSourceSpec(kind=kind, root=root, pattern=pattern, credentials_ref=ref)


def test_crushftp_spec_and_profile_must_match_kinds(db, server):
    _https_profile(db, server)
    FileServerProfileRepository(db).create({"name": "plain-sftp", "kind": "sftp", "host": "h"})

    with pytest.raises(ValueError, match="kind 'sftp', not 'crushftp'"):
        resolve_file_server_profile(db, _spec("plain-sftp"))
    with pytest.raises(ValueError, match="kind 'crushftp', not 'sftp'"):
        resolve_file_server_profile(db, _spec("crush-web", kind="sftp"))


def test_https_protocol_discovers_reads_and_logs_out(db, server):
    _https_profile(db, server)
    spec = _spec("crush-web")

    with RemoteFileSourceSession(db) as session:
        assert session.transport_for(spec) == "crushftp"
        flat = session.discover(spec)
        deep = session.discover(spec, recursive=True)
        frame = session.read_file(flat[0], spec)
        notes = session.discover(_spec("crush-web", pattern="notes.txt"))[0]
        text = session.read_text(notes, spec)

    assert [f.path for f in flat] == ["/data/sales_eu.csv"]
    assert [f.path for f in deep] == ["/data/sales_eu.csv", "/data/sub/sales_us.csv"]
    assert frame.to_dict("list") == {"id": [1, 2], "amount": [10, 20]}
    assert text.startswith("READY\n")
    # one login for the whole session, and close() logged out
    assert server.commands().count("login") == 1
    assert server.commands()[-1] == "logout"


def test_sftp_protocol_runs_through_the_paramiko_path(db, monkeypatch):
    _sftp_profile(db)
    fake_sftp = FakeSFTP()
    fake_sftp.dirs.add("/data")
    fake_sftp.files["/data/sales_eu.csv"] = b"id,amount\n1,10\n"
    built = []

    def _fake_build_sftp_client(profile, spec):
        built.append((profile.name, profile.port, spec.kind))
        return fake_sftp

    monkeypatch.setattr("api.services.multi_file_remote.build_sftp_client", _fake_build_sftp_client)
    spec = _spec("crush-sftp")

    with RemoteFileSourceSession(db) as session:
        assert session.transport_for(spec) == "sftp"
        found = session.discover(spec)
        frame = session.read_file(found[0], spec)
        assert session.read_text(found[0], spec).startswith("id,amount")
        assert session.location_key_for(spec) == ("sftp", "files.example.com", 2222, "atom")

    assert built == [("crush-sftp", 2222, "crushftp")]  # built once, reused
    assert [f.path for f in found] == ["/data/sales_eu.csv"]
    assert frame.to_dict("list") == {"id": [1], "amount": [10]}


def test_transport_for_other_kinds_needs_no_profile(db):
    session = RemoteFileSourceSession(db)

    assert session.transport_for(FileSourceSpec(kind="local", root="/x", pattern="*")) == "local"
    assert session.transport_for(_spec("anything", kind="sftp")) == "sftp"


def test_location_key_for_https_uses_host_port_and_account(db, server):
    _https_profile(db, server)
    _https_profile(db, server, name="crush-web-2")
    port = int(server.base_url.rsplit(":", 1)[1])

    with RemoteFileSourceSession(db) as session:
        first = session.location_key_for(_spec("crush-web"))
        second = session.location_key_for(_spec("crush-web-2"))

    assert first == ("crushftp", "127.0.0.1", port, "atom")
    assert first == second  # same server and account under two profile names


def test_location_key_for_api_key_only_profile_uses_the_header_name(db):
    with FakeCrushFtp(api_key_header="X-Api-Key", api_key="k") as fake:
        _https_profile(
            db, fake, name="crush-key", auth_method="api_key", username=None, password=None,
            api_key_header="X-Api-Key", api_key="k",
        )
        with RemoteFileSourceSession(db) as session:
            key = session.location_key_for(_spec("crush-key"))

    assert key[0] == "crushftp" and key[3] == "X-Api-Key"
