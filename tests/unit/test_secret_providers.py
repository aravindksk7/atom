import pytest

from etl_framework.config.secrets import (
    EnvSecretProvider,
    is_secret_uri,
    register_provider,
    resolve_secret_uri,
)


def test_env_provider_reads_environment(monkeypatch):
    monkeypatch.setenv("MY_DB_PASS", "s3cret")
    assert resolve_secret_uri("secret://env/MY_DB_PASS") == "s3cret"


def test_env_provider_missing_raises(monkeypatch):
    monkeypatch.delenv("NOPE_MISSING", raising=False)
    with pytest.raises(ValueError, match="NOPE_MISSING"):
        resolve_secret_uri("secret://env/NOPE_MISSING")


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="vault"):
        resolve_secret_uri("secret://vault/whatever")


def test_custom_provider_registration():
    class Static:
        def get(self, name: str) -> str:
            return f"static-{name}"

    register_provider("static", Static())
    assert resolve_secret_uri("secret://static/abc") == "static-abc"


def test_is_secret_uri():
    assert is_secret_uri("secret://env/X")
    assert not is_secret_uri("${ENV_VAR}")
    assert not is_secret_uri("plainvalue")
