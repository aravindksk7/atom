import math, pandas as pd, pytest
from etl_framework.bo_layout.model import PdfReadError, PdfReadOptions
from etl_framework.bo_layout.reader import read_pdf_report
from tests.helpers.xlsx_oracle import read_xlsx_table

def _read(path): return read_pdf_report(path.read_bytes(), PdfReadOptions(), file_name=path.name)

def _plain(corpus): return [c for c in corpus if c["layout"] in ("plain", "paginated", "footer")]

def test_twin_exports_reconcile_with_zero_differences(corpus):
    items = [c for c in _plain(corpus) if c.get("twin")]
    assert len(items) >= 3, "the corpus needs at least 3 twin pairs"
    for item in items:
        (a, _), (b, _) = _read(item["file"]), _read(item["twin"])
        s = lambda df: df.sort_values(item["key"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(s(a), s(b), obj=item["file"].name)

def test_row_counts_match_bo(corpus):
    for item in _plain(corpus):
        df, _ = _read(item["file"])
        assert len(df) == item["rows"], item["file"].name

def test_hand_checked_cells(corpus):
    for item in [c for c in _plain(corpus) if c.get("cells")]:
        df, _ = _read(item["file"])
        for cell in item["cells"]:
            row = df.loc[(df[list(cell["key"])] == pd.Series(cell["key"])).all(axis=1)]
            assert len(row) == 1, (item["file"].name, cell)
            got = row.iloc[0][cell["column"]]
            assert (math.isclose(got, cell["value"], abs_tol=1e-9) if isinstance(cell["value"], (int, float)) else got == cell["value"]), (item["file"].name, cell, got)

def test_footer_sum_matches_body(corpus):
    for item in [c for c in corpus if c.get("sum_check")]:
        df, info = _read(item["file"])
        sc = item["sum_check"]; d = info.column_info[sc["column"]].displayed_decimals
        is_sum = df[sc["label_column"]] == sc["label"]
        body = df.loc[~is_sum & df[sc["label_column"]].notna() & (df[sc["label_column"]] != ""), sc["column"]]
        tol = (len(body) + 1) * 0.5 * 10 ** -d + 1e-9
        assert abs(body.sum() - df.loc[is_sum, sc["column"]].iloc[0]) <= tol, item["file"].name

def test_pdf_agrees_with_xlsx_of_the_same_run(corpus):                  # D-3 oracle; skips items without "xlsx"
    items = [c for c in _plain(corpus) if c.get("xlsx")]
    if not items:
        pytest.skip("no corpus item has an xlsx twin (decision D-3)")
    for item in items:
        df, info = _read(item["file"])
        x = read_xlsx_table(item["xlsx"], list(df.columns))
        assert len(x) == len(df), item["file"].name
        for col, ci in info.column_info.items():
            if ci.kind == "number":
                tol = 0.5 * 10 ** -(ci.displayed_decimals or 0) + 1e-9
                scale = 100.0 if ci.unit == "%" and x[col].abs().max() <= 1.5 and df[col].abs().max() > 1.5 else 1.0
                pv, xv = sorted(df[col].dropna()), sorted(pd.to_numeric(x[col], errors="coerce").dropna() * scale)
                assert len(pv) == len(xv) and all(abs(p - q) <= tol for p, q in zip(pv, xv)), (item["file"].name, col)
            elif ci.kind == "text":
                norm = lambda s: sorted("".join(str(v).split()) for v in s.dropna() if str(v).strip() != "")
                assert norm(df[col]) == norm(x[col]), (item["file"].name, col)
            # date columns: the PDF keeps BO's text; comparing it would need date rendering, which is out of scope

@pytest.mark.parametrize("name", ["sample1", "sample2", "sample3"])
def test_original_samples_agree_with_xlsx_of_the_same_run(real_pdf, name):        # D-3; no corpus needed
    df, info = _read(real_pdf(f"{name}.pdf"))
    x = read_xlsx_table(real_pdf(f"{name}.xlsx"), list(df.columns))
    assert len(x) == len(df)
    for col, ci in info.column_info.items():
        if ci.kind == "number":
            tol = 0.5 * 10 ** -(ci.displayed_decimals or 0) + 1e-9
            pv, xv = sorted(df[col].dropna()), sorted(pd.to_numeric(x[col], errors="coerce").dropna())
            assert len(pv) == len(xv) and all(abs(p - q) <= tol for p, q in zip(pv, xv)), col
        elif ci.kind == "text":
            norm = lambda s: sorted("".join(str(v).split()) for v in s.dropna() if str(v).strip() != "")
            assert norm(df[col]) == norm(x[col]), col

@pytest.mark.parametrize("layout, code", [("section", "section_report"), ("breaks", None), ("multi_block", "several_tables")])
def test_unsupported_layouts_fail_loudly_or_read_every_row(corpus, layout, code):
    items = [c for c in corpus if c["layout"] == layout]
    if not items:
        pytest.skip(f"no {layout} sample in the corpus")
    for item in items:
        if code:
            with pytest.raises(PdfReadError) as e:
                _read(item["file"])
            assert e.value.code == code, item["file"].name
        else:
            df, _ = _read(item["file"])
            assert len(df) == item["rows"], item["file"].name                 # subtotal rows are kept as rows

def test_corpus_producers_match_prod(corpus, bo_expected):
    prod = bo_expected["prod"]
    for item in corpus:
        assert (item.get("producer"), item.get("creator")) == (prod["producer"], prod["creator"]), item["file"].name
