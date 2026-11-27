"""Tests for BO layout cell cleaning, number/date parsing, and column typing."""
from __future__ import annotations

import locale

import pandas as pd
import pytest

from etl_framework.bo_layout.assemble import assemble
from etl_framework.bo_layout.extract import extract_tables
from etl_framework.bo_layout.model import PdfReadOptions, PdfTable
from etl_framework.bo_layout.values import clean_cell, infer_date_format, parse_number, to_frame


def tbl(header, rows):
    return PdfTable(header=header, rows=rows, pages=[1], fragments=1)


def test_clean_cell():
    assert clean_cell("ALPHA BETA\nGAMMA") == "ALPHA BETA GAMMA"
    assert clean_cell("Alpha-x-B-\nTRUST") == "Alpha-x-B-TRUST"
    assert clean_cell("a/\nb") == "a/b"
    assert clean_cell("  x \u00a0  y ") == "x y"
    assert clean_cell("\uff21\uff22") == "AB"
    assert clean_cell(None) is None and clean_cell("") == ""


@pytest.mark.parametrize(
    "text, locale_name, expected",
    [
        ("1,234.50", "en", (1234.5, 2, "")),
        ("+14.9", "en", (14.9, 1, "")),
        ("-0.2", "en", (-0.2, 1, "")),
        ("(1,234.50)", "en", (-1234.5, 2, "")),
        ("3,141,592", "en", (3141592.0, 0, "")),
        ("0.5", "en", (0.5, 1, "")),
        ("12%", "en", (12.0, 0, "%")),
        ("$1.00", "en", (1.0, 2, "$")),
        ("1.234,50", "eu", (1234.5, 2, "")),
        ("1\u00a0234,50", "eu", (1234.5, 2, "")),
        ("1 234,50 €", "eu", (1234.5, 2, "€")),
        ("928", "en", (928.0, 0, "")),
        ("($1,234.50)", "en", (-1234.5, 2, "$")),
        ("-£500", "en", (-500.0, 0, "£")),
        ("¥0", "en", (0.0, 0, "¥")),
    ],
)
def test_parse_number(text, locale_name, expected):
    assert parse_number(text, locale_name) == expected


@pytest.mark.parametrize(
    "text",
    ["", "007", "1234567890123456", "12a", "1,23", "1,2345", "--1", "1.2.3", "% 5 %", "(-1)", "1 234"],
)
def test_parse_number_rejects(text):
    assert parse_number(text, "en") is None


def test_parse_number_eu_rejects():
    assert parse_number("12.34", "eu") is None
    assert parse_number("1,234.50", "eu") is None


def test_infer_date_formats():
    assert infer_date_format(["03-Jan-2025", "04-Jan-2025"], None) == ("%d-%b-%Y", [])
    assert infer_date_format(["01/15/25", "12/31/99"], None) == ("%m/%d/%y", [])
    assert infer_date_format(["March 3, 2025"], None) == ("%B %d, %Y", [])
    assert infer_date_format(["Monday, March 3, 2025"], None) == ("%A, %B %d, %Y", [])
    assert infer_date_format(["03/15/2025 12:00:00 AM"], None) == ("%m/%d/%Y %I:%M:%S %p", [])
    assert infer_date_format(["2025-03-03"], None) == ("%Y-%m-%d", [])
    assert infer_date_format(["31-Feb-2025"], None) == (None, [])
    assert infer_date_format(["29-Feb-2024"], None) == ("%d-%b-%Y", [])
    assert infer_date_format(["29-Feb-2025"], None) == (None, [])
    assert infer_date_format(["hello"], None) == (None, [])
    assert infer_date_format([], None) == (None, [])


def test_ambiguous_dates_need_order():
    assert infer_date_format(["01/02/26", "03/04/26"], None) == (None, ["%m/%d/%y", "%d/%m/%y"])
    assert infer_date_format(["01/02/26", "03/04/26"], "dmy") == ("%d/%m/%y", [])
    assert infer_date_format(["01/02/26", "03/04/26"], "mdy") == ("%m/%d/%y", [])


def test_month_names_ignore_process_locale(monkeypatch):
    monkeypatch.setattr(locale, "getlocale", lambda *a: ("de_DE", "UTF-8"))
    assert infer_date_format(["03-Jan-2025"], None) == ("%d-%b-%Y", [])


def test_to_frame_sample1(real_pdf, bo_expected):
    e = bo_expected["sample1"]
    t, _, _ = assemble(extract_tables(real_pdf("sample1.pdf")), PdfReadOptions())
    df, info, warnings = to_frame(t, PdfReadOptions())
    assert df.values.tolist() == e["typed_rows"] and warnings == []
    assert info[e["date_column"]].date_format == e["date_format"] and info[e["number_column"]].displayed_decimals == e["decimals"]


def test_to_frame_sample2(real_pdf, bo_expected):
    e = bo_expected["sample2"]
    t, _, _ = assemble(extract_tables(real_pdf("sample2.pdf")), PdfReadOptions())
    df, info, _ = to_frame(t, PdfReadOptions())
    assert len(df) == e["rows"] and df[e["id_column"]].dtype == "float64" and info[e["id_column"]].displayed_decimals == 0
    assert all(info[c].date_format == e["date_format"] for c in e["date_columns"])
    assert df[e["date_columns"][-1]].iloc[-1] == e["raw_last_row"][-1]


def test_to_frame_sample3(real_pdf, bo_expected):
    e = bo_expected["sample3"]
    t, _, _ = assemble(extract_tables(real_pdf("sample3.pdf")), PdfReadOptions())
    df, info, _ = to_frame(t, PdfReadOptions())
    assert len(df) == e["rows"] and set(e["joined_values"]) <= set(df[e["key"][0]])
    assert all(info[c].kind == "number" and info[c].displayed_decimals == e["decimals"] for c in e["number_columns"])


def test_units_must_match_and_mixed_column_warns():
    df, info, warnings = to_frame(
        tbl(["A", "B", "C"], [["12%", "1.5", "$1.00"]] + [["5", "1.5", "$2.50"]] * 8 + [["5", "n/a", "$2.50"]]),
        PdfReadOptions(),
    )
    assert info["A"].kind == "text" and info["C"].kind == "number" and info["C"].unit == "$"
    assert info["B"].kind == "text" and "mixed_column: B is 90% number; other values: n/a" in warnings


def test_mixed_date_column_warns():
    rows = [["2025-01-0" + str(i)] for i in range(1, 10)] + [["TBD"]]
    _, info, warnings = to_frame(tbl(["D"], rows), PdfReadOptions())
    assert info["D"].kind == "text"
    assert warnings == ["mixed_column: D is 90% date; other values: TBD"]


def test_ambiguous_date_column_warns_and_stays_text():
    _, info, warnings = to_frame(tbl(["D"], [["01/02/26"], ["03/04/26"]]), PdfReadOptions())
    assert info["D"].kind == "text" and info["D"].ambiguous_formats == ["%m/%d/%y", "%d/%m/%y"]
    assert warnings == ["ambiguous_date_format: D could be %m/%d/%y or %d/%m/%y; set the date order"]


def test_leading_zero_codes_stay_text():
    _, info, _ = to_frame(tbl(["Code"], [["007"], ["123"]]), PdfReadOptions())
    assert info["Code"].kind == "text"


def test_null_and_empty_cells_in_number_and_text_columns():
    df, info, warnings = to_frame(
        tbl(["Num", "Txt"], [["1.5", None], [None, ""], ["", "ok"]]),
        PdfReadOptions(),
    )
    assert info["Num"].kind == "number"
    assert df["Num"].dtype == "float64"
    assert df["Num"].iloc[0] == 1.5 and pd.isna(df["Num"].iloc[1]) and pd.isna(df["Num"].iloc[2])
    assert info["Txt"].kind == "text"
    assert df["Txt"].iloc[0] is pd.NA and df["Txt"].iloc[1] == "" and df["Txt"].iloc[2] == "ok"
    assert warnings == []


def test_header_only_frame_is_empty_with_columns():
    df, info, _ = to_frame(tbl(["A", "B"], []), PdfReadOptions())
    assert list(df.columns) == ["A", "B"] and len(df) == 0 and info["A"].kind == "text"
