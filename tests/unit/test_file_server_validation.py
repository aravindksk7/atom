from __future__ import annotations

import pytest

from api.services.file_server_validation import file_server_profile_errors

HTTPS_PW = {"kind": "crushftp", "protocol": "https", "endpoint_url": "https://f.example.com",
            "auth_method": "password", "username": "u", "password": "p"}
HTTPS_KEY = {"kind": "crushftp", "protocol": "https", "endpoint_url": "https://f.example.com",
             "auth_method": "api_key", "api_key_header": "X-Api-Key", "api_key": "k"}
SFTP = {"kind": "crushftp", "protocol": "sftp", "host": "f.example.com", "auth_method": "password",
        "username": "u", "password": "p"}


@pytest.fixture(autouse=True)
def _no_insecure_http(monkeypatch):
    monkeypatch.delenv("CRUSHFTP_ALLOW_INSECURE_HTTP", raising=False)


@pytest.mark.parametrize("data", [
    HTTPS_PW, HTTPS_KEY, SFTP,
    {**HTTPS_KEY, "username": "u", "password": "p"},
    {**SFTP, "auth_method": "private_key"},
    {"kind": "sftp", "host": "h", "auth_method": "password"},
    {"kind": "s3"},
])
def test_valid_profiles_have_no_errors(data):
    assert file_server_profile_errors(data) == []


@pytest.mark.parametrize("data, fragment", [
    ({**HTTPS_PW, "protocol": None}, "protocol"),
    ({**HTTPS_PW, "endpoint_url": None}, "endpoint_url"),
    ({**HTTPS_PW, "endpoint_url": "ftp://f.example.com"}, "https://"),
    ({**HTTPS_PW, "endpoint_url": "http://f.example.com"}, "https://"),
    ({**HTTPS_PW, "endpoint_url": "https://"}, "https://"),
    ({**HTTPS_PW, "username": None}, "username"),
    ({**HTTPS_PW, "password": None}, "password"),
    ({**HTTPS_PW, "auth_method": "private_key"}, "auth_method"),
    ({**HTTPS_KEY, "api_key": None}, "api_key"),
    ({**HTTPS_KEY, "api_key_header": None}, "api_key_header"),
    ({**HTTPS_KEY, "api_key_header": "Bad Header"}, "api_key_header"),
    ({**HTTPS_KEY, "api_key_header": "X-Key\r\nEvil: 1"}, "api_key_header"),
    ({**HTTPS_KEY, "api_key_header": "Cookie"}, "api_key_header"),
    ({**SFTP, "host": None}, "host"),
    ({**SFTP, "auth_method": "api_key"}, "auth_method"),
    ({**SFTP, "api_key": "k"}, "api_key"),
    ({"kind": "sftp", "host": "h", "api_key": "k"}, "api_key"),
])
def test_invalid_profiles_report_the_field(data, fragment):
    errors = file_server_profile_errors(data)
    assert errors and any(fragment in e for e in errors)


def test_http_url_allowed_only_with_insecure_env(monkeypatch):
    data = {**HTTPS_PW, "endpoint_url": "http://127.0.0.1:28080"}
    assert file_server_profile_errors(data)
    monkeypatch.setenv("CRUSHFTP_ALLOW_INSECURE_HTTP", "1")
    assert file_server_profile_errors(data) == []


def test_masked_secret_counts_as_present():
    assert file_server_profile_errors({**HTTPS_KEY, "api_key": "********"}) == []
