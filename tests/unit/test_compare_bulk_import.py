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


# -- column lists ---------------------------------------------------------------

def test_parse_column_list_pairs_and_bare_names():
    assert bulk.parse_column_list("CUST_ID:customer_id; email ;", field="key_columns") == [
        bulk.ColumnPair("CUST_ID", "customer_id"), bulk.ColumnPair("email", "email"),
    ]


def test_parse_column_list_blank_is_empty():
    assert bulk.parse_column_list("", field="compare_columns") == []


@pytest.mark.parametrize("cell", ["a:b:c", ":x", "x:"])
def test_parse_column_list_rejects_malformed_entries(cell):
    with pytest.raises(ValueError, match="compare_columns"):
        bulk.parse_column_list(cell, field="compare_columns")


# -- dialect + SQL ----------------------------------------------------------------

@pytest.mark.parametrize("db_type,dialect", [
    ("mssql", "sqlserver"), ("oracle", "oracle"), ("netezza", "netezza"),
    ("postgresql", "postgresql"), ("aurora_postgresql", "postgresql"),
])
def test_dialect_for_db_type(db_type, dialect):
    assert bulk.dialect_for(db_type) == dialect


def test_is_query_detects_select_and_with_only():
    assert bulk.is_query("  select * from t")
    assert bulk.is_query("WITH x AS (SELECT 1) SELECT * FROM x")
    assert not bulk.is_query("dbo.selections")
    assert not bulk.is_query("public.with_history")


def test_build_select_star_when_no_columns():
    assert bulk.build_select("dbo.customer", [], "", "sqlserver") == "SELECT * FROM dbo.customer"


def test_build_select_aliases_source_to_target_names_sqlserver():
    cols = [bulk.ColumnPair("CUST_ID", "customer_id"), bulk.ColumnPair("NAME", "full_name")]
    assert bulk.build_select("dbo.customer", cols, "active = 1", "sqlserver") == (
        "SELECT CUST_ID AS [customer_id], NAME AS [full_name] FROM dbo.customer WHERE active = 1"
    )


def test_build_select_quotes_aliases_for_postgres_and_oracle():
    cols = [bulk.ColumnPair("customer_id", "customer_id")]
    assert bulk.build_select("public.customer", cols, "", "postgresql") == (
        'SELECT customer_id AS "customer_id" FROM public.customer'
    )
    assert bulk.build_select("SALES.CUSTOMER", [bulk.ColumnPair("CUST_ID", "customer_id")], "", "oracle") == (
        'SELECT CUST_ID AS "customer_id" FROM SALES.CUSTOMER'
    )


def test_build_select_quotes_non_plain_names_on_sqlserver():
    cols = [bulk.ColumnPair("Order Date", "order_date")]
    assert bulk.build_select("dbo.Order Lines", cols, "", "sqlserver") == (
        "SELECT [Order Date] AS [order_date] FROM dbo.[Order Lines]"
    )


def test_build_select_rejects_non_plain_names_on_postgres():
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        bulk.build_select("public.orders", [bulk.ColumnPair("order date", "order_date")], "", "postgresql")


def test_build_select_rejects_mutating_where():
    with pytest.raises(ValueError, match="read-only"):
        bulk.build_select("dbo.t", [], "1=1; DROP TABLE dbo.t", "sqlserver")
