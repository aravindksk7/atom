import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.exceptions import configure_exception_handlers
from etl_framework.exceptions import SchemaValidationError, RepositoryError, ConfigurationError

app = FastAPI()
configure_exception_handlers(app)

@app.get("/schema-error")
def raise_schema_error():
    raise SchemaValidationError("test_query", ["missing_col"], ["extra_col"])

@app.get("/repo-error")
def raise_repo_error():
    raise RepositoryError("sqlite", "save", Exception("DB Locked"))

@app.get("/config-error")
def raise_config_error():
    raise ConfigurationError("Missing db_host", field_name="db_host")

client = TestClient(app)

def test_schema_validation_error_returns_422_with_details():
    response = client.get("/schema-error")
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "SchemaValidationError"
    assert data["details"]["missing_in_target"] == ["missing_col"]

def test_repository_error_returns_500():
    response = client.get("/repo-error")
    assert response.status_code == 500
    assert response.json()["error"] == "RepositoryError"

def test_configuration_error_returns_400():
    response = client.get("/config-error")
    assert response.status_code == 400
    assert response.json()["error"] == "ConfigurationError"