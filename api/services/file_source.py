from __future__ import annotations
import base64
import csv
import io
import os
from pathlib import Path

import pandas as pd
from pandas.errors import ParserError
from fastapi import HTTPException

# Filesystem-path uploads are only permitted when UPLOAD_BASE_DIR is set.
# All paths are resolved and checked to be inside this directory before opening.
_UPLOAD_BASE: Path | None = (
    Path(os.environ["UPLOAD_BASE_DIR"]).resolve()
    if "UPLOAD_BASE_DIR" in os.environ
    else None
)


def _read_csv_bytes(raw: bytes) -> pd.DataFrame:
    try:
        return pd.read_csv(io.BytesIO(raw))
    except ParserError:
        text = raw.decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        start, delimiter = _find_csv_header(lines)
        if start is None:
            raise
        return pd.read_csv(io.StringIO("\n".join(lines[start:])), sep=delimiter)


def _find_csv_header(lines: list[str]) -> tuple[int | None, str]:
    for delimiter in (",", "\t", ";", "|"):
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or set(stripped) <= {"-"} or delimiter not in line:
                continue
            expected_fields = len(next(csv.reader([line], delimiter=delimiter)))
            if expected_fields < 2:
                continue
            next_line = _next_nonempty(lines, idx + 1)
            if next_line is None:
                continue
            next_fields = len(next(csv.reader([next_line], delimiter=delimiter)))
            if next_fields == expected_fields:
                return idx, delimiter
    return None, ","


def _next_nonempty(lines: list[str], start: int) -> str | None:
    for line in lines[start:]:
        stripped = line.strip()
        if stripped and not set(stripped) <= {"-"}:
            return line
    return None


def read_tabular(
    path: str | None = None,
    content_b64: str | None = None,
    file_name: str | None = None,
) -> pd.DataFrame:
    """Read CSV or XLSX into a DataFrame from a filesystem path or base64-encoded bytes."""
    if path is None and content_b64 is None:
        raise HTTPException(status_code=400, detail="Provide path or content_b64")

    if content_b64 is not None:
        raw = base64.b64decode(content_b64)
        name = file_name or ""
        ext = Path(name).suffix.lower()
        if ext == ".csv":
            return _read_csv_bytes(raw)
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(io.BytesIO(raw))
        if ext == ".json":
            try:
                return pd.read_json(io.BytesIO(raw))
            except ValueError:
                return pd.read_json(io.BytesIO(raw), orient="records")
        if ext in (".tsv", ".txt"):
            return pd.read_csv(io.BytesIO(raw), sep="\t")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Use .csv, .xlsx, .json, or .tsv",
        )

    if _UPLOAD_BASE is None:
        raise HTTPException(
            status_code=400,
            detail="Server-side file paths are disabled. Use content_b64 upload instead.",
        )

    resolved = (_UPLOAD_BASE / path).resolve()
    if not str(resolved).startswith(str(_UPLOAD_BASE)):
        raise HTTPException(status_code=400, detail="Invalid file path.")

    p = resolved
    ext = p.suffix.lower()
    try:
        if ext == ".csv":
            return _read_csv_bytes(p.read_bytes())
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(p)
        if ext == ".json":
            try:
                return pd.read_json(p)
            except ValueError:
                return pd.read_json(p, orient="records")
        if ext in (".tsv", ".txt"):
            return pd.read_csv(p, sep="\t")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file format '{ext}'. Use .csv, .xlsx, .json, or .tsv",
    )
