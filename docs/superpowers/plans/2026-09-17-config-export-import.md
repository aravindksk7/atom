# Config Export/Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user export a chosen set of file servers, configs, jobs, execution sequences, and job selections to a single JSON bundle file, and import that bundle into another (or the same) installation, with secrets redacted and existing records left untouched on name collision.

**Architecture:** One backend service module (`api/services/config_bundle.py`) assembles/applies the bundle in strict dependency order (file servers → configs → jobs → sequences → selections), resolving cross-references by **name** rather than numeric ID so a bundle is portable across installs. One new router (`api/routes/bundle.py`) exposes `list`/`export`/`import`. One new frontend tab (Alpine.js, matching the existing `features/*.js` + `partials/tab-*.html` pattern) drives a checklist export and a file-upload import with a result table.

**Tech Stack:** FastAPI + SQLAlchemy + Pydantic (backend, existing stack), Alpine.js + vanilla JS (frontend, existing stack), pytest (backend tests), Playwright (e2e).

**Reference:** `docs/superpowers/specs/2026-09-17-config-export-import-design.md`

**Design note (not in the spec, decided during planning):** each sequence/selection carries only its **latest** version into the bundle. Because collisions are always skipped (never merged), there is never a need to replay older version history through import — the spec's `"versions": [...]` sketch is satisfied by a single-entry list at create time (`version_number=1`). Cross-entity references (`config_id`, `sequence_id`) are exported as the referenced entity's **name** (`config_name`, `sequence_name`) and resolved back to a local numeric ID at import time — this achieves the spec's "remap IDs" requirement without needing an old-ID→new-ID tracking table, and works the same whether the parent was just created or already existed locally.

---

## 1. Data Model / Dependency Order

No new DB tables. Existing repositories used as-is:

- `ConfigRepository`, `FileServerProfileRepository`, `JobRepository`, `JobSelectionRepository` — `etl_framework/repository/repository.py`
- `ExecutionSequenceRepository` — `etl_framework/repository/sequence_repository.py`

## 2. File Structure

- Create: `api/services/config_bundle.py` — `build_bundle()`, `apply_bundle()`, secret-stripping helpers, `BundleItemResult`.
- Create: `api/routes/bundle.py` — `GET /list`, `POST /export`, `POST /import`.
- Modify: `api/main.py` — register the new router.
- Create: `tests/unit/test_config_bundle.py` — service-level tests (no HTTP).
- Create: `tests/unit/test_bundle_api.py` — API-level tests (FastAPI `TestClient`).
- Create: `frontend/features/import-export.js` — Alpine data slice.
- Create: `frontend/partials/tab-import-export.html` — tab markup.
- Modify: `frontend/index.template.html` — script tag + `INCLUDE` marker.
- Modify: `frontend/app.js` — `tabs` array, `FEATURE_SLICES` array, `onTabEnter`.
- Modify (generated): `frontend/index.html` — via `npm run build:html`.
- Create: `tests/e2e/54-import-export.spec.ts`.

---

### Task 1: Bundle service — export side

**Files:**
- Create: `api/services/config_bundle.py`
- Test: `tests/unit/test_config_bundle.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_config_bundle.py
from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401 -- registers ORM models with Base
from etl_framework.repository.repository import ConfigRepository, FileServerProfileRepository
from api.services.config_bundle import BUNDLE_VERSION, build_bundle


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)
    yield key
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    importlib.reload(secret_store)


def _db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_build_bundle_sets_version_and_empty_lists_for_unselected_types():
    db = _db()
    bundle = build_bundle(db, {})
    assert bundle["bundle_version"] == BUNDLE_VERSION
    assert bundle["file_servers"] == []
    assert bundle["configs"] == []
    assert bundle["jobs"] == []
    assert bundle["sequences"] == []
    assert bundle["selections"] == []


def test_build_bundle_strips_config_secrets():
    db = _db()
    ConfigRepository(db).create(
        name="dev", env_name="dev",
        config_data={"db_host": "localhost", "db_password": "hunter2"},
    )
    bundle = build_bundle(db, {"configs": ["dev"]})
    assert len(bundle["configs"]) == 1
    entry = bundle["configs"][0]
    assert entry["name"] == "dev"
    assert entry["env_name"] == "dev"
    assert entry["config_data"]["db_host"] == "localhost"
    assert entry["config_data"]["db_password"] is None


def test_build_bundle_strips_file_server_secrets():
    db = _db()
    FileServerProfileRepository(db).create({
        "name": "sftp_inbound", "kind": "sftp", "host": "sftp.internal", "port": 22,
        "username": "svc", "auth_method": "password", "password": "hunter2",
    })
    bundle = build_bundle(db, {"file_servers": ["sftp_inbound"]})
    assert len(bundle["file_servers"]) == 1
    entry = bundle["file_servers"][0]
    assert entry["name"] == "sftp_inbound"
    assert entry["host"] == "sftp.internal"
    assert entry["password"] is None


def test_build_bundle_skips_unknown_names_silently():
    db = _db()
    bundle = build_bundle(db, {"configs": ["does-not-exist"]})
    assert bundle["configs"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_config_bundle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.services.config_bundle'`

- [ ] **Step 3: Write `api/services/config_bundle.py` (export half)**

```python
"""Assemble and apply a portable JSON bundle of file servers, configs, jobs,
execution sequences, and job selections, for moving records between
installs. See docs/superpowers/specs/2026-09-17-config-export-import-design.md.

Cross-entity references (a sequence's default config, a selection's config or
sequence) are carried by NAME, not numeric ID, so a bundle built on one
install resolves correctly on another where the same names may have
different row IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from etl_framework.config.models import SECRET_FIELDS
from etl_framework.repository.repository import (
    ConfigRepository,
    FileServerProfileRepository,
    JobRepository,
    JobSelectionRepository,
    _FILE_SERVER_SECRET_FIELDS,
)
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

BUNDLE_VERSION = 1
ENTITY_TYPES = ("file_servers", "configs", "jobs", "sequences", "selections")


@dataclass
class BundleItemResult:
    type: str
    name: str
    status: str  # "created" | "skipped" | "error"
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"type": self.type, "name": self.name, "status": self.status, "reason": self.reason}


def _strip_config_secrets(data: dict) -> dict:
    """Blank every SECRET_FIELDS value (top-level, plus nested
    connections/api_endpoints entries) to None. Mirrors the walk in
    api/routes/configs.py::_mask, but blanks instead of masking -- an
    exported bundle must not carry a value a reader could mistake for real,
    even a fixed placeholder string."""
    def _strip_entry(entry: dict) -> dict:
        return {k: (None if k in SECRET_FIELDS else v) for k, v in entry.items()}

    result = _strip_entry(data)
    for group_key in ("connections", "api_endpoints"):
        group = data.get(group_key)
        if isinstance(group, dict):
            result[group_key] = {
                name: (_strip_entry(entry) if isinstance(entry, dict) else entry)
                for name, entry in group.items()
            }
    return result


def _strip_file_server_secrets(data: dict) -> dict:
    return {k: (None if k in _FILE_SERVER_SECRET_FIELDS else v) for k, v in data.items()}


def _export_file_server(profile) -> dict:
    data = {
        "name": profile.name, "kind": profile.kind, "description": profile.description,
        "host": profile.host, "port": profile.port, "username": profile.username,
        "auth_method": profile.auth_method, "password": profile.password,
        "private_key": profile.private_key, "key_passphrase": profile.key_passphrase,
        "host_key_fingerprint": profile.host_key_fingerprint,
        "aws_access_key_id": profile.aws_access_key_id,
        "aws_secret_access_key": profile.aws_secret_access_key,
        "aws_session_token": profile.aws_session_token,
        "region_name": profile.region_name, "endpoint_url": profile.endpoint_url,
    }
    return _strip_file_server_secrets(data)


def _export_config(cfg) -> dict:
    return {
        "name": cfg.name, "env_name": cfg.env_name,
        "config_data": _strip_config_secrets(cfg.config_json or {}),
    }


def _export_sequence(sequence_repo, config_repo, name: str) -> dict | None:
    sequence = sequence_repo.get_by_name(name)
    if sequence is None:
        return None
    version = sequence_repo.latest_version(sequence.id)
    defaults = dict((version.defaults_json or {}) if version else {})
    config_id = defaults.pop("config_id", None)
    if config_id is not None:
        cfg = config_repo.get(config_id)
        defaults["config_name"] = cfg.name if cfg else None
    return {
        "name": sequence.name, "description": sequence.description, "tags": sequence.tags or [],
        "steps": (version.steps_json or []) if version else [],
        "preconditions": version.preconditions_json if version else None,
        "defaults": defaults,
    }


def _export_selection(selection_repo, config_repo, sequence_repo, name: str) -> dict | None:
    sel = selection_repo.get_by_name(name)
    if sel is None:
        return None
    version = selection_repo.latest_version(sel.id)
    config_name = None
    if version and version.config_id is not None:
        cfg = config_repo.get(version.config_id)
        config_name = cfg.name if cfg else None
    sequence_ref = None
    if version and version.sequence_ref:
        seq = sequence_repo.get(version.sequence_ref.get("sequence_id"))
        if seq is not None:
            sequence_ref = {"sequence_name": seq.name}
    return {
        "name": sel.name, "description": sel.description, "tags": sel.tags or [],
        "job_sequence": (version.job_sequence or []) if version else [],
        "run_settings": (version.run_settings_json or {}) if version else {},
        "config_name": config_name,
        "sequence_ref": sequence_ref,
    }


def build_bundle(db: Session, selection: dict[str, list[str]]) -> dict:
    """`selection` maps each entity type in ENTITY_TYPES to the list of names
    to include. A missing key, or a name not found, contributes nothing."""
    from api.routes.jobs import _job_to_schema  # local import: avoids a route<->service import cycle

    bundle: dict[str, Any] = {"bundle_version": BUNDLE_VERSION}

    file_server_repo = FileServerProfileRepository(db)
    bundle["file_servers"] = [
        _export_file_server(profile)
        for name in selection.get("file_servers", [])
        if (profile := file_server_repo.get_by_name(name)) is not None
    ]

    config_repo = ConfigRepository(db)
    bundle["configs"] = [
        _export_config(cfg)
        for name in selection.get("configs", [])
        if (cfg := config_repo.get_by_name(name)) is not None
    ]

    job_repo = JobRepository(db)
    bundle["jobs"] = [
        _job_to_schema(job).model_dump(mode="json")
        for name in selection.get("jobs", [])
        if (job := job_repo.get(name)) is not None
    ]

    sequence_repo = ExecutionSequenceRepository(db)
    bundle["sequences"] = [
        entry
        for name in selection.get("sequences", [])
        if (entry := _export_sequence(sequence_repo, config_repo, name)) is not None
    ]

    selection_repo = JobSelectionRepository(db)
    bundle["selections"] = [
        entry
        for name in selection.get("selections", [])
        if (entry := _export_selection(selection_repo, config_repo, sequence_repo, name)) is not None
    ]

    return bundle
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_config_bundle.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add api/services/config_bundle.py tests/unit/test_config_bundle.py
git commit -m "feat: add bundle export assembly for configs, file servers, jobs, sequences, selections"
```

---

### Task 2: Bundle service — import side

**Files:**
- Modify: `api/services/config_bundle.py`
- Test: `tests/unit/test_config_bundle.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/unit/test_config_bundle.py`:

```python
from etl_framework.repository.repository import JobRepository, JobSelectionRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository
from api.services.config_bundle import apply_bundle


def test_apply_bundle_rejects_unsupported_version():
    db = _db()
    with pytest.raises(ValueError, match="bundle_version"):
        apply_bundle(db, {"bundle_version": 999})


def test_apply_bundle_creates_file_server_and_config_with_blank_secrets():
    db = _db()
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "file_servers": [{
            "name": "sftp_inbound", "kind": "sftp", "description": "", "host": "sftp.internal",
            "port": 22, "username": "svc", "auth_method": "password", "password": None,
            "private_key": None, "key_passphrase": None, "host_key_fingerprint": None,
            "aws_access_key_id": None, "aws_secret_access_key": None, "aws_session_token": None,
            "region_name": None, "endpoint_url": None,
        }],
        "configs": [{"name": "dev", "env_name": "dev", "config_data": {"db_host": "localhost", "db_password": None}}],
        "jobs": [], "sequences": [], "selections": [],
    }
    results = apply_bundle(db, bundle)
    statuses = {(r.type, r.name): r.status for r in results}
    assert statuses[("file_servers", "sftp_inbound")] == "created"
    assert statuses[("configs", "dev")] == "created"
    assert FileServerProfileRepository(db).get_by_name("sftp_inbound").password is None
    assert ConfigRepository(db).get_by_name("dev").config_json["db_password"] is None


def test_apply_bundle_skips_existing_by_name():
    db = _db()
    ConfigRepository(db).create(name="dev", env_name="dev", config_data={})
    bundle = {
        "bundle_version": BUNDLE_VERSION, "file_servers": [], "jobs": [], "sequences": [], "selections": [],
        "configs": [{"name": "dev", "env_name": "dev", "config_data": {}}],
    }
    results = apply_bundle(db, bundle)
    assert results[0].status == "skipped"
    assert results[0].reason == "already exists"


def test_apply_bundle_creates_job():
    db = _db()
    bundle = {
        "bundle_version": BUNDLE_VERSION, "file_servers": [], "configs": [], "sequences": [], "selections": [],
        "jobs": [{
            "name": "orders_check", "description": "", "tags": [], "job_type": "reconciliation",
            "query": "SELECT 1", "key_columns": ["id"], "exclude_columns": [], "source_env": None,
            "target_env": None, "params": {}, "enabled": True, "rules": [], "depends_on": [], "pass_condition": None,
        }],
    }
    results = apply_bundle(db, bundle)
    assert results[0].status == "created"
    assert JobRepository(db).get("orders_check") is not None


def test_apply_bundle_resolves_sequence_config_name_and_errors_if_missing():
    db = _db()
    ConfigRepository(db).create(name="dev", env_name="dev", config_data={})
    bundle = {
        "bundle_version": BUNDLE_VERSION, "file_servers": [], "configs": [], "jobs": [], "selections": [],
        "sequences": [
            {"name": "seq_ok", "description": "", "tags": [], "steps": [], "preconditions": None,
             "defaults": {"config_name": "dev"}},
            {"name": "seq_missing_config", "description": "", "tags": [], "steps": [], "preconditions": None,
             "defaults": {"config_name": "nope"}},
        ],
    }
    results = apply_bundle(db, bundle)
    by_name = {r.name: r for r in results}
    assert by_name["seq_ok"].status == "created"
    dev_id = ConfigRepository(db).get_by_name("dev").id
    seq = ExecutionSequenceRepository(db).get_by_name("seq_ok")
    version = ExecutionSequenceRepository(db).latest_version(seq.id)
    assert version.defaults_json["config_id"] == dev_id
    assert by_name["seq_missing_config"].status == "error"


def test_apply_bundle_resolves_selection_sequence_ref_by_name():
    db = _db()
    ExecutionSequenceRepository(db).create(name="seq1", description="", tags=[], steps=[])
    bundle = {
        "bundle_version": BUNDLE_VERSION, "file_servers": [], "configs": [], "jobs": [], "sequences": [],
        "selections": [{
            "name": "sel1", "description": "", "tags": [], "job_sequence": [], "run_settings": {},
            "config_name": None, "sequence_ref": {"sequence_name": "seq1"},
        }],
    }
    results = apply_bundle(db, bundle)
    assert results[0].status == "created"
    seq1_id = ExecutionSequenceRepository(db).get_by_name("seq1").id
    sel = JobSelectionRepository(db).get_by_name("sel1")
    version = JobSelectionRepository(db).latest_version(sel.id)
    assert version.sequence_ref == {"sequence_id": seq1_id, "sequence_version": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_config_bundle.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_bundle'`

- [ ] **Step 3: Append the import half to `api/services/config_bundle.py`**

```python
def _apply_file_servers(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    repo = FileServerProfileRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if repo.get_by_name(name) is not None:
            out.append(BundleItemResult("file_servers", name, "skipped", "already exists"))
            continue
        repo.create(dict(entry))
        out.append(BundleItemResult("file_servers", name, "created"))
    return out


def _apply_configs(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    repo = ConfigRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if repo.get_by_name(name) is not None:
            out.append(BundleItemResult("configs", name, "skipped", "already exists"))
            continue
        repo.create(name=name, env_name=entry["env_name"], config_data=entry.get("config_data") or {})
        out.append(BundleItemResult("configs", name, "created"))
    return out


def _apply_jobs(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    from api.routes.jobs import _job_to_data
    from api.schemas import JobDefinition

    repo = JobRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if repo.get(name) is not None:
            out.append(BundleItemResult("jobs", name, "skipped", "already exists"))
            continue
        try:
            definition = JobDefinition(**entry)
        except Exception as exc:
            out.append(BundleItemResult("jobs", name, "error", str(exc)))
            continue
        repo.create(_job_to_data(definition))
        out.append(BundleItemResult("jobs", name, "created"))
    return out


def _apply_sequences(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    sequence_repo = ExecutionSequenceRepository(db)
    config_repo = ConfigRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if sequence_repo.get_by_name(name) is not None:
            out.append(BundleItemResult("sequences", name, "skipped", "already exists"))
            continue
        defaults = dict(entry.get("defaults") or {})
        config_name = defaults.pop("config_name", None)
        if config_name is not None:
            cfg = config_repo.get_by_name(config_name)
            if cfg is None:
                out.append(BundleItemResult(
                    "sequences", name, "error", f"referenced config '{config_name}' was not found",
                ))
                continue
            defaults["config_id"] = cfg.id
        sequence_repo.create(
            name=name, description=entry.get("description", ""), tags=entry.get("tags") or [],
            steps=entry.get("steps") or [], preconditions=entry.get("preconditions"), defaults=defaults,
        )
        out.append(BundleItemResult("sequences", name, "created"))
    return out


def _apply_selections(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    selection_repo = JobSelectionRepository(db)
    config_repo = ConfigRepository(db)
    sequence_repo = ExecutionSequenceRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if selection_repo.get_by_name(name) is not None:
            out.append(BundleItemResult("selections", name, "skipped", "already exists"))
            continue
        config_id = None
        config_name = entry.get("config_name")
        if config_name is not None:
            cfg = config_repo.get_by_name(config_name)
            if cfg is None:
                out.append(BundleItemResult(
                    "selections", name, "error", f"referenced config '{config_name}' was not found",
                ))
                continue
            config_id = cfg.id
        sequence_ref = None
        ref = entry.get("sequence_ref")
        if ref is not None:
            seq = sequence_repo.get_by_name(ref["sequence_name"])
            if seq is None:
                out.append(BundleItemResult(
                    "selections", name, "error",
                    f"referenced sequence '{ref['sequence_name']}' was not found",
                ))
                continue
            sequence_ref = {"sequence_id": seq.id, "sequence_version": None}
        selection_repo.create(
            name=name, description=entry.get("description", ""), tags=entry.get("tags") or [],
            job_sequence=entry.get("job_sequence") or [], run_settings=entry.get("run_settings") or {},
            config_id=config_id, sequence_ref=sequence_ref,
        )
        out.append(BundleItemResult("selections", name, "created"))
    return out


def apply_bundle(db: Session, bundle: dict) -> list[BundleItemResult]:
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError(f"Unsupported bundle_version: {bundle.get('bundle_version')!r}")
    results: list[BundleItemResult] = []
    results += _apply_file_servers(db, bundle.get("file_servers") or [])
    results += _apply_configs(db, bundle.get("configs") or [])
    results += _apply_jobs(db, bundle.get("jobs") or [])
    results += _apply_sequences(db, bundle.get("sequences") or [])
    results += _apply_selections(db, bundle.get("selections") or [])
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_config_bundle.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add api/services/config_bundle.py tests/unit/test_config_bundle.py
git commit -m "feat: add bundle import apply with skip-on-duplicate and name-based ref resolution"
```

---

### Task 3: Bundle API routes

**Files:**
- Create: `api/routes/bundle.py`
- Modify: `api/main.py`
- Test: `tests/unit/test_bundle_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_bundle_api.py
from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import ConfigRepository, TokenRepository
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
        c.engine = engine
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)
    yield key
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    importlib.reload(secret_store)


def test_list_bundle_items_returns_configs(client):
    with Session(client.engine) as db:
        ConfigRepository(db).create(name="dev", env_name="dev", config_data={})
    resp = client.get("/api/bundle/list")
    assert resp.status_code == 200
    assert {"type": "configs", "name": "dev"} in resp.json()


def test_export_then_import_round_trip_skips_everything(client):
    with Session(client.engine) as db:
        ConfigRepository(db).create(name="dev", env_name="dev", config_data={"db_host": "localhost"})

    export_resp = client.post("/api/bundle/export", json={"selection": {"configs": ["dev"]}})
    assert export_resp.status_code == 200
    bundle = export_resp.json()
    assert bundle["configs"][0]["name"] == "dev"

    import_resp = client.post("/api/bundle/import", json=bundle)
    assert import_resp.status_code == 200
    assert import_resp.json() == [{"type": "configs", "name": "dev", "status": "skipped", "reason": "already exists"}]


def test_import_rejects_bad_bundle_version(client):
    resp = client.post("/api/bundle/import", json={"bundle_version": 999})
    assert resp.status_code == 400


def test_export_rejects_unknown_entity_type(client):
    resp = client.post("/api/bundle/export", json={"selection": {"not_a_type": ["x"]}})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_bundle_api.py -v`
Expected: FAIL with 404s (routes don't exist yet)

- [ ] **Step 3: Write `api/routes/bundle.py`**

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.audit_service import AuditService
from api.services.config_bundle import ENTITY_TYPES, apply_bundle, build_bundle
from etl_framework.repository.repository import (
    ConfigRepository,
    FileServerProfileRepository,
    JobRepository,
    JobSelectionRepository,
)
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

router = APIRouter(tags=["bundle"])


class BundleListItem(BaseModel):
    type: str
    name: str


class BundleExportRequest(BaseModel):
    selection: dict[str, list[str]]


class BundleItemOut(BaseModel):
    type: str
    name: str
    status: str
    reason: str | None = None


@router.get("/list", response_model=list[BundleListItem])
def list_bundle_items(db: Session = Depends(get_session)):
    items: list[BundleListItem] = []
    items += [BundleListItem(type="file_servers", name=p.name) for p in FileServerProfileRepository(db).list()]
    items += [BundleListItem(type="configs", name=c.name) for c in ConfigRepository(db).list()]
    items += [BundleListItem(type="jobs", name=j.name) for j in JobRepository(db).list()]
    items += [BundleListItem(type="sequences", name=s.name) for s in ExecutionSequenceRepository(db).list()]
    items += [BundleListItem(type="selections", name=s.name) for s in JobSelectionRepository(db).list()]
    return items


@router.post("/export")
def export_bundle(body: BundleExportRequest, db: Session = Depends(get_session)):
    unknown = sorted(set(body.selection) - set(ENTITY_TYPES))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown entity type(s): {unknown}")
    return build_bundle(db, body.selection)


@router.post("/import", response_model=list[BundleItemOut])
def import_bundle(body: dict, request: Request, db: Session = Depends(get_session)):
    try:
        results = apply_bundle(db, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    AuditService(db).log(request, "bundle.imported", "bundle", None, {"summary": counts})
    return [BundleItemOut(**r.to_dict()) for r in results]
```

- [ ] **Step 4: Register the router in `api/main.py`**

Add to the import block (after `file_servers as file_servers_routes`, `api/main.py:31`):

```python
from api.routes import bundle as bundle_routes
```

Add to the `include_router` block (after `api/main.py:87`):

```python
app.include_router(bundle_routes.router, prefix="/api/bundle")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_bundle_api.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add api/routes/bundle.py api/main.py tests/unit/test_bundle_api.py
git commit -m "feat: add /api/bundle list/export/import routes"
```

---

### Task 4: Frontend — Import/Export tab

**Files:**
- Create: `frontend/features/import-export.js`
- Create: `frontend/partials/tab-import-export.html`
- Modify: `frontend/index.template.html`
- Modify: `frontend/app.js`
- Generated: `frontend/index.html`

- [ ] **Step 1: Create `frontend/features/import-export.js`**

```js
(function (global) {
  'use strict';
  global.ETL_FEATURE_IMPORT_EXPORT = function () {
    return {
      bundleItems: [],
      bundleSelected: {},
      bundleImportResult: [],
      bundleImporting: false,
      bundleExporting: false,

      async loadBundleList() {
        try {
          this.bundleItems = await api('GET', '/api/bundle/list');
        } catch (e) {
          this.toast('error', 'Failed to load exportable items', e.message);
        }
      },

      bundleItemsByType(type) {
        return this.bundleItems.filter((i) => i.type === type);
      },

      bundleSelectionKey(type, name) {
        return `${type}::${name}`;
      },

      toggleBundleItem(type, name) {
        const key = this.bundleSelectionKey(type, name);
        this.bundleSelected = { ...this.bundleSelected, [key]: !this.bundleSelected[key] };
      },

      async exportBundle() {
        const selection = {};
        for (const key of Object.keys(this.bundleSelected)) {
          if (!this.bundleSelected[key]) continue;
          const [type, name] = key.split('::');
          (selection[type] = selection[type] || []).push(name);
        }
        if (!Object.keys(selection).length) {
          this.toast('error', 'Nothing selected', 'Select at least one item to export');
          return;
        }
        this.bundleExporting = true;
        try {
          const bundle = await api('POST', '/api/bundle/export', { selection });
          const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
          triggerDownload(blob, `bundle-${Date.now()}.json`);
          this.toast('success', 'Bundle exported');
        } catch (e) {
          this.toast('error', 'Export failed', e.message);
        } finally {
          this.bundleExporting = false;
        }
      },

      async importBundleFile(event) {
        const file = event.target.files && event.target.files[0];
        event.target.value = '';
        if (!file) return;
        this.bundleImporting = true;
        try {
          const text = await file.text();
          const bundle = JSON.parse(text);
          this.bundleImportResult = await api('POST', '/api/bundle/import', bundle);
          await this.loadBundleList();
          this.toast('success', 'Import complete');
        } catch (e) {
          this.toast('error', 'Import failed', e.message);
        } finally {
          this.bundleImporting = false;
        }
      },
    };
  };
})(window);
```

- [ ] **Step 2: Create `frontend/partials/tab-import-export.html`**

```html
<template x-if="currentView === 'import-export'"><div data-testid="import-export-tab">
  <div class="section-header">
    <div>
      <div class="section-title">Import / Export</div>
      <div class="section-sub">Move configs, file servers, jobs, sequences, and job selections between environments.</div>
    </div>
  </div>

  <div class="card mb-4">
    <div class="section-title" style="font-size: 1rem;">Export</div>
    <template x-for="type in ['file_servers', 'configs', 'jobs', 'sequences', 'selections']" :key="type">
      <div class="mb-4">
        <div class="text-sm font-semibold mb-2" x-text="type.replace('_', ' ')"></div>
        <template x-if="!bundleItemsByType(type).length">
          <div class="text-muted text-sm" :data-testid="'import-export-empty-' + type">None saved</div>
        </template>
        <template x-for="item in bundleItemsByType(type)" :key="item.type + item.name">
          <label class="flex items-center gap-2 text-sm mb-1">
            <input type="checkbox"
                   :checked="bundleSelected[bundleSelectionKey(item.type, item.name)]"
                   @change="toggleBundleItem(item.type, item.name)"
                   :data-testid="'import-export-check-' + item.type + '-' + item.name">
            <span x-text="item.name"></span>
          </label>
        </template>
      </div>
    </template>
    <button class="btn-primary" @click="exportBundle()" :disabled="bundleExporting" data-testid="import-export-export-btn">
      <span x-show="!bundleExporting">Export Selected</span>
      <span x-show="bundleExporting">Exporting...</span>
    </button>
  </div>

  <div class="card">
    <div class="section-title" style="font-size: 1rem;">Import</div>
    <input type="file" accept="application/json" @change="importBundleFile($event)" data-testid="import-export-file-input">
    <template x-if="bundleImportResult.length">
      <div class="table-scroll mt-4">
        <table class="data-table" aria-label="Import result">
          <thead><tr><th scope="col">Type</th><th scope="col">Name</th><th scope="col">Status</th><th scope="col">Reason</th></tr></thead>
          <tbody>
            <template x-for="row in bundleImportResult" :key="row.type + row.name">
              <tr :data-testid="'import-export-result-' + row.type + '-' + row.name">
                <td x-text="row.type"></td>
                <td x-text="row.name"></td>
                <td x-text="row.status"></td>
                <td x-text="row.reason || ''"></td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</div></template>
```

- [ ] **Step 3: Wire the tab into `frontend/app.js`**

In the `tabs` array (`frontend/app.js:214`, right after the `logs` tab entry and before the `help` tab entry), add:

```js
      { id: 'import-export', label: 'Import/Export', group: 'system',
        icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>' },
```

In `onTabEnter` (`frontend/app.js:370-379`), add a load call:

```js
      if (id === 'import-export') this.loadBundleList();
```

In the `FEATURE_SLICES` array (`frontend/app.js:1499`), add `ETL_FEATURE_IMPORT_EXPORT()` to the list.

- [ ] **Step 4: Wire the tab into `frontend/index.template.html`**

After the script tag for `features/file-servers.js` (`frontend/index.template.html:456`), add:

```html
    <script src="features/import-export.js"></script>
```

After the include marker for `partials/tab-file-servers.html` (`frontend/index.template.html:163`), add:

```html
<!-- INCLUDE: partials/tab-import-export.html -->
```

- [ ] **Step 5: Rebuild `frontend/index.html`**

Run: `npm run build:html`
Expected: `Built .../frontend/index.html from .../frontend/index.template.html + N partials` with no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/import-export.js frontend/partials/tab-import-export.html frontend/index.template.html frontend/app.js frontend/index.html
git commit -m "feat: add Import/Export tab for bundle export and import"
```

---

### Task 5: End-to-end round trip

**Files:**
- Create: `tests/e2e/54-import-export.spec.ts`

- [ ] **Step 1: Write the e2e test**

```ts
import { test, expect } from './fixtures';
import { authedContext } from './api-helpers';

let token: string;
let configName: string;

test.beforeAll(async ({ adminToken }) => {
  token = adminToken;
  configName = `e2e-import-export-${Date.now()}`;
  const ctx = await authedContext(token);
  try {
    const resp = await ctx.post('/api/configs', {
      data: { name: configName, env_name: configName, config_data: { db_host: 'localhost' } },
    });
    if (!resp.ok()) throw new Error(`create config failed: ${resp.status()} ${await resp.text()}`);
  } finally { await ctx.dispose(); }
});

test.afterAll(async () => {
  const ctx = await authedContext(token);
  try {
    const list = await ctx.get('/api/configs');
    const configs = await list.json();
    const match = configs.find((c: any) => c.name === configName);
    if (match) await ctx.delete(`/api/configs/${match.id}`);
  } finally { await ctx.dispose(); }
});

test('exports a selected config and re-import reports it as skipped', async ({ authedPage }) => {
  await authedPage.goto('/#import-export');
  await expect(authedPage.locator('[data-testid="import-export-tab"]')).toBeVisible();

  const checkbox = authedPage.locator(`[data-testid="import-export-check-configs-${configName}"]`);
  await expect(checkbox).toBeVisible();
  await checkbox.check();

  const downloadPromise = authedPage.waitForEvent('download');
  await authedPage.locator('[data-testid="import-export-export-btn"]').click();
  const download = await downloadPromise;
  const bundlePath = await download.path();
  expect(bundlePath).toBeTruthy();

  const fs = require('fs');
  const bundleText = fs.readFileSync(bundlePath, 'utf-8');
  const bundle = JSON.parse(bundleText);
  expect(bundle.configs.some((c: any) => c.name === configName)).toBe(true);
  expect(bundle.configs.find((c: any) => c.name === configName).config_data.db_host).toBe('localhost');

  await authedPage.setInputFiles('[data-testid="import-export-file-input"]', {
    name: 'bundle.json',
    mimeType: 'application/json',
    buffer: Buffer.from(bundleText),
  });

  const resultRow = authedPage.locator(`[data-testid="import-export-result-configs-${configName}"]`);
  await expect(resultRow).toBeVisible();
  await expect(resultRow).toContainText('skipped');
});
```

- [ ] **Step 2: Run the test**

Run: `node node_modules/@playwright/test/cli.js test tests/e2e/54-import-export.spec.ts`
Expected: 1 passed

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/54-import-export.spec.ts
git commit -m "test(e2e): cover config export/import bundle round trip"
```

---

## Self-Review Notes

- **Spec coverage:** file servers/configs/jobs/sequences/selections export+import (Tasks 1-3); secret redaction (Task 1); skip-on-duplicate (Task 2); name-based ref resolution for sequences/selections (Task 2); `GET /list`, `POST /export`, `POST /import` (Task 3); checklist UI + result table (Task 4); e2e round trip (Task 5). Scheduled runs are explicitly out of scope per the spec — no task touches `ScheduledRun`.
- **Placeholder scan:** none — every step has complete, runnable code.
- **Type consistency:** `BundleItemResult`/`BundleItemOut` fields (`type`, `name`, `status`, `reason`) match across the service, the route, and the frontend's `bundleImportResult` rendering. `ENTITY_TYPES` in the service matches the keys used in `build_bundle`'s per-type loops and the frontend's `type in [...]` loop.
