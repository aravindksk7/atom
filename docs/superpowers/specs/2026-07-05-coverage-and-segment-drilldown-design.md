# Coverage Visibility & Mismatch Segment Drill-Down — Design

**Date:** 2026-07-05
**Status:** Approved

## Problem

Two gaps in the ETL Test Framework:

1. **Coverage visibility** — no way to see which tables/columns are covered by
   which tests, where the gaps are, or which tests are flaky (flip-flopping
   pass/fail across runs).
2. **Mismatch root-cause** — reconciliation mismatches are a flat row list;
   nothing localizes them to a segment (date range, region, category), so
   root-cause analysis is manual.

## Approach (chosen)

Compute-on-read services with minimal schema change. One new nullable JSON
column on `test_results`; no new tables. Follows existing repo patterns
(trend TTL cache, post-run hooks, History sub-tabs).

Rejected alternatives:
- **Materialized coverage tables** — faster reads but migrations, stale-data
  risk, more code; the matrix is cheap to compute live.
- **Static HTML reports only** — least work but no API for CI consumption and
  weakest UX.

---

## Feature A — Coverage Visibility

### Universe construction (compute-on-read)

- **Tables:** regex-parse `FROM` / `JOIN` references from every enabled job's
  `query` (plus `params` queries used by `freshness` / `profile` /
  `schema_snapshot` job types). CTE names are excluded via a simple `WITH`
  name blacklist — no full SQL AST parsing.
- **Columns:** union of
  - latest `SchemaSnapshot.columns` per job,
  - `ColumnProfile.column_name` per job,
  - `key_columns` and DQ-rule column references from job configs.

No live-DB `INFORMATION_SCHEMA` introspection in this iteration: the universe
is what the framework has seen. Works in simulation mode.

### Coverage mapping

Per `(table, column)`:

- which jobs touch it (query reference),
- which DQ rules assert on it (rule `column` field),
- which job types cover it (reconciliation / profile / schema_snapshot /
  freshness).

Coverage **level** per column:

| Level | Meaning |
|---|---|
| `tested` | DQ rule asserts on it, or it is a reconciliation key/compared column |
| `observed` | only profiled or schema-snapshotted |
| `untested` | in universe but no job/rule touches it |

### Flakiness detection

Per `(job, query_name)` over the last N runs (default 20, `?window=` query
param):

- `score = status_transitions / (N - 1)`
- Status source: `test_results.status`; `override_status` wins when set.
- Skipped/cancelled runs excluded from the window.
- Fewer than 2 usable runs → score 0.
- `score >= 0.3` flagged flaky.

### Service & API

- `api/services/coverage_service.py` — pure functions; in-process TTL cache
  using the same pattern as the trend cache.
- `api/routes/coverage.py`, behind existing bearer middleware:
  - `GET /api/coverage` →
    ```json
    {
      "tables": [
        {
          "table": "...",
          "columns": [{"column": "...", "level": "tested", "jobs": [...], "rules": [...]}],
          "job_count": 2,
          "tested_pct": 61.5
        }
      ],
      "summary": {"tables": 12, "columns": 240, "tested_pct": 48.3, "observed_pct": 30.1}
    }
    ```
  - `GET /api/coverage/flaky?window=20` →
    `[{"job": "...", "query_name": "...", "score": 0.42, "transitions": 8, "window": 20, "recent_statuses": [...]}]`

### UI

Coverage sub-tab in the History tab (alongside the existing Profile and
Schema sub-tabs):

- matrix table with level color badges,
- gap filter (show `untested` only),
- flaky list with score bars.

---

## Feature B — Mismatch Segment Drill-Down

### Segment column selection

- **Manual (always wins):** `params.segment_columns: ["region", "load_date"]`
  on the job definition.
- **Auto (when unset):** pick from the latest `ColumnProfile` rows where
  `distinct_count <= 50` and the column is not in `key_columns`; take at most
  3, lowest `distinct_count` first.
- No profile history and no manual setting → feature silently skips.

### Inline analysis (during run)

- New field `MismatchRecord.segment_values: dict[str, Any]`, populated by the
  comparison backends when segment columns are configured (value from
  `key_values` when the segment column is a key; otherwise captured from the
  source row during comparison).
- After `ReconciliationEngine` produces a `ReconciliationResult`, the run
  executor groups mismatches by each segment column's value.
- Summary shape per result:
  ```json
  {
    "region": [
      {"value": "EMEA", "mismatch_count": 120, "missing_in_target": 40,
       "missing_in_source": 0, "value_diff": 80, "pct_of_total": 62.5}
    ]
  }
  ```
  Top 20 values per segment column; null segment values bucketed as `"(null)"`.
- Stored in a new **nullable JSON column `test_results.segment_summary`**.
  One additive migration.

### On-demand drill-down (re-query)

- `POST /api/results/{result_id}/drilldown`, body
  `{"segment_column": "...", "environment_pair": "..."}` (pair optional).
- Runs `SELECT <seg>, COUNT(*) FROM (<job query>) GROUP BY <seg>` against both
  source and target; returns per-value row counts side-by-side with deltas.
- Live counts — catches data drift since the stored run.
- Only valid for `reconciliation` job type; 400 otherwise.
  `api_reconciliation` drill-down is deferred.

### UI

Result detail view gains a **Segments** panel:

- bar list per segment column rendered from stored `segment_summary`,
- "Re-query now" button that calls the drilldown endpoint.

### Failure handling

- Inline segment grouping errors never fail the run: caught, logged,
  `segment_summary` remains null.
- Drilldown endpoint returns a structured error payload on DB failure
  (upstream-error style, not a 500 crash).

---

## Testing

- **Unit — coverage service:** universe parsing (quoted and schema-prefixed
  table names, empty-snapshot fallback), level classification, flakiness math
  (override precedence, <2-run history → score 0).
- **Unit — segments:** auto-pick respects distinct-count cutoff, key-column
  exclusion, max 3, no-profile skip; inline grouping covers top-20
  truncation, pct math, `"(null)"` bucketing.
- **API:** route tests for `/api/coverage`, `/api/coverage/flaky`,
  `/drilldown` — auth required, 400 on non-reconciliation drilldown,
  empty-data response shapes.
- **E2E:** run a reconciliation with `segment_columns` set in simulation mode;
  assert `segment_summary` is persisted and matches the UI shape.

## Non-Goals (this iteration)

- Live-DB `INFORMATION_SCHEMA` introspection.
- Column-level lineage.
- SQL parsing beyond FROM/JOIN table extraction.
- Drill-down for `api_reconciliation` jobs.

## Documentation

- README: capabilities bullets for both features; API usage entries for the
  three new endpoints.
