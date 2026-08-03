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
