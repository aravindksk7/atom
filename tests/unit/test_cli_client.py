"""Tests for etl_framework.cli.client.AtomClient."""
from __future__ import annotations

import pytest
import requests

from etl_framework.cli.client import (
    AtomAPIError,
    AtomAuthError,
    AtomClient,
    AtomConnectionError,
    AtomNotFoundError,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = text or (content.decode() if content else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.fixture
def capture(monkeypatch):
    calls = []

    def install(response):
        def fake_request(self, method, url, **kwargs):
            if isinstance(response, Exception):
                raise response
            calls.append({"method": method, "url": url, **kwargs})
            return response

        monkeypatch.setattr(requests.Session, "request", fake_request)
        return calls

    return install


def test_get_json_sends_bearer_token_and_parses(capture):
    calls = capture(FakeResponse(json_data={"ok": True}))
    client = AtomClient("http://atom.test/", token="secret-token")
    assert client.get_json("/api/selections") == {"ok": True}
    assert calls[0]["url"] == "http://atom.test/api/selections"
    assert client._session.headers["Authorization"] == "Bearer secret-token"


def test_post_json_sends_payload(capture):
    calls = capture(FakeResponse(status_code=202, json_data={"run_id": "r1"}))
    client = AtomClient("http://atom.test")
    out = client.post_json("/api/selections/3/launch", {"source_env": "dev"})
    assert out == {"run_id": "r1"}
    assert calls[0]["method"] == "POST"
    assert calls[0]["json"] == {"source_env": "dev"}


def test_401_raises_auth_error(capture):
    capture(FakeResponse(status_code=401, text="unauthorized"))
    client = AtomClient("http://atom.test")
    with pytest.raises(AtomAuthError):
        client.get_json("/api/runs")


def test_404_raises_not_found(capture):
    capture(FakeResponse(status_code=404, json_data={"detail": "Run not found"}))
    client = AtomClient("http://atom.test")
    with pytest.raises(AtomNotFoundError, match="Run not found"):
        client.get_json("/api/runs/nope/status")


def test_500_raises_api_error(capture):
    capture(FakeResponse(status_code=500, text="boom"))
    client = AtomClient("http://atom.test")
    with pytest.raises(AtomAPIError):
        client.get_json("/api/runs")


def test_connection_error_raises_atom_connection_error_after_retries(capture):
    capture(requests.ConnectionError("refused"))
    client = AtomClient("http://atom.test")
    with pytest.raises(AtomConnectionError):
        client.get_json("/api/runs")


def test_get_bytes_returns_raw_content(capture):
    capture(FakeResponse(content=b"<xml/>"))
    client = AtomClient("http://atom.test")
    assert client.get_bytes("/api/runs/r1/junit") == b"<xml/>"
