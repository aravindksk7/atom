# File Transfer Job — Design

**Goal:** Add a `file_transfer` job type that copies files from a source location to a destination location, where each side can be a local folder, S3, or SFTP/SCP. It drops into a sequence like any other job, so it can stage files for a reconciliation step (`file_watcher` → `file_transfer` → reconciliation) or serve any other copy use case on its own.

**Tech stack:** Python (FastAPI, Pydantic), paramiko, boto3, existing `RunExecutor` / DAG step machinery, `FileServerProfile` credentials, Alpine.js frontend, pytest / Playwright.

---

## 1. Context and decisions

Remote file access in the codebase is read-only today. `api/services/multi_file_remote.py` builds S3 and SFTP clients from a `FileServerProfile` (looked up by `credentials_ref`) and only discovers and reads files. Nothing writes to S3 or SFTP. The `scp` kind is an alias for the SFTP client (`resolve_file_server_profile` accepts either profile kind for an sftp/scp spec) and there is no separate SCP transport. `file_watcher` (`docs/superpowers/specs/2026-09-14-file-watcher-job-design.md`) established the pattern this design follows: a new `job_type`, dispatched in `RunExecutor._build_case`, validated in both `api/schemas.py` and `etl_framework/runner/job_validation.py`, with results mapped onto the existing `ReconciliationResult` shape.

Decisions made while scoping:

| Question | Decision |
|---|---|
| How is copy exposed? | New standalone `file_transfer` job type, usable in any sequence. Not embedded in multi-file reconciliation. |
| Which endpoints? | Any to any: `local`, `s3`, `sftp`/`scp` as source or destination, including remote-to-remote (streamed through the app server). |
| How does reconciliation use copied files? | User configures a fixed destination and points the reconciliation `file_mapping` at the same location. No runtime parameter injection (same stance as `file_watcher` v1). |
| Destination collision | `on_exists` = `fail` \| `overwrite` \| `skip`, default `fail`. |
| Extras in v1 | Preserve subfolder structure (with opt-in recursion, see §3). Size verification after copy is always on. No checksum, move, or dry run. |
| SCP | Stays an SFTP alias. Hosts with no SFTP subsystem are out of scope. |

## 2. Params and schema

`api/schemas.py` `JobDefinition.job_type` Literal gains `"file_transfer"`.

```python
params = {
    "source": {
        "kind": "local" | "s3" | "sftp" | "scp",
        "root": str,                        # folder path / "s3://bucket/prefix" / sftp dir
        "pattern": str,                     # same token pattern syntax as multi-file sources
        "credentials_ref": str | None,      # required for s3/sftp/scp
    },
    "destination": {
        "kind": "local" | "s3" | "sftp" | "scp",
        "root": str,
        "credentials_ref": str | None,      # required for s3/sftp/scp
    },
    "on_exists": "fail" | "overwrite" | "skip",   # default "fail"
    "recursive": bool,                             # default false
    "preserve_structure": bool,                    # default false
}
```

Validation, implemented in both `validate_reconciliation_contract` (`api/schemas.py`) and a new `_validate_file_transfer` in `etl_framework/runner/job_validation.py`, mirroring how `file_watcher` is validated in both places:

- `source` and `destination` must be objects.
- `kind` on each side must be one of `local`, `s3`, `sftp`, `scp`.
- `source` requires `root` and `pattern`. `destination` requires `root`.
- A non-local kind on either side requires a non-empty `credentials_ref`.
- `on_exists`, if given, must be one of the three values.
- `recursive` and `preserve_structure`, if given, must be booleans.
- `preserve_structure: true` requires `recursive: true` (flat discovery has no structure to preserve).
- Whether the referenced profile exists and matches the kind is checked at run time by `resolve_file_server_profile`, not at save time (same known limitation as `file_watcher`).

`scp` is normalized to `sftp` before it reaches any adapter or `FileSourceSpec`.

## 3. Discovery: optional recursion

All three existing discovery functions in `etl_framework/reconciliation/file_mapping.py` (`discover_local_files`, `discover_s3_files`, `discover_sftp_files`) list only files *directly under* the root, and match `pattern` (a token pattern compiled by `compile_token_pattern`, not a shell glob) against the basename. Preserving subfolder structure is meaningless without recursion, so each function gains a keyword argument `recursive: bool = False`.

- Default `False` keeps every existing caller (multi-file reconciliation, `file_watcher`) unchanged.
- `recursive=True` walks subdirectories. The pattern still matches the basename only.
  - local: `os.walk`, sorted, symlinks not followed.
  - s3: skips the "no `/` in the relative key" filter.
  - sftp: recurses into directories via `listdir_attr` and `stat.S_ISDIR`, with a depth cap of 20 to guard against pathological trees.
- `DiscoveredFile` is unchanged. The relative path under the source root is derived from `DiscoveredFile.path` and the source root.

## 4. Architecture

New module `api/services/file_transfer.py`. It owns copy logic so `RunExecutor` stays a thin dispatcher and `multi_file_remote.py` does not grow an N×N branch matrix.

- **Endpoint adapters**, one small class per kind (`local`, `s3`, `sftp`), each with `open_read(path)`, `write(path, stream)`, `exists(path)`, `size(path)`, `delete(path)`. Clients come from `RemoteFileSourceSession._client_for`, so credential resolution, host-key verification, and the one-client-per-`(kind, credentials_ref)` cache are reused unchanged. Local paths go through `resolve_allowed_path` for both source and destination, so writes stay inside `SERVER_FILE_ALLOWED_DIRS`.
- **Planner**, `plan_transfer(source_files, source_root, destination_adapter, destination_root, on_exists, preserve_structure)`, a pure function that returns the list of `(source_file, destination_key, action)`.
- **Executor**, `run_transfer(plan, source_adapter, destination_adapter)`, streams and verifies each planned copy.
- `RunExecutor._build_case` gains `if job.job_type == "file_transfer": return self._build_case_file_transfer(job)`, following `file_watcher`.

## 5. Plan phase

1. Discover source files (`recursive` as configured).
2. Compute each destination key.
   - `preserve_structure` false: `destination.root` + basename (flatten).
   - `preserve_structure` true: `destination.root` + path relative to `source.root`.
   - The relative path is normalized. Any `..` segment, absolute path, or result that escapes `destination.root` is rejected.
3. Reject duplicate destination keys within the plan, regardless of `on_exists`. This happens when flattening a recursive discovery where two subfolders hold the same basename, and it prevents one file silently overwriting another in the same run.
4. Reject any plan entry where source and destination resolve to the same object, so `overwrite` can never truncate its own source.
5. Apply `on_exists` against the destination.
   - `fail`: any existing destination file aborts the whole job before a byte is written.
   - `skip`: existing files are dropped from the plan and counted as skipped.
   - `overwrite`: all files stay in the plan.
6. Zero source files matched: the job fails with "no files matching `<pattern>` under `<root>`".

## 6. Execute phase

Files are copied one at a time (sequential in v1), streaming in chunks with no whole-file buffering.

- Readers: local `open(path, "rb")`; sftp `client.open(path, "rb")`; s3 the `get_object` `Body` stream.
- Writers, all atomic from the point of view of anything watching the destination:
  - local: write to `<name>.part`, then `os.replace`.
  - sftp: `putfo` to `<name>.part`, then `rename` (replacing an existing file for `overwrite`).
  - s3: `upload_fileobj` (multipart), visible only on completion.
- Destination directories are created as needed: local `mkdir(parents=True)`, an sftp recursive mkdir helper, nothing for S3.
- A counting wrapper around the reader tracks bytes streamed.
- After each copy, stat the destination (`os.stat` / `client.stat` / `head_object`) and compare its size to the bytes streamed. A mismatch deletes the destination file on a best-effort basis and fails the step.
- Stop on the first per-file failure. Files already copied stay in place (no rollback).

## 7. Result mapping

Onto `ReconciliationResult` (`etl_framework/reconciliation/models.py`), no schema change:

- All planned files copied: `PASSED`. `mismatch_summary = {copied, skipped, bytes, files: [...]}` and `data_artifact_path` is left unset (the run-level Compare row-diff counts these paths, so a directory or URI here would disable it); the destination root is reported as `mismatch_summary.destination_root` instead.
- `on_exists=fail` collision, size-verify mismatch, per-file copy failure, no files matched, plan rejection (duplicate key, path escape, same object): `FAILED`. `mismatch_summary` adds `{failed_file, error}` where applicable.
- Connection, credential, host-key, or missing-bucket problems: `ERROR`, via the existing `build_*_client` messages. Secrets are never logged.

Restart note: restarting a failed sequence with `on_exists=fail` fails in the plan phase on files already copied by the earlier attempt. Users pick `skip` for an idempotent resume. The help text says so.

## 8. Frontend

- Job editor and sequence step editor gain a **File Transfer** option beside File Watcher.
- Form: Source panel (kind, root, pattern, profile) and Destination panel (kind, root, profile). Profile dropdowns come from `GET /api/file-servers`, filtered by kind (sftp and scp share one list). Below: `on_exists` select, "Include subfolders" checkbox (`recursive`), and "Preserve subfolder structure" checkbox (`preserve_structure`, enabled only when subfolders are included).
- Help text in `frontend/help-content.js` covers: SCP is really SFTP; the destination profile needs write permission on the target; use `skip` for idempotent restarts; the reconciliation step must point at the same destination.
- Run detail shows copied/skipped counts, total bytes, the failed file if any, and the destination path in the existing result panel. No new component.

## 9. Testing

- Unit, `tests/unit/test_file_transfer.py`:
  - destination key computation (flatten vs preserve), path-escape rejection, duplicate-destination-key rejection, same-object rejection.
  - `on_exists` plan behavior for all three modes, including `fail` writing nothing.
  - size-mismatch cleanup and stop-on-first-failure summary.
  - adapter read/write for local, S3 (the S3 mock pattern already used in `tests/unit/test_s3_client.py`) and SFTP (fake client, as in `tests/unit/test_multi_file_remote.py`).
  - `.part` then rename atomicity.
- Unit, `tests/unit/test_file_mapping.py`: `recursive=True` for each discovery function, default behavior unchanged, sftp depth cap.
- Unit, `tests/unit/test_job_validation.py` and `tests/unit/test_api.py`: every rejection case in §2 plus valid configs for each kind pair.
- Unit, executor: `_build_case_file_transfer` maps to `PASSED`, `FAILED`, `ERROR` correctly.
- e2e (Playwright): create a `file_transfer` job in the UI, run it in a sequence between a watcher and a reconciliation step, check the run detail. Use `node node_modules/@playwright/test/cli.js` per the repo's known npx quirk.
- Live e2e against the docker-compose SFTP and MinIO services is an optional follow-up, separate from this change.

## 10. Out of scope (deferred)

- Raw SCP-only hosts (no SFTP subsystem).
- Move mode (delete source), checksum verification, dry run.
- Automatic injection of copied paths into the next step's params.
- Parallel file copies.
- Rollback of already-copied files after a failure.
- Save-time check that a referenced profile exists.
