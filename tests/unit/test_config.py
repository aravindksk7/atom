import pytest
from etl_framework.exceptions import ConfigurationError
from etl_framework.config.loader import ConfigLoader


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
