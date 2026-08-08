# SAP BO Download Server-Side Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a user downloads a SAP BO report from the Adapters tab, the server also writes a timestamped copy into a directory configured once on the Config tab, while the browser still receives its own copy.

**Architecture:** A new `api/services/bo_archive.py` owns the three write policies (timestamped naming, never overwrite, never raise) behind one function. `AdapterService.download_bo_report` calls it and returns a `BOReportDownload` dataclass instead of raw bytes. The four download routes read the directory out of app settings, pass it in, and surface the outcome as `X-Saved-Path` / `X-Save-Error` headers that the browser turns into toasts.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy (`api/`, `etl_framework/repository/`), Alpine.js (`frontend/`), pytest.

Spec: `docs/superpowers/specs/2026-08-07-bo-download-server-copy-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| **Create** `api/services/bo_archive.py` | Resolve directory, build a collision-free timestamped name, sanitize ids, write, never raise |
| **Create** `tests/unit/test_bo_archive.py` | Direct tests of those four policies — no HTTP, no BO client |
| **Modify** `etl_framework/repository/models.py` | `AppSettings.bo_download_dir` column |
| **Modify** `etl_framework/repository/database.py` | `ensure_column` for the new column on existing databases |
| **Modify** `etl_framework/repository/repository.py` | `SettingsRepository.get_bo_download_dir` / `set_bo_download_dir` + validation |
| **Modify** `tests/unit/test_settings_repository.py` | Validation tests |
| **Modify** `api/routes/settings.py` | Expose the field on GET and PUT |
| **Modify** `api/services/adapter_service.py` | `BOReportDownload` dataclass, `download_dir` parameter |
| **Modify** `api/routes/adapters.py` | Read the setting, pass it, one shared response helper adding the headers |
| **Modify** `tests/unit/test_adapter_service.py` | Update mocks for the new return type |
| **Modify** `tests/unit/test_adapters_routes.py` | Header assertions |
| **Modify** `frontend/app.js` | `apiBlob` surfaces the headers; Config tab state + save function |
| **Modify** `frontend/partials/tab-config.html` | The settings card |
| **Modify** `frontend/features/adapters.js` | One shared finish-download helper producing the toasts |
| **Modify** `tests/integration/test_sapbo_ui_download_flow.py` | Prove the file lands on disk through the real route |

Tasks 1–3 are backend-only and independent of the UI. Task 4 is the breaking change. Tasks 5–7 are the wiring. Task 8 is the proof.

---

### Task 1: The archive module

**Files:**
- Create: `api/services/bo_archive.py`
- Test: `tests/unit/test_bo_archive.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_bo_archive.py`:

```python
"""Tests for the server-side copy of a SAP BO download.

Everything here runs against bytes and a tmp directory — no HTTP, no BO
client. That is the whole reason the write policy lives in its own module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.services.bo_archive import save_bo_download

STAMP = datetime(2026, 8, 7, 20, 30, 15, tzinfo=timezone.utc)


def test_empty_directory_means_disabled(tmp_path):
    """An unset setting must be a no-op, not an error and not a write. This is
    the default on every upgraded install."""
    path, error = save_bo_download(
        b"PK\x03\x04", doc_id="124313", report_id="1", fmt="xlsx",
        directory="", now=STAMP,
    )
    assert path is None
    assert error is None
    assert list(tmp_path.iterdir()) == []


def test_writes_a_timestamped_file(tmp_path):
    path, error = save_bo_download(
        b"PK\x03\x04", doc_id="124313", report_id="1", fmt="xlsx",
        directory=str(tmp_path), now=STAMP,
    )
    assert error is None
    assert path == tmp_path / "report_124313_1_20260807T203015Z.xlsx"
    assert path.read_bytes() == b"PK\x03\x04"


def test_whole_document_export_omits_the_report_segment(tmp_path):
    """An empty report_id is SAP's whole-document export; the routes already
    treat it that way in Content-Disposition, so the name must match."""
    path, error = save_bo_download(
        b"data", doc_id="124313", report_id="", fmt="csv",
        directory=str(tmp_path), now=STAMP,
    )
    assert error is None
    assert path == tmp_path / "report_124313_20260807T203015Z.csv"


def test_unknown_format_falls_back_to_bin(tmp_path):
    path, _ = save_bo_download(
        b"data", doc_id="1", report_id="", fmt="docx",
        directory=str(tmp_path), now=STAMP,
    )
    assert path.name.endswith(".bin")


def test_never_overwrites_within_the_same_second(tmp_path):
    """Two downloads inside one second would collide. Timestamped names are
    the whole point of the feature, so the first file must survive."""
    first, _ = save_bo_download(
        b"first", doc_id="124313", report_id="1", fmt="xlsx",
        directory=str(tmp_path), now=STAMP,
    )
    second, _ = save_bo_download(
        b"second", doc_id="124313", report_id="1", fmt="xlsx",
        directory=str(tmp_path), now=STAMP,
    )
    assert first != second
    assert second.name == "report_124313_1_20260807T203015Z-1.xlsx"
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_ids_cannot_escape_the_directory(tmp_path):
    """doc_id and report_id arrive straight off the URL path. Unsanitized,
    an id like ../../x writes outside the directory the operator nominated."""
    nested = tmp_path / "out"
    nested.mkdir()
    path, error = save_bo_download(
        b"data", doc_id="../../evil", report_id="a/b", fmt="xlsx",
        directory=str(nested), now=STAMP,
    )
    assert error is None
    assert path.parent == nested
    assert path.name == "report_______evil_a_b_20260807T203015Z.xlsx"


def test_missing_directory_returns_an_error_and_does_not_raise(tmp_path):
    """A path that vanished between save-time validation and download time —
    a network share, typically. The download must not die with it."""
    path, error = save_bo_download(
        b"data", doc_id="1", report_id="", fmt="xlsx",
        directory=str(tmp_path / "does-not-exist"), now=STAMP,
    )
    assert path is None
    assert error


def test_uses_the_current_time_when_now_is_omitted(tmp_path):
    path, error = save_bo_download(
        b"data", doc_id="1", report_id="", fmt="xlsx",
        directory=str(tmp_path),
    )
    assert error is None
    assert path.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_bo_archive.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'api.services.bo_archive'`

- [ ] **Step 3: Write the implementation**

Create `api/services/bo_archive.py`:

```python
"""Write a copy of a SAP BO export into a directory the operator nominated.

Separate from api_artifact.py on purpose. That module manages the app's own
artifact root, keyed by run and config, and may prune it. This one writes into
a path someone typed on the Config tab — possibly a shared drive holding
unrelated files — so it only ever creates, never deletes, and never raises.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

# Same mapping the download routes apply (api/routes/adapters.py). Carried
# here rather than imported because routes -> service -> archive would become
# circular, and it is one line.
_EXT_MAP = {"pdf": "pdf", "xlsx": "xlsx", "csv": "csv"}

# doc_id and report_id come straight off the URL path.
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def _safe(value: str) -> str:
    return _UNSAFE.sub("_", str(value))


def save_bo_download(content: bytes, *, doc_id: str, report_id: str, fmt: str,
                     directory: str, now: datetime | None = None
                     ) -> tuple[Path | None, str | None]:
    """Write a BO export to `directory`. Never raises.

    (path, None)  wrote it
    (None, error) tried and failed
    (None, None)  disabled, because `directory` is empty
    """
    if not (directory or "").strip():
        return None, None

    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    ext = _EXT_MAP.get(fmt, "bin")
    parts = ["report", _safe(doc_id)]
    if str(report_id or "").strip():
        parts.append(_safe(report_id))
    stem = "_".join(parts) + "_" + stamp

    try:
        target_dir = Path(directory)
        candidate = target_dir / f"{stem}.{ext}"
        # Never overwrite: two downloads inside one second share a stamp.
        suffix = 0
        while candidate.exists():
            suffix += 1
            candidate = target_dir / f"{stem}-{suffix}.{ext}"
        candidate.write_bytes(content)
        return candidate, None
    except OSError as exc:
        return None, str(exc)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_bo_archive.py -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add api/services/bo_archive.py tests/unit/test_bo_archive.py
git commit -m "feat(sap_bo): module that writes a server-side copy of an export

Owns the three policies the design settled: a UTC-timestamped name that never
overwrites, ids sanitized before they reach a filename because they arrive off
the URL path, and an OSError returned rather than raised so a vanished network
share cannot kill a download that otherwise succeeded."
```

---

### Task 2: The setting — column and repository

**Files:**
- Modify: `etl_framework/repository/models.py:333-339`
- Modify: `etl_framework/repository/database.py:318-327`
- Modify: `etl_framework/repository/repository.py` (`SettingsRepository`)
- Test: `tests/unit/test_settings_repository.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_settings_repository.py`:

```python
def test_bo_download_dir_defaults_to_empty():
    """Empty means disabled, and it is the default — an upgraded install keeps
    today's browser-only behaviour until someone sets a path."""
    db = _session()
    assert SettingsRepository(db).get_bo_download_dir() == ""


def test_set_bo_download_dir_round_trips(tmp_path):
    db = _session()
    repo = SettingsRepository(db)
    repo.set_bo_download_dir(str(tmp_path))
    assert repo.get_bo_download_dir() == str(tmp_path)


def test_set_bo_download_dir_accepts_empty_to_disable(tmp_path):
    db = _session()
    repo = SettingsRepository(db)
    repo.set_bo_download_dir(str(tmp_path))
    repo.set_bo_download_dir("")
    assert repo.get_bo_download_dir() == ""


def test_set_bo_download_dir_rejects_a_relative_path():
    db = _session()
    with pytest.raises(ValueError, match="absolute"):
        SettingsRepository(db).set_bo_download_dir("reports/out")


def test_set_bo_download_dir_rejects_a_missing_directory(tmp_path):
    db = _session()
    with pytest.raises(ValueError, match="does not exist"):
        SettingsRepository(db).set_bo_download_dir(str(tmp_path / "nope"))


def test_set_bo_download_dir_rejects_a_file(tmp_path):
    target = tmp_path / "a-file.txt"
    target.write_text("x")
    db = _session()
    with pytest.raises(ValueError, match="not a directory"):
        SettingsRepository(db).set_bo_download_dir(str(target))
```

Check the top of the file for the existing session helper and `pytest` import; if
the helper is named something other than `_session()`, use that name in the tests
above and nothing else changes.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_settings_repository.py -q`
Expected: FAIL — `AttributeError: 'SettingsRepository' object has no attribute 'get_bo_download_dir'`

- [ ] **Step 3: Add the column to the model**

In `etl_framework/repository/models.py`, inside `class AppSettings`, after the
`upload_retention_days` line:

```python
    bo_download_dir = Column(String(1024), nullable=False, default="")
```

- [ ] **Step 4: Add the column to existing databases**

In `etl_framework/repository/database.py`, directly after the existing
`ensure_column(conn, "app_settings", "upload_retention_days", ...)` line:

```python
        ensure_column(conn, "app_settings", "bo_download_dir", "ALTER TABLE app_settings ADD COLUMN bo_download_dir VARCHAR(1024) NOT NULL DEFAULT ''")
```

- [ ] **Step 5: Add the repository methods**

In `etl_framework/repository/repository.py`, inside `SettingsRepository`, after
`get_upload_retention_days`:

```python
    def get_bo_download_dir(self) -> str:
        return self._get_or_create().bo_download_dir or ""

    def set_bo_download_dir(self, path: str) -> AppSettings:
        """Empty disables the feature. Anything else must be a writable
        directory that exists right now.

        Validating here catches a typo where the user can act on it. It cannot
        be the only check — a network share can be reachable at save and gone
        at download — so bo_archive still treats every write as fallible.
        """
        import os

        cleaned = (path or "").strip()
        if cleaned:
            candidate = Path(cleaned)
            if not candidate.is_absolute():
                raise ValueError(f"Download directory must be an absolute path: {cleaned}")
            if not candidate.exists():
                raise ValueError(f"Download directory does not exist: {cleaned}")
            if not candidate.is_dir():
                raise ValueError(f"Download directory is not a directory: {cleaned}")
            # Advisory on Windows; download-time failure is the real check.
            if not os.access(candidate, os.W_OK):
                raise ValueError(f"Download directory is not writable: {cleaned}")
        row = self._get_or_create()
        row.bo_download_dir = cleaned
        row.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(row)
        return row
```

Add `from pathlib import Path` to the module imports if it is not already there.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_settings_repository.py -q`
Expected: all pass, including the four pre-existing timezone tests

- [ ] **Step 7: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py etl_framework/repository/repository.py tests/unit/test_settings_repository.py
git commit -m "feat(settings): app-wide directory for server-side BO download copies

Empty is the default and means disabled, so an upgraded install behaves
exactly as it does today. Non-empty is validated at save so a typo surfaces
where the user can fix it; os.access is advisory on Windows, which is why the
write path still treats every attempt as fallible."
```

---

### Task 3: Expose the setting on the API

**Files:**
- Modify: `api/routes/settings.py`
- Test: `tests/unit/test_settings_routes.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create or append to `tests/unit/test_settings_routes.py`:

```python
"""The settings endpoint's contract for the BO download directory."""
from __future__ import annotations


def test_get_settings_reports_the_bo_download_dir(admin_client):
    resp = admin_client.get("/api/settings")
    assert resp.status_code == 200
    assert resp.json()["bo_download_dir"] == ""


def test_put_settings_sets_the_bo_download_dir(admin_client, tmp_path):
    resp = admin_client.put("/api/settings", json={"bo_download_dir": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["bo_download_dir"] == str(tmp_path)


def test_put_settings_rejects_a_bad_bo_download_dir(admin_client, tmp_path):
    """A ValueError from the repository must surface as 422, the same as an
    unknown timezone already does."""
    resp = admin_client.put(
        "/api/settings", json={"bo_download_dir": str(tmp_path / "nope")})
    assert resp.status_code == 422
    assert "does not exist" in resp.text
```

Prepend this fixture to the file. `PUT /api/settings` carries
`dependencies=[Depends(require_admin)]`, so the token must be created with
`is_admin=True` — the fixture in `test_adapters_routes.py` does not, and copying it
would give you 403s:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository


@pytest.fixture
def admin_client(monkeypatch):
    from api.main import app

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test", is_admin=True)
    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_settings_routes.py -q`
Expected: FAIL — `KeyError: 'bo_download_dir'`

- [ ] **Step 3: Add the field to both schemas and the handler**

In `api/routes/settings.py`:

```python
class SettingsOut(BaseModel):
    timezone: str
    upload_retention_days: int = 30
    bo_download_dir: str = ""


class SettingsUpdate(BaseModel):
    timezone: str | None = None
    upload_retention_days: int | None = None
    bo_download_dir: str | None = None
```

In `get_settings`, add to the returned `SettingsOut`:

```python
        bo_download_dir=repo.get_bo_download_dir(),
```

In `update_settings`, inside the existing `try:` block, after the
`upload_retention_days` branch:

```python
        if body.bo_download_dir is not None:
            row = repo.set_bo_download_dir(body.bo_download_dir)
```

In the same function, extend the audit payload and the returned `SettingsOut`:

```python
        {"timezone": row.timezone,
         "upload_retention_days": row.upload_retention_days,
         "bo_download_dir": row.bo_download_dir},
```

```python
    return SettingsOut(
        timezone=row.timezone,
        upload_retention_days=int(row.upload_retention_days or 30),
        bo_download_dir=row.bo_download_dir or "",
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_settings_routes.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add api/routes/settings.py tests/unit/test_settings_routes.py
git commit -m "feat(settings): expose bo_download_dir on GET and PUT /api/settings

A bad path surfaces as 422 through the handler's existing ValueError branch,
the same as an unknown timezone."
```

---

### Task 4: Service returns the archive outcome

**Files:**
- Modify: `api/services/adapter_service.py:363-386`
- Test: `tests/unit/test_adapter_service.py`

This is the breaking change: `download_bo_report` stops returning `bytes`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_adapter_service.py`:

These use the file's existing `service` fixture (`tests/unit/test_adapter_service.py:32`),
which builds an `AdapterService` over a mocked config repo — no new helper needed.

```python
def test_download_bo_report_writes_a_server_copy(service, tmp_path):
    """The service threads the configured directory into the archive module and
    reports back where the file landed."""
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.download_report.return_value = b"PK\x03\x04"
        result = service.download_bo_report(
            1, "124313", "1", fmt="xlsx", download_dir=str(tmp_path))

    assert result.content == b"PK\x03\x04"
    assert result.save_error is None
    assert result.saved_path is not None
    assert result.saved_path.parent == tmp_path
    assert result.saved_path.read_bytes() == b"PK\x03\x04"


def test_download_bo_report_without_a_directory_saves_nothing(service, tmp_path):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.download_report.return_value = b"PK\x03\x04"
        result = service.download_bo_report(1, "124313", "1", fmt="xlsx")

    assert result.content == b"PK\x03\x04"
    assert result.saved_path is None
    assert result.save_error is None
    assert list(tmp_path.iterdir()) == []


def test_download_bo_report_still_returns_bytes_when_the_copy_fails(service, tmp_path):
    """A bad directory must never cost the user their download."""
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.download_report.return_value = b"PK\x03\x04"
        result = service.download_bo_report(
            1, "124313", "1", fmt="xlsx",
            download_dir=str(tmp_path / "does-not-exist"))

    assert result.content == b"PK\x03\x04"
    assert result.saved_path is None
    assert result.save_error
```

Then update the **existing** `test_download_bo_report_returns_bytes` at
`tests/unit/test_adapter_service.py:214`, which currently asserts on raw bytes:

```python
def test_download_bo_report_returns_bytes(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.download_report.return_value = b"PDF bytes"
        result = service.download_bo_report(1, "101", "1", "pdf")
    assert result.content == b"PDF bytes"
```

Search the rest of the file for other assertions against the return of
`download_bo_report` and give each the same `.content` treatment —
`test_download_bo_report_answers_parameters_before_downloading` around line 221 is
one of them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_adapter_service.py -q`
Expected: FAIL — `AttributeError: 'bytes' object has no attribute 'content'`

- [ ] **Step 3: Add the dataclass and the parameter**

At the top of `api/services/adapter_service.py`, with the other imports:

```python
from dataclasses import dataclass
from pathlib import Path

from api.services.bo_archive import save_bo_download
```

Above the `AdapterService` class:

```python
@dataclass(frozen=True)
class BOReportDownload:
    """A BO export plus what happened to the server-side copy.

    `saved_path` and `save_error` are mutually exclusive, and both are None
    when no download directory is configured.
    """
    content: bytes
    saved_path: Path | None
    save_error: str | None
```

Change the signature and the return in `download_bo_report`:

```python
    def download_bo_report(
        self,
        config_id: int,
        doc_id: str,
        report_id: str = "",
        fmt: str = "xlsx",
        auth: SAPBOAuthContext | None = None,
        parameters: list[dict] | None = None,
        timezone: str | None = None,
        download_dir: str = "",
    ) -> BOReportDownload:
        env = self._get_env_config(config_id)
        with _bo_lock:
            client = self._client_for_auth(env, auth)
            try:
                self._authenticate_if_needed(client, auth)
                if parameters:
                    built = build_parameter_answers(parameters, timezone or "UTC")
                    client.answer_document_parameters(doc_id, built)
                content = client.download_report(doc_id, report_id, fmt)
            except Exception as exc:
                auth_type = auth.auth_type if auth and auth.auth_type else env.bo_auth_type
                raise HTTPException(status_code=502, detail=_friendly_error(exc, auth_type=auth_type)) from exc
            finally:
                client.logout()
        saved_path, save_error = save_bo_download(
            content, doc_id=doc_id, report_id=report_id, fmt=fmt,
            directory=download_dir,
        )
        return BOReportDownload(content=content, saved_path=saved_path, save_error=save_error)
```

Note the archive call sits **outside** `with _bo_lock:` — writing to disk does not
need the BO client lock, and holding it would serialize file writes behind network
calls.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_adapter_service.py -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add api/services/adapter_service.py tests/unit/test_adapter_service.py
git commit -m "feat(sap_bo): service returns the export plus its archive outcome

download_bo_report returns BOReportDownload rather than bytes so the routes can
report where the server copy landed, or why it did not. The archive call sits
outside the BO client lock: a disk write does not need it, and holding it would
queue writes behind network calls."
```

---

### Task 5: Routes read the setting and surface the outcome

**Files:**
- Modify: `api/routes/adapters.py:167-250`
- Test: `tests/unit/test_adapters_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_adapters_routes.py`:

This file has an **autouse** fixture (`mock_adapter_service`, line 39) that replaces
`AdapterService` through `app.dependency_overrides` with a `MagicMock`. So do not
patch the service — set the mock's return value. The routes never reach the real
service, which is also why these tests do not need the setting configured.

```python
def test_download_route_reports_the_saved_path(client, mock_adapter_service, tmp_path):
    """The header is percent-encoded: HTTP header values are latin-1 and a
    Windows share path can carry characters outside it."""
    from urllib.parse import unquote
    from api.services.adapter_service import BOReportDownload

    saved = tmp_path / "report_101_1_20260807T203015Z.xlsx"
    mock_adapter_service.download_bo_report.return_value = BOReportDownload(
        content=b"PK", saved_path=saved, save_error=None)

    resp = client.get(
        "/api/adapters/sap-bo/documents/101/reports/1/download?config_id=1")

    assert resp.status_code == 200
    assert resp.content == b"PK"
    assert unquote(resp.headers["x-saved-path"]) == str(saved)
    assert "x-save-error" not in resp.headers


def test_download_route_reports_a_save_error_and_still_returns_the_file(
    client, mock_adapter_service
):
    from urllib.parse import unquote
    from api.services.adapter_service import BOReportDownload

    mock_adapter_service.download_bo_report.return_value = BOReportDownload(
        content=b"PK", saved_path=None, save_error="Permission denied")

    resp = client.get(
        "/api/adapters/sap-bo/documents/101/reports/1/download?config_id=1")

    assert resp.status_code == 200
    assert resp.content == b"PK"
    assert unquote(resp.headers["x-save-error"]) == "Permission denied"
    assert "x-saved-path" not in resp.headers


def test_download_route_sends_neither_header_when_disabled(client, mock_adapter_service):
    from api.services.adapter_service import BOReportDownload

    mock_adapter_service.download_bo_report.return_value = BOReportDownload(
        content=b"PK", saved_path=None, save_error=None)

    resp = client.get(
        "/api/adapters/sap-bo/documents/101/reports/1/download?config_id=1")

    assert "x-saved-path" not in resp.headers
    assert "x-save-error" not in resp.headers
```

For these to receive the mock, `mock_adapter_service` must **return** the mock it
builds. Change its last lines from setting up and yielding nothing to:

```python
    yield svc
```

(keeping everything it already does with `app.dependency_overrides`), and add
`request` — no other test in the file uses its value, so nothing else changes.

- [ ] **Step 1b: Update the autouse fixture's stale return value**

Still in `tests/unit/test_adapters_routes.py`, line 53 currently reads:

```python
    svc.download_bo_report.return_value = b"PDF content"
```

Once the routes call `result.content`, that raw-bytes mock makes every existing
download route test fail. Replace it with:

```python
    from api.services.adapter_service import BOReportDownload
    svc.download_bo_report.return_value = BOReportDownload(
        content=b"PDF content", saved_path=None, save_error=None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_adapters_routes.py -q -k "saved_path or save_error or neither_header"`
Expected: FAIL — `KeyError: 'x-saved-path'`

- [ ] **Step 3a: Take the extension mapping from bo_archive**

Task 1's code review established that this module's local `_EXT_MAP` and
`bo_archive`'s copy would drift silently — add a format to one and the browser's
filename and the server copy's filename disagree, with no test failing. There is no
circular-import obstacle: this module already imports `api.services.adapter_service`,
so routes → services is the established direction and importing upward from
`bo_archive` adds no cycle.

Delete the local map at `api/routes/adapters.py:45`:

```python
_EXT_MAP = {"pdf": "pdf", "xlsx": "xlsx", "csv": "csv"}
```

and import Task 1's public one instead, with the other imports at the top:

```python
from api.services.bo_archive import EXT_MAP as _EXT_MAP
```

Every existing `_EXT_MAP.get(...)` call site keeps working unchanged.

- [ ] **Step 3: Add the shared response helper**

In `api/routes/adapters.py`, below the `_MIME_MAP` block at the top:

```python
def _download_response(result, doc_id: str, report_id: str, fmt: str) -> Response:
    """One Response builder for all four download routes.

    Both headers are percent-encoded: HTTP header values are latin-1, and a
    Windows share path or an OS error string can carry characters outside it.
    """
    from urllib.parse import quote

    mime = _MIME_MAP.get(fmt, "application/octet-stream")
    ext = _EXT_MAP.get(fmt, "bin")
    name = f"report_{doc_id}_{report_id}.{ext}" if report_id else f"report_{doc_id}.{ext}"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    if result.saved_path is not None:
        headers["X-Saved-Path"] = quote(str(result.saved_path))
    if result.save_error:
        headers["X-Save-Error"] = quote(result.save_error)
    return Response(content=result.content, media_type=mime, headers=headers)
```

- [ ] **Step 4: Rewrite the four routes to use it**

Replace the bodies of the four download routes. `download_whole_bo_document`:

```python
@router.get("/sap-bo/documents/{doc_id}/download")
def download_whole_bo_document(
    doc_id: str,
    config_id: int,
    request: Request,
    format: str = "xlsx",
    db: Session = Depends(get_session),
    service: AdapterService = Depends(get_adapter_service),
):
    """SAP's primary step 5: every tab of the document in one file. Naming no
    report is the whole point, so this cannot be folded into the report-scoped
    route below — a path segment always names a tab."""
    result = service.download_bo_report(
        config_id,
        doc_id,
        "",
        fmt=format,
        auth=_sap_bo_auth_from_request(request),
        download_dir=SettingsRepository(db).get_bo_download_dir(),
    )
    return _download_response(result, doc_id, "", format)
```

`download_whole_bo_document_with_parameters`:

```python
@router.post("/sap-bo/documents/{doc_id}/download")
def download_whole_bo_document_with_parameters(
    doc_id: str,
    config_id: int,
    body: BOReportDownloadRequest,
    request: Request,
    db: Session = Depends(get_session),
    service: AdapterService = Depends(get_adapter_service),
):
    settings = SettingsRepository(db)
    result = service.download_bo_report(
        config_id,
        doc_id,
        "",
        fmt=body.format,
        auth=_sap_bo_auth_from_request(request),
        parameters=[p.model_dump() for p in body.parameters],
        timezone=settings.get_timezone(),
        download_dir=settings.get_bo_download_dir(),
    )
    return _download_response(result, doc_id, "", body.format)
```

`download_bo_report` (report-scoped GET):

```python
@router.get("/sap-bo/documents/{doc_id}/reports/{report_id}/download")
def download_bo_report(
    doc_id: str,
    report_id: str,
    config_id: int,
    request: Request,
    format: str = "xlsx",
    db: Session = Depends(get_session),
    service: AdapterService = Depends(get_adapter_service),
):
    result = service.download_bo_report(
        config_id,
        doc_id,
        report_id,
        fmt=format,
        auth=_sap_bo_auth_from_request(request),
        download_dir=SettingsRepository(db).get_bo_download_dir(),
    )
    return _download_response(result, doc_id, report_id, format)
```

And the report-scoped POST route immediately below it, the same way:

```python
    settings = SettingsRepository(db)
    result = service.download_bo_report(
        config_id,
        doc_id,
        report_id,
        fmt=body.format,
        auth=_sap_bo_auth_from_request(request),
        parameters=[p.model_dump() for p in body.parameters],
        timezone=settings.get_timezone(),
        download_dir=settings.get_bo_download_dir(),
    )
    return _download_response(result, doc_id, report_id, body.format)
```

Add `db: Session = Depends(get_session)` to that route's signature too, and remove
its now-duplicated `tz = SettingsRepository(db).get_timezone()` line. Ensure
`SettingsRepository` is imported at the top of the module:

```python
from etl_framework.repository.repository import SettingsRepository
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_adapters_routes.py -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add api/routes/adapters.py tests/unit/test_adapters_routes.py
git commit -m "feat(sap_bo): download routes surface where the server copy landed

All four build their Response through one helper so the header logic is written
once. X-Saved-Path and X-Save-Error are percent-encoded because header values
are latin-1 and both a share path and an OS error can exceed it."
```

---

### Task 6: The browser reports the outcome

**Files:**
- Modify: `frontend/app.js:88` (`apiBlob`)
- Modify: `frontend/features/adapters.js:216-272` (`downloadBOReport`)

No automated test — this codebase covers the frontend through Playwright e2e, and
Task 8 proves the behaviour end to end through the real route. Verify this task in
the browser.

- [ ] **Step 1: Surface the headers from apiBlob**

In `frontend/app.js`, replace the return of `apiBlob`:

```javascript
  return {
    blob: await resp.blob(),
    disposition: resp.headers.get('content-disposition') || '',
    savedPath: decodeURIComponent(resp.headers.get('x-saved-path') || ''),
    saveError: decodeURIComponent(resp.headers.get('x-save-error') || ''),
  };
```

- [ ] **Step 2: Add one finish helper and use it in both branches**

In `frontend/features/adapters.js`, add a method next to `downloadBOReport`:

```javascript
    // Both download branches end the same way: hand the blob to the browser,
    // then report the server-side copy. A failed copy still gets its own error
    // toast on top of the success one — the download really did succeed and
    // the archive really did fail, and saying only one of those would be a lie.
    finishBODownload(blob, filename, savedPath, saveError) {
      triggerDownload(blob, filename);
      this.toast('success', 'Download started',
        savedPath ? `Also saved to ${savedPath}` : '');
      if (saveError) {
        this.toast('error', 'Server copy failed', saveError);
      }
    },
```

In the no-prompts branch, replace the `triggerDownload` + `toast` pair with:

```javascript
          const { blob, disposition, savedPath, saveError } = await apiBlob(
            `/api/adapters/sap-bo/documents/${docId}${scope}/download?config_id=${this.boConfigId}&format=${format}`
          );
          const match = disposition.match(/filename="?([^"]+)"?/);
          this.finishBODownload(blob, match ? match[1] : fallbackName, savedPath, saveError);
```

In the prompts branch, replace its `triggerDownload` + `toast` pair with:

```javascript
        const disposition = resp.headers.get('content-disposition') || '';
        const match = disposition.match(/filename="?([^"]+)"?/);
        this.finishBODownload(
          await resp.blob(),
          match ? match[1] : fallbackName,
          decodeURIComponent(resp.headers.get('x-saved-path') || ''),
          decodeURIComponent(resp.headers.get('x-save-error') || ''),
        );
```

- [ ] **Step 3: Verify in the browser**

Start the app, set a valid directory on the Config tab (Task 7 adds that card — if
doing this task first, set it with
`curl -X PUT localhost:8000/api/settings -H 'Content-Type: application/json' -d '{"bo_download_dir":"/tmp/bo"}'`),
download a report, and confirm: the file arrives in the browser, the success toast
reads `Also saved to /tmp/bo/report_...xlsx`, and the file is on disk. Then set the
directory to a path you delete underneath it and confirm the second, red toast.

- [ ] **Step 4: Commit**

```bash
git add frontend/app.js frontend/features/adapters.js
git commit -m "feat(frontend): report where a BO download's server copy landed

Both download branches now end in one shared helper instead of duplicating the
toast code. A failed copy raises its own error toast alongside the success one:
the download did succeed and the archive did fail, and reporting only one would
misstate what happened."
```

---

### Task 7: The Config tab card

**Files:**
- Modify: `frontend/partials/tab-config.html:271-297`
- Modify: `frontend/app.js:257-260` (state), `frontend/app.js:955-971` (load and save)

- [ ] **Step 1: Add the state**

In `frontend/app.js`, beside `appTimezone` / `timezoneDraft`:

```javascript
    boDownloadDir: '',
    boDownloadDirDraft: '',
    boDownloadDirSaving: false,
```

- [ ] **Step 2: Load it with the other settings**

In the same function that sets `this.appTimezone = resp.timezone || 'UTC'`, add:

```javascript
        this.boDownloadDir = resp.bo_download_dir || '';
        this.boDownloadDirDraft = this.boDownloadDir;
```

- [ ] **Step 3: Add the save function**

Next to `saveTimezoneSetting`:

```javascript
    async saveBoDownloadDirSetting() {
      this.boDownloadDirSaving = true;
      try {
        const resp = await api('PUT', '/api/settings', { bo_download_dir: this.boDownloadDirDraft });
        this.boDownloadDir = resp.bo_download_dir || '';
        this.toast('success', 'Download directory updated',
          this.boDownloadDir
            ? `SAP BO downloads will also be saved to ${this.boDownloadDir}`
            : 'SAP BO downloads will go to the browser only');
      } catch (e) {
        this.toast('error', 'Failed to update download directory', e.message || '');
      } finally {
        this.boDownloadDirSaving = false;
      }
    },
```

- [ ] **Step 4: Add the card**

In `frontend/partials/tab-config.html`, directly after the closing `</div>` of the
Timezone card:

```html
  <!-- SAP BO – server-side copy of downloads -->
  <div class="card mt-4">
    <button type="button" class="card-toggle" @click="boDownloadDirOpen = !boDownloadDirOpen" :aria-expanded="boDownloadDirOpen.toString()">
      <div class="font-semibold text-slate-700">💾 SAP BO — download directory</div>
      <span class="text-muted text-sm" x-text="boDownloadDirOpen ? '▲ collapse' : '▼ expand'"></span>
    </button>
    <template x-if="boDownloadDirOpen">
      <div class="mt-3 space-y-3">
        <p class="text-muted text-sm">When set, every SAP BO report downloaded from the Adapters tab is also written here on the server, with a timestamp in the filename so nothing is ever overwritten. Files are never deleted. Leave empty to keep downloads browser-only. The path must already exist on the machine running this app.</p>
        <template x-if="!activeTokenIsAdmin">
          <div class="text-sm">Current: <span class="font-medium" x-text="boDownloadDir || '(browser only)'"></span> <span class="text-muted text-xs">(administrator access required to change)</span></div>
        </template>
        <template x-if="activeTokenIsAdmin">
          <div class="flex items-center gap-2">
            <input type="text" x-model="boDownloadDirDraft" placeholder="/var/reports/sapbo" class="field-input flex-1" aria-label="bodownloaddirdraft">
            <button @click="saveBoDownloadDirSetting()" :disabled="boDownloadDirSaving || boDownloadDirDraft === boDownloadDir" class="btn-primary btn-sm">
              <span x-show="!boDownloadDirSaving">Save</span>
              <span x-show="boDownloadDirSaving">Saving…</span>
            </button>
          </div>
        </template>
      </div>
    </template>
  </div>
```

- [ ] **Step 5: Add the open/closed state**

In `frontend/app.js`, beside `timezoneOpen: false`:

```javascript
    boDownloadDirOpen: false,
```

- [ ] **Step 6: Verify in the browser**

Load the Config tab, expand the card, save a valid path (success toast), save a
nonsense path like `not/absolute` (red toast quoting the 422 message), save an empty
value (success toast saying browser-only).

- [ ] **Step 7: Commit**

```bash
git add frontend/app.js frontend/partials/tab-config.html
git commit -m "feat(frontend): Config card for the SAP BO download directory

Sits beside Timezone and follows its admin-gated pattern. The copy states the
two properties an operator needs to know before pointing this at a shared
drive: filenames are timestamped so nothing is overwritten, and files are never
deleted."
```

---

### Task 8: Prove it end to end

**Files:**
- Modify: `tests/integration/test_sapbo_ui_download_flow.py`

This drives the real FastAPI route, the real `AdapterService`, the real
`BORestClient` and the real `bo_archive` against the mock BO server. It is the test
that proves the feature.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_sapbo_ui_download_flow.py`:

```python
def test_ui_download_also_writes_a_server_copy(api, tmp_path):
    """The whole feature, through the endpoint the browser calls: the response
    still carries the workbook AND the file is on disk under a timestamped
    name."""
    from urllib.parse import unquote
    from pathlib import Path

    client, config_id = api

    settings = client.put("/api/settings", json={"bo_download_dir": str(tmp_path)})
    assert settings.status_code == 200

    download = client.post(
        f"/api/adapters/sap-bo/documents/1003/reports/rpt-daily-sales/download"
        f"?config_id={config_id}",
        json={"format": "xlsx", "parameters": [
            {"id": 0, "type": "DateTime", "value": "2026-06-03"},
            {"id": 1, "type": "String", "value": "ASX"},
        ]},
    )

    assert download.status_code == 200
    assert download.content.startswith(b"PK")

    written = list(tmp_path.glob("report_1003_rpt-daily-sales_*.xlsx"))
    assert len(written) == 1
    assert written[0].read_bytes() == download.content
    assert unquote(download.headers["x-saved-path"]) == str(written[0])

    # The archived copy holds the answered day's data, not just any workbook.
    sheet = _sheet_text(written[0].read_bytes())
    assert "D400" in sheet and "A100" not in sheet


def test_ui_download_survives_an_unwritable_directory(api, tmp_path):
    """A directory that disappears after it was configured must not cost the
    user the download — only the archive."""
    from urllib.parse import unquote

    client, config_id = api

    target = tmp_path / "share"
    target.mkdir()
    assert client.put(
        "/api/settings", json={"bo_download_dir": str(target)}).status_code == 200
    target.rmdir()          # the share went away after it was configured

    download = client.post(
        f"/api/adapters/sap-bo/documents/1003/reports/rpt-daily-sales/download"
        f"?config_id={config_id}",
        json={"format": "xlsx", "parameters": [
            {"id": 0, "type": "DateTime", "value": "2026-06-03"},
            {"id": 1, "type": "String", "value": "ASX"},
        ]},
    )

    assert download.status_code == 200
    assert download.content.startswith(b"PK")
    assert unquote(download.headers["x-save-error"])
    assert "x-saved-path" not in download.headers
```

The `api` fixture currently calls `TokenRepository(db).create("test")`, which is
**not** an admin token, and `PUT /api/settings` carries
`dependencies=[Depends(require_admin)]`. Change that one line in the fixture:

```python
        raw, _ = TokenRepository(db).create("test", is_admin=True)
```

The existing tests in this file do not check authorisation level, so widening the
fixture's token does not weaken any of them.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/integration/test_sapbo_ui_download_flow.py -q`
Expected: FAIL — the glob finds no file

- [ ] **Step 3: Confirm no implementation is needed**

Tasks 1–5 already implement this. If these tests fail for any reason other than a
fixture/token problem, the failure is real — fix the code, not the test.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/unit tests/integration/test_sapbo_ui_download_flow.py tests/integration/test_sapbo_mock_pagination.py -q`
Expected: all pass, no failures. Baseline before this plan was `1794 passed, 2 skipped`.

Verify the runner is live rather than serving a cached summary — run
`python -m pytest tests/unit/test_bo_archive.py -q` and confirm the count matches
that file alone.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_sapbo_ui_download_flow.py
git commit -m "test(sap_bo): the server copy, through the route the browser calls

Drives the real route, service, client and archive module against the mock BO
server: the response still carries the workbook, the timestamped file is on
disk, and its bytes match. The second test deletes the configured directory
after it was validated — the share-went-away case — and pins that only the
archive fails, never the download."
```

---

### Task 9: Merge

- [ ] **Step 1: Confirm the branch is clean and green**

```bash
git status --short --untracked-files=no
python -m pytest tests/unit tests/integration/test_sapbo_ui_download_flow.py -q
```

Expected: no tracked changes, all tests pass.

- [ ] **Step 2: Merge to master and push**

```bash
git checkout master
git merge --no-ff feat/bo-download-server-copy -m "Merge branch 'feat/bo-download-server-copy'"
git push origin master
```
