from __future__ import annotations

import importlib
import types

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.models import SavedJob
from etl_framework.repository.repository import FileServerProfileRepository, TokenRepository
from api.main import app


@pytest.fixture
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        # Exposed so tests that need direct DB access (inserting a SavedJob
        # row, reading back through the repository) can open their own
        # Session on the same in-memory engine the app is using -- mirrors
        # the `client, engine = client` convention in
        # tests/integration/test_contracts_testing_api.py, but attached as
        # an attribute so the existing client.post(...)-style calls above
        # don't need to change.
        c.engine = engine
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def encryption_key(monkeypatch):
    """secret_store.py reads WEBHOOK_ENCRYPTION_KEY at import time and stores
    secrets as plaintext (with a warning) when it's unset -- see its module
    docstring. Without this fixture, a test asserting real encryption/decryption
    round-trips would silently exercise the plaintext fallback instead (mirrors
    the `_encryption_key` fixture in tests/unit/test_file_server_profile_repository.py)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)
    yield key
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    importlib.reload(secret_store)


def test_create_file_server_masks_secret_on_response(client):
    resp = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal",
        "port": 22, "username": "svc", "auth_method": "password", "password": "hunter2",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["password"] == "********"
    assert data["host"] == "sftp.internal"


def test_list_file_servers_masks_secrets(client):
    client.post("/api/file-servers", json={"name": "s3_prod", "kind": "s3", "aws_secret_access_key": "s3cr3t"})
    resp = client.get("/api/file-servers")
    assert resp.status_code == 200
    [entry] = resp.json()
    assert entry["aws_secret_access_key"] == "********"


def test_update_preserves_masked_secret(client):
    created = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal",
        "port": 22, "username": "svc", "auth_method": "password", "password": "hunter2",
    }).json()

    resp = client.put(f"/api/file-servers/{created['id']}", json={
        "host": "sftp2.internal", "password": created["password"],  # echoes the mask back, like the real GUI would
    })
    assert resp.status_code == 200
    assert resp.json()["host"] == "sftp2.internal"
    assert resp.json()["password"] == "********"


def test_delete_file_server(client):
    created = client.post("/api/file-servers", json={"name": "s3_prod", "kind": "s3"}).json()
    resp = client.delete(f"/api/file-servers/{created['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/file-servers/{created['id']}").status_code == 404


def test_delete_unknown_file_server_404s(client):
    assert client.delete("/api/file-servers/999").status_code == 404


def test_create_rejects_unknown_kind(client):
    resp = client.post("/api/file-servers", json={"name": "x", "kind": "ftp"})
    assert resp.status_code == 422


def test_get_unknown_file_server_404s(client):
    assert client.get("/api/file-servers/999").status_code == 404


def test_delete_blocked_when_referenced_by_saved_job(client):
    created = client.post("/api/file-servers", json={"name": "sftp_inbound", "kind": "sftp"}).json()

    with Session(client.engine) as db:
        db.add(SavedJob(
            name="job_using_profile",
            query="SELECT 1",
            params={"source": {"credentials_ref": "sftp_inbound"}},
        ))
        db.commit()

    resp = client.delete(f"/api/file-servers/{created['id']}")
    assert resp.status_code == 409
    assert client.get(f"/api/file-servers/{created['id']}").status_code == 200


def test_update_preserving_masked_secret_does_not_corrupt_stored_value(client, encryption_key):
    created = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal",
        "port": 22, "username": "svc", "auth_method": "password", "password": "hunter2",
    }).json()

    resp = client.put(f"/api/file-servers/{created['id']}", json={
        "host": "sftp2.internal", "password": created["password"],  # echoes the mask back
    })
    assert resp.status_code == 200

    with Session(client.engine) as db:
        resolved = FileServerProfileRepository(db).get_decrypted_by_name("sftp_inbound")
    assert resolved is not None
    assert resolved.password == "hunter2"  # not double-encrypted garbage
    assert resolved.host == "sftp2.internal"


def test_create_rejects_duplicate_name(client):
    first = client.post("/api/file-servers", json={"name": "s3_prod", "kind": "s3"})
    assert first.status_code == 201

    resp = client.post("/api/file-servers", json={"name": "s3_prod", "kind": "s3"})
    assert resp.status_code == 409


def test_test_connection_sftp_returns_unpinned_fingerprint_on_first_call(client, monkeypatch):
    created = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "h", "port": 22,
        "username": "u", "auth_method": "password", "password": "p",
    }).json()

    class _FakeKey:
        def asbytes(self):
            return b"fake-key-bytes"

    class _FakeTransport:
        def __init__(self, *a, **k): pass
        def connect(self, **k): pass
        def get_remote_server_key(self): return _FakeKey()
        def close(self): pass

    monkeypatch.setattr("api.routes.file_servers.paramiko.Transport", _FakeTransport)

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unpinned"
    assert body["presented_fingerprint"]


def test_test_connection_sftp_accepts_and_pins_fingerprint(client, monkeypatch):
    created = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "h", "port": 22,
        "username": "u", "auth_method": "password", "password": "p",
    }).json()

    class _FakeKey:
        def asbytes(self):
            return b"fake-key-bytes"

    class _FakeTransport:
        def __init__(self, *a, **k): pass
        def connect(self, **k): pass
        def get_remote_server_key(self): return _FakeKey()
        def close(self): pass

    monkeypatch.setattr("api.routes.file_servers.paramiko.Transport", _FakeTransport)

    first = client.post(f"/api/file-servers/{created['id']}/test").json()
    resp = client.post(f"/api/file-servers/{created['id']}/test", json={"accept_fingerprint": True})
    assert resp.json()["status"] == "ok"

    pinned = client.get(f"/api/file-servers/{created['id']}").json()
    assert pinned["host_key_fingerprint"] == first["presented_fingerprint"]


def test_test_connection_sftp_reports_mismatch_after_pinning(client, monkeypatch):
    created = client.post("/api/file-servers", json={
        "name": "sftp_inbound", "kind": "sftp", "host": "h", "port": 22,
        "username": "u", "auth_method": "password", "password": "p",
    }).json()
    client.put(f"/api/file-servers/{created['id']}", json={"host_key_fingerprint": "deadbeef"})

    class _FakeKey:
        def asbytes(self):
            return b"different-key-bytes"

    class _FakeTransport:
        def __init__(self, *a, **k): pass
        def connect(self, **k): pass
        def get_remote_server_key(self): return _FakeKey()
        def close(self): pass

    monkeypatch.setattr("api.routes.file_servers.paramiko.Transport", _FakeTransport)

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.json()["status"] == "mismatch"


def test_test_connection_sftp_with_private_key_auth(client, monkeypatch):
    """Regression coverage for the private_key auth branch, which used to call
    the broken paramiko.PKey.from_private_key(io.StringIO(...)) directly --
    that raises TypeError on the paramiko version this project pins, since
    from_private_key only works on a concrete key subclass (RSAKey/
    Ed25519Key/ECDSAKey), not the abstract PKey base. The endpoint now reuses
    _load_sftp_private_key (api/services/multi_file_remote.py) instead, and
    this test proves that helper really runs end-to-end here and produces a
    real key object that gets passed to transport.connect(pkey=...)."""
    import io
    import paramiko

    generated_key = paramiko.RSAKey.generate(1024)
    key_buf = io.StringIO()
    generated_key.write_private_key(key_buf)
    private_key_pem = key_buf.getvalue()

    created = client.post("/api/file-servers", json={
        "name": "sftp_keyauth", "kind": "sftp", "host": "h", "port": 22,
        "username": "u", "auth_method": "private_key", "private_key": private_key_pem,
    }).json()

    class _FakeKey:
        def asbytes(self):
            return b"fake-key-bytes"

    connect_calls = []

    class _FakeTransport:
        def __init__(self, *a, **k): pass
        def connect(self, **k): connect_calls.append(k)
        def get_remote_server_key(self): return _FakeKey()
        def close(self): pass

    monkeypatch.setattr("api.routes.file_servers.paramiko.Transport", _FakeTransport)

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.status_code == 200
    assert resp.json()["status"] == "unpinned"
    assert len(connect_calls) == 1
    assert "pkey" in connect_calls[0]
    assert isinstance(connect_calls[0]["pkey"], paramiko.RSAKey)


def test_test_connection_sftp_unresolvable_host_returns_clean_error(client, monkeypatch):
    """Regression coverage for the bug fixed in 0af0fc9: paramiko.Transport.__init__
    resolves the host (socket.getaddrinfo) synchronously, so an unresolvable host
    raises from the *constructor itself* -- before transport.connect() is ever
    reached. The endpoint's try/except must wrap the Transport(...) construction
    call, not just the connect/auth calls after it, or this surfaces as an
    unhandled 500 instead of FileServerTestResult(status="error"). Every other
    _FakeTransport in this file has a no-op __init__, which can't catch a
    regression that moves the construction back outside the try -- this one's
    __init__ is the part that raises."""
    created = client.post("/api/file-servers", json={
        "name": "sftp_unresolvable", "kind": "sftp",
        "host": "this-host-definitely-does-not-exist.invalid", "port": 22,
        "username": "u", "auth_method": "password", "password": "p",
    }).json()

    class _FakeTransport:
        def __init__(self, *a, **k):
            import socket
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr("api.routes.file_servers.paramiko.Transport", _FakeTransport)

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "Name or service not known" in body["message"]


def test_create_smb_file_server(client):
    resp = client.post("/api/file-servers", json={
        "name": "vendor-share", "kind": "smb", "host": "fileserver01",
        "username": "CORP\\svc-atom", "password": "s3cret",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "smb"
    assert body["password"] == "********"


def test_test_connection_smb_reports_clear_error_without_real_windows_net_use(client, monkeypatch):
    created = client.post("/api/file-servers", json={
        "name": "vendor-share-2", "kind": "smb", "host": "fileserver02",
        "username": "svc", "password": "s3cret",
    }).json()
    # Force the route's platform guard down the Windows branch regardless of
    # the OS actually running this test, so it behaves the same in CI on
    # Linux as it does on a Windows dev box.
    monkeypatch.setattr("api.routes.file_servers.os", types.SimpleNamespace(name="nt"))

    net_use_calls = []
    delete_calls = []

    def _raise(resource, u, p, port=None):
        net_use_calls.append((resource, u, p))
        from api.services.multi_file_remote import SmbConnectError
        raise SmbConnectError("net use \\\\fileserver02\\IPC$ failed: System error 53 has occurred.")

    monkeypatch.setattr("api.services.multi_file_remote._net_use", _raise)
    monkeypatch.setattr("api.services.multi_file_remote._net_use_delete", lambda resource: delete_calls.append(resource))

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "System error 53" in body["message"]
    assert "s3cret" not in resp.text
    # The real (decrypted) password is what gets forwarded to _net_use, not the mask.
    assert net_use_calls == [("\\\\fileserver02\\IPC$", "svc", "s3cret")]
    # Teardown still runs even though _net_use failed.
    assert delete_calls == ["\\\\fileserver02\\IPC$"]


def test_test_connection_smb_succeeds_and_always_disconnects(client, monkeypatch):
    created = client.post("/api/file-servers", json={
        "name": "vendor-share-3", "kind": "smb", "host": "fileserver03",
        "username": "svc", "password": "s3cret",
    }).json()
    monkeypatch.setattr("api.routes.file_servers.os", types.SimpleNamespace(name="nt"))

    calls = []
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p, port=None: calls.append(("use", resource, u, p)))
    monkeypatch.setattr("api.services.multi_file_remote._net_use_delete", lambda resource: calls.append(("delete", resource)))

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "presented_fingerprint": None, "pinned_fingerprint": None, "message": None}
    # The real (decrypted) password is what gets forwarded to _net_use, not the mask.
    assert calls == [("use", "\\\\fileserver03\\IPC$", "svc", "s3cret"), ("delete", "\\\\fileserver03\\IPC$")]


def test_test_connection_smb_forwards_an_alternative_port(client, monkeypatch):
    created = client.post("/api/file-servers", json={
        "name": "vendor-share-alt-port", "kind": "smb", "host": "127.0.0.1", "port": 1445,
        "username": "svc", "password": "s3cret",
    }).json()
    assert created["port"] == 1445
    monkeypatch.setattr("api.routes.file_servers.os", types.SimpleNamespace(name="nt"))

    calls = []
    monkeypatch.setattr(
        "api.services.multi_file_remote._net_use",
        lambda resource, u, p, port=None: calls.append((resource, port)),
    )
    monkeypatch.setattr("api.services.multi_file_remote._net_use_delete", lambda resource: None)

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.json()["status"] == "ok"
    assert calls == [("\\\\127.0.0.1\\IPC$", 1445)]


def test_test_connection_smb_reports_clear_error_on_non_windows(client, monkeypatch):
    created = client.post("/api/file-servers", json={
        "name": "vendor-share-4", "kind": "smb", "host": "fileserver04",
        "username": "svc", "password": "s3cret",
    }).json()
    monkeypatch.setattr("api.routes.file_servers.os", types.SimpleNamespace(name="posix"))

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "Windows" in body["message"]


def test_file_servers_module_imports_without_paramiko_installed(monkeypatch):
    """api/routes/file_servers.py must not hard-crash app startup just because
    paramiko isn't installed -- SFTP/SCP is one optional feature among several
    (s3-only deployments, or environments that just never ran `pip install -r
    requirements.txt` since paramiko was added, have every reason to start
    fine). Reproduces this by blocking `import paramiko` the way Python would
    if the package were genuinely absent, then reloading this module and
    confirming that doesn't raise. Every other paramiko/boto3 user in this
    codebase (api/services/multi_file_remote.py) imports lazily inside the
    function that needs it for exactly this reason -- this module's top-level
    `import paramiko` was the one place that didn't."""
    import builtins
    import importlib
    import sys

    real_import = builtins.__import__

    def _blocked_import(name, *args, **kwargs):
        if name == "paramiko" or name.startswith("paramiko."):
            raise ImportError("No module named 'paramiko'")
        return real_import(name, *args, **kwargs)

    for mod_name in [m for m in sys.modules if m == "paramiko" or m.startswith("paramiko.")]:
        monkeypatch.delitem(sys.modules, mod_name, raising=False)
    monkeypatch.setattr(builtins, "__import__", _blocked_import)

    import api.routes.file_servers as file_servers_module
    try:
        importlib.reload(file_servers_module)  # must not raise ImportError/ModuleNotFoundError
    finally:
        monkeypatch.undo()
        importlib.reload(file_servers_module)  # restore real paramiko for every later test in this file
