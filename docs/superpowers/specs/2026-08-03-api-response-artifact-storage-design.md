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
artifact root. This mirrors the BO live pull and closes the **second** gap: a
failed pull can now be read back off disk, status, content type and body
intact, instead of the response being discarded before anyone can look at it.

It does **not** close the first gap. An API-backed run still cannot serve as a
Compare source, because no call site records a `data_artifact_path` — see
"What lands on disk". Reusing these bytes in Compare waits for a single-source
API run to exist.

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

A paginated pull produces one file per page. Raw pages, and nothing else:

```
reports/uploads/<run_id>/
  orders_p1.json      raw, exactly as received
  orders_p2.json
```

Raw pages preserve fidelity for forensics.

**No assembled frame is written and no `data_artifact_path` is recorded by any
call site in this design.** Every run-scoped API pull that exists today is one
of *two* sources — the `api_reconciliation` job pulls a source and a target, and
a compare pulls A and B. `resolve_row_diffable_artifact` returns `None` unless a
run has exactly one artifact path, so recording one side would either
misrepresent what the run consumed or silently make the run undiffable. With
nothing to record, an assembled CSV would be a file no code path ever reads, so
it is not written either. Both wait for a single-source API run to exist, and
are deliberately not invented here.

### Filenames

Derived in this order:

1. A filename from a `Content-Disposition` response header, when present. This
   is the literal "the API response downloads a file" case. RFC 6266 defines
   *two* filename parameters, and `filename*` takes precedence over the plain
   `filename` whenever it is present and parseable (RFC 6266 §4.3):

   - `filename*=charset'language'percent-encoded` — an RFC 5987 ext-value. It
     is split on its two `'` separators and the value is percent-decoded in
     the declared charset (empty charset defaults to UTF-8). An unknown
     charset or an undecodable value is **not** fatal: it falls back to the
     plain `filename` rather than raising and breaking the pull.
   - `filename=value` — a quoted-string or a bare token. Surrounding double
     quotes are stripped.

   Parameter names are matched case-insensitively; on a duplicate parameter
   the first occurrence wins.

   The header is walked by a small left-to-right tokenizer that tracks quote
   state — it replaced the original regex `.search()`, which cannot implement
   this correctly. A regex over the whole header has no notion of a *parameter
   boundary*, so a vendor parameter such as `original-filename=` matches as
   though it were `filename=`; and no notion of *quote state*, so a `;` or a
   literal `filename*=` sitting inside a quoted value looks like a real
   delimiter or parameter. The tokenizer splits on `;` only when not inside
   quotes, skips the leading disposition-type token, and compares the whole
   parameter name — fixing both by construction.

2. Otherwise `<endpoint>_p<N>` plus an extension from `Content-Type`. Any
   parameters (`; charset=…`) are stripped and the type is matched
   case-insensitively:

   | `Content-Type` | Extension |
   |---|---|
   | `application/json`, `text/json` | `.json` |
   | `text/csv`, `application/csv` | `.csv` |
   | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | `.xlsx` |
   | `application/vnd.ms-excel` | `.xls` |
   | `application/xml`, `text/xml` | `.xml` |
   | `text/plain` | `.txt` |
   | anything unrecognised | `.bin` |

Every name passes through `safe_filename` — public in `upload_store`, renamed
from the original private `_safe_filename` once a second module needed it.
Collisions are resolved by `unique_path`, the suffixing loop (`_2`, `_3`, …)
extracted out of `_persist_bytes` into its own public helper. The sink calls
`unique_path` and writes the bytes itself rather than going through
`_persist_bytes`, which takes a `run_id` and therefore cannot address an ad-hoc
destination directory.

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

Threading:

```python
_load_bo_source(src, doc_id, report_id, run_id=None, *, store_responses=True)
_load_api_source(src, run_id=None, *, store_responses=True)
```

Whether to store and where to store are two separate decisions, so they are two
separate keyword-only parameters:

- `store_responses` decides whether to keep the bytes **at all**. `False`
  builds no sink and derives no destination.
- `run_id` only picks **where** they go, once keeping them has been decided.
  `None` means "no run behind this pull" (column stats) and selects the ad-hoc
  directory — it never means "store nothing".

The `difference_export` exclusion is therefore `store_responses=False`, not
`on_response=None` and not `run_id=None`. Overloading `run_id=None` to mean
"store nothing" was rejected because it already means "ad-hoc directory": the
export re-pull would have silently written the bytes this spec promises not to
write, into a fresh ad-hoc directory, with nothing to flag it.

Both sources of one comparison land in the same run directory, distinguished by
endpoint name. Two sources naming the same endpoint collide, and `unique_path`
resolves that by suffixing — B's pages become `orders_p1_2.json`. This is the
existing house behaviour; no second de-duplication scheme is introduced.

## Security

Two untrusted strings reach the filesystem:

- **The `Content-Disposition` filename**, chosen by the remote server. A
  response claiming `filename="../../../etc/passwd"` must not escape the
  destination. `safe_filename` takes `Path(name).name` first, which removes
  traversal and absolute paths, then strips everything outside
  `[A-Za-z0-9._-]` and truncates to 160 characters. The write target is always
  `dest_dir / safe_name`, never built from the header directly.
- **The endpoint name**, from config JSON. Same treatment.

`safe_filename` additionally defuses the Windows reserved device names
(`_WINDOWS_RESERVED_STEMS`: `CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`,
`LPT1`-`LPT9`) by prefixing an underscore. Windows resolves these stems to a
device regardless of extension or case. A reviewer on this branch demonstrated
the consequence directly: `Path(tmp/"NUL").write_bytes(b"hello")` returns
successfully, `Path(tmp/"NUL").exists()` is `True`, and the directory is empty
— the bytes went to the device and are gone. A remote server sending
`Content-Disposition: attachment; filename="NUL"` could therefore make a
pull's evidence silently evaporate with no error raised and no file to find,
which is precisely the blindness this feature exists to cure. The stem is
tested before the extension is considered, so `nul.json` is stored as
`_nul.json`.

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

- `safe_filename` neutralises `../`, absolute POSIX and Windows paths, UNC and
  drive-relative paths and an NTFS alternate-data-stream suffix arriving via
  `Content-Disposition`, and defuses the Windows reserved device stems.
- Extension derived from `Content-Type` for json / csv / xlsx / unknown →
  `.bin`; a `Content-Disposition` filename wins when present; `filename*` wins
  over `filename` regardless of header order, and a malformed `filename*`
  falls back to the plain one.
- A vendor parameter ending in `filename` does not hijack the match, and a
  `;` or a `filename*=` inside a quoted value is treated as literal data.
- An over-cap response is skipped and the pull still succeeds.
- An `OSError` on write is swallowed and the pull still succeeds.
- `_write_bo_compare` passes `store_responses=False`: nothing is written on the
  export re-pull.
- An ad-hoc directory is a direct child of `UPLOAD_ROOT` and is removed by
  `cleanup_expired_uploads` once past the cutoff.

## Out of scope

- A scheduled retention sweep. Startup-only sweeping is pre-existing.
- Any change to how responses are parsed into frames.
- The silent discard of an unparseable body in the config UI
  (`frontend/features/config.js`, `catch { body = null }`). Tracked separately.
