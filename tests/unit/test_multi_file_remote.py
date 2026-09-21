# tests/unit/test_multi_file_remote.py
from __future__ import annotations

import concurrent.futures

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.services.multi_file_remote import (
    RemoteFileSourceSession,
    resolve_file_server_profile,
)
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import FileServerProfileRepository
from etl_framework.reconciliation.file_mapping import FileSourceSpec


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # RemoteFileSourceSession._client_for resolves each pair's
    # FileServerProfile on its own short-lived SessionLocal() session, not
    # the db Session this fixture hands to tests -- rebind SessionLocal to
    # this same in-memory engine so that fresh session sees the profiles
    # tests create below instead of hitting the real on-disk sqlite db.
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as session:
        yield session


@pytest.fixture
def file_db_session_local(tmp_path, monkeypatch):
    """Rebinds SessionLocal to a real *file-backed* sqlite database (default
    connection pooling, not StaticPool) and returns a sessionmaker bound to
    that same engine for writing fixture rows.

    The plain `db` fixture above uses an in-memory sqlite engine with
    StaticPool, which funnels every session through ONE shared underlying
    sqlite3 connection -- fine for sequential single-session tests, but it
    silently reintroduces a concurrency hazard for a test that specifically
    wants to prove multiple independent SessionLocal() sessions are safe to
    use from separate threads at once: concurrent threads all issuing
    queries against that one shared raw connection race each other
    regardless of how many Session objects wrap it. A real file gives each
    checked-out session its own independent DBAPI connection (as it does in
    production), so this fixture is what the concurrency test needs to
    actually exercise the fix rather than a StaticPool artifact.
    """
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'concurrency_test.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    monkeypatch.setattr(_db_module, "SessionLocal", session_local)
    return session_local


def test_resolve_file_server_profile_by_ref(db):
    FileServerProfileRepository(db).create({
        "name": "sftp_source", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "secret",
    })
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="sftp_source")

    profile = resolve_file_server_profile(db, spec)

    assert profile.host == "sftp.internal"
    assert profile.password == "secret"


def test_resolve_file_server_profile_returns_none_without_ref(db):
    spec = FileSourceSpec(kind="local", root="/source", pattern="*.csv")
    assert resolve_file_server_profile(db, spec) is None


def test_resolve_file_server_profile_raises_for_unknown_ref(db):
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="does_not_exist")
    with pytest.raises(ValueError, match="No file server profile named 'does_not_exist'"):
        resolve_file_server_profile(db, spec)


def test_resolve_file_server_profile_raises_for_kind_mismatch(db):
    FileServerProfileRepository(db).create({"name": "s3_prod", "kind": "s3", "aws_access_key_id": "AKIA"})
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="s3_prod")
    with pytest.raises(ValueError, match="is kind 's3', not 'sftp'"):
        resolve_file_server_profile(db, spec)


def test_resolve_file_server_profile_allows_scp_location_with_sftp_or_scp_kind_profile(db):
    FileServerProfileRepository(db).create({
        "name": "scp_source", "kind": "scp", "host": "h", "username": "u", "auth_method": "password", "password": "p",
    })
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="scp_source")
    assert resolve_file_server_profile(db, spec).name == "scp_source"


def test_build_s3_client_raises_clear_error_when_profile_is_none() -> None:
    """A misconfigured multi_file source can have kind='s3'/'sftp' with no
    credentials_ref -- file_mapping's own parsing doesn't require one the way
    file_watcher's validator does. Without this guard, build_s3_client(None,
    spec) would crash on `profile.aws_access_key_id` with a bare, unhelpful
    AttributeError deep inside client construction instead of a clear error."""
    from api.services.multi_file_remote import build_s3_client

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="*.csv")
    with pytest.raises(ValueError, match="'s3' source requires credentials_ref"):
        build_s3_client(None, spec)


def test_build_sftp_client_raises_clear_error_when_profile_is_none() -> None:
    from api.services.multi_file_remote import build_sftp_client

    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv")
    with pytest.raises(ValueError, match="'sftp' source requires credentials_ref"):
        build_sftp_client(None, spec)


def test_build_sftp_client_verifies_host_key_before_authenticating(db, monkeypatch) -> None:
    """Host key fingerprint verification must happen BEFORE any credential
    material (password or key signature) is sent -- otherwise a MITM'd or
    unpinned host would receive the password/signature before we ever detect
    the mismatch and abort, defeating the point of pinning a host key at all.
    Simulates a mismatched/attacker-presented host key and proves neither
    auth_password nor auth_publickey is ever invoked."""
    import paramiko
    from api.services.multi_file_remote import build_sftp_client, resolve_file_server_profile
    from etl_framework.repository.repository import FileServerProfileRepository

    class _FakeKey:
        def asbytes(self) -> bytes:
            return b"attacker-presented-key-bytes"

    class _FakeTransport:
        def __init__(self, addr) -> None:
            self.addr = addr
            self.closed = False

        def start_client(self) -> None:
            pass

        def get_remote_server_key(self):
            return _FakeKey()

        def auth_password(self, username, password) -> None:
            raise AssertionError("auth_password must not be called before host key verification passes")

        def auth_publickey(self, username, key) -> None:
            raise AssertionError("auth_publickey must not be called before host key verification passes")

        def close(self) -> None:
            self.closed = True

    created: list[_FakeTransport] = []
    monkeypatch.setattr(paramiko, "Transport", lambda addr: created.append(_FakeTransport(addr)) or created[-1])

    FileServerProfileRepository(db).create({
        "name": "sftp_mitm", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "secret",
        "host_key_fingerprint": "0" * 64,  # will never match _FakeKey's fingerprint
    })
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="sftp_mitm")
    profile = resolve_file_server_profile(db, spec)

    with pytest.raises(RuntimeError, match="Host key verification failed"):
        build_sftp_client(profile, spec)

    assert created[0].closed is True


def test_build_sftp_client_authenticates_with_password_when_host_key_matches(db, monkeypatch) -> None:
    """Positive path: when the presented host key's fingerprint DOES match
    the pinned fingerprint, authentication must actually be attempted with
    the resolved credentials -- the mismatch test above only proves the block
    case, this proves verification doesn't also block the legitimate case."""
    import hashlib
    import paramiko
    from api.services.multi_file_remote import build_sftp_client, resolve_file_server_profile
    from etl_framework.repository.repository import FileServerProfileRepository

    presented_bytes = b"trusted-server-key-bytes"
    fingerprint = hashlib.sha256(presented_bytes).hexdigest()
    auth_calls: list[tuple] = []

    class _FakeKey:
        def asbytes(self) -> bytes:
            return presented_bytes

    class _FakeTransport:
        def __init__(self, addr) -> None:
            self.addr = addr
            self.closed = False

        def start_client(self) -> None:
            pass

        def get_remote_server_key(self):
            return _FakeKey()

        def auth_password(self, username, password) -> None:
            auth_calls.append(("password", username, password))

        def auth_publickey(self, username, key) -> None:
            auth_calls.append(("publickey", username, key))

        def close(self) -> None:
            self.closed = True

    fake_sftp_client = object()
    monkeypatch.setattr(paramiko, "Transport", _FakeTransport)
    monkeypatch.setattr(paramiko.SFTPClient, "from_transport", lambda t: fake_sftp_client)

    FileServerProfileRepository(db).create({
        "name": "sftp_trusted", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "secret",
        "host_key_fingerprint": fingerprint,
    })
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="sftp_trusted")
    profile = resolve_file_server_profile(db, spec)

    client = build_sftp_client(profile, spec)

    assert auth_calls == [("password", "svc", "secret")]
    assert client is fake_sftp_client


def test_build_sftp_client_authenticates_with_private_key_when_host_key_matches(db, monkeypatch) -> None:
    """auth_method='private_key' must parse the stored PEM text into a real
    paramiko key and authenticate with auth_publickey (not auth_password),
    passing the key_passphrase through when the key itself is encrypted."""
    import hashlib
    import io
    import paramiko
    from api.services.multi_file_remote import build_sftp_client, resolve_file_server_profile
    from etl_framework.repository.repository import FileServerProfileRepository

    generated_key = paramiko.RSAKey.generate(1024)
    key_buf = io.StringIO()
    generated_key.write_private_key(key_buf, password="key-pass")
    private_key_pem = key_buf.getvalue()

    presented_bytes = b"trusted-server-key-bytes"
    fingerprint = hashlib.sha256(presented_bytes).hexdigest()
    auth_calls: list[tuple] = []

    class _FakeKey:
        def asbytes(self) -> bytes:
            return presented_bytes

    class _FakeTransport:
        def __init__(self, addr) -> None:
            self.addr = addr
            self.closed = False

        def start_client(self) -> None:
            pass

        def get_remote_server_key(self):
            return _FakeKey()

        def auth_password(self, username, password) -> None:
            raise AssertionError("auth_password must not be called for auth_method=private_key")

        def auth_publickey(self, username, key) -> None:
            auth_calls.append(("publickey", username, key))

        def close(self) -> None:
            self.closed = True

    fake_sftp_client = object()
    monkeypatch.setattr(paramiko, "Transport", _FakeTransport)
    monkeypatch.setattr(paramiko.SFTPClient, "from_transport", lambda t: fake_sftp_client)

    FileServerProfileRepository(db).create({
        "name": "sftp_key_auth", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "private_key",
        "private_key": private_key_pem, "key_passphrase": "key-pass",
        "host_key_fingerprint": fingerprint,
    })
    spec = FileSourceSpec(kind="sftp", root="/source", pattern="*.csv", credentials_ref="sftp_key_auth")
    profile = resolve_file_server_profile(db, spec)

    client = build_sftp_client(profile, spec)

    assert len(auth_calls) == 1
    method, username, key = auth_calls[0]
    assert method == "publickey"
    assert username == "svc"
    assert key.get_base64() == generated_key.get_base64()
    assert client is fake_sftp_client


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


def test_remote_file_source_session_reuses_one_s3_client_across_discover_and_reads(db, monkeypatch) -> None:
    """The whole point of RemoteFileSourceSession: N file reads against the
    same source spec must not open N connections."""
    built_clients: list[_FakeS3Client] = []

    def _fake_build_s3_client(profile, spec):
        client = _FakeS3Client()
        built_clients.append(client)
        return client

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="sales_{region}.csv")
    session = RemoteFileSourceSession(db)

    discovered = session.discover(spec)
    assert len(discovered) == 1
    df = session.read_file(discovered[0], spec)
    assert isinstance(df, pd.DataFrame)
    # A second read against the same spec must reuse the cached client too.
    session.read_file(discovered[0], spec)

    assert len(built_clients) == 1  # exactly one client built for this (kind, credentials_ref)
    session.close()
    assert built_clients[0].closed is True


def test_remote_file_source_session_builds_separate_clients_per_credentials_ref(db, monkeypatch) -> None:
    built = []

    def _fake_build_s3_client(profile, spec):
        client = _FakeS3Client()
        built.append((spec.credentials_ref, client))
        return client

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    FileServerProfileRepository(db).create({"name": "ref_a", "kind": "s3", "aws_access_key_id": "AKIA_A"})
    FileServerProfileRepository(db).create({"name": "ref_b", "kind": "s3", "aws_access_key_id": "AKIA_B"})

    session = RemoteFileSourceSession(db)
    spec_a = FileSourceSpec(kind="s3", root="s3://bucket/a", pattern="*.csv", credentials_ref="ref_a")
    spec_b = FileSourceSpec(kind="s3", root="s3://bucket/b", pattern="*.csv", credentials_ref="ref_b")

    session.discover(spec_a)
    session.discover(spec_b)
    session.discover(spec_a)  # reuses ref_a's client, not a third build

    assert len(built) == 2
    assert {ref for ref, _ in built} == {"ref_a", "ref_b"}


def test_remote_file_source_session_local_kind_needs_no_client(db, tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "sales_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")

    session = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="local", root=str(tmp_path), pattern="sales_{region}.csv")

    discovered = session.discover(spec)
    assert len(discovered) == 1
    df = session.read_file(discovered[0], spec)
    assert list(df.columns) == ["id", "value"]

    session.close()  # no-op: no remote clients were ever built


def test_remote_file_source_session_read_text_local(db, tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "DONE.flag").write_bytes(b"STATUS=COMPLETE\n")

    session = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="local", root=str(tmp_path), pattern="DONE.flag")
    discovered = session.discover(spec)

    assert session.read_text(discovered[0], spec) == "STATUS=COMPLETE\n"


def test_remote_file_source_session_read_text_s3(db, monkeypatch) -> None:
    built_clients: list[_FakeS3Client] = []

    def _fake_build_s3_client(profile, spec):
        client = _FakeS3Client()
        built_clients.append(client)
        return client

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="sales_{region}.csv")
    session = RemoteFileSourceSession(db)
    discovered = session.discover(spec)

    text = session.read_text(discovered[0], spec)
    assert text == "id,value\n1,alpha\n"


class _FakeS3ClientInvalidRangeOnEmptyObject:
    """Like _FakeS3Client, but its one object is 0 bytes and get_object raises
    the ClientError real S3 (and S3-compatible stores) return when a Range
    header is sent against an empty object -- HTTP 416 / error code
    InvalidRange. Used to prove _read_bytes's S3 branch treats that as an
    empty read instead of letting the error propagate."""

    def __init__(self) -> None:
        self.objects = {"prefix/DONE.flag": b""}
        self.closed = False

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        prefix = kwargs["Prefix"]
        return [{"Contents": [{"Key": key} for key in self.objects if key.startswith(prefix)]}]

    def get_object(self, **kwargs):
        import botocore.exceptions

        raise botocore.exceptions.ClientError(
            error_response={"Error": {"Code": "InvalidRange", "Message": "The requested range is not satisfiable"}},
            operation_name="GetObject",
        )

    def close(self) -> None:
        self.closed = True


def test_remote_file_source_session_read_text_s3_empty_object_returns_empty_string(db, monkeypatch) -> None:
    """A 0-byte S3 object (e.g. a completion-flag file created but not yet
    written) must read as "" rather than raising -- read_text's own docstring
    promises a partial/empty snapshot reads as "no match", not an error."""
    monkeypatch.setattr(
        "api.services.multi_file_remote.build_s3_client",
        lambda profile, spec: _FakeS3ClientInvalidRangeOnEmptyObject(),
    )

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="DONE.flag")
    session = RemoteFileSourceSession(db)
    discovered = session.discover(spec)

    assert session.read_text(discovered[0], spec) == ""


def test_remote_file_source_session_read_text_caps_length(db, tmp_path, monkeypatch) -> None:
    from api.services import file_source
    from api.services.multi_file_remote import _CONTENT_MATCH_READ_LIMIT

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "BIG.flag").write_bytes(b"x" * (_CONTENT_MATCH_READ_LIMIT * 2))

    session = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="local", root=str(tmp_path), pattern="BIG.flag")
    discovered = session.discover(spec)

    assert len(session.read_text(discovered[0], spec)) == _CONTENT_MATCH_READ_LIMIT


def test_remote_file_source_session_rejects_unknown_kind(db) -> None:
    # FileSourceSpec's own constructor doesn't validate `kind` (that check
    # lives in _parse_file_source, the config-parsing entry point) -- this
    # exercises RemoteFileSourceSession's own defense-in-depth check.
    session = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="ftp", root="ftp://x", pattern="*.csv")

    with pytest.raises(ValueError, match="Unsupported multi_file source kind"):
        session.discover(spec)


def test_client_for_resolves_profiles_safely_across_concurrent_threads(file_db_session_local, monkeypatch) -> None:
    """Reproduces the original production bug: RunExecutor runs every
    multi_file pair concurrently in its own TestRunner worker thread (up to
    max_workers=4 by default), and each pair's RemoteFileSourceSession is
    constructed with the SAME caller-supplied db Session -- but SQLAlchemy's
    Session is documented as not safe for concurrent use across threads.
    When _client_for resolved credentials via self._db, this produced
    intermittent spurious "No file server profile named ..." ValueErrors
    (and worse, corrupted-cursor IndexErrors) under real concurrent access.

    This drives many real threads (not sequential calls) through
    RemoteFileSourceSession(shared_db)._client_for(spec), all sharing one
    Session object exactly like RunExecutor does, and asserts every call
    resolves the correct profile with no exception -- which is only
    reliably true once _client_for stops touching self._db and instead
    opens its own short-lived SessionLocal() session per call.
    """
    build_calls: list[str] = []

    def _fake_build_s3_client(profile, spec):
        build_calls.append(profile.name)
        return object()

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    with file_db_session_local() as setup_db:
        FileServerProfileRepository(setup_db).create({"name": "ref_a", "kind": "s3", "aws_access_key_id": "AKIA_A"})
        FileServerProfileRepository(setup_db).create({"name": "ref_b", "kind": "s3", "aws_access_key_id": "AKIA_B"})

    errors: list[BaseException] = []

    # The one Session every pair's RemoteFileSourceSession shares -- exactly
    # how RunExecutor.__init__ creates a single self._db and passes it to
    # RemoteFileSourceSession(self._db) once per pair.
    with file_db_session_local() as shared_db:

        def _resolve_one(ref: str) -> None:
            spec = FileSourceSpec(kind="s3", root=f"s3://bucket/{ref}", pattern="*.csv", credentials_ref=ref)
            session = RemoteFileSourceSession(shared_db)
            try:
                session._client_for(spec)
            except BaseException as exc:  # noqa: BLE001 -- capture anything, including SQLAlchemy internals
                errors.append(exc)

        refs = (["ref_a", "ref_b"] * 15)  # 30 calls total, alternating credentials_ref
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_resolve_one, ref) for ref in refs]
            for future in futures:
                future.result()

    assert errors == [], f"concurrent _client_for calls raised: {errors!r}"
    assert len(build_calls) == len(refs)
    assert set(build_calls) == {"ref_a", "ref_b"}


def test_remote_file_source_session_context_manager_closes_clients(db, monkeypatch) -> None:
    built_clients: list[_FakeS3Client] = []

    def _fake_build_s3_client(profile, spec):
        client = _FakeS3Client()
        built_clients.append(client)
        return client

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="sales_{region}.csv")
    with RemoteFileSourceSession(db) as session:
        session.discover(spec)

    assert built_clients[0].closed is True


def test_build_s3_client_forces_path_style_addressing_for_custom_endpoint(db, monkeypatch) -> None:
    """A custom endpoint_url means a non-AWS S3-compatible target (MinIO, on-prem
    object storage) -- these commonly reject virtual-hosted-style bucket addressing,
    which boto3 otherwise defaults to whenever endpoint_url is set. Real AWS never
    sets endpoint_url, so this must not fire for the existing real-AWS path."""
    import boto3
    from api.services.multi_file_remote import build_s3_client, resolve_file_server_profile
    from etl_framework.reconciliation.file_mapping import FileSourceSpec
    from etl_framework.repository.repository import FileServerProfileRepository

    captured_kwargs: dict = {}

    def _capture(service_name, **kwargs):
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", _capture)

    FileServerProfileRepository(db).create({
        "name": "minio", "kind": "s3", "aws_access_key_id": "minioadmin",
        "aws_secret_access_key": "minioadmin", "endpoint_url": "http://127.0.0.1:19000", "region_name": "us-east-1",
    })
    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="*.csv", credentials_ref="minio")

    build_s3_client(resolve_file_server_profile(db, spec), spec)

    assert captured_kwargs["endpoint_url"] == "http://127.0.0.1:19000"
    assert captured_kwargs["config"].s3["addressing_style"] == "path"


def test_build_s3_client_does_not_force_path_style_without_custom_endpoint(db, monkeypatch) -> None:
    """Real AWS (no endpoint_url set) must keep boto3's default addressing --
    this is the existing, already-working production path; it must not regress."""
    import boto3
    from api.services.multi_file_remote import build_s3_client, resolve_file_server_profile
    from etl_framework.reconciliation.file_mapping import FileSourceSpec
    from etl_framework.repository.repository import FileServerProfileRepository

    captured_kwargs: dict = {}

    def _capture(service_name, **kwargs):
        captured_kwargs.update(kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", _capture)

    FileServerProfileRepository(db).create({
        "name": "aws_prod", "kind": "s3", "aws_access_key_id": "AKIA...", "aws_secret_access_key": "s3cr3t",
    })
    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="*.csv", credentials_ref="aws_prod")

    build_s3_client(resolve_file_server_profile(db, spec), spec)

    assert captured_kwargs.get("endpoint_url") is None
    assert "config" not in captured_kwargs


def test_remote_file_source_session_discover_recursive_local(db, tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "sub").mkdir()
    (tmp_path / "top.csv").write_text("id\n1\n", encoding="utf-8")
    (tmp_path / "sub" / "nested.csv").write_text("id\n2\n", encoding="utf-8")

    session = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="local", root=str(tmp_path), pattern="*.csv")

    assert [f.file_name for f in session.discover(spec)] == ["top.csv"]
    # Local recursive discovery sorts by path string, so "sub/nested.csv" comes before "top.csv".
    assert [f.file_name for f in session.discover(spec, recursive=True)] == ["nested.csv", "top.csv"]


def test_remote_file_source_session_client_for_is_public_and_cached(db, monkeypatch) -> None:
    built: list[str] = []

    def _fake_build_s3_client(profile, spec):
        built.append(spec.credentials_ref)
        return object()

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)
    monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db_, spec: None)
    monkeypatch.setattr("api.services.multi_file_remote.discover_s3_files", lambda client, root, pattern, **kwargs: [])

    session = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="s3", root="s3://bkt/x", pattern="*", credentials_ref="prof")

    assert session.discover(spec) == []
    assert session.client_for(spec) is session.client_for(spec)
    assert built == ["prof"]


def test_remote_file_source_session_close_survives_a_failing_client(db) -> None:
    class _Client:
        def __init__(self, fail: bool) -> None:
            self.fail = fail
            self.close_attempted = False

        def close(self) -> None:
            self.close_attempted = True
            if self.fail:
                raise RuntimeError("close failed")

    failing, healthy = _Client(fail=True), _Client(fail=False)
    session = RemoteFileSourceSession(db)
    session._clients[("sftp", "a")] = failing
    session._clients[("sftp", "b")] = healthy

    session.close()  # must not raise

    assert failing.close_attempted is True
    assert healthy.close_attempted is True
    assert session._clients == {}
