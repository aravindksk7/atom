# CrushFTP File Server Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `crushftp` file server kind that `file_transfer`, `file_watcher`, and multi-file reconciliation jobs can use over HTTPS (password session or API-key header) or SFTP, chosen per profile.

**Architecture:** One new module, `api/services/crushftp_client.py`, owns every detail of the CrushFTP HTTP API. `RemoteFileSourceSession` gains a `transport_for(spec)` helper so a `crushftp` profile on the SFTP protocol reuses the existing paramiko client and `SftpEndpoint` untouched, while the HTTPS protocol gets a new `CrushFtpEndpoint`. Profiles gain four columns; cross-field rules live in one pure validation function called from the routes.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, SQLAlchemy, `requests`, paramiko, pytest; Alpine.js; Playwright; Docker (`crushftp/crushftp11`).

**Spec:** `docs/superpowers/specs/2026-10-10-crushftp-file-server-design.md`. §10 of the spec is the wire format; treat it as the source of truth for every request the client makes.

**Conventions for every task:**
- Run tests with `python -m pytest <path> -q` (raw pytest, not a wrapper summary).
- Commit after each task with the message given. Do not stage `frontend/features/diff-search.js`; it carries an unrelated local change.
- Never hand-edit `frontend/index.html`; edit partials and run `npm run build:html`.

---

## File map

| File | Change |
|---|---|
| `etl_framework/repository/models.py` | Four columns on `FileServerProfile`. |
| `etl_framework/repository/database.py` | `_ensure_file_server_columns` at startup. |
| `etl_framework/repository/repository.py` | `api_key` secret; four fields on `ResolvedFileServerProfile`. |
| `scripts/upgrade_db_20260913_to_current.py` | Column spec and `CREATE TABLE` gain the four columns. |
| `api/schemas.py` | Profile schemas; `file_watcher` kind check. |
| `api/services/file_server_validation.py` | **New.** `file_server_profile_errors(data)`. |
| `api/services/crushftp_client.py` | **New.** `CrushFtpHttpClient`, `CrushFtpConnectError`, `build_crushftp_client`. |
| `etl_framework/reconciliation/file_mapping.py` | `discover_crushftp_files`; `crushftp` in `_parse_file_source`. |
| `etl_framework/reconciliation/file_transfer_spec.py` | `crushftp` in the kind tuples. |
| `etl_framework/runner/job_validation.py` | `crushftp` in the `file_watcher` kind check. |
| `api/services/multi_file_remote.py` | `transport_for`; client, discovery, read, location-key branches. |
| `api/services/file_transfer.py` | `CrushFtpEndpoint`; `build_endpoint`; `is_transport_error`. |
| `api/routes/file_servers.py` | Validation on create/update; `/test` for `crushftp`. |
| `api/services/config_bundle.py` | Export the four fields. |
| `frontend/partials/tab-file-servers.html`, `frontend/features/file-servers.js` | Profile form. |
| `frontend/partials/tab-launch.html` | Location-kind options and profile filters. |
| `frontend/help-content.js` | One decision-table row, one scenario. |
| `tests/helpers/fake_crushftp.py` | **New.** In-process fake server. |
| `tests/unit/test_crushftp_client.py`, `tests/unit/test_crushftp_endpoint.py`, `tests/unit/test_file_server_validation.py` | **New.** |
| `tests/integration/test_crushftp_live.py` | **New.** Environment-gated. |
| `tests/e2e/58-crushftp-file-servers.spec.ts` | **New.** |
| `docker-compose.integration.yml` | `crushftp` service. |

---

### Task 1: Profile columns, secret field, startup migration

**Files:** `etl_framework/repository/models.py`, `etl_framework/repository/database.py`, `etl_framework/repository/repository.py`, `scripts/upgrade_db_20260913_to_current.py`, `tests/unit/test_file_server_profile_repository.py`

- [ ] **Step 1: Failing tests** appended to `tests/unit/test_file_server_profile_repository.py`, using that file's existing session fixture:

```python
def test_crushftp_api_key_is_encrypted_at_rest_and_decrypted_on_resolve(db_session):
    repo = FileServerProfileRepository(db_session)
    created = repo.create({
        "name": "cloud-crush", "kind": "crushftp", "protocol": "https",
        "endpoint_url": "https://files.example.com", "auth_method": "api_key",
        "api_key_header": "X-Api-Key", "api_key": "s3cr3t-key", "tls_ca_bundle": "PEM",
    })
    assert created.api_key != "s3cr3t-key"          # stored encrypted
    resolved = repo.get_decrypted_by_name("cloud-crush")
    assert (resolved.protocol, resolved.api_key_header, resolved.api_key, resolved.tls_ca_bundle) == (
        "https", "X-Api-Key", "s3cr3t-key", "PEM",
    )
    assert "s3cr3t-key" not in repr(resolved)       # repr=False on the secret


def test_ensure_file_server_columns_adds_missing_columns_and_is_idempotent(tmp_path):
    import sqlalchemy as sa
    from etl_framework.repository.database import _ensure_file_server_columns

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE file_server_profiles (id INTEGER PRIMARY KEY, name VARCHAR(255))"))
    _ensure_file_server_columns(engine)
    _ensure_file_server_columns(engine)             # second run must not raise
    columns = {c["name"] for c in sa.inspect(engine).get_columns("file_server_profiles")}
    assert {"protocol", "api_key_header", "api_key", "tls_ca_bundle"} <= columns
```

Adapt the fixture name to the one the file already uses.

- [ ] **Step 2:** Run `python -m pytest tests/unit/test_file_server_profile_repository.py -q`. Expected: both new tests fail (`TypeError` on unknown column, `ImportError`).

- [ ] **Step 3: Implement.**
  - `models.py`, after `endpoint_url`:
    ```python
    # crushftp -- protocol picks the transport; api_key encrypted at rest, same repository
    protocol = Column(String(10), nullable=True)         # "https" | "sftp"
    api_key_header = Column(String(255), nullable=True)
    api_key = Column(Text, nullable=True)
    tls_ca_bundle = Column(Text, nullable=True)           # PEM of a private CA; not a secret
    ```
    Update the `kind` column comment to list `crushftp`.
  - `repository.py`: add `"api_key"` to `_FILE_SERVER_SECRET_FIELDS`; add to `ResolvedFileServerProfile` (defaults so existing constructors keep working): `protocol: str | None = None`, `api_key_header: str | None = None`, `api_key: str | None = field(default=None, repr=False)`, `tls_ca_bundle: str | None = None`; populate them in `get_decrypted_by_name` (`api_key` through `decrypt_secret`).
  - `database.py`: add `_ensure_file_server_columns(bind)` following `_ensure_contract_columns` (four `ensure_column` calls with `ALTER TABLE file_server_profiles ADD COLUMN …`: `protocol VARCHAR(10)`, `api_key_header VARCHAR(255)`, `api_key TEXT`, `tls_ca_bundle TEXT`), and call it from `init_db` beside the other `_ensure_*` calls.
  - `scripts/upgrade_db_20260913_to_current.py`: add the four columns to `FILE_SERVER_PROFILE_COLUMNS` and to the `CREATE TABLE file_server_profiles` statement, with the same types.

- [ ] **Step 4:** Run the file's tests plus `python -m pytest tests/unit -q -k "upgrade_db or file_server"`. Expected: all pass.
- [ ] **Step 5:** Commit `feat: crushftp columns on file server profiles`.

---

### Task 2: Profile schemas and cross-field validation

**Files:** `api/schemas.py`, `api/services/file_server_validation.py` (new), `tests/unit/test_file_server_validation.py` (new)

- [ ] **Step 1: Failing tests** in `tests/unit/test_file_server_validation.py`:

```python
import pytest

from api.services.file_server_validation import file_server_profile_errors

HTTPS_PW = {"kind": "crushftp", "protocol": "https", "endpoint_url": "https://f.example.com",
            "auth_method": "password", "username": "u", "password": "p"}
HTTPS_KEY = {"kind": "crushftp", "protocol": "https", "endpoint_url": "https://f.example.com",
             "auth_method": "api_key", "api_key_header": "X-Api-Key", "api_key": "k"}
SFTP = {"kind": "crushftp", "protocol": "sftp", "host": "f.example.com", "auth_method": "password",
        "username": "u", "password": "p"}


@pytest.mark.parametrize("data", [HTTPS_PW, HTTPS_KEY, SFTP,
                                  {**HTTPS_KEY, "username": "u", "password": "p"},
                                  {"kind": "sftp", "host": "h", "auth_method": "password"}])
def test_valid_profiles_have_no_errors(data):
    assert file_server_profile_errors(data) == []


@pytest.mark.parametrize("data, fragment", [
    ({**HTTPS_PW, "protocol": None}, "protocol"),
    ({**HTTPS_PW, "endpoint_url": None}, "endpoint_url"),
    ({**HTTPS_PW, "endpoint_url": "ftp://f.example.com"}, "https://"),
    ({**HTTPS_PW, "endpoint_url": "http://f.example.com"}, "https://"),
    ({**HTTPS_PW, "password": None}, "password"),
    ({**HTTPS_PW, "auth_method": "private_key"}, "auth_method"),
    ({**HTTPS_KEY, "api_key": None}, "api_key"),
    ({**HTTPS_KEY, "api_key_header": "Bad Header"}, "api_key_header"),
    ({**HTTPS_KEY, "api_key_header": "Cookie"}, "api_key_header"),
    ({**SFTP, "host": None}, "host"),
    ({**SFTP, "auth_method": "api_key"}, "auth_method"),
    ({"kind": "sftp", "host": "h", "api_key": "k"}, "api_key"),
])
def test_invalid_profiles_report_the_field(data, fragment):
    errors = file_server_profile_errors(data)
    assert errors and any(fragment in e for e in errors)


def test_http_url_allowed_only_with_insecure_env(monkeypatch):
    data = {**HTTPS_PW, "endpoint_url": "http://127.0.0.1:28080"}
    assert file_server_profile_errors(data)
    monkeypatch.setenv("CRUSHFTP_ALLOW_INSECURE_HTTP", "1")
    assert file_server_profile_errors(data) == []


def test_masked_secret_counts_as_present():
    assert file_server_profile_errors({**HTTPS_KEY, "api_key": "********"}) == []
```

- [ ] **Step 2:** Run; expected `ModuleNotFoundError`.

- [ ] **Step 3: Implement** `api/services/file_server_validation.py`:

```python
"""Cross-field rules for file server profiles. Pure: takes the merged profile
(the stored row overlaid with the request body) as a dict and returns a list
of messages, because an update may send only some fields."""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

_HTTP_TOKEN_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_FORBIDDEN_HEADERS = frozenset({"cookie", "host", "content-length", "transfer-encoding"})


def insecure_http_allowed() -> bool:
    return os.environ.get("CRUSHFTP_ALLOW_INSECURE_HTTP") == "1"


def file_server_profile_errors(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    kind = data.get("kind")
    if kind != "crushftp":
        if data.get("api_key") or data.get("api_key_header"):
            errors.append("api_key and api_key_header are only valid for a crushftp profile using https")
        return errors

    protocol = data.get("protocol")
    auth_method = data.get("auth_method")
    if protocol not in ("https", "sftp"):
        return ["crushftp profile requires protocol 'https' or 'sftp'"]

    if protocol == "sftp":
        if not data.get("host"):
            errors.append("crushftp sftp profile requires host")
        if auth_method not in ("password", "private_key"):
            errors.append("crushftp sftp profile auth_method must be 'password' or 'private_key'")
        if data.get("api_key") or data.get("api_key_header"):
            errors.append("api_key and api_key_header are only valid for a crushftp profile using https")
        return errors

    url = data.get("endpoint_url") or ""
    parts = urlsplit(url)
    if not url:
        errors.append("crushftp https profile requires endpoint_url")
    elif not parts.hostname or parts.scheme not in ("https", "http"):
        errors.append("crushftp endpoint_url must start with https://")
    elif parts.scheme == "http" and not insecure_http_allowed():
        errors.append("crushftp endpoint_url must start with https://")
    if auth_method == "password":
        if not data.get("username"):
            errors.append("crushftp password auth requires username")
        if not data.get("password"):
            errors.append("crushftp password auth requires password")
    elif auth_method == "api_key":
        header = data.get("api_key_header") or ""
        if not header or not _HTTP_TOKEN_RE.match(header) or header.lower() in _FORBIDDEN_HEADERS:
            errors.append("crushftp api_key_header must be a valid HTTP header name")
        if not data.get("api_key"):
            errors.append("crushftp api_key auth requires api_key")
    else:
        errors.append("crushftp https profile auth_method must be 'password' or 'api_key'")
    return errors
```

  - `api/schemas.py`: add `"crushftp"` to both profile `kind` literals; add `"api_key"` to both `auth_method` literals; add `protocol: Literal["https", "sftp"] | None = None`, `api_key_header: str | None = None`, `api_key: str | None = None`, `tls_ca_bundle: str | None = None` to `FileServerProfileCreate`, `FileServerProfileUpdate`, and (`protocol` as `str | None`) `FileServerProfileOut`.

- [ ] **Step 4:** Run `python -m pytest tests/unit/test_file_server_validation.py -q`. Expected: pass.
- [ ] **Step 5:** Commit `feat: crushftp profile schema and validation rules`.

---

### Task 3: Fake CrushFTP server

**Files:** `tests/helpers/fake_crushftp.py` (new)

A threaded `http.server` implementing exactly the behaviour in spec §10, so client tests exercise real HTTP. No test of its own; Task 4's tests are its test.

- [ ] **Step 1: Write** `FakeCrushFtp`:
  - Constructor: `FakeCrushFtp(username="atom", password="atompass", api_key_header=None, api_key=None, certfile=None, keyfile=None)`; attributes `files: dict[str, bytes]`, `dirs: set[str]` (start `{"/", "/data"}`), `requests: list[tuple[str, str, dict]]` (method, path, headers) for assertions, `redirect_all_to: str | None`, `corrupt_md5: bool`, `single_root: str | None` (when set, listing `/` answers with that folder, as in §10).
  - `start()` binds `127.0.0.1:0` on a daemon thread and sets `base_url`; `stop()`; context-manager support. With `certfile` set, wrap the socket with `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)`.
  - Authentication for a request: when `api_key_header` is configured, the header must equal `api_key` or the answer is 401; a session exists when the `CrushAuth` cookie matches one issued by a successful `login`. When `api_key_header` is configured and no username login happened, the key alone authenticates.
  - Unauthenticated: command `POST` → 404; `GET`/`HEAD` → 302 `Location: /WebInterface/login.html`; `PUT` → 401.
  - `POST /WebInterface/function/`: parse the body with `urllib.parse.parse_qsl(body, keep_blank_values=True)` **after replacing `+` with `%2B`**, so a `+`-for-space body addresses the wrong name exactly as the real server does. Commands: `login` (sets both cookies, returns the `loginResult` XML with `<c2f>`), `getXMLListing` (JSON; `{"error_msg": "error_item"}` for a missing directory or a path without a trailing slash; names with `%` rendered as `%25`), `makedir`, `rename`, `delete`, `logout`. Mutating commands require the right `c2f`, else `FAILURE:Access Denied. (c2f)`. Error text in `<response>` is percent-encoded. Rename onto an existing file fails with the §10 message.
  - `HEAD`/`GET /path`: 404 when missing; `Range` → 206 with `Content-Range`, except an empty file → 200.
  - `PUT /path`: 403 when the parent directory is missing; read a `Content-Length` or chunked body; store; 201 with the hex MD5 (a wrong one when `corrupt_md5`).
  - When `redirect_all_to` is set, every request answers 302 to it.

- [ ] **Step 2:** `python -c "from tests.helpers.fake_crushftp import FakeCrushFtp; s=FakeCrushFtp(); s.start(); print(s.base_url); s.stop()"` prints a URL.
- [ ] **Step 3:** Commit with Task 4.

---

### Task 4: `CrushFtpHttpClient`

**Files:** `api/services/crushftp_client.py` (new), `tests/unit/test_crushftp_client.py` (new)

- [ ] **Step 1: Failing tests.** One fixture starts `FakeCrushFtp` and sets `CRUSHFTP_ALLOW_INSECURE_HTTP=1`. Tests, each asserting the named behaviour:

| Test | Asserts |
|---|---|
| `test_password_login_then_list` | `login()` then `list("/data")` returns entries with `name`, `size:int`, `modified:float` (seconds), `is_dir`, `path`. |
| `test_wrong_password_raises_connect_error_without_the_password` | `CrushFtpConnectError`; message lacks the password. |
| `test_list_uses_response_path_for_single_virtual_folder` | With `single_root="/data"`, `list("/")` entries have `path` under `/data/`. |
| `test_list_missing_directory_raises_file_not_found` | `FileNotFoundError`. |
| `test_stat_size_and_missing` | `size()` equals the byte length; missing → `FileNotFoundError`; `exists()` is `True`/`False`. |
| `test_open_read_streams_and_honours_limit` | Full read equals the bytes; `limit=5` returns five bytes; empty file with a limit returns `b""`. |
| `test_upload_round_trip_and_md5_check` | Upload from a `BytesIO`, download matches; with `corrupt_md5` the upload raises `OSError` mentioning the checksum. |
| `test_upload_into_missing_directory_raises_permission_error` | `PermissionError`. |
| `test_names_with_spaces_survive_mkdir_upload_rename_delete` | Directory and file names containing a space and `+` work end to end (proves `%20` form encoding). |
| `test_rename_onto_existing_raises` | `OSError`; both files unchanged. |
| `test_percent_in_listing_name_is_unescaped` | A stored `a%b.txt` lists as `a%b.txt`. |
| `test_api_key_header_sent_on_every_request_and_no_login_without_username` | Every recorded request carries the header; no `login` command recorded. |
| `test_api_key_with_username_also_logs_in` | A `login` command is recorded and carries the header. |
| `test_wrong_api_key_raises_connect_error_without_the_key` | `CrushFtpConnectError`; message lacks the key. |
| `test_redirect_is_never_followed` | With `redirect_all_to="http://127.0.0.1:1/steal"`, `list` raises `CrushFtpConnectError`, and no request reached another origin. |
| `test_expired_session_raises_connect_error` | After the fake drops its sessions, `list` raises `CrushFtpConnectError`. |
| `test_http_url_refused_without_env` | Without the env var, `build_crushftp_client` raises `CrushFtpConnectError` naming `https://`. |
| `test_tls_ca_bundle_verifies_and_missing_bundle_rejects` | Generate a self-signed certificate for `127.0.0.1` (IP SAN) with `cryptography`; fake serves TLS; with the PEM as `tls_ca_bundle` `list` works; without it `CrushFtpConnectError`. |
| `test_unreachable_host_raises_connect_error` | Closed port → `CrushFtpConnectError`. |
| `test_close_logs_out_and_is_idempotent` | `logout` recorded once; second `close()` does not raise. |

- [ ] **Step 2:** Run; expected `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Public surface:

```python
class CrushFtpConnectError(RuntimeError): ...

@dataclass(frozen=True)
class CrushFtpEntry:
    name: str; path: str; size: int; modified: float; is_dir: bool

class CrushFtpHttpClient:
    def __init__(self, base_url, *, username=None, password=None, api_key_header=None,
                 api_key=None, ca_bundle_pem=None, timeout=(10, 120)): ...
    def login(self) -> None
    def list(self, path: str) -> list[CrushFtpEntry]
    def exists(self, path: str) -> bool
    def size(self, path: str) -> int
    def open_read(self, path: str, limit: int | None = None)   # file-like: read(n), close()
    def upload(self, path: str, stream) -> None
    def mkdir(self, path: str) -> None
    def rename(self, src: str, dst: str) -> None
    def delete(self, path: str) -> None
    def close(self) -> None

def build_crushftp_client(profile, spec) -> CrushFtpHttpClient   # validates, builds, logs in
```

  Rules the implementation must follow (each traced to spec §10 or §5):
  - One private `_request(method, url, **kw)` used by everything: sets `allow_redirects=False`, the timeout, and `verify`; wraps `requests.RequestException` as `CrushFtpConnectError`; any 3xx → `CrushFtpConnectError("…redirected…")`; 401 → `CrushFtpConnectError`. Every message passes through `_scrub`.
  - `_scrub(text)` replaces the password, the API key, and the current `CrushAuth` cookie value with `***`.
  - `_command(**fields)` builds the body as `"&".join(f"{quote(k, safe='')}={quote(v, safe='')}")`, adds `c2f` when known, posts to `/WebInterface/function/` with `Content-Type: application/x-www-form-urlencoded`; 404 → `CrushFtpConnectError("session rejected or expired")`.
  - `_response_text(xml)` extracts `<response>` with a regex and `unquote`s it. For `makedir`/`rename`/`delete`, a non-empty response is an error: text containing `Access Denied. (c2f)` → `CrushFtpConnectError`; otherwise `OSError`.
  - `login()`: no-op without a username. Otherwise send `login`; `<response>` not `success` → `CrushFtpConnectError("login rejected: …")`. Set `c2f` from `<c2f>`, falling back to the last four characters of the `CrushAuth` cookie.
  - `list(path)`: request `path` with a trailing slash; `error_msg` → `FileNotFoundError(path)`; build each entry's `path` from the response's `path`; unescape `%25` in names; `modified` = milliseconds / 1000.
  - `size`/`exists`: `HEAD` of `quote(path)`; 404 → `FileNotFoundError` / `False`.
  - `open_read`: `GET` with `stream=True`, plus `Range: bytes=0-{limit-1}` when `limit`; return `response.raw` wrapped so `read()` decodes content and `close()` releases the connection; cap total bytes at `limit`.
  - `upload`: refuse a path containing `%` (`OSError`); `PUT` a generator that reads 64 KiB blocks from `stream` and feeds an `hashlib.md5`; 403 → `PermissionError`; not 201 → `OSError`; compare the response body to the digest, mismatch → `OSError("checksum mismatch …")`.
  - `rename(src, dst)`: refuse `%` in either; `path` = the deepest common directory with a trailing slash, `name1`/`name2` relative to it.
  - `close()`: best-effort `logout` when logged in, then `session.close()`, then delete the temporary CA file. Safe to call twice.
  - TLS: when `ca_bundle_pem` is set, write it to a `NamedTemporaryFile(delete=False)` and pass its path as `verify`; otherwise `verify=True`. Never `False`.
  - `build_crushftp_client(profile, spec)`: `profile is None` → `ValueError` (same wording as `build_sftp_client`); `http://` without `insecure_http_allowed()` → `CrushFtpConnectError`; construct, `login()`, return.

- [ ] **Step 4:** Run `python -m pytest tests/unit/test_crushftp_client.py -q`. Expected: all pass.
- [ ] **Step 5:** Commit `feat: crushftp http client with fake server`.

---

### Task 5: Discovery and kind lists

**Files:** `etl_framework/reconciliation/file_mapping.py`, `etl_framework/reconciliation/file_transfer_spec.py`, `etl_framework/runner/job_validation.py`, `api/schemas.py`, tests beside the existing ones for each.

- [ ] **Step 1: Failing tests.**
  - `tests/unit/test_file_mapping.py`: `discover_crushftp_files` against a stub client whose `list(path)` returns `CrushFtpEntry`-shaped objects — non-recursive ignores subdirectories; recursive descends; pattern tokens are captured; results sorted by path; depth cap honoured.
  - One parametrised test per kind list asserting `crushftp` is accepted and still requires `credentials_ref`: `file_transfer_param_errors`, `_parse_file_source`, `job_validation`'s `file_watcher` check, and the `api/schemas.py` `file_watcher` validator. Each asserts the error message for an unknown kind now names `crushftp`.

- [ ] **Step 2:** Run those test files; expected failures on the new tests only.

- [ ] **Step 3: Implement.**
  - `file_mapping.py`, after `discover_sftp_files`:
    ```python
    def discover_crushftp_files(client: Any, root: str, pattern: str, *, recursive: bool = False) -> list[DiscoveredFile]:
        """Discover files under a CrushFTP directory over its HTTP API. Same
        contract as ``discover_sftp_files``; ``client`` is a
        ``CrushFtpHttpClient`` (or a test double with the same ``list``)."""
        regex = compile_token_pattern(pattern)
        discovered: list[DiscoveredFile] = []

        def _walk(directory: str, depth: int) -> None:
            for entry in client.list(directory):
                if entry.is_dir:
                    if not recursive:
                        continue
                    if depth < _MAX_RECURSION_DEPTH:
                        _walk(entry.path, depth + 1)
                    else:
                        logger.warning(
                            "Recursive discovery under %s stopped at the %d-level depth cap; %s was not entered",
                            root, _MAX_RECURSION_DEPTH, entry.path,
                        )
                    continue
                match = regex.match(entry.name)
                if match is None:
                    continue
                discovered.append(DiscoveredFile(path=entry.path, file_name=entry.name, tokens=match.groupdict()))

        _walk(root.rstrip("/") or "/", 0)
        return sorted(discovered, key=lambda f: f.path)
    ```
  - `_parse_file_source`: add `"crushftp"` to the set and the message.
  - `file_transfer_spec.py`: add `"crushftp"` to `LOCATION_KINDS` and `REMOTE_KINDS`; update the message.
  - `job_validation.py` and `api/schemas.py` `file_watcher` checks: add `"crushftp"` to both tuples and the message.

- [ ] **Step 4:** Run the four test files. Expected: pass.
- [ ] **Step 5:** Commit `feat: crushftp discovery and location kind`.

---

### Task 6: Session dispatch

**Files:** `api/services/multi_file_remote.py`, `tests/unit/test_multi_file_remote.py`

- [ ] **Step 1: Failing tests** (profiles created through `FileServerProfileRepository` the way the file's existing tests do; the HTTPS ones point at `FakeCrushFtp`):
  - `test_crushftp_spec_requires_crushftp_profile` — a `crushftp` spec with an `sftp` profile, and an `sftp` spec with a `crushftp` profile, both raise `ValueError` naming the kinds.
  - `test_transport_for` — `sftp` spec → `"sftp"`; `crushftp`+`sftp` profile → `"sftp"`; `crushftp`+`https` profile → `"crushftp"`; `local` → `"local"`.
  - `test_crushftp_sftp_protocol_uses_build_sftp_client` — monkeypatch `build_sftp_client`; assert it is called and `discover` goes through `discover_sftp_files`.
  - `test_crushftp_https_discover_read_and_read_text` — against the fake: `discover`, `read_file` (a CSV into a DataFrame), `read_text`.
  - `test_location_key_for_crushftp` — SFTP protocol → `("sftp", host, port, username)`; HTTPS → `("crushftp", host, port, username)`, and the API-key-only profile uses the header name in the last slot.
  - `test_close_closes_crushftp_client` — the fake records a `logout`.

- [ ] **Step 2:** Run; expected failures.

- [ ] **Step 3: Implement.**
  - `resolve_file_server_profile`: unchanged logic already rejects cross-kind use (`compatible` is `{profile.kind}` for `crushftp`); add a test-proven guard only if Step 1 shows a gap.
  - Add to `RemoteFileSourceSession`:
    ```python
    def transport_for(self, spec: FileSourceSpec) -> str:
        """The transport that actually serves ``spec``: ``spec.kind``, except a
        ``crushftp`` location, whose profile picks ``sftp`` (the paramiko path)
        or ``crushftp`` (the HTTP API)."""
        if spec.kind != "crushftp":
            return spec.kind
        self._client_for(spec)
        profile = self._profiles.get((spec.kind, spec.credentials_ref))
        return "sftp" if profile is not None and profile.protocol == "sftp" else "crushftp"
    ```
  - `_client_for`: new branch before the final `else`:
    ```python
    elif spec.kind == "crushftp":
        if profile is not None and profile.protocol == "sftp":
            self._clients[key] = build_sftp_client(profile, spec)
        else:
            from api.services.crushftp_client import build_crushftp_client
            self._clients[key] = build_crushftp_client(profile, spec)
    ```
  - `discover`, `read_file`, `_read_bytes`: compute `transport = self.transport_for(spec)` once at the top (after the `local` branch, which must not resolve a profile) and branch on it instead of `spec.kind` for `sftp`; add `crushftp` branches — `discover_crushftp_files(client, spec.root, spec.pattern, recursive=recursive)`; `read_file` reads `client.open_read(file.path)` fully into `_read_tabular_bytes`; `_read_bytes` uses `open_read(file.path, limit=_CONTENT_MATCH_READ_LIMIT)`. Close each reader in a `finally`.
  - `location_key_for`: accept `crushftp`; SFTP protocol returns the existing sftp tuple; HTTPS returns `("crushftp", hostname.lower(), port or scheme default, profile.username or profile.api_key_header or "")` parsed from `endpoint_url` with `urlsplit`.
  - Update the class docstring's kind list.

- [ ] **Step 4:** Run `python -m pytest tests/unit/test_multi_file_remote.py -q`. Expected: pass, including every pre-existing test.
- [ ] **Step 5:** Commit `feat: route crushftp locations through the file source session`.

---

### Task 7: `CrushFtpEndpoint`

**Files:** `api/services/file_transfer.py`, `tests/unit/test_crushftp_endpoint.py` (new)

- [ ] **Step 1: Failing tests** against `FakeCrushFtp` with a real `CrushFtpHttpClient`:
  - `relative_of` / `destination_for`, including `..` escape rejected with `TransferError`.
  - `identity` prefers `location_key`.
  - `exists`, `exists_many` (below and at the listing threshold), `size`.
  - `write` creates missing parent directories, leaves no `.part` behind, and the bytes match.
  - `write` over an existing file replaces it and leaves no `.bak`.
  - `write` whose rename fails restores the original file and removes the `.part`.
  - `write` to a path containing `%` raises `TransferError` before any request.
  - `delete` of a missing file does not raise.
  - `run_transfer` end to end, local → crushftp and crushftp → local, through `plan_transfer` and `build_endpoint` with a session stub.
  - `is_transport_error(CrushFtpConnectError("x"))` is `True`; `is_transport_error(PermissionError())` is `False`.

- [ ] **Step 2:** Run; expected failures.

- [ ] **Step 3: Implement** after `SftpEndpoint`:

```python
# -- CrushFTP (HTTP API) ------------------------------------------------------

class CrushFtpEndpoint:
    """A CrushFTP location reached over its HTTP API. A ``crushftp`` location
    whose profile uses the SFTP protocol is served by ``SftpEndpoint`` instead
    (see ``build_endpoint``)."""

    kind = "crushftp"

    def __init__(
        self, root: str, client: Any, credentials_ref: str | None, *, location_key: tuple | None = None,
    ) -> None:
        self.client = client
        self.credentials_ref = credentials_ref
        self.location_key = location_key
        self.root = root.rstrip("/") or "/"
```

  with `relative_of`, `destination_for`, `identity` mirroring `SftpEndpoint` (kind string `"crushftp"`), and:
  - `exists` → `client.exists`; `size` → `client.size`; `open_read` → `client.open_read`.
  - `exists_many`: below `_SFTP_LISTING_THRESHOLD` call `exists` per path; otherwise list each parent directory once, treating `FileNotFoundError` as empty.
  - `write(path, stream)`: `%` in `path` → `TransferError`; `client.mkdir(dirname)`; upload to `f"{path}.{uuid4().hex}{PART_SUFFIX}"`; if the destination exists, rename it to `f"{path}.{uuid4().hex}.bak"`, rename part → path, on failure rename the backup back and re-raise, on success delete the backup; if it does not exist, rename part → path. On any failure delete the part (best effort).
  - `delete` → `client.delete`.
  - `build_endpoint`: replace the trailing `sftp` branch with
    ```python
    transport = session.transport_for(spec) if spec.kind == "crushftp" else spec.kind
    if transport == "sftp":
        return SftpEndpoint(spec.root, client, spec.credentials_ref, location_key=location_key)
    if transport == "crushftp":
        return CrushFtpEndpoint(spec.root, client, spec.credentials_ref, location_key=location_key)
    ```
  - `is_transport_error`: import `CrushFtpConnectError` beside `SmbConnectError` and return `True` for it.

- [ ] **Step 4:** Run `python -m pytest tests/unit/test_crushftp_endpoint.py tests/unit/test_file_transfer.py -q`. Expected: pass.
- [ ] **Step 5:** Commit `feat: crushftp file transfer endpoint`.

---

### Task 8: Routes, Test Connection, config bundle

**Files:** `api/routes/file_servers.py`, `api/services/config_bundle.py`, `tests/unit/test_file_servers_api.py`, the config-bundle unit test file

- [ ] **Step 1: Failing tests** in `tests/unit/test_file_servers_api.py`, using its existing client fixture:
  - Create a valid `crushftp` https/api_key profile → 201; response masks `api_key` and returns `api_key_header`, `protocol`, `tls_ca_bundle` in clear.
  - Create with `protocol` missing → 422 whose detail names `protocol`.
  - Update sending only `{"description": "x"}` on a valid profile → 200 (merged validation passes).
  - Update that sets `auth_method` to `api_key` without a key on a password profile → 422.
  - Update echoing the mask for `api_key` keeps the stored key (resolve and compare).
  - `/test` on an https profile pointed at `FakeCrushFtp` → `ok`; with a wrong password → `error` whose message lacks the password.
  - `/test` on a `crushftp` sftp-protocol profile follows the fingerprint flow (reuse the file's paramiko patching: `unpinned`, then `accept_fingerprint` → `ok`).
  - Config bundle: exporting a `crushftp` profile includes `protocol`, `api_key_header`, `tls_ca_bundle` and has `api_key` set to `None`; importing it recreates the profile.

- [ ] **Step 2:** Run; expected failures.

- [ ] **Step 3: Implement.**
  - `create_file_server`: `errors = file_server_profile_errors(body.model_dump())`; raise `HTTPException(422, "; ".join(errors))` when non-empty.
  - `update_file_server`: build `merged` from the stored row's column values overlaid with `data` (masked secrets count as present, which the validator already accepts because the stored ciphertext is non-empty); validate `merged` the same way before `repo.update`.
  - `test_file_server`: change the first condition to
    `if profile.kind in ("sftp", "scp") or (profile.kind == "crushftp" and profile.protocol == "sftp"):`
    and add, before the `smb` block:
    ```python
    if profile.kind == "crushftp":
        from api.services.crushftp_client import build_crushftp_client
        from etl_framework.reconciliation.file_mapping import FileSourceSpec
        client = None
        try:
            client = build_crushftp_client(profile, FileSourceSpec(kind="crushftp", root="/", pattern="*"))
            client.list("/")
            return FileServerTestResult(status="ok")
        except Exception as exc:
            return FileServerTestResult(status="error", message=str(exc))
        finally:
            if client is not None:
                client.close()
    ```
    The client scrubs its own messages, so `str(exc)` is safe to return.
  - `config_bundle._export_file_server`: add `protocol`, `api_key_header`, `api_key`, `tls_ca_bundle` to `data` (the existing strip nulls `api_key`).

- [ ] **Step 4:** Run `python -m pytest tests/unit/test_file_servers_api.py tests/unit -q -k "file_server or bundle"`. Expected: pass.
- [ ] **Step 5:** Commit `feat: crushftp profile routes, test connection, bundle export`.

---

### Task 9: Frontend

**Files:** `frontend/partials/tab-file-servers.html`, `frontend/features/file-servers.js`, `frontend/partials/tab-launch.html`, `frontend/help-content.js`, `frontend/index.html` (generated)

- [ ] **Step 1:** `tab-file-servers.html`:
  - Kind select gains `<option value="crushftp">CrushFTP</option>`; section subtitle and empty-state text mention CrushFTP.
  - New `<template x-if="fileServerModal.data.kind === 'crushftp'">` block: a Protocol select (`https` → "HTTPS (web API)", `sftp` → "SFTP") with `data-testid="file-server-crushftp-protocol-select"` and `@change="onFileServerKindChange()"`.
    - `x-if` protocol `https`: Base URL (`endpoint_url`, placeholder `https://files.example.com`, testid `file-server-crushftp-url-input`); Auth Method select (`password` → "Username and password", `api_key` → "API key header", testid `file-server-crushftp-auth-select`); for `api_key`: Header Name (`api_key_header`, placeholder `X-Api-Key`, testid `file-server-crushftp-header-input`) and API Key (password input, `api_key`, testid `file-server-crushftp-key-input`); Username and Password (always shown, labelled "(optional)" under `api_key`; testids `file-server-crushftp-username-input`, `file-server-crushftp-password-input`); CA Bundle textarea (`tls_ca_bundle`, label "CA certificate (PEM, optional — for a private on-premises CA)", testid `file-server-crushftp-ca-input`).
    - `x-if` protocol `sftp`: the same Host / Port / Username / Auth Method / Password / Private Key / Passphrase fields as the SFTP block, with `file-server-crushftp-sftp-…` testids.
  - Card summary line: show `fs.endpoint_url` when `fs.kind === 'crushftp' && fs.protocol === 'https'`.
- [ ] **Step 2:** `file-servers.js` `onFileServerKindChange`: for `crushftp`, default `protocol` to `https` when unset; when protocol is `https` and `auth_method` is not `password`/`api_key`, set `password`; when protocol is `sftp`, set port 22 if it is empty or 445 and `auth_method` to `password` if it is `api_key`. Update the comment above the function.
- [ ] **Step 3:** `tab-launch.html`: in each of the six location-kind selects (multi-file source and target, file watcher, file transfer source and destination — every select that has an `smb` option) add `<option value="crushftp">crushftp</option>`; in each of the six profile filters add a `crushftp` case ahead of the sftp/scp fallback: `… === 'crushftp' ? f.kind === 'crushftp' : …`. Add `crushftp` to any root-placeholder helper text that enumerates kinds.
- [ ] **Step 4:** `help-content.js`: one row in the job-type decision table ("File on a CrushFTP server (on-premises or cloud) → file_transfer / file_watcher with a `crushftp` location and a CrushFTP file server profile") and one `file_transfer` scenario showing a `crushftp` source with `credentials_ref`. Match the surrounding entries' structure exactly.
- [ ] **Step 5:** `npm run build:html`; confirm `git diff --stat frontend/index.html` shows the change.
- [ ] **Step 6:** Commit `feat: crushftp file server ui`.

---

### Task 10: Playwright coverage

**Files:** `tests/e2e/58-crushftp-file-servers.spec.ts` (new), modelled on `tests/e2e/33-file-servers.spec.ts`

- [ ] **Step 1:** Tests:
  - Create a CrushFTP HTTPS profile with username/password; it appears in the list; reopening shows the password masked.
  - Create a CrushFTP HTTPS profile with an API-key header; reopening shows the header name and a masked key.
  - Create a CrushFTP SFTP profile; mocked `/test` returning `unpinned` shows Accept & Pin.
  - Mocked `/test` returning `ok` on an HTTPS profile shows "Connection OK".
  - Saving an HTTPS profile without a URL surfaces the server's 422 message in a toast.
  - Job editor: a file-transfer job with `crushftp` source lists only CrushFTP profiles in its dropdown, saves, and reopens with the same kind and profile.
- [ ] **Step 2:** Run with `node node_modules/@playwright/test/cli.js test tests/e2e/58-crushftp-file-servers.spec.ts` (not `npx playwright`). Expected: pass.
- [ ] **Step 3:** Commit `test(e2e): crushftp file server profiles and job editor`.

---

### Task 11: Live verification against Docker

**Files:** `docker-compose.integration.yml`, `tests/integration/test_crushftp_live.py` (new)

- [ ] **Step 1:** Add a `crushftp` service: image `crushftp/crushftp11`, ports `28080:8080` and `22222:2222`, a writable volume mounted at `/srv/atomdata`. Follow the file's existing service style.
- [ ] **Step 2:** `tests/integration/test_crushftp_live.py`, skipped unless `LIVE_CRUSHFTP_URL` is set (also `LIVE_CRUSHFTP_USER`, `LIVE_CRUSHFTP_PASSWORD`, `LIVE_CRUSHFTP_ROOT`, optional `LIVE_CRUSHFTP_SFTP_PORT`, `LIVE_CRUSHFTP_ADMIN_USER`, `LIVE_CRUSHFTP_ADMIN_PASSWORD`). A session fixture creates the test user through the admin API (`setUserItem`, as recorded in the probe) when admin credentials are given. Tests: login + list; upload/download round trip of 5 MiB of random bytes; names with spaces; rename onto existing refused; `CrushFtpEndpoint.write` overwrite; `discover_crushftp_files` recursive; wrong password → `CrushFtpConnectError`; and, when the SFTP port is set, a `crushftp`/sftp profile discovering and transferring through `RemoteFileSourceSession`.
- [ ] **Step 3:** Start the container, run `python -m pytest tests/integration/test_crushftp_live.py -q` with the variables set and `CRUSHFTP_ALLOW_INSECURE_HTTP=1`. Expected: pass. Fix the client (and the fake, so the two stay in step) for any difference from §10, and record the difference in the spec.
- [ ] **Step 4:** Commit `test: live crushftp verification against docker`.

---

### Task 12: Full verification and docs

- [ ] **Step 1:** `python -m pytest tests/unit -q`. Expected: no failures beyond any that fail on `master` (check a failing test against `master` before attributing it to this branch).
- [ ] **Step 2:** `npm run build:html` leaves no diff.
- [ ] **Step 3:** README: add `crushftp` to the file-server kinds and remote-sources sections, with the `CRUSHFTP_ALLOW_INSECURE_HTTP` variable and the `%`-in-names limit.
- [ ] **Step 4:** Commit `docs: crushftp file servers`.

---

## Spec coverage

| Spec section | Task |
|---|---|
| §3 profile model, secrets, startup columns | 1, 2 |
| §3 validation rules | 2, 8 |
| §4 dispatch, kind lists | 5, 6 |
| §5 client, discovery, endpoint, security properties, errors | 4, 5, 7 |
| §6 API | 8 |
| §7 frontend | 9 |
| §8 testing | 3, 4, 6, 7, 8, 10, 11 |
| §9 cloud checklist | Manual; reported to the user at hand-off |
| §10 wire format | Done before this plan; re-checked in 11 |
