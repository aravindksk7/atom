# SMB/UNC File Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `smb` location kind (Windows UNC/SMB file shares, connected via `net use`) alongside `local`/`s3`/`sftp` for `file_transfer`, `file_watcher`, and multi-file reconciliation.

**Architecture:** A new `connect_smb_share`/`SmbSession` pair in `api/services/multi_file_remote.py` establishes and tears down an authenticated `net use` connection, serialized per host via a process-wide lock (Windows allows only one credential per server at a time). Once connected, a UNC path behaves like any other filesystem path, so discovery reuses `discover_local_files` directly and `file_transfer`'s new `SmbEndpoint` reuses `LocalEndpoint`'s path-safety logic (extracted into a shared `_FilesystemEndpoint` base). `smb` joins the existing `local`/`s3`/`sftp`/`scp` enumeration in every place those kinds are listed.

**Tech Stack:** Python (FastAPI, Pydantic, SQLAlchemy), Windows `net use` via `subprocess`, existing `RemoteFileSourceSession`/`FileServerProfile`/`file_transfer.py` machinery, Alpine.js frontend, pytest.

**Spec:** `docs/superpowers/specs/2026-09-22-smb-winscp-file-transfer-design.md` (§§1-3, 5-8; §4 WinSCP mechanics is a separate plan)

**Test commands.** Use raw `python -m pytest` prefixed with `rtk proxy` (a stale rtk cache once masked failures). For Playwright use `node node_modules/@playwright/test/cli.js test ...`, prefixed with `rtk proxy` if output looks mangled. Run `git` through the Bash tool from `c:/atom` (not inside a worktree). This repo mixes CRLF and LF files; before editing an existing file, check its line endings with a Python byte count and preserve them.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `api/schemas.py` | modify | `FileServerProfileCreate`/`Update` kind Literal gains `"smb"`; file_watcher's kind tuple gains `"smb"` |
| `api/routes/file_servers.py` | modify | Test Connection gains an `smb` branch (connects to `\\host\IPC$`, disconnects) |
| `etl_framework/runner/job_validation.py` | modify | file_watcher's kind tuple gains `"smb"` (mirror of schemas.py) |
| `etl_framework/reconciliation/file_mapping.py` | modify | new `parse_unc_root` helper; `_parse_file_source`'s kind set gains `"smb"` |
| `etl_framework/reconciliation/file_transfer_spec.py` | modify | `LOCATION_KINDS`/`REMOTE_KINDS` gain `"smb"` |
| `api/services/multi_file_remote.py` | modify | `SmbConnectError`, `SmbSession`, `_net_use`/`_net_use_delete`/`_smb_host_lock`, `connect_smb_share`; wired into `_client_for`, `location_key_for`, `discover`, `read_file`, `_read_bytes` |
| `api/services/file_transfer.py` | modify | extract `_FilesystemEndpoint` base (no behavior change to `LocalEndpoint`); new `SmbEndpoint`; `is_transport_error` recognizes `SmbConnectError`; `build_endpoint` gains an `smb` branch |
| `frontend/partials/tab-file-servers.html` | modify | File Servers tab: `smb` kind option + form fields |
| `frontend/partials/tab-launch.html` | modify | 5 location-kind `<select>`s (file_watcher, file_transfer source/destination, multi-file source/target) gain `smb`; their file-server dropdown filters route `smb` to `smb` profiles |
| `frontend/help-content.js` | modify | one line added to the job-type decision table and the file_watcher/file_transfer scenarios |
| `frontend/index.html` | regenerate | output of `npm run build:html` |
| `tests/unit/test_file_mapping.py` | modify | `parse_unc_root` and `_parse_file_source` tests |
| `tests/unit/test_file_transfer_spec.py` | modify | `LOCATION_KINDS`/`REMOTE_KINDS` tests |
| `tests/unit/test_file_transfer_validation.py` | modify | file_watcher `smb` validation tests |
| `tests/unit/test_multi_file_remote.py` | modify | `connect_smb_share`, `SmbSession`, locking, dispatch tests |
| `tests/unit/test_file_transfer.py` | modify | `_FilesystemEndpoint` refactor regression + `SmbEndpoint` tests |
| `tests/unit/test_file_servers_api.py` | modify | Test Connection `smb` branch tests |
| `tests/e2e/56-smb-file-transfer.spec.ts` | create | File Servers CRUD for `smb` + job editor round trip |

`frontend/features/launch.js` and `frontend/features/file-servers.js` need **no changes** — their kind-handling (`kind !== 'local'` credentials gating, params building, profile CRUD) is already generic over any kind string; confirmed by reading both files during planning.

No database migration is needed: `smb` reuses the existing `host`/`username`/`password` columns and `FileServerProfile.kind` is a plain unconstrained `String(10)`, not a DB-level enum.

---

### Task 1: `FileServerProfile` accepts `kind="smb"`, and Test Connection can verify it

**Files:**
- Modify: `api/schemas.py` (`FileServerProfileCreate`, `FileServerProfileUpdate`)
- Modify: `api/routes/file_servers.py`
- Test: `tests/unit/test_file_servers_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_servers_api.py` (use the file's existing `client` fixture — check the top of the file for its exact name and import it the same way the existing tests do):

```python
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

    def _raise(*args, **kwargs):
        from api.services.multi_file_remote import SmbConnectError
        raise SmbConnectError("net use \\\\fileserver02\\IPC$ failed: System error 53 has occurred.")

    monkeypatch.setattr("api.services.multi_file_remote._net_use", _raise)
    monkeypatch.setattr("api.services.multi_file_remote._net_use_delete", lambda *a, **k: None)

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "System error 53" in body["message"]


def test_test_connection_smb_succeeds_and_always_disconnects(client, monkeypatch):
    created = client.post("/api/file-servers", json={
        "name": "vendor-share-3", "kind": "smb", "host": "fileserver03",
        "username": "svc", "password": "s3cret",
    }).json()

    calls = []
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: calls.append(("use", resource)))
    monkeypatch.setattr("api.services.multi_file_remote._net_use_delete", lambda resource: calls.append(("delete", resource)))

    resp = client.post(f"/api/file-servers/{created['id']}/test")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "presented_fingerprint": None, "pinned_fingerprint": None, "message": None}
    assert calls == [("use", "\\\\fileserver03\\IPC$"), ("delete", "\\\\fileserver03\\IPC$")]
```

If `FileServerTestResult`'s field set differs from the literal dict above (check `api/schemas.py`'s `FileServerTestResult` class), adjust the third test's expected dict to match the model's actual optional fields with their default `None`s — don't guess, read the class first.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/unit/test_file_servers_api.py -k smb -v`
Expected: FAIL. The create test fails with a `422` (kind `"smb"` not in the `Literal`). The two Test Connection tests fail because `api.services.multi_file_remote` has no `_net_use`/`_net_use_delete`/`SmbConnectError` to patch, or because the route has no `smb` branch and falls into the `s3` `try/except Exception` block, raising an unrelated error.

- [ ] **Step 3: Add `"smb"` to the profile schemas**

In `api/schemas.py`, change both occurrences of:
```python
    kind: Literal["sftp", "scp", "s3"]
```
and
```python
    kind: Literal["sftp", "scp", "s3"] | None = None
```
(one in `FileServerProfileCreate`, one in `FileServerProfileUpdate`) to:
```python
    kind: Literal["sftp", "scp", "s3", "smb"]
```
and
```python
    kind: Literal["sftp", "scp", "s3", "smb"] | None = None
```
respectively.

- [ ] **Step 4: Add `_net_use`/`_net_use_delete`/`SmbConnectError` stubs so Step 3's tests have something to monkeypatch**

This step only adds the names Task 5 will implement for real, so this task's tests can run in isolation. In `api/services/multi_file_remote.py`, add near the top of the file, after the existing imports:

```python
class SmbConnectError(RuntimeError):
    """Raised when connecting to a UNC share fails: bad credentials, an
    unreachable host, a missing share, or (on a non-Windows atom server) the
    platform itself. Recognised by ``file_transfer.is_transport_error``."""


def _net_use(resource: str, username: str | None, password: str | None) -> None:
    """Authenticate to a UNC resource (``\\\\server\\share``) via Windows'
    built-in ``net use``. Raises ``SmbConnectError`` on any failure, its
    message scrubbed of ``password``. Implemented fully in Task 5; this
    placeholder lets Task 1's Test Connection route and tests exist first."""
    raise NotImplementedError


def _net_use_delete(resource: str) -> None:
    """Best-effort ``net use ... /delete`` -- never raises."""
    raise NotImplementedError
```

- [ ] **Step 5: Add the `smb` branch to Test Connection**

In `api/routes/file_servers.py`, add `import os` to the top-of-file imports (alongside the existing `import hashlib`). Then, in `test_file_server`, insert a new branch before the final `# s3` comment block (i.e. right after the `if profile.kind in ("sftp", "scp"):` block's closing, before `# s3`):

```python
    if profile.kind == "smb":
        if os.name != "nt":
            return FileServerTestResult(status="error", message="SMB transfers require the atom server to run on Windows")
        if not profile.host:
            return FileServerTestResult(status="error", message="SMB profile requires a host")
        # _net_use/_net_use_delete/_smb_host_lock are module-private helpers
        # shared between this route and multi_file_remote's real connect path
        # (same convention as _load_sftp_private_key above).
        from api.services.multi_file_remote import SmbConnectError, _net_use, _net_use_delete, _smb_host_lock

        resource = f"\\\\{profile.host}\\IPC$"
        lock = _smb_host_lock(profile.host)
        lock.acquire()
        try:
            _net_use(resource, profile.username, profile.password)
        except SmbConnectError as exc:
            return FileServerTestResult(status="error", message=str(exc))
        finally:
            _net_use_delete(resource)
            lock.release()
        return FileServerTestResult(status="ok")

```

`_smb_host_lock` doesn't exist yet either — add one more placeholder next to the two above in `multi_file_remote.py`:

```python
def _smb_host_lock(host: str):
    raise NotImplementedError
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/unit/test_file_servers_api.py -k smb -v`
Expected: PASS (the two Test Connection tests monkeypatch the placeholders directly, so `NotImplementedError` is never reached).

- [ ] **Step 7: Run the existing file-servers suite for regressions**

Run: `rtk proxy python -m pytest tests/unit/test_file_servers_api.py -q`
Expected: all PASS (sftp/scp/s3 behavior unchanged).

- [ ] **Step 8: Commit**

```bash
cd /c/atom
git add api/schemas.py api/routes/file_servers.py api/services/multi_file_remote.py tests/unit/test_file_servers_api.py
git commit -m "feat: accept smb file server profiles and test their connection

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `parse_unc_root` and multi-file reconciliation accept `smb`

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py`
- Test: `tests/unit/test_file_mapping.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_mapping.py`:

```python
def test_parse_unc_root_splits_server_and_share() -> None:
    from etl_framework.reconciliation.file_mapping import parse_unc_root

    assert parse_unc_root(r"\\fileserver01\vendor") == ("fileserver01", "vendor")
    assert parse_unc_root(r"\\fileserver01\vendor\inbound\daily") == ("fileserver01", "vendor")


@pytest.mark.parametrize("bad_root", ["", "/vendor/inbound", r"\\fileserver01", r"\\", "C:\\local\\path"])
def test_parse_unc_root_rejects_non_unc_paths(bad_root: str) -> None:
    from etl_framework.reconciliation.file_mapping import parse_unc_root

    with pytest.raises(ValueError, match="UNC path"):
        parse_unc_root(bad_root)


def test_parse_file_source_accepts_smb_kind() -> None:
    from etl_framework.reconciliation.file_mapping import _parse_file_source

    spec = _parse_file_source(
        {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "sales_{region}.csv", "credentials_ref": "vendor-share"},
        "source",
    )
    assert spec.kind == "smb"
    assert spec.root == r"\\fileserver01\vendor\inbound"
    assert spec.credentials_ref == "vendor-share"


def test_parse_file_source_rejects_unknown_kind_lists_smb_in_message() -> None:
    from etl_framework.reconciliation.file_mapping import _parse_file_source

    with pytest.raises(ValueError, match="smb"):
        _parse_file_source({"kind": "ftp", "root": "x", "pattern": "*"}, "source")
```

Check the top of `tests/unit/test_file_mapping.py` for whether `pytest` is already imported for `@pytest.mark.parametrize` — it almost certainly is (other tests in the file use `pytest.raises`); if not, add `import pytest`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/unit/test_file_mapping.py -k "unc_root or parse_file_source" -v`
Expected: FAIL. `parse_unc_root` doesn't exist (`ImportError`/`AttributeError`), and `_parse_file_source` rejects `"smb"` with the current message.

- [ ] **Step 3: Implement `parse_unc_root`**

In `etl_framework/reconciliation/file_mapping.py`, add this function directly above `_parse_file_source`:

```python
def parse_unc_root(root: str) -> tuple[str, str]:
    """Split a UNC path (``\\\\server\\share`` or ``\\\\server\\share\\sub\\path``)
    into its server and share segments. Raises ``ValueError`` for anything
    that isn't a well-formed UNC path with at least a server and a share --
    used both to validate an ``smb`` location's root and to build the
    ``net use \\\\server\\share`` resource for it."""
    if not root.startswith("\\\\"):
        raise ValueError(f"SMB root must be a UNC path (\\\\server\\share\\...): {root!r}")
    parts = [part for part in root[2:].split("\\") if part]
    if len(parts) < 2:
        raise ValueError(f"SMB root must include both a server and a share: {root!r}")
    return parts[0], parts[1]
```

- [ ] **Step 4: Add `smb` to `_parse_file_source`'s allowed kinds**

Change:
```python
    if kind not in {"local", "s3", "sftp"}:
        raise ValueError(
            f"file_mapping.{side}.kind '{kind}' is not supported yet; "
            "supported kinds are 'local', 's3', and 'sftp'"
        )
```
to:
```python
    if kind not in {"local", "s3", "sftp", "smb"}:
        raise ValueError(
            f"file_mapping.{side}.kind '{kind}' is not supported yet; "
            "supported kinds are 'local', 's3', 'sftp', and 'smb'"
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/unit/test_file_mapping.py -k "unc_root or parse_file_source" -v`
Expected: all PASS.

- [ ] **Step 6: Run the whole file for regressions**

Run: `rtk proxy python -m pytest tests/unit/test_file_mapping.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
cd /c/atom
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_file_mapping.py
git commit -m "feat: add parse_unc_root and accept smb in multi-file reconciliation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: `file_transfer` accepts `smb` as a location kind

**Files:**
- Modify: `etl_framework/reconciliation/file_transfer_spec.py`
- Test: `tests/unit/test_file_transfer_spec.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_transfer_spec.py`:

```python
def test_smb_source_and_destination_are_valid_with_credentials_ref():
    params = {
        "source": {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "sales_{region}.csv", "credentials_ref": "vendor-share"},
        "destination": {"kind": "smb", "root": r"\\fileserver01\staging\sales", "credentials_ref": "vendor-share"},
    }
    assert file_transfer_param_errors(params) == []
    spec = parse_file_transfer_params(params)
    assert spec.source.kind == "smb"
    assert spec.destination.kind == "smb"


def test_smb_requires_credentials_ref():
    params = {
        "source": {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "*.csv"},
        "destination": {"kind": "local", "root": "/tmp/out"},
    }
    errors = file_transfer_param_errors(params)
    assert any(field == "params.source.credentials_ref" for field, _ in errors)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/unit/test_file_transfer_spec.py -k smb -v`
Expected: FAIL. `kind not in LOCATION_KINDS` rejects `"smb"`, and (for the first test) `credentials_ref` is required but `"smb"` isn't yet in `REMOTE_KINDS`, but the kind-rejection error fires first either way.

- [ ] **Step 3: Add `smb` to the kind tuples**

In `etl_framework/reconciliation/file_transfer_spec.py`, change:
```python
LOCATION_KINDS = ("local", "s3", "sftp", "scp")
REMOTE_KINDS = ("s3", "sftp", "scp")
```
to:
```python
LOCATION_KINDS = ("local", "s3", "sftp", "scp", "smb")
REMOTE_KINDS = ("s3", "sftp", "scp", "smb")
```
and update the kind-mismatch message text:
```python
            errors.append((f"params.{key}.kind", f"file_transfer {key}.kind must be 'local', 's3', 'sftp', or 'scp'"))
```
to:
```python
            errors.append((f"params.{key}.kind", f"file_transfer {key}.kind must be 'local', 's3', 'sftp', 'scp', or 'smb'"))
```

`_location_spec`'s `kind="sftp" if kind == "scp" else kind` line needs no change -- `smb` passes through unnormalized, same as `s3`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/unit/test_file_transfer_spec.py -k smb -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole file for regressions**

Run: `rtk proxy python -m pytest tests/unit/test_file_transfer_spec.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /c/atom
git add etl_framework/reconciliation/file_transfer_spec.py tests/unit/test_file_transfer_spec.py
git commit -m "feat: accept smb as a file_transfer location kind

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: `file_watcher` accepts `smb`

**Files:**
- Modify: `api/schemas.py`
- Modify: `etl_framework/runner/job_validation.py`
- Test: `tests/unit/test_file_transfer_validation.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_transfer_validation.py` (check its imports at the top -- it should already import `JobDefinition` and `validate_job_definition` the same way as the rest of the file):

```python
def test_file_watcher_accepts_smb_location():
    job = JobDefinition(
        name="watch_vendor_share", job_type="file_watcher",
        params={
            "location": {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "SALES_*.csv", "credentials_ref": "vendor-share"},
            "max_tries": 5,
        },
    )
    assert job.job_type == "file_watcher"
    assert validate_job_definition(job) == []


def test_file_watcher_smb_requires_credentials_ref():
    with pytest.raises(ValueError, match="credentials_ref"):
        JobDefinition(
            name="watch_vendor_share_2", job_type="file_watcher",
            params={
                "location": {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "SALES_*.csv"},
                "max_tries": 5,
            },
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/unit/test_file_transfer_validation.py -k smb -v`
Expected: FAIL with `ValueError: file_watcher location.kind must be 'local', 's3', 'sftp', or 'scp'` for the first test.

- [ ] **Step 3: Add `smb` in `api/schemas.py`**

Change (two occurrences, in `validate_reconciliation_contract`'s `file_watcher` branch):
```python
            if kind not in ("local", "s3", "sftp", "scp"):
                raise ValueError("file_watcher location.kind must be 'local', 's3', 'sftp', or 'scp'")
            if not location.get("root") or not location.get("pattern"):
                raise ValueError("file_watcher location requires 'root' and 'pattern'")
            if kind in ("s3", "sftp", "scp") and not location.get("credentials_ref"):
```
to:
```python
            if kind not in ("local", "s3", "sftp", "scp", "smb"):
                raise ValueError("file_watcher location.kind must be 'local', 's3', 'sftp', 'scp', or 'smb'")
            if not location.get("root") or not location.get("pattern"):
                raise ValueError("file_watcher location requires 'root' and 'pattern'")
            if kind in ("s3", "sftp", "scp", "smb") and not location.get("credentials_ref"):
```

- [ ] **Step 4: Add `smb` in `etl_framework/runner/job_validation.py`**

Change:
```python
    kind = location.get("kind")
    if kind not in ("local", "s3", "sftp", "scp"):
        issues.append(ValidationIssue(
            "params.location.kind", "file_watcher location.kind must be 'local', 's3', 'sftp', or 'scp'",
        ))
    if not location.get("root"):
        issues.append(ValidationIssue("params.location.root", "file_watcher location requires 'root'"))
    if not location.get("pattern"):
        issues.append(ValidationIssue("params.location.pattern", "file_watcher location requires 'pattern'"))
    if kind in ("s3", "sftp", "scp") and not location.get("credentials_ref"):
```
to:
```python
    kind = location.get("kind")
    if kind not in ("local", "s3", "sftp", "scp", "smb"):
        issues.append(ValidationIssue(
            "params.location.kind", "file_watcher location.kind must be 'local', 's3', 'sftp', 'scp', or 'smb'",
        ))
    if not location.get("root"):
        issues.append(ValidationIssue("params.location.root", "file_watcher location requires 'root'"))
    if not location.get("pattern"):
        issues.append(ValidationIssue("params.location.pattern", "file_watcher location requires 'pattern'"))
    if kind in ("s3", "sftp", "scp", "smb") and not location.get("credentials_ref"):
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/unit/test_file_transfer_validation.py -k smb -v`
Expected: all PASS.

- [ ] **Step 6: Run the neighbouring suites for regressions**

Run: `rtk proxy python -m pytest tests/unit/test_file_transfer_validation.py tests/unit/test_job_validation.py -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
cd /c/atom
git add api/schemas.py etl_framework/runner/job_validation.py tests/unit/test_file_transfer_validation.py
git commit -m "feat: accept smb as a file_watcher location kind

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `connect_smb_share` and `RemoteFileSourceSession` wiring

**Files:**
- Modify: `api/services/multi_file_remote.py`
- Test: `tests/unit/test_multi_file_remote.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_multi_file_remote.py`. First check the top of the file for its existing `db` fixture, `_sftp_profile`-style helper, and how it monkeypatches `resolve_file_server_profile`/builds a `FileServerProfile` row via `FileServerProfileRepository` -- mirror that pattern exactly rather than reinventing it. Then append:

```python
def _smb_profile(name="vendor-share", host="fileserver01", username="svc", password="s3cret"):
    return {
        "name": name, "kind": "smb", "host": host, "port": 22,
        "username": username, "password": password,
    }


def test_parse_unc_root_used_by_connect_smb_share_rejects_bad_root(db, monkeypatch):
    from api.services.multi_file_remote import SmbConnectError, connect_smb_share
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile())
    spec = FileSourceSpec(kind="smb", root="not-a-unc-path", pattern="*.csv", credentials_ref="vendor-share")
    monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db_, spec_: FileServerProfileRepository(db).get_decrypted_by_name("vendor-share"))

    with pytest.raises(SmbConnectError, match="UNC path"):
        connect_smb_share(FileServerProfileRepository(db).get_decrypted_by_name("vendor-share"), spec)


def test_connect_smb_share_raises_when_profile_host_does_not_match_root(db):
    from api.services.multi_file_remote import SmbConnectError, connect_smb_share
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile(host="fileserver01"))
    profile = FileServerProfileRepository(db).get_decrypted_by_name("vendor-share")
    spec = FileSourceSpec(kind="smb", root=r"\\wrong-server\share\sub", pattern="*.csv", credentials_ref="vendor-share")

    with pytest.raises(SmbConnectError, match="does not match"):
        connect_smb_share(profile, spec)


def test_connect_smb_share_requires_a_password(db):
    from api.services.multi_file_remote import SmbConnectError, connect_smb_share
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile(password=""))
    profile = FileServerProfileRepository(db).get_decrypted_by_name("vendor-share")
    spec = FileSourceSpec(kind="smb", root=r"\\fileserver01\share\sub", pattern="*.csv", credentials_ref="vendor-share")

    with pytest.raises(SmbConnectError, match="password"):
        connect_smb_share(profile, spec)


def test_connect_smb_share_calls_net_use_with_resource_password_and_user(db, monkeypatch):
    from api.services.multi_file_remote import connect_smb_share
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile())
    profile = FileServerProfileRepository(db).get_decrypted_by_name("vendor-share")
    spec = FileSourceSpec(kind="smb", root=r"\\fileserver01\share\sub", pattern="*.csv", credentials_ref="vendor-share")

    calls = []
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: calls.append((resource, u, p)))

    session = connect_smb_share(profile, spec)
    assert calls == [("\\\\fileserver01\\share", "svc", "s3cret")]
    assert session.resource == "\\\\fileserver01\\share"


def test_connect_smb_share_releases_host_lock_on_net_use_failure(db, monkeypatch):
    from api.services.multi_file_remote import SmbConnectError, _smb_host_lock, connect_smb_share
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile())
    profile = FileServerProfileRepository(db).get_decrypted_by_name("vendor-share")
    spec = FileSourceSpec(kind="smb", root=r"\\fileserver01\share\sub", pattern="*.csv", credentials_ref="vendor-share")

    def _fail(resource, u, p):
        raise SmbConnectError("boom")

    monkeypatch.setattr("api.services.multi_file_remote._net_use", _fail)
    with pytest.raises(SmbConnectError):
        connect_smb_share(profile, spec)

    # A second connect attempt to the same host must not deadlock -- proves
    # the lock was released after the failed attempt.
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    session = connect_smb_share(profile, spec)
    assert session.resource == "\\\\fileserver01\\share"


def test_smb_session_close_deletes_connection_and_releases_lock(db, monkeypatch):
    from api.services.multi_file_remote import connect_smb_share
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile())
    profile = FileServerProfileRepository(db).get_decrypted_by_name("vendor-share")
    spec = FileSourceSpec(kind="smb", root=r"\\fileserver01\share\sub", pattern="*.csv", credentials_ref="vendor-share")

    calls = []
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    monkeypatch.setattr("api.services.multi_file_remote._net_use_delete", lambda resource: calls.append(resource))

    session = connect_smb_share(profile, spec)
    session.close()
    assert calls == ["\\\\fileserver01\\share"]

    # The lock is free again: a second connect must not block.
    session2 = connect_smb_share(profile, spec)
    assert session2.resource == "\\\\fileserver01\\share"


def test_client_for_smb_caches_the_session(db, monkeypatch):
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile())
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    session_local = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="smb", root=r"\\fileserver01\share\sub", pattern="*.csv", credentials_ref="vendor-share")

    first = session_local.client_for(spec)
    second = session_local.client_for(spec)
    assert first is second


def test_location_key_for_smb_keys_on_server_and_share(db, monkeypatch):
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile(name="profile-a", host="fileserver01"))
    FileServerProfileRepository(db).create(_smb_profile(name="profile-b", host="fileserver01"))
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    session_local = RemoteFileSourceSession(db)
    spec_a = FileSourceSpec(kind="smb", root=r"\\fileserver01\share\sub", pattern="*.csv", credentials_ref="profile-a")
    spec_b = FileSourceSpec(kind="smb", root=r"\\fileserver01\share\other", pattern="*.csv", credentials_ref="profile-b")

    assert session_local.location_key_for(spec_a) == session_local.location_key_for(spec_b)
    assert session_local.location_key_for(spec_a) == ("smb", "fileserver01", "share")


def test_discover_smb_reuses_discover_local_files(db, monkeypatch, tmp_path):
    from etl_framework.repository.repository import FileServerProfileRepository

    # A real local directory stands in for the far side of the UNC path --
    # once "connected", smb discovery is just discover_local_files on the
    # given root, with no resolve_allowed_path call. parse_unc_root is
    # patched so the connect step accepts this plain tmp_path as if it were
    # the UNC root "\\fileserver01\share"; discover_local_files still walks
    # the real tmp_path directory underneath.
    (tmp_path / "sales_east.csv").write_text("id\n1\n", encoding="utf-8")
    FileServerProfileRepository(db).create(_smb_profile())
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    monkeypatch.setattr("api.services.multi_file_remote.parse_unc_root", lambda root: ("fileserver01", "share"))
    session_local = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="smb", root=str(tmp_path), pattern="sales_{region}.csv", credentials_ref="vendor-share")

    discovered = session_local.discover(spec)
    assert [f.file_name for f in discovered] == ["sales_east.csv"]


def test_read_file_smb_reads_without_allowlist(db, monkeypatch, tmp_path):
    from etl_framework.repository.repository import FileServerProfileRepository

    (tmp_path / "sales_east.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    FileServerProfileRepository(db).create(_smb_profile())
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    monkeypatch.setattr("api.services.multi_file_remote.parse_unc_root", lambda root: ("fileserver01", "share"))
    session_local = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="smb", root=str(tmp_path), pattern="sales_{region}.csv", credentials_ref="vendor-share")

    discovered = session_local.discover(spec)
    df = session_local.read_file(discovered[0], spec)
    assert list(df.columns) == ["id", "value"]


def test_read_text_smb(db, monkeypatch, tmp_path):
    from etl_framework.repository.repository import FileServerProfileRepository

    (tmp_path / "DONE.flag").write_bytes(b"STATUS=COMPLETE\n")
    FileServerProfileRepository(db).create(_smb_profile())
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    monkeypatch.setattr("api.services.multi_file_remote.parse_unc_root", lambda root: ("fileserver01", "share"))
    session_local = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="smb", root=str(tmp_path), pattern="DONE.flag", credentials_ref="vendor-share")

    discovered = session_local.discover(spec)
    assert session_local.read_text(discovered[0], spec) == "STATUS=COMPLETE\n"


def test_close_releases_smb_client(db, monkeypatch, tmp_path):
    from etl_framework.repository.repository import FileServerProfileRepository

    FileServerProfileRepository(db).create(_smb_profile())
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    delete_calls = []
    monkeypatch.setattr("api.services.multi_file_remote._net_use_delete", lambda resource: delete_calls.append(resource))
    session_local = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="smb", root=str(tmp_path), pattern="*.csv", credentials_ref="vendor-share")
    session_local.client_for(spec)

    session_local.close()
    assert delete_calls == ["\\\\fileserver01\\share"]
```

Note on why `test_discover_smb_reuses_discover_local_files`, `test_read_file_smb_reads_without_allowlist`, and `test_read_text_smb` (above) each monkeypatch `parse_unc_root` in addition to `_net_use`: their `spec.root` is `str(tmp_path)`, a plain local path, not a `\\server\share` UNC path. `discover()`'s `smb` branch (built in Step 3 below) calls `self._client_for(spec)` first, which parses `spec.root` via the real `parse_unc_root` and would reject a non-UNC `tmp_path`. Patching `parse_unc_root` to return a fixed `("fileserver01", "share")` regardless of input lets the connect step succeed while `discover_local_files` still walks the real `tmp_path` directory underneath for the actual file listing -- the two concerns (UNC-shape validation for connecting, and directory-walking for discovery) are decoupled in the test the same way they're decoupled in the two separate calls the implementation makes.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/unit/test_multi_file_remote.py -k smb -v`
Expected: FAIL. `connect_smb_share` doesn't exist yet (only the `NotImplementedError` placeholders from Task 1 exist for `_net_use`/`_net_use_delete`/`_smb_host_lock`), and `_client_for`/`discover`/`location_key_for` reject `spec.kind == "smb"`.

- [ ] **Step 3: Implement `connect_smb_share` and wire it in**

In `api/services/multi_file_remote.py`, replace the three `NotImplementedError` placeholders added in Task 1 with:

```python
import subprocess
import threading

_smb_host_locks: dict[str, threading.Lock] = {}
_smb_host_locks_guard = threading.Lock()


def _smb_host_lock(host: str) -> threading.Lock:
    """One lock per lower-cased host, shared by every ``smb`` connect for
    that server -- Windows allows only one authenticated identity per server
    at a time, so concurrent connects with different credentials must be
    serialized rather than racing ``net use``."""
    key = host.lower()
    with _smb_host_locks_guard:
        if key not in _smb_host_locks:
            _smb_host_locks[key] = threading.Lock()
        return _smb_host_locks[key]


def _scrub_smb_error(text: str, password: str | None) -> str:
    if password:
        text = text.replace(password, "***")
    return text


def _net_use(resource: str, username: str | None, password: str | None) -> None:
    """Authenticate to a UNC resource (``\\\\server\\share``) via Windows'
    built-in ``net use``. Raises ``SmbConnectError`` on any failure, its
    message scrubbed of ``password``."""
    if not password:
        raise SmbConnectError("SMB profile requires a password")
    args = ["net", "use", resource, password]
    if username:
        args.append("/user:" + username)
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except Exception as exc:
        raise SmbConnectError(_scrub_smb_error(str(exc), password)) from exc
    if result.returncode != 0:
        message = _scrub_smb_error((result.stderr or result.stdout or "").strip(), password)
        raise SmbConnectError(f"net use {resource} failed: {message}")


def _net_use_delete(resource: str) -> None:
    """Best-effort ``net use ... /delete`` -- never raises."""
    try:
        subprocess.run(["net", "use", resource, "/delete"], capture_output=True, text=True, timeout=30)
    except Exception:
        pass


class SmbSession:
    """A connected UNC resource, held for the lifetime of one
    ``RemoteFileSourceSession``. ``close()`` runs ``net use ... /delete`` and
    releases the per-host lock -- picked up automatically by
    ``close_remote_client``'s generic ``getattr(client, "close", None)``."""

    def __init__(self, resource: str, lock: "threading.Lock") -> None:
        self.resource = resource
        self._lock = lock

    def close(self) -> None:
        try:
            _net_use_delete(self.resource)
        finally:
            self._lock.release()


def connect_smb_share(profile: ResolvedFileServerProfile | None, spec: FileSourceSpec) -> SmbSession:
    if profile is None:
        raise ValueError(f"'{spec.kind}' source requires credentials_ref, but none was set")
    if os.name != "nt":
        raise SmbConnectError("SMB transfers require the atom server to run on Windows")
    try:
        server, share = parse_unc_root(spec.root)
    except ValueError as exc:
        raise SmbConnectError(str(exc)) from exc
    if profile.host and profile.host.lower() != server.lower():
        raise SmbConnectError(
            f"file_transfer source/destination root '\\\\{server}\\{share}' does not match "
            f"file server profile '{profile.name}''s host '{profile.host}'"
        )
    resource = f"\\\\{server}\\{share}"
    lock = _smb_host_lock(server)
    lock.acquire()
    try:
        _net_use(resource, profile.username, profile.password)
    except Exception:
        lock.release()
        raise
    return SmbSession(resource, lock)
```

Add `import os` alongside the existing `import hashlib` and `import io` at the top of the file (check it isn't already imported). Add `from etl_framework.reconciliation.file_mapping import parse_unc_root` to the existing `from etl_framework.reconciliation.file_mapping import (...)` block.

Then wire `smb` into `RemoteFileSourceSession`:

In `_client_for`, change:
```python
            if spec.kind == "s3":
                self._clients[key] = build_s3_client(profile, spec)
            elif spec.kind == "sftp":
                self._clients[key] = build_sftp_client(profile, spec)
            else:
                raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```
to:
```python
            if spec.kind == "s3":
                self._clients[key] = build_s3_client(profile, spec)
            elif spec.kind == "sftp":
                self._clients[key] = build_sftp_client(profile, spec)
            elif spec.kind == "smb":
                self._clients[key] = connect_smb_share(profile, spec)
            else:
                raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```

In `location_key_for`, change:
```python
        if spec.kind not in ("s3", "sftp"):
            return None
        self._client_for(spec)
        profile = self._profiles.get((spec.kind, spec.credentials_ref))
        if profile is None:
            return None
        if spec.kind == "s3":
            # Bucket names are treated as global per endpoint (true for AWS and
            # MinIO); tenant-scoped gateways such as Ceph RGW would need the
            # tenant in this key.
            return ("s3", (profile.endpoint_url or "").rstrip("/").lower())
        # The SSH username is part of the location: relative roots resolve
        # against each account's own home and chrooted accounts can both use
        # the same absolute path.
        return ("sftp", (profile.host or "").lower(), int(profile.port or 22), profile.username or "")
```
to:
```python
        if spec.kind not in ("s3", "sftp", "smb"):
            return None
        self._client_for(spec)
        profile = self._profiles.get((spec.kind, spec.credentials_ref))
        if profile is None:
            return None
        if spec.kind == "s3":
            # Bucket names are treated as global per endpoint (true for AWS and
            # MinIO); tenant-scoped gateways such as Ceph RGW would need the
            # tenant in this key.
            return ("s3", (profile.endpoint_url or "").rstrip("/").lower())
        if spec.kind == "smb":
            # A UNC root is always fully qualified (never account-relative
            # the way an SFTP root can be), so the account isn't part of the
            # location -- two profiles for the same server+share are the
            # same object regardless of which account each uses.
            server, share = parse_unc_root(spec.root)
            return ("smb", server.lower(), share.lower())
        # The SSH username is part of the location: relative roots resolve
        # against each account's own home and chrooted accounts can both use
        # the same absolute path.
        return ("sftp", (profile.host or "").lower(), int(profile.port or 22), profile.username or "")
```

In `discover`, change:
```python
        if spec.kind == "sftp":
            return discover_sftp_files(self._client_for(spec), spec.root, spec.pattern, recursive=recursive)
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```
(the first occurrence, inside `discover`) to:
```python
        if spec.kind == "sftp":
            return discover_sftp_files(self._client_for(spec), spec.root, spec.pattern, recursive=recursive)
        if spec.kind == "smb":
            self._client_for(spec)  # connects (net use); the UNC path is then a normal filesystem path
            return discover_local_files(Path(spec.root), spec.pattern, recursive=recursive)
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```

In `read_file`, change:
```python
        if spec.kind == "sftp":
            client = self._client_for(spec)
            with client.open(file.path, "rb") as fh:
                raw = fh.read()
            return _read_tabular_bytes(raw, Path(file.file_name).suffix.lower())
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```
(the occurrence inside `read_file`) to:
```python
        if spec.kind == "sftp":
            client = self._client_for(spec)
            with client.open(file.path, "rb") as fh:
                raw = fh.read()
            return _read_tabular_bytes(raw, Path(file.file_name).suffix.lower())
        if spec.kind == "smb":
            self._client_for(spec)
            with open(file.path, "rb") as fh:
                raw = fh.read()
            return _read_tabular_bytes(raw, Path(file.file_name).suffix.lower())
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```

In `_read_bytes`, change:
```python
        if spec.kind == "sftp":
            client = self._client_for(spec)
            with client.open(file.path, "rb") as fh:
                return fh.read(_CONTENT_MATCH_READ_LIMIT)
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```
(the occurrence inside `_read_bytes`) to:
```python
        if spec.kind == "sftp":
            client = self._client_for(spec)
            with client.open(file.path, "rb") as fh:
                return fh.read(_CONTENT_MATCH_READ_LIMIT)
        if spec.kind == "smb":
            self._client_for(spec)
            with open(file.path, "rb") as fh:
                return fh.read(_CONTENT_MATCH_READ_LIMIT)
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```

`close()` and `close_remote_client` need no changes: `SmbSession.close()` already exists, and `close_remote_client`'s generic `getattr(client, "close", None)` call already invokes it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/unit/test_multi_file_remote.py -k smb -v`
Expected: all PASS.

- [ ] **Step 5: Run the whole file plus the Test Connection tests from Task 1 for regressions**

Run: `rtk proxy python -m pytest tests/unit/test_multi_file_remote.py tests/unit/test_file_servers_api.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /c/atom
git add api/services/multi_file_remote.py tests/unit/test_multi_file_remote.py
git commit -m "feat: connect and discover smb shares in RemoteFileSourceSession

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `SmbEndpoint` in `file_transfer.py`

**Files:**
- Modify: `api/services/file_transfer.py`
- Test: `tests/unit/test_file_transfer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_transfer.py` (reuse the existing `allowed_dir` fixture the file already defines for `LocalEndpoint` tests):

```python
class _FakeSmbSession:
    def __init__(self, resource: str) -> None:
        self.resource = resource
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_local_endpoint_still_behaves_identically_after_the_shared_base_refactor(allowed_dir):
    # Regression guard for Task 6's _FilesystemEndpoint extraction: every
    # existing LocalEndpoint behavior (allowlist, path safety, atomic write,
    # identity) must be unchanged. The pre-existing test_local_endpoint_*
    # tests earlier in this file already cover this in detail; this test
    # just confirms LocalEndpoint is still importable and constructible the
    # same way after the refactor.
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    assert endpoint.kind == "local"
    assert endpoint.identity(str(allowed_dir / "a.csv"))[0] == "local"


def test_smb_endpoint_relative_of_and_destination_for(tmp_path):
    endpoint = ft.SmbEndpoint(str(tmp_path / "src"))
    (tmp_path / "src" / "sub").mkdir(parents=True)
    file = DiscoveredFile(path=str(tmp_path / "src" / "sub" / "a.csv"), file_name="a.csv", tokens={})
    assert endpoint.relative_of(file) == "sub/a.csv"

    dest = ft.SmbEndpoint(str(tmp_path / "dst"))
    assert Path(dest.destination_for("sub/x.csv")) == tmp_path / "dst" / "sub" / "x.csv"


def test_smb_endpoint_rejects_traversal_like_local_endpoint(tmp_path):
    endpoint = ft.SmbEndpoint(str(tmp_path / "dst"))
    with pytest.raises(ft.TransferError, match="escapes destination root"):
        endpoint.destination_for("../escape.csv")


def test_smb_endpoint_has_no_allowlist(tmp_path, monkeypatch):
    # Unlike LocalEndpoint, SmbEndpoint must not call resolve_allowed_path --
    # a UNC path is never inside SERVER_FILE_ALLOWED_DIRS.
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", None)
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", ())
    endpoint = ft.SmbEndpoint(str(tmp_path / "anywhere"))
    assert endpoint.root == (tmp_path / "anywhere").resolve()


def test_smb_endpoint_write_read_exists_size_delete(tmp_path):
    endpoint = ft.SmbEndpoint(str(tmp_path / "out"))
    dest = endpoint.destination_for("sub/x.csv")

    assert not endpoint.exists(dest)
    endpoint.write(dest, io.BytesIO(b"id\n1\n"))

    assert endpoint.exists(dest)
    assert endpoint.size(dest) == 5
    with endpoint.open_read(dest) as fh:
        assert fh.read() == b"id\n1\n"
    assert not Path(dest + ".part").exists()

    endpoint.delete(dest)
    assert not endpoint.exists(dest)


def test_smb_endpoint_identity_prefers_location_key_over_none(tmp_path):
    a = ft.SmbEndpoint(str(tmp_path / "out"), location_key=("smb", "fileserver01", "share"))
    b = ft.SmbEndpoint(str(tmp_path / "out"), location_key=("smb", "fileserver01", "share"))
    c = ft.SmbEndpoint(str(tmp_path / "out"), location_key=("smb", "fileserver02", "share"))
    path = str(tmp_path / "out" / "x.csv")
    assert a.identity(path) == b.identity(path)
    assert a.identity(path) != c.identity(path)
    assert a.identity(path)[0] == "smb"


def test_build_endpoint_smb_connects_via_session_and_returns_smb_endpoint(tmp_path):
    class _FakeSession:
        def __init__(self) -> None:
            self.connected = []

        def client_for(self, spec):
            self.connected.append(spec.credentials_ref)
            return _FakeSmbSession(f"\\\\host\\share")

        def location_key_for(self, spec):
            return ("smb", "host", "share")

    session = _FakeSession()
    spec = FileSourceSpec(kind="smb", root=str(tmp_path / "in"), pattern="*.csv", credentials_ref="vendor-share")

    endpoint = ft.build_endpoint(session, spec)

    assert isinstance(endpoint, ft.SmbEndpoint)
    assert session.connected == ["vendor-share"]
    assert endpoint.identity(str(tmp_path / "in" / "x.csv"))[1] == ("smb", "host", "share")


def test_is_transport_error_recognizes_smb_connect_error():
    from api.services.multi_file_remote import SmbConnectError

    assert ft.is_transport_error(SmbConnectError("net use failed")) is True
    assert ft.is_transport_error(ft.TransferError("boom")) is False
```

Check the top of `tests/unit/test_file_transfer.py` for its existing imports (`io`, `Path`, `DiscoveredFile`, `FileSourceSpec`, `ft` alias for `api.services.file_transfer`, `pytest`) -- it already imports all of these for the S3/Sftp endpoint tests; add `from etl_framework.reconciliation.file_mapping import FileSourceSpec` only if it isn't already imported (check first).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `rtk proxy python -m pytest tests/unit/test_file_transfer.py -k smb -v`
Expected: FAIL. `ft.SmbEndpoint` doesn't exist yet (`AttributeError`), and `build_endpoint` raises `ValueError: Unsupported file_transfer location kind: smb`.

- [ ] **Step 3: Extract `_FilesystemEndpoint` and add `SmbEndpoint`**

In `api/services/file_transfer.py`, replace the whole `LocalEndpoint` class (from `class LocalEndpoint:` through its `delete` method, i.e. everything currently between the `# -- Local ---` section's `_windows_illegal_name` function and the `# -- S3 ---` comment) with:

```python
class _FilesystemEndpoint:
    """Shared path-safety and streaming I/O for a filesystem-backed endpoint
    (``local`` or ``smb``) once ``self.root`` is set. Subclasses set
    ``self.root`` in their own ``__init__`` -- after their own containment
    check, if any -- and implement ``identity``."""

    def relative_of(self, file: DiscoveredFile) -> str:
        try:
            return Path(file.path).resolve().relative_to(self.root).as_posix()
        except ValueError:
            raise TransferError(f"'{file.path}' is not under source root '{self.root}'") from None

    def destination_for(self, relative: str) -> str:
        target = (self.root / relative).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            raise TransferError(
                f"destination path for '{relative}' escapes destination root '{self.root}'"
            ) from None
        if os.name == "nt":
            # After the escape check so traversal/absolute inputs keep their
            # more specific "escapes destination root" error.
            for component in relative.split("/"):
                reason = _windows_illegal_name(component)
                if reason:
                    raise TransferError(f"destination name '{component}' in '{relative}' {reason}")
        return str(target)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def exists_many(self, paths: list[str]) -> set[str]:
        return {path for path in paths if Path(path).exists()}

    def size(self, path: str) -> int:
        return Path(path).stat().st_size

    def open_read(self, path: str):
        return open(path, "rb")

    def write(self, path: str, stream: Any) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f"{dest.name}.{uuid4().hex}{PART_SUFFIX}")
        try:
            with open(part, "wb") as fh:
                _copy_stream(stream, fh)
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    def delete(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)


class LocalEndpoint(_FilesystemEndpoint):
    kind = "local"

    def __init__(self, root: str) -> None:
        self.credentials_ref = None
        # Raises HTTPException(400) when root is outside the server allowlist.
        self.root = resolve_allowed_path(root)

    def identity(self, path: str) -> tuple:
        return ("local", None, os.path.normcase(str(Path(path).resolve())))


class SmbEndpoint(_FilesystemEndpoint):
    """A Windows UNC/SMB share, already connected (``net use``) by
    ``RemoteFileSourceSession``/``build_endpoint`` before this is
    constructed. No ``SERVER_FILE_ALLOWED_DIRS`` allowlist applies -- a UNC
    path is remote, matching how ``sftp``/``s3`` also skip it."""

    kind = "smb"

    def __init__(self, root: str, *, location_key: tuple | None = None) -> None:
        self.credentials_ref = None
        self.location_key = location_key
        self.root = Path(root).resolve()

    def identity(self, path: str) -> tuple:
        location = self.location_key if self.location_key is not None else None
        return ("smb", location, os.path.normcase(str(Path(path).resolve())))
```

- [ ] **Step 4: Add `smb` to `is_transport_error`**

Change:
```python
    if exc is None:
        return False
    if isinstance(exc, (EOFError, ConnectionError, TimeoutError)):
        return True
    try:
        import botocore.exceptions
```
to:
```python
    if exc is None:
        return False
    if isinstance(exc, (EOFError, ConnectionError, TimeoutError)):
        return True
    from api.services.multi_file_remote import SmbConnectError

    if isinstance(exc, SmbConnectError):
        return True
    try:
        import botocore.exceptions
```

- [ ] **Step 5: Add `smb` to `build_endpoint`**

Change:
```python
def build_endpoint(session: Any, spec: Any):
    """Endpoint for a ``FileSourceSpec``. ``session`` is a
    ``RemoteFileSourceSession`` (supplies cached, credential-resolved clients)."""
    if spec.kind == "local":
        return LocalEndpoint(spec.root)
    client = session.client_for(spec)
    location_key = session.location_key_for(spec)
    if spec.kind == "s3":
        return S3Endpoint(spec.root, client, spec.credentials_ref, location_key=location_key)
    if spec.kind == "sftp":
        return SftpEndpoint(spec.root, client, spec.credentials_ref, location_key=location_key)
    raise ValueError(f"Unsupported file_transfer location kind: {spec.kind}")
```
to:
```python
def build_endpoint(session: Any, spec: Any):
    """Endpoint for a ``FileSourceSpec``. ``session`` is a
    ``RemoteFileSourceSession`` (supplies cached, credential-resolved clients)."""
    if spec.kind == "local":
        return LocalEndpoint(spec.root)
    if spec.kind == "smb":
        session.client_for(spec)  # connects (net use) before any path is touched
        return SmbEndpoint(spec.root, location_key=session.location_key_for(spec))
    client = session.client_for(spec)
    location_key = session.location_key_for(spec)
    if spec.kind == "s3":
        return S3Endpoint(spec.root, client, spec.credentials_ref, location_key=location_key)
    if spec.kind == "sftp":
        return SftpEndpoint(spec.root, client, spec.credentials_ref, location_key=location_key)
    raise ValueError(f"Unsupported file_transfer location kind: {spec.kind}")
```

Also update the module docstring's first line (currently `"""Copy files between ``local``, ``s3`` and ``sftp`` locations for`) to add `smb`:
```python
"""Copy files between ``local``, ``s3``, ``sftp`` and ``smb`` locations for
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `rtk proxy python -m pytest tests/unit/test_file_transfer.py -v`
Expected: all PASS, including every pre-existing `test_local_endpoint_*` test (the refactor must not change `LocalEndpoint`'s behavior).

- [ ] **Step 7: Commit**

```bash
cd /c/atom
git add api/services/file_transfer.py tests/unit/test_file_transfer.py
git commit -m "feat: add SmbEndpoint and wire smb into build_endpoint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `RunExecutor` smoke test for an end-to-end `smb` `file_transfer` run

**Files:**
- Test: `tests/unit/test_run_executor_file_transfer.py`

No production code changes -- `_execute_file_transfer` already works for any kind `build_endpoint`/`RemoteFileSourceSession` support, so this task is purely a regression/integration test proving the wiring from Tasks 5-6 actually reaches a real `RunExecutor` run.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_run_executor_file_transfer.py` (reuse the file's existing `db_session`/`allowed_dir`-style fixtures and `executor(...)` helper -- check the top of the file for their exact names):

```python
def test_smb_to_local_copies_files_end_to_end(db_session, allowed_dir, tmp_path, monkeypatch):
    from etl_framework.repository.repository import FileServerProfileRepository

    smb_src = tmp_path / "smb_src"
    smb_src.mkdir()
    (smb_src / "sales_east.csv").write_bytes(b"id\n1\n")

    FileServerProfileRepository(db_session).create({
        "name": "vendor-share", "kind": "smb", "host": "fileserver01",
        "username": "svc", "password": "s3cret",
    })
    monkeypatch.setattr("api.services.multi_file_remote._net_use", lambda resource, u, p: None)
    monkeypatch.setattr("api.services.multi_file_remote.parse_unc_root", lambda root: ("fileserver01", "share"))

    job = transfer_job(
        {"kind": "smb", "root": str(smb_src), "pattern": "sales_{region}.csv", "credentials_ref": "vendor-share"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED
    assert (allowed_dir / "dst" / "sales_east.csv").read_bytes() == b"id\n1\n"
    assert result.mismatch_summary["copied"] == 1
```

If `transfer_job`/`allowed_dir`/`executor`/`TestStatus` aren't the exact names already used elsewhere in this file, read the file first and use whatever names it already defines -- don't introduce a second, differently-named helper.

- [ ] **Step 2: Run the test to verify it fails**

Run: `rtk proxy python -m pytest tests/unit/test_run_executor_file_transfer.py -k smb -v`
Expected: FAIL before Tasks 5-6 land; if this task runs after them (as planned), it should PASS immediately -- in that case skip to Step 4 and note in the commit that no red-green cycle was needed since this is a pure integration check of already-implemented code.

- [ ] **Step 3: If it fails for an unexpected reason, fix it**

If it fails with anything other than confirming the smb wiring works end to end, that's a real gap Tasks 5-6 missed -- fix the root cause in `api/services/multi_file_remote.py` or `api/services/file_transfer.py`, not in this test.

- [ ] **Step 4: Run the whole file for regressions**

Run: `rtk proxy python -m pytest tests/unit/test_run_executor_file_transfer.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/atom
git add tests/unit/test_run_executor_file_transfer.py
git commit -m "test: cover an end-to-end smb-to-local file_transfer run

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: File Servers tab UI

**Files:**
- Modify: `frontend/partials/tab-file-servers.html`
- Regenerate: `frontend/index.html`

- [ ] **Step 1: Add the `smb` kind option and its form fields**

In `frontend/partials/tab-file-servers.html`, change:
```html
        <div><label class="field-label">Kind</label>
          <select x-model="fileServerModal.data.kind" class="field-input field-select" data-testid="file-server-kind-select">
            <option value="sftp">SFTP</option><option value="scp">SCP</option><option value="s3">S3</option>
          </select>
        </div>
```
to:
```html
        <div><label class="field-label">Kind</label>
          <select x-model="fileServerModal.data.kind" class="field-input field-select" data-testid="file-server-kind-select">
            <option value="sftp">SFTP</option><option value="scp">SCP</option><option value="s3">S3</option><option value="smb">SMB / UNC Share</option>
          </select>
        </div>
```

Then insert a new field block right after the closing `</template>` of the existing `x-if="fileServerModal.data.kind === 'sftp' || fileServerModal.data.kind === 'scp'"` block (i.e. immediately before `<template x-if="fileServerModal.data.kind === 's3'">`):

```html
        <template x-if="fileServerModal.data.kind === 'smb'">
          <div class="space-y-3">
            <div><label class="field-label">Host</label><input x-model="fileServerModal.data.host" class="field-input" placeholder="fileserver01" data-testid="file-server-smb-host-input" /></div>
            <div><label class="field-label">Username</label><input x-model="fileServerModal.data.username" class="field-input" placeholder="DOMAIN\\user or user@domain.com" data-testid="file-server-smb-username-input" /></div>
            <div><label class="field-label">Password</label><input type="password" x-model="fileServerModal.data.password" class="field-input" data-testid="file-server-smb-password-input" /></div>
          </div>
        </template>
```

- [ ] **Step 2: Update the intro copy to mention SMB**

Change:
```html
      <div class="section-sub">SFTP / SCP / S3 authentication profiles for file_watcher, file_transfer and multi-file jobs</div>
```
to:
```html
      <div class="section-sub">SFTP / SCP / S3 / SMB authentication profiles for file_watcher, file_transfer and multi-file jobs</div>
```
and:
```html
    <div class="empty-state-sub">Add an SFTP, SCP, or S3 profile to use with file_watcher, file_transfer and multi-file jobs (file_transfer destinations need write access)</div>
```
to:
```html
    <div class="empty-state-sub">Add an SFTP, SCP, S3, or SMB profile to use with file_watcher, file_transfer and multi-file jobs (file_transfer destinations need write access)</div>
```

- [ ] **Step 3: Rebuild the generated HTML**

Run: `cd /c/atom && npm run build:html`
Expected: `Built ... index.html from ... + N partials`. If it also touches `frontend/features/diff-search.js` with no content diff (`git diff --stat frontend/features/diff-search.js` shows 0 insertions/deletions despite `git status` flagging it), run `git checkout -- frontend/features/diff-search.js` to leave the tree clean.

- [ ] **Step 4: Confirm the diff is only the intended change**

Run: `git diff --stat frontend/index.html`
Expected: shows changes; `git diff frontend/index.html` should contain only the new `<option>`, the new field block, and the two copy edits -- nothing else.

- [ ] **Step 5: Commit**

```bash
cd /c/atom
git add frontend/partials/tab-file-servers.html frontend/index.html
git commit -m "feat: add smb kind to the File Servers tab

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Job editor location-kind dropdowns

**Files:**
- Modify: `frontend/partials/tab-launch.html`
- Regenerate: `frontend/index.html`

- [ ] **Step 1: Add `smb` to all 5 location-kind `<select>`s and their file-server filters**

In `frontend/partials/tab-launch.html`, there are 5 occurrences of a `<select ... data-testid="job-modal-*-kind-select">` immediately followed by `<option value="local">local</option>` / `<option value="s3">s3</option>` / `<option value="sftp">sftp</option>` (three of the five also have `<option value="scp">scp</option>`), each paired with a `fileServers.filter(f => jobModal.<field> === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))` expression further down in the same block. They are, by their `data-testid` prefix: `job-modal-fw-location-kind-select` (file_watcher), `job-modal-ft-source-kind-select` and `job-modal-ft-dest-kind-select` (file_transfer), `job-modal-mf-source-kind-select` and `job-modal-mf-target-kind-select` (multi-file reconciliation).

For **each of the 5**, add `<option value="smb">smb</option>` as the last option in its `<select>`, and change its filter expression from the two-way ternary:
```
f.kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp')
```
(with the actual field name substituted for the kind check) to a three-way one that adds `smb`, for example for `fw`:
```
jobModal.fw_location_kind === 's3' ? f.kind === 's3' : jobModal.fw_location_kind === 'smb' ? f.kind === 'smb' : (f.kind === 'sftp' || f.kind === 'scp')
```
Apply the same transformation (only the field name changes) to all 5. Concretely, for the file_watcher one, change:
```html
              <select x-model="jobModal.fw_location_kind" class="field-input field-select" data-testid="job-modal-fw-location-kind-select" id="a11y-launch-fw-location-kind">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
                <option value="scp">scp</option>
              </select>
```
to:
```html
              <select x-model="jobModal.fw_location_kind" class="field-input field-select" data-testid="job-modal-fw-location-kind-select" id="a11y-launch-fw-location-kind">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
                <option value="scp">scp</option>
                <option value="smb">smb</option>
              </select>
```
and:
```html
                <template x-for="fs in fileServers.filter(f => jobModal.fw_location_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```
to:
```html
                <template x-for="fs in fileServers.filter(f => jobModal.fw_location_kind === 's3' ? f.kind === 's3' : jobModal.fw_location_kind === 'smb' ? f.kind === 'smb' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```

For file_transfer source (`ft_source_kind`):
```html
              <select x-model="jobModal.ft_source_kind" @change="jobModal.ft_source_credentials_ref = ''" class="field-input field-select" data-testid="job-modal-ft-source-kind-select" id="a11y-launch-ft-source-kind">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
                <option value="scp">scp</option>
              </select>
```
to (add the option, keep the existing `@change` attribute):
```html
              <select x-model="jobModal.ft_source_kind" @change="jobModal.ft_source_credentials_ref = ''" class="field-input field-select" data-testid="job-modal-ft-source-kind-select" id="a11y-launch-ft-source-kind">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
                <option value="scp">scp</option>
                <option value="smb">smb</option>
              </select>
```
and:
```html
                <template x-for="fs in fileServers.filter(f => jobModal.ft_source_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```
to:
```html
                <template x-for="fs in fileServers.filter(f => jobModal.ft_source_kind === 's3' ? f.kind === 's3' : jobModal.ft_source_kind === 'smb' ? f.kind === 'smb' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```

For file_transfer destination (`ft_dest_kind`), same transformation:
```html
                <select x-model="jobModal.ft_dest_kind" @change="jobModal.ft_dest_credentials_ref = ''" class="field-input field-select" data-testid="job-modal-ft-dest-kind-select" id="a11y-launch-ft-dest-kind">
                  <option value="local">local</option>
                  <option value="s3">s3</option>
                  <option value="sftp">sftp</option>
                  <option value="scp">scp</option>
                </select>
```
to:
```html
                <select x-model="jobModal.ft_dest_kind" @change="jobModal.ft_dest_credentials_ref = ''" class="field-input field-select" data-testid="job-modal-ft-dest-kind-select" id="a11y-launch-ft-dest-kind">
                  <option value="local">local</option>
                  <option value="s3">s3</option>
                  <option value="sftp">sftp</option>
                  <option value="scp">scp</option>
                  <option value="smb">smb</option>
                </select>
```
and:
```html
                  <template x-for="fs in fileServers.filter(f => jobModal.ft_dest_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```
to:
```html
                  <template x-for="fs in fileServers.filter(f => jobModal.ft_dest_kind === 's3' ? f.kind === 's3' : jobModal.ft_dest_kind === 'smb' ? f.kind === 'smb' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```

For multi-file source (`mf_source_kind`), which has no `scp` option today -- only add `smb`, don't add `scp`:
```html
              <select x-model="jobModal.mf_source_kind" class="field-input field-select" data-testid="job-modal-mf-source-kind-select" aria-label="job modal mf source kind select">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
              </select>
```
to:
```html
              <select x-model="jobModal.mf_source_kind" class="field-input field-select" data-testid="job-modal-mf-source-kind-select" aria-label="job modal mf source kind select">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
                <option value="smb">smb</option>
              </select>
```
and:
```html
                  <template x-for="fs in fileServers.filter(f => jobModal.mf_source_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```
to:
```html
                  <template x-for="fs in fileServers.filter(f => jobModal.mf_source_kind === 's3' ? f.kind === 's3' : jobModal.mf_source_kind === 'smb' ? f.kind === 'smb' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```

For multi-file target (`mf_target_kind`), same shape as source:
```html
              <select x-model="jobModal.mf_target_kind" class="field-input field-select" data-testid="job-modal-mf-target-kind-select" aria-label="job modal mf target kind select">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
              </select>
```
to:
```html
              <select x-model="jobModal.mf_target_kind" class="field-input field-select" data-testid="job-modal-mf-target-kind-select" aria-label="job modal mf target kind select">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
                <option value="smb">smb</option>
              </select>
```
and:
```html
                  <template x-for="fs in fileServers.filter(f => jobModal.mf_target_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```
to:
```html
                  <template x-for="fs in fileServers.filter(f => jobModal.mf_target_kind === 's3' ? f.kind === 's3' : jobModal.mf_target_kind === 'smb' ? f.kind === 'smb' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
```

Before making each edit, `grep -n` the exact current text in the file first (line numbers drift as earlier tasks/other work touch the file) and use the Edit tool's exact-string matching against what's actually there, not the line numbers quoted above.

- [ ] **Step 2: Confirm `frontend/features/launch.js` needs no changes**

Run: `grep -n "kind !== 'local'" frontend/features/launch.js`
Expected: shows the existing generic `m.fw_location_kind === 'local' || ...`, `m.ft_source_kind === 'local' || ...` etc. checks in `canSaveJob` and the equally generic `kind: m.xxx_kind || 'local'` / `if (m.xxx_kind !== 'local' && m.xxx_credentials_ref)` blocks in the params-building code -- all already kind-agnostic, so `smb` works through them with no edit. If any of these turn out to hardcode `'s3'`/`'sftp'`/`'scp'` instead of a generic `!== 'local'` check, that's new information this plan didn't anticipate -- stop and report it rather than guessing a fix.

- [ ] **Step 3: Rebuild the generated HTML**

Run: `cd /c/atom && npm run build:html`
Expected: builds cleanly. Same `diff-search.js` cleanup as Task 8's Step 3 if needed.

- [ ] **Step 4: Sanity-check the JS still parses**

Run: `node --check frontend/features/launch.js`
Expected: no output (exit 0) -- confirms Step 2 found no JS to touch, or that any touched JS is still syntactically valid.

- [ ] **Step 5: Commit**

```bash
cd /c/atom
git add frontend/partials/tab-launch.html frontend/index.html
git commit -m "feat: add smb to the file_watcher, file_transfer, and multi-file kind pickers

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Help content

**Files:**
- Modify: `frontend/help-content.js`

- [ ] **Step 1: Update the job-type decision table and the file_watcher/file_transfer scenarios**

In `frontend/help-content.js`, find the `Which Job Type When?` entry (its `text` string currently reads `...file_watcher to gate on arrival; file_transfer to copy files between local, S3, and SFTP/SCP locations;...` after the earlier file_transfer work). Change `local, S3, and SFTP/SCP locations` to `local, S3, SFTP/SCP, and SMB locations`.

Find Scenario 6 (`File Watcher Gating a Sequence on a Nightly Drop`) and Scenario 7 (`Staging Files with File Transfer Before a Reconciliation`) -- their `text` fields currently describe `local, S3, SFTP, or SCP location`. In each, change `local, S3, SFTP, or SCP location` to `local, S3, SFTP, SCP, or SMB location`. Use `grep -n "SFTP, or SCP location" frontend/help-content.js` first to find the exact current occurrences before editing -- don't guess the surrounding text.

- [ ] **Step 2: Sanity-check the JS still parses**

Run: `node --check frontend/help-content.js`
Expected: no output (exit 0).

- [ ] **Step 3: Run the help e2e spec to check the section/scenario count didn't shift**

Run: `rtk proxy node node_modules/@playwright/test/cli.js test tests/e2e/11-help.spec.ts --reporter=list`
Expected: all PASS -- editing existing scenario text, not adding a new scenario object, must not change the `.help-nav-item`/`.help-section` count the spec asserts.

- [ ] **Step 4: Commit**

```bash
cd /c/atom
git add frontend/help-content.js
git commit -m "docs: mention smb in the file_watcher/file_transfer help content

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Playwright e2e

**Files:**
- Create: `tests/e2e/56-smb-file-transfer.spec.ts`

- [ ] **Step 1: Write the spec**

Look at `tests/e2e/33-file-servers.spec.ts` and `tests/e2e/32-launch-remaining-job-types.spec.ts` first (both already exist and cover the equivalent sftp/scp flows) to match their exact helper imports, fixtures, and cleanup pattern -- reuse `authedContext`, `deleteJob`, `deleteFileServerByName` from `tests/e2e/api-helpers.ts` the same way those two files already do. Then create `tests/e2e/56-smb-file-transfer.spec.ts`:

```ts
import { test, expect } from './fixtures';
import { authedContext, deleteJob, deleteFileServerByName } from './api-helpers';

// Covers the smb kind added to File Servers (frontend/partials/tab-file-servers.html)
// and to the file_watcher/file_transfer/multi-file location pickers
// (frontend/partials/tab-launch.html). No real Windows host or SMB share is
// available in CI, so this is UI-round-trip coverage only (matching how the
// sftp/scp kinds are covered without a live server in 32-launch-remaining-job-types.spec.ts) --
// live SMB connectivity is a manual verification step, not part of this suite.
test.describe('56 smb file server and job editor', () => {
  const createdJobNames: string[] = [];
  const createdFileServerNames: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    if (createdJobNames.length === 0 && createdFileServerNames.length === 0) return;
    const ctx = await authedContext(adminToken);
    try {
      while (createdJobNames.length) await deleteJob(ctx, createdJobNames.pop()!);
      while (createdFileServerNames.length) await deleteFileServerByName(ctx, createdFileServerNames.pop()!);
    } finally {
      await ctx.dispose();
    }
  });

  test('File Servers: smb profile round-trips host, username, password', async ({ authedPage }) => {
    const name = `e2e-smb-profile-${Date.now()}`;
    createdFileServerNames.push(name);

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-file-servers"]').click();
    await authedPage.locator('[data-testid="file-server-add-btn"]').click();
    await authedPage.locator('[data-testid="file-server-name-input"]').fill(name);
    await authedPage.locator('[data-testid="file-server-kind-select"]').selectOption('smb');
    await authedPage.locator('[data-testid="file-server-smb-host-input"]').fill('fileserver01');
    await authedPage.locator('[data-testid="file-server-smb-username-input"]').fill('CORP\\svc-atom');
    await authedPage.locator('[data-testid="file-server-smb-password-input"]').fill('s3cret');
    await authedPage.locator('[data-testid="file-server-save-btn"]').click();

    await expect(authedPage.locator(`text=${name}`)).toBeVisible();
  });

  test('file_transfer job: smb source and local destination round-trip', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-file-transfer-smb-${Date.now()}`;
    createdJobNames.push(jobName);
    const profileName = `e2e-smb-profile-2-${Date.now()}`;
    createdFileServerNames.push(profileName);

    const ctx = await authedContext(adminToken);
    try {
      await ctx.post('/api/file-servers', {
        data: { name: profileName, kind: 'smb', host: 'fileserver01', username: 'svc', password: 'x' },
      });
    } finally {
      await ctx.dispose();
    }

    await authedPage.goto('/');
    await authedPage.locator('[data-testid="nav-tab-jobs"]').click();
    await authedPage.locator('[data-testid="job-new-btn"]').click();
    await authedPage.locator('[data-testid="job-modal-name-input"]').fill(jobName);
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('file_transfer');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await authedPage.locator('[data-testid="job-modal-ft-source-kind-select"]').selectOption('smb');
    await authedPage.locator('[data-testid="job-modal-ft-source-root-input"]').fill('\\\\fileserver01\\vendor\\inbound');
    await authedPage.locator('[data-testid="job-modal-ft-source-pattern-input"]').fill('SALES_*.csv');
    await authedPage.locator('[data-testid="job-modal-ft-source-credentials-ref-select"]').selectOption(profileName);
    await authedPage.locator('[data-testid="job-modal-ft-dest-root-input"]').fill('/data/staged');

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-kind-select"]')).toHaveValue('smb');
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-root-input"]')).toHaveValue('\\\\fileserver01\\vendor\\inbound');
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-credentials-ref-select"]')).toHaveValue(profileName);
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });
});
```

If `nav-tab-file-servers` isn't the exact `data-testid` the File Servers nav tab uses, check `tests/e2e/33-file-servers.spec.ts` for the real one and use that instead.

- [ ] **Step 2: Run the spec**

Run: `rtk proxy node node_modules/@playwright/test/cli.js test tests/e2e/56-smb-file-transfer.spec.ts --reporter=list`
Expected: 2 passed (plus setup tests).

- [ ] **Step 3: Run the neighbouring specs for regressions**

Run: `rtk proxy node node_modules/@playwright/test/cli.js test tests/e2e/33-file-servers.spec.ts tests/e2e/32-launch-remaining-job-types.spec.ts tests/e2e/11-help.spec.ts --reporter=list`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
cd /c/atom
git add tests/e2e/56-smb-file-transfer.spec.ts
git commit -m "test(e2e): cover smb file server profile and file_transfer round trip

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Full verification

- [ ] **Step 1: Run the entire unit suite**

Run: `rtk proxy python -m pytest tests/unit -q -p no:cacheprovider`
Expected: all pass, no regressions in the untouched `sftp`/`scp`/`s3`/`local` test suites.

- [ ] **Step 2: Confirm the generated HTML matches the partials**

Run: `cd /c/atom && npm run build:html && git status --short frontend/`
Expected: no changes reported.

- [ ] **Step 3: Re-run every e2e spec touched by this plan**

Run: `rtk proxy node node_modules/@playwright/test/cli.js test tests/e2e/56-smb-file-transfer.spec.ts tests/e2e/33-file-servers.spec.ts tests/e2e/32-launch-remaining-job-types.spec.ts tests/e2e/11-help.spec.ts --reporter=list`
Expected: all pass.

- [ ] **Step 4: Confirm a clean tree**

Run: `cd /c/atom && git status --short`
Expected: empty output.

---

## Self-Review

**Spec coverage** (spec section → task):
- §2 profile/credential model (`smb` kind, no new columns, `host`/`username`/`password` reuse) → Task 1.
- §3 SMB mechanics (root format/validation, `net use` connect with per-host locking, `net use ... /delete` on close, reusing `discover_local_files`/`LocalEndpoint` logic, `identity()` keyed by server+share) → Tasks 2, 5, 6.
- §5 wiring into the five kind-enumeration sites and the two dispatch points, for free to `file_watcher`/multi-file reconciliation → Tasks 2, 3, 4, 5, 6, 7.
- §6 platform guard (`os.name != "nt"`), missing-tool handling (N/A for `net use`, which is always present on Windows; the platform guard covers the equivalent case), transport-error classification, no secrets in results (`_scrub_smb_error`) → Task 5 (guard + scrub), Task 6 (`is_transport_error`).
- §7 frontend (File Servers kind + fields, job editor kind pickers + filters, help content) → Tasks 8, 9, 10.
- §8 testing (unit coverage for connect/lock/discovery/endpoint, e2e for File Servers + job editor) → Tasks 1-7, 11.
- §9 out of scope: nothing planned for it (WebDAV, `pywin32`, lock refinement, live e2e, and — since this plan is SMB-only — WinSCP/§4 entirely, which is the separate plan).

**Placeholder scan:** none — every code step shows the actual code, every test shows actual assertions, every command has an exact invocation and expected result.

**Type consistency:** `SmbSession(resource, lock)` (Task 5) is what `connect_smb_share` returns and what `SmbSession.close()` (called generically by `close_remote_client`) operates on — used identically in Tasks 5 and its tests. `SmbEndpoint(root, *, location_key=None)` (Task 6) matches how `build_endpoint` constructs it and how `S3Endpoint`/`SftpEndpoint` already take `location_key` as a keyword-only argument, for consistency. `parse_unc_root(root) -> tuple[str, str]` (Task 2) is used unchanged by `connect_smb_share` and `location_key_for` (Task 5) and by `SmbEndpoint`'s tests (Task 6, indirectly via `location_key`). `SmbConnectError` (introduced as a placeholder in Task 1, implemented in Task 5) is the exact name `is_transport_error` imports in Task 6.

**Known limitations carried from the spec, not fixed here:** the per-host lock serializes every `smb` connect to a given server, even when two jobs share the same credentials (§9, deliberately deferred). FTPS/true-SCP via WinSCP is a separate plan.
