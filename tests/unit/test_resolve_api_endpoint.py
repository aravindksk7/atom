import pytest
from pydantic import ValidationError

from etl_framework.config.models import ApiEndpointEntry, resolve_api_endpoint


def test_api_endpoint_entry_defaults():
    entry = ApiEndpointEntry(base_url="https://api.example.com/orders")
    assert entry.method == "GET"
    assert entry.auth_type == "none"
    assert entry.response_format == "json"
    assert entry.pagination_type == "none"
    assert entry.pagination_max_pages == 50
    assert entry.timeout == 30
    assert entry.verify_ssl is True


def test_api_endpoint_entry_requires_url_scheme():
    with pytest.raises(ValidationError):
        ApiEndpointEntry(base_url="api.example.com/orders")


def test_api_endpoint_entry_rejects_non_positive_timeout():
    with pytest.raises(ValidationError):
        ApiEndpointEntry(base_url="https://api.example.com", timeout=0)


def test_api_endpoint_entry_rejects_max_pages_out_of_range():
    with pytest.raises(ValidationError):
        ApiEndpointEntry(base_url="https://api.example.com", pagination_max_pages=0)


def test_resolve_api_endpoint_returns_entry_with_name():
    config_json = {
        "api_endpoints": {
            "orders": {"base_url": "https://api.example.com/orders", "method": "GET"}
        }
    }
    entry = resolve_api_endpoint(config_json, "orders")
    assert entry.name == "orders"
    assert entry.base_url == "https://api.example.com/orders"


def test_resolve_api_endpoint_raises_for_missing_name():
    with pytest.raises(ValueError, match="not found"):
        resolve_api_endpoint({"api_endpoints": {}}, "missing")


def test_resolve_api_endpoint_raises_when_no_api_endpoints_key():
    with pytest.raises(ValueError, match="not found"):
        resolve_api_endpoint({}, "orders")
