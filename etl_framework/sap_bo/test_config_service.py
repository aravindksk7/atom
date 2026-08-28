import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from api.services.config_service import ConfigService
from etl_framework.exceptions import ConfigurationError

@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.list_configs.return_value = [{"name": "dev", "db_host": "localhost"}]
    repo.get_config.return_value = {"name": "dev", "db_host": "localhost"}
    return repo

@pytest.fixture
def service(mock_repo):
    return ConfigService(repository=mock_repo)

def test_list_configs(service, mock_repo):
    configs = service.list_configs()
    assert len(configs) == 1
    mock_repo.list_configs.assert_called_once()

def test_get_config_not_found(service, mock_repo):
    mock_repo.get_config.return_value = None
    with pytest.raises(HTTPException) as exc:
        service.get_config("missing")
    assert exc.value.status_code == 404

def test_validate_config_raises_configuration_error_on_invalid_data(service):
    with pytest.raises(ConfigurationError) as exc:
        service.validate_config("dev", {"db_host": "localhost", "db_port": 999999})
    assert "db_port" in str(exc.value)

def test_save_config_validates_and_saves(service, mock_repo):
    valid_data = {"db_host": "localhost", "db_port": 1433}
    service.save_config("dev", valid_data)
    mock_repo.save_config.assert_called_once_with("dev", valid_data)

def test_delete_config_not_found(service, mock_repo):
    mock_repo.get_config.return_value = None
    with pytest.raises(HTTPException) as exc:
        service.delete_config("dev")
    assert exc.value.status_code == 404

@patch("api.services.config_service.ConfigLoader")
def test_import_yaml(MockLoader, mock_repo):
    mock_loader_instance = MockLoader.return_value
    
    mock_env = MagicMock()
    mock_env.model_dump.return_value = {"db_host": "remote"}
    mock_loader_instance.load.return_value = {"prod": mock_env}
    
    service = ConfigService(repository=mock_repo)
    # Mock out the inner loader instance
    service._loader = mock_loader_instance
    
    result = service.import_yaml("environments:\n  prod:\n    db_host: remote\n")
    assert "prod" in result
    mock_repo.save_config.assert_called_once_with("prod", {"db_host": "remote"})