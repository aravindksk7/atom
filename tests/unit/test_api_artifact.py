from __future__ import annotations

from pathlib import Path

import pytest

from api.services.upload_store import safe_filename, unique_path


def test_safe_filename_strips_traversal():
    assert safe_filename("../../../etc/passwd", "fallback") == "passwd"


def test_safe_filename_strips_windows_path():
    assert safe_filename(r"C:\Windows\System32\evil.dll", "fallback") == "evil.dll"


def test_safe_filename_falls_back_when_everything_stripped():
    assert safe_filename("...", "fallback.bin") == "fallback.bin"


def test_unique_path_suffixes_on_collision(tmp_path):
    (tmp_path / "page.json").write_bytes(b"{}")
    assert unique_path(tmp_path, "page.json").name == "page_2.json"


def test_unique_path_returns_name_when_free(tmp_path):
    assert unique_path(tmp_path, "page.json").name == "page.json"


@pytest.mark.parametrize(
    "raw",
    [
        r"\\server\share\evil.txt",  # UNC path
        r"C:foo\bar.txt",  # drive-relative path
        "file.txt:evil",  # NTFS alternate data stream
        "NUL",
        "nul.txt",
        "CON",
        "con.txt",
        "PRN",
        "AUX",
        "COM1",
        "com1.txt",
        "LPT1",
        "lpt9.log",
    ],
)
def test_safe_filename_result_cannot_escape_destination_directory(tmp_path, raw):
    candidate = tmp_path / safe_filename(raw, "fallback.dat")
    assert candidate.resolve().parent == tmp_path.resolve()


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("NUL", "_NUL"),
        ("nul.txt", "_nul.txt"),
        ("CON", "_CON"),
        ("con.txt", "_con.txt"),
        ("PRN", "_PRN"),
        ("AUX", "_AUX"),
        ("COM1", "_COM1"),
        ("com1.txt", "_com1.txt"),
        ("LPT1", "_LPT1"),
        ("lpt9.log", "_lpt9.log"),
    ],
)
def test_safe_filename_defuses_windows_reserved_device_names(raw, expected):
    assert safe_filename(raw, "fallback.dat") == expected


from unittest.mock import MagicMock

from api.services.api_artifact import artifact_filename

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _resp(content_type="application/json", disposition=None):
    resp = MagicMock()
    headers = {"Content-Type": content_type}
    if disposition is not None:
        headers["Content-Disposition"] = disposition
    resp.headers = headers
    return resp


def test_filename_from_content_type_json():
    assert artifact_filename(_resp(), "orders", 1) == "orders_p1.json"


def test_filename_from_content_type_csv():
    assert artifact_filename(_resp("text/csv"), "orders", 2) == "orders_p2.csv"


def test_filename_from_content_type_xlsx():
    assert artifact_filename(_resp(_XLSX), "orders", 1) == "orders_p1.xlsx"


def test_filename_unknown_content_type_is_bin():
    assert artifact_filename(_resp("application/octet-stream"), "orders", 1) == "orders_p1.bin"


def test_filename_ignores_charset_parameter():
    assert artifact_filename(_resp("application/json; charset=utf-8"), "orders", 1) == "orders_p1.json"


def test_content_disposition_filename_wins():
    resp = _resp(_XLSX, 'attachment; filename="Q3 report.xlsx"')
    assert artifact_filename(resp, "orders", 1) == "Q3_report.xlsx"


def test_content_disposition_traversal_is_neutralised():
    resp = _resp("application/json", 'attachment; filename="../../../etc/passwd"')
    assert artifact_filename(resp, "orders", 1) == "passwd"


def test_content_disposition_absolute_windows_path_is_neutralised():
    resp = _resp("application/json", r'attachment; filename="C:\Windows\evil.dll"')
    assert artifact_filename(resp, "orders", 1) == "evil.dll"


def test_endpoint_name_with_separators_is_neutralised():
    assert artifact_filename(_resp(), "../../orders", 1) == "orders_p1.json"


# RFC 6266 section 4.3 precedence: filename* (RFC 5987, percent-encoded, with
# an explicit charset) wins over the plain filename when present and
# decodable; a malformed filename* must fall back to plain rather than raise.
from api.services.api_artifact import _disposition_filename


def test_disposition_unquoted_filename_without_quotes():
    resp = _resp(disposition="attachment; filename=report.csv")
    assert _disposition_filename(resp) == "report.csv"


def test_disposition_quoted_filename_keeps_embedded_semicolon():
    resp = _resp(disposition='attachment; filename="a;b.csv"')
    assert _disposition_filename(resp) == "a;b.csv"


def test_disposition_extended_value_is_percent_decoded_then_sanitised():
    resp = _resp(disposition="attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf")
    assert _disposition_filename(resp) == "résumé.pdf"
    assert artifact_filename(resp, "orders", 1) == safe_filename("résumé.pdf", "page_p1.bin")
    assert artifact_filename(resp, "orders", 1) == "r_sum_.pdf"


@pytest.mark.parametrize(
    "disposition",
    [
        'attachment; filename="plain.csv"; filename*=UTF-8\'\'fancy%20name.csv',
        'attachment; filename*=UTF-8\'\'fancy%20name.csv; filename="plain.csv"',
    ],
)
def test_disposition_extended_value_wins_regardless_of_header_order(disposition):
    resp = _resp(disposition=disposition)
    assert _disposition_filename(resp) == "fancy name.csv"


def test_disposition_malformed_extended_value_falls_back_to_plain_filename():
    resp = _resp(disposition="attachment; filename*=BOGUS-CHARSET''%FF%FE; filename=\"plain.csv\"")
    assert _disposition_filename(resp) == "plain.csv"


def test_disposition_inline_has_no_filename():
    resp = _resp(disposition="inline")
    assert _disposition_filename(resp) is None


# Regression coverage for a bug in an earlier regex-based implementation: an
# unanchored .search() over the whole header let a vendor param that merely
# *ends* in "filename" hijack the match, and quote state wasn't tracked, so
# a "filename*=" embedded inside a quoted value was mistaken for a real
# parameter. The tokenizer-based parser fixes both by construction.
def test_disposition_vendor_param_ending_in_filename_does_not_hijack():
    resp = _resp(disposition='attachment; original-filename="wrong.txt"; filename="right.pdf"')
    assert _disposition_filename(resp) == "right.pdf"


def test_disposition_filename_star_inside_quoted_value_is_literal_data():
    resp = _resp(disposition="attachment; filename=\"x; filename*=UTF-8''y\"")
    assert _disposition_filename(resp) == "x; filename*=UTF-8''y"


def test_disposition_param_name_is_case_insensitive():
    resp = _resp(disposition="attachment; FILENAME*=UTF-8''caps.csv")
    assert _disposition_filename(resp) == "caps.csv"
    resp2 = _resp(disposition='attachment; Filename="Mixed.csv"')
    assert _disposition_filename(resp2) == "Mixed.csv"


def test_disposition_tolerates_whitespace_around_equals():
    resp = _resp(disposition='attachment; filename = "x.csv"')
    assert _disposition_filename(resp) == "x.csv"


def test_disposition_duplicate_filename_first_wins():
    resp = _resp(disposition='attachment; filename="first.csv"; filename="second.csv"')
    assert _disposition_filename(resp) == "first.csv"


from datetime import datetime, timezone

from api.services.api_artifact import adhoc_artifact_dir, run_artifact_dir
from api.services.upload_store import UPLOAD_ROOT

_WHEN = datetime(2026, 8, 3, 21, 14, 8, tzinfo=timezone.utc)


def test_adhoc_dir_is_direct_child_of_upload_root():
    path = adhoc_artifact_dir(3, "orders", now=_WHEN)
    assert path.parent == UPLOAD_ROOT
    assert path.name == "adhoc_3_orders_20260803T211408Z"


def test_adhoc_dir_neutralises_endpoint_name():
    path = adhoc_artifact_dir(3, "../../evil", now=_WHEN)
    assert path.parent == UPLOAD_ROOT
    assert ".." not in path.name


def test_run_dir_is_direct_child_of_upload_root():
    path = run_artifact_dir("run-abc")
    assert path.parent == UPLOAD_ROOT
    assert path.name == "run-abc"


from api.services import api_artifact
from api.services.api_artifact import build_api_response_sink


def test_sink_writes_the_bytes(tmp_path):
    sink = build_api_response_sink(tmp_path, "orders")
    sink(b'{"a":1}', 1, _resp())
    assert (tmp_path / "orders_p1.json").read_bytes() == b'{"a":1}'


def test_sink_creates_the_directory(tmp_path):
    dest = tmp_path / "nested" / "dir"
    sink = build_api_response_sink(dest, "orders")
    sink(b"{}", 1, _resp())
    assert (dest / "orders_p1.json").exists()


def test_sink_suffixes_on_collision(tmp_path):
    sink = build_api_response_sink(tmp_path, "orders")
    sink(b"a", 1, _resp())
    sink(b"b", 1, _resp())
    assert (tmp_path / "orders_p1.json").read_bytes() == b"a"
    assert (tmp_path / "orders_p1_2.json").read_bytes() == b"b"


def test_sink_skips_over_cap_payload(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(api_artifact, "RUN_DATA_ARTIFACT_MAX_BYTES", 4)
    sink = build_api_response_sink(tmp_path, "orders")
    with caplog.at_level("WARNING"):
        sink(b"way too long", 1, _resp())
    assert list(tmp_path.iterdir()) == []
    assert "cap" in caplog.text.lower()


def test_sink_over_cap_does_not_create_the_directory(tmp_path, monkeypatch):
    """Cap check must run before mkdir: an over-cap pull leaves nothing behind,
    not an empty directory. tmp_path itself already exists, so this must use a
    nested path or the assertion is blind to the ordering."""
    monkeypatch.setattr(api_artifact, "RUN_DATA_ARTIFACT_MAX_BYTES", 4)
    dest = tmp_path / "nested" / "dir"
    sink = build_api_response_sink(dest, "orders")
    sink(b"way too long", 1, _resp())
    assert not dest.exists()


def test_sink_swallows_write_errors(tmp_path, monkeypatch):
    sink = build_api_response_sink(tmp_path, "orders")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", boom)
    sink(b"{}", 1, _resp())  # must not raise


def test_sink_swallows_directory_errors(tmp_path, monkeypatch):
    sink = build_api_response_sink(tmp_path / "x", "orders")

    def boom(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "mkdir", boom)
    sink(b"{}", 1, _resp())  # must not raise


import os
import time

from api.services.upload_store import cleanup_expired_uploads


def test_adhoc_directory_is_swept_by_existing_retention(tmp_path):
    adhoc = tmp_path / "adhoc_3_orders_20260803T211408Z"
    adhoc.mkdir()
    (adhoc / "orders_p1.json").write_bytes(b"{}")
    old = time.time() - (40 * 86400)
    os.utime(adhoc, (old, old))
    removed = cleanup_expired_uploads(30, root=tmp_path)
    assert removed == 1
    assert not adhoc.exists()


def test_recent_adhoc_directory_survives_retention(tmp_path):
    adhoc = tmp_path / "adhoc_3_orders_20260803T211408Z"
    adhoc.mkdir()
    assert cleanup_expired_uploads(30, root=tmp_path) == 0
    assert adhoc.exists()
