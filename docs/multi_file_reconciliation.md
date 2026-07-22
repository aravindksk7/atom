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

## Current limitations (Phase 2)

- `kind: "local"` only — S3 and SFTP sources are on the roadmap.
- Automated matching pairs single files only; shard-collapsing (many files
  on one side sharing a key) is `strategy: "explicit"` only.
- Pairs are compared sequentially; per-pair parallelism and per-pair failure
  isolation are on the roadmap.
- No dedicated web UI repeater yet; multi-file jobs are created via the API
  (or a hand-written JSON/YAML payload) until the job editor's file-mapping
  UI ships. The lineage manifest is a JSON file on disk, not yet surfaced in
  the UI or run report.

See `docs/superpowers/plans/2026-07-22-multi-file-reconciliation-architecture.md`
§7 for the full phased roadmap.
