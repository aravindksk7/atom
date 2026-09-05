import pytest
from unittest.mock import MagicMock
from etl_framework.airflow.client import AirflowRestClient
from etl_framework.airflow.models import AirflowDag, AirflowDagRun, AirflowTaskInstance

@pytest.mark.asyncio
async def test_list_dags():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "dags": [
            {
                "dag_id": "example_dag",
                "description": "test dag",
                "is_paused": False,
                "schedule_interval": {"value": "@daily"},
            },
            {
                "dag_id": "string_sched_dag",
                "description": None,
                "is_paused": True,
                "schedule_interval": "0 0 * * *",
            },
        ],
        "total_entries": 2,
    }
    mock_session.get.return_value = mock_resp
    
    client = AirflowRestClient(
        base_url="https://airflow.example.com",
        username="admin",
        password="password",
        session=mock_session
    )
    dags = await client.list_dags(limit=50, offset=10)
    assert len(dags) == 2
    assert dags[0].dag_id == "example_dag"
    assert dags[1].dag_id == "string_sched_dag"
    mock_session.get.assert_called_once()
    args, kwargs = mock_session.get.call_args
    assert args[0] == "https://airflow.example.com/api/v1/dags"
    assert kwargs["params"] == {"limit": 50, "offset": 10}

@pytest.mark.asyncio
async def test_trigger_dag_run():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "dag_run_id": "manual__123",
        "dag_id": "example_dag",
        "state": "queued",
        "conf": {"batch_id": "123"},
    }
    mock_session.post.return_value = mock_resp
    
    client = AirflowRestClient(
        base_url="https://airflow.example.com",
        token="jwt-token",
        session=mock_session
    )
    run = await client.trigger_dag_run("example_dag", conf={"batch_id": "123"})
    assert run.dag_run_id == "manual__123"
    assert run.state == "queued"
    mock_session.post.assert_called_once()
    args, kwargs = mock_session.post.call_args
    assert kwargs["json"] == {"conf": {"batch_id": "123"}}
    assert kwargs["headers"]["Authorization"] == "Bearer jwt-token"

def test_sync_methods():
    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "dags": [{"dag_id": "sync_dag", "description": "sync", "is_paused": False, "schedule_interval": None}],
        "total_entries": 1,
    }
    mock_session.get.return_value = mock_resp
    mock_session.post.return_value = MagicMock(json=lambda: {"dag_run_id": "run_sync", "dag_id": "sync_dag", "state": "queued"})

    client = AirflowRestClient(base_url="https://airflow.example.com", session=mock_session)
    dags = client.list_dags_sync()
    assert dags[0].dag_id == "sync_dag"
    
    run = client.trigger_dag_run_sync("sync_dag")
    assert run.dag_run_id == "run_sync"

def test_auth_headers_and_basic_auth():
    token_client = AirflowRestClient(base_url="https://airflow.example.com", token="my-token")
    headers = token_client._get_headers()
    assert headers["Authorization"] == "Bearer my-token"
    assert token_client._get_auth() is None

    basic_client = AirflowRestClient(base_url="https://airflow.example.com", username="user", password="pwd")
    assert "Authorization" not in basic_client._get_headers()
    assert basic_client._get_auth() == ("user", "pwd")
