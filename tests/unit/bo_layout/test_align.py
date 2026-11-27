"""Unit tests for PDF-vs-PDF frame alignment (etl_framework.bo_layout.align)."""
from __future__ import annotations

import pandas as pd

from etl_framework.bo_layout.align import align_column_case, align_pdf_frames
from etl_framework.bo_layout.model import ColumnInfo, PdfSourceInfo


def info(widths=None, **cols):
    return PdfSourceInfo(
        "p.pdf",
        "",
        0,
        1,
        None,
        None,
        {"column_widths": widths if widths is not None else {}},
        0,
        0,
        0,
        [],
        [],
        [],
        cols,
    )


def test_align_column_case_renames_and_skips_ambiguous_or_colliding():
    df = pd.DataFrame({"id": [1], "name": ["a"], "amt": [2], "AMT": [3]})
    aligned = align_column_case(df, ["ID", "Name", "NAME", "Amt"])
    assert list(aligned.columns) == ["ID", "name", "amt", "AMT"]
    # Input not mutated
    assert list(df.columns) == ["id", "name", "amt", "AMT"]


def test_case_insensitive_columns_follow_key_columns():
    a, b, _ = align_pdf_frames(
        pd.DataFrame({"As Of Date": ["x"]}),
        pd.DataFrame({"AS OF DATE": ["x"]}),
        info(**{"As Of Date": ColumnInfo("text")}),
        info(**{"AS OF DATE": ColumnInfo("text")}),
        key_columns=["As Of Date"],
    )
    assert list(b.columns) == ["As Of Date"]


def test_case_insensitive_column_info_and_widths_after_rename():
    a, b, meta = align_pdf_frames(
        pd.DataFrame({"Group": ["NORTHERN REGION TR"]}),
        pd.DataFrame({"GROUP": ["NORTHERN REGIONTR"]}),
        info({"Group": 120.0}, Group=ColumnInfo("text")),
        info({"GROUP": 90.0}, GROUP=ColumnInfo("text")),
        key_columns=["Group"],
    )
    assert list(a.columns) == ["Group"]
    assert list(b.columns) == ["Group"]
    assert b["Group"].tolist() == ["NORTHERN REGION TR"]
    assert meta["rewrapped_values"] == {"Group": 1}


def test_numbers_compared_as_is_and_decimal_change_warned():  # Review Focus 2
    a, b, meta = align_pdf_frames(
        pd.DataFrame({"v": [1.23]}),
        pd.DataFrame({"v": [1.2]}),
        info(v=ColumnInfo("number", 2)),
        info(v=ColumnInfo("number", 1)),
    )
    assert (a["v"].iloc[0], b["v"].iloc[0]) == (1.23, 1.2)
    assert meta["warnings"] == ["v: printed with different decimals (A 2 dp, B 1 dp)"]
    assert meta["printed_as"]["v"] == {"a": "2 dp", "b": "1 dp"}
    assert (
        meta["precision_note"]
        == "compared as printed; differences smaller than the printed decimals can't be seen (v: 1 dp)"
    )


def test_date_format_change_warned_and_compared_as_text():  # Review Focus 2
    _, _, meta = align_pdf_frames(
        pd.DataFrame({"D": ["03-Jan-2025"]}),
        pd.DataFrame({"D": ["01/03/25"]}),
        info(D=ColumnInfo("date", date_format="%d-%b-%Y")),
        info(D=ColumnInfo("date", date_format="%m/%d/%y")),
    )
    assert meta["warnings"] == ["D: printed in different date formats (A %d-%b-%Y, B %m/%d/%y)"]
    assert meta["printed_as"]["D"] == {"a": "%d-%b-%Y", "b": "%m/%d/%y"}


def test_kind_conflict_compared_as_text():
    a, b, meta = align_pdf_frames(
        pd.DataFrame({"v": [1.5, float("nan")]}),
        pd.DataFrame({"v": ["n/a", None]}),
        info(v=ColumnInfo("number", 2)),
        info(v=ColumnInfo("text")),
    )
    assert (a["v"].iloc[0], b["v"].iloc[0]) == ("1.50", "n/a")
    assert a["v"].iloc[1] is pd.NA
    assert b["v"].iloc[1] is pd.NA
    assert meta["warnings"] == ["v: printed as number in A and text in B, compared as text"]


def test_ambiguous_date_counts_as_text_in_kind_conflict():
    a, b, meta = align_pdf_frames(
        pd.DataFrame({"D": ["2025-01-03"]}),
        pd.DataFrame({"D": ["01/03/2025"]}),
        info(D=ColumnInfo("date", date_format="%Y-%m-%d")),
        info(D=ColumnInfo("date", ambiguous_formats=["%d/%m/%Y", "%m/%d/%Y"])),
    )
    assert (a["D"].iloc[0], b["D"].iloc[0]) == ("2025-01-03", "01/03/2025")
    assert meta["warnings"] == ["D: printed as date in A and text in B, compared as text"]


def test_rewrap_when_widths_differ_rewrites_b_to_a_and_skips_non_unique():
    a = pd.DataFrame({"G": ["NORTHERN REGION TR", "NORTHERN REGION TR", "A B", "AB", None]})
    b = pd.DataFrame({"G": ["NORTHERN REGIONTR", "A  B", None]})
    _, b2, meta = align_pdf_frames(
        a,
        b,
        info({"G": 120.0}, G=ColumnInfo("text")),
        info({"G": 90.0}, G=ColumnInfo("text")),
    )
    assert b2["G"].iloc[:2].tolist() == ["NORTHERN REGION TR", "A  B"]
    assert pd.isna(b2["G"].iloc[2])
    assert meta["rewrapped_values"] == {"G": 1}
    assert meta["warnings"] == [
        "G: column widths differ (A 120.0 pt, B 90.0 pt); 1 values re-wrapped to A's form"
    ]


def test_no_rewrap_when_widths_equal():
    _, b2, meta = align_pdf_frames(
        pd.DataFrame({"G": ["A B"]}),
        pd.DataFrame({"G": ["AB"]}),
        info({"G": 100.0}, G=ColumnInfo("text")),
        info({"G": 100.4}, G=ColumnInfo("text")),
    )
    assert b2["G"].tolist() == ["AB"]
    assert meta["rewrapped_values"] == {}
    assert meta["warnings"] == []


def test_no_rewrap_when_disabled():
    _, b2, meta = align_pdf_frames(
        pd.DataFrame({"G": ["A B"]}),
        pd.DataFrame({"G": ["AB"]}),
        info({"G": 120.0}, G=ColumnInfo("text")),
        info({"G": 90.0}, G=ColumnInfo("text")),
        rewrap=False,
    )
    assert b2["G"].tolist() == ["AB"]
    assert meta["rewrapped_values"] == {}


def test_precision_note_uses_smaller_decimals_across_number_columns():
    _, _, meta = align_pdf_frames(
        pd.DataFrame({"a": [1.23], "b": [10.0], "c": ["x"]}),
        pd.DataFrame({"a": [1.2], "b": [10.000], "c": ["x"]}),
        info(a=ColumnInfo("number", 2), b=ColumnInfo("number", 0), c=ColumnInfo("text")),
        info(a=ColumnInfo("number", 1), b=ColumnInfo("number", 3), c=ColumnInfo("text")),
    )
    assert (
        meta["precision_note"]
        == "compared as printed; differences smaller than the printed decimals can't be seen (a: 1 dp, b: 0 dp)"
    )


def test_no_number_columns_no_precision_note():
    assert (
        align_pdf_frames(
            pd.DataFrame({"G": ["x"]}),
            pd.DataFrame({"G": ["x"]}),
            info(G=ColumnInfo("text")),
            info(G=ColumnInfo("text")),
        )[2]["precision_note"]
        is None
    )


def test_inputs_not_mutated():
    a = pd.DataFrame({"v": [1.5]})
    b = pd.DataFrame({"v": ["n/a"]})
    a_out, b_out, _ = align_pdf_frames(
        a, b, info(v=ColumnInfo("number", 2)), info(v=ColumnInfo("text"))
    )
    assert a["v"].iloc[0] == 1.5
    assert b["v"].iloc[0] == "n/a"
    assert a_out is not a and b_out is not b
