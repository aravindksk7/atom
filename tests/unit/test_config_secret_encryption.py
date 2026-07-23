"""Connection credentials (db_password, bo_password, automic_password, api_key,
bearer_token, basic_password, sap_bo_logon_token) stored in SavedConfig.config_json
were plaintext at rest — only masked in API *responses*, never encrypted in the
DB. This reuses the existing WEBHOOK_ENCRYPTION_KEY Fernet key (already used by
api/services/secret_store.py for webhook signing secrets) to encrypt/decrypt
these same fields transparently at the ConfigRepository boundary, so every
existing caller of cfg.config_json keeps seeing plaintext without changes.
"""
from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from etl_framework.repository.database import Base
from etl_framework.repository.models import SavedConfig
from etl_framework.repository.repository import ConfigRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def encryption_key(monkeypatch):
    """secret_store.py reads WEBHOOK_ENCRYPTION_KEY at import time, so the env
    var must be set and the module reloaded for the key to take effect."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOK_ENCRYPTION_KEY", key)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)
    yield key
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    importlib.reload(secret_store)


def _raw_config_json(db, config_id):
    """Read the column as actually persisted, bypassing the ORM identity map
    (which may hold an already-decrypted, mutated-in-place instance)."""
    return db.execute(select(SavedConfig.config_json).where(SavedConfig.id == config_id)).scalar_one()


def test_connection_password_encrypted_at_rest(db, encryption_key):
    repo = ConfigRepository(db)
    cfg = repo.create(name="dev", env_name="dev", config_data={"db_host": "h", "db_password": "hunter2"})

    raw = _raw_config_json(db, cfg.id)

    assert raw["db_password"] != "hunter2"
    assert raw["db_host"] == "h"


def test_get_transparently_decrypts_for_consumers(db, encryption_key):
    repo = ConfigRepository(db)
    cfg = repo.create(name="dev", env_name="dev", config_data={"db_password": "hunter2"})

    fetched = repo.get(cfg.id)

    assert fetched.config_json["db_password"] == "hunter2"


def test_list_and_get_by_name_also_decrypt(db, encryption_key):
    repo = ConfigRepository(db)
    repo.create(name="dev", env_name="dev", config_data={"bo_password": "bo-secret"})

    assert repo.list()[0].config_json["bo_password"] == "bo-secret"
    assert repo.get_by_name("dev").config_json["bo_password"] == "bo-secret"


def test_nested_connection_and_api_endpoint_secrets_encrypted_at_rest(db, encryption_key):
    repo = ConfigRepository(db)
    cfg = repo.create(name="dev", env_name="dev", config_data={
        "connections": {"replica": {"db_password": "replica-secret"}},
        "api_endpoints": {"orders": {"api_key": "endpoint-secret"}},
    })

    raw = _raw_config_json(db, cfg.id)
    assert raw["connections"]["replica"]["db_password"] != "replica-secret"
    assert raw["api_endpoints"]["orders"]["api_key"] != "endpoint-secret"

    fetched = repo.get(cfg.id)
    assert fetched.config_json["connections"]["replica"]["db_password"] == "replica-secret"
    assert fetched.config_json["api_endpoints"]["orders"]["api_key"] == "endpoint-secret"


def test_update_encrypts_new_secret_value(db, encryption_key):
    repo = ConfigRepository(db)
    cfg = repo.create(name="dev", env_name="dev", config_data={"db_password": "hunter2"})

    repo.update(cfg.id, config_data={"db_password": "new-secret"})

    raw = _raw_config_json(db, cfg.id)
    assert raw["db_password"] != "new-secret"
    assert repo.get(cfg.id).config_json["db_password"] == "new-secret"


def test_update_without_config_data_does_not_plaintext_existing_secret(db, encryption_key):
    """Regression guard: renaming a config (config_data absent from the
    update kwargs) must not re-persist a decrypted-in-memory value from a
    prior get() over the encrypted column."""
    repo = ConfigRepository(db)
    cfg = repo.create(name="dev", env_name="dev", config_data={"db_password": "hunter2"})
    repo.get(cfg.id)  # populate the identity map with a decrypted-in-place instance

    repo.update(cfg.id, name="dev-renamed")

    raw = _raw_config_json(db, cfg.id)
    assert raw["db_password"] != "hunter2"


def test_preexisting_plaintext_row_still_readable_after_key_configured(db, encryption_key):
    """Rows written before encryption was enabled are plain strings, not
    Fernet tokens; decrypt must fall back to returning them unchanged."""
    cfg = SavedConfig(name="legacy", env_name="legacy", config_json={"db_password": "already-plaintext"})
    db.add(cfg)
    db.commit()
    db.refresh(cfg)

    fetched = ConfigRepository(db).get(cfg.id)

    assert fetched.config_json["db_password"] == "already-plaintext"


def test_no_encryption_key_configured_behaves_as_plaintext(db, monkeypatch):
    """Without WEBHOOK_ENCRYPTION_KEY set, behavior is unchanged: config_data
    round-trips as plaintext, matching pre-feature behavior."""
    monkeypatch.delenv("WEBHOOK_ENCRYPTION_KEY", raising=False)
    import api.services.secret_store as secret_store
    importlib.reload(secret_store)

    repo = ConfigRepository(db)
    cfg = repo.create(name="dev", env_name="dev", config_data={"db_password": "hunter2"})

    assert _raw_config_json(db, cfg.id)["db_password"] == "hunter2"
    assert repo.get(cfg.id).config_json["db_password"] == "hunter2"
