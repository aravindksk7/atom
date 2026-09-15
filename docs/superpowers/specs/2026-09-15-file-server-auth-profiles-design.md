# File Server Authentication Profiles (SFTP / SCP / S3) — Design

**Goal:** Give `file_watcher` and multi-file (`file_mapping`) jobs a persisted, encrypted-at-rest, reusable way to authenticate to remote SFTP/SCP/S3 servers — named "file server profiles" the web GUI can create, edit, and pick from — instead of today's raw, unencrypted, hand-typed-per-launch credentials. Adds SSH key-pair auth and host-key fingerprint verification for SFTP/SCP, both currently absent.

**Tech stack:** Python (FastAPI, SQLAlchemy, paramiko, boto3), Alpine.js frontend, pytest / Playwright.

**Context — what investigation found:** `file_watcher`/`file_mapping` sources support `local`, `s3`, `sftp`, and `scp` (the last silently aliased to the `sftp` client in `run_executor.py:1615` — there is no separate SCP transport). Every non-local source needs a `credentials_ref`, resolved at run time by `resolve_file_source_credentials()` in [multi_file_remote.py](../../../api/services/multi_file_remote.py) against `config_snapshot["file_source_credentials"][ref]` — a **raw dict the caller must supply fresh on every single launch**, via the API body only. The GUI (`fw_credentials_ref` / `mf_source_credentials_ref` inputs in `tab-launch.html`) only ever lets a user type that *name* — there is no field anywhere in the GUI to enter an actual host, username, password, or key, so **the web GUI cannot today launch a working SFTP/S3/SCP file_watcher job at all**; only a direct API call with `file_source_credentials` in the body can.

This is a real gap against the pattern this codebase already uses for every other credential type: `SavedConfig.config_json["connections"][name]` (`ConnectionEntry`) and `["api_endpoints"][name]` (`ApiEndpointEntry`) are named, reusable, and run through `SECRET_FIELDS` + `ConfigRepository`'s `_encrypt`/`_decrypt` (`etl_framework/repository/repository.py`) and `api/routes/configs.py`'s `_mask`/`_preserve_masked_secrets` on the way in and out of the API. `file_source_credentials` participates in none of that. Additionally: SFTP auth (`build_sftp_client`) supports username/password only via `paramiko.Transport.connect()` — no key-pair option — and performs **no host-key verification at all** (no `set_missing_host_key_policy`, no fingerprint check anywhere in the codebase), an MITM exposure.

Decisions made during design: profiles live in their own table (not nested in `SavedConfig`, since a file server is reused across many configs/sequences, not scoped to one environment); SSH keys are pasted and encrypted at rest (no filesystem dependency); host keys are pinned via an explicit accept step, not silent trust-on-first-use; the old inline `file_source_credentials` path is removed rather than kept as a fallback — this is a deliberate breaking change (see §6, Migration).

---

## 1. Data model

New table, `etl_framework/repository/models.py`:

```python
class FileServerProfile(Base):
    __tablename__ = "file_server_profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    kind = Column(String(10), nullable=False)  # "sftp" | "scp" | "s3" -- scp still uses the sftp client/fields
    description = Column(Text, nullable=False, default="")

    # sftp / scp
    host = Column(String(255), nullable=True)
    port = Column(Integer, nullable=False, default=22)
    username = Column(String(255), nullable=True)
    auth_method = Column(String(20), nullable=True)   # "password" | "private_key"
    password = Column(Text, nullable=True)             # encrypted at rest
    private_key = Column(Text, nullable=True)           # encrypted at rest, PEM/OpenSSH text
    key_passphrase = Column(Text, nullable=True)        # encrypted at rest
    host_key_fingerprint = Column(String(128), nullable=True)  # SHA256, pinned via /test accept

    # s3
    aws_access_key_id = Column(String(255), nullable=True)
    aws_secret_access_key = Column(Text, nullable=True)  # encrypted at rest
    aws_session_token = Column(Text, nullable=True)      # encrypted at rest
    region_name = Column(String(50), nullable=True)
    endpoint_url = Column(String(1024), nullable=True)   # non-AWS S3-compatible target (MinIO, etc.)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
```

This table's secrets are *not* added to the shared `SECRET_FIELDS` set (`etl_framework/config/models.py`) — that set is keyed by field name across a JSON blob shared by several unrelated schemas, and a generic name like `password` would collide with unrelated uses. Instead, a small `FileServerProfileRepository._encrypt`/`_decrypt` pair calls the existing `encrypt_secret`/`decrypt_secret` from `api/services/secret_store.py` directly on this table's own explicit field list — `{password, private_key, key_passphrase, aws_secret_access_key, aws_session_token}` — mirroring `ConfigRepository`'s approach without overloading the shared set.

## 2. API

New `api/routes/file_servers.py`, `router = APIRouter(prefix="/api/file-servers", tags=["file-servers"])`:

- `GET /` → list, secrets masked (reuse `configs.py`'s `_MASK` constant and masking approach, applied to the flat profile dict).
- `POST /` → create.
- `GET /{id}` → get, masked.
- `PUT /{id}` → update; submitting the mask value for a secret field keeps the stored value (`_preserve_masked_secrets`, same pattern as `configs.py`).
- `DELETE /{id}` → delete. 409 if any `saved_jobs`/`execution_sequence_versions`/`scheduled_runs` currently reference this profile's `name` as a `credentials_ref` (cheap grep-style JSON scan, matching how other delete-guards in this codebase check usage before allowing removal).
- `POST /{id}/test` → attempts a real connection.
  - `sftp`/`scp`: connects with the stored auth; fetches `transport.get_remote_server_key()`; computes its SHA256 fingerprint.
    - If `host_key_fingerprint` is unset on the profile: returns the presented fingerprint as `{status: "unpinned", presented_fingerprint: "..."}` for the caller to review; a separate `POST /{id}/test?accept_fingerprint=true` (or a small `accepted: bool` body field) pins it.
    - If set: compares; returns `{status: "ok"}` or `{status: "mismatch", presented_fingerprint: "...", pinned_fingerprint: "..."}` — never auto-updates a pinned fingerprint.
  - `s3`: `head_bucket` if a bucket/prefix convention is available, else `list_buckets`; returns `{status: "ok"}` or the boto3 error.

## 3. Credential resolution

`resolve_file_source_credentials(config_snapshot, spec)` in `multi_file_remote.py` is replaced by `resolve_file_server_profile(db, spec.credentials_ref)`, which looks up `FileServerProfile` by name and decrypts its secret fields. `build_s3_client`/`build_sftp_client` take the decrypted profile instead of `config_snapshot`.

`RunTrigger`, `JobSelectionLaunchRequest`, `SequenceLaunchRequest`, and the discovery-preview body in `api/routes/jobs.py:177` all **drop the `file_source_credentials` field** — it's no longer accepted.

**Deviation from the original plan (decided during implementation, see the implementation plan's header):** `job_validation.py`'s `validate_job_definition()` is a pure function with no `db` parameter, called from 3 route handlers and ~90 existing unit tests with no DB access anywhere in its call chain — threading a `Session` through it just for this one check would have been a disproportionate signature change. Instead, the DB-backed "does this profile exist and match this kind" check lives in `resolve_file_server_profile()` (§3 above), which is exactly where credential resolution already happens for every real execution and for the synchronous `preview-file-mapping` endpoint alike. `job_validation.py`'s existing pure structural check (`kind in ("s3","sftp","scp") and not location.get("credentials_ref")`) is unchanged — it still only requires a non-empty `credentials_ref` string, not that the referenced profile exists. See §8 for the resulting error-handling shape.

## 4. SFTP/SCP client — key auth + host-key verification

```python
def build_sftp_client(profile: FileServerProfile):
    transport = paramiko.Transport((profile.host, profile.port))
    if profile.auth_method == "private_key":
        key = paramiko.PKey.from_private_key(
            io.StringIO(profile.private_key), password=profile.key_passphrase or None,
        )
        transport.connect(username=profile.username, pkey=key)
    else:
        transport.connect(username=profile.username, password=profile.password)
    presented = transport.get_remote_server_key()
    fingerprint = hashlib.sha256(presented.asbytes()).hexdigest()
    if not profile.host_key_fingerprint or fingerprint != profile.host_key_fingerprint:
        transport.close()
        raise RuntimeError(f"Host key verification failed for '{profile.name}' — run Test Connection to review/pin the fingerprint")
    return paramiko.SFTPClient.from_transport(transport)
```

**Superseded during implementation — this sketch has two bugs the real code doesn't have**, both found and fixed with independent verification (see the implementation plan for details):
1. `paramiko.PKey.from_private_key` raises `TypeError` on every key in this project's pinned paramiko version (5.0.0) — it only works on a concrete subclass, not the abstract `PKey` base. The real implementation uses a `_load_sftp_private_key()` helper that sniffs the key type via the `cryptography` library and dispatches to the correct subclass.
2. `transport.connect(...)` above authenticates (sends the password, or does a key-signature exchange) *before* the host-key fingerprint is checked — so credential material reaches an unpinned/wrong host before the check can abort. The real implementation uses `transport.start_client()` (negotiates the transport/host key only, no auth) → fingerprint check → `transport.auth_password()`/`transport.auth_publickey()`, so nothing is ever sent to an unverified host.

`paramiko.PKey.from_private_key` auto-detects RSA/Ed25519/ECDSA key types from the PEM/OpenSSH text, so the profile doesn't need a separate `key_type` field — this part of the reasoning holds, `_load_sftp_private_key` still auto-detects the same way, just via a working code path.

## 5. S3 client

Same `client_kwargs` shape as today's `build_s3_client`, just reading `aws_access_key_id`/`aws_secret_access_key`/`aws_session_token`/`region_name`/`endpoint_url` off the decrypted `FileServerProfile` instead of a request-supplied dict. The existing `endpoint_url` → path-style addressing special case (MinIO/on-prem compatibility) is unchanged.

## 6. Migration (breaking change)

Removing inline `file_source_credentials` breaks any existing saved job/sequence/schedule whose `credentials_ref` currently only works via out-of-band API calls supplying that dict. Rollout note for whoever deploys this: run one query before cutover —

```sql
-- every distinct credentials_ref currently referenced, across the three places it can appear
SELECT DISTINCT json_extract(value, '$.credentials_ref') FROM saved_jobs, json_each(params) ...
```
(exact form depends on SQLite JSON functions available; a Python-side scan over `SavedJob.params`, `ExecutionSequenceVersion.steps_json`, and `ScheduledRun.job_sequence` is simpler and matches how this codebase already does ad-hoc JSON scans elsewhere)

— and create a `FileServerProfile` with a matching `name` for each one found, before the change ships, so nothing silently starts failing validation on the next launch.

## 7. GUI

- New **File Servers** tab: table of profiles (name, kind, host/bucket summary, last-tested status), create/edit modal (fields conditional on `kind`, matching the file_watcher location-kind conditional pattern already used in `tab-launch.html`), masked secret fields preserved on edit exactly like the Configs tab. A **Test Connection** button surfaces the `/test` flow inline, including the fingerprint-accept step for sftp/scp (shown as "Server presented fingerprint `SHA256:...` — Accept & Pin" the first time, plain pass/fail after).
- Launch tab / Sequences tab / Jobs modal: the free-text `fw_credentials_ref` / `mf_source_credentials_ref` / `mf_target_credentials_ref` inputs become `<select>` dropdowns populated from `GET /api/file-servers`, filtered to `kind` matching the location's kind (`sftp`/`scp` share one list).

## 8. Error handling

- Save-time (`create_job`/`update_job`/`import_jobs`): only the pure structural check runs (`credentials_ref` non-empty for a remote kind) — a `credentials_ref` naming a profile that doesn't exist is **not** caught here (see §3's deviation note). `POST /preview-file-mapping` is the one save-adjacent path that IS DB-backed: it calls `resolve_file_server_profile` synchronously and returns 422 with the profile name and expected kind on a missing/mismatched reference.
- Launch/run-time: `resolve_file_server_profile` raises `ValueError` for a missing/mismatched `credentials_ref`, which propagates into `TestRunner`'s generic exception handling and surfaces as a clean `TestStatus.ERROR` result (not a crash) — confirmed by tracing the actual call chain during final verification.
- Runtime: host-key mismatch or unpinned fingerprint → the job step fails with that explicit message (not a raw paramiko traceback), visible in the run's step/error detail same as any other job failure.
- `DELETE` on an in-use profile → 409, listing which saved jobs/sequences/schedules reference it (so the user can retarget them first, not just get a bare "can't delete").
- **Known follow-up, not implemented in this change:** there is no save-time (as opposed to run-time) feedback for a `file_watcher` job's `credentials_ref` pointing at a nonexistent profile. A user won't find out until the job actually runs. Low severity (the run-time failure is clean and clearly worded, and the migration script in §6 lets an operator catch every existing bad reference before cutover), but worth closing in a future change if it proves confusing in practice — likely by threading a `db` session into just the 3 save routes' validation call, not into `job_validation.py`'s pure function itself.

## 9. Testing

- Unit: profile create/update masking + `_preserve_masked_secrets` round-trip (mirrors `tests/unit/test_api.py`'s existing config-masking tests). `build_sftp_client` kwargs/behavior for both `auth_method`s and host-key match/mismatch/unpinned cases — mock `paramiko.Transport` the way `test_multi_file_remote.py` already mocks the credential-resolution layer (no live SSH server in the unit suite, consistent with existing style). `build_s3_client` kwargs sourced from a profile. `resolve_file_server_profile` checks (unknown ref, kind mismatch) — see §3's deviation note; this replaced the originally-planned `job_validation.py` extension.
- e2e: File Servers CRUD including the fingerprint-accept flow (mocked `/test` response); Launch/Sequences dropdowns populated and selectable; a file_watcher job saved with a profile-backed `credentials_ref` round-trips correctly.
- Live e2e (optional follow-up, not required for this change): a real SFTP/S3 target, in the same style as the recent floci AWS live e2e coverage — separate track, separate PR.
