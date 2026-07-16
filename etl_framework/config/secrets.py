"""Pluggable secret resolution for config values.

Config files reference secrets as ``secret://<provider>/<name>``. The ``env``
provider ships by default; deployments register others (Vault, Azure Key
Vault, ...) at startup via ``register_provider`` without touching config
parsing code.
"""
from __future__ import annotations

import os
from typing import Protocol

_PREFIX = "secret://"


class SecretProvider(Protocol):
    def get(self, name: str) -> str: ...


class EnvSecretProvider:
    def get(self, name: str) -> str:
        value = os.environ.get(name)
        if value is None:
            raise ValueError(f"Secret env var '{name}' is not set")
        return value


_PROVIDERS: dict[str, SecretProvider] = {"env": EnvSecretProvider()}


def register_provider(name: str, provider: SecretProvider) -> None:
    _PROVIDERS[name] = provider


def is_secret_uri(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def resolve_secret_uri(uri: str) -> str:
    rest = uri[len(_PREFIX):]
    provider_name, _, secret_name = rest.partition("/")
    if not provider_name or not secret_name:
        raise ValueError(f"Malformed secret URI: {uri!r} (want secret://<provider>/<name>)")
    provider = _PROVIDERS.get(provider_name)
    if provider is None:
        raise ValueError(
            f"Unknown secret provider '{provider_name}' in {uri!r}; "
            f"registered: {sorted(_PROVIDERS)}"
        )
    return provider.get(secret_name)
