"""Bulk compare import: sheet parsing, SQL generation, row validation."""
from __future__ import annotations

import io

import pandas as pd
import pytest

from api.services import compare_bulk_import as bulk

HEADER = "job_name,source_config,source_table,target_config,target_table"


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


# -- read_sheet ---------------------------------------------------------------

def test_read_sheet_csv_normalizes_headers_and_trims_cells():
    content = _csv(" Job_Name ,SOURCE_CONFIG,source_table,target_config,target_table",
                   " cust , src , dbo.customer , tgt , public.customer ")
    rows = bulk.read_sheet("map.csv", content)
    assert rows == [{
        "job_name": "cust", "source_config": "src", "source_table": "dbo.customer",
        "target_config": "tgt", "target_table": "public.customer",
    }]


def test_read_sheet_csv_with_utf8_bom():
    rows = bulk.read_sheet("map.csv", b"\xef\xbb\xbf" + _csv(HEADER, "a,s,t1,t,t2"))
    assert rows[0]["job_name"] == "a"


def test_read_sheet_skips_blank_lines():
    rows = bulk.read_sheet("map.csv", _csv(HEADER, "a,s,t1,t,t2", ",,,,", "b,s,t3,t,t4"))
    assert [r["job_name"] for r in rows] == ["a", "b"]


def test_read_sheet_xlsx_first_sheet():
    buf = io.BytesIO()
    pd.DataFrame([{
        "job_name": "x", "source_config": "s", "source_table": "a",
        "target_config": "t", "target_table": "b", "numeric_tolerance": 0.5,
    }]).to_excel(buf, index=False)
    rows = bulk.read_sheet("map.xlsx", buf.getvalue())
    assert rows[0]["job_name"] == "x"
    assert rows[0]["numeric_tolerance"] == "0.5"


def test_read_sheet_rejects_other_extensions():
    with pytest.raises(bulk.SheetError, match=r"\.csv or \.xlsx"):
        bulk.read_sheet("map.txt", b"x")


def test_read_sheet_rejects_unknown_headers():
    with pytest.raises(bulk.SheetError, match="Unknown column.*colour"):
        bulk.read_sheet("map.csv", _csv(HEADER + ",colour", "a,s,t1,t,t2,red"))


def test_read_sheet_rejects_missing_required_headers():
    with pytest.raises(bulk.SheetError, match="Missing required column.*target_table"):
        bulk.read_sheet("map.csv", _csv("job_name,source_config,source_table,target_config", "a,s,t1,t"))


def test_read_sheet_rejects_empty_sheet():
    with pytest.raises(bulk.SheetError, match="no data rows"):
        bulk.read_sheet("map.csv", _csv(HEADER))


def test_read_sheet_rejects_too_many_rows(monkeypatch):
    monkeypatch.setattr(bulk, "MAX_ROWS", 2)
    with pytest.raises(bulk.SheetError, match="at most 2"):
        bulk.read_sheet("map.csv", _csv(HEADER, "a,s,1,t,1", "b,s,2,t,2", "c,s,3,t,3"))


def test_read_sheet_unreadable_xlsx():
    with pytest.raises(bulk.SheetError, match="Could not read"):
        bulk.read_sheet("map.xlsx", b"not a zip")
