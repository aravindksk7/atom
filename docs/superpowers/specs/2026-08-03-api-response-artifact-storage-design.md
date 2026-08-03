# Storing API endpoint responses as run artifacts

Date: 2026-08-03

## Problem

`APIEndpointClient` converts every response straight into a DataFrame and
discards the bytes ([`etl_framework/rest_api/client.py`
`_parse_response`](../../../etl_framework/rest_api/client.py)). Nothing that a
REST API pull returns is ever written to disk.

Two consequences:

- An API-backed run cannot later serve as a source in the Compare tab, while a
  SAP BO run can — the BO live pull persists what it fetched
  (`RunExecutor._build_case_bo_live_reconciliation`, the
  `_persist_run_data_artifact` call).
- When a pull fails, the response that caused it is gone. A `Cannot parse API
  response as json` carries no status, no content type and no body, so the
  failure cannot name its own cause.

## Decision

Every API pull persists the raw bytes it received, under the server's existing
artifact root. This mirrors the BO live pull and closes both gaps with one
mechanism.

## Where files go

Reuse `api/services/upload_store.py` — no new root, no new retention scheme.

- Root: `UPLOAD_ROOT`, i.e. `reports/uploads`, overridable with the
  `COMPARE_UPLOAD_ROOT` environment variable.
- Run-scoped pulls: `UPLOAD_ROOT/<run_id>/`.
- Pulls with no run: `UPLOAD_ROOT/adhoc_<config_id>_<safe_endpoint>_<UTC
  timestamp>/`, e.g. `adhoc_3_orders_20260803T211408Z/`.

The ad-hoc directory is deliberately a **direct child** of the root.
`cleanup_expired_uploads` iterates direct children and removes any directory
older than the cutoff regardless of its name, so ad-hoc pulls are swept by the
existing retention code under the existing `upload_retention_days` setting
(default 30) with no new code.

Known footprint: that sweep runs only at application startup (`api/main.py`).
On a server that stays up for months, ad-hoc directories accumulate until the
next restart. This is pre-existing behaviour for run directories and is not
changed here.

## What lands on disk

A paginated pull produces one file per page plus one assembled file:

```
reports/uploads/<run_id>/
  api_orders_p1.json      raw, exactly as received
  api_orders_p2.json
  api_orders.csv          assembled frame, recorded as data_artifact_path
```

Raw pages preserve fidelity for forensics. The assembled frame is written once
per source as a single re-readable CSV.

**No `data_artifact_path` is recorded by any call site in this design.** Every
run-scoped API pull that exists today is one of *two* sources — the
`api_reconciliation` job pulls a source and a target, and a compare pulls A and
B. `resolve_row_diffable_artifact` returns `None` unless a run has exactly one
artifact path, so recording one side would either misrepresent what the run
consumed or silently make the run undiffable. The assembled CSV is written and
discoverable on disk; wiring it to `data_artifact_path` waits for a
single-source API run to exist, and is deliberately not invented here.

### Filenames

Derived in this order:

1. The `filename=` value from a `Content-Disposition` response header, when
   present. This is the literal "the API response downloads a file" case.
2. Otherwise `<endpoint>_p<N>` plus an extension from `Content-Type`:
   `application/json` → `.json`, `text/csv` → `.csv`, the spreadsheet mime →
   `.xlsx`, anything unrecognised → `.bin`.

Every name passes through the existing `_safe_filename`. Collisions are
resolved by the existing `_persist_bytes` suffixing (`_2`, `_3`, …).

## Architecture

`APIEndpointClient.fetch_dataframe` gains an optional `on_response` callback and
passes it down to `_request`, which invokes it with
`(raw_bytes, page_number, response)`.

The call site inside the client matters and is fixed: **`_request` invokes the
callback immediately after the response is received, before the
`status_code >= 400` check**. Invoking it from `fetch_dataframe` instead would
mean no 4xx or 5xx response is ever stored, because `_request` raises before
returning. `_request` therefore also takes the current page number so the
callback can name the file. The API exchange inspector design depends on this
same ordering.

The client does not write files. `etl_framework/` must not import
`api/services/` — the dependency runs one way, `api/` → `etl_framework/`, and
`upload_store` lives in `api/services/`. A callback keeps the client a pure HTTP
client, keeps filesystem layout and retention policy in the layer that already
owns them, and hands bytes off per page instead of accumulating every page in
memory alongside the concatenated frame.

New module `api/services/api_artifact.py`:

```python
def build_api_response_sink(dest_dir: Path, endpoint_name: str) -> Callable
```

Returns the callback. Per-file size cap reuses `RUN_DATA_ARTIFACT_MAX_BYTES`
(`RUN_DATA_ARTIFACT_MAX_MB`, default 256).

### Call sites

| Call site | Destination | Rationale |
|---|---|---|
| `RunExecutor._build_case_api_reconciliation` | `UPLOAD_ROOT/<run_id>/` via `self._run_id` | Already run-scoped; mirrors the BO job |
| `CompareService._load_api_source` from `run_bo_comparison` | `UPLOAD_ROOT/<run_id>/` | `run_id` exists at the caller, needs threading down |
| `CompareService.run_column_stats` | ad-hoc directory | User-facing pull, but no run is persisted for it |
| `AdapterService.test_api_endpoint` / `preview_api_endpoint` | ad-hoc directory | No run exists |
| `difference_export._write_bo_compare` | **no sink** | Re-pulls sources from a stored payload to build an export; those bytes were already written by the run that produced the payload |

Threading: `_load_bo_source(src, doc_id, report_id, run_id=None)` →
`_load_api_source(src, run_id)`. `None` means "ad-hoc directory", not "store
nothing", so there is no silent gap. The `difference_export` exclusion is
explicit `on_response=None` with a comment stating why.

Both sources of one comparison land in the same run directory, distinguished by
endpoint name. Two sources naming the same endpoint collide, and `_persist_bytes`
resolves that by suffixing — B's pages become `api_orders_p1_2.json`. This is
the existing house behaviour; no second de-duplication scheme is introduced.

## Security

Two untrusted strings reach the filesystem:

- **The `Content-Disposition` filename**, chosen by the remote server. A
  response claiming `filename="../../../etc/passwd"` must not escape the
  destination. `_safe_filename` takes `Path(name).name` first, which removes
  traversal and absolute paths, then strips everything outside
  `[A-Za-z0-9._-]`. The write target is always `dest_dir / safe_name`, never
  built from the header directly.
- **The endpoint name**, from config JSON. Same treatment.

Reads stay behind `resolve_run_data_artifact`, which re-resolves the path and
enforces `relative_to(UPLOAD_ROOT)`, so a tampered `data_artifact_path` in the
database cannot become an arbitrary file read.

## Error handling

Every write is best-effort and must never turn a successful pull into a failed
run — the same contract as `persist_run_data_artifact`:

- Over the size cap: log and skip the file.
- `OSError` on write: log and continue.
- The sink never propagates an exception to the caller.

## Testing

- `_safe_filename` neutralises `../`, absolute POSIX and Windows paths, and a
  null byte arriving via `Content-Disposition`.
- Extension derived from `Content-Type` for json / csv / xlsx / unknown →
  `.bin`; a `Content-Disposition` filename wins when present.
- A multi-page pull writes `_p1` and `_p2` plus one assembled `.csv`, and only
  the assembled path is recorded as `data_artifact_path`.
- An over-cap response is skipped and the pull still succeeds.
- An `OSError` on write is swallowed and the pull still succeeds.
- `_write_bo_compare` passes no sink: nothing is written on the export re-pull.
- An ad-hoc directory is a direct child of `UPLOAD_ROOT` and is removed by
  `cleanup_expired_uploads` once past the cutoff.

## Out of scope

- A scheduled retention sweep. Startup-only sweeping is pre-existing.
- Any change to how responses are parsed into frames.
- The silent discard of an unparseable body in the config UI
  (`frontend/features/config.js`, `catch { body = null }`). Tracked separately.
