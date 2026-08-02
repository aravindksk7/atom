"""Static assets must be served gzip-compressed.

frontend/index.html is large and is fetched on every page load. Without
GZipMiddleware the whole thing goes over the wire verbatim.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client() -> TestClient:
    import api.main as main_module

    return TestClient(main_module.app, raise_server_exceptions=False)


def test_index_html_is_gzipped_when_client_accepts_it():
    with _client() as c:
        r = c.get("/index.html", headers={"accept-encoding": "gzip"})
        assert r.status_code == 200
        assert r.headers.get("content-encoding") == "gzip"


def test_index_html_is_not_gzipped_when_client_does_not_accept_it():
    with _client() as c:
        r = c.get("/index.html", headers={"accept-encoding": "identity"})
        assert r.status_code == 200
        assert "content-encoding" not in r.headers


def test_small_responses_are_not_gzipped():
    """minimum_size=1024: compressing tiny JSON costs more than it saves."""
    with _client() as c:
        r = c.get("/api/health", headers={"accept-encoding": "gzip"})
        assert r.headers.get("content-encoding") != "gzip"
