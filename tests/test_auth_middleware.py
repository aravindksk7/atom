import time
import pytest
from unittest.mock import MagicMock
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.middleware.auth import BearerTokenMiddleware, evict_token_cache, _cache


def make_app():
    async def homepage(request):
        return JSONResponse({"ok": True})
    app = Starlette(routes=[Route("/api/jobs", homepage)])
    app.add_middleware(BearerTokenMiddleware)
    return app


def test_missing_auth_header_returns_401():
    client = TestClient(make_app(), raise_server_exceptions=False)
    resp = client.get("/api/jobs")
    assert resp.status_code == 401


def test_badge_svg_is_exempt():
    client = TestClient(make_app(), raise_server_exceptions=False)
    # middleware exempts badge SVG — no auth needed, route doesn't exist so 404/405 not 401
    resp = client.get("/api/runs/abc-123/badge.svg")
    assert resp.status_code != 401


def test_post_api_tokens_is_exempt():
    client = TestClient(make_app(), raise_server_exceptions=False)
    resp = client.post("/api/tokens", json={"name": "x"})
    assert resp.status_code != 401


def test_get_api_tokens_is_not_exempt():
    client = TestClient(make_app(), raise_server_exceptions=False)
    resp = client.get("/api/tokens")
    assert resp.status_code == 401


def test_evict_removes_entry_from_cache():
    _cache["somehash"] = (MagicMock(), time.monotonic())
    evict_token_cache("somehash")
    assert "somehash" not in _cache


def test_evict_nonexistent_key_is_safe():
    evict_token_cache("nonexistent")  # should not raise
