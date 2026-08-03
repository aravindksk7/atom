from __future__ import annotations

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
