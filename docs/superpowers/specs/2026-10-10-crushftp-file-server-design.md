# CrushFTP File Server Support (On-Premises and Cloud) — Design

**Goal:** Let `file_transfer`, `file_watcher`, and multi-file reconciliation jobs read from and write to a CrushFTP server, whether it is an on-premises instance reached with a username and password or a cloud instance reached with an API key sent in a configurable HTTP header. A job names a `crushftp` location and a file server profile; the profile alone decides the protocol and the authentication, so moving a job between on-premises and cloud means swapping the profile, not editing the job.

**Tech stack:** Python (FastAPI, Pydantic, SQLAlchemy, `requests`, paramiko), Alpine.js frontend, pytest, Playwright, Docker (CrushFTP container for live verification).

---

## 1. Context and decisions

`FileServerProfile` kinds in code today are `sftp`, `scp`, `s3`, and `smb`. The `ftp` kind and the WinSCP transport described in `2026-09-22-smb-winscp-file-transfer-design.md` were never built (there is no `ftp` kind and no `transport` column), and this change does not build them.

Kind dispatch happens in two places: `RemoteFileSourceSession._client_for` (`api/services/multi_file_remote.py`) and `build_endpoint` (`api/services/file_transfer.py`). The list of allowed kinds is repeated in `api/schemas.py`, `etl_framework/runner/job_validation.py`, `etl_framework/reconciliation/file_transfer_spec.py`, `etl_framework/reconciliation/file_mapping.py`, and the frontend dropdowns.

Decisions made while scoping:

| Question | Decision |
|---|---|
| Protocols | Both. A `crushftp` profile picks `https` (CrushFTP's HTTP(S) WebInterface API) or `sftp` (the existing paramiko path). |
| Modeling | One new `crushftp` kind for both profiles and job locations. The profile's `protocol` field selects the transport. |
| On-premises auth | Username and password (HTTPS session login), or password / SSH key over SFTP. |
| Cloud auth | An API key sent in a custom header whose name is stored on the profile. A username and password may be set as well, for a gateway that sits in front of a normal CrushFTP session. |
| TLS | System trust store by default; a pasted PEM CA bundle for a private on-premises CA. There is no switch to disable verification. |
| Base URL storage | The existing `endpoint_url` column is reused for the HTTPS base URL rather than adding a `base_url` column. |
| Verification | A local CrushFTP Docker container. The client is written to observed behaviour. |

## 2. Unverified assumptions

Two things could not be confirmed while designing and are settled during implementation, not assumed:

1. **The CrushFTP HTTP API wire format.** Resolved for CrushFTP 11.5.5: a local container was probed before the client was written, and §10 records what was observed. Other major versions are not verified.
2. **How a cloud instance treats the API-key header.** This is not a stock CrushFTP login mechanism that could be confirmed; it is most likely enforced by an API gateway or reverse proxy in front of the server. The design therefore sends the header on every request and logs in only when a username is set, which covers both a gateway that authenticates on the key alone and one that still expects a CrushFTP session behind it. This mode cannot be verified against the local container and stays labelled unverified until the manual checklist in §9 is run against the real cloud instance.

## 3. Profile model

`FileServerProfile.kind` (`String(10)`) gains the value `crushftp`.

New nullable columns on `file_server_profiles`:

| Column | Type | Meaning |
|---|---|---|
| `protocol` | `String(10)` | `https` or `sftp`. Meaningful only when `kind="crushftp"`. |
| `api_key_header` | `String(255)` | Header name, for example `X-Api-Key`. |
| `api_key` | `Text` | Header value. Encrypted at rest. |
| `tls_ca_bundle` | `Text` | PEM text of a private CA. Empty means the system trust store. Not a secret. |

Reused columns: `endpoint_url` holds the HTTPS base URL (`https://files.example.com[:port][/prefix]`); `host`, `port`, `username`, `password`, `private_key`, `key_passphrase`, and `host_key_fingerprint` serve the SFTP protocol exactly as they do for an `sftp` profile. `auth_method` gains the value `api_key`.

`api_key` joins `_FILE_SERVER_SECRET_FIELDS` (`etl_framework/repository/repository.py`), so it is encrypted on write, masked on read, and preserved when the client echoes the mask back. `ResolvedFileServerProfile` gains the four new fields. The config bundle export/import (`api/services/config_bundle.py`) carries them.

Validation rules, enforced in the `FileServerProfileCreate` / `FileServerProfileUpdate` schemas:

- `kind="crushftp"` requires `protocol`.
- `protocol="sftp"`: `host` required; `auth_method` is `password` or `private_key`.
- `protocol="https"`: `endpoint_url` required; `auth_method` is `password` or `api_key`.
  - `password`: `username` and `password` required.
  - `api_key`: `api_key_header` and `api_key` required; `username` and `password` optional. The header name must be a valid HTTP token and must not be `Cookie`, `Host`, `Content-Length`, or `Transfer-Encoding`.
- `endpoint_url` must be `https://`. An `http://` URL is accepted only when the environment variable `CRUSHFTP_ALLOW_INSECURE_HTTP=1` is set on the atom server, which exists for the local Docker container and nothing else.
- `api_key` / `api_key_header` are rejected on any profile that is not `crushftp` + `https`.

These cross-field rules run in the routes against the merged profile (the stored row overlaid with the request body), because an update may send only some fields.

Already-deployed databases gain the four columns at startup through `ensure_column` in `etl_framework/repository/database.py`, the mechanism every other added column uses. `scripts/upgrade_db_20260913_to_current.py` checks that `file_server_profiles` has an exact column set, so its column list and `CREATE TABLE` statement gain the same four columns.

## 4. Dispatch

A job location is `{"kind": "crushftp", "root": "/path", "pattern": "...", "credentials_ref": "<profile name>"}`. `root` is a POSIX path on the server for both protocols.

- `resolve_file_server_profile`: a `crushftp` spec requires a `crushftp` profile, and a `crushftp` profile is valid only for a `crushftp` spec.
- `RemoteFileSourceSession` gains one helper, `transport_for(spec)`, returning the effective transport: `sftp` when `spec.kind` is `sftp`, or when it is `crushftp` and the resolved profile's protocol is `sftp`; `crushftp` when the protocol is `https`; otherwise `spec.kind`. The existing `spec.kind == "sftp"` branches in `discover`, `read_file`, `_read_bytes`, and `location_key_for`, and in `build_endpoint`, branch on this helper instead. A CrushFTP profile on the SFTP protocol therefore runs through `build_sftp_client` and `SftpEndpoint` unchanged, including host-key pinning before any credential is sent.
- `_client_for` builds a `CrushFtpHttpClient` for the HTTPS protocol and the paramiko client for the SFTP protocol. The client cache key stays `(spec.kind, spec.credentials_ref)`.
- `location_key_for`: the SFTP protocol returns the existing `("sftp", host, port, username)`, so a `crushftp` profile and a plain `sftp` profile for the same account compare equal. The HTTPS protocol returns `("crushftp", host, port, username or api_key_header)`.
- `close_remote_client` already calls `close()` on whatever it is handed; `CrushFtpHttpClient.close()` logs out and closes the HTTP session.

The kind lists gain `crushftp` in `api/schemas.py` (profile `kind` literals and the `file_watcher` location check), `etl_framework/runner/job_validation.py`, `etl_framework/reconciliation/file_transfer_spec.py` (`LOCATION_KINDS`, `REMOTE_KINDS`), and `etl_framework/reconciliation/file_mapping.py` (`_parse_file_source`).

## 5. HTTPS client

New module `api/services/crushftp_client.py`. Every piece of CrushFTP wire knowledge lives in this module and nowhere else.

`CrushFtpHttpClient`, built on `requests.Session` with explicit connect and read timeouts:

| Method | Purpose |
|---|---|
| `login()` | Session login when a username is set; no-op otherwise. |
| `list(path)` | Entries with name, size, modified time, and directory flag. |
| `stat(path)` | One entry, or `FileNotFoundError`. |
| `open_read(path, limit=None)` | Streamed download; `limit` bounds the read for `file_watcher` content matching. |
| `upload(path, stream)` | Streamed upload. |
| `rename(src, dst)`, `mkdir(path)`, `delete(path)` | As named. |
| `close()` | Logout (best effort) and close the session. |

`discover_crushftp_files(client, root, pattern, recursive)` sits beside the existing S3 and SFTP discovery functions and returns the same `DiscoveredFile` shape.

`CrushFtpEndpoint` in `api/services/file_transfer.py` implements the same interface as `SftpEndpoint`: `relative_of`, `destination_for` (rejecting paths that escape the root), `identity`, `exists`, `exists_many`, `size`, `open_read`, `write`, `delete`. `write` uploads to a `.part` name and then renames, so a destination is never left half-written; the existing `_copy_one` verifies the size afterwards. The server refuses to rename onto an existing file (§10), so replacing a file moves the old one aside, renames the new one in, and restores the old one if that fails — the same sequence `SftpEndpoint` uses when the server lacks `posix-rename`.

The server answers an upload with the MD5 of the bytes it stored. The client hashes the stream as it sends it and raises if the two differ, which catches corruption in transit without a second request.

A destination path containing `%` is rejected with a clear error: the server's handling of that character differs between upload, listing, and rename (§10), so it cannot be written reliably. Reading and discovering existing files whose names contain `%` works.

Security properties:

- **No redirects.** `requests` strips `Authorization` on a cross-host redirect but not custom headers, so following a redirect could send the API key to whatever host it names. The server also answers a rejected or expired session with a redirect to its login page (§10). The client therefore never follows a redirect; any 3xx response raises `CrushFtpConnectError`.
- **No secrets in output.** The password, the API key, and the session cookie value are scrubbed from every exception message before it can reach a run result, a log line, or a Test Connection response.
- **TLS.** `verify` is the path to a temporary file holding `tls_ca_bundle` when one is set, otherwise `True`. It is never `False`.

Errors:

- `CrushFtpConnectError(RuntimeError)` for connection failure, TLS failure, login rejection, HTTP 401/403, and a refused redirect. Added to `is_transport_error`, so the run reports ERROR, matching a dropped SFTP connection.
- A per-file failure (missing file, server-side write error) surfaces as `FileNotFoundError` or `TransferError` and the run reports FAILED.

## 6. API

- `POST /api/file-servers/{id}/test` for a `crushftp` profile: the SFTP protocol reuses the existing fingerprint review-and-pin flow; the HTTPS protocol logs in (when a username is set) and lists the root, returning `ok` or a scrubbed error. HTTPS has no fingerprint step.
- CRUD routes are unchanged apart from the schema additions.

## 7. Frontend

- **File Servers tab.** The kind selector gains `CrushFTP`, followed by a protocol selector (`HTTPS` / `SFTP`). HTTPS shows the base URL, an auth-method selector (`Username and password` / `API key header`), the matching fields, and a CA bundle textarea. SFTP shows the existing SFTP fields. Choosing a protocol sets a sensible default port for SFTP (22).
- **Job editor.** The location-kind dropdowns for multi-file source and target, file watcher, and file transfer source and destination gain `crushftp`, and the profile dropdown filter gains a matching case.
- **Help content.** One row in the job-type decision table and one scenario.

Edits are made in `frontend/partials/` and `frontend/features/`, and `frontend/index.html` is rebuilt from them.

## 8. Testing

- **Unit.** `tests/helpers/fake_crushftp.py` is an in-process HTTP server implementing the subset of the API observed in §10, in the spirit of `tests/helpers/fake_sftp.py`. Covered: both HTTPS auth modes; the header sent on every request; login skipped without a username; cross-origin redirect refused; secrets scrubbed from errors; `transport_for` and every branch that uses it; `CrushFtpEndpoint` path safety and `.part`-then-rename; the profile validation rules; `is_transport_error`; the migration script run twice.
- **Live, environment-gated.** The client, discovery, and endpoint against the Docker container over its HTTP port and its SFTP port. Skipped unless the live variables are set, matching the existing live tracks. The container's stock certificate is self-signed for `CN=www.crushftp.com` with no subject alternative name, so it cannot pass hostname verification on `127.0.0.1`; the live track therefore runs over plain HTTP with `CRUSHFTP_ALLOW_INSECURE_HTTP=1`, and TLS verification with a CA bundle is covered by a unit test against the fake server with a generated certificate.
- **Playwright.** Profile CRUD for both protocols and both HTTPS auth modes, and a job-editor round trip for a `crushftp` location, with a mocked `/test` response.

## 9. Manual checklist for the cloud instance

Run against the real cloud instance before relying on API-key mode:

1. Create a `crushftp` / `https` / `api_key` profile with only the header name and key. Test Connection returns `ok`.
2. If step 1 fails with an authentication error, add the username and password to the same profile and retest.
3. Run a `file_transfer` job that uploads one file and downloads it again; the bytes match.
4. Confirm that a wrong key produces an ERROR run whose message does not contain the key.

## 10. Observed CrushFTP API

Observed on 2026-10-10 against the official `crushftp/crushftp11` image, version 11.5.5, with a test user whose single virtual folder `/data/` maps to a writable directory. Commands are form-encoded `POST`s to `/WebInterface/function/`.

| Operation | Request | Response |
|---|---|---|
| Login | `command=login&username=…&password=…` | 200, XML `<loginResult><response>success</response><c2f>XXXX</c2f></loginResult>`; sets cookies `CrushAuth` (HttpOnly) and `currentAuth`. A wrong password is also 200, with `<response>failure</response>` and a message. |
| Anti-CSRF token | `c2f` = the `<c2f>` value = the last four characters of the `CrushAuth` cookie. | Read-only commands work without it. Mutating commands without it, or with a wrong one, return 200 with `<response>FAILURE:Access Denied. (c2f)</response>`. |
| List | `command=getXMLListing&path=/dir/&format=JSONOBJ&c2f=…` | 200, JSON with `path` and `listing`; each entry has `name`, `type` (`FILE` / `DIR`), `size`, `modified` (epoch milliseconds), all as strings. A missing directory, or a path without a trailing slash, returns `{"error_msg": "error_item"}`. |
| Size / existence | `HEAD /dir/file` | 200 with `Content-Length`; 404 when missing. A directory also answers 200. |
| Download | `GET /dir/file`, optional `Range: bytes=a-b` | 200, or 206 with `Content-Range`; 404 when missing. A ranged request on an empty file returns 200 with no body. |
| Upload | `PUT /dir/file` with the bytes as the body (chunked transfer encoding accepted) | 201, body is the lowercase hex MD5 of the stored bytes. Overwrites an existing file. 403 when the parent directory is missing or the account lacks write permission. |
| Make directory | `command=makedir&path=/dir/sub/&c2f=…` | 200, empty `<response>`. Creates missing parents; succeeds when the directory already exists. |
| Rename | `command=rename&path=/dir/&name1=old&name2=new&c2f=…` | 200, empty `<response>` on success. `name2` may contain a subdirectory. Renaming onto an existing file fails with `File rename error. (bad path? in use? locked?)` and leaves both files untouched. A missing source fails with `%RNFR-bad%ERROR:Access not allowed.` |
| Delete | `command=delete&names=/dir/file&c2f=…` | 200, empty `<response>`, for an existing file, a directory, and a missing path alike. |
| Logout | `command=logout&c2f=…` | 200, `<response>Logged out.</response>`. |

Other observed behaviour the client depends on:

- **Not authenticated.** A command `POST` without a valid session returns 404. A `GET` of a file returns 302 to `/WebInterface/login.html`. A `PUT` returns 401.
- **Form encoding.** The server does not decode `+` as a space in a form body, so a body built by `requests` (which encodes a space as `+`) addresses the wrong name. Form values must be percent-encoded (`%20`).
- **Error text** inside `<response>` is percent-encoded.
- **Single virtual folder.** Listing `/` for an account with one virtual folder returns that folder's contents, with `path` set to the folder (`/data/`). File paths are therefore built from the `path` in the response, not from the path requested.
- **The `%` character.** A file uploaded as `pct%x.txt` is listed as `pct%25x.txt`, and a rename whose source name contains an encoded `%` fails. Listing names are unescaped (`%25` to `%`) for reading; writing such names is refused (§5).
- **SFTP** listens on port 2222 in the image, presents an `ssh-rsa` host key, accepts password authentication, and does not support the `posix-rename@openssh.com` extension; the existing `SftpEndpoint` fallback handles that.
- **TLS.** The stock HTTPS certificate is self-signed for `CN=www.crushftp.com` with no subject alternative name.

## 11. Out of scope

- TOTP / two-factor, SSO, SAML, OAuth.
- FTP, FTPS, WebDAV.
- An outbound HTTP or SOCKS proxy.
- The CrushFTP admin API (user and server management).
- Detecting that an HTTPS profile and an SFTP profile point at the same server. The same-file guard in `file_transfer` compares location keys, which differ across protocols, so a transfer between two such profiles is not recognised as source and destination being one place.
- The WinSCP transport and `ftp` kind from the 2026-09-22 spec.
