from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import (
    BOCompareRequest,
    BOPdfPreviewOut,
    BOPdfPreviewRequest,
    PdfReadOptionsModel,
    RunStatusOut,
    SourceConfig,
)
from etl_framework.bo_layout.model import PdfReadOptions


def test_source_config_accepts_pdf_format_and_options():
    s = SourceConfig(
        source_type="upload",
        file_name="r.pdf",
        file_content_b64="eA==",
        format="pdf",
        pdf={"table": 2, "number_locale": "eu", "date_order": "dmy", "fill_down": False},
    )
    assert s.is_pdf and s.pdf.to_options() == PdfReadOptions(2, "eu", "dmy", False, {})


def test_is_pdf_by_extension_case_insensitive():
    assert SourceConfig(source_type="path", file_path="/r/REPORT.PDF").is_pdf
    assert SourceConfig(source_type="upload", file_name="x.Pdf", file_content_b64="eA==").is_pdf
    assert not SourceConfig(source_type="upload", file_name="x.xlsx", file_content_b64="eA==").is_pdf


def test_table_settings_allow_list():
    SourceConfig(source_type="path", file_path="/a.pdf", pdf={"table_settings": {"snap_tolerance": 3}})
    with pytest.raises(ValidationError, match=r"unsupported table_settings: \['explicit_lines'\]"):
        SourceConfig(source_type="path", file_path="/a.pdf", pdf={"table_settings": {"explicit_lines": []}})


def test_table_index_must_be_positive():
    with pytest.raises(ValidationError):
        SourceConfig(source_type="path", file_path="/a.pdf", pdf={"table": 0})


def test_table_string_selector_and_defaults():
    opts = PdfReadOptionsModel(table="Revenue")
    assert opts.to_options() == PdfReadOptions("Revenue", "en", None, True, {})


def test_old_bo_request_json_still_validates_with_defaults():
    req = BOCompareRequest(
        source_a={"source_type": "path", "file_path": "/a.xlsx"},
        source_b={"source_type": "path", "file_path": "/b.xlsx"},
    )
    assert req.source_a.pdf is None and req.source_a.format == "xlsx" and not req.source_a.is_pdf and req.pdf_rewrap is True


def test_run_status_out_compared_as_defaults_to_none():
    assert RunStatusOut(run_id="r", status="PASSED").compared_as is None
    assert RunStatusOut(run_id="r", status="PASSED", compared_as="printed").compared_as == "printed"


def test_preview_request_and_response_schemas():
    req = BOPdfPreviewRequest(
        source={"source_type": "path", "file_path": "/a.pdf"},
        doc_id="123",
        report_id="1",
    )
    assert req.source.is_pdf and req.doc_id == "123" and req.report_id == "1"

    out = BOPdfPreviewOut(
        pages=2,
        tables=[{"index": 1, "header": ["Id", "Amt"], "rows": 3, "pages": [1, 2], "fragments": 2}],
        chosen={
            "index": 1,
            "header": ["Id", "Amt"],
            "column_types": {"Id": "text", "Amt": "number:2dp"},
            "sample_rows": [["A1", 1.5]],
            "suggested_key": "Id",
        },
        choose_error=None,
        prompts=[{"label": "As Of", "value": "2026-01-01"}],
        warnings=[],
    )
    assert out.pages == 2
    assert out.chosen is not None
    assert out.chosen.suggested_key == "Id"
    assert out.prompts[0].label == "As Of"


# ---------------------------------------------------------------------------
# Task 9: CompareService PDF loading, _load_bo_frames, key rules, Column Stats,
# _friendly_error
# ---------------------------------------------------------------------------

import base64
import api.services.compare_service as cs_mod
from fastapi import HTTPException
from api.schemas import ColumnStatsRequest
from api.services.adapter_service import _friendly_error
from etl_framework.bo_layout.model import PdfReadError
from etl_framework.reconciliation.column_stats import ColumnStatsComparer
from tests.helpers.pdf_fixtures import build_bo_pdf
from tests.unit.test_bo_compare_prompts import _svc

pytestmark = pytest.mark.usefixtures("pdf_env")


def _up(path, name=None):
    return {
        "source_type": "upload",
        "file_name": name or path.name,
        "file_content_b64": base64.b64encode(path.read_bytes()).decode(),
    }


H = ["Region", "As Of Date", "Index Value"]
ROWS = [["NORTH", "03-Jan-2025", "1,234.56"], ["NORTH", "04-Jan-2025", "1,240.10"]]


def test_same_pdf_both_sides_has_no_differences(real_pdf, bo_expected):
    e = bo_expected["sample1"]
    p = real_pdf("sample1.pdf")
    result = _svc().compare_bo(
        BOCompareRequest(source_a=_up(p, "qa.pdf"), source_b=_up(p, "prod.pdf"), key_columns=e["key"])
    )
    assert (result.value_mismatch_count, result.missing_in_source_count, result.missing_in_target_count) == (0, 0, 0)
    ps = result.mismatch_summary["pdf_sources"]
    assert ps["a"]["file_name"] == "qa.pdf" and ps["b"]["file_name"] == "prod.pdf" and ps["prompt_differences"] == []
    assert ps["alignment"]["precision_note"].endswith(e["precision_note_suffix"])


def test_seeded_change_gives_exactly_one_value_diff(tmp_path):
    a = build_bo_pdf(tmp_path / "a.pdf", tables=[{"header": H, "rows": ROWS}])
    b = build_bo_pdf(tmp_path / "b.pdf", tables=[{"header": H, "rows": [ROWS[0], ROWS[1][:2] + ["1,241.10"]]}])
    result = _svc().compare_bo(BOCompareRequest(source_a=_up(a), source_b=_up(b), key_columns=H[:2]))
    assert result.value_mismatch_count == 1 and result.mismatches[0].column_name == "Index Value"


def test_whitespace_change_is_a_difference_when_widths_equal(tmp_path):
    a = build_bo_pdf(tmp_path / "a.pdf", tables=[{"header": ["K", "G"], "rows": [["1", "A B"]]}])
    b = build_bo_pdf(tmp_path / "b.pdf", tables=[{"header": ["K", "G"], "rows": [["1", "AB"]]}])
    result = _svc().compare_bo(BOCompareRequest(source_a=_up(a), source_b=_up(b), key_columns=["K"]))
    assert result.value_mismatch_count == 1


def test_prompt_differences_reported(tmp_path):
    mk = lambda n, v: build_bo_pdf(tmp_path / n, prompts=[("1. Enter start date:", v)], tables=[{"header": H, "rows": ROWS}])
    result = _svc().compare_bo(
        BOCompareRequest(source_a=_up(mk("a.pdf", "01/03/2025")), source_b=_up(mk("b.pdf", "01/04/2025")), key_columns=H[:2])
    )
    assert result.mismatch_summary["pdf_sources"]["prompt_differences"] == [
        {"label": "Enter start date", "a": "01/03/2025", "b": "01/04/2025"}
    ]


def test_one_pdf_side_rejected_before_loading(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cs_mod, "read_pdf_report", lambda *a, **k: calls.append(1))
    p = build_bo_pdf(tmp_path / "a.pdf", tables=[{"header": H, "rows": ROWS}])
    req = BOCompareRequest(source_a={"source_type": "path", "file_path": "/a.xlsx"}, source_b=_up(p), key_columns=H[:2])
    with pytest.raises(HTTPException) as e:
        _svc().compare_bo(req)
    assert (e.value.status_code, e.value.detail, calls) == (422, "a PDF compare needs a PDF on both sides", [])


def test_pdf_compare_without_key_is_rejected_with_suggestion(real_pdf, bo_expected):
    p = real_pdf("sample3.pdf")
    with pytest.raises(HTTPException) as e:
        _svc().compare_bo(BOCompareRequest(source_a=_up(p), source_b=_up(p)))
    assert e.value.status_code == 422 and e.value.detail == {
        "message": "set key columns for a PDF compare",
        "suggested_key": bo_expected["sample3"]["key"][0],
    }


def test_duplicate_keys_rejected(tmp_path):
    p = build_bo_pdf(tmp_path / "f.pdf", tables=[{"header": ["Region", "V"], "rows": [["X", "1"], ["Sum:", "1"], ["Sum:", "1"]]}])
    with pytest.raises(HTTPException) as e:
        _svc().compare_bo(BOCompareRequest(source_a=_up(p), source_b=_up(p), key_columns=["Region"], label_a="QA"))
    assert e.value.detail == "key columns aren't unique in QA: 1 duplicate rows, for example Region=Sum:"


def test_path_pdf_goes_through_allow_list(monkeypatch, real_pdf):
    seen = []
    monkeypatch.setattr(cs_mod, "resolve_allowed_path", lambda p: seen.append(p) or real_pdf("sample1.pdf"))
    _svc()._load_bo_source(SourceConfig(source_type="path", file_path="/reports/X.PDF"), None, None)
    assert seen == ["/reports/X.PDF"]


def test_pdf_read_error_maps_to_422_with_table_list(tmp_path):
    two = build_bo_pdf(
        tmp_path / "two.pdf",
        tables=[{"header": ["Id", "V"], "rows": [["1", "a"]]}, {"header": ["Code", "Amt"], "rows": [["x", "9"]]}],
    )
    with pytest.raises(HTTPException) as e:
        _svc()._load_bo_source(SourceConfig(**_up(two)), None, None)
    assert e.value.status_code == 422 and e.value.detail["code"] == "several_tables" and len(e.value.detail["tables"]) == 2


def test_busy_maps_to_503(monkeypatch, real_pdf):
    def busy(*a, **k):
        raise PdfReadError("busy", "PDF reader busy")

    monkeypatch.setattr(cs_mod, "read_pdf_report", busy)
    with pytest.raises(HTTPException) as e:
        _svc()._load_bo_source(SourceConfig(**_up(real_pdf("sample3.pdf"))), None, None)
    assert (e.value.status_code, e.value.detail) == (503, "PDF reader busy")


def test_non_pdf_compare_unchanged(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text("x,y\n1,2\n3,4\n")
    b = tmp_path / "b.csv"
    b.write_text("x,y\n1,2\n3,5\n")
    up = lambda p: {
        "source_type": "upload",
        "file_name": p.name,
        "file_content_b64": base64.b64encode(p.read_bytes()).decode(),
        "format": "csv",
    }
    result = _svc().compare_bo(BOCompareRequest(source_a=up(a), source_b=up(b)))
    assert "pdf_sources" not in (result.mismatch_summary or {}) and result.value_mismatch_count == 1


def test_column_stats_reads_pdfs(real_pdf, bo_expected, monkeypatch):
    seen_cols: list[list[str]] = []
    orig_compare = ColumnStatsComparer.compare

    def _capture(self, df_a, df_b, *a, **k):
        seen_cols.append([str(c) for c in df_a.columns])
        return orig_compare(self, df_a, df_b, *a, **k)

    monkeypatch.setattr(ColumnStatsComparer, "compare", _capture)
    p = real_pdf("sample3.pdf")
    out = _svc().run_column_stats(ColumnStatsRequest(source_a=_up(p), source_b=_up(p)))
    assert out.has_diffs is False
    assert out.diffs == []
    assert out.diff_by_column == {}
    assert seen_cols and all(col in seen_cols[0] for col in bo_expected["sample3"]["header"])


def test_friendly_error_renders_pdf_http_exception():
    assert (
        _friendly_error(HTTPException(422, {"message": "set key columns for a PDF compare", "suggested_key": "Region"}))
        == "set key columns for a PDF compare (suggested key: Region)"
    )
    assert (
        _friendly_error(
            HTTPException(
                422,
                {
                    "message": "the PDF holds several data tables; choose one",
                    "code": "several_tables",
                    "tables": [{"header": ["Id", "V"]}, {"header": ["Code", "Amt"]}],
                },
            )
        )
        == "the PDF holds several data tables; choose one (tables: Id | V; Code | Amt)"
    )
    assert _friendly_error(HTTPException(503, "PDF reader busy")) == "PDF reader busy"


def test_corpus_twins_through_compare_bo(corpus):
    items = [
        (idx, item)
        for idx, item in enumerate(corpus)
        if item.get("layout") in ("plain", "paginated", "footer") and item.get("twin")
    ]
    assert len(items) >= 3, "corpus needs at least 3 twin pairs"
    svc = _svc()
    for idx, item in items:
        opts = item.get("options")
        src_a = _up(item["file"])
        src_b = _up(item["twin"])
        if opts:
            src_a["pdf"] = opts
            src_b["pdf"] = opts
        result = svc.compare_bo(
            BOCompareRequest(source_a=src_a, source_b=src_b, key_columns=item["key"])
        )
        assert (
            result.value_mismatch_count,
            result.missing_in_source_count,
            result.missing_in_target_count,
        ) == (0, 0, 0), f"manifest[{idx}]"


def test_live_and_unsupported_pdf_source_types_rejected():
    svc = _svc()
    with pytest.raises(HTTPException) as e_live:
        svc._load_bo_source(SourceConfig(source_type="live", config_id=1, format="pdf"), None, None)
    assert (e_live.value.status_code, e_live.value.detail) == (422, "PDF export from a live BO source isn't enabled")

    for src in (
        SourceConfig(source_type="run", run_id="r1", job_name="j1", format="pdf"),
        SourceConfig(source_type="api", config_id=1, api_endpoint_name="ep", format="pdf"),
    ):
        with pytest.raises(HTTPException) as e_other:
            svc._load_bo_source(src, None, None)
        assert (e_other.value.status_code, e_other.value.detail) == (422, "PDF is supported for path and upload sources")


def test_prompt_differences_case_insensitive_whitespace_and_missing(tmp_path):
    a = build_bo_pdf(
        tmp_path / "a.pdf",
        prompts=[("1. Enter Start Date:", "01  Jan"), ("2. Only A:", "yes")],
        tables=[{"header": H, "rows": ROWS}],
    )
    b = build_bo_pdf(
        tmp_path / "b.pdf",
        prompts=[("1. enter start date:", "01 Jan"), ("2. Only B:", "no")],
        tables=[{"header": H, "rows": ROWS}],
    )
    result = _svc().compare_bo(BOCompareRequest(source_a=_up(a), source_b=_up(b), key_columns=H[:2]))
    assert result.mismatch_summary["pdf_sources"]["prompt_differences"] == [
        {"label": "Only A", "a": "yes", "b": None},
        {"label": "Only B", "a": None, "b": "no"},
    ]
