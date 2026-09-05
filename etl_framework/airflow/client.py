from __future__ import annotations

import asyncio
from typing import Any
import requests

from .models import AirflowDag, AirflowDagRun, AirflowTaskInstance


class AirflowRestClient:
    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.timeout = timeout
        self._session = session or requests.Session()

    def _get_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _get_auth(self) -> tuple[str, str] | None:
        if self.username and self.password:
            return (self.username, self.password)
        return None

    @staticmethod
    def _parse_dag(d: dict[str, Any]) -> AirflowDag:
        sched = d.get("schedule_interval")
        sched_str = sched.get("value") if isinstance(sched, dict) else (str(sched) if sched else None)
        return AirflowDag(
            dag_id=d["dag_id"],
            description=d.get("description"),
            is_paused=bool(d.get("is_paused", False)),
            schedule_interval=sched_str,
        )

    @staticmethod
    def _parse_dag_run(d: dict[str, Any]) -> AirflowDagRun:
        return AirflowDagRun(
            dag_run_id=d["dag_run_id"],
            dag_id=d["dag_id"],
            state=d.get("state") or "unknown",
            logical_date=d.get("logical_date") or d.get("execution_date"),
            conf=d.get("conf") if isinstance(d.get("conf"), dict) else {},
            start_date=d.get("start_date"),
            end_date=d.get("end_date"),
        )

    @staticmethod
    def _parse_task_instance(t: dict[str, Any]) -> AirflowTaskInstance:
        return AirflowTaskInstance(
            task_id=t["task_id"],
            dag_id=t["dag_id"],
            state=t.get("state") or "unknown",
            start_date=t.get("start_date"),
            end_date=t.get("end_date"),
            duration=float(t["duration"]) if t.get("duration") is not None else None,
        )

    def list_dags_sync(self, limit: int = 100, offset: int = 0) -> list[AirflowDag]:
        url = f"{self.base_url}/api/v1/dags"
        resp = self._session.get(
            url,
            headers=self._get_headers(),
            auth=self._get_auth(),
            params={"limit": limit, "offset": offset},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [self._parse_dag(d) for d in data.get("dags", [])]

    def trigger_dag_run_sync(self, dag_id: str, conf: dict[str, Any] | None = None) -> AirflowDagRun:
        url = f"{self.base_url}/api/v1/dags/{dag_id}/dagRuns"
        payload: dict[str, Any] = {"conf": conf or {}}
        resp = self._session.post(
            url,
            headers=self._get_headers(),
            auth=self._get_auth(),
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return self._parse_dag_run(resp.json())

    def get_dag_run_sync(self, dag_id: str, dag_run_id: str) -> AirflowDagRun:
        url = f"{self.base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}"
        resp = self._session.get(
            url,
            headers=self._get_headers(),
            auth=self._get_auth(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return self._parse_dag_run(resp.json())

    def list_task_instances_sync(self, dag_id: str, dag_run_id: str) -> list[AirflowTaskInstance]:
        url = f"{self.base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
        resp = self._session.get(
            url,
            headers=self._get_headers(),
            auth=self._get_auth(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return [self._parse_task_instance(t) for t in data.get("task_instances", [])]

    async def list_dags(self, limit: int = 100, offset: int = 0) -> list[AirflowDag]:
        return await asyncio.to_thread(self.list_dags_sync, limit, offset)

    async def trigger_dag_run(self, dag_id: str, conf: dict[str, Any] | None = None) -> AirflowDagRun:
        return await asyncio.to_thread(self.trigger_dag_run_sync, dag_id, conf)

    async def get_dag_run(self, dag_id: str, dag_run_id: str) -> AirflowDagRun:
        return await asyncio.to_thread(self.get_dag_run_sync, dag_id, dag_run_id)

    async def list_task_instances(self, dag_id: str, dag_run_id: str) -> list[AirflowTaskInstance]:
        return await asyncio.to_thread(self.list_task_instances_sync, dag_id, dag_run_id)
