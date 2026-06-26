# ETL Test Capabilities Expansion — Design Spec

**Date:** 2026-06-26
**Status:** Approved for implementation

## Summary

Expand the ETL Test Framework with 12 new DQ rule types and 4 new job types drawn from industry-standard ETL testing practices (Great Expectations, Soda Core, dbt-tests, Monte Carlo). All changes are additive — no existing jobs, rules, or API contracts are modified.

**Constraints (all must hold):**
- No new runtime dependencies beyond the existing pandas/polars/SQLAlchemy stack
- Every capability accessible via REST API and Alpine.js UI
- All existing job configs, DQ rules, and API contracts continue to work unchanged

---

## Section 1 — New DQ Rule Types

Added to `etl_framework/reconciliation/dq_engine.py` `DQEngine.evaluate()`. Pure-DataFrame rules need no changes to callers. The two DB-engine rules (`referential_check`, `custom_sql_assert`) accept an optional `engine` argument; if absent they log a warning and return no violations (simulation mode safe).

### Pure DataFrame Rules (10)

| Rule type | New params | What it checks |
|---|---|---|
| `completeness_ratio` | `column`, `min_ratio` (float 0–1) | % non-null values ≥ threshold. Looser than `not_null` (which requires 100%). |
| `distinct_count_between` | `column`, `min_value`, `max_value` | Unique value count in `[min, max]`. |
| `column_sum_between` | `column`, `min_value`, `max_value` | Column sum in `[min, max]`. |
| `column_std_dev_between` | `column`, `min_value`, `max_value` | Standard deviation in `[min, max]`. |
| `column_percentile` | `column`, `percentile` (int 0–100), `min_value`, `max_value` | p-th percentile value in `[min, max]`. |
| `column_type_check` | `column`, `expected_type` (`int`/`float`/`date`/`uuid`) | All non-null values castable to the expected type. |
| `column_value_between` | `column`, `min_value`, `max_value` | Every row's value in `[min, max]` (row-level, not aggregate). |
| `cross_column_consistency` | `column_a`, `column_b`, `operator` (`<=`/`<`/`>=`/`>`/`==`) | Column A op Column B holds for every row (e.g. `start_date <= end_date`). |
| `pii_mask_check` | `column`, `pattern` (regex) | Values must NOT match the PII pattern — detects un-masked sensitive data in target. |
| `no_whitespace` | `column` | No leading or trailing whitespace in string column values. |

### DB-Engine Rules (2)

| Rule type | New params | What it checks |
|---|---|---|
| `referential_check` | `column`, `lookup_query` | Every value in `column` exists in the scalar result set of `lookup_query` (FK-style integrity). Requires DB engine. |
| `custom_sql_assert` | `sql`, `operator` (`<`/`<=`/`>`/`>=`/`==`/`!=`), `threshold` | Executes `sql`, expects it to return exactly one row and one column (a scalar); compares scalar op threshold. If the query returns zero rows or multiple rows/columns the rule produces a violation with `ERROR` severity. Replaces the current no-op `custom_sql`. Requires DB engine. |

### DQRule Schema Change

`api/schemas.py` `DQRule` model gets three new optional fields — all default to `None` so existing serialised rules deserialise without change:

```python
percentile: int | None = None        # for column_percentile
operator: str | None = None          # for cross_column_consistency, custom_sql_assert
lookup_query: str | None = None      # for referential_check
```

---

## Section 2 — New Job Types

All four job types use the existing `JobDefinition.job_type` field and `params` JSON blob. The run executor (`api/services/run_executor.py`) dispatches them via the same `job_type` switch used for `bo_report` and `automic_job`. No changes to the `saved_jobs` table.

---

### `freshness` job

**Purpose:** Assert that data in a table was loaded recently enough — fundamental operational reliability check.

**Params (in `params` JSON):**

| Field | Type | Description |
|---|---|---|
| `query` | str | SQL that returns rows containing a timestamp column |
| `timestamp_column` | str | Column name to find MAX of |
| `max_age_hours` | float | Maximum acceptable age of MAX(timestamp_column) relative to now() |

**Execution:**
1. Run `query` against the configured environment DB engine.
2. Find `MAX(timestamp_column)` in the result.
3. Compute `age_hours = (now_utc - max_timestamp).total_seconds() / 3600`.
4. `PASSED` if `age_hours <= max_age_hours`, else `FAILED`.
5. Mismatch detail records the actual age and threshold.
6. In simulation mode (no live connection): returns `PASSED` with a note.

---

### `cross_job_assertion` job

**Purpose:** Compare aggregate metrics between two completed jobs in the same run — the "orders total must equal payments total" pattern used in every serious ETL suite.

**Params (in `params` JSON):**

| Field | Type | Description |
|---|---|---|
| `source_job` | str | Name of the first job (must be in `depends_on`) |
| `source_metric` | str | `sum` / `count` / `distinct_count` |
| `source_column` | str | Column to aggregate (ignored for `count`) |
| `target_job` | str | Name of the second job (must be in `depends_on`) |
| `target_metric` | str | Same options as `source_metric` |
| `target_column` | str | Column to aggregate |
| `tolerance` | float | Absolute or % tolerance (default `0`) |
| `tolerance_type` | str | `absolute` or `percent` (default `absolute`) |

**Metric sources:**
- `count` — read from `test_results.source_row_count` (any job type)
- `sum` / `distinct_count` — read from `column_profiles` table; the referenced job must be a `profile` job that has profiled the specified column. If no profile row exists the executor produces `ERROR`.

**Execution:**
1. After both referenced jobs finish (enforced via `depends_on` DAG), retrieve their stored result rows.
2. Extract the requested metric per the metric source rules above.
3. Compare: `PASSED` if `abs(source_metric - target_metric) <= effective_tolerance`, else `FAILED`.
   - `tolerance_type: absolute` — `effective_tolerance = tolerance`
   - `tolerance_type: percent` — `effective_tolerance = tolerance / 100 * abs(source_metric)`
4. If either referenced job has not completed or is in `ERROR`/`SKIPPED`, this job status becomes `SKIPPED`.

**Constraint:** Both `source_job` and `target_job` must appear in this job's `depends_on` list. The run executor validates this at job-start and produces `ERROR` if violated.

---

### `schema_snapshot` job

**Purpose:** Track schema evolution over time — detect unexpected column additions, removals, or type changes across ETL runs.

**Params (in `params` JSON):**

| Field | Type | Description |
|---|---|---|
| `query` | str | SQL whose result schema is captured |
| `environment` | str | `source` / `target` / `both` (default `both`) |

**Execution:**
1. Run the query (or `SELECT TOP 0 ... / LIMIT 0` for schema-only).
2. Record column names and inferred dtypes into `schema_snapshots` table.
3. Diff against the previous snapshot for the same `job_name` + `environment`.
4. `PASSED` if schema is identical to last snapshot or this is the first run.
5. `FAILED` if any columns were added, removed, or changed type — each change recorded as a mismatch detail row.

**New DB table:**
```sql
CREATE TABLE schema_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name     TEXT NOT NULL,
    environment  TEXT NOT NULL,
    run_id       INTEGER REFERENCES test_runs(id),
    captured_at  DATETIME NOT NULL,
    columns      TEXT NOT NULL  -- JSON: [{name, dtype}, ...]
);
```

---

### `profile` job

**Purpose:** Statistical column profiling with drift detection and auto-rule suggestion — foundation for data observability.

**Params (in `params` JSON):**

| Field | Type | Description |
|---|---|---|
| `query` | str | SQL to profile |
| `columns` | list[str] | Columns to profile (empty = all) |
| `drift_threshold_pct` | float | % change in any metric vs previous profile that triggers a flag (default `20`) |

**Execution:**
1. Run query, compute per-column stats: `null_rate`, `distinct_count`, `min`, `max`, `mean`, `std`, `p25`, `p50`, `p75`, `p95`.
2. Store into `column_profiles` table.
3. Diff each metric against previous profile for same job+column.
4. `PASSED` if all metrics within `drift_threshold_pct` of last profile or first run.
5. `FAILED` if any column's metric drifted beyond threshold — each flagged column recorded as a mismatch detail.

**New DB table:**
```sql
CREATE TABLE column_profiles (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name       TEXT NOT NULL,
    run_id         INTEGER REFERENCES test_runs(id),
    column_name    TEXT NOT NULL,
    null_rate      REAL,
    distinct_count INTEGER,
    min_val        TEXT,
    max_val        TEXT,
    mean_val       REAL,
    std_val        REAL,
    p25            REAL,
    p50            REAL,
    p75            REAL,
    p95            REAL,
    captured_at    DATETIME NOT NULL
);
```

**Feeds:** `POST /api/jobs/{name}/suggest-rules` endpoint uses the latest profile to auto-generate DQ rule JSON (e.g. `column_value_between` from `[min, max]`, `completeness_ratio` from `null_rate`).

---

## Section 3 — API Surface

All new endpoints require existing Bearer token auth. No existing endpoints are modified.

### New route files

| File | Prefix |
|---|---|
| `api/routes/profiles.py` | `/api/jobs` |
| `api/routes/schema_snapshots.py` | `/api/jobs` |

### New endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/jobs/{name}/profile` | Latest column profile for a job |
| `GET` | `/api/jobs/{name}/profile/history` | All profiles over time (for per-column trend charts) |
| `POST` | `/api/jobs/{name}/suggest-rules` | Returns DQ rule JSON auto-generated from latest profile (not saved) |
| `GET` | `/api/jobs/{name}/schema-history` | Schema snapshots over time with sequential diffs |

### Existing endpoints — unchanged

- `/api/jobs` CRUD — new job types use the same `JobDefinition` schema
- `/api/runs` — same trigger; executor dispatches new job types internally
- `/api/runs/{id}/results` — new job types produce `test_results` rows using the same ORM model
- All DQ rule endpoints — new rule types are serialised/deserialised via the existing `rules` array on `JobDefinition`

---

## Section 4 — UI Changes

All changes are additive to `frontend/app.js` and `frontend/index.html`.

### Launch tab — Job editor

- DQ Rule type dropdown: add all 12 new types. Show/hide fields conditionally:
  - `percentile` input → visible for `column_percentile` only
  - `operator` select → visible for `cross_column_consistency` and `custom_sql_assert`
  - `lookup_query` textarea → visible for `referential_check`
  - `column_b` input → visible for `cross_column_consistency`
- Job type selector: add `freshness`, `cross_job_assertion`, `schema_snapshot`, `profile`
- Each new type shows a dedicated param form (same pattern as existing `bo_report`/`automic_job` forms)

### History tab — two new sub-tabs

**Profile sub-tab:**
- Job selector dropdown
- Table view: latest profile — one row per column, columns for `null_rate`, `distinct_count`, `p50`, `mean`, `std`
- Line chart: select column + metric, plot value over run history (uses `/api/jobs/{name}/profile/history`)

**Schema sub-tab:**
- Job selector dropdown
- Timeline list: each entry shows `captured_at`, `environment`, and a diff summary (N added, N removed, N changed)
- Expand any entry to see the full column list with added (green) / removed (red) / changed (yellow) highlights

---

## Section 5 — Testing

### New unit test files

| File | Covers |
|---|---|
| `tests/unit/test_dq_engine_new_rules.py` | All 12 new rule types: pass case, fail case, edge cases (empty df, nulls, wrong column name) |
| `tests/unit/test_freshness_executor.py` | Freshness job with mocked DB engine; simulation mode pass |
| `tests/unit/test_cross_job_assertion.py` | Metric extraction and comparison logic; tolerance absolute and percent; SKIPPED on missing upstream |
| `tests/unit/test_schema_snapshot.py` | Schema diff: added column, removed column, type change, identical schema |
| `tests/unit/test_profile_job.py` | Per-column stat computation; drift detection at threshold boundary |

### Property-based tests

`tests/property/test_dq_rules_property.py` — uses `hypothesis` (added to `[dev]` extras only, no runtime dep) to fuzz all 12 new DQ rules with random DataFrames. Invariant: a rule never raises an unhandled exception, always returns a list of `DQViolation`.

### Integration tests

`tests/integration/test_api_frontend_smoke.py` extended with smoke cases for:
- `GET /api/jobs/{name}/profile` returns 200 or 404
- `GET /api/jobs/{name}/schema-history` returns 200
- `POST /api/jobs/{name}/suggest-rules` returns valid rule JSON

---

## Rollout Notes

- `referential_check` and `custom_sql_assert` skip gracefully when no DB engine is available (simulation mode). They log a `WARNING`, produce no violation, and never raise.
- `cross_job_assertion` jobs with unresolved upstream produce `SKIPPED` — same behaviour as existing DAG upstream-failure skip.
- `schema_snapshot` and `profile` jobs on first run always produce `PASSED` (no previous snapshot/profile to diff against).
- `hypothesis` is a dev-only dependency — not required at runtime.
- New DB tables are created by `init_db()` at startup using the existing column-addition pattern — no manual migration required.
- The `suggest-rules` endpoint returns rule JSON but does not save it — the user reviews and applies via the job editor.
