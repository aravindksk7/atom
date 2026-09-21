# File Transfer Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `file_transfer` job type that copies files between local folders, S3 and SFTP/SCP locations, usable in a sequence to stage files for reconciliation or for any other copy use case.

**Architecture:** A new `api/services/file_transfer.py` owns copy logic through one small endpoint adapter per kind (`LocalEndpoint`, `S3Endpoint`, `SftpEndpoint`), a pure `plan_transfer` function, and a streaming `run_transfer`. A new `etl_framework/reconciliation/file_transfer_spec.py` owns param validation and parsing so `api/schemas.py` and `etl_framework/runner/job_validation.py` share one implementation. `RunExecutor` dispatches the job type like `file_watcher`. The three existing `discover_*` functions gain an opt-in `recursive` keyword.

**Tech Stack:** Python 3.14 (FastAPI, Pydantic, boto3, paramiko), pytest + moto, Alpine.js frontend built from partials with `npm run build:html`, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-21-file-transfer-job-design.md`

**Test commands.** Use raw `python -m pytest` (a stale rtk cache once masked failures). For Playwright use `node node_modules/@playwright/test/cli.js test <file>` (the `npx` version can mismatch), and prefix with `rtk proxy` if the Bash hook mangles the output. Run `git` through the Bash tool from `c:/atom` (not inside a worktree).

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `etl_framework/reconciliation/file_mapping.py` | modify | `recursive` keyword on the three `discover_*` functions, depth cap constant |
| `api/services/multi_file_remote.py` | modify | `discover(..., recursive=)` passthrough, public `client_for` |
| `etl_framework/reconciliation/file_transfer_spec.py` | create | `file_transfer_param_errors`, `FileTransferSpec`, `parse_file_transfer_params` |
| `api/schemas.py` | modify | `"file_transfer"` in the `job_type` Literal, validation branch |
| `etl_framework/runner/job_validation.py` | modify | `_validate_file_transfer` and dispatch |
| `api/services/file_transfer.py` | create | `TransferError`, three endpoint adapters, `plan_transfer`, `run_transfer`, `build_endpoint` |
| `api/services/run_executor.py` | modify | `_build_case` dispatch, `_execute_file_transfer`, result mapping |
| `tests/helpers/fake_sftp.py` | create | Shared in-memory fake of the paramiko SFTP client |
| `tests/unit/test_file_mapping.py` | modify | Recursive discovery tests |
| `tests/unit/test_multi_file_remote.py` | modify | `discover(recursive=True)` and `client_for` tests |
| `tests/unit/test_file_transfer_spec.py` | create | Spec module tests |
| `tests/unit/test_file_transfer_validation.py` | create | `JobDefinition` and `validate_job_definition` tests |
| `tests/unit/test_file_transfer.py` | create | Endpoint, plan and run tests |
| `tests/unit/test_run_executor_file_transfer.py` | create | Executor integration with real local files, moto S3, fake SFTP |
| `frontend/features/launch.js` | modify | `ft_*` modal state, edit load, params build, `canSaveJob` |
| `frontend/partials/tab-launch.html` | modify | Job type option and File Transfer panel |
| `frontend/index.html` | regenerate | Output of `npm run build:html` |
| `frontend/help-content.js` | modify | Scenario 7 and decision matrix row |
| `tests/e2e/55-file-transfer-job.spec.ts` | create | UI round trip and API-driven local run |

Nothing to change for credentials bookkeeping: `api/routes/file_servers.py::_references_credentials_ref` and `scripts/list_file_server_credential_refs.py` already scan every nested dict for a `credentials_ref` key, so the new `source` and `destination` objects are covered by the in-use delete guard and the audit script.

---

### Task 1: Recursive discovery

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py:181-259`
- Modify: `api/services/multi_file_remote.py:174-207`
- Test: `tests/unit/test_file_mapping.py`, `tests/unit/test_multi_file_remote.py`

- [ ] **Step 1: Write the failing discovery tests**

Append to `tests/unit/test_file_mapping.py`:

```python
def test_discover_local_files_recursive_walks_subfolders(tmp_path) -> None:
    from pathlib import Path

    (tmp_path / "a.csv").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    (tmp_path / "sub" / "b.csv").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "deeper" / "c.csv").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "skip.txt").write_text("x", encoding="utf-8")

    flat = discover_local_files(tmp_path, "*.csv")
    deep = discover_local_files(tmp_path, "*.csv", recursive=True)

    assert [f.file_name for f in flat] == ["a.csv"]
    assert [Path(f.path).relative_to(tmp_path).as_posix() for f in deep] == [
        "a.csv",
        "sub/b.csv",
        "sub/deeper/c.csv",
    ]


def test_discover_s3_files_recursive_includes_nested_keys() -> None:
    from etl_framework.reconciliation.file_mapping import discover_s3_files

    class FakeS3Client:
        def get_paginator(self, name):
            return self

        def paginate(self, **kwargs):
            return [{"Contents": [
                {"Key": "daily/a.csv"},
                {"Key": "daily/sub/b.csv"},
                {"Key": "daily/sub/c.txt"},
            ]}]

    flat = discover_s3_files(FakeS3Client(), "s3://bkt/daily", "*.csv")
    deep = discover_s3_files(FakeS3Client(), "s3://bkt/daily", "*.csv", recursive=True)

    assert [f.path for f in flat] == ["s3://bkt/daily/a.csv"]
    assert [f.path for f in deep] == ["s3://bkt/daily/a.csv", "s3://bkt/daily/sub/b.csv"]
    assert [f.file_name for f in deep] == ["a.csv", "b.csv"]


def _sftp_attr(name: str, mode: int):
    return type("Attr", (), {"filename": name, "st_mode": mode})()


def test_discover_sftp_files_recursive_walks_directories() -> None:
    from etl_framework.reconciliation.file_mapping import discover_sftp_files

    tree = {
        "/x": [_sftp_attr("a.csv", 0o100644), _sftp_attr("sub", 0o040755)],
        "/x/sub": [_sftp_attr("b.csv", 0o100644), _sftp_attr("deeper", 0o040755)],
        "/x/sub/deeper": [_sftp_attr("c.csv", 0o100644), _sftp_attr("n.txt", 0o100644)],
    }

    class FakeSFTPClient:
        def listdir_attr(self, path):
            return tree[path]

    flat = discover_sftp_files(FakeSFTPClient(), "/x", "*.csv")
    deep = discover_sftp_files(FakeSFTPClient(), "/x", "*.csv", recursive=True)

    assert [f.path for f in flat] == ["/x/a.csv"]
    assert [f.path for f in deep] == ["/x/a.csv", "/x/sub/b.csv", "/x/sub/deeper/c.csv"]


def test_discover_sftp_files_recursive_stops_at_depth_cap() -> None:
    from etl_framework.reconciliation.file_mapping import _MAX_RECURSION_DEPTH, discover_sftp_files

    calls: list[str] = []

    class EndlessSFTPClient:
        def listdir_attr(self, path):
            calls.append(path)
            return [_sftp_attr("d", 0o040755)]

    assert discover_sftp_files(EndlessSFTPClient(), "/x", "*.csv", recursive=True) == []
    assert len(calls) == _MAX_RECURSION_DEPTH + 1
```

Append to `tests/unit/test_multi_file_remote.py`:

```python
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
    # Discovery sorts by full path, so the "sub/" folder sorts before "top.csv".
    assert [f.file_name for f in session.discover(spec, recursive=True)] == ["nested.csv", "top.csv"]


def test_remote_file_source_session_client_for_is_public_and_cached(db, monkeypatch) -> None:
    built: list[str] = []

    def _fake_build_s3_client(profile, spec):
        built.append(spec.credentials_ref)
        return object()

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)
    monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db_, spec: None)

    session = RemoteFileSourceSession(db)
    spec = FileSourceSpec(kind="s3", root="s3://bkt/x", pattern="*", credentials_ref="prof")

    assert session.client_for(spec) is session.client_for(spec)
    assert built == ["prof"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_file_mapping.py tests/unit/test_multi_file_remote.py -k "recursive or client_for" -v`
Expected: FAIL with `TypeError: discover_local_files() got an unexpected keyword argument 'recursive'` (and `AttributeError: ... 'client_for'`).

- [ ] **Step 3: Implement recursive discovery in `file_mapping.py`**

Replace the three functions `discover_local_files`, `discover_s3_files` and `discover_sftp_files` (lines 181-259) with:

```python
# Deepest directory level a recursive discovery will descend to. Guards against
# pathological or cyclic trees (e.g. an SFTP server exposing a directory loop).
_MAX_RECURSION_DEPTH = 20


def discover_local_files(root: Path, pattern: str, *, recursive: bool = False) -> list[DiscoveredFile]:
    """Match files under ``root`` against ``pattern``. By default only files
    directly under ``root`` are considered; ``recursive=True`` also walks
    subfolders (up to ``_MAX_RECURSION_DEPTH`` levels, symlinks not followed).
    ``pattern`` always matches the basename.

    ``root`` must already be a trusted, resolved directory -- callers outside
    this module (e.g. ``RunExecutor``) are responsible for allow-listing it
    first (see ``api.services.file_source.resolve_allowed_path``), the same
    way every other file-backed job resolves paths today.
    """
    regex = compile_token_pattern(pattern)
    root = Path(root)
    if recursive:
        candidates: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            depth = len(Path(dirpath).relative_to(root).parts)
            if depth >= _MAX_RECURSION_DEPTH:
                dirnames[:] = []
            candidates.extend(Path(dirpath) / name for name in filenames)
        candidates.sort()
    else:
        candidates = sorted(root.iterdir())
    discovered: list[DiscoveredFile] = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        match = regex.match(candidate.name)
        if match is None:
            continue
        discovered.append(DiscoveredFile(
            path=str(candidate),
            file_name=candidate.name,
            tokens=match.groupdict(),
        ))
    return discovered


def discover_s3_files(client: Any, root: str, pattern: str, *, recursive: bool = False) -> list[DiscoveredFile]:
    """Discover S3 objects under ``root`` whose basename matches ``pattern``.
    By default only objects directly under the prefix are considered;
    ``recursive=True`` also includes nested keys (up to ``_MAX_RECURSION_DEPTH``
    levels). ``root`` must be ``s3://bucket/prefix``; callers own client
    construction and credentials so tests can inject a fake client without
    requiring boto3.
    """
    parsed = urlparse(root)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError("S3 file source root must be s3://bucket/prefix")
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    regex = compile_token_pattern(pattern)
    discovered: list[DiscoveredFile] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []) or []:
            key = str(item.get("Key") or "")
            if not key or key.endswith("/"):
                continue
            relative = key[len(prefix):] if prefix and key.startswith(prefix) else key
            if "/" in relative and not recursive:
                continue
            if relative.count("/") > _MAX_RECURSION_DEPTH:
                continue
            file_name = Path(relative).name
            match = regex.match(file_name)
            if match is None:
                continue
            discovered.append(DiscoveredFile(
                path=f"s3://{bucket}/{quote(key, safe='/')}",
                file_name=file_name,
                tokens=match.groupdict(),
            ))
    return sorted(discovered, key=lambda f: f.path)


def discover_sftp_files(client: Any, root: str, pattern: str, *, recursive: bool = False) -> list[DiscoveredFile]:
    """Discover regular files under an SFTP directory. By default only files
    directly under ``root`` are considered; ``recursive=True`` also walks
    subdirectories (up to ``_MAX_RECURSION_DEPTH`` levels). Callers own client
    construction and credentials so tests can inject a fake client without
    requiring paramiko.
    """
    regex = compile_token_pattern(pattern)
    root_path = root.rstrip("/") or "/"
    discovered: list[DiscoveredFile] = []

    def _walk(directory: str, depth: int) -> None:
        for item in client.listdir_attr(directory):
            file_name = str(getattr(item, "filename", ""))
            mode = getattr(item, "st_mode", 0)
            path = f"{directory}/{file_name}" if directory != "/" else f"/{file_name}"
            if recursive and mode and stat.S_ISDIR(mode):
                if depth < _MAX_RECURSION_DEPTH:
                    _walk(path, depth + 1)
                continue
            if mode and not stat.S_ISREG(mode):
                continue
            match = regex.match(file_name)
            if match is None:
                continue
            discovered.append(DiscoveredFile(path=path, file_name=file_name, tokens=match.groupdict()))

    _walk(root_path, 0)
    return sorted(discovered, key=lambda f: f.path)
```

- [ ] **Step 4: Add `recursive` and `client_for` to `RemoteFileSourceSession`**

In `api/services/multi_file_remote.py`, add a public method directly after `_client_for` (after the line `return self._clients[key]`):

```python
    def client_for(self, spec: FileSourceSpec):
        """Public accessor for the cached S3/SFTP client of ``spec`` (used by
        ``api.services.file_transfer``). Same one-client-per-(kind,
        credentials_ref) caching as every other call on this session."""
        return self._client_for(spec)
```

Replace the `discover` method with:

```python
    def discover(self, spec: FileSourceSpec, *, recursive: bool = False) -> list[DiscoveredFile]:
        if spec.kind == "local":
            root = resolve_allowed_path(spec.root)
            return discover_local_files(root, spec.pattern, recursive=recursive)
        if spec.kind == "s3":
            return discover_s3_files(self._client_for(spec), spec.root, spec.pattern, recursive=recursive)
        if spec.kind == "sftp":
            return discover_sftp_files(self._client_for(spec), spec.root, spec.pattern, recursive=recursive)
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```

- [ ] **Step 5: Run the tests to verify they pass, plus the existing discovery suites**

Run: `python -m pytest tests/unit/test_file_mapping.py tests/unit/test_multi_file_remote.py tests/unit/test_run_executor_file_watcher.py -v`
Expected: all PASS (existing non-recursive tests unchanged).

- [ ] **Step 6: Commit**

```bash
cd /c/atom
git add etl_framework/reconciliation/file_mapping.py api/services/multi_file_remote.py tests/unit/test_file_mapping.py tests/unit/test_multi_file_remote.py
git commit -m "feat: add opt-in recursive discovery for local, s3 and sftp sources

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: `file_transfer` params spec module

**Files:**
- Create: `etl_framework/reconciliation/file_transfer_spec.py`
- Test: `tests/unit/test_file_transfer_spec.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_file_transfer_spec.py`:

```python
from __future__ import annotations

import pytest

from etl_framework.reconciliation.file_transfer_spec import (
    file_transfer_param_errors,
    parse_file_transfer_params,
)


def _params(**overrides):
    params = {
        "source": {"kind": "local", "root": "/data/in", "pattern": "SALES_*.csv"},
        "destination": {"kind": "local", "root": "/data/out"},
    }
    params.update(overrides)
    return params


def _fields(errors):
    return {field for field, _ in errors}


def test_valid_local_params_have_no_errors():
    assert file_transfer_param_errors(_params()) == []


def test_requires_source_and_destination_objects():
    errors = file_transfer_param_errors({})
    assert _fields(errors) == {"params.source", "params.destination"}


def test_requires_known_kinds():
    errors = file_transfer_param_errors(_params(
        source={"kind": "ftp", "root": "/a", "pattern": "*"},
        destination={"kind": "webdav", "root": "/b"},
    ))
    assert {"params.source.kind", "params.destination.kind"} <= _fields(errors)


def test_source_requires_root_and_pattern_destination_requires_root():
    errors = file_transfer_param_errors(_params(
        source={"kind": "local"},
        destination={"kind": "local"},
    ))
    assert {"params.source.root", "params.source.pattern", "params.destination.root"} <= _fields(errors)


@pytest.mark.parametrize("kind", ["s3", "sftp", "scp"])
def test_remote_kinds_require_credentials_ref_on_each_side(kind):
    errors = file_transfer_param_errors(_params(
        source={"kind": kind, "root": "x", "pattern": "*"},
        destination={"kind": kind, "root": "y"},
    ))
    assert {"params.source.credentials_ref", "params.destination.credentials_ref"} <= _fields(errors)


def test_on_exists_must_be_a_known_value():
    assert "params.on_exists" in _fields(file_transfer_param_errors(_params(on_exists="merge")))
    for value in ("fail", "overwrite", "skip"):
        assert file_transfer_param_errors(_params(on_exists=value)) == []


def test_flags_must_be_booleans():
    errors = file_transfer_param_errors(_params(recursive="yes", preserve_structure=1))
    assert {"params.recursive", "params.preserve_structure"} <= _fields(errors)


def test_preserve_structure_requires_recursive():
    errors = file_transfer_param_errors(_params(preserve_structure=True))
    assert "params.preserve_structure" in _fields(errors)
    assert file_transfer_param_errors(_params(preserve_structure=True, recursive=True)) == []


def test_parse_normalizes_scp_to_sftp_and_applies_defaults():
    spec = parse_file_transfer_params(_params(
        source={"kind": "scp", "root": "/in", "pattern": "*.csv", "credentials_ref": "vendor"},
        destination={"kind": "s3", "root": "s3://bkt/out", "credentials_ref": "aws"},
    ))
    assert spec.source.kind == "sftp"
    assert spec.source.credentials_ref == "vendor"
    assert spec.destination.kind == "s3"
    assert spec.destination.pattern == "*"
    assert (spec.on_exists, spec.recursive, spec.preserve_structure) == ("fail", False, False)


def test_parse_raises_value_error_with_first_message():
    with pytest.raises(ValueError, match="'source' object"):
        parse_file_transfer_params({})
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_file_transfer_spec.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'etl_framework.reconciliation.file_transfer_spec'`.

- [ ] **Step 3: Implement the module**

Create `etl_framework/reconciliation/file_transfer_spec.py`:

```python
# etl_framework/reconciliation/file_transfer_spec.py
"""Param validation and parsing for ``file_transfer`` jobs.

``api/schemas.py`` and ``etl_framework/runner/job_validation.py`` both validate
job params, and ``RunExecutor`` needs them parsed into typed specs. This module
is the single implementation all three share (same idea as ``file_mapping``'s
``FileMappingSpec.from_params``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from etl_framework.reconciliation.file_mapping import FileSourceSpec

LOCATION_KINDS = ("local", "s3", "sftp", "scp")
REMOTE_KINDS = ("s3", "sftp", "scp")
ON_EXISTS_VALUES = ("fail", "overwrite", "skip")


@dataclass(frozen=True)
class FileTransferSpec:
    source: FileSourceSpec
    destination: FileSourceSpec
    on_exists: str = "fail"
    recursive: bool = False
    preserve_structure: bool = False


def file_transfer_param_errors(params: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``(field, message)`` for every problem in ``params``. An empty
    list means the structural checks pass. A referenced file server profile is
    only checked at run time, not here."""
    errors: list[tuple[str, str]] = []

    def check_location(key: str, needs_pattern: bool) -> None:
        location = params.get(key)
        if not isinstance(location, dict):
            errors.append((f"params.{key}", f"file_transfer jobs require a '{key}' object in params"))
            return
        kind = location.get("kind")
        if kind not in LOCATION_KINDS:
            errors.append((f"params.{key}.kind", f"file_transfer {key}.kind must be 'local', 's3', 'sftp', or 'scp'"))
        if not location.get("root"):
            errors.append((f"params.{key}.root", f"file_transfer {key} requires 'root'"))
        if needs_pattern and not location.get("pattern"):
            errors.append((f"params.{key}.pattern", f"file_transfer {key} requires 'pattern'"))
        if kind in REMOTE_KINDS and not location.get("credentials_ref"):
            errors.append((
                f"params.{key}.credentials_ref",
                f"file_transfer {key}.kind '{kind}' requires 'credentials_ref'",
            ))

    check_location("source", needs_pattern=True)
    check_location("destination", needs_pattern=False)

    on_exists = params.get("on_exists")
    if on_exists is not None and on_exists not in ON_EXISTS_VALUES:
        errors.append(("params.on_exists", "file_transfer on_exists must be 'fail', 'overwrite', or 'skip'"))
    for flag in ("recursive", "preserve_structure"):
        value = params.get(flag)
        if value is not None and not isinstance(value, bool):
            errors.append((f"params.{flag}", f"file_transfer {flag} must be true or false"))
    if params.get("preserve_structure") is True and params.get("recursive") is not True:
        errors.append((
            "params.preserve_structure",
            "file_transfer preserve_structure requires recursive to be true",
        ))
    return errors


def _location_spec(location: dict[str, Any]) -> FileSourceSpec:
    kind = location["kind"]
    return FileSourceSpec(
        kind="sftp" if kind == "scp" else kind,
        root=location["root"],
        pattern=location.get("pattern") or "*",
        credentials_ref=location.get("credentials_ref"),
    )


def parse_file_transfer_params(params: dict[str, Any]) -> FileTransferSpec:
    errors = file_transfer_param_errors(params)
    if errors:
        raise ValueError(errors[0][1])
    return FileTransferSpec(
        source=_location_spec(params["source"]),
        destination=_location_spec(params["destination"]),
        on_exists=params.get("on_exists") or "fail",
        recursive=bool(params.get("recursive", False)),
        preserve_structure=bool(params.get("preserve_structure", False)),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_file_transfer_spec.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/atom
git add etl_framework/reconciliation/file_transfer_spec.py tests/unit/test_file_transfer_spec.py
git commit -m "feat: add file_transfer params spec and validation helper

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Register the job type in schema and validation

**Files:**
- Modify: `api/schemas.py:777-783` (Literal) and `api/schemas.py:974-975` (validator branch)
- Modify: `etl_framework/runner/job_validation.py:215-276`
- Test: `tests/unit/test_file_transfer_validation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_file_transfer_validation.py`:

```python
from __future__ import annotations

import pytest

from api.schemas import JobDefinition
from etl_framework.runner.job_validation import validate_job_definition


def _job(**param_overrides):
    params = {
        "source": {"kind": "sftp", "root": "/in", "pattern": "SALES_*.csv", "credentials_ref": "vendor"},
        "destination": {"kind": "s3", "root": "s3://bkt/out", "credentials_ref": "aws"},
        "on_exists": "skip",
        "recursive": True,
        "preserve_structure": True,
    }
    params.update(param_overrides)
    return {"name": "stage_sales", "job_type": "file_transfer", "params": params}


def test_valid_file_transfer_job_definition_builds():
    job = JobDefinition(**_job())
    assert job.job_type == "file_transfer"


def test_valid_file_transfer_job_has_no_validation_issues():
    assert validate_job_definition(_job()) == []


def test_job_definition_rejects_missing_destination():
    payload = _job()
    del payload["params"]["destination"]
    with pytest.raises(ValueError, match="'destination' object"):
        JobDefinition(**payload)


def test_job_definition_rejects_remote_kind_without_credentials_ref():
    payload = _job(destination={"kind": "s3", "root": "s3://bkt/out"})
    with pytest.raises(ValueError, match="requires 'credentials_ref'"):
        JobDefinition(**payload)


def test_job_definition_rejects_preserve_structure_without_recursive():
    payload = _job(recursive=False)
    with pytest.raises(ValueError, match="requires recursive"):
        JobDefinition(**payload)


def test_validate_job_definition_reports_field_level_issues():
    payload = _job(source={"kind": "local"}, on_exists="merge")
    issues = validate_job_definition(payload)
    fields = {issue.field for issue in issues}
    assert {"params.source.root", "params.source.pattern", "params.on_exists"} <= fields
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_file_transfer_validation.py -v`
Expected: FAIL (`job_type` Literal rejects `file_transfer`; `validate_job_definition` returns no issues).

- [ ] **Step 3: Register the type in `api/schemas.py`**

In the `job_type: Literal[...]` list (lines 777-783) change the last row from `"file_watcher",` to:

```python
        "file_watcher", "file_transfer",
```

Directly after the file_watcher branch (after the line `raise ValueError("file_watcher content_match, if given, requires a 'text' field")`, before `if self.job_type == "compare":`) add:

```python
        elif self.job_type == "file_transfer":
            from etl_framework.reconciliation.file_transfer_spec import file_transfer_param_errors

            errors = file_transfer_param_errors(self.params)
            if errors:
                raise ValueError(errors[0][1])
```

- [ ] **Step 4: Register the type in `job_validation.py`**

Add after `_validate_file_watcher` (before `def validate_job_definition`):

```python
def _validate_file_transfer(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    from etl_framework.reconciliation.file_transfer_spec import file_transfer_param_errors

    for field, message in file_transfer_param_errors(params):
        issues.append(ValidationIssue(field, message))
```

In `validate_job_definition`, after the `elif job_type == "file_watcher":` branch add:

```python
    elif job_type == "file_transfer":
        _validate_file_transfer(params, issues)
```

- [ ] **Step 5: Run to verify pass, plus existing validation suites**

Run: `python -m pytest tests/unit/test_file_transfer_validation.py tests/unit/test_job_validation.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
cd /c/atom
git add api/schemas.py etl_framework/runner/job_validation.py tests/unit/test_file_transfer_validation.py
git commit -m "feat: register file_transfer job type in schema and validation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Shared SFTP fake and `LocalEndpoint`

**Files:**
- Create: `tests/helpers/fake_sftp.py`
- Create: `api/services/file_transfer.py`
- Test: `tests/unit/test_file_transfer.py`

- [ ] **Step 1: Create the shared fake SFTP client**

Create `tests/helpers/fake_sftp.py`:

```python
"""In-memory stand-in for ``paramiko.SFTPClient`` covering the calls
``api.services.file_transfer`` and ``discover_sftp_files`` make."""
from __future__ import annotations

import io
import posixpath
import stat as stat_module
from types import SimpleNamespace


class FakeSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/"}
        self.posix_rename_supported = True
        self.fail_putfo_after_partial = False

    # -- discovery ----------------------------------------------------------
    def listdir_attr(self, path: str):
        prefix = path.rstrip("/") + "/"
        entries = []
        for file_path in sorted(self.files):
            rest = file_path[len(prefix):] if file_path.startswith(prefix) else None
            if rest and "/" not in rest:
                entries.append(SimpleNamespace(filename=rest, st_mode=stat_module.S_IFREG | 0o644))
        for dir_path in sorted(self.dirs):
            rest = dir_path[len(prefix):] if dir_path.startswith(prefix) else None
            if rest and "/" not in rest:
                entries.append(SimpleNamespace(filename=rest, st_mode=stat_module.S_IFDIR | 0o755))
        return entries

    # -- reads --------------------------------------------------------------
    def open(self, path: str, mode: str = "rb"):
        if path not in self.files:
            raise FileNotFoundError(path)
        return io.BytesIO(self.files[path])

    def stat(self, path: str):
        if path in self.files:
            return SimpleNamespace(st_size=len(self.files[path]))
        if path in self.dirs:
            return SimpleNamespace(st_size=0)
        raise FileNotFoundError(path)

    # -- writes -------------------------------------------------------------
    def mkdir(self, path: str) -> None:
        self.dirs.add(path)

    def putfo(self, fl, remotepath: str, callback=None, confirm: bool = True):
        assert posixpath.dirname(remotepath) in self.dirs, f"parent directory missing for {remotepath}"
        data = b""
        while True:
            chunk = fl.read(32768)
            if not chunk:
                break
            data += chunk
            if self.fail_putfo_after_partial:
                self.files[remotepath] = data
                raise IOError("connection reset")
        self.files[remotepath] = data
        return SimpleNamespace(st_size=len(data))

    def posix_rename(self, old: str, new: str) -> None:
        if not self.posix_rename_supported:
            raise IOError("Operation unsupported")
        self.files[new] = self.files.pop(old)

    def rename(self, old: str, new: str) -> None:
        if new in self.files:
            raise IOError("Failure")
        self.files[new] = self.files.pop(old)

    def remove(self, path: str) -> None:
        if path not in self.files:
            raise FileNotFoundError(path)
        del self.files[path]

    def close(self) -> None:
        pass
```

- [ ] **Step 2: Write the failing `LocalEndpoint` tests**

Create `tests/unit/test_file_transfer.py`:

```python
from __future__ import annotations

import io
from pathlib import Path

import boto3
import pytest
from fastapi import HTTPException
from moto import mock_aws

from api.services import file_transfer as ft
from etl_framework.reconciliation.file_mapping import DiscoveredFile
from tests.helpers.fake_sftp import FakeSFTP


@pytest.fixture
def allowed_dir(tmp_path, monkeypatch):
    """Restrict the server-side file allowlist to <tmp>/allowed."""
    from api.services import file_source

    base = tmp_path / "allowed"
    base.mkdir()
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", base.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (base.resolve(),))
    return base.resolve()


class _Boom:
    """Stream that yields one chunk, then fails like a dropped connection."""

    def __init__(self) -> None:
        self.calls = 0

    def read(self, size: int = -1) -> bytes:
        self.calls += 1
        if self.calls == 1:
            return b"partial"
        raise OSError("connection reset")


# -- LocalEndpoint -----------------------------------------------------------

def test_local_endpoint_rejects_root_outside_allowlist(allowed_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(HTTPException) as excinfo:
        ft.LocalEndpoint(str(outside))
    assert "Invalid file path" in str(excinfo.value.detail)


def test_local_endpoint_relative_of_is_posix_relative_to_root(allowed_dir):
    (allowed_dir / "src" / "sub").mkdir(parents=True)
    endpoint = ft.LocalEndpoint(str(allowed_dir / "src"))
    file = DiscoveredFile(path=str(allowed_dir / "src" / "sub" / "a.csv"), file_name="a.csv", tokens={})
    assert endpoint.relative_of(file) == "sub/a.csv"


def test_local_endpoint_relative_of_rejects_file_outside_root(allowed_dir):
    (allowed_dir / "src").mkdir()
    endpoint = ft.LocalEndpoint(str(allowed_dir / "src"))
    other = DiscoveredFile(path=str(allowed_dir / "elsewhere.csv"), file_name="elsewhere.csv", tokens={})
    with pytest.raises(ft.TransferError, match="not under source root"):
        endpoint.relative_of(other)


def test_local_endpoint_destination_for_stays_inside_root(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir / "out"))
    assert Path(endpoint.destination_for("sub/x.csv")) == allowed_dir / "out" / "sub" / "x.csv"


def test_local_endpoint_write_read_exists_size_delete(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir / "out"))
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


def test_local_endpoint_write_replaces_existing_file(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    dest = endpoint.destination_for("a.csv")
    endpoint.write(dest, io.BytesIO(b"old"))
    endpoint.write(dest, io.BytesIO(b"newer"))
    assert Path(dest).read_bytes() == b"newer"


def test_local_endpoint_failed_write_keeps_existing_and_leaves_no_part_file(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    dest = endpoint.destination_for("a.csv")
    endpoint.write(dest, io.BytesIO(b"old"))

    with pytest.raises(OSError, match="connection reset"):
        endpoint.write(dest, _Boom())

    assert Path(dest).read_bytes() == b"old"
    assert not Path(dest + ".part").exists()


def test_local_endpoint_identity_is_stable_for_same_file(allowed_dir):
    endpoint = ft.LocalEndpoint(str(allowed_dir))
    a = endpoint.identity(str(allowed_dir / "x.csv"))
    b = endpoint.identity(str(allowed_dir / "sub" / ".." / "x.csv"))
    assert a == b
```

- [ ] **Step 3: Run to verify failure**

Run: `python -m pytest tests/unit/test_file_transfer.py -v`
Expected: FAIL with `ImportError: cannot import name 'file_transfer' from 'api.services'`.

- [ ] **Step 4: Create the module with `TransferError` and `LocalEndpoint`**

Create `api/services/file_transfer.py`:

```python
"""Copy files between ``local``, ``s3`` and ``sftp`` locations for
``file_transfer`` jobs (see docs/superpowers/specs/2026-09-21-file-transfer-job-design.md).

Structure:

* One small *endpoint* class per location kind (``LocalEndpoint``,
  ``S3Endpoint``, ``SftpEndpoint``) with the same duck-typed interface, so a
  copy is kind-agnostic: ``relative_of``, ``destination_for``, ``identity``,
  ``exists``, ``size``, ``open_read``, ``write``, ``delete``.
* ``plan_transfer`` -- pure planning: destination keys, safety checks,
  ``on_exists`` handling. Writes nothing.
* ``run_transfer`` -- streams each planned copy and verifies its size.

Writes are atomic from the point of view of anything watching the destination
(local/sftp write ``<name>.part`` then rename; S3 uploads only become visible
on completion), so a ``file_watcher`` or reconciliation job never sees a
half-written file. Credentials and clients come from
``RemoteFileSourceSession`` (see ``multi_file_remote.py``).
"""
from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlparse

from api.services.file_source import resolve_allowed_path
from etl_framework.reconciliation.file_mapping import DiscoveredFile

CHUNK_SIZE = 1024 * 1024
PART_SUFFIX = ".part"


class TransferError(Exception):
    """A failure that should surface as a FAILED step: a plan-time rejection
    (collision, unsafe path, no files) or a per-file copy failure. Connection
    and credential problems are left as their original exception type so the
    executor reports them as ERROR."""


def _copy_stream(stream: Any, sink: Any) -> None:
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            return
        sink.write(chunk)


# -- Local -------------------------------------------------------------------

class LocalEndpoint:
    kind = "local"

    def __init__(self, root: str) -> None:
        self.credentials_ref = None
        # Raises HTTPException(400) when root is outside the server allowlist.
        self.root = resolve_allowed_path(root)

    def relative_of(self, file: DiscoveredFile) -> str:
        try:
            return Path(file.path).relative_to(self.root).as_posix()
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
        return str(target)

    def identity(self, path: str) -> tuple:
        return ("local", None, os.path.normcase(str(Path(path).resolve())))

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def size(self, path: str) -> int:
        return Path(path).stat().st_size

    def open_read(self, path: str):
        return open(path, "rb")

    def write(self, path: str, stream: Any) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + PART_SUFFIX)
        try:
            with open(part, "wb") as fh:
                _copy_stream(stream, fh)
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    def delete(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)
```

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/unit/test_file_transfer.py -v`
Expected: all `test_local_endpoint_*` PASS.

- [ ] **Step 6: Commit**

```bash
cd /c/atom
git add api/services/file_transfer.py tests/unit/test_file_transfer.py tests/helpers/fake_sftp.py
git commit -m "feat: add file_transfer module with LocalEndpoint and shared SFTP fake

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: `S3Endpoint`

**Files:**
- Modify: `api/services/file_transfer.py`
- Test: `tests/unit/test_file_transfer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_transfer.py`:

```python
# -- S3Endpoint --------------------------------------------------------------

@pytest.fixture
def s3_raw():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="bkt")
        yield raw


def test_s3_endpoint_rejects_non_s3_root(s3_raw):
    with pytest.raises(ValueError, match="s3://bucket/prefix"):
        ft.S3Endpoint("/not/s3", s3_raw, "prof")


def test_s3_endpoint_relative_of_strips_prefix_and_unquotes(s3_raw):
    endpoint = ft.S3Endpoint("s3://bkt/in", s3_raw, "prof")
    file = DiscoveredFile(path="s3://bkt/in/sub/a%20b.csv", file_name="a b.csv", tokens={})
    assert endpoint.relative_of(file) == "sub/a b.csv"


def test_s3_endpoint_destination_for_quotes_key(s3_raw):
    endpoint = ft.S3Endpoint("s3://bkt/out", s3_raw, "prof")
    assert endpoint.destination_for("sub/a b#1.csv") == "s3://bkt/out/sub/a%20b%231.csv"


def test_s3_endpoint_write_read_exists_size_delete(s3_raw):
    endpoint = ft.S3Endpoint("s3://bkt/out", s3_raw, "prof")
    dest = endpoint.destination_for("sub/a b.csv")

    assert not endpoint.exists(dest)
    endpoint.write(dest, io.BytesIO(b"id\n1\n"))

    assert endpoint.exists(dest)
    assert endpoint.size(dest) == 5
    assert s3_raw.get_object(Bucket="bkt", Key="out/sub/a b.csv")["Body"].read() == b"id\n1\n"
    body = endpoint.open_read(dest)
    try:
        assert body.read() == b"id\n1\n"
    finally:
        body.close()

    endpoint.delete(dest)
    assert not endpoint.exists(dest)


def test_s3_endpoint_identity_includes_credentials_ref(s3_raw):
    a = ft.S3Endpoint("s3://bkt/out", s3_raw, "prof-a")
    b = ft.S3Endpoint("s3://bkt/out", s3_raw, "prof-b")
    path = "s3://bkt/out/x.csv"
    assert a.identity(path) != b.identity(path)
    assert a.identity(path) == a.identity("s3://bkt/out/x.csv")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_file_transfer.py -k s3_endpoint -v`
Expected: FAIL with `AttributeError: module 'api.services.file_transfer' has no attribute 'S3Endpoint'`.

- [ ] **Step 3: Implement `S3Endpoint`**

Append to `api/services/file_transfer.py`:

```python
# -- S3 ----------------------------------------------------------------------

class S3Endpoint:
    """Paths are ``s3://bucket/key`` URIs with the key percent-quoted, the
    same shape ``discover_s3_files`` puts in ``DiscoveredFile.path``."""

    kind = "s3"

    def __init__(self, root: str, client: Any, credentials_ref: str | None) -> None:
        parsed = urlparse(root)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError("S3 root must be s3://bucket/prefix")
        self.client = client
        self.credentials_ref = credentials_ref
        self.bucket = parsed.netloc
        prefix = unquote(parsed.path.lstrip("/"))
        self.prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"

    @staticmethod
    def _split(uri: str) -> tuple[str, str]:
        parsed = urlparse(uri)
        return parsed.netloc, unquote(parsed.path.lstrip("/"))

    def relative_of(self, file: DiscoveredFile) -> str:
        _, key = self._split(file.path)
        if self.prefix and key.startswith(self.prefix):
            return key[len(self.prefix):]
        return key

    def destination_for(self, relative: str) -> str:
        return f"s3://{self.bucket}/{quote(self.prefix + relative, safe='/')}"

    def identity(self, path: str) -> tuple:
        bucket, key = self._split(path)
        return ("s3", self.credentials_ref, f"{bucket}/{key}")

    def exists(self, path: str) -> bool:
        import botocore.exceptions

        bucket, key = self._split(path)
        try:
            self.client.head_object(Bucket=bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def size(self, path: str) -> int:
        bucket, key = self._split(path)
        return int(self.client.head_object(Bucket=bucket, Key=key)["ContentLength"])

    def open_read(self, path: str):
        bucket, key = self._split(path)
        return self.client.get_object(Bucket=bucket, Key=key)["Body"]

    def write(self, path: str, stream: Any) -> None:
        bucket, key = self._split(path)
        self.client.upload_fileobj(stream, bucket, key)

    def delete(self, path: str) -> None:
        bucket, key = self._split(path)
        self.client.delete_object(Bucket=bucket, Key=key)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_file_transfer.py -k "s3_endpoint or local_endpoint" -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/atom
git add api/services/file_transfer.py tests/unit/test_file_transfer.py
git commit -m "feat: add S3Endpoint for file_transfer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: `SftpEndpoint`

**Files:**
- Modify: `api/services/file_transfer.py`
- Test: `tests/unit/test_file_transfer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_transfer.py`:

```python
# -- SftpEndpoint ------------------------------------------------------------

def test_sftp_endpoint_relative_of_and_destination_for():
    endpoint = ft.SftpEndpoint("/in/", FakeSFTP(), "vendor")
    file = DiscoveredFile(path="/in/sub/a.csv", file_name="a.csv", tokens={})
    assert endpoint.relative_of(file) == "sub/a.csv"
    assert ft.SftpEndpoint("/out", FakeSFTP(), "vendor").destination_for("a/b.csv") == "/out/a/b.csv"


def test_sftp_endpoint_write_creates_parent_dirs_and_renames_part_file():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("a/b/x.csv")

    endpoint.write(dest, io.BytesIO(b"hello"))

    assert fake.files == {"/out/a/b/x.csv": b"hello"}
    assert {"/out", "/out/a", "/out/a/b"} <= fake.dirs
    assert endpoint.exists(dest)
    assert endpoint.size(dest) == 5
    with endpoint.open_read(dest) as fh:
        assert fh.read() == b"hello"


def test_sftp_endpoint_write_overwrites_via_posix_rename():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    endpoint.write(dest, io.BytesIO(b"old"))
    endpoint.write(dest, io.BytesIO(b"newer"))
    assert fake.files == {"/out/x.csv": b"newer"}


def test_sftp_endpoint_write_falls_back_when_posix_rename_unsupported():
    fake = FakeSFTP()
    fake.posix_rename_supported = False
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    endpoint.write(dest, io.BytesIO(b"old"))
    endpoint.write(dest, io.BytesIO(b"newer"))
    assert fake.files == {"/out/x.csv": b"newer"}


def test_sftp_endpoint_failed_write_removes_part_file_and_keeps_existing():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    endpoint.write(dest, io.BytesIO(b"old"))

    fake.fail_putfo_after_partial = True
    with pytest.raises(IOError, match="connection reset"):
        endpoint.write(dest, io.BytesIO(b"newer"))

    assert fake.files == {"/out/x.csv": b"old"}


def test_sftp_endpoint_exists_false_for_missing_and_delete_removes():
    fake = FakeSFTP()
    endpoint = ft.SftpEndpoint("/out", fake, "vendor")
    dest = endpoint.destination_for("x.csv")
    assert not endpoint.exists(dest)
    endpoint.write(dest, io.BytesIO(b"x"))
    endpoint.delete(dest)
    assert not endpoint.exists(dest)


def test_sftp_endpoint_identity_normalizes_path_and_keys_on_credentials_ref():
    a = ft.SftpEndpoint("/out", FakeSFTP(), "vendor")
    b = ft.SftpEndpoint("/out", FakeSFTP(), "other")
    assert a.identity("/out/sub/../x.csv") == a.identity("/out/x.csv")
    assert a.identity("/out/x.csv") != b.identity("/out/x.csv")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_file_transfer.py -k sftp_endpoint -v`
Expected: FAIL with `AttributeError: ... no attribute 'SftpEndpoint'`.

- [ ] **Step 3: Implement `SftpEndpoint`**

Append to `api/services/file_transfer.py`:

```python
# -- SFTP / SCP --------------------------------------------------------------

class SftpEndpoint:
    """``scp`` locations are normalized to ``sftp`` before reaching here (see
    ``file_transfer_spec.parse_file_transfer_params``)."""

    kind = "sftp"

    def __init__(self, root: str, client: Any, credentials_ref: str | None) -> None:
        self.client = client
        self.credentials_ref = credentials_ref
        self.root = root.rstrip("/") or "/"

    def relative_of(self, file: DiscoveredFile) -> str:
        prefix = self.root.rstrip("/") + "/"
        if file.path.startswith(prefix):
            return file.path[len(prefix):]
        return PurePosixPath(file.path).name

    def destination_for(self, relative: str) -> str:
        return posixpath.join(self.root, relative)

    def identity(self, path: str) -> tuple:
        return ("sftp", self.credentials_ref, posixpath.normpath(path))

    def exists(self, path: str) -> bool:
        try:
            self.client.stat(path)
        except FileNotFoundError:
            return False
        return True

    def size(self, path: str) -> int:
        return int(self.client.stat(path).st_size)

    def open_read(self, path: str):
        return self.client.open(path, "rb")

    def _make_dirs(self, directory: str) -> None:
        current = ""
        for part in PurePosixPath(directory).parts:
            current = posixpath.join(current, part) if current else part
            try:
                self.client.stat(current)
            except FileNotFoundError:
                self.client.mkdir(current)

    def write(self, path: str, stream: Any) -> None:
        self._make_dirs(posixpath.dirname(path))
        part = path + PART_SUFFIX
        try:
            self.client.putfo(stream, part)
        except BaseException:
            try:
                self.client.remove(part)
            except Exception:
                pass
            raise
        try:
            self.client.posix_rename(part, path)
        except IOError:
            # Server has no posix-rename extension: plain rename refuses to
            # replace an existing file, so remove it first.
            if self.exists(path):
                self.client.remove(path)
            self.client.rename(part, path)

    def delete(self, path: str) -> None:
        self.client.remove(path)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_file_transfer.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/atom
git add api/services/file_transfer.py tests/unit/test_file_transfer.py
git commit -m "feat: add SftpEndpoint for file_transfer

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: `plan_transfer`

**Files:**
- Modify: `api/services/file_transfer.py`
- Test: `tests/unit/test_file_transfer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_transfer.py`:

```python
# -- plan_transfer -----------------------------------------------------------

def _make_files(root: Path, *relatives: str) -> list[DiscoveredFile]:
    files = []
    for relative in relatives:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"data-" + relative.encode())
        files.append(DiscoveredFile(path=str(path), file_name=path.name, tokens={}))
    return files


def _local_pair(allowed_dir: Path):
    return ft.LocalEndpoint(str(allowed_dir / "src")), ft.LocalEndpoint(str(allowed_dir / "dst"))


def _relative_destinations(entries, dst_root: Path) -> list[str]:
    return [Path(entry.destination).relative_to(dst_root).as_posix() for entry in entries]


def test_plan_flattens_into_destination_root_by_default(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "sub/b.csv")

    plan = ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=False)

    assert _relative_destinations(plan.to_copy, allowed_dir / "dst") == ["a.csv", "b.csv"]
    assert plan.skipped == []


def test_plan_preserves_relative_structure_when_asked(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "sub/b.csv")

    plan = ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=True)

    assert _relative_destinations(plan.to_copy, allowed_dir / "dst") == ["a.csv", "sub/b.csv"]


def test_plan_rejects_duplicate_destination_when_flattening(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "x/a.csv", "y/a.csv")

    with pytest.raises(ft.TransferError, match="duplicate destination"):
        ft.plan_transfer(files, source, destination, on_exists="overwrite", preserve_structure=False)


def test_plan_rejects_source_and_destination_being_the_same_object(allowed_dir):
    source = ft.LocalEndpoint(str(allowed_dir / "src"))
    files = _make_files(allowed_dir / "src", "a.csv")

    with pytest.raises(ft.TransferError, match="same object"):
        ft.plan_transfer(files, source, source, on_exists="overwrite", preserve_structure=False)


@pytest.mark.parametrize("bad_name", ["..", ".", "a\\b.csv"])
def test_plan_rejects_unsafe_relative_paths(allowed_dir, bad_name):
    source, destination = _local_pair(allowed_dir)
    files = [DiscoveredFile(path=str(allowed_dir / "src" / "x.csv"), file_name=bad_name, tokens={})]

    with pytest.raises(ft.TransferError, match="unsafe"):
        ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=False)


def test_plan_on_exists_fail_aborts_before_anything_is_planned_for_writing(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "b.csv")
    (allowed_dir / "dst").mkdir()
    (allowed_dir / "dst" / "b.csv").write_bytes(b"already here")

    with pytest.raises(ft.TransferError, match=r"already contains 1 file\(s\)"):
        ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=False)

    assert not (allowed_dir / "dst" / "a.csv").exists()


def test_plan_on_exists_skip_drops_existing_files(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "b.csv")
    (allowed_dir / "dst").mkdir()
    (allowed_dir / "dst" / "b.csv").write_bytes(b"already here")

    plan = ft.plan_transfer(files, source, destination, on_exists="skip", preserve_structure=False)

    assert _relative_destinations(plan.to_copy, allowed_dir / "dst") == ["a.csv"]
    assert _relative_destinations(plan.skipped, allowed_dir / "dst") == ["b.csv"]


def test_plan_on_exists_overwrite_keeps_every_file(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "b.csv")
    (allowed_dir / "dst").mkdir()
    (allowed_dir / "dst" / "b.csv").write_bytes(b"already here")

    plan = ft.plan_transfer(files, source, destination, on_exists="overwrite", preserve_structure=False)

    assert _relative_destinations(plan.to_copy, allowed_dir / "dst") == ["a.csv", "b.csv"]
    assert plan.skipped == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_file_transfer.py -k plan_ -v`
Expected: FAIL with `AttributeError: ... no attribute 'plan_transfer'`.

- [ ] **Step 3: Implement planning**

Append to `api/services/file_transfer.py`:

```python
# -- Planning ----------------------------------------------------------------

@dataclass(frozen=True)
class PlannedCopy:
    source: DiscoveredFile
    destination: str
    relative: str


@dataclass
class TransferPlan:
    to_copy: list[PlannedCopy] = field(default_factory=list)
    skipped: list[PlannedCopy] = field(default_factory=list)


def _validate_relative(relative: str) -> str:
    unsafe = (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(part in ("", ".", "..") for part in relative.split("/"))
    )
    if unsafe:
        raise TransferError(f"unsafe destination path '{relative}' -- refusing to write outside the destination root")
    return relative


def plan_transfer(
    files: list[DiscoveredFile],
    source: Any,
    destination: Any,
    *,
    on_exists: str,
    preserve_structure: bool,
) -> TransferPlan:
    """Decide what to copy without writing anything. Raises ``TransferError``
    when the plan is unsafe or (``on_exists="fail"``) would collide with
    existing destination files."""
    entries: list[PlannedCopy] = []
    seen: dict[tuple, str] = {}
    for file in files:
        relative = source.relative_of(file) if preserve_structure else file.file_name
        _validate_relative(relative)
        target = destination.destination_for(relative)
        target_identity = destination.identity(target)
        if source.identity(file.path) == target_identity:
            raise TransferError(f"source and destination are the same object: {file.path}")
        if target_identity in seen:
            raise TransferError(
                f"duplicate destination '{target}' for '{seen[target_identity]}' and '{file.path}' -- "
                "enable preserve_structure or narrow the pattern"
            )
        seen[target_identity] = file.path
        entries.append(PlannedCopy(source=file, destination=target, relative=relative))

    plan = TransferPlan()
    existing: list[PlannedCopy] = []
    for entry in entries:
        if on_exists == "overwrite" or not destination.exists(entry.destination):
            plan.to_copy.append(entry)
        elif on_exists == "skip":
            plan.skipped.append(entry)
        else:
            existing.append(entry)
    if existing:
        shown = ", ".join(entry.destination for entry in existing[:5])
        more = f" and {len(existing) - 5} more" if len(existing) > 5 else ""
        raise TransferError(
            f"destination already contains {len(existing)} file(s) (on_exists=fail): {shown}{more}"
        )
    return plan
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_file_transfer.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/atom
git add api/services/file_transfer.py tests/unit/test_file_transfer.py
git commit -m "feat: add file_transfer planning with on_exists and path safety checks

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: `run_transfer`

**Files:**
- Modify: `api/services/file_transfer.py`
- Test: `tests/unit/test_file_transfer.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_file_transfer.py`:

```python
# -- run_transfer ------------------------------------------------------------

class _MemorySource:
    def __init__(self, contents: dict[str, bytes], fail_on: str | None = None) -> None:
        self.contents = contents
        self.fail_on = fail_on
        self.opened: list[str] = []

    def open_read(self, path: str):
        self.opened.append(path)
        if path == self.fail_on:
            raise OSError(f"cannot read {path}")
        return io.BytesIO(self.contents[path])


class _MemoryDestination:
    def __init__(self, size_override: int | None = None) -> None:
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.size_override = size_override

    def write(self, path: str, stream) -> None:
        data = b""
        while True:
            chunk = stream.read(4)
            if not chunk:
                break
            data += chunk
        self.files[path] = data

    def size(self, path: str) -> int:
        return self.size_override if self.size_override is not None else len(self.files[path])

    def delete(self, path: str) -> None:
        self.deleted.append(path)
        self.files.pop(path, None)


def _entry(name: str) -> ft.PlannedCopy:
    return ft.PlannedCopy(
        source=DiscoveredFile(path=f"/src/{name}", file_name=name, tokens={}),
        destination=f"/dst/{name}",
        relative=name,
    )


def test_run_transfer_copies_every_planned_file_and_counts_bytes():
    source = _MemorySource({"/src/a.csv": b"aaaa", "/src/b.csv": b"bb"})
    destination = _MemoryDestination()
    plan = ft.TransferPlan(to_copy=[_entry("a.csv"), _entry("b.csv")], skipped=[_entry("c.csv")])

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.error is None and outcome.failed_file is None
    assert destination.files == {"/dst/a.csv": b"aaaa", "/dst/b.csv": b"bb"}
    assert outcome.bytes_copied == 6
    assert [item["action"] for item in outcome.copied] == ["copied", "copied"]
    assert outcome.copied[0] == {"source": "/src/a.csv", "destination": "/dst/a.csv", "bytes": 4, "action": "copied"}
    assert outcome.skipped == [{"source": "/src/c.csv", "destination": "/dst/c.csv", "bytes": 0, "action": "skipped"}]


def test_run_transfer_stops_at_first_failure_and_keeps_earlier_copies():
    source = _MemorySource({"/src/a.csv": b"a", "/src/b.csv": b"b", "/src/c.csv": b"c"}, fail_on="/src/b.csv")
    destination = _MemoryDestination()
    plan = ft.TransferPlan(to_copy=[_entry("a.csv"), _entry("b.csv"), _entry("c.csv")])

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.failed_file == "/src/b.csv"
    assert "cannot read /src/b.csv" in outcome.error
    assert [item["source"] for item in outcome.copied] == ["/src/a.csv"]
    assert "/dst/c.csv" not in destination.files
    assert "/src/c.csv" not in source.opened


def test_run_transfer_deletes_destination_and_fails_on_size_mismatch():
    source = _MemorySource({"/src/a.csv": b"aaaa"})
    destination = _MemoryDestination(size_override=3)
    plan = ft.TransferPlan(to_copy=[_entry("a.csv")])

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.failed_file == "/src/a.csv"
    assert "size mismatch" in outcome.error and "4 bytes" in outcome.error
    assert destination.deleted == ["/dst/a.csv"]
    assert outcome.copied == []


def test_run_transfer_end_to_end_local_to_local(allowed_dir):
    source, destination = _local_pair(allowed_dir)
    files = _make_files(allowed_dir / "src", "a.csv", "sub/b.csv")
    plan = ft.plan_transfer(files, source, destination, on_exists="fail", preserve_structure=True)

    outcome = ft.run_transfer(plan, source, destination)

    assert outcome.error is None
    assert (allowed_dir / "dst" / "a.csv").read_bytes() == b"data-a.csv"
    assert (allowed_dir / "dst" / "sub" / "b.csv").read_bytes() == b"data-sub/b.csv"
    assert list((allowed_dir / "dst").rglob("*.part")) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_file_transfer.py -k run_transfer -v`
Expected: FAIL with `AttributeError: ... no attribute 'run_transfer'`.

- [ ] **Step 3: Implement `run_transfer`**

Append to `api/services/file_transfer.py`:

```python
# -- Execution ---------------------------------------------------------------

class _CountingReader:
    """Wraps a readable stream and counts the bytes read through it."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        data = self._raw.read(size)
        self.count += len(data)
        return data


@dataclass
class TransferOutcome:
    copied: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    bytes_copied: int = 0
    failed_file: str | None = None
    error: str | None = None


def _copy_one(entry: PlannedCopy, source: Any, destination: Any) -> int:
    reader = source.open_read(entry.source.path)
    try:
        counting = _CountingReader(reader)
        destination.write(entry.destination, counting)
    finally:
        close = getattr(reader, "close", None)
        if callable(close):
            close()
    actual = destination.size(entry.destination)
    if actual != counting.count:
        try:
            destination.delete(entry.destination)
        except Exception:
            pass
        raise TransferError(
            f"size mismatch after copy: streamed {counting.count} bytes but destination holds {actual}"
        )
    return counting.count


def run_transfer(plan: TransferPlan, source: Any, destination: Any) -> TransferOutcome:
    """Copy every planned file, one at a time. Stops at the first failure;
    files copied before it stay in place (no rollback)."""
    outcome = TransferOutcome(skipped=[
        {"source": entry.source.path, "destination": entry.destination, "bytes": 0, "action": "skipped"}
        for entry in plan.skipped
    ])
    for entry in plan.to_copy:
        try:
            copied_bytes = _copy_one(entry, source, destination)
        except Exception as exc:
            outcome.failed_file = entry.source.path
            outcome.error = f"{entry.source.path}: {exc}"
            return outcome
        outcome.copied.append({
            "source": entry.source.path,
            "destination": entry.destination,
            "bytes": copied_bytes,
            "action": "copied",
        })
        outcome.bytes_copied += copied_bytes
    return outcome
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/unit/test_file_transfer.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/atom
git add api/services/file_transfer.py tests/unit/test_file_transfer.py
git commit -m "feat: add streaming run_transfer with size verification

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: `build_endpoint` and `RunExecutor` integration

**Files:**
- Modify: `api/services/file_transfer.py`
- Modify: `api/services/run_executor.py:554-555` (dispatch) and after `_execute_file_watcher` (line ~1648)
- Test: `tests/unit/test_run_executor_file_transfer.py`

- [ ] **Step 1: Write the failing executor tests**

Create `tests/unit/test_run_executor_file_transfer.py`:

```python
from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.repository import database as _db_module
from etl_framework.repository.database import Base
from etl_framework.runner.state import TestStatus
from tests.helpers.fake_sftp import FakeSFTP


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # RemoteFileSourceSession resolves profiles on its own SessionLocal().
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    with Session(engine) as db:
        yield db


@pytest.fixture
def allowed_dir(tmp_path, monkeypatch):
    from api.services import file_source

    base = tmp_path / "allowed"
    base.mkdir()
    monkeypatch.setattr(file_source, "_UPLOAD_BASE", base.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (base.resolve(),))
    return base.resolve()


def executor(db_session):
    return RunExecutor(
        db=db_session, run_id="run-1", source_env="qa", target_env="prod",
        job_sequence=[], run_settings=RunSettings(use_live_connections=True),
        config_snapshot={},
    )


def transfer_job(source, destination, **params):
    return JobDefinition(
        name="stage_sales",
        job_type="file_transfer",
        params={"source": source, "destination": destination, **params},
    )


def local_job(allowed_dir: Path, **params):
    return transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
        **params,
    )


def seed_sources(allowed_dir: Path):
    src = allowed_dir / "src"
    src.mkdir()
    (src / "sales_east.csv").write_bytes(b"id\n1\n")
    (src / "sales_west.csv").write_bytes(b"id\n2\n22\n")
    (src / "ignore.txt").write_bytes(b"nope")


def test_build_case_dispatches_file_transfer_job_type(db_session, allowed_dir):
    seed_sources(allowed_dir)
    result = executor(db_session)._build_case(local_job(allowed_dir))()
    assert result.status == TestStatus.PASSED


def test_local_to_local_copies_matching_files_and_reports_summary(db_session, allowed_dir):
    seed_sources(allowed_dir)

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.PASSED
    assert sorted(p.name for p in (allowed_dir / "dst").iterdir()) == ["sales_east.csv", "sales_west.csv"]
    assert (allowed_dir / "dst" / "sales_west.csv").read_bytes() == b"id\n2\n22\n"
    assert result.mismatch_summary["copied"] == 2
    assert result.mismatch_summary["skipped"] == 0
    assert result.mismatch_summary["bytes"] == 13
    assert result.mismatch_summary["files_truncated"] is False
    assert result.source_row_count == 2 and result.matched_count == 2
    assert result.data_artifact_path == str(allowed_dir / "dst")
    assert result.mismatches == []


def test_second_run_with_default_on_exists_fails_and_reports_collision(db_session, allowed_dir):
    seed_sources(allowed_dir)
    executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.FAILED
    assert "already contains 2 file(s)" in result.mismatch_summary["error"]
    assert result.data_artifact_path is None
    assert len(result.mismatches) == 1


def test_second_run_with_skip_is_idempotent(db_session, allowed_dir):
    seed_sources(allowed_dir)
    executor(db_session)._execute_file_transfer(local_job(allowed_dir, on_exists="skip"))

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir, on_exists="skip"))

    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["copied"] == 0
    assert result.mismatch_summary["skipped"] == 2


def test_overwrite_replaces_changed_destination_file(db_session, allowed_dir):
    seed_sources(allowed_dir)
    executor(db_session)._execute_file_transfer(local_job(allowed_dir))
    (allowed_dir / "src" / "sales_east.csv").write_bytes(b"id\n1\n99\n")

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir, on_exists="overwrite"))

    assert result.status == TestStatus.PASSED
    assert (allowed_dir / "dst" / "sales_east.csv").read_bytes() == b"id\n1\n99\n"


def test_no_matching_files_fails_with_clear_message(db_session, allowed_dir):
    (allowed_dir / "src").mkdir()

    result = executor(db_session)._execute_file_transfer(local_job(allowed_dir))

    assert result.status == TestStatus.FAILED
    assert "no files matching 'sales_{region}.csv'" in result.mismatch_summary["error"]


def test_recursive_with_preserve_structure_keeps_subfolders(db_session, allowed_dir):
    src = allowed_dir / "src"
    (src / "2026" / "09").mkdir(parents=True)
    (src / "top.csv").write_bytes(b"1")
    (src / "2026" / "09" / "deep.csv").write_bytes(b"22")
    job = transfer_job(
        {"kind": "local", "root": str(src), "pattern": "*.csv"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
        recursive=True, preserve_structure=True,
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED
    assert (allowed_dir / "dst" / "top.csv").read_bytes() == b"1"
    assert (allowed_dir / "dst" / "2026" / "09" / "deep.csv").read_bytes() == b"22"


def test_destination_outside_allowlist_is_an_error_not_a_failure(db_session, allowed_dir, tmp_path):
    seed_sources(allowed_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    job = transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "local", "root": str(outside)},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.ERROR
    assert "Invalid file path" in result.mismatch_summary["error"]
    assert list(outside.iterdir()) == []


def test_local_to_s3_uploads_objects(db_session, allowed_dir, monkeypatch):
    seed_sources(allowed_dir)
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="bkt")
        monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db, spec: None)
        monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", lambda profile, spec: raw)
        job = transfer_job(
            {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
            {"kind": "s3", "root": "s3://bkt/staged", "credentials_ref": "aws"},
        )

        result = executor(db_session)._execute_file_transfer(job)

        assert result.status == TestStatus.PASSED
        keys = sorted(o["Key"] for o in raw.list_objects_v2(Bucket="bkt")["Contents"])
        assert keys == ["staged/sales_east.csv", "staged/sales_west.csv"]
        assert raw.get_object(Bucket="bkt", Key="staged/sales_east.csv")["Body"].read() == b"id\n1\n"


def test_s3_to_local_downloads_objects(db_session, allowed_dir, monkeypatch):
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="bkt")
        raw.put_object(Bucket="bkt", Key="in/sales_east.csv", Body=b"id\n1\n")
        raw.put_object(Bucket="bkt", Key="in/readme.txt", Body=b"skip")
        monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db, spec: None)
        monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", lambda profile, spec: raw)
        job = transfer_job(
            {"kind": "s3", "root": "s3://bkt/in", "pattern": "sales_{region}.csv", "credentials_ref": "aws"},
            {"kind": "local", "root": str(allowed_dir / "dst")},
        )

        result = executor(db_session)._execute_file_transfer(job)

        assert result.status == TestStatus.PASSED
        assert (allowed_dir / "dst" / "sales_east.csv").read_bytes() == b"id\n1\n"


def test_sftp_to_s3_remote_to_remote(db_session, monkeypatch):
    fake = FakeSFTP()
    fake.dirs.add("/exports")
    fake.files["/exports/sales_east.csv"] = b"id\n1\n"
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="bkt")
        monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db, spec: None)
        monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", lambda profile, spec: raw)
        monkeypatch.setattr("api.services.multi_file_remote.build_sftp_client", lambda profile, spec: fake)
        job = transfer_job(
            {"kind": "scp", "root": "/exports", "pattern": "sales_{region}.csv", "credentials_ref": "vendor"},
            {"kind": "s3", "root": "s3://bkt/mirror", "credentials_ref": "aws"},
        )

        result = executor(db_session)._execute_file_transfer(job)

        assert result.status == TestStatus.PASSED
        assert raw.get_object(Bucket="bkt", Key="mirror/sales_east.csv")["Body"].read() == b"id\n1\n"


def test_local_to_sftp_writes_through_part_file(db_session, allowed_dir, monkeypatch):
    seed_sources(allowed_dir)
    fake = FakeSFTP()
    monkeypatch.setattr("api.services.multi_file_remote.resolve_file_server_profile", lambda db, spec: None)
    monkeypatch.setattr("api.services.multi_file_remote.build_sftp_client", lambda profile, spec: fake)
    job = transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "sftp", "root": "/inbound", "credentials_ref": "vendor"},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED
    assert fake.files == {
        "/inbound/sales_east.csv": b"id\n1\n",
        "/inbound/sales_west.csv": b"id\n2\n22\n",
    }


def test_missing_profile_reference_is_an_error(db_session, allowed_dir):
    seed_sources(allowed_dir)
    job = transfer_job(
        {"kind": "local", "root": str(allowed_dir / "src"), "pattern": "sales_{region}.csv"},
        {"kind": "sftp", "root": "/inbound", "credentials_ref": "does-not-exist"},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.ERROR
    assert "No file server profile named 'does-not-exist'" in result.mismatch_summary["error"]


def test_summary_caps_file_list_but_reports_true_counts(db_session, allowed_dir):
    src = allowed_dir / "src"
    src.mkdir()
    for index in range(105):
        (src / f"sales_{index:03d}.csv").write_bytes(b"x")
    job = transfer_job(
        {"kind": "local", "root": str(src), "pattern": "sales_{n}.csv"},
        {"kind": "local", "root": str(allowed_dir / "dst")},
    )

    result = executor(db_session)._execute_file_transfer(job)

    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["copied"] == 105
    assert len(result.mismatch_summary["files"]) == 100
    assert result.mismatch_summary["files_truncated"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/unit/test_run_executor_file_transfer.py -v`
Expected: FAIL with `AttributeError: 'RunExecutor' object has no attribute '_execute_file_transfer'` (and `_build_case` returning the wrong handler).

- [ ] **Step 3: Add `build_endpoint`**

Append to `api/services/file_transfer.py`:

```python
# -- Wiring ------------------------------------------------------------------

def build_endpoint(session: Any, spec: Any):
    """Endpoint for a ``FileSourceSpec``. ``session`` is a
    ``RemoteFileSourceSession`` (supplies cached, credential-resolved clients)."""
    if spec.kind == "local":
        return LocalEndpoint(spec.root)
    client = session.client_for(spec)
    if spec.kind == "s3":
        return S3Endpoint(spec.root, client, spec.credentials_ref)
    if spec.kind == "sftp":
        return SftpEndpoint(spec.root, client, spec.credentials_ref)
    raise ValueError(f"Unsupported file_transfer location kind: {spec.kind}")
```

- [ ] **Step 4: Dispatch the job type in `RunExecutor._build_case`**

In `api/services/run_executor.py`, directly after the file_watcher dispatch (lines 554-555):

```python
        if job.job_type == "file_watcher":
            return self._build_case_file_watcher(job)
```

add:

```python
        if job.job_type == "file_transfer":
            return self._build_case_file_transfer(job)
```

- [ ] **Step 5: Add the executor methods**

In `api/services/run_executor.py`, insert this block immediately before the `# -- Freshness ---` comment (after `_execute_file_watcher`):

```python
    # -- File Transfer ----------------------------------------------------------

    _FILE_TRANSFER_SUMMARY_FILE_LIMIT = 100

    def _build_case_file_transfer(self, job: JobDefinition):
        def run_file_transfer() -> ReconciliationResult:
            return self._execute_file_transfer(job)
        return run_file_transfer

    def _file_transfer_result(
        self,
        job: JobDefinition,
        status: TestStatus,
        executed_at: datetime,
        duration_seconds: float,
        total: int = 0,
        destination_root: str | None = None,
        outcome: Any = None,
        error: str | None = None,
    ) -> ReconciliationResult:
        copied = outcome.copied if outcome else []
        skipped = outcome.skipped if outcome else []
        listed = copied + skipped
        limit = self._FILE_TRANSFER_SUMMARY_FILE_LIMIT
        mismatch_summary: dict[str, Any] = {
            "copied": len(copied),
            "skipped": len(skipped),
            "bytes": outcome.bytes_copied if outcome else 0,
            "files": listed[:limit],
            "files_truncated": len(listed) > limit,
        }
        mismatches: list[MismatchRecord] = []
        if error is not None:
            mismatch_summary["error"] = error
            if outcome is not None and outcome.failed_file:
                mismatch_summary["failed_file"] = outcome.failed_file
            mismatches.append(MismatchRecord({"job": job.name}, "file_transfer", "ok", error, "file_transfer_error"))
        done = len(listed)
        return ReconciliationResult(
            query_name=job.name,
            source_env=self._source_env,
            target_env=self._target_env,
            source_row_count=total,
            target_row_count=done,
            matched_count=done,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=len(mismatches),
            mismatches=mismatches,
            status=status,
            executed_at=executed_at,
            duration_seconds=duration_seconds,
            data_artifact_path=destination_root if status == TestStatus.PASSED else None,
            mismatch_summary=mismatch_summary,
        )

    def _execute_file_transfer(self, job: JobDefinition) -> ReconciliationResult:
        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        from api.services.file_transfer import (
            TransferError, build_endpoint, plan_transfer, run_transfer,
        )
        from api.services.multi_file_remote import RemoteFileSourceSession
        from etl_framework.reconciliation.file_transfer_spec import parse_file_transfer_params

        total = 0
        try:
            spec = parse_file_transfer_params(job.params)
            with RemoteFileSourceSession(self._db) as session:
                files = session.discover(spec.source, recursive=spec.recursive)
                if not files:
                    raise TransferError(
                        f"no files matching '{spec.source.pattern}' under '{spec.source.root}'"
                    )
                total = len(files)
                source = build_endpoint(session, spec.source)
                destination = build_endpoint(session, spec.destination)
                plan = plan_transfer(
                    files, source, destination,
                    on_exists=spec.on_exists, preserve_structure=spec.preserve_structure,
                )
                outcome = run_transfer(plan, source, destination)
        except TransferError as exc:
            return self._file_transfer_result(
                job, TestStatus.FAILED, executed_at, time.monotonic() - t0, total=total, error=str(exc),
            )
        except Exception as exc:
            detail = getattr(exc, "detail", None) or str(exc)
            return self._file_transfer_result(
                job, TestStatus.ERROR, executed_at, time.monotonic() - t0, total=total, error=str(detail),
            )
        return self._file_transfer_result(
            job,
            TestStatus.FAILED if outcome.error else TestStatus.PASSED,
            executed_at,
            time.monotonic() - t0,
            total=total,
            destination_root=spec.destination.root,
            outcome=outcome,
            error=outcome.error,
        )

```

- [ ] **Step 6: Run to verify pass, plus the neighbouring suites**

Run: `python -m pytest tests/unit/test_run_executor_file_transfer.py tests/unit/test_run_executor_file_watcher.py tests/unit/test_file_transfer.py -v`
Expected: all PASS. (Note the `HTTPException` from a disallowed path is unwrapped through `exc.detail` so the message reads "Invalid file path. Allowed server-side base directories: ...".)

- [ ] **Step 7: Commit**

```bash
cd /c/atom
git add api/services/file_transfer.py api/services/run_executor.py tests/unit/test_run_executor_file_transfer.py
git commit -m "feat: execute file_transfer jobs in RunExecutor

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 10: Web UI job editor

**Files:**
- Modify: `frontend/features/launch.js:200-203`, `:375-376`, `:684-685`, `:804-805`
- Modify: `frontend/partials/tab-launch.html:355` and after `:796`
- Regenerate: `frontend/index.html`

- [ ] **Step 1: Add modal state defaults in `launch.js`**

After the line `fw_poll_interval_seconds: 30, fw_max_tries: '', fw_window_start: '', fw_window_end: '',` in the new-job modal state (line 202), add:

```js
        ft_source_kind: 'local', ft_source_root: '', ft_source_pattern: '', ft_source_credentials_ref: '',
        ft_dest_kind: 'local', ft_dest_root: '', ft_dest_credentials_ref: '',
        ft_on_exists: 'fail', ft_recursive: false, ft_preserve_structure: false,
```

- [ ] **Step 2: Load saved params into the edit modal**

After the line `fw_window_end: job.params?.window_end || '',` (line 375) in `openEditJobModal`, add:

```js
        ft_source_kind: job.params?.source?.kind || 'local',
        ft_source_root: job.params?.source?.root || '',
        ft_source_pattern: job.params?.source?.pattern || '',
        ft_source_credentials_ref: job.params?.source?.credentials_ref || '',
        ft_dest_kind: job.params?.destination?.kind || 'local',
        ft_dest_root: job.params?.destination?.root || '',
        ft_dest_credentials_ref: job.params?.destination?.credentials_ref || '',
        ft_on_exists: job.params?.on_exists || 'fail',
        ft_recursive: job.params?.recursive || false,
        ft_preserve_structure: job.params?.preserve_structure || false,
```

- [ ] **Step 3: Build params when saving**

After the closing brace of the `if (m.job_type === 'file_watcher') { ... }` block (line 684), add:

```js
      if (m.job_type === 'file_transfer') {
        params.source = {
          kind: m.ft_source_kind || 'local',
          root: m.ft_source_root,
          pattern: m.ft_source_pattern,
        };
        if (m.ft_source_kind !== 'local' && m.ft_source_credentials_ref) {
          params.source.credentials_ref = m.ft_source_credentials_ref;
        }
        params.destination = { kind: m.ft_dest_kind || 'local', root: m.ft_dest_root };
        if (m.ft_dest_kind !== 'local' && m.ft_dest_credentials_ref) {
          params.destination.credentials_ref = m.ft_dest_credentials_ref;
        }
        params.on_exists = m.ft_on_exists || 'fail';
        params.recursive = Boolean(m.ft_recursive);
        params.preserve_structure = Boolean(m.ft_recursive && m.ft_preserve_structure);
      }
```

- [ ] **Step 4: Gate the Save button in `canSaveJob`**

After the `file_watcher` block in `canSaveJob` (ends at line 804), before `return true;`, add:

```js
      if (m.job_type === 'file_transfer') {
        const sourceOk = Boolean(m.ft_source_root && m.ft_source_pattern)
          && (m.ft_source_kind === 'local' || Boolean(m.ft_source_credentials_ref));
        const destOk = Boolean(m.ft_dest_root)
          && (m.ft_dest_kind === 'local' || Boolean(m.ft_dest_credentials_ref));
        return sourceOk && destOk;
      }
```

- [ ] **Step 5: Add the job type option**

In `frontend/partials/tab-launch.html` after `<option value="file_watcher">file_watcher</option>` (line 355) add:

```html
            <option value="file_transfer">file_transfer</option>
```

- [ ] **Step 6: Add the File Transfer panel**

In `frontend/partials/tab-launch.html`, insert directly after the File Watcher panel's closing `</div>` (line 796, just before `<!-- Freshness params -->`):

```html
        <!-- File Transfer params -->
        <div x-show="jobModal.job_type === 'file_transfer'" class="space-y-3">
          <p class="text-xs font-medium text-slate-500">Source</p>
          <div class="grid-2">
            <div>
              <label class="field-label" for="a11y-launch-ft-source-kind">Location Kind</label>
              <select x-model="jobModal.ft_source_kind" class="field-input field-select" data-testid="job-modal-ft-source-kind-select" id="a11y-launch-ft-source-kind">
                <option value="local">local</option>
                <option value="s3">s3</option>
                <option value="sftp">sftp</option>
                <option value="scp">scp</option>
              </select>
            </div>
            <div>
              <label class="field-label" for="a11y-launch-ft-source-root">Folder / Bucket Path</label>
              <input x-model="jobModal.ft_source_root" class="field-input" placeholder="/data/inbound or s3://bucket/prefix"
                     data-testid="job-modal-ft-source-root-input" id="a11y-launch-ft-source-root" />
            </div>
            <div>
              <label class="field-label" for="a11y-launch-ft-source-pattern">File Name Pattern</label>
              <input x-model="jobModal.ft_source_pattern" class="field-input" placeholder="SALES_*.csv"
                     data-testid="job-modal-ft-source-pattern-input" id="a11y-launch-ft-source-pattern" />
            </div>
            <div x-show="jobModal.ft_source_kind !== 'local'">
              <label class="field-label" for="a11y-launch-ft-source-credentials-ref">File Server</label>
              <select x-model="jobModal.ft_source_credentials_ref" class="field-input field-select"
                      data-testid="job-modal-ft-source-credentials-ref-select" id="a11y-launch-ft-source-credentials-ref">
                <option value="">— select a file server —</option>
                <template x-for="fs in fileServers.filter(f => jobModal.ft_source_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
                  <option :value="fs.name" x-text="fs.name"></option>
                </template>
              </select>
            </div>
          </div>
          <div class="border-t border-slate-200 pt-3">
            <p class="text-xs font-medium text-slate-500 mb-2">Destination</p>
            <div class="grid-2">
              <div>
                <label class="field-label" for="a11y-launch-ft-dest-kind">Location Kind</label>
                <select x-model="jobModal.ft_dest_kind" class="field-input field-select" data-testid="job-modal-ft-dest-kind-select" id="a11y-launch-ft-dest-kind">
                  <option value="local">local</option>
                  <option value="s3">s3</option>
                  <option value="sftp">sftp</option>
                  <option value="scp">scp</option>
                </select>
              </div>
              <div>
                <label class="field-label" for="a11y-launch-ft-dest-root">Folder / Bucket Path</label>
                <input x-model="jobModal.ft_dest_root" class="field-input" placeholder="/data/staged or s3://bucket/prefix"
                       data-testid="job-modal-ft-dest-root-input" id="a11y-launch-ft-dest-root" />
              </div>
              <div x-show="jobModal.ft_dest_kind !== 'local'">
                <label class="field-label" for="a11y-launch-ft-dest-credentials-ref">File Server</label>
                <select x-model="jobModal.ft_dest_credentials_ref" class="field-input field-select"
                        data-testid="job-modal-ft-dest-credentials-ref-select" id="a11y-launch-ft-dest-credentials-ref">
                  <option value="">— select a file server —</option>
                  <template x-for="fs in fileServers.filter(f => jobModal.ft_dest_kind === 's3' ? f.kind === 's3' : (f.kind === 'sftp' || f.kind === 'scp'))" :key="fs.id">
                    <option :value="fs.name" x-text="fs.name"></option>
                  </template>
                </select>
              </div>
            </div>
          </div>
          <div class="grid-2 border-t border-slate-200 pt-3">
            <div>
              <label class="field-label" for="a11y-launch-ft-on-exists">If a file already exists at the destination</label>
              <select x-model="jobModal.ft_on_exists" class="field-input field-select" data-testid="job-modal-ft-on-exists-select" id="a11y-launch-ft-on-exists">
                <option value="fail">fail (default)</option>
                <option value="skip">skip</option>
                <option value="overwrite">overwrite</option>
              </select>
            </div>
            <div class="space-y-1">
              <label class="flex items-center gap-1 text-xs">
                <input type="checkbox" x-model="jobModal.ft_recursive" class="rounded" data-testid="job-modal-ft-recursive-checkbox" aria-label="include subfolders" /> Include subfolders
              </label>
              <label class="flex items-center gap-1 text-xs">
                <input type="checkbox" x-model="jobModal.ft_preserve_structure" :disabled="!jobModal.ft_recursive" class="rounded" data-testid="job-modal-ft-preserve-structure-checkbox" aria-label="preserve subfolder structure" /> Preserve subfolder structure
              </label>
            </div>
          </div>
          <p class="text-xs text-slate-400">
            Copies every file under the source that matches the pattern. The destination file server needs write permission. "scp" targets are treated as SFTP under the hood. Use "skip" to make a restarted sequence resume without failing on files already copied. Point a downstream reconciliation job at the same destination.
          </p>
        </div>
```

- [ ] **Step 7: Rebuild the generated HTML**

Run: `cd /c/atom && npm run build:html`
Expected: prints `Built frontend/index.html from ... + N partials`. Then `git diff --stat frontend/index.html` shows `frontend/index.html` changed (the new option and panel appear there).

- [ ] **Step 8: Sanity-check the JS parses**

Run: `node --check frontend/features/launch.js`
Expected: no output (exit 0).

- [ ] **Step 9: Commit**

```bash
cd /c/atom
git add frontend/features/launch.js frontend/partials/tab-launch.html frontend/index.html
git commit -m "feat: add file_transfer editor to the Launch job modal

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 11: Help content

**Files:**
- Modify: `frontend/help-content.js:318-324`

- [ ] **Step 1: Insert Scenario 7 and update the decision matrix**

In `frontend/help-content.js`, insert this object between the end of Scenario 6 (line 317, `},`) and the `Which Job Type When?` entry (line 318, `{`):

```js
        {
          title: 'Scenario 7: Staging Files with File Transfer Before a Reconciliation',
          text: 'Create a file_transfer job with a source (local, S3, SFTP, or SCP folder plus a file-name pattern) and a destination (local, S3, SFTP, or SCP folder). Files stream from source to destination and are size-checked; a .part temp name means watchers never see half-written files. Point a multi_file reconciliation at the same destination. on_exists controls collisions: fail (default) stops before writing anything, skip resumes idempotently after a restart, overwrite replaces. Include subfolders walks nested folders; Preserve subfolder structure keeps their relative paths. SCP is really SFTP, and the destination file server needs write permission.',
          where: 'Launch -> Job Editor -> file_transfer, then Sequences -> dependencies',
          when: 'Reconciliation or another job needs files copied from one server or bucket to another first.',
          uiMockup: { title: 'Stage vendor files to S3', elements: [
            { type: 'select', label: 'Source', value: 'SFTP: vendor-sftp' },
            { type: 'input', label: 'Pattern', value: 'SALES_*.csv' },
            { type: 'select', label: 'Destination', value: 'S3: s3://recon-staging/sales' },
            { type: 'select', label: 'If a file exists', value: 'skip' },
            { type: 'dag', label: 'wait-for-sales', value: 'stage-sales' },
            { type: 'button', label: 'Save transfer', highlight: true },
          ] },
          cli: {
            command: 'atom run "Stage Sales Files" --target-type sequence --source-env prod',
            description: 'Runs the saved sequence containing the file_transfer step. Prerequisites: ATOM_API_URL, launch-capable ATOM_API_TOKEN, file server profiles with read access to the source and write access to the destination, and the sequence.',
            params: [
              { flag: '--target-type sequence', desc: 'Launches the dependency DAG (watcher, transfer, reconciliation).' },
              { flag: '--source-env prod', desc: 'Environment for any downstream reconciliation step.' },
            ],
            sampleOutput: 'PASSED run=run-xfer7 passed=3 failed=0 error=0 exit=0',
          },
        },
```

Then in the `Which Job Type When?` entry, change the `text` string's `file_watcher to gate on arrival;` to `file_watcher to gate on arrival; file_transfer to copy files between local, S3, and SFTP/SCP locations;` and change the table row list ending `['Arrival gate', 'file_watcher']]` to `['Arrival gate', 'file_watcher'], ['Copy files', 'file_transfer']]`.

- [ ] **Step 2: Sanity-check the JS parses**

Run: `node --check frontend/help-content.js`
Expected: no output (exit 0).

- [ ] **Step 3: Commit**

```bash
cd /c/atom
git add frontend/help-content.js
git commit -m "docs: add file_transfer help scenario and job-type matrix row

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 12: Playwright e2e

**Files:**
- Create: `tests/e2e/55-file-transfer-job.spec.ts`

- [ ] **Step 1: Write the spec**

Create `tests/e2e/55-file-transfer-job.spec.ts`:

```ts
import * as fs from 'fs';
import * as path from 'path';
import { test, expect } from './fixtures';
import {
  authedContext, deleteJob, deleteFileServerByName, triggerRun, waitForTerminal,
} from './api-helpers';

// file_transfer job type: (1) the Launch job modal round-trips source/destination/
// on_exists/recursive/preserve_structure through save and edit, including a File Server
// profile picked from the dropdown; (2) a real local -> local run copies the multi_source
// fixtures, refuses to overwrite by default, and the copies reconcile clean against the
// originals (proving the staged files are byte-equal and usable as a reconciliation input).
const FIXTURE_DIR = path.join(__dirname, 'fixtures', 'data');

test.describe('55 file_transfer job', () => {
  const createdJobNames: string[] = [];
  const createdFileServerNames: string[] = [];
  const createdDirs: string[] = [];

  test.afterEach(async ({ adminToken }) => {
    const ctx = await authedContext(adminToken);
    try {
      // Jobs first: a profile still referenced by a saved job 409s on delete.
      while (createdJobNames.length) await deleteJob(ctx, createdJobNames.pop()!);
      while (createdFileServerNames.length) await deleteFileServerByName(ctx, createdFileServerNames.pop()!);
    } finally {
      await ctx.dispose();
    }
    while (createdDirs.length) fs.rmSync(createdDirs.pop()!, { recursive: true, force: true });
  });

  test('modal round-trips source, destination profile, on_exists and subfolder options', async ({ authedPage, adminToken }) => {
    const jobName = `e2e-file-transfer-${Date.now()}`;
    createdJobNames.push(jobName);
    const profileName = `e2e-ft-sftp-${Date.now()}`;
    createdFileServerNames.push(profileName);

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
    await authedPage.locator('[data-testid="job-modal-type-select"]').selectOption('file_transfer');
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();

    await authedPage.locator('[data-testid="job-modal-ft-source-root-input"]').fill('/data/inbound');
    await authedPage.locator('[data-testid="job-modal-ft-source-pattern-input"]').fill('SALES_*.csv');
    // Save stays disabled until the destination is complete.
    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeDisabled();

    await authedPage.locator('[data-testid="job-modal-ft-dest-kind-select"]').selectOption('sftp');
    await authedPage.locator('[data-testid="job-modal-ft-dest-root-input"]').fill('/staged');
    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeDisabled();
    await authedPage.locator('[data-testid="job-modal-ft-dest-credentials-ref-select"]').selectOption(profileName);
    await authedPage.locator('[data-testid="job-modal-ft-on-exists-select"]').selectOption('skip');
    await authedPage.locator('[data-testid="job-modal-ft-recursive-checkbox"]').check();
    await authedPage.locator('[data-testid="job-modal-ft-preserve-structure-checkbox"]').check();

    await expect(authedPage.locator('[data-testid="job-modal-save-btn"]')).toBeEnabled();
    await authedPage.locator('[data-testid="job-modal-save-btn"]').click();
    await expect(authedPage.locator('[data-testid="job-modal"]')).toBeHidden();

    await authedPage.locator(`[data-testid="job-row-${jobName}-edit-btn"]`).click();
    await authedPage.locator('[data-testid="job-modal-tab-settings"]').click();
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-root-input"]')).toHaveValue('/data/inbound');
    await expect(authedPage.locator('[data-testid="job-modal-ft-source-pattern-input"]')).toHaveValue('SALES_*.csv');
    await expect(authedPage.locator('[data-testid="job-modal-ft-dest-kind-select"]')).toHaveValue('sftp');
    await expect(authedPage.locator('[data-testid="job-modal-ft-dest-root-input"]')).toHaveValue('/staged');
    await expect(authedPage.locator('[data-testid="job-modal-ft-dest-credentials-ref-select"]')).toHaveValue(profileName);
    await expect(authedPage.locator('[data-testid="job-modal-ft-on-exists-select"]')).toHaveValue('skip');
    await expect(authedPage.locator('[data-testid="job-modal-ft-recursive-checkbox"]')).toBeChecked();
    await expect(authedPage.locator('[data-testid="job-modal-ft-preserve-structure-checkbox"]')).toBeChecked();
    await authedPage.locator('[data-testid="job-modal-cancel-btn"]').click();
  });

  test('runs local to local, refuses to overwrite by default, and the copies reconcile clean', async ({ adminToken }) => {
    const stamp = Date.now();
    const outDir = path.join(FIXTURE_DIR, `transfer_out_${stamp}`);
    createdDirs.push(outDir);
    const transferName = `e2e-file-transfer-run-${stamp}`;
    const reconName = `e2e-file-transfer-recon-${stamp}`;
    createdJobNames.push(reconName, transferName);

    const ctx = await authedContext(adminToken);
    try {
      const created = await ctx.post('/api/jobs', {
        data: {
          name: transferName,
          job_type: 'file_transfer',
          params: {
            source: { kind: 'local', root: path.join(FIXTURE_DIR, 'multi_source'), pattern: 'sales_{region}.csv' },
            destination: { kind: 'local', root: outDir },
          },
        },
      });
      expect(created.ok()).toBeTruthy();

      const first = await waitForTerminal(ctx, (await triggerRun(ctx, [transferName])).run_id);
      expect(String(first.status).toUpperCase()).toBe('PASSED');
      expect(fs.readdirSync(outDir).sort()).toEqual(['sales_east.csv', 'sales_west.csv']);

      // Default on_exists=fail: a second run must not silently replace staged inputs.
      const second = await waitForTerminal(ctx, (await triggerRun(ctx, [transferName])).run_id);
      expect(String(second.status).toUpperCase()).toBe('FAILED');

      // The staged copies are a valid reconciliation input: identical to the originals.
      const recon = await ctx.post('/api/jobs', {
        data: {
          name: reconName,
          job_type: 'reconciliation',
          key_columns: ['id'],
          params: {
            source_mode: 'multi_file',
            file_mapping: {
              strategy: 'explicit',
              match_on: ['region'],
              source: { kind: 'local', root: outDir, pattern: 'sales_{region}.csv' },
              target: { kind: 'local', root: path.join(FIXTURE_DIR, 'multi_source'), pattern: 'sales_{region}.csv' },
            },
          },
        },
      });
      expect(recon.ok()).toBeTruthy();
      const reconRun = await waitForTerminal(ctx, (await triggerRun(ctx, [reconName])).run_id);
      expect(String(reconRun.status).toUpperCase()).toBe('PASSED');
    } finally {
      await ctx.dispose();
    }
  });
});
```

- [ ] **Step 2: Run the spec**

Run: `rtk proxy node node_modules/@playwright/test/cli.js test tests/e2e/55-file-transfer-job.spec.ts --reporter=list`
Expected: 2 passed. If the app server fails to start on port 8055, see the playwright webServer port note (an excluded Windows port range can block it) and retry after freeing or overriding it.

- [ ] **Step 3: Commit**

```bash
cd /c/atom
git add tests/e2e/55-file-transfer-job.spec.ts
git commit -m "test(e2e): cover file_transfer editor round trip and local run

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 13: Full verification

- [ ] **Step 1: Run the entire unit suite**

Run: `python -m pytest tests/unit -q`
Expected: all pass, including the untouched `test_file_mapping.py`, `test_multi_file_remote.py`, `test_run_executor_file_watcher.py`, `test_job_validation.py`, `test_api.py` and `test_file_servers_api.py` suites. Any failure here is a regression in the recursive-discovery or validation changes; fix before continuing.

- [ ] **Step 2: Confirm the generated HTML matches the partials**

Run: `cd /c/atom && npm run build:html && git status --short frontend/`
Expected: no changes reported (index.html already committed in sync). This mirrors the CI drift check.

- [ ] **Step 3: Re-run the existing Launch and help e2e specs**

Run: `rtk proxy node node_modules/@playwright/test/cli.js test tests/e2e/32-launch-remaining-job-types.spec.ts tests/e2e/11-help.spec.ts tests/e2e/55-file-transfer-job.spec.ts --reporter=list`
Expected: all pass (the file_watcher editor still works, help still renders 14 sections and lists its scenarios).

- [ ] **Step 4: Confirm a clean tree**

Run: `cd /c/atom && git status --short`
Expected: empty output.

---

## Self-Review

**Spec coverage** (spec section -> task):
- §2 params and schema, validation in both places, `scp` normalization -> Tasks 2, 3.
- §3 optional recursion, depth cap, defaults unchanged -> Task 1.
- §4 architecture (endpoint adapters, planner, executor, `_build_case` dispatch, `resolve_allowed_path` on local sides, reuse of `RemoteFileSourceSession`) -> Tasks 4-9.
- §5 plan phase (relative path normalization, duplicate keys, same-object, `on_exists`, zero matches) -> Tasks 7, 9 (zero matches lives in the executor because it needs the source pattern for the message).
- §6 execute phase (streaming, `.part` atomic writes, mkdir, counting wrapper, size verify + cleanup, stop on first failure) -> Tasks 4-6, 8.
- §7 result mapping (`PASSED`/`FAILED`/`ERROR`, `mismatch_summary`, `data_artifact_path`) -> Task 9. The spec's `files: [...]` is capped at 100 entries with a `files_truncated` flag so a large transfer cannot bloat the stored result; this is an addition not spelled out in the spec.
- §8 frontend (job option, two location panels, profile dropdowns filtered by kind, `on_exists`, both checkboxes with preserve disabled until recursive, help text, run detail via the existing panel) -> Tasks 10, 11.
- §9 testing (unit for transfer/discovery/validation/executor, e2e) -> Tasks 1-9 and 12. The spec described the e2e as a watcher -> transfer -> reconciliation sequence. The plan covers transfer -> reconciliation with two API-launched jobs rather than a three-step sequence, because a real `file_watcher` step needs polling and adds no coverage of the new code. The live docker SFTP/MinIO run stays the optional follow-up the spec names.
- §10 out of scope: nothing planned for it.

**Placeholder scan:** none. Every code step shows the code, every run step gives the command and expected result.

**Type consistency:** endpoint interface (`relative_of`, `destination_for`, `identity`, `exists`, `size`, `open_read`, `write`, `delete`) is identical in `LocalEndpoint`, `S3Endpoint`, `SftpEndpoint` and matches what `plan_transfer`, `_copy_one` and the in-memory test fakes call. `PlannedCopy(source, destination, relative)`, `TransferPlan(to_copy, skipped)`, `TransferOutcome(copied, skipped, bytes_copied, failed_file, error)` are defined once and used unchanged in Tasks 8 and 9. `parse_file_transfer_params` / `FileTransferSpec` field names (`source`, `destination`, `on_exists`, `recursive`, `preserve_structure`) match Task 9. `RemoteFileSourceSession.discover(spec, *, recursive)` and `client_for(spec)` from Task 1 are what Task 9 calls. `_MAX_RECURSION_DEPTH` is defined in Task 1 and imported by its own test.

**Known limitation kept from the spec:** the same-object check compares `(kind, credentials_ref, path)`, so two different profiles pointing at the same host and path are not detected. That is safe here because writes go through `.part` files and the source is only replaced with identical content, so no data is lost.
