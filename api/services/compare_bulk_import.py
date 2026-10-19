"""Compile a CSV/XLSX mapping sheet into ``compare_type: "sql"`` compare jobs.

One sheet row = one table-to-table compare. Each side's SELECT is generated
for that side's database dialect, with source columns aliased to the
target-side names so both frames carry identical column names.
"""
from __future__ import annotations

import io

import pandas as pd

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
        if lower.endswith(".csv"):
            df = pd.read_csv(
                io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig",
            )
        else:
            df = pd.read_excel(io.BytesIO(content), sheet_name=0, dtype=str)
    except Exception as exc:
        raise SheetError(f"Could not read {filename}: {exc}") from exc

    headers = [str(c).strip().lower() for c in df.columns]
    unknown = [h for h in headers if h not in ALL_COLUMNS]
    if unknown:
        raise SheetError(f"Unknown column(s): {', '.join(unknown)}")
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise SheetError(f"Missing required column(s): {', '.join(missing)}")
    df.columns = headers
    df = df.fillna("")

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
