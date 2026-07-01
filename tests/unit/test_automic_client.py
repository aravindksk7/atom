from etl_framework.automic.client import AutomicClient
from etl_framework.config.models import EnvironmentConfig


def _make_client():
    env = EnvironmentConfig(
        name="test-env",
        db_host="localhost",
        db_password="",
        automic_url="https://automic.test",
    )
    return AutomicClient(env)


def test_get_status_by_run_id_checked_at_is_timezone_aware(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_request", lambda method, url: {"status": "ENDED_OK"})
    status = client.get_status_by_run_id("run-123")
    assert status.checked_at.tzinfo is not None


def test_get_status_by_job_name_checked_at_is_timezone_aware(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        client, "_request", lambda method, url: {"data": [{"status": "ENDED_OK"}]}
    )
    status = client.get_status_by_job_name("job-1")
    assert status.checked_at.tzinfo is not None


def test_get_status_by_job_name_no_executions_checked_at_is_timezone_aware(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_request", lambda method, url: {"data": []})
    status = client.get_status_by_job_name("job-1")
    assert status.checked_at.tzinfo is not None
