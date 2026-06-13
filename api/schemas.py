from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel


class ConfigCreate(BaseModel):
    name: str
    env_name: str
    config_data: dict[str, Any] = {}


class ConfigUpdate(BaseModel):
    name: str | None = None
    env_name: str | None = None
    config_data: dict[str, Any] | None = None


class ConfigOut(BaseModel):
    id: int
    name: str
    env_name: str
    config_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RunTrigger(BaseModel):
    source_env: str
    target_env: str
    job_names: list[str] = []
    config_id: int | None = None
    config_data: dict[str, Any] = {}


class RunStatusOut(BaseModel):
    run_id: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    slow: int = 0
    error: int = 0

    model_config = {"from_attributes": True}


class TestResultOut(BaseModel):
    id: int
    query_name: str
    status: str
    duration_seconds: float
    source_row_count: int
    target_row_count: int
    value_mismatch_count: int
    missing_in_target_count: int
    missing_in_source_count: int
    error_message: str | None = None
    executed_at: datetime | None = None

    model_config = {"from_attributes": True}


class MismatchOut(BaseModel):
    id: int
    column_name: str | None = None
    key_values: dict | None = None
    source_value: str | None = None
    target_value: str | None = None
    mismatch_type: str | None = None

    model_config = {"from_attributes": True}


class RunDetailOut(RunStatusOut):
    source_env: str | None = None
    target_env: str | None = None
    config_snapshot: dict | None = None
    results: list[TestResultOut] = []


class JobOut(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
