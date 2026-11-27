import json
import pathlib

from etl_framework.bo_layout.model import ColumnInfo, PdfPrompt, PdfReadError, PdfReadOptions, PdfSourceInfo


def test_pdf_source_info_round_trips_through_json():
    info = PdfSourceInfo(
        file_name="a.pdf",
        sha256="x",
        bytes=10,
        pages=1,
        producer="WriterX 1.0",
        creator="ServerY",
        table={
            "header": ["A"],
            "pages": [1],
            "rows": 1,
            "stitched_fragments": 1,
            "column_types": {"A": "number:2dp"},
            "displayed_decimals": {"A": 2},
            "column_widths": {"A": 80.0},
        },
        filled_cells=1,
        dropped_empty_rows=1,
        dropped_repeated_headers=0,
        ignored_tables=[],
        prompts=[PdfPrompt("Enter start date", "01/03/2025 12:00:00 AM")],
        warnings=[],
        column_info={"A": ColumnInfo("number", displayed_decimals=2, unit="%")},
    )
    assert PdfSourceInfo.from_dict(json.loads(json.dumps(info.to_dict()))) == info


def test_read_options_defaults_and_round_trip():
    assert PdfReadOptions() == PdfReadOptions(None, "en", None, True, {})
    o = PdfReadOptions(2, "eu", "dmy", False, {"snap_tolerance": 3})
    assert PdfReadOptions.from_dict(json.loads(json.dumps(o.to_dict()))) == o


def test_pdf_read_error_carries_code_message_tables():
    e = PdfReadError("encrypted", "the PDF is encrypted")
    assert (e.code, e.message, str(e), e.tables) == ("encrypted", "the PDF is encrypted", "the PDF is encrypted", [])


def test_real_pdf_fails_when_samples_required(pytester, monkeypatch):
    pytester.makeconftest((pathlib.Path(__file__).parents[1] / "conftest.py").read_text())
    pytester.makepyfile("def test_x(real_pdf): real_pdf('missing.pdf')")
    monkeypatch.setenv("ATOM_REQUIRE_REAL_SAMPLES", "1")
    monkeypatch.delenv("ATOM_BO_SAMPLES_DIR", raising=False)
    pytester.runpytest().assert_outcomes(failed=1)


def test_private_samples_are_not_tracked():  # D-1
    import subprocess

    root = pathlib.Path(__file__).resolve().parents[3]
    out = subprocess.run(
        ["git", "ls-files", "tests/fixtures/bo_layout", "tests/e2e/fixtures/data"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    # Matches .gitignore: nothing under tests/fixtures/bo_layout/ (except .gitkeep) and no .pdf under e2e data.
    leaked = [
        f
        for f in out
        if not f.endswith(".gitkeep")
        and (f.startswith("tests/fixtures/bo_layout/") or f.lower().endswith(".pdf"))
    ]
    assert leaked == [], f"private BO samples are tracked in git: {leaked}"


def test_sample_aliases_resolve(real_pdf, bo_expected):
    assert real_pdf("sample3.pdf").name == bo_expected["files"]["sample3.pdf"]
    assert real_pdf("sample3.xlsx").suffix == ".xlsx"


def test_real_sample_failures_are_redacted(pytester, monkeypatch):
    pytester.makeconftest((pathlib.Path(__file__).parents[1] / "conftest.py").read_text())
    pytester.makepyfile("def test_x(bo_expected):\n    assert 'SECRET-VALUE' == 'other'")
    (pytester.path / "expected.json").write_text('{"files": {}}')
    monkeypatch.setenv("ATOM_BO_SAMPLES_DIR", str(pytester.path))
    r = pytester.runpytest()
    r.assert_outcomes(failed=1)
    assert "SECRET-VALUE" not in r.stdout.str() and "local only" in r.stdout.str()


def test_real_pdf_skips_and_reports_count_when_not_required(pytester, monkeypatch):
    pytester.makeconftest((pathlib.Path(__file__).parents[1] / "conftest.py").read_text())
    pytester.makepyfile("def test_x(real_pdf): real_pdf('missing.pdf')")
    monkeypatch.delenv("ATOM_REQUIRE_REAL_SAMPLES", raising=False)
    monkeypatch.delenv("ATOM_BO_SAMPLES_DIR", raising=False)
    r = pytester.runpytest()
    r.assert_outcomes(skipped=1)
    r.stdout.fnmatch_lines(["*1 real-sample tests skipped*"])
