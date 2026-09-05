from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AirflowDag:
    dag_id: str
    description: str | None = None
    is_paused: bool = False
    schedule_interval: str | None = None


@dataclass
class AirflowDagRun:
    dag_run_id: str
    dag_id: str
    state: str
    logical_date: str | None = None
    conf: dict[str, Any] = field(default_factory=dict)
    start_date: str | None = None
    end_date: str | None = None


@dataclass
class AirflowTaskInstance:
    task_id: str
    dag_id: str
    state: str
    start_date: str | None = None
    end_date: str | None = None
    duration: float | None = None
