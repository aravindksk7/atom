import pytest
from etl_framework.exceptions import ConfigurationError
from etl_framework.config.loader import ConfigLoader
from api.routes.configs import _preserve_masked_secrets


def test_invalid_db_port_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: pass
    db_port: 99999
""")
    with pytest.raises(ConfigurationError, match="db_port"):
        ConfigLoader().load(str(cfg))


def test_string_pool_size_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: pass
    db_pool_size: "five"
""")
    with pytest.raises(ConfigurationError, match="db_pool_size"):
        ConfigLoader().load(str(cfg))


def test_valid_config_loads_typed_object(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: secret
    db_port: 1433
""")
    envs = ConfigLoader().load(str(cfg))
    assert envs["dev"].db_port == 1433
    assert isinstance(envs["dev"].db_port, int)
    assert envs["dev"].name == "dev"


def test_negative_pool_overflow_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: pass
    db_pool_overflow: -1
""")
    with pytest.raises(ConfigurationError, match="db_pool_overflow"):
        ConfigLoader().load(str(cfg))


def test_missing_required_field_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
""")
    with pytest.raises(ConfigurationError, match="db_password"):
        ConfigLoader().load(str(cfg))


def test_malformed_yaml_top_level_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("- item1\n- item2\n")  # YAML list, not mapping
    with pytest.raises(ConfigurationError, match="must be a YAML mapping"):
        ConfigLoader().load(str(cfg))


def test_env_var_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_DB_PASS", "secret123")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: "${MY_DB_PASS}"
""")
    envs = ConfigLoader().load(str(cfg))
    assert envs["dev"].db_password == "secret123"


def test_bo_auth_type_defaults_to_secEnterprise(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: secret
""")
    envs = ConfigLoader().load(str(cfg))
    assert envs["dev"].bo_auth_type == "secEnterprise"


def test_bo_auth_type_accepts_secWinAD_for_on_premises_AD_login(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: secret
    bo_auth_type: secWinAD
""")
    envs = ConfigLoader().load(str(cfg))
    assert envs["dev"].bo_auth_type == "secWinAD"


def test_invalid_bo_auth_type_raises_configuration_error(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("""
environments:
  dev:
    db_host: localhost
    db_name: mydb
    db_user: user
    db_password: secret
    bo_auth_type: bogus
""")
    with pytest.raises(ConfigurationError, match="bo_auth_type"):
        ConfigLoader().load(str(cfg))


def test_preserve_masked_top_level_secrets_on_config_update():
    incoming = {
        "db_password": "********",
        "bo_password": "********",
        "automic_password": "changed",
    }
    existing = {
        "db_password": "db-secret",
        "bo_password": "bo-secret",
        "automic_password": "automic-secret",
    }

    result = _preserve_masked_secrets(incoming, existing)

    assert result["db_password"] == "db-secret"
    assert result["bo_password"] == "bo-secret"
    assert result["automic_password"] == "changed"


def test_preserve_masked_named_connection_secrets_on_config_update():
    incoming = {
        "connections": {
            "finance": {"db_password": "********"},
            "hr": {"db_password": "new-secret"},
        }
    }
    existing = {
        "connections": {
            "finance": {"db_password": "finance-secret"},
            "hr": {"db_password": "old-hr-secret"},
        }
    }

    result = _preserve_masked_secrets(incoming, existing)

    assert result["connections"]["finance"]["db_password"] == "finance-secret"
    assert result["connections"]["hr"]["db_password"] == "new-secret"


def test_loader_resolves_secret_uris(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_DB_PASS", "resolved-pass")
    config = tmp_path / "envs.yml"
    config.write_text(
        "environments:\n"
        "  qa:\n"
        "    db_host: qa-host\n"
        "    db_password: secret://env/QA_DB_PASS\n",
        encoding="utf-8",
    )
    envs = ConfigLoader().load(str(config))
    assert envs["qa"].db_password == "resolved-pass"


def test_loader_merges_base_overlay(tmp_path):
    config = tmp_path / "envs.yml"
    config.write_text(
        "environments:\n"
        "  base:\n"
        "    db_host: shared-host\n"
        "    db_password: shared-pass\n"
        "    db_port: 1433\n"
        "  dev:\n"
        "    db_name: dev_db\n"
        "  qa:\n"
        "    db_name: qa_db\n"
        "    db_host: qa-override\n",
        encoding="utf-8",
    )
    envs = ConfigLoader().load(str(config))
    assert "base" not in envs
    assert envs["dev"].db_host == "shared-host"
    assert envs["dev"].db_name == "dev_db"
    assert envs["qa"].db_host == "qa-override"
