"""Unit and performance tests for BO PDF child-process reader, preview, and selftest."""
from __future__ import annotations

import hashlib
import os
import subprocess
import time

import pytest

import etl_framework.bo_layout.reader as reader_mod
from etl_framework.bo_layout.model import PdfReadError, PdfReadOptions
from etl_framework.bo_layout.reader import ERROR_COUNTS, preview_pdf_report, read_pdf_report, selftest
from tests.helpers.pdf_fixtures import build_bo_pdf


@pytest.fixture
def spawns(monkeypatch):
    calls, real = [], subprocess.run

    def counting(*a, **k):
        calls.append(k.get("env"))
        return real(*a, **k)

    monkeypatch.setattr(reader_mod.subprocess, "run", counting)
    return calls


def test_read_sample1_end_to_end(real_pdf, bo_expected):
    e, prod = bo_expected["sample1"], bo_expected["prod"]
    raw = real_pdf("sample1.pdf").read_bytes()
    df, info = read_pdf_report(raw, PdfReadOptions(), file_name="s1.pdf")
    assert df.values.tolist() == e["typed_rows"]
    assert (info.file_name, info.sha256, info.bytes, info.creator) == (
        "s1.pdf",
        hashlib.sha256(raw).hexdigest(),
        len(raw),
        prod["creator"],
    )
    assert info.table["column_types"] == e["column_types"]
    assert (info.filled_cells, info.dropped_empty_rows, len(info.prompts)) == (
        e["filled_cells"],
        e["dropped_empty_rows"],
        len(e["prompt_labels"]),
    )
    assert set(info.table["column_widths"]) == set(df.columns)


def test_preview_sample2_suggests_key(real_pdf, bo_expected):
    e = bo_expected["sample2"]
    p = preview_pdf_report(real_pdf("sample2.pdf").read_bytes(), PdfReadOptions())
    assert p["pages"] == 1 and p["chosen"]["suggested_key"] == e["key"][0] and len(p["chosen"]["sample_rows"]) == 10
    assert p["choose_error"] is None and len(p["tables"]) == 1


def test_preview_lists_tables_when_choice_needed(tmp_path):
    two = build_bo_pdf(
        tmp_path / "two.pdf",
        tables=[
            {"header": ["Id", "V"], "rows": [["1", "a"]]},
            {"header": ["Code", "Amt"], "rows": [["x", "9"]]},
        ],
    )
    p = preview_pdf_report(two.read_bytes(), PdfReadOptions())
    assert (
        p["chosen"] is None
        and p["choose_error"] == "the PDF holds several data tables; choose one"
        and len(p["tables"]) == 2
    )


def test_timeout_raises(tmp_path, monkeypatch):
    pdf = build_bo_pdf(tmp_path / "a.pdf", tables=[{"header": ["A"], "rows": [["1"]]}])

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=1)

    monkeypatch.setattr(reader_mod.subprocess, "run", boom)
    with pytest.raises(PdfReadError) as e:
        read_pdf_report(pdf.read_bytes(), PdfReadOptions(), timeout_s=1)
    assert (e.value.code, e.value.message) == ("timeout", "the PDF could not be read in 1 s")


def test_too_large_rejected_before_spawning(monkeypatch, spawns):
    monkeypatch.setenv("ATOM_PDF_MAX_BYTES", "10")
    with pytest.raises(PdfReadError) as e:
        read_pdf_report(b"x" * 11, PdfReadOptions())
    assert e.value.code == "too_large" and spawns == []


def test_garbage_bytes_give_a_clean_error_and_are_counted():
    before = ERROR_COUNTS["invalid_pdf"]
    with pytest.raises(PdfReadError) as e:
        read_pdf_report(b"not a pdf", PdfReadOptions())
    assert e.value.code == "invalid_pdf" and ERROR_COUNTS["invalid_pdf"] == before + 1


def test_temp_files_removed(tmp_path, monkeypatch):
    work = tmp_path / "tmp"
    work.mkdir()
    monkeypatch.setattr(reader_mod.tempfile, "tempdir", str(work))
    pdf = build_bo_pdf(tmp_path / "a.pdf", tables=[{"header": ["A"], "rows": [["1"]]}])
    read_pdf_report(pdf.read_bytes(), PdfReadOptions())
    assert list(work.iterdir()) == []


def test_cache_hit_skips_worker_and_keeps_caller_file_name(tmp_path, spawns):
    raw = build_bo_pdf(tmp_path / "a.pdf", tables=[{"header": ["A"], "rows": [["1"]]}]).read_bytes()
    read_pdf_report(raw, PdfReadOptions(), file_name="one.pdf")
    _, info = read_pdf_report(raw, PdfReadOptions(), file_name="two.pdf")
    assert len(spawns) == 1 and info.file_name == "two.pdf"
    read_pdf_report(raw, PdfReadOptions(fill_down=False))
    assert len(spawns) == 2


def test_pythonpath_is_prepended(tmp_path, monkeypatch, spawns):
    monkeypatch.setenv("PYTHONPATH", "EXISTING")
    raw = build_bo_pdf(tmp_path / "a.pdf", tables=[{"header": ["A"], "rows": [["1"]]}]).read_bytes()
    read_pdf_report(raw, PdfReadOptions())
    parts = spawns[0]["PYTHONPATH"].split(os.pathsep)
    assert parts[-1] == "EXISTING" and os.path.isdir(os.path.join(parts[0], "etl_framework"))


def test_worker_crash_uses_last_stderr_line(tmp_path, monkeypatch):
    raw = build_bo_pdf(tmp_path / "a.pdf", tables=[{"header": ["A"], "rows": [["1"]]}]).read_bytes()

    def crashed(*a, **k):
        return subprocess.CompletedProcess(
            args=a[0],
            returncode=1,
            stdout="",
            stderr="traceback line 1\nsegfault in parser\n\n",
        )

    monkeypatch.setattr(reader_mod.subprocess, "run", crashed)
    with pytest.raises(PdfReadError) as e:
        read_pdf_report(raw, PdfReadOptions())
    assert (e.value.code, e.value.message) == ("worker_crash", "segfault in parser")


def test_selftest_ok():
    r = selftest()
    assert r["ok"] is True and r["error"] is None


def test_selftest_is_memoised(monkeypatch):
    reader_mod._SELFTEST_CACHE = None
    calls = []
    real = subprocess.run

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(reader_mod.subprocess, "run", counting)
    r1 = selftest()
    r2 = selftest()
    assert r1["ok"] is True and r2 == r1 and len(calls) == 1


@pytest.mark.slow
@pytest.mark.parametrize("rows_per_page", [None, 50])
def test_twenty_thousand_rows_within_run_timeout(tmp_path, rows_per_page):
    pdf = build_bo_pdf(
        tmp_path / "big.pdf",
        tables=[{"header": ["Id", "Amount"], "rows": [[f"R{i + 1}", f"{i}.25"] for i in range(20_000)]}],
        rows_per_page=rows_per_page,
    )
    t0 = time.monotonic()
    df, _ = read_pdf_report(pdf.read_bytes(), PdfReadOptions(), timeout_s=120)
    assert len(df) == 20_000
    print("seconds", rows_per_page, round(time.monotonic() - t0, 1))
