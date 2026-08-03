from __future__ import annotations

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
