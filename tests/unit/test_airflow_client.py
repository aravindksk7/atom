import pytest
import respx
import httpx
from etl_framework.airflow.client import AirflowRestClient
from etl_framework.airflow.models import AirflowDag, AirflowDagRun, AirflowTaskInstance


@pytest.mark.asyncio
async def test_list_dags():
    async with respx.mock(base_url="https://airflow.example.com/api/v1") as respx_mock:
        respx_mock.get("/dags").respond(
            status_code=200,
            json={
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
            },
        )
        client = AirflowRestClient(
            base_url="https://airflow.example.com",
            username="admin",
            password="password",
        )
        dags = await client.list_dags(limit=50, offset=10)
        assert len(dags) == 2
        assert dags[0].dag_id == "example_dag"
        assert dags[0].description == "test dag"
        assert dags[0].is_paused is False
        assert dags[0].schedule_interval == "@daily"
        assert dags[1].dag_id == "string_sched_dag"
        assert dags[1].is_paused is True
        assert dags[1].schedule_interval == "0 0 * * *"


@pytest.mark.asyncio
async def test_trigger_dag_run():
    async with respx.mock(base_url="https://airflow.example.com/api/v1") as respx_mock:
        route = respx_mock.post("/dags/example_dag/dagRuns").respond(
            status_code=200,
            json={
                "dag_run_id": "manual__2026-09-05T00:00:00+00:00",
                "dag_id": "example_dag",
                "state": "queued",
                "logical_date": "2026-09-05T00:00:00+00:00",
                "conf": {"batch_id": "123"},
                "start_date": "2026-09-05T00:00:01+00:00",
                "end_date": None,
            },
        )
        client = AirflowRestClient(
            base_url="https://airflow.example.com",
            token="jwt-token",
        )
        run = await client.trigger_dag_run("example_dag", conf={"batch_id": "123"})
        assert run.dag_run_id == "manual__2026-09-05T00:00:00+00:00"
        assert run.dag_id == "example_dag"
        assert run.state == "queued"
        assert run.conf == {"batch_id": "123"}
        assert run.start_date == "2026-09-05T00:00:01+00:00"
        assert run.end_date is None
        assert route.called
        sent_headers = route.calls.last.request.headers
        assert sent_headers.get("authorization") == "Bearer jwt-token"


@pytest.mark.asyncio
async def test_get_dag_run():
    async with respx.mock(base_url="https://airflow.example.com/api/v1") as respx_mock:
        respx_mock.get("/dags/example_dag/dagRuns/manual__1").respond(
            status_code=200,
            json={
                "dag_run_id": "manual__1",
                "dag_id": "example_dag",
                "state": "success",
                "logical_date": "2026-09-05T00:00:00+00:00",
                "conf": {},
                "start_date": "2026-09-05T00:00:01+00:00",
                "end_date": "2026-09-05T00:01:00+00:00",
            },
        )
        client = AirflowRestClient(base_url="https://airflow.example.com")
        run = await client.get_dag_run("example_dag", "manual__1")
        assert run.dag_run_id == "manual__1"
        assert run.dag_id == "example_dag"
        assert run.state == "success"
        assert run.logical_date == "2026-09-05T00:00:00+00:00"
        assert run.end_date == "2026-09-05T00:01:00+00:00"


@pytest.mark.asyncio
async def test_list_task_instances():
    async with respx.mock(base_url="https://airflow.example.com/api/v1") as respx_mock:
        respx_mock.get("/dags/example_dag/dagRuns/manual__1/taskInstances").respond(
            status_code=200,
            json={
                "task_instances": [
                    {
                        "task_id": "task_1",
                        "dag_id": "example_dag",
                        "state": "success",
                        "start_date": "2026-09-05T00:00:02+00:00",
                        "end_date": "2026-09-05T00:00:30+00:00",
                        "duration": 28.0,
                    },
                    {
                        "task_id": "task_2",
                        "dag_id": "example_dag",
                        "state": "running",
                        "start_date": "2026-09-05T00:00:31+00:00",
                        "end_date": None,
                        "duration": None,
                    },
                ],
                "total_entries": 2,
            },
        )
        client = AirflowRestClient(base_url="https://airflow.example.com")
        tasks = await client.list_task_instances("example_dag", "manual__1")
        assert len(tasks) == 2
        assert tasks[0].task_id == "task_1"
        assert tasks[0].state == "success"
        assert tasks[0].duration == 28.0
        assert tasks[1].task_id == "task_2"
        assert tasks[1].state == "running"
        assert tasks[1].duration is None


def test_sync_methods():
    with respx.mock(base_url="https://airflow.example.com/api/v1") as respx_mock:
        respx_mock.get("/dags").respond(
            status_code=200,
            json={
                "dags": [{"dag_id": "sync_dag", "description": "sync", "is_paused": False, "schedule_interval": None}],
                "total_entries": 1,
            },
        )
        respx_mock.post("/dags/sync_dag/dagRuns").respond(
            status_code=200,
            json={"dag_run_id": "run_sync", "dag_id": "sync_dag", "state": "queued"},
        )
        respx_mock.get("/dags/sync_dag/dagRuns/run_sync").respond(
            status_code=200,
            json={"dag_run_id": "run_sync", "dag_id": "sync_dag", "state": "running"},
        )
        respx_mock.get("/dags/sync_dag/dagRuns/run_sync/taskInstances").respond(
            status_code=200,
            json={"task_instances": [{"task_id": "task_sync", "dag_id": "sync_dag", "state": "running"}]},
        )

        client = AirflowRestClient(base_url="https://airflow.example.com")
        dags = client.list_dags_sync()
        assert len(dags) == 1
        assert dags[0].dag_id == "sync_dag"

        run = client.trigger_dag_run_sync("sync_dag")
        assert run.dag_run_id == "run_sync"

        run_status = client.get_dag_run_sync("sync_dag", "run_sync")
        assert run_status.state == "running"

        tasks = client.list_task_instances_sync("sync_dag", "run_sync")
        assert len(tasks) == 1
        assert tasks[0].task_id == "task_sync"


def test_auth_headers_and_basic_auth():
    token_client = AirflowRestClient(base_url="https://airflow.example.com", token="my-token")
    headers = token_client._get_headers()
    assert headers["Authorization"] == "Bearer my-token"
    assert token_client._get_auth() is None

    basic_client = AirflowRestClient(base_url="https://airflow.example.com", username="user", password="pwd")
    assert "Authorization" not in basic_client._get_headers()
    auth = basic_client._get_auth()
    assert isinstance(auth, httpx.BasicAuth)
