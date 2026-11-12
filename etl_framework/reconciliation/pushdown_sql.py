"""Dialect SQL for the pushdown (bucketed hash) reconciliation strategy.

Each side of a reconciliation runs the same aggregate in its own database:
rows are assigned to a bucket from a hash of their key columns, and each
bucket reports ``COUNT(*)`` plus two sums of 32-bit slices of an MD5 over a
canonical text rendering of the row. Buckets whose triples agree on both sides
are taken as equal; only the rows of buckets that differ are fetched.

Correctness never rests on the canonical text matching across databases. A
value rendered differently on each side only makes its bucket look different,
and a key hashed into different buckets on each side makes *both* buckets look
different, so the rows are fetched and the exact comparison sees them. What
the canonical form must avoid is the opposite: two different values rendering
to the same text, which would hide a real difference. Numbers are therefore
rendered at the precision of the float tolerance and timestamps to the
microsecond.
"""
from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import math
import uuid as _uuid
from dataclasses import dataclass

from etl_framework.db.sql_utils import reject_mutating_sql

SUPPORTED_DIALECTS = frozenset({"oracle", "postgresql", "mssql"})

# Column kinds the canonical text knows how to render on every dialect.
NUMBER = "number"
BOOL = "bool"
DATETIME = "datetime"
DATETIME_TZ = "datetime_tz"
STRING = "string"
UUID = "uuid"
# Large objects are rendered as the MD5 of their content, computed in the
# database: BLOB / RAW / bytea / varbinary, and CLOB / NCLOB.
BINARY = "binary"
TEXT_LOB = "text_lob"
# A plain string column paired with a CLOB on the other side: rendered as the
# MD5 of its text so both sides produce the same canonical value.
STRING_HASHED = "string_hashed"
UNSUPPORTED = "unsupported"

# Kinds whose fetched value is the content hash rather than the content.
HASHED_KINDS = frozenset({BINARY, TEXT_LOB, STRING_HASHED})

QUERY_ALIAS = "atom_q"
BUCKET_ALIAS = "atom_b"
HASH_ALIAS = "atom_h"

# Oracle rejects more than 1000 literals in one IN list.
IN_LIST_CHUNK = 1000


@dataclass(frozen=True)
class ColumnInfo:
    """One result column: the name SQLAlchemy reports, the name the database
    reports (used for quoting), and the kind used to render it."""

    name: str
    db_name: str
    kind: str


def engine_dialect(engine) -> str | None:
    """SQLAlchemy dialect name of a DBEngine-like object, else None."""
    sa_engine = getattr(engine, "_engine", None)
    name = getattr(getattr(sa_engine, "dialect", None), "name", None)
    return name if isinstance(name, str) else None


def numeric_scale_for_tolerance(tolerance: float) -> int:
    """Decimal places a number keeps in the canonical text.

    Fine enough that any difference above the float tolerance changes the
    text; capped at 9 so ``DECIMAL(38, 9)`` on SQL Server still holds 29
    integer digits.
    """
    if tolerance <= 0:
        return 9
    return max(0, min(9, math.ceil(-math.log10(tolerance))))


# ---------------------------------------------------------------------------
# Column kinds from cursor metadata
# ---------------------------------------------------------------------------

_PG_OID_KINDS = {
    16: BOOL,
    17: BINARY,
    20: NUMBER, 21: NUMBER, 23: NUMBER, 26: NUMBER, 700: NUMBER, 701: NUMBER, 1700: NUMBER,
    18: STRING, 19: STRING, 25: STRING, 1042: STRING, 1043: STRING, 114: STRING, 3802: STRING,
    1082: DATETIME, 1114: DATETIME,
    1184: DATETIME_TZ,
    2950: UUID,
}

_ORACLE_TYPE_KINDS = {
    "DB_TYPE_NUMBER": NUMBER,
    "DB_TYPE_BINARY_INTEGER": NUMBER,
    "DB_TYPE_BINARY_FLOAT": NUMBER,
    "DB_TYPE_BINARY_DOUBLE": NUMBER,
    "DB_TYPE_BOOLEAN": BOOL,
    "DB_TYPE_VARCHAR": STRING,
    "DB_TYPE_NVARCHAR": STRING,
    "DB_TYPE_CHAR": STRING,
    "DB_TYPE_NCHAR": STRING,
    # SQLAlchemy fetches LOBs as LONG / LONG RAW, so the cursor reports BLOB
    # and CLOB columns under those names. A genuine legacy LONG column makes
    # DBMS_CRYPTO.HASH fail, and the reconciler streams instead.
    "DB_TYPE_LONG": TEXT_LOB,
    "DB_TYPE_LONG_RAW": BINARY,
    "DB_TYPE_BLOB": BINARY,
    "DB_TYPE_RAW": BINARY,
    "DB_TYPE_CLOB": TEXT_LOB,
    "DB_TYPE_NCLOB": TEXT_LOB,
    "DB_TYPE_DATE": DATETIME,
    "DB_TYPE_TIMESTAMP": DATETIME,
    "DB_TYPE_TIMESTAMP_TZ": DATETIME_TZ,
    "DB_TYPE_TIMESTAMP_LTZ": DATETIME_TZ,
}

_PY_TYPE_KINDS = {
    bool: BOOL,
    bytes: BINARY,
    bytearray: BINARY,
    int: NUMBER,
    float: NUMBER,
    _decimal.Decimal: NUMBER,
    str: STRING,
    _dt.datetime: DATETIME,
    _dt.date: DATETIME,
    _uuid.UUID: UUID,
}


def align_kinds(source: ColumnInfo, target: ColumnInfo) -> tuple[ColumnInfo, ColumnInfo]:
    """Make a column render the same way on both sides.

    A CLOB is hashed in the database; a plain string column facing a CLOB
    is hashed too, so the two canonical values can match.
    """
    kinds = {source.kind, target.kind}
    if TEXT_LOB in kinds and kinds <= {TEXT_LOB, STRING}:
        aligned = [
            c if c.kind == TEXT_LOB else ColumnInfo(c.name, c.db_name, STRING_HASHED)
            for c in (source, target)
        ]
        return aligned[0], aligned[1]
    return source, target


def column_kind(dialect: str, type_code: object) -> str:
    """Map a DB-API ``cursor.description`` type code to a column kind."""
    if dialect == "postgresql":
        try:
            return _PG_OID_KINDS.get(int(type_code), UNSUPPORTED)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return UNSUPPORTED
    if dialect == "oracle":
        name = getattr(type_code, "name", None) or str(type_code)
        return _ORACLE_TYPE_KINDS.get(name, UNSUPPORTED)
    if dialect == "mssql":
        if isinstance(type_code, type):
            for py_type, kind in _PY_TYPE_KINDS.items():
                if issubclass(type_code, py_type):
                    return kind
        return UNSUPPORTED
    return UNSUPPORTED


# ---------------------------------------------------------------------------
# Identifiers and query wrapping
# ---------------------------------------------------------------------------

def quote(dialect: str, name: str) -> str:
    """Quote a column name exactly as the database reported it."""
    if dialect == "mssql":
        return "[" + name.replace("]", "]]") + "]"
    return '"' + name.replace('"', '""') + '"'


def wrap(dialect: str, query: str) -> str:
    """``(query) atom_q``; Oracle does not accept ``AS`` before a table alias."""
    body = reject_mutating_sql(query).strip().rstrip(";")
    return f"({body}) {QUERY_ALIAS}"


def sample_query(dialect: str, query: str, rows: int = 0) -> str:
    """A query returning at most ``rows`` rows of ``query`` (0 = only the shape)."""
    src = wrap(dialect, query)
    if rows <= 0:
        return f"SELECT * FROM {src} WHERE 1 = 0"
    if dialect == "oracle":
        return f"SELECT * FROM {src} FETCH FIRST {int(rows)} ROWS ONLY"
    if dialect == "mssql":
        return f"SELECT TOP {int(rows)} * FROM {src}"
    return f"SELECT * FROM {src} LIMIT {int(rows)}"


# ---------------------------------------------------------------------------
# Canonical text and hashing
# ---------------------------------------------------------------------------

def _number_text(dialect: str, col: str, scale: int) -> str:
    factor = "1" + "0" * scale
    if dialect == "oracle":
        return f"TO_CHAR(ROUND(CAST({col} AS NUMBER) * {factor}))"
    if dialect == "postgresql":
        return f"CAST(ROUND(CAST({col} AS NUMERIC) * {factor}) AS TEXT)"
    return (
        f"CAST(CAST(ROUND(CAST({col} AS DECIMAL(38, {scale})) * {factor}, 0) "
        f"AS DECIMAL(38, 0)) AS VARCHAR(40))"
    )


def _datetime_text(dialect: str, col: str, with_tz: bool) -> str:
    if dialect == "oracle":
        value = f"SYS_EXTRACT_UTC({col})" if with_tz else f"CAST({col} AS TIMESTAMP)"
        return f"TO_CHAR({value}, 'YYYY-MM-DD HH24:MI:SS.FF6')"
    if dialect == "postgresql":
        value = f"({col} AT TIME ZONE 'UTC')" if with_tz else col
        return f"TO_CHAR({value}, 'YYYY-MM-DD HH24:MI:SS.US')"
    value = f"SWITCHOFFSET({col}, '+00:00')" if with_tz else col
    return f"CONVERT(VARCHAR(26), CAST({value} AS DATETIME2(6)), 121)"


def canonical_text(dialect: str, column: ColumnInfo, scale: int) -> str:
    """SQL text for one non-NULL value of ``column`` (no NULL handling)."""
    col = f"{QUERY_ALIAS}.{quote(dialect, column.db_name)}"
    kind = column.kind
    if kind == NUMBER:
        return _number_text(dialect, col, scale)
    if kind == BOOL:
        # Rendered as the number 1/0 so it matches a NUMBER(1) flag on Oracle.
        if dialect == "mssql":
            as_number = f"(CASE WHEN {col} = 1 THEN 1 ELSE 0 END)"
        else:
            as_number = f"(CASE WHEN {col} THEN 1 ELSE 0 END)"
        return _number_text(dialect, as_number, scale)
    if kind in (DATETIME, DATETIME_TZ):
        return _datetime_text(dialect, col, kind == DATETIME_TZ)
    if kind == UUID:
        if dialect == "postgresql":
            return f"UPPER(CAST({col} AS TEXT))"
        return f"UPPER(CAST({col} AS VARCHAR(36)))"
    if kind in HASHED_KINDS:
        return content_hash(dialect, column)
    if kind == STRING:
        if dialect == "postgresql":
            return f"CAST({col} AS TEXT)"
        if dialect == "mssql":
            return f"CAST({col} AS NVARCHAR(MAX))"
        return col
    raise ValueError(f"column {column.name!r} has a type pushdown cannot render")


def content_hash(dialect: str, column: ColumnInfo) -> str:
    """Upper-case hex MD5 of a large object's content, computed in the database.

    Oracle needs EXECUTE on DBMS_CRYPTO; without it the summary query fails
    and the reconciler streams instead. DBMS_CRYPTO hashes CLOBs as AL32UTF8,
    matching PostgreSQL's UTF-8 text.
    """
    col = f"{QUERY_ALIAS}.{quote(dialect, column.db_name)}"
    kind = column.kind
    if dialect == "oracle":
        if kind == STRING_HASHED:
            return f"RAWTOHEX(STANDARD_HASH({col}, 'MD5'))"
        return f"RAWTOHEX(DBMS_CRYPTO.HASH({col}, 2))"
    if dialect == "postgresql":
        if kind == BINARY:
            return f"UPPER(MD5({col}))"
        return f"UPPER(MD5(CAST({col} AS TEXT)))"
    if kind == BINARY:
        return f"CONVERT(VARCHAR(32), HASHBYTES('MD5', {col}), 2)"
    return (
        f"CONVERT(VARCHAR(32), HASHBYTES('MD5', CAST(CAST({col} AS NVARCHAR(MAX)) "
        f"COLLATE Latin1_General_100_BIN2_UTF8 AS VARCHAR(MAX))), 2)"
    )


def _concat(dialect: str, parts: list[str]) -> str:
    joiner = " + " if dialect == "mssql" else " || "
    return joiner.join(parts)


def row_text(dialect: str, columns: list[ColumnInfo], scale: int) -> str:
    """Canonical text of a whole row: ``n`` for NULL, ``v<text>`` otherwise.

    The ``v`` prefix keeps an empty string distinct from NULL on PostgreSQL and
    SQL Server, and is harmless on Oracle, where both are NULL anyway.
    """
    parts: list[str] = []
    for idx, column in enumerate(columns):
        col = f"{QUERY_ALIAS}.{quote(dialect, column.db_name)}"
        value = canonical_text(dialect, column, scale)
        if idx:
            parts.append("'|'")
        present = _concat(dialect, ["'v'", value])
        parts.append(f"(CASE WHEN {col} IS NULL THEN 'n' ELSE {present} END)")
    return _concat(dialect, parts) if parts else "'x'"


def md5_hex(dialect: str, text_sql: str) -> str:
    """32 hex digits of MD5(text). Case differs by dialect; callers only parse it."""
    if dialect == "oracle":
        return f"RAWTOHEX(STANDARD_HASH({text_sql}, 'MD5'))"
    if dialect == "postgresql":
        return f"MD5({text_sql})"
    # Hash the UTF-8 bytes, as Oracle (AL32UTF8) and PostgreSQL do. Needs SQL
    # Server 2019+; older servers reject the collation and the reconciler
    # falls back to streaming.
    return (
        f"CONVERT(VARCHAR(32), HASHBYTES('MD5', "
        f"CAST(({text_sql}) COLLATE Latin1_General_100_BIN2_UTF8 AS VARCHAR(MAX))), 2)"
    )


def hex_slice_to_int(dialect: str, hex_sql: str, start: int) -> str:
    """Eight hex digits from 1-based ``start`` as an unsigned 32-bit integer."""
    if dialect == "oracle":
        return f"TO_NUMBER(SUBSTR({hex_sql}, {start}, 8), 'XXXXXXXX')"
    if dialect == "postgresql":
        return f"CAST(CAST('x' || SUBSTR({hex_sql}, {start}, 8) AS BIT(32)) AS BIGINT)"
    return f"CAST(CONVERT(VARBINARY(4), SUBSTRING({hex_sql}, {start}, 8), 2) AS BIGINT)"


def bucket_expr(dialect: str, key_columns: list[ColumnInfo], buckets: int, scale: int) -> str:
    """Bucket number 0..buckets-1 from a hash of the key columns."""
    key_hash = hex_slice_to_int(dialect, md5_hex(dialect, row_text(dialect, key_columns, scale)), 1)
    if dialect == "mssql":
        return f"({key_hash} % {int(buckets)})"
    return f"MOD({key_hash}, {int(buckets)})"


def summary_query(
    dialect: str,
    query: str,
    key_columns: list[ColumnInfo],
    value_columns: list[ColumnInfo],
    buckets: int,
    scale: int,
) -> str:
    """``bucket, row count, hash sum 1, hash sum 2`` per bucket."""
    bucket = bucket_expr(dialect, key_columns, buckets, scale)
    row_hash = md5_hex(dialect, row_text(dialect, key_columns + value_columns, scale))
    inner = (
        f"SELECT {bucket} AS {BUCKET_ALIAS}, {row_hash} AS {HASH_ALIAS} "
        f"FROM {wrap(dialect, query)}"
    )
    s1 = hex_slice_to_int(dialect, HASH_ALIAS, 1)
    s2 = hex_slice_to_int(dialect, HASH_ALIAS, 9)
    return (
        f"SELECT {BUCKET_ALIAS} AS bucket, COUNT(*) AS row_count, "
        f"SUM({s1}) AS hash_a, SUM({s2}) AS hash_b "
        f"FROM ({inner}) atom_s GROUP BY {BUCKET_ALIAS}"
    )


def fetch_query(
    dialect: str,
    query: str,
    columns: list[ColumnInfo],
    key_columns: list[ColumnInfo],
    buckets: int,
    scale: int,
    only_buckets: list[int] | None,
    hashed: frozenset[str] | set[str] = frozenset(),
) -> str:
    """The selected columns plus their bucket, optionally only some buckets.

    Columns named in ``hashed`` come back as their content hash (same column
    name), so large objects never leave the database.
    """
    bucket = bucket_expr(dialect, key_columns, buckets, scale)

    def select(c: ColumnInfo) -> str:
        raw = f"{QUERY_ALIAS}.{quote(dialect, c.db_name)}"
        if c.name not in hashed:
            return raw
        digest = content_hash(dialect, c)
        return f"CASE WHEN {raw} IS NULL THEN NULL ELSE {digest} END AS {quote(dialect, c.db_name)}"

    select_cols = ", ".join(select(c) for c in columns)
    inner = f"SELECT {select_cols}, {bucket} AS {BUCKET_ALIAS} FROM {wrap(dialect, query)}"
    if not only_buckets:
        return inner
    ids = sorted({int(b) for b in only_buckets})
    clauses = [
        f"{BUCKET_ALIAS} IN ({', '.join(str(b) for b in ids[i:i + IN_LIST_CHUNK])})"
        for i in range(0, len(ids), IN_LIST_CHUNK)
    ]
    return f"SELECT * FROM ({inner}) atom_f WHERE {' OR '.join(clauses)}"
