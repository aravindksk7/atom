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
