# Design: Schema Explorer, Query Preview, Compare Diffs & Multi-format File Compare

**Date:** 2026-06-30  
**Status:** Approved

---

## Problem Summary

Four related gaps were identified in the ETL Framework UI and API:

1. **Job creation query preview** — the job form has a query textarea and a syntax-only validator, but no way to execute the query against a real DB and see sample rows. Users are writing SQL blind.
2. **No schema/table explorer in Config** — the config form only stores credentials. Users cannot browse schemas, tables, or columns from the UI.
3. **Compare results show no diff details and have no export** — after running a BO or recon-file comparison, the results panel shows "Matched"/"Differs" per test but no row-level field differences, and no way to download the results.
4. **Recon-file compare only accepts HTML** — the upload input is hard-coded to `.html`/`.htm`. CSV, Excel, JSON, and TSV reports/files cannot be compared.

---

## Approach: Integrated Schema Explorer + Job Preview + Compare Enhancements

Issues 1 and 2 are solved together as one coherent flow: the schema explorer (in Config) feeds directly into job creation (in the job form). Issues 3 and 4 are independent bounded additions to the Compare tab.

---

## Section 1: Backend API

### 1a. Schema Explorer — `GET /api/configs/{config_id}/schema`

Connects to the MSSQL DB using the saved config credentials, queries `INFORMATION_SCHEMA.COLUMNS`, and returns a list of tables with their columns.

**Response schema:**
```json
[
  {
    "schema": "dbo",
    "table": "orders",
    "columns": [
      { "name": "id", "type": "int" },
      { "name": "amount", "type": "decimal" },
      { "name": "status", "type": "varchar" }
    ]
  }
]
```

- Returns `400` if the config does not exist.
- Returns `400` with detail if the DB connection fails (not a 500 — the user needs actionable feedback).
- Implemented in `api/routes/configs.py` as a new route; uses `DBEngine` from `etl_framework/db/engine.py`.
- SQL used:
  ```sql
  SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE
  FROM INFORMATION_SCHEMA.COLUMNS
  ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
  ```

### 1b. Query Preview — `POST /api/configs/{config_id}/preview-query`

Executes a user-supplied query against the DB (source side) and returns the first N rows.

**Request:**
```json
{ "query": "SELECT * FROM orders", "limit": 50 }
```

**Response:**
```json
{ "columns": ["id", "amount", "status"], "rows": [[1, 123.45, "pending"], [2, 67.00, "shipped"]] }
```

- Wraps the user query as `SELECT TOP {limit} * FROM ({query}) AS _preview` to enforce the row cap safely (no string interpolation of user data into the outer query).
- `limit` is clamped to max 200 server-side.
- Returns `422` with the DB error message if the query fails — shown inline in the job form so the user can fix it without leaving the modal.
- Implemented in `api/routes/configs.py`.

### 1c. Mismatch Download — `GET /api/runs/{run_id}/mismatches/download`

Query param: `format=csv|xlsx|html`

- `csv`: fetches all `MismatchDetail` rows for the run from DB, streams a `text/csv` response. Columns: `test_name`, `key_values`, `column_name`, `source_value`, `target_value`, `mismatch_type`.
- `xlsx`: same data, streamed as `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` via `pandas.DataFrame.to_excel()`.
- `html`: proxies the existing `/api/runs/{run_id}/report` endpoint response.
- Returns `404` if the run does not exist.
- Implemented in `api/routes/runs.py`.

### 1d. Multi-format Recon-file Compare

**`api/services/file_source.py`** — extend `read_tabular` to handle:
- `.json` → `pd.read_json(io.BytesIO(raw))` with `orient='records'` fallback
- `.tsv` / `.txt` → `pd.read_csv(io.BytesIO(raw), sep='\t')`

**`api/services/compare_service.py`** — two changes:

**Change A — store per-metric mismatch details in `run_recon_file_compare`:**
Currently the method creates `ReconciliationResult` objects but never calls `add_mismatch_details`, so there are no queryable diff rows. For each test where metrics differ, create one `MismatchRecord` per differing metric (field_name = metric name, source_value = A's value, target_value = B's value) and call `self._repo.add_mismatch_details(tr.id, mismatches)`. This enables both the diff detail expand in the UI and the download endpoint to work.

**Change B — `_load_recon_source` tabular branch:**
Currently routes to either a stored run or `_load_recon_html`. Add a third branch:

```
if file extension in {.csv, .xlsx, .xls, .json, .tsv, .txt}:
    → read_tabular(path, content_b64, file_name) → DataFrame
    → return {"__tabular__": df}  # sentinel value
```

The caller (`run_recon_file_compare`) detects the sentinel and uses `ReconciliationEngine` on the two DataFrames directly (same path as BO compare), rather than the `_parse_html_report` stat-comparison path.

HTML files keep the existing `_parse_html_report` route.

**`api/schemas.py`** — `ReconFileCompareRequest` gains two optional string fields:
- `file_a_name: str | None = None`
- `file_b_name: str | None = None`

These carry the original filename so the backend can detect the extension when only `file_a_content_b64` is provided (no filesystem path to inspect).

---

## Section 2: Frontend — Config Tab (Schema Explorer)

### Layout

Each config card gains a **"Browse Schema"** button. Clicking it expands a full-width panel directly below the card list (not a modal) and fetches `GET /api/configs/{id}/schema`.

```
┌─────────────────────────────────────────────────────────────┐
│ 🗃️  prod-env   dev   Updated 2 days ago                     │
│ [Edit] [Delete] [Browse Schema ▼]                           │
├─────────────────────────────────────────────────────────────┤
│ Schema Explorer — prod-env              [× Close]           │
│ ┌─ dbo (12 tables)                                          │
│ │  ├─ orders        [▶ Preview] [Use in Job]                │
│ │  │  └─ id (int), amount (decimal), status (varchar)       │
│ │  ├─ customers     [▶ Preview] [Use in Job]                │
│ └─ staging (3 tables)                                       │
└─────────────────────────────────────────────────────────────┘
```

### Interactions

| Action | Behaviour |
|--------|-----------|
| **"Browse Schema"** | Calls `/api/configs/{id}/schema`; shows spinner; renders tree |
| **Click schema row** | Toggle expand/collapse tables under that schema |
| **Click table row** | Toggle expand/collapse column list for that table |
| **"▶ Preview"** | Calls `/api/configs/{id}/preview-query` with `SELECT * FROM [schema].[table]`; renders a scrollable mini grid inline (max 50 rows, max-height 200px, `overflow-x: auto`) |
| **"Use in Job"** | Writes `SELECT * FROM [schema].[table]` to `sessionStorage.etl_pending_query`; navigates to the Launch tab; opens the New Job modal with the query pre-filled; clears the storage key; shows toast "Query pre-filled — finish the job setup" |
| **"× Close"** | Collapses the explorer and clears its data |

### New state in `app()`

```js
schemaExplorerId: null,        // config id whose explorer is open (null = closed)
schemaExplorerData: [],        // [{schema, table, columns: [{name, type}]}]
schemaExplorerLoading: false,
schemaExpandedSchemas: {},     // { 'dbo': true, 'staging': false }
schemaExpandedTables: {},      // { 'dbo.orders': true }
schemaTablePreviews: {},       // { 'dbo.orders': { columns, rows } | 'loading' | 'error:...' }
```

---

## Section 3: Frontend — Job Form (Query Preview)

### Layout

In the job modal's Settings tab, for job types with a SQL query (`reconciliation`, `freshness`, `profile`, `schema_snapshot`), below the query textarea:

```
┌───────────────────────────────────────────────────────────┐
│ SQL Query                                                 │
│ ┌─────────────────────────────────────────────────────┐   │
│ │ SELECT * FROM orders WHERE status = 'pending'       │   │
│ └─────────────────────────────────────────────────────┘   │
│ Preview against: [prod-env (config #2) ▼] [▶ Preview]    │
│ ✓ Query looks valid                                       │
├───────────────────────────────────────────────────────────┤
│ Preview — first 50 rows                   [✕ Close]       │
│ ┌──────┬──────────┬──────────┐                            │
│ │  id  │  amount  │  status  │                            │
│ ├──────┼──────────┼──────────┤                            │
│ │   1  │  123.45  │ pending  │                            │
│ │   2  │   67.00  │ pending  │                            │
│ └──────┴──────────┴──────────┘                            │
└───────────────────────────────────────────────────────────┘
```

### Interactions

- **Config picker**: `<select>` populated from `this.configs`. Defaults to `this.launchSettings.config_id` if set when the modal opens, otherwise blank.
- **"▶ Preview"**: disabled if no query or no config selected. Calls `POST /api/configs/{id}/preview-query`. Shows spinner on button while loading.
- **Error state**: DB/query errors shown as a red inline message below the grid area (not a toast) — keeps user in context to fix the query.
- **"Use in Job" handoff**: if `sessionStorage.etl_pending_query` is set when the modal opens, the query is pre-filled and the key is deleted immediately.

### New state on `jobModal` object

```js
previewConfigId: '',     // which config to preview against
previewLoading: false,
previewResult: null,     // { columns: string[], rows: any[][] } | null
previewError: '',        // inline error message
```

---

## Section 4: Frontend — Compare Tab

### 4a. Recon-file compare — diff detail rows

The result table adds an expand toggle on "Differs" rows:

```
┌──────────────────┬──────┬──────┬────────────────────────┐
│ Test             │ Rows │ Rows │ Match                   │
│                  │  A   │  B   │                         │
├──────────────────┼──────┼──────┼────────────────────────┤
│ ▶ orders_recon   │ 1024 │ 1020 │ ● Differs               │
├──────────────────┴──────┴──────┴────────────────────────┤
│ (expanded)                                               │
│   Field              Value A        Value B              │
│   source_row_count   1024           1020                 │
│   total_issues       5              9                    │
├──────────────────┬──────┬──────┬────────────────────────┤
│ ✓ customers_recon│  500 │  500 │ ✓ Matched               │
└──────────────────┴──────┴──────┴────────────────────────┘
```

The diff detail rows are fetched from the existing `GET /api/runs/{run_id}/results/{result_id}/mismatches` endpoint when the user expands a "Differs" row. This works because `run_recon_file_compare` now stores `MismatchRecord` rows per differing metric (see Section 1d Change A). Each expanded row shows: `column_name` (the metric that differed), `source_value` (A's value), `target_value` (B's value).

State: `fileExpandedDiffs: {}` is keyed by result `id`; the value is either `null` (collapsed), `'loading'`, or an array of mismatch rows.

For BO comparison, the "View" mismatch drawer already exists and stays. Only the download bar is added.

### 4b. Download bar

Added above the results table in both BO and recon-file compare results:

```
[↓ Download ▼]   CSV  |  Excel (.xlsx)  |  HTML Report
```

- **CSV / Excel**: calls `GET /api/runs/{run_id}/mismatches/download?format=csv|xlsx` via `apiBlob()`, then `triggerDownload()` (helper already in `app.js`).
- **HTML**: calls `GET /api/runs/{run_id}/mismatches/download?format=html` (proxies to the report endpoint), triggering a file download.

### 4c. Multi-format file upload

Upload inputs change:
```html
<!-- Before -->
<input type="file" accept=".html,.htm" @change="handleReconFileUpload($event, 'a')" />
<div x-show="fileB64A">HTML loaded</div>

<!-- After -->
<input type="file" accept=".html,.htm,.csv,.xlsx,.xls,.json,.tsv"
       @change="handleReconFileUpload($event, 'a')" />
<div x-show="fileB64A" x-text="fileNameA + ' loaded'"></div>
```

`handleReconFileUpload` stores the original `file.name` in `fileNameA` / `fileNameB`.

The compare request payload gains `file_a_name` and `file_b_name` fields (passed to `ReconFileCompareRequest`).

### New state in `app()`

```js
fileNameA: '',   // original filename for server-side format detection
fileNameB: '',
fileExpandedDiffs: {},  // { result_id: bool } toggle for diff detail rows
```

---

## Files Changed

| File | Change |
|------|--------|
| `api/routes/configs.py` | Add `GET /{id}/schema` and `POST /{id}/preview-query` routes |
| `api/routes/runs.py` | Add `GET /{run_id}/mismatches/download` route |
| `api/services/compare_service.py` | Store per-metric `MismatchRecord` rows in `run_recon_file_compare`; extend `_load_recon_source` to handle tabular file formats |
| `api/services/file_source.py` | Add JSON and TSV support to `read_tabular` |
| `api/schemas.py` | Add `file_a_name`/`file_b_name` to `ReconFileCompareRequest` |
| `frontend/app.js` | Schema explorer state + methods; job preview state + methods; compare download + diff expand methods; multi-format upload handler |
| `frontend/index.html` | Schema explorer panel in Config tab; preview bar in job modal; download bar + diff expand in Compare tab; updated file input accept |

---

## Out of Scope

- Schema explorer for non-MSSQL databases (MSSQL-only via `INFORMATION_SCHEMA`)
- Saving query preview results to a file (the preview is for quick inspection only)
- Diff detail rows for BO comparison (the existing mismatch drawer covers this)
- Parquet file support (heavy dependency, deferred)
