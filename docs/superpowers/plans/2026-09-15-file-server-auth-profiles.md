# File Server Authentication Profiles (SFTP / SCP / S3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw, unencrypted, hand-typed-per-launch `file_source_credentials` mechanism for SFTP/SCP/S3 `file_watcher`/`file_mapping` sources with persisted, encrypted, named `FileServerProfile` records — manageable through a new API and GUI — plus SSH key-pair auth and pinned host-key fingerprint verification for SFTP/SCP.

**Architecture:** New `FileServerProfile` table + repository (encrypt-at-rest via the existing `secret_store`, mirroring `ConfigRepository`'s pattern). New `api/routes/file_servers.py` CRUD + test-connection router, mirroring `api/routes/configs.py`'s mask/preserve-secret pattern. `api/services/multi_file_remote.py`'s credential resolution moves from a `config_snapshot` dict lookup to a DB-backed profile lookup; `build_sftp_client`/`build_s3_client` become pure functions of a resolved profile and gain key-auth + host-key verification. Three existing call sites (`run_executor.py`, `difference_export.py`, `jobs.py`'s preview endpoint) switch from passing a `config_snapshot` dict to passing the `db: Session` each already has in scope. GUI adds a File Servers management tab and turns the existing free-text `credentials_ref` inputs into dropdowns.

**Tech Stack:** Python (FastAPI, SQLAlchemy, paramiko, boto3, cryptography/Fernet via existing `secret_store`), Alpine.js frontend, pytest / Playwright.

**Design doc:** [docs/superpowers/specs/2026-09-15-file-server-auth-profiles-design.md](../specs/2026-09-15-file-server-auth-profiles-design.md)

**Scoping note (deviation from the design doc, decided during planning):** The design doc's §3 mentioned extending `job_validation.py`'s pure `validate_job_definition()` to check profile existence in the DB. `validate_job_definition` is a pure function called from 3 route handlers and ~90 existing unit tests with no `db` parameter anywhere in its call chain — threading a DB session through it would be a large, disproportionate signature change across all of them. Instead, the DB-backed "does this profile exist and match this kind" check lives in `resolve_file_server_profile()` (Task 5), which is exactly where credential resolution already happens for every real execution and the synchronous preview endpoint alike — it raises a clear, typed error there. `job_validation.py`'s existing pure structural check (non-empty `credentials_ref` string) is unchanged.

---

### Task 1: `FileServerProfile` model

**Files:**
- Modify: `etl_framework/repository/models.py`

- [ ] **Step 1: Add the model**

Add after the `JobLineageEdge` class (after line 317, before the `# P0 — Auth` section) in `etl_framework/repository/models.py`:

```python
# ---------------------------------------------------------------------------
# File server authentication profiles (SFTP / SCP / S3)
# ---------------------------------------------------------------------------

class FileServerProfile(Base):
    __tablename__ = "file_server_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    kind = Column(String(10), nullable=False)  # "sftp" | "scp" | "s3" -- scp uses the sftp client/fields too
    description = Column(Text, nullable=False, default="")

    # sftp / scp
    host = Column(String(255), nullable=True)
    port = Column(Integer, nullable=False, default=22)
    username = Column(String(255), nullable=True)
    auth_method = Column(String(20), nullable=True)   # "password" | "private_key"
    password = Column(Text, nullable=True)
    private_key = Column(Text, nullable=True)
    key_passphrase = Column(Text, nullable=True)
    host_key_fingerprint = Column(String(128), nullable=True)

    # s3
    aws_access_key_id = Column(String(255), nullable=True)
    aws_secret_access_key = Column(Text, nullable=True)
    aws_session_token = Column(Text, nullable=True)
    region_name = Column(String(50), nullable=True)
    endpoint_url = Column(String(1024), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
```

No migration step is needed beyond this: `etl_framework/repository/database.py:28` runs `Base.metadata.create_all(bind=engine)` on import, which picks up any new `Base` subclass automatically (same as every other table in this file).

- [ ] **Step 2: Verify the table is created**

Run: `python -c "from etl_framework.repository.database import Base, engine; import etl_framework.repository.models; Base.metadata.create_all(bind=engine); print('file_server_profiles' in Base.metadata.tables)"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add etl_framework/repository/models.py
git commit -m "feat: add FileServerProfile model for SFTP/SCP/S3 auth profiles"
```

---

### Task 2: `FileServerProfileRepository`

**Files:**
- Modify: `etl_framework/repository/repository.py`
- Test: `tests/unit/test_file_server_profile_repository.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_file_server_profile_repository.py`:

```python
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401 -- registers ORM models with Base
from etl_framework.repository.repository import FileServerProfileRepository


def _db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_create_encrypts_secret_fields_at_rest():
    db = _db()
    repo = FileServerProfileRepository(db)
    repo.create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })

    raw = db.query(
        __import__("etl_framework.repository.models", fromlist=["FileServerProfile"]).FileServerProfile
    ).filter_by(name="sftp_inbound").one()
    assert raw.password != "hunter2"  # stored encrypted, not plaintext


def test_get_by_name_returns_decrypted_view():
    db = _db()
    repo = FileServerProfileRepository(db)
    repo.create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })

    profile = repo.get_decrypted_by_name("sftp_inbound")

    assert profile is not None
    assert profile.password == "hunter2"
    assert profile.host == "sftp.internal"


def test_get_decrypted_by_name_returns_none_for_unknown_name():
    db = _db()
    repo = FileServerProfileRepository(db)
    assert repo.get_decrypted_by_name("does_not_exist") is None


def test_update_preserves_unspecified_fields():
    db = _db()
    repo = FileServerProfileRepository(db)
    created = repo.create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })

    repo.update(created.id, {"host": "sftp2.internal"})

    profile = repo.get_decrypted_by_name("sftp_inbound")
    assert profile.host == "sftp2.internal"
    assert profile.password == "hunter2"  # untouched field survives the update


def test_delete_removes_the_row():
    db = _db()
    repo = FileServerProfileRepository(db)
    created = repo.create({"name": "x", "kind": "s3", "aws_access_key_id": "AKIA"})

    assert repo.delete(created.id) is True
    assert repo.get_decrypted_by_name("x") is None
    assert repo.delete(created.id) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_server_profile_repository.py -v`
Expected: FAIL with `ImportError: cannot import name 'FileServerProfileRepository'`

- [ ] **Step 3: Implement the repository**

Add to `etl_framework/repository/repository.py`, after `RunBatchRepository` (after line 1027, before the stray `import hashlib` block that follows it):

```python
_FILE_SERVER_SECRET_FIELDS = frozenset({"password", "private_key", "key_passphrase", "aws_secret_access_key", "aws_session_token"})


class FileServerProfileRepository:
    """CRUD + encrypt-at-rest for FileServerProfile. Kept separate from
    ConfigRepository's `_transform_secret_fields` (which walks a JSON blob
    keyed by the shared SECRET_FIELDS set) because this is a plain table with
    its own explicit field list -- a generic name like `password` here would
    otherwise collide with unrelated uses of SECRET_FIELDS elsewhere."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def _encrypt_fields(self, data: dict) -> dict:
        from api.services.secret_store import encrypt_secret
        return {
            k: (encrypt_secret(v) if k in _FILE_SERVER_SECRET_FIELDS and isinstance(v, str) and v else v)
            for k, v in data.items()
        }

    def create(self, data: dict) -> FileServerProfile:
        profile = FileServerProfile(**self._encrypt_fields(data))
        self._db.add(profile)
        self._db.commit()
        self._db.refresh(profile)
        return profile

    def list(self) -> list[FileServerProfile]:
        return self._db.query(FileServerProfile).order_by(FileServerProfile.name).all()

    def get(self, profile_id: int) -> FileServerProfile | None:
        return self._db.get(FileServerProfile, profile_id)

    def update(self, profile_id: int, data: dict) -> FileServerProfile | None:
        profile = self._db.get(FileServerProfile, profile_id)
        if profile is None:
            return None
        for key, value in self._encrypt_fields(data).items():
            setattr(profile, key, value)
        profile.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(profile)
        return profile

    def delete(self, profile_id: int) -> bool:
        profile = self._db.get(FileServerProfile, profile_id)
        if profile is None:
            return False
        self._db.delete(profile)
        self._db.commit()
        return True

    def get_decrypted_by_name(self, name: str) -> "ResolvedFileServerProfile | None":
        profile = self._db.query(FileServerProfile).filter(FileServerProfile.name == name).first()
        if profile is None:
            return None
        from api.services.secret_store import decrypt_secret
        return ResolvedFileServerProfile(
            id=profile.id, name=profile.name, kind=profile.kind,
            host=profile.host, port=profile.port, username=profile.username,
            auth_method=profile.auth_method,
            password=decrypt_secret(profile.password) if profile.password else None,
            private_key=decrypt_secret(profile.private_key) if profile.private_key else None,
            key_passphrase=decrypt_secret(profile.key_passphrase) if profile.key_passphrase else None,
            host_key_fingerprint=profile.host_key_fingerprint,
            aws_access_key_id=profile.aws_access_key_id,
            aws_secret_access_key=decrypt_secret(profile.aws_secret_access_key) if profile.aws_secret_access_key else None,
            aws_session_token=decrypt_secret(profile.aws_session_token) if profile.aws_session_token else None,
            region_name=profile.region_name,
            endpoint_url=profile.endpoint_url,
        )
```

Note the deliberate difference from `ConfigRepository._decrypt`: that method decrypts a JSON blob column and uses `set_committed_value` to avoid SQLAlchemy's dirty-tracking/autoflush from writing plaintext back over the encrypted column. `FileServerProfile` has many separate typed columns instead of one JSON blob, so decrypting five columns in place would need five `set_committed_value` calls and still leave decrypted secrets sitting on a tracked ORM instance for the rest of the request. Returning a separate, untracked `ResolvedFileServerProfile` dataclass sidesteps that risk entirely: the DB row is never mutated, decrypted secrets exist only in this throwaway object.

Add the dataclass near the top of `etl_framework/repository/repository.py`, after the existing `@dataclass` import line:

```python
@dataclass(frozen=True)
class ResolvedFileServerProfile:
    id: int
    name: str
    kind: str
    host: str | None
    port: int
    username: str | None
    auth_method: str | None
    password: str | None
    private_key: str | None
    key_passphrase: str | None
    host_key_fingerprint: str | None
    aws_access_key_id: str | None
    aws_secret_access_key: str | None
    aws_session_token: str | None
    region_name: str | None
    endpoint_url: str | None
```

Add `FileServerProfile` to the `from etl_framework.repository.models import (...)` block at the top of the file (alongside `RunBatch`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_server_profile_repository.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_file_server_profile_repository.py
git commit -m "feat: add FileServerProfileRepository with encrypt-at-rest"
```

---

### Task 3: Pydantic schemas

**Files:**
- Modify: `api/schemas.py`

- [ ] **Step 1: Add the schemas**

Add near `ConfigOut`/`ConfigCreate`/`ConfigUpdate` (after line 32) in `api/schemas.py`:

```python
class FileServerProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["sftp", "scp", "s3"]
    description: str = ""
    host: str | None = None
    port: int = 22
    username: str | None = None
    auth_method: Literal["password", "private_key"] | None = None
    password: str | None = None
    private_key: str | None = None
    key_passphrase: str | None = None
    host_key_fingerprint: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    region_name: str | None = None
    endpoint_url: str | None = None


class FileServerProfileUpdate(BaseModel):
    name: str | None = None
    kind: Literal["sftp", "scp", "s3"] | None = None
    description: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    auth_method: Literal["password", "private_key"] | None = None
    password: str | None = None
    private_key: str | None = None
    key_passphrase: str | None = None
    host_key_fingerprint: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    region_name: str | None = None
    endpoint_url: str | None = None


class FileServerProfileOut(BaseModel):
    id: int
    name: str
    kind: str
    description: str
    host: str | None = None
    port: int
    username: str | None = None
    auth_method: str | None = None
    password: str | None = None
    private_key: str | None = None
    key_passphrase: str | None = None
    host_key_fingerprint: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_session_token: str | None = None
    region_name: str | None = None
    endpoint_url: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FileServerTestResult(BaseModel):
    status: Literal["ok", "unpinned", "mismatch", "error"]
    presented_fingerprint: str | None = None
    pinned_fingerprint: str | None = None
    message: str | None = None
```

`Literal` is already imported in `api/schemas.py` (used by `EnvironmentConfig`-adjacent schemas) — confirm with `grep -n "^from typing import" api/schemas.py`; if `Literal` isn't in that import line, add it.

- [ ] **Step 2: Remove the inline raw-credentials field from the preview request**

In `api/schemas.py`, replace the `PreviewFileMappingRequest` class (around line 1373):

```python
class PreviewFileMappingRequest(BaseModel):
    """Body for POST /api/jobs/preview-file-mapping. ``file_mapping`` is the
    same config shape used inside a saved multi_file job's
    ``params.file_mapping`` (see FileMappingSpec.from_params). Local sources
    need nothing else; s3/sftp/scp sources need ``credentials_ref`` set on
    the relevant side, matching the ``name`` of a saved ``FileServerProfile``
    (see ``api/services/multi_file_remote.py``'s ``resolve_file_server_profile``)
    -- there is no separate inline-credentials path any more; profiles are
    the only way to authenticate a remote source, at preview time or launch
    time alike.
    """
    file_mapping: dict[str, Any] = Field(...)
```

- [ ] **Step 3: Commit**

```bash
git add api/schemas.py
git commit -m "feat: add FileServerProfile schemas, drop inline preview credentials field"
```

---

### Task 4: `api/routes/file_servers.py` — CRUD

**Files:**
- Create: `api/routes/file_servers.py`
- Modify: `api/main.py`
- Test: `tests/unit/test_file_servers_api.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_file_servers_api.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository
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
        yield c
    app.dependency_overrides.clear()


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_servers_api.py -v`
Expected: FAIL — `404` on `/api/file-servers` (router doesn't exist yet) or connection error.

- [ ] **Step 3: Implement the router**

Create `api/routes/file_servers.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import FileServerProfileCreate, FileServerProfileOut, FileServerProfileUpdate
from api.services.audit_service import AuditService
from etl_framework.repository.repository import FileServerProfileRepository, _FILE_SERVER_SECRET_FIELDS
from etl_framework.repository.models import ExecutionSequenceVersion, SavedJob, ScheduledRun

router = APIRouter(tags=["file-servers"])

_MASK = "********"
_SECRET_FIELDS = _FILE_SERVER_SECRET_FIELDS  # single source of truth, defined alongside FileServerProfileRepository


def _mask(profile) -> FileServerProfileOut:
    out = FileServerProfileOut.model_validate(profile)
    data = out.model_dump()
    for field in _SECRET_FIELDS:
        if data.get(field):
            data[field] = _MASK
    return FileServerProfileOut(**data)


def _preserve_masked_secrets(incoming: dict, existing) -> dict:
    """Keep the stored secret value when the client echoes back the display mask."""
    if existing is None:
        return incoming
    result = dict(incoming)
    for field in _SECRET_FIELDS:
        if result.get(field) == _MASK:
            result[field] = getattr(existing, field)
    return result


def _references_credentials_ref(value, name: str) -> bool:
    """Recursively scan a saved job's params / a sequence step / a schedule's
    job_sequence for a `credentials_ref` key equal to `name`. Generic rather
    than shape-specific because the three JSON columns that can carry a
    credentials_ref (SavedJob.params, ExecutionSequenceVersion.steps_json,
    ScheduledRun.job_sequence) nest it at different depths."""
    if isinstance(value, dict):
        if value.get("credentials_ref") == name:
            return True
        return any(_references_credentials_ref(v, name) for v in value.values())
    if isinstance(value, list):
        return any(_references_credentials_ref(v, name) for v in value)
    return False


@router.get("", response_model=list[FileServerProfileOut])
def list_file_servers(db: Session = Depends(get_session)):
    return [_mask(p) for p in FileServerProfileRepository(db).list()]


@router.post("", response_model=FileServerProfileOut, status_code=201)
def create_file_server(body: FileServerProfileCreate, request: Request, db: Session = Depends(get_session)):
    repo = FileServerProfileRepository(db)
    profile = repo.create(body.model_dump())
    AuditService(db).log(request, "file_server.created", "file_server", profile.id, {"name": profile.name, "kind": profile.kind})
    return _mask(profile)


@router.get("/{profile_id}", response_model=FileServerProfileOut)
def get_file_server(profile_id: int, db: Session = Depends(get_session)):
    profile = FileServerProfileRepository(db).get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="File server profile not found")
    return _mask(profile)


@router.put("/{profile_id}", response_model=FileServerProfileOut)
def update_file_server(profile_id: int, body: FileServerProfileUpdate, request: Request, db: Session = Depends(get_session)):
    repo = FileServerProfileRepository(db)
    existing = repo.get(profile_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="File server profile not found")
    data = _preserve_masked_secrets({k: v for k, v in body.model_dump().items() if v is not None}, existing)
    profile = repo.update(profile_id, data)
    AuditService(db).log(request, "file_server.updated", "file_server", profile.id, {"name": profile.name})
    return _mask(profile)


@router.delete("/{profile_id}", status_code=204)
def delete_file_server(profile_id: int, request: Request, db: Session = Depends(get_session)):
    repo = FileServerProfileRepository(db)
    profile = repo.get(profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="File server profile not found")

    in_use = (
        any(_references_credentials_ref(j.params, profile.name) for j in db.query(SavedJob).all())
        or any(_references_credentials_ref(v.steps_json, profile.name) for v in db.query(ExecutionSequenceVersion).all())
        or any(_references_credentials_ref(s.job_sequence, profile.name) for s in db.query(ScheduledRun).all())
    )
    if in_use:
        raise HTTPException(status_code=409, detail=f"File server profile '{profile.name}' is still referenced by a saved job, sequence, or schedule")

    repo.delete(profile_id)
    AuditService(db).log(request, "file_server.deleted", "file_server", profile_id, {"name": profile.name})
```

- [ ] **Step 4: Register the router**

In `api/main.py`, add the import alongside the other route imports (near line 30):

```python
from api.routes import file_servers as file_servers_routes
```

Add the registration alongside the other `include_router` calls (near line 85):

```python
app.include_router(file_servers_routes.router, prefix="/api/file-servers")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_servers_api.py -v`
Expected: `7 passed`

- [ ] **Step 6: Commit**

```bash
git add api/routes/file_servers.py api/main.py tests/unit/test_file_servers_api.py
git commit -m "feat: add file server profile CRUD API with masked secrets"
```

---

### Task 5: Credential resolution + client construction rewrite

**Files:**
- Modify: `api/services/multi_file_remote.py`
- Test: `tests/unit/test_multi_file_remote.py` (rewrite)

This is the core behavior change: `resolve_file_source_credentials(config_snapshot, spec)` (dict lookup) is replaced by `resolve_file_server_profile(db, spec.credentials_ref)` (DB lookup, raising on a missing/mismatched profile). `build_s3_client`/`build_sftp_client` take a resolved `ResolvedFileServerProfile` instead of a raw dict. `RemoteFileSourceSession` takes `db: Session` instead of `config_snapshot: dict`.

- [ ] **Step 1: Write the failing tests (new resolution + client behavior)**

Replace the top of `tests/unit/test_multi_file_remote.py` (through line 34, the three `resolve_file_source_credentials` tests) with:

```python
# tests/unit/test_multi_file_remote.py
from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.services.multi_file_remote import (
    RemoteFileSourceSession,
    resolve_file_server_profile,
)
from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import FileServerProfileRepository
from etl_framework.reconciliation.file_mapping import FileSourceSpec


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


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
```

Then, for every remaining test in the file that builds a `RemoteFileSourceSession({...})` or calls `build_s3_client(config_snapshot, spec)` directly, apply this exact mechanical transform:

1. Add the `db` fixture parameter to the test function signature.
2. Replace `RemoteFileSourceSession({})` → `RemoteFileSourceSession(db)` (the `db` fixture — most of these tests exercise `local`/mocked-`s3` paths that never touch a profile, so an empty DB is fine).
3. Replace `RemoteFileSourceSession({"file_source_credentials": {...}})` (the two `build_s3_client` path-style-addressing tests, lines 255-315) with: create a `FileServerProfileRepository(db).create({...})` row with the same fields, then call `build_s3_client(resolve_file_server_profile(db, spec), spec)` — since `build_s3_client` now takes a resolved profile, not a `config_snapshot`.
4. Update every `def _fake_build_s3_client(config_snapshot, spec):` monkeypatch target to `def _fake_build_s3_client(profile, spec):` (parameter renamed only — these are fakes standing in for the real function's new signature, none of their bodies reference the parameter by name).

Applying that transform, `test_build_s3_client_forces_path_style_addressing_for_custom_endpoint` (line 255) becomes:

```python
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
```

`test_build_s3_client_does_not_force_path_style_without_custom_endpoint` (line 290) follows the identical pattern with an `"aws_prod"` profile (`aws_access_key_id: "AKIA..."`, `aws_secret_access_key: "s3cr3t"`, no `endpoint_url`).

Every other test in the file (`test_remote_file_source_session_*`, `test_resolve_file_source_credentials_returns_empty_without_ref`) only needs transform steps 1-2 (add `db` fixture, swap the constructor argument) since they never construct real credentials.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_multi_file_remote.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_file_server_profile'`

- [ ] **Step 3: Implement the rewrite**

Replace `api/services/multi_file_remote.py` lines 1-75 (module docstring through `build_sftp_client`) with:

```python
"""Shared S3/SFTP client construction, credential resolution, and file
discovery/read dispatch for ``multi_file`` reconciliation jobs.

Both ``RunExecutor`` (live job execution, ``api/services/run_executor.py``)
and ``difference_export`` (recomputing a run's full diff set for export/HTML
reports, ``api/services/difference_export.py``) need to discover and read
files from the same three source kinds (``local``, ``s3``, ``sftp``). This
module is the single place that owns that logic, so the two call sites don't
each re-derive their own copy of client construction and credential lookup.

Credentials come from a persisted, encrypted ``FileServerProfile`` (see
``etl_framework.repository.repository.FileServerProfileRepository``) looked
up by the source spec's ``credentials_ref`` -- there is no inline/raw
credentials path any more.

``RemoteFileSourceSession`` also caches one client per ``(kind,
credentials_ref)`` for the caller's lifetime -- a source with N files opens
one S3/SFTP connection total, not one per file read.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pandas as pd
from sqlalchemy.orm import Session

from api.services.file_source import _read_tabular_bytes, read_tabular, resolve_allowed_path
from etl_framework.reconciliation.file_mapping import (
    DiscoveredFile,
    FileSourceSpec,
    discover_local_files,
    discover_s3_files,
    discover_sftp_files,
)
from etl_framework.repository.repository import FileServerProfileRepository, ResolvedFileServerProfile

_CONTENT_MATCH_READ_LIMIT = 65536


def resolve_file_server_profile(db: Session, spec: FileSourceSpec) -> ResolvedFileServerProfile | None:
    if not spec.credentials_ref:
        return None
    profile = FileServerProfileRepository(db).get_decrypted_by_name(spec.credentials_ref)
    if profile is None:
        raise ValueError(f"No file server profile named '{spec.credentials_ref}' -- create one in File Servers before running this job")
    # scp locations reuse the sftp client/fields, so either kind is valid for an sftp-kind spec;
    # an s3 location must be backed by an s3-kind profile and vice versa.
    compatible = {profile.kind} | ({"sftp", "scp"} if profile.kind in ("sftp", "scp") else set())
    if spec.kind not in compatible:
        raise ValueError(f"File server profile '{spec.credentials_ref}' is kind '{profile.kind}', not '{spec.kind}'")
    return profile


def build_s3_client(profile: ResolvedFileServerProfile, spec: FileSourceSpec):
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise RuntimeError("boto3 is required for multi_file S3 sources") from exc
    client_kwargs: dict[str, Any] = {
        "aws_access_key_id": profile.aws_access_key_id,
        "aws_secret_access_key": profile.aws_secret_access_key,
        "aws_session_token": profile.aws_session_token,
        "region_name": profile.region_name,
        "endpoint_url": profile.endpoint_url,
    }
    if profile.endpoint_url:
        # A custom endpoint_url means a non-AWS, S3-compatible target (MinIO,
        # on-prem object storage) -- these commonly reject the virtual-hosted-
        # style bucket addressing boto3 otherwise defaults to whenever a
        # custom endpoint is set. Real AWS never sets endpoint_url, so this
        # never affects the existing real-AWS path.
        client_kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
    return boto3.client("s3", **client_kwargs)


def build_sftp_client(profile: ResolvedFileServerProfile, spec: FileSourceSpec):
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is required for multi_file SFTP sources") from exc
    transport = paramiko.Transport((profile.host, int(profile.port or 22)))
    try:
        if profile.auth_method == "private_key":
            key = paramiko.PKey.from_private_key(io.StringIO(profile.private_key), password=profile.key_passphrase or None)
            transport.connect(username=profile.username, pkey=key)
        else:
            transport.connect(username=profile.username, password=profile.password)
        presented = transport.get_remote_server_key()
        fingerprint = hashlib.sha256(presented.asbytes()).hexdigest()
        if not profile.host_key_fingerprint or fingerprint != profile.host_key_fingerprint:
            raise RuntimeError(
                f"Host key verification failed for file server profile '{profile.name}' -- "
                "run Test Connection in File Servers to review and pin the presented fingerprint"
            )
    except Exception:
        transport.close()
        raise
    return paramiko.SFTPClient.from_transport(transport)
```

Update `RemoteFileSourceSession.__init__` and `_client_for` (currently lines 96-109) to:

```python
    def __init__(self, db: Session) -> None:
        self._db = db
        self._clients: dict[tuple[str, str | None], Any] = {}

    def _client_for(self, spec: FileSourceSpec):
        key = (spec.kind, spec.credentials_ref)
        if key not in self._clients:
            profile = resolve_file_server_profile(self._db, spec)
            if spec.kind == "s3":
                self._clients[key] = build_s3_client(profile, spec)
            elif spec.kind == "sftp":
                self._clients[key] = build_sftp_client(profile, spec)
            else:
                raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
        return self._clients[key]
```

The rest of `RemoteFileSourceSession` (`discover`, `read_file`, `read_text`, `_read_bytes`, `close`, `__enter__`/`__exit__`) is unchanged — none of it touches `_config_snapshot` directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_multi_file_remote.py -v`
Expected: all tests pass (5 new + the transformed existing ones).

- [ ] **Step 5: Commit**

```bash
git add api/services/multi_file_remote.py tests/unit/test_multi_file_remote.py
git commit -m "feat: resolve file source credentials from FileServerProfile, add SSH key auth + host-key verification"
```

---

### Task 6: Update the three call sites

**Files:**
- Modify: `api/services/run_executor.py:734, 798, 1629`
- Modify: `api/services/difference_export.py:603, 753, 784`
- Modify: `api/routes/jobs.py:167-177`

`RemoteFileSourceSession` now takes `db: Session` instead of a `config_snapshot` dict. Every call site already has a `db`/`self._db` in scope.

- [ ] **Step 1: `run_executor.py`**

Three occurrences of `RemoteFileSourceSession(self._config_snapshot)` (lines 734, 798, 1629) each become `RemoteFileSourceSession(self._db)`. `RunExecutor.__init__` (line 247) already stores `self._db = db`, so no constructor change is needed.

- [ ] **Step 2: `difference_export.py`**

Line 603, inside `_write_multi_file_compare(db, payload, writer)`: `RemoteFileSourceSession({})` → `RemoteFileSourceSession(db)` (the function already has `db` as its first parameter).

Line 753/784: `_write_multi_file_reconciliation_job(job, settings, writer, snapshot)` gains a `db: Session` parameter — update its signature (line 769) to `def _write_multi_file_reconciliation_job(db: Session, job, settings, writer, config_snapshot=None) -> None:`, its one call site (line 753, inside `_write_reconciliation_run` which already has `db` in scope) to `_write_multi_file_reconciliation_job(db, job, settings, writer, snapshot)`, and its internal `RemoteFileSourceSession(config_snapshot)` (line 784) to `RemoteFileSourceSession(db)`.

- [ ] **Step 3: `jobs.py` preview endpoint**

Replace `preview_file_mapping` (lines 166-177):

```python
@router.post("/preview-file-mapping")
def preview_file_mapping(body: PreviewFileMappingRequest, db: Session = Depends(get_session)):
    from etl_framework.reconciliation.file_mapping import FileMappingSpec, pair_files, pair_files_automated
    from api.services.multi_file_remote import RemoteFileSourceSession

    try:
        spec = FileMappingSpec.from_params({"file_mapping": body.file_mapping})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        with RemoteFileSourceSession(db) as session:
            source_files = session.discover(spec.source)
            target_files = session.discover(spec.target)
```

(the rest of the function body, from `if spec.strategy == "automated":` onward, is unchanged). `db: Session = Depends(get_session)` is a new parameter — `Session`, `Depends`, and `get_session` are already imported at the top of `jobs.py` (confirmed: `from sqlalchemy.orm import Session`, `from api.dependencies import get_session`).

Wrap the `session.discover`/`session.read_file` calls in a `try/except ValueError as exc: raise HTTPException(status_code=422, detail=str(exc))` so a missing/mismatched `credentials_ref` (raised by `resolve_file_server_profile`) surfaces as a client error, not a 500 — add this around the existing `try` block that starts at (old) line 171.

- [ ] **Step 4: Run the existing test suites for these three modules to check nothing else references the old signatures**

Run: `python -m pytest tests/unit/test_run_executor_file_watcher.py tests/unit/test_multi_file_jobs.py tests/unit/test_difference_export.py tests/unit/test_api.py -v 2>&1 | tail -80`
Expected: failures only in tests that construct `config_snapshot["file_source_credentials"]` directly — fixed in Task 7. Note which tests fail here for Task 7.

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py api/services/difference_export.py api/routes/jobs.py
git commit -m "refactor: pass db instead of config_snapshot to RemoteFileSourceSession at every call site"
```

---

### Task 7: Fix remaining tests that used inline `file_source_credentials`

**Files:**
- Modify: `tests/unit/test_run_executor_file_watcher.py`
- Modify: `tests/unit/test_multi_file_jobs.py`
- Modify: `tests/unit/test_difference_export.py` (if applicable)
- Modify: `tests/unit/test_api.py` (if applicable)

- [ ] **Step 1: Find every remaining reference**

Run: `python -m pytest tests/unit -k "file_watcher or multi_file or file_mapping" -v 2>&1 | grep -E "FAILED|ERROR"`

For each failing test that builds a `config_snapshot` containing `"file_source_credentials"`, apply the same transform as Task 5 Step 1: create a `FileServerProfileRepository(db).create({...})` row with matching fields (using a `db` fixture — reuse the pattern from `tests/unit/test_multi_file_remote.py`'s new `db` fixture, or the module's existing DB fixture if `test_run_executor_file_watcher.py`/`test_multi_file_jobs.py` already has one), and drop the `file_source_credentials` key from the snapshot dict passed to `RunExecutor`/`RemoteFileSourceSession`.

The known one from investigation: `tests/unit/test_run_executor_file_watcher.py:138` builds a job with `"location": {"kind": "scp", ..., "credentials_ref": "scp_host"}`. Read that test in full (`sed -n '100,170p' tests/unit/test_run_executor_file_watcher.py`) before editing — its `RunExecutor` construction and DB fixture must be matched exactly, not guessed.

- [ ] **Step 2: Run the full targeted suite to verify it passes**

Run: `python -m pytest tests/unit -k "file_watcher or multi_file or file_mapping or file_server" -v`
Expected: all pass, 0 failures.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_run_executor_file_watcher.py tests/unit/test_multi_file_jobs.py
git commit -m "test: update file_watcher/multi_file tests to use FileServerProfile instead of inline credentials"
```

---

### Task 8: Test Connection endpoint

**Files:**
- Modify: `api/routes/file_servers.py`
- Modify: `api/schemas.py` (already added `FileServerTestResult` in Task 3)
- Test: `tests/unit/test_file_servers_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_file_servers_api.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_file_servers_api.py -k test_connection -v`
Expected: FAIL — `404` (no `/test` route yet).

- [ ] **Step 3: Implement**

Add to `api/routes/file_servers.py` (add `import hashlib` and `import paramiko` at the top, plus `TestConnectionRequest` inline since it's tiny):

```python
from pydantic import BaseModel
from api.schemas import FileServerTestResult


class TestConnectionRequest(BaseModel):
    accept_fingerprint: bool = False


@router.post("/{profile_id}/test", response_model=FileServerTestResult)
def test_file_server(profile_id: int, body: TestConnectionRequest = TestConnectionRequest(), db: Session = Depends(get_session)):
    repo = FileServerProfileRepository(db)
    profile = repo.get_decrypted_by_name(repo.get(profile_id).name) if repo.get(profile_id) else None
    if profile is None:
        raise HTTPException(status_code=404, detail="File server profile not found")

    if profile.kind in ("sftp", "scp"):
        import io
        transport = paramiko.Transport((profile.host, int(profile.port or 22)))
        try:
            if profile.auth_method == "private_key":
                key = paramiko.PKey.from_private_key(io.StringIO(profile.private_key), password=profile.key_passphrase or None)
                transport.connect(username=profile.username, pkey=key)
            else:
                transport.connect(username=profile.username, password=profile.password)
            presented = transport.get_remote_server_key()
            fingerprint = hashlib.sha256(presented.asbytes()).hexdigest()
        except Exception as exc:
            return FileServerTestResult(status="error", message=str(exc))
        finally:
            transport.close()

        if body.accept_fingerprint:
            repo.update(profile_id, {"host_key_fingerprint": fingerprint})
            return FileServerTestResult(status="ok", presented_fingerprint=fingerprint)
        if not profile.host_key_fingerprint:
            return FileServerTestResult(status="unpinned", presented_fingerprint=fingerprint)
        if fingerprint != profile.host_key_fingerprint:
            return FileServerTestResult(status="mismatch", presented_fingerprint=fingerprint, pinned_fingerprint=profile.host_key_fingerprint)
        return FileServerTestResult(status="ok", presented_fingerprint=fingerprint)

    # s3
    try:
        import boto3
        from api.services.multi_file_remote import build_s3_client
        from etl_framework.reconciliation.file_mapping import FileSourceSpec
        client = build_s3_client(profile, FileSourceSpec(kind="s3", root="", pattern=""))
        client.list_buckets()
        return FileServerTestResult(status="ok")
    except Exception as exc:
        return FileServerTestResult(status="error", message=str(exc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_file_servers_api.py -v`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add api/routes/file_servers.py tests/unit/test_file_servers_api.py
git commit -m "feat: add File Server test-connection endpoint with host-key fingerprint pinning"
```

---

### Task 9: GUI — File Servers tab

**Files:**
- Create: `frontend/features/file-servers.js`
- Create: `frontend/partials/tab-file-servers.html`
- Modify: `frontend/app.js` (register the feature slice + tab)
- Modify: `frontend/index.html` (regenerated build artifact — see Task 11)

- [ ] **Step 1: Feature slice**

Create `frontend/features/file-servers.js`, following `frontend/features/monitor.js`'s exact shape:

```javascript
(function (global) {
  'use strict';
  global.ETL_FEATURE_FILE_SERVERS = function () {
    return {
      fileServers: [],
      fileServerModal: { show: false, editingId: null, data: {} },
      fileServerTestResult: {},

      async loadFileServers() {
        try {
          this.fileServers = await api('GET', '/api/file-servers');
        } catch (e) {
          this.toast('error', 'Failed to load file servers', e.message);
        }
      },

      openFileServerModal(profile) {
        this.fileServerModal = {
          show: true,
          editingId: profile ? profile.id : null,
          data: profile ? { ...profile } : { kind: 'sftp', port: 22, auth_method: 'password' },
        };
      },

      async saveFileServer() {
        const m = this.fileServerModal;
        try {
          if (m.editingId) {
            await api('PUT', `/api/file-servers/${m.editingId}`, m.data);
          } else {
            await api('POST', '/api/file-servers', m.data);
          }
          this.fileServerModal.show = false;
          await this.loadFileServers();
          this.toast('success', 'File server saved');
        } catch (e) {
          this.toast('error', 'Save failed', e.message);
        }
      },

      async deleteFileServer(id) {
        try {
          await api('DELETE', `/api/file-servers/${id}`);
          await this.loadFileServers();
          this.toast('success', 'File server deleted');
        } catch (e) {
          this.toast('error', 'Delete failed', e.message);
        }
      },

      async testFileServer(id, acceptFingerprint) {
        try {
          const result = await api('POST', `/api/file-servers/${id}/test`, { accept_fingerprint: Boolean(acceptFingerprint) });
          this.fileServerTestResult = { ...this.fileServerTestResult, [id]: result };
          if (result.status === 'ok') this.toast('success', 'Connection OK');
        } catch (e) {
          this.toast('error', 'Test failed', e.message);
        }
      },
    };
  };
})(window);
```

- [ ] **Step 3: Partial**

Create `frontend/partials/tab-file-servers.html`:

```html
<template x-if="currentView === 'file-servers'"><div x-init="loadFileServers()">
  <div class="section-header">
    <div>
      <div class="section-title">File Servers</div>
      <div class="section-sub">SFTP / SCP / S3 authentication profiles for file_watcher and multi-file jobs</div>
    </div>
    <button @click="openFileServerModal(null)" class="btn-primary" data-testid="file-server-add-btn">Add File Server</button>
  </div>

  <div class="space-y-3">
    <template x-for="fs in fileServers" :key="fs.id">
      <div class="card" :data-testid="'file-server-' + fs.id">
        <div class="flex items-start justify-between">
          <div>
            <div class="font-semibold" x-text="fs.name"></div>
            <div class="text-muted text-sm" x-text="fs.kind + (fs.host ? (' · ' + fs.host) : '')"></div>
          </div>
          <div class="flex gap-2">
            <button @click="testFileServer(fs.id)" class="btn-secondary btn-sm" :data-testid="'file-server-test-' + fs.id">Test Connection</button>
            <button @click="openFileServerModal(fs)" class="btn-secondary btn-sm">Edit</button>
            <button @click="deleteFileServer(fs.id)" class="btn-danger btn-sm">Delete</button>
          </div>
        </div>
        <template x-if="fileServerTestResult[fs.id]">
          <div class="mt-2 text-sm">
            <template x-if="fileServerTestResult[fs.id].status === 'unpinned'">
              <div class="text-amber-700">
                Server presented fingerprint <code x-text="fileServerTestResult[fs.id].presented_fingerprint"></code>
                <button @click="testFileServer(fs.id, true)" class="btn-primary btn-sm ml-2" :data-testid="'file-server-accept-fingerprint-' + fs.id">Accept &amp; Pin</button>
              </div>
            </template>
            <template x-if="fileServerTestResult[fs.id].status === 'mismatch'">
              <div class="text-rose-700">Host key mismatch — presented <code x-text="fileServerTestResult[fs.id].presented_fingerprint"></code>, pinned <code x-text="fileServerTestResult[fs.id].pinned_fingerprint"></code></div>
            </template>
            <template x-if="fileServerTestResult[fs.id].status === 'ok'">
              <div class="text-emerald-700">Connection OK</div>
            </template>
            <template x-if="fileServerTestResult[fs.id].status === 'error'">
              <div class="text-rose-700" x-text="fileServerTestResult[fs.id].message"></div>
            </template>
          </div>
        </template>
      </div>
    </template>
  </div>

  <div x-show="fileServerModal.show" x-cloak class="modal-backdrop" @click.self="fileServerModal.show = false">
    <div class="modal-box w-full max-w-lg" role="dialog" aria-modal="true">
      <h2 class="text-lg font-bold mb-4" x-text="fileServerModal.editingId ? 'Edit File Server' : 'Add File Server'"></h2>
      <div class="space-y-3">
        <div><label class="field-label">Name</label><input x-model="fileServerModal.data.name" class="field-input" data-testid="file-server-name-input" /></div>
        <div><label class="field-label">Kind</label>
          <select x-model="fileServerModal.data.kind" class="field-input field-select" data-testid="file-server-kind-select">
            <option value="sftp">SFTP</option><option value="scp">SCP</option><option value="s3">S3</option>
          </select>
        </div>
        <template x-if="fileServerModal.data.kind === 'sftp' || fileServerModal.data.kind === 'scp'">
          <div class="space-y-3">
            <div><label class="field-label">Host</label><input x-model="fileServerModal.data.host" class="field-input" /></div>
            <div><label class="field-label">Port</label><input type="number" x-model="fileServerModal.data.port" class="field-input" /></div>
            <div><label class="field-label">Username</label><input x-model="fileServerModal.data.username" class="field-input" /></div>
            <div><label class="field-label">Auth Method</label>
              <select x-model="fileServerModal.data.auth_method" class="field-input field-select">
                <option value="password">Password</option><option value="private_key">Private Key</option>
              </select>
            </div>
            <div x-show="fileServerModal.data.auth_method === 'password'"><label class="field-label">Password</label><input type="password" x-model="fileServerModal.data.password" class="field-input" data-testid="file-server-password-input" /></div>
            <template x-if="fileServerModal.data.auth_method === 'private_key'">
              <div class="space-y-3">
                <div><label class="field-label">Private Key</label><textarea x-model="fileServerModal.data.private_key" class="field-input" rows="4"></textarea></div>
                <div><label class="field-label">Key Passphrase (optional)</label><input type="password" x-model="fileServerModal.data.key_passphrase" class="field-input" /></div>
              </div>
            </template>
          </div>
        </template>
        <template x-if="fileServerModal.data.kind === 's3'">
          <div class="space-y-3">
            <div><label class="field-label">Access Key ID</label><input x-model="fileServerModal.data.aws_access_key_id" class="field-input" /></div>
            <div><label class="field-label">Secret Access Key</label><input type="password" x-model="fileServerModal.data.aws_secret_access_key" class="field-input" /></div>
            <div><label class="field-label">Region</label><input x-model="fileServerModal.data.region_name" class="field-input" /></div>
            <div><label class="field-label">Endpoint URL (optional, for MinIO/on-prem)</label><input x-model="fileServerModal.data.endpoint_url" class="field-input" /></div>
          </div>
        </template>
      </div>
      <div class="flex justify-end gap-2 mt-4">
        <button @click="fileServerModal.show = false" class="btn-secondary">Cancel</button>
        <button @click="saveFileServer()" class="btn-primary" data-testid="file-server-save-btn">Save</button>
      </div>
    </div>
  </div>
</div></template>
```

- [ ] **Step 3: Add the nav entry**

In `frontend/app.js`, the sidebar is driven by the `tabs:` array (line 183) grouped by `tabGroups:` (line 217: `setup`/`execution`/`analysis`/`system`). Add a new entry to the `setup` group, alongside `config`/`adapters`/`aws`/`contracts` (after the `contracts` entry, line 193):

```javascript
      { id: 'file-servers', label: 'File Servers', group: 'setup',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="8" rx="2"></rect><rect x="2" y="13" width="20" height="8" rx="2"></rect><line x1="6" y1="7" x2="6.01" y2="7"></line><line x1="6" y1="17" x2="6.01" y2="17"></line></svg>' },
```

Clicking this nav item sets `currentView = 'file-servers'` via the existing generic `onTabEnter(tab.id)` handler (same one every other tab uses) — no per-tab click handler needed.

- [ ] **Step 4: Register the feature slice**

In `frontend/app.js` line 1528, add `ETL_FEATURE_FILE_SERVERS()` to the `FEATURE_SLICES` array:

```javascript
const FEATURE_SLICES = [ETL_FEATURE_COMPARE(), ETL_FEATURE_CONFIG(), ETL_FEATURE_LAUNCH(), ETL_FEATURE_MONITOR(), ETL_FEATURE_HISTORY(), ETL_FEATURE_CI_RUNS(), ETL_FEATURE_ADAPTERS(), ETL_FEATURE_AWS(), ETL_FEATURE_REPORTS(), ETL_FEATURE_DIFFERENCES(), ETL_FEATURE_CONTRACTS(), ETL_FEATURE_SCHEDULER_REPORTS(), ETL_FEATURE_LOGS(), ETL_FEATURE_SEQUENCES(), ETL_FEATURE_FILE_SERVERS()];
```

- [ ] **Step 5: Wire the partial include and script tag**

In `frontend/index.template.html`, add the partial include after the Contracts tab include (find `<!-- INCLUDE: partials/tab-contracts.html -->` — it sits in the same tab-numbered block style as the `TAB 1 – CONFIG EDITOR`/`TAB 3 – MONITOR` comments shown earlier in this file) — add a matching block:

```html
<!-- ====================================================================
     TAB – FILE SERVERS
     ==================================================================== -->
<!-- INCLUDE: partials/tab-file-servers.html -->
```

Add the script tag to the list at line 441-456 (alongside the other `features/*.js` tags), inserted after `<script src="features/adapters.js"></script>`:

```html
<script src="features/file-servers.js"></script>
```

- [ ] **Step 6: Run the frontend unit test that checks markup wiring**

Run: `python -m pytest tests/unit/test_batch_progress_frontend.py -v` — this doesn't test File Servers, but confirms the `x-init`/markup-assertion pattern this plan's Task 12 e2e test will build on still passes after `app.js`/`index.template.html` edits (a quick regression check before the slower e2e run in Task 12).
Expected: `2 passed`

- [ ] **Step 7: Commit**

```bash
git add frontend/features/file-servers.js frontend/partials/tab-file-servers.html frontend/app.js frontend/index.template.html
git commit -m "feat: add File Servers management tab to the GUI"
```

---

### Task 10: GUI — replace free-text/raw-credential inputs with File Server dropdowns

**Files:**
- Modify: `frontend/features/launch.js:32, 203-204, 377-378, 440-511`
- Modify: `frontend/partials/tab-launch.html:576-643` (multi_file source/target) `and 755-781` (file_watcher location)
- Modify: `tests/e2e/02b-launch-jobs-remote-preview.spec.ts`
- Modify: `tests/e2e/17b-multi-file-live-remote.spec.ts`

Investigation while writing this task found the multi_file (`mf_source`/`mf_target`) job modal has **two** separate credential-entry mechanisms today, not one: the `mf_source_credentials_ref`/`mf_target_credentials_ref` free-text field (the name saved with the job, used at real launch time — same as `file_watcher`'s `fw_credentials_ref`), *and* a completely separate set of raw "preview only" input fields (`mf_source_preview_creds.{aws_access_key_id,aws_secret_access_key,region_name,endpoint_url,host,port,username,password}`, mirrored for target) that `previewFileMapping()` (`launch.js:482-511`) sends inline under synthetic `__preview_source__`/`__preview_target__` keys — this is exactly the `PreviewFileMappingRequest.file_source_credentials` inline path Task 3 Step 2 removes from the backend. Both mechanisms need updating together, in this one task, since removing the backend field without fixing this call site would break Preview Mapping outright.

- [ ] **Step 1: Load the profile list into Launch tab state**

In `frontend/features/launch.js`, add `fileServers: []` to the state object (alongside `jobModal:` at line 32) and a `loadFileServers()` method that calls `GET /api/file-servers` (reuse the exact same call the File Servers feature slice makes in Task 9 — no new endpoint), invoked from the same place `frontend/partials/tab-launch.html`'s `x-init` already loads jobs/configs when the Launch (`jobs`) tab mounts.

- [ ] **Step 2: Replace the file_watcher `credentials_ref` input with a dropdown**

In `frontend/partials/tab-launch.html`, replace lines 776-780:

```html
<!-- was: -->
<div x-show="jobModal.fw_location_kind !== 'local'">
  <label  class="field-label" for="a11y-launch-fw-credentials-ref">Credentials Ref</label>
  <input x-model="jobModal.fw_credentials_ref" class="field-input" placeholder="credentials_ref (s3/sftp/scp only)"
         data-testid="job-modal-fw-credentials-ref-input" id="a11y-launch-fw-credentials-ref" />
</div>
```
with:
```html
<div x-show="jobModal.fw_location_kind !== 'local'">
  <label  class="field-label" for="a11y-launch-fw-credentials-ref">File Server</label>
  <select x-model="jobModal.fw_credentials_ref" class="field-input field-select"
          data-testid="job-modal-fw-credentials-ref-select" id="a11y-launch-fw-credentials-ref">
    <option value="">— select a file server —</option>
    <template x-for="fs in fileServers.filter(f => jobModal.fw_location_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
      <option :value="fs.name" x-text="fs.name"></option>
    </template>
  </select>
</div>
```

- [ ] **Step 3: Replace the multi_file source/target credential blocks with dropdowns, remove the raw preview fields**

Replace the entire source credentials block, `frontend/partials/tab-launch.html:586-607` (the `credentials_ref` input through the "used only for Preview Mapping" `<p>`):

```html
<!-- was: 22 lines from the mf_source_credentials_ref input through the closing </p> at 607 -->
```
with:
```html
<div x-show="jobModal.mf_source_kind !== 'local'">
  <select x-model="jobModal.mf_source_credentials_ref" class="field-input field-select"
          data-testid="job-modal-mf-source-credentials-ref-select" aria-label="mf source file server">
    <option value="">— select a file server —</option>
    <template x-for="fs in fileServers.filter(f => jobModal.mf_source_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
      <option :value="fs.name" x-text="fs.name"></option>
    </template>
  </select>
</div>
```

Apply the identical transform to the target block, `frontend/partials/tab-launch.html:621-642` (`mf_target_credentials_ref` through its closing "Preview Mapping" `<p>`), swapping `mf_source_*` for `mf_target_*` and the `data-testid` to `job-modal-mf-target-credentials-ref-select`.

This removes the 12 raw preview-only inputs (`job-modal-mf-source-s3-access-key-input`, `-s3-secret-key-input`, `-sftp-host-input`, `-sftp-password-input`, and their `mf-target-*` mirrors, plus the unlabeled region/endpoint/port/username fields alongside them) entirely — a profile selected via the dropdown is already a real, authenticated entity, so Preview Mapping no longer needs its own separate credential-entry UI.

- [ ] **Step 4: Simplify `previewFileMapping()`**

In `frontend/features/launch.js`, delete the `_previewCredsForKind` method (lines 463-480) entirely, and replace `previewFileMapping()` (lines 482-511):

```javascript
    async previewFileMapping() {
      const m = this.jobModal;
      m.mfPreviewLoading = true;
      m.mfPreviewResult = null;
      m.mfPreviewError = '';
      try {
        const fileMapping = this._buildFileMappingConfig(m);
        m.mfPreviewResult = await api('POST', '/api/jobs/preview-file-mapping', { file_mapping: fileMapping });
      } catch (e) {
        m.mfPreviewError = e.message || 'Preview failed';
      } finally {
        m.mfPreviewLoading = false;
      }
    },
```

`_buildFileMappingConfig(m)` (line ~440) already sets `config.source.credentials_ref`/`config.target.credentials_ref` from `m.mf_source_credentials_ref`/`m.mf_target_credentials_ref` when the kind isn't `local` (line 458-459) — unchanged, since Step 3's dropdown writes into that same `jobModal` field, just via a `<select>` instead of a free-text `<input>`.

- [ ] **Step 5: Remove the now-dead `preview_creds` state**

Delete `mf_source_preview_creds: {}, mf_target_preview_creds: {}` from the `jobModal:` initializer at `launch.js:32`, and the two full `mf_source_preview_creds: { aws_access_key_id: '', ... }` / `mf_target_preview_creds: { ... }` object literals at lines 203-204 and 377-378 (the job-modal-reset and job-modal-populate-from-existing-job functions respectively — read both surrounding functions first with `sed -n '195,210p;370,385p' frontend/features/launch.js` so the removal doesn't leave a dangling comma or an unrelated sibling key deleted by mistake).

- [ ] **Step 6: Update the two e2e specs that drive the old raw preview fields**

`tests/e2e/02b-launch-jobs-remote-preview.spec.ts` fills `job-modal-mf-source-s3-access-key-input`/`-s3-secret-key-input` (line 50-51) and asserts the preview request body's `file_mapping.source.credentials_ref` equals the synthetic `'__preview_source__'` (line 64); same pattern for target at lines 95-103 with `-sftp-host-input`/`-sftp-password-input`. Read the full file first (`sed -n '1,110p' tests/e2e/02b-launch-jobs-remote-preview.spec.ts`) to see how it seeds/intercepts the request, then rewrite it using this repo's established `authedContext`/`api-helpers.ts` pattern (`import { authedContext } from './api-helpers'`, same as `32-launch-remaining-job-types.spec.ts`'s `test.afterEach`): in a `test.beforeEach`, `const ctx = await authedContext(adminToken); await ctx.post('/api/file-servers', { data: { name: 'preview_s3', kind: 's3', aws_access_key_id: 'x', aws_secret_access_key: 'y' } }); await ctx.dispose();` — then select `'preview_s3'` via the new `job-modal-mf-source-credentials-ref-select` dropdown instead of filling the deleted raw fields, and change the assertion to `expect(capturedBody.file_mapping.source.credentials_ref).toBe('preview_s3')`. Apply the same transform to the target half of the file (a second profile, e.g. `'preview_sftp_target'`, `kind: 'sftp'`).

`tests/e2e/17b-multi-file-live-remote.spec.ts` (a *live* MinIO/SFTP e2e, per its name — read it fully first, `sed -n '1,160p'`) fills the same raw fields against a real target. Rewrite it the same way: create a `FileServerProfile` via the API pointing at the live MinIO/SFTP endpoint this spec already connects to (the host/port/credentials it currently types into the raw fields become the profile's fields instead), then select that profile via the dropdown. This spec is part of the "live" e2e track (same category as the floci AWS live tests) — check whether it's gated behind an env var/tag the way those are (`grep -n "LIVE_\|test.skip" tests/e2e/17b-multi-file-live-remote.spec.ts`) and preserve that gating.

- [ ] **Step 7: Run the updated e2e specs**

Run: `node node_modules/@playwright/test/cli.js test tests/e2e/02b-launch-jobs-remote-preview.spec.ts --reporter=list`
Expected: pass. (`17b-multi-file-live-remote.spec.ts` requires a live target per Step 6 — run it only if that environment is available; otherwise confirm it still skips cleanly.)

- [ ] **Step 8: Commit**

```bash
git add frontend/features/launch.js frontend/partials/tab-launch.html tests/e2e/02b-launch-jobs-remote-preview.spec.ts tests/e2e/17b-multi-file-live-remote.spec.ts
git commit -m "feat: replace free-text/raw-credential job modal inputs with File Server profile dropdowns"
```

---

### Task 11: Regenerate the bundled `frontend/index.html`

**Files:**
- Modify: `frontend/index.html`

The repo bundles `frontend/partials/*.html` (via `<!-- INCLUDE: ... -->` markers) + `frontend/features/*.js` into `frontend/index.html` using `scripts/build-html.js`, run via the `npm run build:html` script defined in `package.json`.

- [ ] **Step 1: Run the build**

Run: `npm run build:html`
Expected: exits 0, rewrites `frontend/index.html`.

- [ ] **Step 2: Verify the new markup is present**

Run: `grep -c "file-server" frontend/index.html`
Expected: > 0

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "build: regenerate frontend/index.html to include File Servers UI"
```

---

### Task 12: e2e coverage — File Servers tab CRUD + Launch-tab dropdown integration

**Files:**
- Create: `tests/e2e/33-file-servers.spec.ts`
- Modify: `tests/e2e/32-launch-remaining-job-types.spec.ts`

- [ ] **Step 1: File Servers tab CRUD spec**

Create `tests/e2e/33-file-servers.spec.ts`, following `32-launch-remaining-job-types.spec.ts`'s exact structure (`import { test, expect } from './fixtures'`, a `test.describe` block, `data-testid` locators):

```typescript
import { test, expect } from './fixtures';

test.describe('33 file servers: profile CRUD', () => {
  test('create, mask secret, edit, delete an SFTP profile', async ({ authedPage }) => {
    const name = `e2e-sftp-${Date.now()}`;

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-file-servers"]').click();
    await authedPage.locator('[data-testid="file-server-add-btn"]').click();
    await authedPage.locator('[data-testid="file-server-name-input"]').fill(name);
    await authedPage.locator('[data-testid="file-server-kind-select"]').selectOption('sftp');
    await authedPage.locator('[data-testid="file-server-password-input"]').fill('hunter2');
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();

    const row = authedPage.locator(`[data-testid^="file-server-"]`, { hasText: name });
    await expect(row).toBeVisible();

    await row.getByRole('button', { name: 'Edit' }).click();
    await expect(authedPage.locator('[data-testid="file-server-password-input"]')).toHaveValue('********');

    await authedPage.locator('[data-testid="file-server-save-btn"]').click();
    await row.getByRole('button', { name: 'Delete' }).click();
    await expect(row).toBeHidden();
  });
});
```

- [ ] **Step 2: Launch-tab dropdown integration spec**

Extend `tests/e2e/32-launch-remaining-job-types.spec.ts`'s existing `'file_watcher: location and max tries round-trip'` test (line 192-218) — it currently only covers `location.kind === 'local'` (its own comment at line 205 says so). Add a new test in the same `describe` block, right after it:

```typescript
  test('file_watcher: sftp location uses a File Server profile via credentials_ref dropdown', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-file-watcher-sftp-${Date.now()}`;
    createdJobNames.push(jobName);
    const profileName = `e2e-sftp-profile-${Date.now()}`;

    const ctx = await authedContext(adminToken);
    try {
      await ctx.post('/api/file-servers', {
        data: { name: profileName, kind: 'sftp', host: 'sftp.example.internal', port: 22, username: 'svc', auth_method: 'password', password: 'x' },
      });
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('file_watcher');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-fw-location-kind-select"]').selectOption('sftp');
    await authedPage.locator('[data-testid="job-modal-fw-root-input"]').fill('/inbound');
    await authedPage.locator('[data-testid="job-modal-fw-pattern-input"]').fill('*.csv');
    await authedPage.locator('[data-testid="job-modal-fw-max-tries-input"]').fill('5');
    await authedPage.locator('[data-testid="job-modal-fw-credentials-ref-select"]').selectOption(profileName);

    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-fw-credentials-ref-select"]')).toHaveValue(profileName);
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });
```

This requires adding `import { authedContext } from './api-helpers';` to `32-launch-remaining-job-types.spec.ts`'s imports (line 2 already imports `authedContext, deleteJob` from the same module — just reuse it, no new import line needed).

- [ ] **Step 3: Run both specs**

Run: `node node_modules/@playwright/test/cli.js test tests/e2e/33-file-servers.spec.ts tests/e2e/32-launch-remaining-job-types.spec.ts --reporter=list`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/33-file-servers.spec.ts tests/e2e/32-launch-remaining-job-types.spec.ts
git commit -m "test(e2e): add File Server profile CRUD and credentials_ref dropdown coverage"
```

---

### Task 13: Migration note + help content

**Files:**
- Modify: `frontend/help-content.js:141` (the existing file_watcher help entry, found during investigation)
- Modify: `docs/superpowers/plans/2026-09-15-file-server-auth-profiles.md` is this file — no; modify the design spec's rollout note is already written. This task just updates in-app help text and adds a one-time audit script.

- [ ] **Step 1: Update the file_watcher help entry**

In `frontend/help-content.js:141`, the existing text says `"scp" is accepted as a location kind but is treated as SFTP under the hood (same host/port/credentials_ref shape); there is no separate raw-SCP transport.` — append: `credentials_ref must name a saved File Server profile (File Servers tab) — there is no more inline/raw credential entry.`

- [ ] **Step 2: Write the one-time audit helper**

Create `scripts/list_file_server_credential_refs.py` (a standalone script an operator runs once before deploying this change, per the design doc's migration note):

```python
"""One-time audit: list every distinct file_watcher/file_mapping
credentials_ref currently referenced by a saved job, sequence, or schedule,
so an operator can create a matching FileServerProfile for each one before
this deploy removes the old inline-credentials fallback. Run with:
    python scripts/list_file_server_credential_refs.py
"""
from __future__ import annotations

from etl_framework.repository.database import SessionLocal
from etl_framework.repository.models import ExecutionSequenceVersion, SavedJob, ScheduledRun


def _find_refs(value, found: set[str]) -> None:
    if isinstance(value, dict):
        ref = value.get("credentials_ref")
        if isinstance(ref, str) and ref:
            found.add(ref)
        for v in value.values():
            _find_refs(v, found)
    elif isinstance(value, list):
        for v in value:
            _find_refs(v, found)


def main() -> None:
    found: set[str] = set()
    with SessionLocal() as db:
        for job in db.query(SavedJob).all():
            _find_refs(job.params, found)
        for version in db.query(ExecutionSequenceVersion).all():
            _find_refs(version.steps_json, found)
        for schedule in db.query(ScheduledRun).all():
            _find_refs(schedule.job_sequence, found)

    if not found:
        print("No credentials_ref values found — nothing to migrate.")
        return
    print("Create a File Server profile for each of these names before deploying:")
    for ref in sorted(found):
        print(f"  - {ref}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add frontend/help-content.js scripts/list_file_server_credential_refs.py
git commit -m "docs: update file_watcher help text, add pre-migration credentials_ref audit script"
```

---

### Task 14: Full verification

- [ ] **Step 1: Run the full unit suite**

Run: `python -m pytest tests/unit -q`
Expected: all pass, 0 failures (compare count to the pre-change baseline — 2548 passed, 2 skipped per the last full run on this branch — this change adds roughly 25-30 new tests across Tasks 2/4/5/8).

- [ ] **Step 2: Run the file_watcher/multi_file/file_server e2e specs**

Run: `node node_modules/@playwright/test/cli.js test -g "file server|file_watcher|multi_file" --reporter=list`
Expected: all pass.

- [ ] **Step 3: Manually smoke-test in a browser**

Start the app (find the project's existing dev-run instructions — check for a `run` skill or `README.md`'s "Running locally" section), open the File Servers tab, create an SFTP profile with a fake host, click Test Connection, confirm it reports a connection error (not a 500/crash) since the fake host doesn't exist — this exercises the real (non-mocked) `paramiko.Transport` failure path end-to-end.
