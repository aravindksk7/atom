"""Test-only openpyxl oracle helper for reading BO XLSX exports."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

from etl_framework.bo_layout.assemble import normalise_header


def _is_empty_cell(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def read_xlsx_table(path: Path, header: list[str], sheet: int | str = 0) -> pd.DataFrame:
    """Read a table from an XLSX sheet by matching its normalised header row.

    Merged cells take their top-left anchor cell's value. Body rows are read
    from the row immediately below the matched header row over the exact
    matched column indices until a row where all of those columns are empty.
    Native openpyxl cell values (float, int, datetime, str) are preserved.
    """
    if not header:
        raise ValueError("header must not be empty")

    wb = openpyxl.load_workbook(path, data_only=True)
    try:
        ws = wb.worksheets[sheet] if isinstance(sheet, int) else wb[sheet]

        merged_values: dict[tuple[int, int], Any] = {}
        for rng in ws.merged_cells.ranges:
            anchor_val = ws.cell(rng.min_row, rng.min_col).value
            for r_idx in range(rng.min_row, rng.max_row + 1):
                for c_idx in range(rng.min_col, rng.max_col + 1):
                    merged_values[(r_idx, c_idx)] = anchor_val

        max_row = ws.max_row or 0
        max_col = ws.max_column or 0
        rows: list[list[Any]] = []
        for r_idx, row_cells in enumerate(
            ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col),
            start=1,
        ):
            rows.append([
                merged_values.get((r_idx, c_idx), cell.value)
                for c_idx, cell in enumerate(row_cells, start=1)
            ])
    finally:
        wb.close()

    width = len(header)
    header_row_idx: int | None = None
    start_col: int | None = None

    for r_idx, row_vals in enumerate(rows):
        norm_row = [normalise_header(str(v) if v is not None else None) for v in row_vals]
        for col_idx in range(len(norm_row) - width + 1):
            if norm_row[col_idx : col_idx + width] == header:
                header_row_idx = r_idx
                start_col = col_idx
                break
        if header_row_idx is not None:
            break

    if header_row_idx is None or start_col is None:
        raise ValueError(f"Header {header!r} not found in {path}")

    body_rows: list[list[Any]] = []
    for row_vals in rows[header_row_idx + 1 :]:
        sliced = row_vals[start_col : start_col + width]
        if all(_is_empty_cell(v) for v in sliced):
            break
        body_rows.append(sliced)

    return pd.DataFrame(body_rows, columns=header)
