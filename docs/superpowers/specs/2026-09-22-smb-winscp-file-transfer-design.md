# SMB/UNC and WinSCP File Transfer — Design

**Goal:** Extend `file_transfer` (and, for free, `file_watcher` and multi-file reconciliation) with two new source/destination kinds: `smb` for Windows UNC/SMB file shares, and a WinSCP-backed path for servers `paramiko` can't reach — true SCP-only SSH hosts and FTP/FTPS servers.

**Tech stack:** Python (FastAPI, Pydantic, SQLAlchemy), Windows `net use` (subprocess), WinSCP's `winscp.com` scripting console (subprocess), existing `RemoteFileSourceSession` / `FileServerProfile` / `file_transfer.py` machinery, Alpine.js frontend, pytest.

---

## 1. Context and decisions

`file_transfer` (and the discovery layer it shares with `file_watcher` and multi-file reconciliation) supports `local`, `s3`, and `sftp`/`scp` (the latter an alias for the paramiko SFTP client — see `docs/superpowers/specs/2026-09-21-file-transfer-job-design.md`). Two gaps remain:

1. **Windows file shares.** A UNC path (`\\server\share\path`) reachable over SMB with Windows credentials has no home in the current `kind` model.
2. **True SCP-only and FTP/FTPS servers.** `paramiko` needs the SFTP subsystem; some vendor/appliance servers only speak raw SCP, and `paramiko` has no FTP support at all. The prior design explicitly left "true SCP-only hosts" out of scope.

Decisions made while scoping:

| Question | Decision |
|---|---|
| Scope of integration | Full: `smb` and `ftp` join `local`/`s3`/`sftp`/`scp` everywhere those kinds are enumerated — `file_transfer`, `file_watcher`, and multi-file reconciliation all gain both kinds from one shared discovery-layer change. |
| How SMB is reached | Windows' built-in `net use`, shelled out to. No new Python dependency (rejected `pywin32`/impersonation as an alternative). |
| How true-SCP/FTP is reached | Shell out to `winscp.com` (WinSCP's scriptable console), the documented automation path. No new Python dependency. |
| Modeling true-SCP | Not a new `kind`. A per-profile `transport` field (`paramiko` default, or `winscp`) on existing `sftp`/`scp` profiles opts a profile into the WinSCP engine. |
| Modeling FTP/FTPS | A new `ftp` kind, always routed through WinSCP (paramiko has no FTP support at all). |
| Platform | The atom API server is not always Windows (mixed Linux deployments exist). Both new mechanisms are Windows-only and must fail with a clear error on Linux, never crash obscurely. |
| WebDAV | Out of scope. |
| FTPS certificate pinning | Out of scope for v1; relies on WinSCP's own default TLS certificate validation. |

## 2. Profile and credential model

`FileServerProfile.kind` (`etl_framework/repository/models.py`, `String(10)`) gains two new allowed values, both fitting the column: `smb` and `ftp`.

Two new nullable columns on the same table:
- `transport` (`String(10)`, nullable). Meaningful only when `kind` is `sftp`/`scp`. A value of `"winscp"` opts that profile into the WinSCP engine — for a host that only speaks true SCP. Null (every existing profile's current state) keeps today's paramiko path unchanged.
- `ftp_use_tls` (`Boolean`, nullable, default `False`). Meaningful only when `kind="ftp"`: explicit FTPS vs plain FTP.

Field reuse, no new secret fields:
- `smb` reuses `host`/`username`/`password`. `host` is the server name — informational, plus a light check that a job's UNC root actually points at it. `username` accepts `DOMAIN\user` or `user@domain.com`, exactly what `net use /user:` already takes.
- `ftp` reuses `host`/`port`/`username`/`password` (`port` defaults to 21).
- `password` is already in the encrypted-at-rest secret-field set; both new kinds reuse it as-is.

One ad-hoc SQLite migration script, matching the existing `scripts/upgrade_db_20260913_to_current.py` pattern, adds the two new columns to `file_server_profiles` for already-deployed databases. Idempotent — safe to run twice.

## 3. SMB/UNC mechanics

- **Root format.** A job's `root` for `kind="smb"` is a UNC path: `\\server\share\subpath`. Validated to start with `\\` and have at least a host and a share segment. If the profile's `host` is set, it must match the UNC's host (case-insensitive) — catches a job pointed at the wrong server with a saved credential.
- **Connecting.** Before any file operation: `net use \\server\share password /user:DOMAIN\user` (password is positional, before the `/user:` switch), using only the first two path segments (the server+share), not the full root. Windows allows only one credential to be connected to a given server at a time — `net use` fails with "multiple connections... using more than one user name" otherwise — so connects to the same server are serialized through a process-wide lock keyed by hostname, held for the whole job's `RemoteFileSourceSession` lifetime. This is a known, deliberate serialization point, not a bug to fix later.
- **Disconnecting.** `net use \\server\share /delete`, run when the session closes — the same place `close_remote_client` already tears down S3/paramiko clients, extended to handle an SMB "client" marker.
- **Reusing existing code.** Once `net use` succeeds, `\\server\share\path` behaves like any other filesystem path to Python. Discovery is `discover_local_files` called directly on the UNC root, skipping the `resolve_allowed_path` server-side allowlist (which is for genuinely local paths, matching how `sftp`/`s3` already skip it). `SmbEndpoint` (in `api/services/file_transfer.py`) reuses `LocalEndpoint`'s path-safety internals — the `.part`+rename atomic write, the Windows-illegal-name guard, `..`/absolute-path rejection — but skips the allowlist and validates against the UNC root instead. Its `identity()` is keyed like `SftpEndpoint`'s: by server, share, and path, not by credential name, so two profiles pointing at the same share are recognized as the same object.

## 4. WinSCP mechanics

- **Invocation.** Shell out to `winscp.com` with a generated script file per operation. Executable path is configurable via `WINSCP_PATH` (default: `winscp.com` on `PATH`), matching how other external tools are configured in this repo.
- **Host key / certificate handling.** For winscp-transport SCP, the existing `host_key_fingerprint` profile field is reused and required, exactly like the current paramiko path — no fingerprint pinned, no connection. For FTP/FTPS there's no SSH host key; v1 relies on WinSCP's own default TLS certificate validation (reject-by-default) rather than per-profile certificate pinning.
- **Credentials on disk — a genuine trade-off.** Unlike paramiko (private keys parsed in memory, never written to disk), `winscp.com` needs its script on disk to run, so the script briefly contains the plaintext password. It's written to a per-job temp file, ACL-restricted to the service account, and deleted in a `finally` immediately after the run. Disclosed explicitly rather than glossed over.
- **No true streaming.** `winscp.com` is a file-oriented CLI (`get`/`put`), not a byte-stream API. `WinscpEndpoint` reads by downloading to a private local temp file first, then streaming from that; it writes by streaming the source into a local temp file first, then uploading it (via the same temp-name-then-rename pattern used elsewhere for atomicity). WinSCP transfers therefore need local disk space proportional to the largest single file moved — a real cost the fully-streamed S3/SFTP paths don't have.

## 5. Wiring `smb` and `ftp` into the shared discovery layer

`local`/`s3`/`sftp`/`scp` are enumerated separately in five places; `smb` and `ftp` join all five:
- `api/schemas.py` and `etl_framework/runner/job_validation.py` (`file_watcher` validation, both copies)
- `etl_framework/reconciliation/file_transfer_spec.py`'s `LOCATION_KINDS` (`file_transfer`)
- `etl_framework/reconciliation/file_mapping.py`'s `_parse_file_source` (multi-file reconciliation — this one currently omits `scp` too; that pre-existing gap is left alone)
- `api/services/multi_file_remote.py`'s client dispatch

Two dispatch points need a genuine new branch:
- `RemoteFileSourceSession._client_for` (`multi_file_remote.py`) picks a client by `spec.kind` today. New branches: `smb` establishes (or reuses, host-locked) the `net use` session and caches a marker object as its "client"; `ftp` always builds a WinSCP session; `sftp`/`scp` builds a WinSCP session instead of the paramiko client **only if** the resolved profile's `transport` is `"winscp"`. `RemoteFileSourceSession.discover()` then calls `discover_local_files` directly on the UNC root for `smb` (skipping `resolve_allowed_path`), and a new `discover_winscp_files(session, root, pattern, recursive)` — parsing `winscp.com`'s `ls` output — for `ftp` and winscp-transport `sftp`/`scp`.
- `file_transfer.py`'s `build_endpoint` gains matching branches: `smb` → `SmbEndpoint`; `ftp`, or `sftp`/`scp` with `transport="winscp"` → `WinscpEndpoint`.

`file_watcher` and multi-file reconciliation gain `smb`/`ftp` support from this discovery-layer change alone — no changes needed in `run_executor.py`'s watcher/reconciliation code paths.

## 6. Platform guard, failures, and secret handling

- **Platform guard.** The new branches in `_client_for` and `build_endpoint` check `os.name != "nt"` first and raise `RuntimeError("SMB/WinSCP transfers require the atom server to run on Windows")` before touching `subprocess`. A Linux deployment fails cleanly with that message; existing `local`/`s3`/`sftp` jobs are unaffected.
- **Missing WinSCP binary.** If `winscp.com` isn't found, the subprocess call's `FileNotFoundError` is wrapped into `RuntimeError("WinSCP executable not found at '<path>' -- install WinSCP or set WINSCP_PATH")`, matching the existing "boto3 is required..." style.
- **Connection failures classify as transport errors.** Two new exception types, `SmbConnectError` and `WinscpConnectError` (both `RuntimeError` subclasses), are raised only for connection-establishment failures — bad credentials, unreachable host, missing share, WinSCP host-key/certificate rejection. Both are added to `is_transport_error`'s True set alongside the existing botocore/paramiko cases, so these map to ERROR the same way a dropped SFTP connection already does. A per-file copy failure (e.g. disk full on the far end) still surfaces through the normal `TransferError`/generic-exception path and stays FAILED.
- **No secrets leak into results.** The `net use` command line, `winscp.com`'s stdout/stderr, and any WinSCP `/log=` output are all scrubbed of the password (and of a `user:password@` embedded in a session URL) before any of it can reach `mismatch_summary.error` — the same "no secrets in logs or summaries" property the existing S3/SFTP paths already have.

## 7. Frontend

- **File Servers tab.** Kind selector gains `SMB / UNC Share` and `FTP`. SMB's form: host, username (with `DOMAIN\user` hint), password — no port, no key options. FTP's form: host, port (default 21), username, password, "Use FTPS (TLS)" checkbox. For existing `sftp`/`scp` profiles, a new "Transport" selector appears: `Standard (SFTP)` (default) or `WinSCP (for true SCP-only servers)`, mapping to `transport`.
- **Test Connection.** Extended through the existing `POST /{id}/test` flow: SMB attempts `net use` then immediately releases it; FTP/winscp-transport SFTP shells to `winscp.com` for a minimal connect-and-list. The existing host-key fingerprint pin/accept flow is reused for winscp-transport SFTP/SCP; FTP/FTPS has no such step.
- **Job editor.** Location-kind dropdowns in `file_transfer`, `file_watcher`, and multi-file reconciliation's source/target pickers gain `smb` and `ftp`. The file-server-profile dropdown filter gains matching filters for both new kinds. Root-field placeholder text gets a UNC example (`\\server\share\path`) and a note that an FTP root is a plain path, not a URL.
- **Help content.** The job-type decision table and the `file_watcher`/`file_transfer` help scenarios get a line each for the two new kinds, plus one short new note on the WinSCP path's disk-staging and credential-file trade-offs.

## 8. Testing

No real Windows SMB share, WinSCP install, or SCP/FTP server exists in CI, so everything below is mocked/unit-level; live verification against a real target is manual, matching the existing stance for live SFTP/S3.

- `SmbEndpoint` and the connect/lock helper: mocked `subprocess.run` for `net use` success/failure, the per-host lock's serialization behavior, identity/path-safety (reusing `LocalEndpoint`'s already-tested logic).
- `WinscpEndpoint` and `discover_winscp_files`: mocked `winscp.com` calls — script generation (right `open`/`get`/`put`/`mv` commands, host-key arg present for winscp-transport SCP, credentials never appearing in anything logged), temp-file staging on read and write, `ls`-output parsing against a canned sample.
- `is_transport_error` gains cases for `SmbConnectError`/`WinscpConnectError`.
- Platform guard: monkeypatch `os.name` to simulate non-Windows, assert the clear `RuntimeError`.
- Profile CRUD for `kind="smb"`/`"ftp"` and the new `transport` field on `sftp`/`scp` profiles; the migration script runs cleanly and twice without failing.
- The five kind-enumeration sites and both dispatch points from §5.
- e2e: File Servers CRUD for the two new kinds and the transport selector (mocked `/test` response, matching the existing SFTP/SCP e2e pattern), and job-editor round-trip for `smb`/`ftp` locations. No live-transfer e2e.

## 9. Out of scope (deferred)

- FTPS certificate pinning (relies on WinSCP's own default validation).
- WebDAV.
- `pywin32`/impersonation as an SMB alternative to `net use`.
- Any refinement of the per-host SMB lock beyond simple serialization (e.g. detecting "same username, no need to serialize").
- Automated CI/live e2e against a real SMB share or WinSCP-reachable server.
- FTP directory-listing parsing beyond the common `LIST` format.
