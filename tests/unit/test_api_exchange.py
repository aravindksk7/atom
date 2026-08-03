from __future__ import annotations

from etl_framework.config.models import ApiEndpointEntry
from api.services.api_exchange import redact_headers


def _entry(**overrides) -> ApiEndpointEntry:
    base = {"base_url": "https://api.example.com/v1/orders"}
    base.update(overrides)
    return ApiEndpointEntry(**base)


def test_authorization_is_redacted():
    out = redact_headers({"Authorization": "Bearer abcdefghijklmnop"}, _entry())
    assert out["Authorization"] == "<23 chars, ...mnop>"


def test_redaction_is_case_insensitive():
    out = redact_headers({"authorization": "Bearer abcdefghijklmnop"}, _entry())
    assert out["authorization"].startswith("<23 chars")


def test_cookie_and_logon_token_are_redacted():
    out = redact_headers(
        {"Cookie": "session=aaaabbbb", "X-SAP-LogonToken": "tokenvalue1234"}, _entry()
    )
    assert out["Cookie"].startswith("<")
    assert out["X-SAP-LogonToken"].startswith("<")


def test_configured_api_key_header_is_redacted():
    entry = _entry(auth_type="api_key", api_key_header="X-Custom-Token", api_key="s3cret-value")
    out = redact_headers({"X-Custom-Token": "s3cret-value"}, entry)
    assert out["X-Custom-Token"] == "<12 chars, ...alue>"


def test_ordinary_headers_pass_through():
    out = redact_headers({"Content-Type": "application/json"}, _entry())
    assert out["Content-Type"] == "application/json"


def test_short_secret_does_not_leak_the_whole_value():
    out = redact_headers({"Authorization": "abc"}, _entry())
    assert "abc" not in out["Authorization"]


from api.services.api_exchange import BODY_LIMIT, render_body


def test_text_body_passes_through():
    body, truncated, binary = render_body(b'{"a":1}', "application/json")
    assert body == '{"a":1}'
    assert truncated is False
    assert binary is False


def test_long_body_is_truncated():
    body, truncated, binary = render_body(b"x" * (BODY_LIMIT + 100), "text/plain")
    assert len(body) == BODY_LIMIT
    assert truncated is True


def test_binary_body_is_hex_not_decoded():
    body, truncated, binary = render_body(b"\x89PNG\r\n\x1a\n", "image/png")
    assert binary is True
    assert body.startswith("89504e47")


def test_undecodable_text_body_falls_back_to_replacement():
    body, truncated, binary = render_body(b"\xff\xfe bad", "text/plain")
    assert binary is False
    assert "�" in body


def test_empty_body_is_empty_string_not_none():
    body, truncated, binary = render_body(b"", "application/json")
    assert body == ""


from unittest.mock import MagicMock

from api.services.api_exchange import capture_exchange


def _response(status=200, body=b'{"a":1}', content_type="application/json",
              req_body=b'{"filter":"recent"}', req_headers=None):
    request = MagicMock()
    request.method = "POST"
    request.url = "https://api.example.com/v1/orders/search?region=us"
    request.headers = req_headers if req_headers is not None else {"Content-Type": "application/json"}
    request.body = req_body

    resp = MagicMock()
    resp.request = request
    resp.status_code = status
    resp.content = body
    resp.headers = {"Content-Type": content_type}
    resp.history = []
    elapsed = MagicMock()
    elapsed.total_seconds.return_value = 0.412
    resp.elapsed = elapsed
    return resp


def test_capture_records_request_and_response():
    sink, seen = capture_exchange(_entry())
    sink(b'{"a":1}', 1, _response())
    assert len(seen) == 1
    exchange = seen[0]
    assert exchange["request"]["method"] == "POST"
    assert exchange["request"]["url"].endswith("?region=us")
    assert exchange["request"]["body"] == '{"filter":"recent"}'
    assert exchange["response"]["status"] == 200
    assert exchange["response"]["elapsed_ms"] == 412
    assert exchange["response"]["bytes"] == 7


def test_capture_records_a_missing_request_body_as_null():
    """The dropped-body case: this is what makes it visible."""
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, _response(req_body=None))
    assert seen[0]["request"]["body"] is None


def test_capture_redacts_request_headers():
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, _response(req_headers={"Authorization": "Bearer abcdefghijkl"}))
    assert seen[0]["request"]["headers"]["Authorization"].startswith("<")


def test_capture_counts_redirects():
    resp = _response()
    resp.history = [MagicMock(), MagicMock()]
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, resp)
    assert seen[0]["response"]["redirects"] == 2


def test_capture_never_raises_on_a_malformed_response():
    sink, seen = capture_exchange(_entry())
    sink(b"{}", 1, object())  # no .request, no .headers
    assert seen == []
