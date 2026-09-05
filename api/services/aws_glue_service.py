from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel

from api.services.aws_glue_runtime import AwsGlueRuntime
from etl_framework.aws_s3.formats import normalize_schema_type
from etl_framework.repository.repository import ConfigRepository

GLUE_JOB_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT", "ERROR"}


class GlueDatabasesResponse(BaseModel):
    databases: list[str]


class GlueTablesResponse(BaseModel):
    database: str
    tables: list[str]


class GlueTableResponse(BaseModel):
    database: str
    table: str
    columns: list[dict[str, str]]
    partition_keys: list[dict[str, str]]
    location: str | None = None
    input_format: str | None = None
    output_format: str | None = None
    table_type: str | None = None


class GlueCatalogCompareResponse(BaseModel):
    match: bool
    source: dict[str, Any]
    target: dict[str, Any]
    diff: dict[str, Any]


def normalize_glue_type(type_name: str) -> str:
    normalized = normalize_schema_type(str(type_name).replace(" ", ""))
    return {"int": "int64", "bigint": "int64"}.get(normalized, normalized)


def _cols(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "name": str(c.get("Name", "")),
            "type": normalize_glue_type(c.get("Type", "string")),
            **({"comment": str(c.get("Comment"))} if c.get("Comment") else {}),
        }
        for c in raw
    ]


def normalize_glue_table(raw: dict[str, Any], database: str) -> dict[str, Any]:
    sd = raw.get("StorageDescriptor") or {}
    return {
        "database": database,
        "table": str(raw.get("Name", "")),
        "columns": _cols(sd.get("Columns") or []),
        "partition_keys": _cols(raw.get("PartitionKeys") or []),
        "location": sd.get("Location"),
        "input_format": sd.get("InputFormat"),
        "output_format": sd.get("OutputFormat"),
        "table_type": raw.get("TableType"),
    }


def _column_type_mismatches(source: dict[str, Any], target: dict[str, Any]) -> list[dict[str, str]]:
    src = {c["name"]: c["type"] for c in source["columns"]}
    tgt = {c["name"]: c["type"] for c in target["columns"]}
    return [
        {"column": name, "expected_type": src[name], "actual_type": tgt[name]}
        for name in sorted(src.keys() & tgt.keys())
        if src[name] != tgt[name]
    ]


def compare_glue_tables(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    compare_location: bool = True,
    compare_formats: bool = True,
    compare_partitions: bool = True,
) -> dict[str, Any]:
    src_names = {c["name"] for c in source["columns"]}
    tgt_names = {c["name"] for c in target["columns"]}
    diff: dict[str, Any] = {
        "missing_columns": sorted(src_names - tgt_names),
        "extra_columns": sorted(tgt_names - src_names),
        "type_mismatches": _column_type_mismatches(source, target),
        "partition_key_mismatches": [],
        "location_mismatch": None,
        "format_mismatch": None,
    }
    if compare_partitions and source["partition_keys"] != target["partition_keys"]:
        diff["partition_key_mismatches"] = [{"source": source["partition_keys"], "target": target["partition_keys"]}]
    if compare_location and source.get("location") != target.get("location"):
        diff["location_mismatch"] = {"source": source.get("location"), "target": target.get("location")}
    fmt = {
        "input_format": (source.get("input_format"), target.get("input_format")),
        "output_format": (source.get("output_format"), target.get("output_format")),
        "table_type": (source.get("table_type"), target.get("table_type")),
    }
    if compare_formats:
        changed = {k: {"source": v[0], "target": v[1]} for k, v in fmt.items() if v[0] != v[1]}
        diff["format_mismatch"] = changed or None
    match = not any(diff.values())
    return {"match": match, "source": source, "target": target, "diff": diff}


class AwsGlueService:
    def __init__(self, config_repo: ConfigRepository) -> None:
        self._runtime = AwsGlueRuntime(config_repo)
        self._glue_client_override: Any | None = None

    @property
    def runtime(self) -> AwsGlueRuntime:
        return self._runtime

    def _client(self, config_id: int | str) -> Any:
        return self._runtime.client(config_id, self._glue_client_override)

    def list_databases(self, config_id: int | str) -> GlueDatabasesResponse:
        client = self._client(config_id)
        if hasattr(client, "get_paginator"):
            pages = client.get_paginator("get_databases").paginate()
            return GlueDatabasesResponse(databases=[d["Name"] for page in pages for d in page.get("DatabaseList", [])])
        data = client.get_databases()
        return GlueDatabasesResponse(databases=[d["Name"] for d in data.get("DatabaseList", [])])

    def list_tables(self, config_id: int | str, database: str) -> GlueTablesResponse:
        client = self._client(config_id)
        if hasattr(client, "get_paginator"):
            pages = client.get_paginator("get_tables").paginate(DatabaseName=database)
            return GlueTablesResponse(database=database, tables=[t["Name"] for page in pages for t in page.get("TableList", [])])
        data = client.get_tables(DatabaseName=database)
        return GlueTablesResponse(database=database, tables=[t["Name"] for t in data.get("TableList", [])])

    def describe_table(self, config_id: int | str, database: str, table: str) -> GlueTableResponse:
        raw = self._client(config_id).get_table(DatabaseName=database, Name=table)["Table"]
        return GlueTableResponse(**normalize_glue_table(raw, database))

    def compare_tables(
        self,
        config_id: int | str,
        source_database: str,
        source_table: str,
        target_database: str,
        target_table: str,
        compare_location: bool = True,
        compare_formats: bool = True,
        compare_partitions: bool = True,
    ) -> GlueCatalogCompareResponse:
        source = self.describe_table(config_id, source_database, source_table).model_dump()
        target = self.describe_table(config_id, target_database, target_table).model_dump()
        return GlueCatalogCompareResponse(
            **compare_glue_tables(
                source,
                target,
                compare_location=compare_location,
                compare_formats=compare_formats,
                compare_partitions=compare_partitions,
            )
        )

    def list_jobs(self, config_id: int | str) -> list[dict[str, Any]]:
        client = self._client(config_id)
        resp = client.get_jobs()
        return [
            {
                "name": j.get("Name"),
                "description": j.get("Description"),
                "role": j.get("Role"),
                "script_location": (j.get("Command") or {}).get("ScriptLocation"),
                "worker_type": j.get("WorkerType"),
            }
            for j in resp.get("Jobs", [])
        ]

    def get_job(self, config_id: int | str, job_name: str) -> dict[str, Any]:
        client = self._client(config_id)
        resp = client.get_job(JobName=job_name)
        job = resp.get("Job", resp)
        return {
            "name": job.get("Name"),
            "description": job.get("Description"),
            "role": job.get("Role"),
            "script_location": (job.get("Command") or {}).get("ScriptLocation"),
            "worker_type": job.get("WorkerType"),
            "max_capacity": job.get("MaxCapacity"),
        }

    def start_job_run(self, config_id: int | str, job_name: str, arguments: dict[str, str] | None = None) -> dict[str, Any]:
        client = self._client(config_id)
        resp = client.start_job_run(JobName=job_name, Arguments=arguments or {})
        return {"job_run_id": resp.get("JobRunId"), "job_name": job_name}

    def get_job_run_status(self, config_id: int | str, job_name: str, job_run_id: str) -> dict[str, Any]:
        client = self._client(config_id)
        resp = client.get_job_run(JobName=job_name, RunId=job_run_id)
        jr = resp.get("JobRun") or {}
        return {
            "job_run_id": job_run_id,
            "job_name": job_name,
            "job_run_state": jr.get("JobRunState", "UNKNOWN"),
            "execution_time": jr.get("ExecutionTime"),
            "error_message": jr.get("ErrorMessage"),
        }

    def run_job_to_completion(
        self,
        config_id: int | str,
        job_name: str,
        arguments: dict[str, str] | None = None,
        poll_interval_seconds: float = 2.0,
        max_attempts: int = 120,
    ) -> dict[str, Any]:
        run = self.start_job_run(config_id, job_name, arguments)
        run_id = str(run.get("job_run_id") or "")
        for _ in range(max_attempts):
            time.sleep(poll_interval_seconds)
            status = self.get_job_run_status(config_id, job_name, run_id)
            if status.get("job_run_state") in GLUE_JOB_TERMINAL_STATES:
                return status
        raise TimeoutError(f"Glue job '{job_name}' run '{run_id}' timed out after {max_attempts * poll_interval_seconds}s")
