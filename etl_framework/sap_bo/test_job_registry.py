import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException
from api.services.job_registry import JobRegistryService

@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.list_jobs.return_value = [{"name": "test_job", "query": "SELECT 1"}]
    repo.get_job.return_value = {"name": "test_job", "query": "SELECT 1"}
    return repo

@pytest.fixture
def service(mock_repo):
    return JobRegistryService(repository=mock_repo)

def test_get_job_not_found(service, mock_repo):
    mock_repo.get_job.return_value = None
    with pytest.raises(HTTPException) as exc:
        service.get_job("missing")
    assert exc.value.status_code == 404

def test_delete_job_not_found(service, mock_repo):
    mock_repo.get_job.return_value = None
    with pytest.raises(HTTPException) as exc:
        service.delete_job("missing")
    assert exc.value.status_code == 404