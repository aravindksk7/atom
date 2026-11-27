from __future__ import annotations

import pytest

from etl_framework.bo_layout.assemble import assemble, candidate_tables, normalise_header
from etl_framework.bo_layout.extract import ExtractResult, extract_tables
from etl_framework.bo_layout.model import PdfReadError, PdfReadOptions, RawTable
from tests.helpers.pdf_fixtures import build_bo_pdf

H = ["Id", "Name", "Amount"]
X = [0.0, 100.0, 200.0, 300.0]


def rt(rows, page=1, top=100.0, col_x=X, above=None, starts=False, ends=False):
    return RawTable(
        rows=rows,
        page=page,
        top=top,
        bottom=top + 20 * len(rows),
        col_x=list(col_x),
        above_text=above,
        starts_page=starts,
        ends_page=ends,
    )


def er(*tables):
    return ExtractResult(
        tables=list(tables),
        text_lines=[],
        page_count=max([t.page for t in tables] or [1]),
        producer=None,
        creator=None,
    )


def test_sample1_assembles_prompts_fill_down_and_empty_row(real_pdf, bo_expected):
    e = bo_expected["sample1"]
    t, prompts, st = assemble(extract_tables(real_pdf("sample1.pdf")), PdfReadOptions())
    assert t.header == e["header"] and t.rows == e["raw_rows_filled"]
    assert [p.label for p in prompts] == e["prompt_labels"] and [p.value for p in prompts] == e["prompt_values"]
    assert (st.filled_cells, st.dropped_empty_rows) == (e["filled_cells"], e["dropped_empty_rows"])


def test_fill_down_off_keeps_none():
    t, _, st = assemble(er(rt([H, ["1", "a", "5"], ["2", None, "6"]])), PdfReadOptions(fill_down=False))
    assert t.rows[1] == ["2", None, "6"] and st.filled_cells == 0


def test_empty_rows_dropped_before_fill_down():
    t, _, st = assemble(er(rt([H, ["1", "a", "5"], ["", None, None]])), PdfReadOptions())
    assert t.rows == [["1", "a", "5"]] and (st.dropped_empty_rows, st.filled_cells) == (1, 0)


def test_first_row_none_stays_none():
    t, _, _ = assemble(er(rt([H, [None, "a", "5"]])), PdfReadOptions())
    assert t.rows == [[None, "a", "5"]]


def test_repeated_header_pages_stitch_into_one_table():
    t, _, st = assemble(
        er(
            rt([H, ["1", "a", "5"], ["2", "b", "6"]], page=1, ends=True),
            rt([H, ["3", "c", "7"]], page=2, top=10, starts=True),
        ),
        PdfReadOptions(),
    )
    assert t.rows == [["1", "a", "5"], ["2", "b", "6"], ["3", "c", "7"]]
    assert (t.pages, t.fragments, st.dropped_repeated_headers) == ([1, 2], 2, 1)


def test_headerless_continuation_appends_when_columns_align():
    t, _, _ = assemble(
        er(
            rt([H, ["1", "a", "5"]], page=1, ends=True),
            rt([["2", "b", "6"]], page=2, top=10, col_x=[0.5, 100.5, 200.5, 300.5], starts=True),
        ),
        PdfReadOptions(),
    )
    assert t.rows == [["1", "a", "5"], ["2", "b", "6"]] and t.pages == [1, 2]


def test_misaligned_headerless_fragment_is_a_separate_group():
    with pytest.raises(PdfReadError) as e:
        assemble(
            er(
                rt([H, ["1", "a", "5"]], page=1, ends=True),
                rt([["2", "b", "6"]], page=2, top=10, col_x=[0.0, 150.0, 200.0, 300.0], starts=True),
            ),
            PdfReadOptions(),
        )
    assert e.value.code == "several_tables" and len(e.value.tables) == 2


def test_body_row_equal_to_header_dropped():
    t, _, st = assemble(er(rt([H, ["1", "a", "5"], list(H), ["2", "b", "6"]])), PdfReadOptions())
    assert t.rows == [["1", "a", "5"], ["2", "b", "6"]] and st.dropped_repeated_headers == 1


def test_several_tables_need_a_choice_and_can_be_chosen():
    two = er(
        rt([["Id", "V"], ["1", "a"]], col_x=[0, 100, 200]),
        rt([["Code", "Amt"], ["x", "9"]], top=300, col_x=[0, 100, 200]),
    )
    with pytest.raises(PdfReadError) as e:
        assemble(two, PdfReadOptions())
    assert e.value.code == "several_tables" and e.value.message == "the PDF holds several data tables; choose one"
    assert e.value.tables == [
        {"index": 1, "header": ["Id", "V"], "rows": 1, "pages": [1]},
        {"index": 2, "header": ["Code", "Amt"], "rows": 1, "pages": [1]},
    ]
    t, _, st = assemble(two, PdfReadOptions(table=2))
    assert t.header == ["Code", "Amt"] and len(st.ignored_tables) == 1
    assert assemble(two, PdfReadOptions(table="amt"))[0].header == ["Code", "Amt"]
    with pytest.raises(PdfReadError) as e:
        assemble(two, PdfReadOptions(table="zzz"))
    assert e.value.code == "table_not_found" and len(e.value.tables) == 2


def test_section_report_rejected_on_one_page():
    with pytest.raises(PdfReadError) as e:
        assemble(
            er(
                rt([H, ["1", "a", "5"]], above="EAST"),
                rt([H, ["2", "b", "6"]], top=300, above="WEST"),
            ),
            PdfReadOptions(),
        )
    assert e.value.code == "section_report"


def test_section_per_page_rejected():  # Review Focus 6
    with pytest.raises(PdfReadError) as e:
        assemble(
            er(
                rt([H, ["1", "a", "5"]], page=1, above="EAST", ends=True),
                rt([H, ["2", "b", "6"]], page=2, top=40, above="WEST"),
            ),
            PdfReadOptions(),
        )
    assert e.value.code == "section_report"


def test_page_number_text_is_not_a_section():
    t, _, _ = assemble(
        er(
            rt([H, ["1", "a", "5"]], page=1, above="Page 1 of 2", ends=True),
            rt([H, ["2", "b", "6"]], page=2, top=40, above="Page 2 of 2"),
        ),
        PdfReadOptions(),
    )
    assert len(t.rows) == 2


def test_title_repeated_on_every_page_is_not_a_section():
    t, _, _ = assemble(
        er(
            rt([H, ["1", "a", "5"]], page=1, above="Daily Prices", ends=True),
            rt([H, ["2", "b", "6"]], page=2, top=40, above="Daily Prices"),
        ),
        PdfReadOptions(),
    )
    assert len(t.rows) == 2


def test_synthetic_section_per_page_pdf_rejected(tmp_path):
    p = build_bo_pdf(
        tmp_path / "s.pdf",
        tables=[
            {"header": H, "rows": [["1", "a", "5"]], "above": "EAST"},
            {"header": H, "rows": [["2", "b", "6"]], "above": "WEST", "new_page": True},
        ],
    )
    with pytest.raises(PdfReadError) as e:
        assemble(extract_tables(p), PdfReadOptions())
    assert e.value.code == "section_report"


def test_prompts_only_pdf_has_no_data_table():  # Review Focus 5
    with pytest.raises(PdfReadError) as e:
        assemble(er(rt([["1. Enter date:", "03-Jan-2025"]], col_x=[0, 100, 200], above="Prompts:")), PdfReadOptions())
    assert (e.value.code, e.value.message) == ("no_data_table", "no data table found in the PDF")


def test_header_only_table_gives_zero_rows():  # Review Focus 4
    t, _, _ = assemble(er(rt([H])), PdfReadOptions())
    assert (t.header, t.rows) == (H, [])


def test_duplicate_and_empty_headers_are_renamed():
    t, _, _ = assemble(er(rt([["X", "X", ""], ["1", "2", "3"]])), PdfReadOptions())
    assert t.header == ["X", "X (2)", "Column 3"]


def test_normalise_header():
    assert normalise_header(" Trade\nDate ") == "Trade Date" and normalise_header(None) == ""


def test_candidate_tables_lists_groups_without_choosing():
    groups, prompts = candidate_tables(
        er(
            rt([["Id", "V"], ["1", "a"]], col_x=[0, 100, 200]),
            rt([["Code", "Amt"], ["x", "9"]], top=300, col_x=[0, 100, 200]),
        )
    )
    assert [g.header for g in groups] == [["Id", "V"], ["Code", "Amt"]] and prompts == []


def test_paginated_synthetic_stitches_end_to_end(tmp_path):
    rows = [[str(i), f"{i}.00"] for i in range(60)]
    for repeat in (True, False):
        p = build_bo_pdf(
            tmp_path / f"p{repeat}.pdf",
            tables=[{"header": ["Id", "V"], "rows": rows}],
            rows_per_page=25,
            repeat_header=repeat,
        )
        t, _, _ = assemble(extract_tables(p), PdfReadOptions())
        assert (t.rows, t.pages) == (rows, [1, 2, 3])


def test_paginated_with_page_numbers_stitches(tmp_path):
    rows = [[str(i), f"{i}.00"] for i in range(60)]
    p = build_bo_pdf(
        tmp_path / "n.pdf",
        tables=[{"header": ["Id", "V"], "rows": rows}],
        rows_per_page=25,
        page_text=lambda n, total: f"Page {n} of {total}",
    )
    t, _, _ = assemble(extract_tables(p), PdfReadOptions())
    assert t.rows == rows
