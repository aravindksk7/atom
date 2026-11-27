"""Unit tests for the BO PDF parse cache."""
from __future__ import annotations

from etl_framework.bo_layout import cache
from etl_framework.bo_layout.model import PdfReadOptions


def test_put_get_and_prune(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("ATOM_PDF_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("ATOM_PDF_CACHE_MAX_ENTRIES", "2")
    keys = [cache.cache_key(f"sha{i}", "read", PdfReadOptions()) for i in range(3)]
    for i, k in enumerate(keys):
        cache.cache_put(k, {"ok": True, "n": i})
    assert cache.cache_get(keys[0]) is None and cache.cache_get(keys[2]) == {"ok": True, "n": 2}


def test_key_depends_on_options_mode_and_version(monkeypatch):
    a = cache.cache_key("s", "read", PdfReadOptions())
    assert (
        a
        != cache.cache_key("s", "read", PdfReadOptions(fill_down=False))
        != cache.cache_key("s", "preview", PdfReadOptions())
    )
    monkeypatch.setattr(cache, "PARSER_VERSION", "999")
    assert cache.cache_key("s", "read", PdfReadOptions()) != a


def test_corrupt_entry_is_a_miss(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("ATOM_PDF_CACHE_DIR", str(cache_dir))
    k = cache.cache_key("s", "read", PdfReadOptions())
    cache.cache_put(k, {"ok": True})
    next(cache_dir.iterdir()).write_text("{not json")
    assert cache.cache_get(k) is None


def test_zero_entries_disables(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("ATOM_PDF_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("ATOM_PDF_CACHE_MAX_ENTRIES", "0")
    k = cache.cache_key("s", "read", PdfReadOptions())
    cache.cache_put(k, {"ok": True})
    assert cache.cache_get(k) is None and list(cache_dir.iterdir()) == []


def test_only_ok_true_payloads_are_cached(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("ATOM_PDF_CACHE_DIR", str(cache_dir))
    k = cache.cache_key("s", "read", PdfReadOptions())
    cache.cache_put(k, {"ok": False, "code": "invalid_pdf", "message": "bad"})
    assert cache.cache_get(k) is None and list(cache_dir.iterdir()) == []
