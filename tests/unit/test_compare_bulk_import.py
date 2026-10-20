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


def test_read_sheet_xlsx_keeps_na_text():
    buf = io.BytesIO()
    pd.DataFrame([{
        "job_name": "x", "source_config": "s", "source_table": "a",
        "target_config": "t", "target_table": "b", "description": "N/A", "tags": "NA",
    }]).to_excel(buf, index=False)
    rows = bulk.read_sheet("map.xlsx", buf.getvalue())
    assert rows[0]["description"] == "N/A"
    assert rows[0]["tags"] == "NA"


@pytest.mark.parametrize("fname", ["map.csv", "map.xlsx"])
def test_read_sheet_rejects_duplicate_headers(fname):
    if fname.endswith(".csv"):
        content = _csv(HEADER + ",Job_Name", "a,s,t1,t,t2,b")
    else:
        buf = io.BytesIO()
        pd.DataFrame([["a", "s", "t1", "t", "t2", "b"]],
                     columns=HEADER.split(",") + ["Job_Name"]).to_excel(buf, index=False)
        content = buf.getvalue()
    with pytest.raises(bulk.SheetError, match="Duplicate column.*job_name"):
        bulk.read_sheet(fname, content)


def test_read_sheet_rejects_blank_header():
    with pytest.raises(bulk.SheetError, match="blank column header"):
        bulk.read_sheet("map.csv", _csv(HEADER + ",", "a,s,t1,t,t2,"))


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


# -- build_job / preview ------------------------------------------------------------

class _Cfg:
    def __init__(self, id, name, config_json):
        self.id, self.name, self.config_json = id, name, config_json


CONFIGS = {
    "SQL_PROD": _Cfg(1, "SQL_PROD", {"db_type": "mssql", "connections": {"dw": {"db_type": "oracle"}}}),
    "PG_PROD": _Cfg(2, "PG_PROD", {"db_type": "aurora_postgresql"}),
    "S3_ONLY": _Cfg(3, "S3_ONLY", {"db_type": "snowflake"}),
}


def _row(**overrides) -> dict:
    row = {
        "job_name": "cust_cmp", "source_config": "SQL_PROD", "source_table": "dbo.customer",
        "target_config": "PG_PROD", "target_table": "public.customer",
        "key_columns": "CUST_ID:customer_id", "compare_columns": "NAME:full_name;email",
    }
    return {**row, **overrides}


def test_build_job_generates_sql_compare_job():
    job, query_a, query_b = bulk.build_job(_row(tags="nightly;crm", description="Customer"), CONFIGS)
    assert query_a == "SELECT CUST_ID AS [customer_id], NAME AS [full_name], email AS [email] FROM dbo.customer"
    assert query_b == 'SELECT customer_id AS "customer_id", full_name AS "full_name", email AS "email" FROM public.customer'
    assert job.name == "cust_cmp"
    assert job.job_type == "compare"
    assert job.tags == ["nightly", "crm"]
    assert job.description == "Customer"
    req = job.params["request"]
    assert job.params["compare_type"] == "sql"
    assert (req["config_id_a"], req["config_id_b"]) == (1, 2)
    assert req["key_columns"] == ["customer_id"]
    assert req["label_a"] == "SQL_PROD dbo.customer"
    assert req["label_b"] == "PG_PROD public.customer"
    assert req["chunk_size"] == 10000
    assert req["advanced"]["float_tolerance"] == 1e-9


def test_build_job_uses_named_connection_dialect():
    _, query_a, _ = bulk.build_job(_row(source_connection="dw"), CONFIGS)
    assert query_a.startswith('SELECT CUST_ID AS "customer_id"')


def test_build_job_key_only_without_rename_selects_star():
    _, query_a, query_b = bulk.build_job(_row(key_columns="id", compare_columns=""), CONFIGS)
    assert query_a == "SELECT * FROM dbo.customer"
    assert query_b == "SELECT * FROM public.customer"


def test_build_job_options_tolerance_ignore_case_chunk_exclude():
    job, _, _ = bulk.build_job(_row(
        numeric_tolerance="0.01", ignore_case="yes", chunk_size="500", exclude_columns="load_ts;etl_id",
    ), CONFIGS)
    req = job.params["request"]
    assert req["advanced"]["float_tolerance"] == 0.01
    assert req["advanced"]["case_insensitive_columns"] == ["customer_id", "full_name", "email"]
    assert req["chunk_size"] == 500
    assert req["exclude_columns"] == ["load_ts", "etl_id"]


def test_build_job_wheres_apply_per_side():
    _, query_a, query_b = bulk.build_job(_row(source_where="active = 1", target_where="is_active"), CONFIGS)
    assert query_a.endswith("FROM dbo.customer WHERE active = 1")
    assert query_b.endswith("FROM public.customer WHERE is_active")


def test_build_job_query_cell_passes_through():
    _, query_a, _ = bulk.build_job(_row(
        source_table="SELECT CUST_ID AS customer_id, NAME AS full_name, email FROM dbo.customer",
        key_columns="customer_id", compare_columns="",
    ), CONFIGS)
    assert query_a == "SELECT CUST_ID AS customer_id, NAME AS full_name, email FROM dbo.customer"


@pytest.mark.parametrize("overrides,message", [
    ({"job_name": ""}, "job_name is required"),
    ({"source_config": "NOPE"}, "source_config 'NOPE' does not exist"),
    ({"target_connection": "missing"}, "target_connection 'missing' does not exist in config 'PG_PROD'"),
    ({"target_config": "S3_ONLY"}, "is not a SQL database"),
    ({"key_columns": "a:b:c"}, "key_columns"),
    ({"compare_columns": "", "key_columns": "CUST_ID:customer_id"}, "renamed key"),
    ({"compare_columns": "", "key_columns": "id", "ignore_case": "true"}, "ignore_case needs compare_columns"),
    ({"source_table": "SELECT * FROM dbo.customer"}, "source_table is a query"),
    ({"target_table": "SELECT * FROM public.customer", "key_columns": "id", "compare_columns": "",
      "target_where": "x = 1"}, "target_where"),
    ({"numeric_tolerance": "abc"}, "numeric_tolerance"),
    ({"numeric_tolerance": "-1"}, "numeric_tolerance"),
    ({"chunk_size": "-5"}, "chunk_size"),
    ({"ignore_case": "maybe"}, "ignore_case"),
    ({"source_where": "1=1; DELETE FROM x"}, "read-only"),
])
def test_build_job_row_errors(overrides, message):
    with pytest.raises(bulk.RowError) as exc_info:
        bulk.build_job(_row(**overrides), CONFIGS)
    assert any(message in err for err in exc_info.value.errors), exc_info.value.errors


def test_build_job_rejects_where_hiding_a_statement_behind_a_literal():
    with pytest.raises(bulk.RowError, match="read-only"):
        bulk.build_job(_row(source_where="name = 'x--' ; DROP TABLE dbo.t"), CONFIGS)


def test_build_job_rejects_target_column_mapped_twice():
    with pytest.raises(bulk.RowError) as exc_info:
        bulk.build_job(_row(compare_columns="A:x;B:x"), CONFIGS)
    assert any("target column 'x' is mapped more than once" in e for e in exc_info.value.errors)


def test_build_job_key_repeated_in_compare_columns_is_not_a_duplicate():
    job, _, _ = bulk.build_job(_row(compare_columns="CUST_ID:customer_id;email"), CONFIGS)
    assert job.params["request"]["key_columns"] == ["customer_id"]


@pytest.mark.parametrize("value", ["inf", "nan", "-inf"])
def test_build_job_rejects_non_finite_tolerance(value):
    with pytest.raises(bulk.RowError) as exc_info:
        bulk.build_job(_row(numeric_tolerance=value), CONFIGS)
    assert any("numeric_tolerance" in e for e in exc_info.value.errors)


def test_build_job_accepts_integral_float_chunk_size_and_rejects_fractions():
    job, _, _ = bulk.build_job(_row(chunk_size="10000.0"), CONFIGS)
    assert job.params["request"]["chunk_size"] == 10000
    for bad in ("1.5", "-5"):
        with pytest.raises(bulk.RowError) as exc_info:
            bulk.build_job(_row(chunk_size=bad), CONFIGS)
        assert any("chunk_size" in e for e in exc_info.value.errors)


def test_build_job_validation_errors_include_field_location(monkeypatch):
    from api.schemas import SQLCompareRequest

    monkeypatch.setattr(bulk, "SQLCompareRequest", lambda **kw: SQLCompareRequest(config_id_a="x"))
    with pytest.raises(bulk.RowError) as exc_info:
        bulk.build_job(_row(), CONFIGS)
    assert any(e.startswith("config_id_a: ") for e in exc_info.value.errors), exc_info.value.errors


def test_build_job_non_dict_connection_entry_is_a_row_error():
    configs = {**CONFIGS, "BAD": _Cfg(9, "BAD", {"db_type": "mssql", "connections": {"c": "oops"}})}
    with pytest.raises(bulk.RowError) as exc_info:
        bulk.build_job(_row(source_config="BAD", source_connection="c"), configs)
    assert any("is not a valid connection" in e for e in exc_info.value.errors)


def test_build_job_collects_several_errors():
    with pytest.raises(bulk.RowError) as exc_info:
        bulk.build_job(_row(source_config="", target_config=""), CONFIGS)
    assert len(exc_info.value.errors) >= 2


def test_preview_rows_flags_duplicates_updates_and_counts():
    rows = [
        _row(job_name="a"), _row(job_name="a"),
        _row(job_name="b"), _row(job_name="c", source_config="NOPE"),
    ]
    out = bulk.preview_rows(rows, CONFIGS, existing_job_names={"b"})
    by_row = {r["row_number"]: r for r in out["rows"]}
    assert by_row[2]["status"] == "invalid" and "appears 2 times" in by_row[2]["errors"][0]
    assert by_row[3]["status"] == "invalid"
    assert by_row[4]["status"] == "valid" and by_row[4]["will_update"] is True
    assert by_row[4]["job"]["name"] == "b"
    assert by_row[4]["query_a"].startswith("SELECT ")
    assert by_row[5]["status"] == "invalid" and by_row[5]["job"] is None
    assert (out["valid_count"], out["invalid_count"]) == (1, 3)


def test_template_parses_and_matches_columns():
    rows = bulk.read_sheet("t.csv", bulk.TEMPLATE_CSV.encode())
    assert len(rows) == 2
    assert list(rows[0]) == list(bulk.ALL_COLUMNS)
