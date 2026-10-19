# PostgreSQL / Aurora PostgreSQL configs and bulk compare-job import

Date: 2026-10-19
Status: approved design, pending implementation plan

## Goals

1. Add PostgreSQL and Aurora PostgreSQL as database types on environment
   configs and named connections, usable everywhere a SQL database source is
   used today (SQL compare, compare jobs, reconciliation jobs, schema
   discovery, query preview, test connection).
2. Let a user import many table-to-table compare jobs at once from a CSV or
   Excel mapping sheet, one row per table pair, with optional per-column
   mapping between differently named columns, across any mix of supported DB
   types.

## Non-goals

- Aurora MySQL or plain MySQL.
- RDS IAM token authentication (password auth only).
- A JSON/YAML bulk format (the existing `/api/jobs/import` already accepts
  full `JobDefinition` JSON).
- Running the imported jobs automatically; the user runs or schedules them
  through the existing selection/schedule tools.

## Findings that shape the design

- `db_type` is `Literal["mssql", "netezza", "oracle"]` on both
  `EnvironmentConfig` and `ConnectionEntry`
  (`etl_framework/config/models.py`). Dialect branches live in
  `DBEngine.__init__` (`etl_framework/db/engine.py`), schema discovery and
  preview in `api/routes/configs.py`, and port/driver defaults in
  `frontend/features/config.js`.
- Saved compare jobs (`job_type: "compare"`) support `compare_type` of `bo`,
  `recon_file`, `matrix`. A Matrix `sql` source does **not** resolve
  `config_id` to a database: `extract_data_source` reads from the app's own
  DB session or an in-memory SQLite fallback. Matrix is therefore not a usable
  vehicle for config-backed DB-to-DB compares.
- The ad hoc `/api/compare/sql` path (`SQLCompareRequest`,
  `CompareService.run_sql_comparison`) already resolves both configs, named
  connections, chunked loading and advanced options correctly. It is not
  available as a saved compare job type.
- `load_in_chunks` in `etl_framework/reconciliation/chunker.py` pages with
  `ORDER BY keys OFFSET n FETCH NEXT m` built by `build_chunk_query`, which
  defaults to SQL Server quoting (`[col]`) and never receives a dialect, so
  chunked loading is wrong for every non-SQL Server database, Postgres
  included. Even where it works, every page re-sorts and skips all prior rows
  (quadratic database work), pages are only stable with unique keys, and all
  pages are concatenated into one frame anyway, so paging saves no memory.

## Part 1: PostgreSQL and Aurora PostgreSQL

### Config model

- `db_type` Literal on `EnvironmentConfig` and `ConnectionEntry` gains
  `"postgresql"` and `"aurora_postgresql"`.
- New optional field `db_sslmode: str = ""` on both models (ignored by other
  DB types).
- `set_db_type_defaults` validator, for both new types, when port is absent or
  still the SQL Server default 1433: port `5432`; when driver is absent or a
  SQL Server ODBC name: driver `psycopg`. Default sslmode: `prefer` for
  `postgresql`, `require` for `aurora_postgresql` (only when the user did not
  set one).
- `db_name` is the database name. Aurora `db_host` is the cluster (writer) or
  reader endpoint; no other Aurora-specific settings.

### Engine

`DBEngine` adds one branch shared by both types:

```
postgresql+psycopg://{quote_plus(user)}:{quote_plus(pwd)}@{host}:{port}/{db}
    ?sslmode={sslmode}&connect_timeout={db_connect_timeout}
```

Pool settings are passed as for the other types.

### Discovery, preview, test connection

In `api/routes/configs.py`:

- Schema discovery: `information_schema.columns` filtered to exclude
  `pg_catalog` and `information_schema`, ordered by schema, table,
  `ordinal_position`.
- Preview: `SELECT * FROM ({query}) AS _preview LIMIT {n}`.
- Test connection uses the same engine path; no change beyond the branch.

### Chunked loading: cursor batches instead of OFFSET paging

Chunked loading changes from repeated `OFFSET`/`FETCH` queries to one query
read in cursor batches. This is database-agnostic, so no per-dialect paging
SQL is needed for Postgres, and Oracle and Netezza chunked loading are fixed
as a side effect.

- `DBEngine.iter_query_batches(query, params, batch_size)` yields DataFrames
  from `pd.read_sql(text(query), conn, params=..., chunksize=batch_size)` on
  a connection opened with `execution_options(stream_results=True)`. On
  psycopg this uses a server-side cursor; on pyodbc, oracledb and nzpy it
  falls back to driver `fetchmany` batching. The connection stays open until
  the generator is exhausted or closed.
- `load_in_chunks(engine, query, key_columns, chunk_size, params, normalize)`
  keeps its signature. When `chunk_size > 0` and the engine has
  `iter_query_batches`, it applies `normalize` per batch and concatenates;
  otherwise it does a single `execute_query` read as today. `key_columns` is
  no longer needed for loading (no `ORDER BY`), so chunked loading also works
  without key columns; the parameter is kept for callers and ignored.
- `chunk_size` keeps its meaning to users ("rows per fetch"); 0 still means a
  single plain read. Query safety stays as today: `SQLCompareRequest` queries
  pass through the same `reject_mutating_sql` checks at the call sites that
  already apply them.
- `build_chunk_query` is removed along with its tests
  (`tests/unit/test_chunker.py`, the chunk-query assertions in
  `tests/test_oracle_integration.py`). The OFFSET-window emulation in the
  file-backed fake engine in `api/services/run_executor.py` (`_apply_window`
  and the `_base` unwrapping) becomes dead and is removed; that fake has no
  `iter_query_batches`, so it gets a single read.
- `build_hash_query` is SQL Server-only (`CHECKSUM_AGG`) and unrelated to
  paging; it is left as is.

Peak memory is unchanged: both full frames are still held for the compare.
Lowering it would need a streaming or hash-partitioned compare and is out of
scope.

Follow-up, not in this work: an optional fast path for very large tables
using Polars with connectorx
(`pl.read_database_uri(..., engine="connectorx", partition_on=key,
partition_num=k)`), giving parallel Arrow-native reads for SQL Server,
Postgres and Oracle (not Netezza) when a numeric partition key exists.

### Frontend

Config form and named-connection form: two new DB type options
("PostgreSQL", "Aurora PostgreSQL"), port/driver defaults matching the
validator, an SSL mode select (`disable`, `prefer`, `require`, `verify-ca`,
`verify-full`) shown for the two Postgres types, and a hint that Aurora host
is the cluster endpoint.

### Dependency

`psycopg[binary]>=3.1` added to `requirements.txt` and as a `postgres` extra
in `pyproject.toml`. A missing driver raises a clear error naming the extra,
mirroring the Netezza handling.

## Part 2: SQL compare as a saved compare job type

- `JobDefinition` validation accepts `compare_type: "sql"` with
  `params.request` validated as `SQLCompareRequest`. Whether both
  `config_id`s exist is checked at run time (same as other compare types);
  the bulk importer checks them up front.
- `CompareService` gains `compare_sql(req) -> ReconciliationResult`, the
  synchronous core extracted from `run_sql_comparison` (which then calls it),
  matching the shape of `compare_recon_file` / `compare_matrix`.
- `RunExecutor._build_case_compare` adds an `sql` branch calling
  `compare_sql`; like the other live compare types it requires live
  connections to be enabled.
- The compare job editor in the frontend renders the `sql` type: config and
  connection selectors per side, query text areas, labels, key and exclude
  columns, chunk size. Jobs created by the bulk importer open and save in this
  editor like any other job.

## Part 3: Bulk compare import

### Mapping sheet

CSV (UTF-8, header row) or XLSX (first sheet). One row per compare job.

| Column | Required | Meaning |
|---|---|---|
| `job_name` | yes | Saved job name; unique within the file. |
| `source_config` | yes | Saved config name for side A. |
| `source_connection` | no | Named connection inside that config. |
| `source_table` | yes | `schema.table`, or a full `SELECT`/`WITH` query. |
| `target_config` | yes | Saved config name for side B. |
| `target_connection` | no | Named connection inside that config. |
| `target_table` | yes | `schema.table`, or a full `SELECT`/`WITH` query. |
| `key_columns` | no | `;`-separated; each `src:tgt` or a bare name. |
| `compare_columns` | no | `;`-separated; each `src:tgt` or a bare name. Blank means all columns. |
| `exclude_columns` | no | `;`-separated target-side names. |
| `source_where` | no | Filter appended as `WHERE` on side A. |
| `target_where` | no | Filter appended as `WHERE` on side B. |
| `numeric_tolerance` | no | Float, default 0. |
| `ignore_case` | no | `true`/`false`, default false. |
| `chunk_size` | no | Int, default 10000 (0 disables chunking). |
| `tags` | no | `;`-separated job tags. |
| `description` | no | Job description. |

Header names are matched case-insensitively with surrounding whitespace
trimmed. Unknown headers are a file-level error.

### SQL generation

For each side, when the `_table` cell is a table reference:

- Column list: when key or compare columns are given, `SELECT` key columns
  then compare columns, each source-side expression aliased to the
  target-side name. When `compare_columns` is blank, key columns are still
  aliased and the rest is `*` only if no key mapping renames anything;
  otherwise the row is an error asking for an explicit `compare_columns` list
  (a renamed key plus `*` would duplicate the column).
- Column references: emitted unquoted when they match a plain identifier
  (`[A-Za-z_][A-Za-z0-9_$#]*`) so the database's own case folding applies, and
  quoted with the side's dialect otherwise.
- Aliases: always quoted with the side's dialect, spelled exactly as the
  target-side name in the sheet, so both frames carry identical column names
  regardless of Postgres lower-case or Oracle upper-case folding.
- Table reference: each dot-separated part handled like a column reference.
- `_where` cells are appended verbatim after `WHERE`; the final query must
  pass `reject_mutating_sql`.

When the `_table` cell is a query (starts with `SELECT` or `WITH`), it is used
as is: column mapping and `_where` must be blank for that side, otherwise the
row is an error.

The generated job is `job_type: "compare"`, `compare_type: "sql"`,
`params.request` a `SQLCompareRequest` with resolved `config_id_a/b`,
connections, `query_a/b`, labels `"{config} {table}"`, key columns
(target-side names), exclude columns, chunk size, and `advanced` built from
tolerance and ignore case.

### Row validation

A row is invalid when: a required cell is empty; a config name does not
exist; a named connection does not exist in that config; the config's DB type
is not a SQL database; a list cell has a malformed `src:tgt` entry;
`ignore_case` is true with blank `compare_columns` (`AdvancedCompareOptions`
needs an explicit case-insensitive column list, which becomes the
target-side compare column names); generated
or supplied SQL fails `reject_mutating_sql`; or the resulting `JobDefinition`
fails validation. Duplicate `job_name` within the file marks every duplicate
row invalid. A `job_name` that already exists is valid and flagged
`will_update`.

### API

All under `api/routes/jobs.py`, using a new service
`api/services/compare_bulk_import.py` (parse, validate, build).

- `GET /api/jobs/bulk-compare/template` returns a sample CSV with the header
  and two example rows.
- `POST /api/jobs/bulk-compare/preview` (multipart file upload) returns
  `{rows: [{row_number, job_name, status: "valid"|"invalid", will_update,
  errors: [str], job: JobDefinition|null, query_a, query_b}], valid_count,
  invalid_count}`. Nothing is saved. File-level errors (unreadable file,
  unknown headers, no rows, over 1000 rows) return 422.
- `POST /api/jobs/bulk-compare/commit` takes `{jobs: [JobDefinition],
  selection_name: str | null}` (the `job` objects from a preview). It reuses
  the `/api/jobs/import` path: admin check for the whole payload first, then
  validate and upsert each job with `job.created` / `job.updated` audit events
  tagged `source: "bulk_compare_import"`. If `selection_name` is set, it
  creates a Job Selection with those jobs in file order; an existing selection
  name is a 409 before any job is saved. All-or-nothing: any invalid job
  fails the request before writes.

### UI

Jobs view gets a "Bulk import compares" action opening a modal:

1. Download template link; file picker (CSV/XLSX).
2. Preview table: row number, job name, source, target, status chip, and an
   expandable cell with errors or the two generated queries. "Will update"
   rows are marked.
3. Optional "Create selection" checkbox with name input.
4. Import button, enabled only when every row is valid; on success, toast and
   job list refresh.

## Error handling

- Missing `psycopg` driver: config test and runs fail with a message naming
  `pip install 'etl-framework[postgres]'`.
- Connection failures surface through the existing test-connection, preview
  and run error paths unchanged.
- Bulk preview never raises per-row problems as HTTP errors; they are row
  results. Commit re-validates server-side and returns 422 with per-job
  errors if anything changed since preview (for example a config renamed).

## Testing

- Unit: config validator defaults for both new types; `DBEngine` URL
  building (engine creation mocked); discovery and preview SQL selection per
  type; `DBEngine.iter_query_batches` against SQLite (batch sizes, params,
  empty result, generator closed early releases the connection);
  `load_in_chunks` batched path, single-read fallback for engines without
  `iter_query_batches`, `normalize` per batch, chunking without key columns.
- Unit: `JobDefinition` with `compare_type: "sql"`; executor `sql` branch with
  a fake `CompareService`.
- Unit: mapping-sheet parser (CSV and XLSX, header normalization, list and
  `src:tgt` parsing), SQL generation per dialect (quoting, aliasing, where,
  query passthrough), every row-validation rule, duplicate names,
  `will_update`.
- API: template, preview, commit (including selection creation, 409 on
  existing selection, admin gate, all-or-nothing).
- Live: a Postgres container in `docker-compose.integration.yml` (on a
  non-default host port to avoid Windows excluded ranges) seeded with tables
  matching an existing SQL Server fixture; runs test connection, discovery,
  preview, and a bulk-imported SQL Server-to-Postgres compare job end to end,
  including a chunked load.
- E2E (Playwright): Postgres/Aurora options in the config form; bulk import
  modal through preview with an invalid row, then a valid file through
  commit with selection creation.
