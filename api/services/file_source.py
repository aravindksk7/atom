from __future__ import annotations
import base64
import io
from pathlib import Path

import pandas as pd
from fastapi import HTTPException


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
            return pd.read_csv(io.BytesIO(raw))
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(io.BytesIO(raw))
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Use .csv or .xlsx",
        )

    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext == ".csv":
            return pd.read_csv(p)
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(p)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file format '{ext}'. Use .csv or .xlsx",
    )
