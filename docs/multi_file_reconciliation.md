# Multi-file reconciliation jobs

A reconciliation job can compare more than one file per side by setting
`params.source_mode` to `"multi_file"` and providing a `params.file_mapping`
block. See `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`
for the full design; this page is the quick reference.

## Minimal example

```json
{
  "name": "regional_sales_recon",
  "job_type": "reconciliation",
  "key_columns": ["id"],
  "params": {
    "source_mode": "multi_file",
    "file_mapping": {
      "match_on": ["region", "date"],
      "source": {
        "kind": "local",
        "root": "/spool/bo_exports",
        "pattern": "sales_data_{region}_{date:%Y%m%d}.csv"
      },
      "target": {
        "kind": "local",
        "root": "/exports/finance/sales",
        "pattern": "financials_{region}_{date:%Y%m%d}.dat"
      },
      "unmatched_policy": "fail"
    }
  }
}
```

## How pairing works

- Every `{token}` in a pattern becomes a named capture group; `{date:%Y%m%d}`
  additionally constrains it to 8 digits. Literal characters (including `.`,
  `+`, `(`) are matched literally.
- A no-spec token (`{region}`) captures any run of characters up to the next
  `_`, `.`, `/`, or `\`. If a token value can itself contain `_` or `.`
  (e.g. `north_america`), give it an explicit spec so it captures correctly.
- Files are grouped per side by the tuple of `match_on` token values. A key
  present on both sides becomes one comparison pair; several files sharing a
  key on one side (e.g. sharded exports) are concatenated into a single
  dataset for that side before comparison. Multiple files on *both* sides
  sharing a key is the full many-to-many case and works the same way.
- `match_on` may be omitted (or empty) for jobs that only need dynamic
  discovery, not pairing — every matched file on a side collapses into one
  group, e.g. `pattern: "sales_data_*.csv"`.
- `unmatched_policy` controls what happens when a key exists on only one
  side: `fail` (default) aborts the job, `warn` proceeds and logs it, `ignore`
  proceeds silently. Unmatched groups are always recorded in the result's
  `mismatch_summary` regardless of policy.

## Supported file formats

Both sides are read through the framework's tabular reader: `.csv`, `.tsv`,
`.txt`, `.xlsx`, `.xls`, `.json`, `.xml`, and `.dat`. `.dat` files are parsed
as delimited text (the reader sniffs comma/tab/semicolon/pipe), which covers
the common `financials_{YYYYMMDD}.dat` flat-file baseline shape.

## Result shape

The job produces one aggregate result, same as any other reconciliation job
(one `TestResult` row — no schema change). The top-level counts
(`source_row_count`, `value_mismatch_count`, etc.) are the sums across all
pairs, the status is PASSED only if every pair passed, and each mismatch row
is tagged with its originating pair under a reserved `__pair__` key.
`mismatch_summary` carries the per-pair breakdown:

```json
{
  "pairs_total": 2,
  "pairs_passed": 1,
  "pairs_failed": 1,
  "file_pairs": [
    {"key": {"region": "east", "date": "20260101"}, "status": "PASSED", "source_files": ["..."], "target_files": ["..."], "value_mismatch_count": 0},
    {"key": {"region": "west", "date": "20260101"}, "status": "FAILED", "source_files": ["..."], "target_files": ["..."], "value_mismatch_count": 1}
  ],
  "unmatched_sources": [],
  "unmatched_targets": []
}
```

## Automated mapping (no `match_on` needed)

Set `strategy: "automated"` to have the framework guess pairs by structural
similarity instead of matching on filename tokens:

```json
{
  "file_mapping": {
    "strategy": "automated",
    "source": {"kind": "local", "root": "/spool/exports", "pattern": "*.csv"},
    "target": {"kind": "local", "root": "/baseline", "pattern": "*.dat"},
    "automated_mapping": {
      "similarity_threshold": 0.7,
      "signals": ["filename_tokens", "column_signature", "row_count_ratio"]
    }
  }
}
```

Every source file is scored against every target file using the selected
signals (filename similarity, column-name overlap, row-count ratio),
averaged into one score per candidate pair. Pairs are assigned greedily from
the highest-scoring candidate down, each file used at most once; anything
left over when no remaining candidate clears `similarity_threshold` is
reported as unmatched, same as the explicit strategy. Automated matching
always pairs single files (it does not guess which shards belong together
across several files sharing a key on one side) — use `strategy: "explicit"`
with `match_on` for that.

## Lineage manifest

Every multi_file job execution (explicit or automated) writes
`logs/file_mapping_manifest_{run_id}_{job_name}.json`, recording each pair's
mapping method and (for automated pairs) its similarity score breakdown, plus
every unmatched group -- an audit trail for why files were or weren't paired.

## Parallel execution and failure isolation

Pairs within one job run concurrently, using the run's `max_workers` setting
(the same setting used elsewhere for job-level parallelism, default 4). If
one pair's files can't be read or compared, that pair's result becomes an
`ERROR`-status entry in `mismatch_summary["file_pairs"]` (with the failure
message under `"error"`) instead of crashing the whole job — every other
pair's real result is still computed and reported. The aggregate job status
becomes `ERROR` whenever at least one pair errored (`mismatch_summary`
gains a `pairs_errored` count alongside the existing `pairs_total` /
`pairs_passed` / `pairs_failed`).

## Readiness (waiting for a live spool to finish writing)

For a local root a live process is still actively writing into, add a
`readiness` block to either side's source spec:

```json
{
  "source": {
    "kind": "local",
    "root": "/spool/live_exports",
    "pattern": "sales_data_{region}_{date:%Y%m%d}.csv",
    "readiness": {
      "expected_count": 6,
      "poll_interval_seconds": 5,
      "timeout_seconds": 300
    }
  }
}
```

Discovery polls that side every `poll_interval_seconds` until at least
`expected_count` files match the pattern, or fails the whole job with a
clear error once `timeout_seconds` elapses — so the job doesn't race a
partial spool and compare against files that haven't all landed yet.
`poll_interval_seconds` defaults to 5, `timeout_seconds` to 300.

## Remote sources (S3 and SFTP)

`kind: "s3"` and `kind: "sftp"` are supported alongside `"local"` for both
`source` and `target`. Add `credentials_ref` to look up credentials from
`config_snapshot["file_source_credentials"][credentials_ref]` at run time
(an admin-configured mapping, not stored in the job itself):

```json
{
  "source": {
    "kind": "s3",
    "root": "s3://finance-spool/daily",
    "pattern": "sales_{region}_{date:%Y%m%d}.csv",
    "credentials_ref": "aws_finance"
  }
}
```

One client per `(kind, credentials_ref)` is reused for the whole job
(discovery and every file read), not reopened per file.

**Preview Mapping against s3/sftp (Phase 8):** the job editor's Preview
Mapping button now works for `s3`/`sftp` sources too, not just `local`. Since
there's no saved job yet at preview time to resolve a persisted
`credentials_ref` against a real `config_snapshot`, the request instead
carries an inline `file_source_credentials` field — the same shape as
`config_snapshot["file_source_credentials"]`, but supplied directly in the
`POST /api/jobs/preview-file-mapping` body for that one call only. These
preview-time credentials are never saved anywhere: not to the job, not to
any config. The job's own persisted `credentials_ref` (used for the job's
real, saved-config-backed execution) is untouched by this.

## Job editor UI

The job editor (Launch tab → New/Edit Job → Input Source → "Multiple
Files") supports creating and editing `multi_file` jobs directly — strategy,
`match_on`, automated-mapping threshold/signals, unmatched policy, and
source/target kind + root + pattern + credentials_ref. A **Preview Mapping**
button runs real discovery + pairing before you save the job, via
`POST /api/jobs/preview-file-mapping`. For `s3`/`sftp` sources, additional
preview-only credential fields appear (AWS access key/secret/region/endpoint
for s3; host/port/username/password for sftp) — these are sent inline for
the preview call and are never persisted with the job (see "Preview Mapping
against s3/sftp" above).

## Compare tab ad-hoc multi-file comparison (Phase 7)

You don't need a saved job to run a one-off multi-file reconciliation.
The Compare tab's **Multi-File** sub-tab lets you configure a source/target
file mapping (strategy, `match_on` or automated-mapping signals, key/exclude
columns, unmatched policy) and run it directly:

- **Preview Mapping** reuses the same `POST /api/jobs/preview-file-mapping`
  endpoint the job editor uses — same discovery/pairing logic, no job needs
  to exist first. The Compare tab's own Multi-File sub-tab form is
  `local`-only by deliberate choice (it has no kind selector at all), even
  though the endpoint itself now also supports `s3`/`sftp` for the job
  editor (Phase 8) — see limitations below.
- **Run Comparison** calls `POST /api/compare/multi-file`, which creates a
  real `TestRun` row and runs the comparison in a background task, exactly
  like the existing `bo`/`sql`/`recon-file` ad-hoc flows do (not a stateless
  preview — a persisted run you can revisit later). Every matched pair is
  reconciled **sequentially** (not through the parallel `TestRunner` saved
  jobs use — see "Parallel execution" above), then rolled up into one
  aggregate `TestResult`, the same `mismatch_summary` shape a saved
  multi_file job produces.
- The result view renders the per-pair breakdown (status, row counts,
  mismatch counts, errors) and any unmatched source/target groups, right in
  the Compare tab — no need to switch to the Reports tab.
- Only `kind: "local"` source/target is supported, same restriction as
  Preview Mapping (see limitations below) — this is a synchronous 400 from
  the route, before any `TestRun` row is created.

## Current limitations (Phase 8)

- The Compare tab's Multi-File sub-tab (ad-hoc, no saved job) is still
  `kind: "local"`-only for both preview AND running a comparison — the job
  editor's Preview Mapping button supports `s3`/`sftp` now (Phase 8, via
  inline `file_source_credentials`), but ad-hoc *running* a comparison
  against remote sources is a separate, still-unsolved question (see Phase
  7's scope decisions) and the Compare tab's form has no kind selector to
  even attempt it.
- There's no admin UI for populating a saved job's real
  `config_snapshot["file_source_credentials"]` — an operator has to attach
  it to a `SavedConfig`'s JSON directly via `/api/configs`. Preview's inline
  credentials (Phase 8) sidestep this for previewing, but don't fix it for
  actually running a saved s3/sftp job.
- No SSH-key auth for SFTP, in preview or real execution — only
  username/password (`build_sftp_client` only ever does
  `transport.connect(username=..., password=...)`).
- Readiness polling only applies to `kind: "local"` sources; `bo_live` isn't
  a supported multi_file source kind yet (a separate, later-phase item), so
  it has no readiness support here either. The Compare tab's ad-hoc flow
  doesn't expose readiness configuration at all (a one-off comparison has no
  reason to wait for files that don't exist yet).
- Automated matching pairs single files only; shard-collapsing (many files
  on one side sharing a key) is `strategy: "explicit"` only.
- The Compare tab's ad-hoc multi-file flow runs pairs **sequentially**, not
  in parallel like a saved job's `RunExecutor` path — simplicity over
  throughput, since ad-hoc comparisons are typically a handful of files.
- The Compare tab's "Save/Load Template" feature only captures/restores BO
  sub-tab fields regardless of which sub-tab is active — a pre-existing gap
  affecting `sql`/`recon`/`colstats`/`mmdiff` sub-tabs too, not something
  specific to (or fixed by) the new Multi-File sub-tab.

See `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`
§7 for the full phased roadmap.
