import pytest
from fastapi import HTTPException
from unittest.mock import MagicMock, patch
from api.services.artifact_service import ArtifactService

def test_generate_report_raises_404_if_run_missing():
    mock_repo = MagicMock()
    mock_repo.get_run.return_value = None
    service = ArtifactService(repository=mock_repo)
    
    with pytest.raises(HTTPException) as exc_info:
        service.generate_html_report("missing-123")
    assert exc_info.value.status_code == 404

@patch("api.services.artifact_service.ReportGenerator")
def test_generate_report_returns_path_on_success(MockGenerator):
    mock_repo = MagicMock()
    mock_repo.get_run.return_value = MagicMock(run_id="run-123")
    mock_generator_instance = MockGenerator.return_value
    mock_generator_instance.generate.return_value = "/tmp/reports/report_run-123.html"

    service = ArtifactService(repository=mock_repo, report_dir="/tmp/reports")
    path = service.generate_html_report("run-123")
    assert path == "/tmp/reports/report_run-123.html"


@patch("api.services.artifact_service._current_app_timezone", return_value="America/New_York")
@patch("api.services.artifact_service.ReportGenerator")
def test_generate_report_passes_configured_timezone(MockGenerator, mock_tz):
    mock_repo = MagicMock()
    mock_repo.get_run.return_value = MagicMock(run_id="run-123")
    mock_generator_instance = MockGenerator.return_value
    mock_generator_instance.generate.return_value = "/tmp/reports/report_run-123.html"

    service = ArtifactService(repository=mock_repo, report_dir="/tmp/reports")
    service.generate_html_report("run-123")

    MockGenerator.assert_called_once_with(output_dir="/tmp/reports", timezone="America/New_York")