from __future__ import annotations
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl_framework.config.models import ApiEndpointEntry
from etl_framework.exceptions import APIRequestError
from etl_framework.rest_api.client import APIEndpointClient


def _entry(**overrides) -> ApiEndpointEntry:
    base = {"base_url": "https://api.example.com/v1/orders"}
    base.update(overrides)
    return ApiEndpointEntry(**base)


def _fake_response(status_code=200, json_data=None, text="", url="https://api.example.com/v1/orders"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.url = url
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


def test_fetch_dataframe_parses_json_with_root_path():
    entry = _entry(json_root_path="data.items")
    client = APIEndpointClient(entry)
    payload = {"data": {"items": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}}
    with patch.object(client._session, "request", return_value=_fake_response(json_data=payload)):
        df = client.fetch_dataframe()
    assert list(df["id"]) == [1, 2]


def test_fetch_dataframe_parses_json_without_root_path():
    entry = _entry(json_root_path="")
    client = APIEndpointClient(entry)
    payload = [{"id": 1}, {"id": 2}]
    with patch.object(client._session, "request", return_value=_fake_response(json_data=payload)):
        df = client.fetch_dataframe()
    assert len(df) == 2


def test_fetch_dataframe_raises_on_error_status():
    entry = _entry()
    client = APIEndpointClient(entry)
    with patch.object(client._session, "request", return_value=_fake_response(status_code=500, text="boom")):
        with pytest.raises(APIRequestError):
            client.fetch_dataframe()


def test_fetch_dataframe_bearer_auth_header():
    entry = _entry(auth_type="bearer", bearer_token="tok123")
    client = APIEndpointClient(entry)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _fake_response(json_data=[{"id": 1}])

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured["headers"]["Authorization"] == "Bearer tok123"


def test_fetch_dataframe_basic_auth():
    entry = _entry(auth_type="basic", basic_username="user", basic_password="pw")
    client = APIEndpointClient(entry)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _fake_response(json_data=[{"id": 1}])

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured["auth"] == ("user", "pw")


def test_fetch_dataframe_api_key_header():
    entry = _entry(auth_type="api_key", api_key_header="X-API-Key", api_key="k1")
    client = APIEndpointClient(entry)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _fake_response(json_data=[{"id": 1}])

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured["headers"]["X-API-Key"] == "k1"


def test_fetch_dataframe_no_auth_sends_no_auth_tuple():
    entry = _entry(auth_type="none")
    client = APIEndpointClient(entry)
    captured = {}

    def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return _fake_response(json_data=[{"id": 1}])

    with patch.object(client._session, "request", side_effect=fake_request):
        client.fetch_dataframe()
    assert captured["auth"] is None
    assert "Authorization" not in captured["headers"]
