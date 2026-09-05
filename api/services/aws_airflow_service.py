from __future__ import annotations

import asyncio
import time
from typing import Any

from api.services.aws_airflow_runtime import AwsAirflowRuntime
from etl_framework.repository.repository import ConfigRepository

TERMINAL_STATES = {"success", "failed"}


def _dag_dict(dag: Any) -> dict[str, Any]:
    return {
        "dag_id": dag.dag_id,
        "description": dag.description,
        "is_paused": dag.is_paused,
        "schedule_interval": dag.schedule_interval,
    }


def _run_dict(run: Any) -> dict[str, Any]:
    return {
        "dag_run_id": run.dag_run_id,
        "dag_id": run.dag_id,
        "state": run.state,
        "logical_date": run.logical_date,
    }


def _status_dict(run: Any, tasks: list[Any]) -> dict[str, Any]:
    return {
        "dag_run_id": run.dag_run_id,
        "dag_id": run.dag_id,
        "state": run.state,
        "task_instances": [
            {"task_id": t.task_id, "state": t.state, "duration": t.duration}
            for t in tasks
        ],
    }


class AwsAirflowService:
    def __init__(
        self,
        config_repo: ConfigRepository | None = None,
        runtime: AwsAirflowRuntime | None = None,
    ) -> None:
        self._runtime = runtime or AwsAirflowRuntime(config_repo)

    def _client(self, config_id: int | str) -> Any:
        return self._runtime.client(config_id)

    # --- sync (used by synchronous FastAPI `def` routes) ---

    def list_dags(self, config_id: int | str) -> list[dict[str, Any]]:
        return [_dag_dict(dag) for dag in self._client(config_id).list_dags_sync()]

    def get_dag_details(self, config_id: int | str, dag_id: str) -> dict[str, Any]:
        for dag in self.list_dags(config_id):
            if dag["dag_id"] == dag_id:
                return dag
        raise ValueError(f"DAG {dag_id!r} not found")

    def trigger_dag_run(
        self,
        config_id: int | str,
        dag_id: str,
        conf: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = self._client(config_id).trigger_dag_run_sync(dag_id, conf=conf)
        return _run_dict(run)

    def get_dag_run_status(
        self,
        config_id: int | str,
        dag_id: str,
        dag_run_id: str,
    ) -> dict[str, Any]:
        client = self._client(config_id)
        return _status_dict(
            client.get_dag_run_sync(dag_id, dag_run_id),
            client.list_task_instances_sync(dag_id, dag_run_id),
        )

    def run_dag_to_completion(
        self,
        config_id: int | str,
        dag_id: str,
        conf: dict[str, Any] | None = None,
        poll_interval_seconds: float = 1.0,
        max_attempts: int = 60,
    ) -> dict[str, Any]:
        started = self.trigger_dag_run(config_id, dag_id, conf=conf)
        dag_run_id = started["dag_run_id"]
        status: dict[str, Any] | None = None
        for attempt in range(max_attempts):
            status = self.get_dag_run_status(config_id, dag_id, dag_run_id)
            if status["state"] in TERMINAL_STATES:
                return status
            if poll_interval_seconds and attempt < max_attempts - 1:
                time.sleep(poll_interval_seconds)
        raise TimeoutError(
            f"Airflow DAG run {dag_run_id!r} for DAG {dag_id!r} did not reach a "
            f"terminal state after {max_attempts} attempts"
        )

    # --- async ---

    async def list_dags_async(self, config_id: int | str) -> list[dict[str, Any]]:
        return [_dag_dict(dag) for dag in await self._client(config_id).list_dags()]

    async def get_dag_details_async(self, config_id: int | str, dag_id: str) -> dict[str, Any]:
        for dag in await self.list_dags_async(config_id):
            if dag["dag_id"] == dag_id:
                return dag
        raise ValueError(f"DAG {dag_id!r} not found")

    async def trigger_dag_run_async(
        self,
        config_id: int | str,
        dag_id: str,
        conf: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run = await self._client(config_id).trigger_dag_run(dag_id, conf=conf)
        return _run_dict(run)

    async def get_dag_run_status_async(
        self,
        config_id: int | str,
        dag_id: str,
        dag_run_id: str,
    ) -> dict[str, Any]:
        client = self._client(config_id)
        return _status_dict(
            await client.get_dag_run(dag_id, dag_run_id),
            await client.list_task_instances(dag_id, dag_run_id),
        )

    async def run_dag_to_completion_async(
        self,
        config_id: int | str,
        dag_id: str,
        conf: dict[str, Any] | None = None,
        poll_interval_seconds: float = 1.0,
        max_attempts: int = 60,
    ) -> dict[str, Any]:
        started = await self.trigger_dag_run_async(config_id, dag_id, conf=conf)
        dag_run_id = started["dag_run_id"]
        status: dict[str, Any] | None = None
        for attempt in range(max_attempts):
            status = await self.get_dag_run_status_async(config_id, dag_id, dag_run_id)
            if status["state"] in TERMINAL_STATES:
                return status
            if poll_interval_seconds and attempt < max_attempts - 1:
                await asyncio.sleep(poll_interval_seconds)
        raise TimeoutError(
            f"Airflow DAG run {dag_run_id!r} for DAG {dag_id!r} did not reach a "
            f"terminal state after {max_attempts} attempts"
        )