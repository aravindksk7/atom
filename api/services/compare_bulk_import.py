"""Compile a CSV/XLSX mapping sheet into ``compare_type: "sql"`` compare jobs.

One sheet row = one table-to-table compare. Each side's SELECT is generated
for that side's database dialect, with source columns aliased to the
target-side names so both frames carry identical column names.
"""
from __future__ import annotations

import io
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd
from pydantic import ValidationError

from api.schemas import AdvancedCompareOptions, JobDefinition, SQLCompareRequest
from etl_framework.db.sql_utils import quote_identifier, reject_mutating_sql

MAX_ROWS = 1000

REQUIRED_COLUMNS = (
    "job_name", "source_config", "source_table", "target_config", "target_table",
)
# Order used by the downloadable template; uploaded files may order headers freely.
ALL_COLUMNS = (
    "job_name", "source_config", "source_connection", "source_table",
    "target_config", "target_connection", "target_table",
    "key_columns", "compare_columns", "exclude_columns", "source_where", "target_where",
    "numeric_tolerance", "ignore_case", "chunk_size", "tags", "description",
)


class SheetError(ValueError):
    """The file as a whole cannot be used (as opposed to one bad row)."""


def read_sheet(filename: str, content: bytes) -> list[dict[str, str]]:
    """Parse a mapping sheet into row dicts keyed by normalized header names."""
    lower = (filename or "").lower()
    if not (lower.endswith(".csv") or lower.endswith(".xlsx")):
        raise SheetError("Upload a .csv or .xlsx file")
    try:
        # header=None keeps the raw header row, so duplicates are not mangled by pandas.
        if lower.endswith(".csv"):
            raw = pd.read_csv(
                io.BytesIO(content), dtype=str, keep_default_na=False, na_values=[],
                encoding="utf-8-sig", header=None,
            )
        else:
            raw = pd.read_excel(
                io.BytesIO(content), sheet_name=0, dtype=str, header=None,
                keep_default_na=False, na_values=[],
            )
    except Exception as exc:
        raise SheetError(f"Could not read {filename}: {exc}") from exc

    raw = raw.fillna("")
    if raw.empty:
        raise SheetError("The sheet has no data rows")
    raw_headers = [str(c).strip() for c in raw.iloc[0]]
    if any(not h for h in raw_headers):
        raise SheetError("The sheet has a blank column header")
    headers = [h.lower() for h in raw_headers]
    duplicates = sorted({h for h in headers if headers.count(h) > 1})
    if duplicates:
        raise SheetError(f"Duplicate column(s): {', '.join(duplicates)}")
    unknown = [h for h in headers if h not in ALL_COLUMNS]
    if unknown:
        raise SheetError(f"Unknown column(s): {', '.join(unknown)}")
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise SheetError(f"Missing required column(s): {', '.join(missing)}")
    df = raw.iloc[1:].copy()
    df.columns = headers

    rows = [
        {key: str(value).strip() for key, value in record.items()}
        for record in df.to_dict(orient="records")
    ]
    rows = [row for row in rows if any(row.values())]
    if not rows:
        raise SheetError("The sheet has no data rows")
    if len(rows) > MAX_ROWS:
        raise SheetError(f"A sheet can hold at most {MAX_ROWS} rows; this one has {len(rows)}")
    return rows


DIALECT_BY_DB_TYPE = {
    "mssql": "sqlserver",
    "oracle": "oracle",
    "netezza": "netezza",
    "postgresql": "postgresql",
    "aurora_postgresql": "postgresql",
}

_PLAIN_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")
_QUERY_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ColumnPair:
    source: str
    target: str


def parse_column_list(cell: str, *, field: str) -> list[ColumnPair]:
    """Parse ``"SRC:tgt;name"`` into pairs; a bare name maps to itself."""
    pairs: list[ColumnPair] = []
    for raw in (cell or "").split(";"):
        item = raw.strip()
        if not item:
            continue
        if item.count(":") > 1:
            raise ValueError(f"{field}: '{item}' has more than one ':'")
        if ":" in item:
            source, target = (part.strip() for part in item.split(":"))
        else:
            source = target = item
        if not source or not target:
            raise ValueError(f"{field}: '{item}' must be 'source:target' or a column name")
        pairs.append(ColumnPair(source, target))
    return pairs


def dialect_for(db_type: str) -> str:
    return DIALECT_BY_DB_TYPE[db_type]


def is_query(cell: str) -> bool:
    return bool(_QUERY_START.match(cell or ""))


def _ref(name: str, dialect: str) -> str:
    """Leave plain identifiers unquoted so the database's own case folding applies."""
    name = name.strip()
    return name if _PLAIN_IDENTIFIER.fullmatch(name) else quote_identifier(name, dialect)


def build_select(table: str, columns: list[ColumnPair], where: str, dialect: str) -> str:
    """SELECT one side's columns, aliased to the target names, from ``table``.

    Aliases are always quoted so they keep the exact spelling from the sheet
    (Postgres would lower-case and Oracle upper-case an unquoted alias).
    """
    if columns:
        select_list = ", ".join(
            f"{_ref(c.source, dialect)} AS {quote_identifier(c.target, dialect)}" for c in columns
        )
    else:
        select_list = "*"
    table_ref = ".".join(_ref(part, dialect) for part in table.split("."))
    sql = f"SELECT {select_list} FROM {table_ref}"
    if where:
        sql = f"{sql} WHERE {where}"
    return reject_mutating_sql(sql)


DEFAULT_CHUNK_SIZE = 10_000
_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0", ""}


class RowError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class _Side:
    config: Any
    connection: str | None
    dialect: str


def _resolve_side(row: Mapping[str, str], prefix: str, configs: Mapping[str, Any], errors: list[str]) -> _Side | None:
    config_name = row.get(f"{prefix}_config", "")
    connection = row.get(f"{prefix}_connection", "") or None
    if not config_name:
        errors.append(f"{prefix}_config is required")
        return None
    cfg = configs.get(config_name)
    if cfg is None:
        errors.append(f"{prefix}_config '{config_name}' does not exist")
        return None
    config_json = cfg.config_json or {}
    entry: Mapping[str, Any] = {}
    if connection:
        entry = (config_json.get("connections") or {}).get(connection)
        if entry is None:
            errors.append(f"{prefix}_connection '{connection}' does not exist in config '{config_name}'")
            return None
        if not isinstance(entry, Mapping):
            errors.append(f"{prefix}_connection '{connection}' is not a valid connection")
            return None
    db_type = entry.get("db_type") or config_json.get("db_type") or "mssql"
    if db_type not in DIALECT_BY_DB_TYPE:
        errors.append(f"{prefix}_config '{config_name}' ({db_type}) is not a SQL database")
        return None
    return _Side(cfg, connection, dialect_for(db_type))


def _parse_bool(cell: str, field: str, errors: list[str]) -> bool:
    value = (cell or "").strip().lower()
    if value in _TRUE:
        return True
    if value not in _FALSE:
        errors.append(f"{field} must be true or false, got '{cell}'")
    return False


def _finite_float(cell: str) -> float:
    value = float(cell)
    if not math.isfinite(value):
        raise ValueError("not finite")
    return value


def _whole_number(cell: str) -> int:
    """Accept '500' and CSV-style '10000.0'; reject '1.5'."""
    value = float(cell)
    if not math.isfinite(value) or value != int(value):
        raise ValueError("not a whole number")
    return int(value)


def _parse_number(cell: str, field: str, cast, default, errors: list[str]):
    if not (cell or "").strip():
        return default
    try:
        value = cast(cell)
    except ValueError:
        errors.append(f"{field} must be a number, got '{cell}'")
        return default
    if value < 0:
        errors.append(f"{field} must be >= 0, got '{cell}'")
        return default
    return value


def _side_label(cfg: Any, table: str) -> str:
    return cfg.name if is_query(table) else f"{cfg.name} {table}"


def build_job(row: Mapping[str, str], configs: Mapping[str, Any]) -> tuple[JobDefinition, str, str]:
    """Compile one sheet row; raises RowError listing every problem found."""
    errors: list[str] = []
    for field in ("job_name", "source_table", "target_table"):
        if not row.get(field):
            errors.append(f"{field} is required")
    source = _resolve_side(row, "source", configs, errors)
    target = _resolve_side(row, "target", configs, errors)

    keys: list[ColumnPair] = []
    compares: list[ColumnPair] = []
    for field, sink in (("key_columns", keys), ("compare_columns", compares)):
        try:
            sink.extend(parse_column_list(row.get(field, ""), field=field))
        except ValueError as exc:
            errors.append(str(exc))
    excludes = [c.strip() for c in row.get("exclude_columns", "").split(";") if c.strip()]
    tolerance = _parse_number(row.get("numeric_tolerance", ""), "numeric_tolerance", _finite_float, 0.0, errors)
    chunk_size = _parse_number(row.get("chunk_size", ""), "chunk_size", _whole_number, DEFAULT_CHUNK_SIZE, errors)
    ignore_case = _parse_bool(row.get("ignore_case", ""), "ignore_case", errors)

    source_table, target_table = row.get("source_table", ""), row.get("target_table", "")
    renamed_key = any(k.source != k.target for k in keys)
    if not compares and renamed_key:
        errors.append(
            "a renamed key needs an explicit compare_columns list (SELECT * would duplicate the key)"
        )
    mapped = [k.target for k in keys] + [c.target for c in compares if c.target not in {k.target for k in keys}]
    for name in sorted({t for t in mapped if mapped.count(t) > 1}):
        errors.append(f"target column '{name}' is mapped more than once")
    if ignore_case and not compares:
        errors.append("ignore_case needs compare_columns (the case-insensitive column list)")
    for prefix, table in (("source", source_table), ("target", target_table)):
        if is_query(table):
            if compares or renamed_key:
                errors.append(
                    f"{prefix}_table is a query; column mapping only applies to table references - "
                    "alias the columns inside the query instead"
                )
            if row.get(f"{prefix}_where"):
                errors.append(f"{prefix}_where only applies to a table reference; put the filter in the query")

    if errors or source is None or target is None:
        raise RowError(errors)

    key_targets = {k.target for k in keys}
    columns = keys + [c for c in compares if c.target not in key_targets] if compares else []
    queries: list[str] = []
    for prefix, side, table, side_columns in (
        ("source", source, source_table, columns),
        ("target", target, target_table, [ColumnPair(c.target, c.target) for c in columns]),
    ):
        try:
            if is_query(table):
                queries.append(reject_mutating_sql(table))
            else:
                queries.append(build_select(table, side_columns, row.get(f"{prefix}_where", ""), side.dialect))
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
    if errors:
        raise RowError(errors)
    query_a, query_b = queries

    try:
        request = SQLCompareRequest(
            config_id_a=source.config.id,
            config_id_b=target.config.id,
            query_a=query_a,
            query_b=query_b,
            label_a=_side_label(source.config, source_table),
            label_b=_side_label(target.config, target_table),
            connection_a=source.connection,
            connection_b=target.connection,
            key_columns=[k.target for k in keys],
            exclude_columns=excludes,
            chunk_size=chunk_size,
            advanced=AdvancedCompareOptions(
                float_tolerance=tolerance if tolerance > 0 else 1e-9,
                case_insensitive_columns=[c.target for c in columns] if ignore_case else [],
            ),
        )
        job = JobDefinition(
            name=row["job_name"],
            description=row.get("description", ""),
            tags=[t.strip() for t in row.get("tags", "").split(";") if t.strip()],
            job_type="compare",
            params={"compare_type": "sql", "request": request.model_dump()},
        )
    except ValidationError as exc:
        raise RowError([
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" if err["loc"] else err["msg"]
            for err in exc.errors()
        ]) from exc
    return job, query_a, query_b


def preview_rows(
    rows: list[dict[str, str]],
    configs: Mapping[str, Any],
    existing_job_names: set[str],
) -> dict[str, Any]:
    """Validate every row; nothing is saved. Row numbers match the sheet (header = 1)."""
    name_counts = Counter(row.get("job_name", "") for row in rows)
    results: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        name = row.get("job_name", "")
        errors: list[str] = []
        if name and name_counts[name] > 1:
            errors.append(f"job_name '{name}' appears {name_counts[name]} times in the file")
        job = query_a = query_b = None
        try:
            job, query_a, query_b = build_job(row, configs)
        except RowError as exc:
            errors.extend(exc.errors)
        valid = not errors
        results.append({
            "row_number": row_number,
            "job_name": name,
            "source": f"{row.get('source_config', '')} {row.get('source_table', '')}".strip(),
            "target": f"{row.get('target_config', '')} {row.get('target_table', '')}".strip(),
            "status": "valid" if valid else "invalid",
            "will_update": valid and name in existing_job_names,
            "errors": errors,
            "job": job.model_dump() if valid else None,
            "query_a": query_a,
            "query_b": query_b,
        })
    valid_count = sum(1 for r in results if r["status"] == "valid")
    return {"rows": results, "valid_count": valid_count, "invalid_count": len(results) - valid_count}


TEMPLATE_CSV = (
    ",".join(ALL_COLUMNS) + "\n"
    + "customer_cmp,PROD_MSSQL,,dbo.customer,PROD_AURORA,,public.customer,"
      "CUST_ID:customer_id,NAME:full_name;EMAIL:email,,active = 1,,0,false,10000,nightly,Customer master\n"
    + "orders_cmp,PROD_ORACLE,,SALES.ORDERS,PROD_PG,,sales.orders,"
      "ORDER_ID:order_id;LINE_NO:line_no,AMOUNT:amount,load_ts,,,0.01,false,10000,nightly,Order lines\n"
)
