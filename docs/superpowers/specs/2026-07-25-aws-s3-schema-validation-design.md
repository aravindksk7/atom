# AWS S3 Storage & Schema Validation — Design (Spec 1 of 4)

**Date:** 2026-07-25
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)

## Context

The ETL test framework (`etl_framework/`) currently supports SQL Server
reconciliation, SAP BO, and SAP DS. This spec adds native AWS data-platform
support. The full request spans four subsystems; it is **decomposed into four
independent spec → plan → build cycles**:

1. **`aws_s3`** — S3 Storage & Schema Validation *(this spec, foundational)*
2. **`aws_glue`** — Glue & Catalog Integration *(depends on Spec 1 core)*
3. **`aws_athena`** — Athena Query Execution *(depends on Spec 1 core)*
4. **`airflow`** — DAG Validation *(independent)*

Dependency chain: `1 → 2`, `1 → 3`, `4` standalone.

This spec ships the **shared AWS foundation** (session/client factory,
credential config, base exception family) consumed by Specs 2–3, plus the S3
feature set: file metadata, row counts, partition-scheme discovery, and file
format validation for CSV / Parquet / JSON / ORC.

## Goals

- Read S3 object metadata (size, last modified, etag, storage class, content type).
- Count rows across CSV, JSON, Parquet, ORC.
- Discover Hive-style (`key=value`) partition schemes under a prefix.
- Validate file format (parse-check) with optional schema assertion.
- Establish a reusable AWS session/config/exception foundation for Specs 2–3.
- Ship with CI-safe unit tests (no live AWS) plus opt-in live integration tests.

## Non-Goals

- Glue, Athena, Airflow functionality (separate specs).
- Non-Hive / positional partition inference (Hive-style `key=value` only).
- Writing to S3 or mutating objects (read/validate only).
- New runtime dependencies (`boto3`, `pyarrow` already present).

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Row-count engine | S3 Select (CSV/JSON) + pyarrow footer (Parquet/ORC) | S3 Select does **not** support ORC and is deprecation-bound; Parquet/ORC row counts live in the file footer, so a footer read avoids a full scan and needs no new dep. |
| Partition discovery | Hive-style `key=value` from object keys | Covers the common data-lake layout without heuristic ambiguity. |
| Format validation | Parse + optional schema assert | Catches corrupt files and schema drift; schema assert is opt-in. |
| Test strategy | Local mocks (moto / botocore `Stubber`) + opt-in live | CI-safe by default; live path gated behind an env flag. |
| Footer reads | `pyarrow.fs.S3FileSystem` | Built into pyarrow (no `s3fs` dep), does range reads for footer-only, supports `endpoint_override` for moto/localstack. |

## Architecture

### `etl_framework/aws/` — shared AWS core

**`config.py`**
- `AWSConfig` (pydantic `BaseModel`, `ConfigDict(str_strip_whitespace=True)`),
  matching the existing config-model style:
  - `region: str = ""`
  - `profile: str = ""`
  - `access_key_id: str = ""`
  - `secret_access_key: str = ""`
  - `session_token: str = ""`
  - `endpoint_url: str = ""` (moto/localstack/live override)
  - `verify_ssl: bool = True`
- Validator: `endpoint_url`, if set, must include a URL scheme (mirrors
  existing `validate_base_url`).
- Add `secret_access_key` and `session_token` to `config/models.py`
  `SECRET_FIELDS` so response masking and encryption-at-rest cover them.

**`session.py`**
- `AWSSession`:
  - `__init__(cfg: AWSConfig, _session=None)` — build a `boto3.Session` from
    the config (profile OR explicit keys), or accept an injected session for
    tests (mirrors `DBEngine(env_config, _engine=None)`).
  - `.client(service: str)` — cached boto3 client factory; passes
    `endpoint_url` / `verify` when set.
  - `.resource(service: str)` — where a resource API reads cleaner.

### `etl_framework/aws_s3/` — S3 feature module

**`client.py` — `S3Client`** (thin wrapper over `AWSSession.client("s3")`):
- `list_objects(bucket, prefix)` — paginated (`list_objects_v2` paginator),
  yields object dicts.
- `head_object(bucket, key)` — object HEAD.
- `get_object(bucket, key)` — streamed body.
- `select_object_content(bucket, key, expression, input_serialization)` — S3
  Select passthrough.
- Wraps botocore `ClientError` → typed exceptions with bucket/key context.

**`metadata.py`**
- `read_object_metadata(client, bucket, key) -> ObjectMetadata` — from
  `head_object`: `size_bytes`, `last_modified`, `etag`, `storage_class`,
  `content_type`.

**`row_count.py` — `RowCounter`**
- `count(client, bucket, key, fmt) -> RowCountResult`.
- Strategy routing by `fmt`:
  - `csv`, `json` → S3 Select `SELECT COUNT(*) FROM s3object`, format-specific
    `InputSerialization`.
  - `parquet`, `orc` → `pyarrow.fs.S3FileSystem` footer metadata
    (`ParquetFile(...).metadata.num_rows` / `ORCFile(...).nrows`).
- `RowCountResult` records `engine` used ("s3_select" | "pyarrow_footer") so
  tests and reports can assert the path taken.

**`partitions.py`**
- `discover_partitions(client, bucket, prefix, fmt=None) -> PartitionScheme`:
  - List objects under prefix; parse Hive-style `key=value` segments from each
    key.
  - Produce ordered partition columns, distinct values per column, and per
    leaf-partition object count. When `fmt` is passed, also attach per-partition
    row counts via `RowCounter`.

**`formats.py`**
- `validate_format(client, bucket, key, fmt, expected_schema=None) -> FormatValidationResult`:
  - Parse-check: open the object as `fmt` (pyarrow for parquet/orc; pandas for
    csv, streamed json). Parse failure → `FileFormatValidationError`.
  - If `expected_schema` given: extract actual column names/types and compare
    via `expectations/schema_compat.py`; drift → `SchemaValidationError`
    (existing type, carries missing/extra columns).

**`models.py`** (pydantic result models):
- `ObjectMetadata`, `RowCountResult`, `PartitionEntry`, `PartitionScheme`,
  `FormatValidationResult`.

### Exceptions (append to `etl_framework/exceptions.py`)

```python
class AWSError(ETLFrameworkError): ...                    # base
class S3ObjectNotFoundError(AWSError): ...                # NoSuchKey / 404
class S3SelectError(AWSError): ...                        # S3 Select failure
class UnsupportedFormatError(AWSError): ...               # unknown fmt
class FileFormatValidationError(AWSError): ...            # parse failure
```

Schema drift reuses the existing `SchemaValidationError`.

## Data Flow

```
AWSConfig ──> AWSSession ──> S3Client ──┬─> read_object_metadata ──> ObjectMetadata
                                        ├─> RowCounter.count ──────> RowCountResult
                                        ├─> discover_partitions ──> PartitionScheme
                                        └─> validate_format ──────> FormatValidationResult
                                                                        │
pytest assertions <─────────────────────────────────────────────────────┘
```

## Error Handling

- All botocore `ClientError`s caught at `S3Client` and re-raised as typed
  `AWSError` subclasses carrying `bucket` / `key`.
- `NoSuchKey` / 404 → `S3ObjectNotFoundError`.
- S3 Select execution/stream errors → `S3SelectError`.
- Parquet/ORC never routed to S3 Select; unknown `fmt` → `UnsupportedFormatError`.
- Parse failures in `validate_format` → `FileFormatValidationError`; schema
  drift → `SchemaValidationError`.

## Testing

**Unit (`tests/unit/`, CI-safe, no creds):**
- `test_aws_session.py` — session/client construction, endpoint_url passthrough,
  secret masking.
- `test_s3_metadata.py` — moto `@mock_aws`, upload fixtures, assert metadata.
- `test_s3_row_count.py` — S3 Select via botocore `Stubber` (moto select
  support is thin); Parquet/ORC via `pyarrow.fs.S3FileSystem` with
  `endpoint_override` at moto; assert `engine` field per format.
- `test_s3_partitions.py` — moto with `dt=.../region=...` keys; assert columns,
  values, per-partition counts.
- `test_s3_formats.py` — valid + corrupt fixtures per format; schema-assert pass
  and drift (→ `SchemaValidationError`).

Fixtures: tiny CSV / Parquet / JSON / ORC generated under `tests/helpers/`.

**Integration (`tests/integration/test_s3_live.py`):**
- Gated by `ATOM_AWS_LIVE=1`; skipped otherwise. Bucket/region from env. Smoke
  path over all four functions against a real (small) object set.

## Dependencies

- Runtime: **none new** (`boto3`, `pyarrow` already in `requirements.txt` /
  `pyproject.toml`).
- Dev: add **`moto>=5.0`** to the `[dev]` extra and `requirements.txt` dev block.

## Open Questions

None. All design decisions resolved during brainstorming.
