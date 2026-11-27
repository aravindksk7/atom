import time, pdfplumber, pytest
import etl_framework.bo_layout.extract as extract_mod
from etl_framework.bo_layout.extract import extract_tables, grid_tables
from etl_framework.bo_layout.model import PdfReadError
from tests.helpers.pdf_fixtures import build_bo_pdf

def test_sample1_two_tables_with_geometry(real_pdf, bo_expected):
    e, prod = bo_expected["sample1"], bo_expected["prod"]
    r = extract_tables(real_pdf("sample1.pdf"))
    assert r.page_count == 1 and r.creator == prod["creator"] and r.producer.startswith(prod["producer"])
    assert [len(t.rows) for t in r.tables] == e["raw_table_rows"]
    assert r.tables[0].above_text == "Prompts:"
    assert r.tables[1].rows[0] == e["header"]
    assert r.tables[1].rows[2] == e["raw_merged_row"] and r.tables[1].rows[3] == e["raw_empty_row"]

def test_sample2_one_tall_table(real_pdf, bo_expected):
    e = bo_expected["sample2"]
    t = extract_tables(real_pdf("sample2.pdf")).tables
    assert [len(x.rows) for x in t] == e["raw_table_rows"]
    assert t[0].rows[0] == e["header"] and t[0].rows[-1] == e["raw_last_row"]

def test_sample3_prompt_and_data_table(real_pdf, bo_expected):
    e = bo_expected["sample3"]
    r = extract_tables(real_pdf("sample3.pdf"))
    assert [len(t.rows) for t in r.tables] == e["raw_table_rows"] and r.tables[1].above_text == e["above_text"]
    assert r.tables[1].rows[0] == e["header"] and r.tables[1].rows[1][0] == e["raw_wrapped_cell"]

@pytest.mark.parametrize("name", ["sample1.pdf", "sample2.pdf", "sample3.pdf"])
def test_grid_matches_find_tables_on_real_samples(real_pdf, name):
    with pdfplumber.open(real_pdf(name)) as pdf:
        for page in pdf.pages:
            assert [t.rows for t in grid_tables(page)] == [t.extract() for t in page.find_tables()]

def test_synthetic_merged_and_wrapped_cells(tmp_path):
    p = build_bo_pdf(tmp_path / "m.pdf", tables=[{"header": ["G", "V"], "rows": [["A\nB", "1"], [None, "2"], ["", "3"]]}])
    t = extract_tables(p).tables[0]
    assert t.rows == [["G", "V"], ["A\nB", "1"], [None, "2"], ["", "3"]]

def test_paginated_synthetic_marks_page_edges(tmp_path):
    p = build_bo_pdf(tmp_path / "p.pdf", tables=[{"header": ["Id", "V"], "rows": [[str(i), "1.00"] for i in range(60)]}], rows_per_page=25)
    r = extract_tables(p)
    assert [t.page for t in r.tables] == [1, 2, 3] and r.tables[0].ends_page and r.tables[1].starts_page

def test_small_borderless_page_falls_back_to_find_tables(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pdfplumber.page.Page, "find_tables", lambda self, *a, **k: calls.append(1) or [])
    extract_tables(build_bo_pdf(tmp_path / "l.pdf", tables=[{"header": ["A"], "rows": [["1"]]}], borders="lines"))
    assert calls == [1]

def test_large_borderless_page_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(extract_mod, "FALLBACK_MAX_EDGES", 10)
    p = build_bo_pdf(tmp_path / "l.pdf", tables=[{"header": ["A"], "rows": [[str(i)] for i in range(20)]}], borders="lines")
    with pytest.raises(PdfReadError) as e:
        extract_tables(p)
    assert (e.value.code, e.value.message) == ("no_borders", "the PDF has no cell borders and is too large to read without them")

def test_preflight_errors(tmp_path):
    one = {"header": ["A"], "rows": [["1"]]}
    cases = [(build_bo_pdf(tmp_path / "e.pdf", tables=[one], encrypt="pw"), {}, 1000, "encrypted"),
             (build_bo_pdf(tmp_path / "i.pdf", tables=[], image_only=True), {}, 1000, "no_text_layer"),
             (build_bo_pdf(tmp_path / "m.pdf", tables=[{"header": ["A"], "rows": [[str(i)] for i in range(30)]}], rows_per_page=10), {}, 2, "too_many_pages"),
             (build_bo_pdf(tmp_path / "s.pdf", tables=[one]), {"explicit_lines": []}, 1000, "bad_settings")]
    (tmp_path / "g.pdf").write_bytes(b"not a pdf at all")
    cases.append((tmp_path / "g.pdf", {}, 1000, "invalid_pdf"))
    for path, settings, max_pages, code in cases:
        with pytest.raises(PdfReadError) as e:
            extract_tables(path, settings, max_pages=max_pages)
        assert e.value.code == code, path.name

@pytest.mark.slow
def test_tall_page_is_fast_and_equals_paginated_twin(tmp_path):
    rows = [[f"{i:,}", "10/23/26", f"{i * 1.25:,.2f}"] for i in range(5000)]
    h = ["Id", "Date", "Amount"]
    tall = build_bo_pdf(tmp_path / "t.pdf", tables=[{"header": h, "rows": rows}])
    paged = build_bo_pdf(tmp_path / "p.pdf", tables=[{"header": h, "rows": rows}], rows_per_page=50)
    t0 = time.monotonic(); r = extract_tables(tall); elapsed = time.monotonic() - t0
    body = lambda res: [row for t in res.tables for row in t.rows if row != h]
    assert elapsed <= 30 and body(r) == body(extract_tables(paged)) == rows
