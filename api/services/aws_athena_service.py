from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from api.services.aws_athena_runtime import AwsAthenaRuntime
from etl_framework.repository.repository import ConfigRepository

TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}


class AthenaStartQueryResponse(BaseModel):
    query_execution_id: str


class AthenaQueryStatusResponse(BaseModel):
    query_execution_id: str
    state: str
    state_change_reason: str | None = None
    submission_time: datetime | None = None
    completion_time: datetime | None = None
    engine_execution_time_ms: int | None = None
    data_scanned_bytes: int | None = None


class AthenaQueryResultsResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, str | None]]


class AthenaRunQueryResponse(BaseModel):
    query_execution_id: str
    status: AthenaQueryStatusResponse
    results: AthenaQueryResultsResponse
    dq_metrics: dict[str, Any]


class AthenaQueryFailedError(RuntimeError):
    def __init__(self, status: AthenaQueryStatusResponse) -> None:
        message = f"Athena query ended with {status.state}: {status.state_change_reason or ''}".strip()
        super().__init__(message)
        self.status = status


def compute_dq_metrics(rows: list[dict[str, str | None]]) -> dict[str, Any]:
    columns = list(rows[0].keys()) if rows else []
    null_counts = {col: 0 for col in columns}
    distinct_values = {col: set() for col in columns}
    numeric_values: dict[str, list[float]] = {col: [] for col in columns}
    non_numeric = set()
    for row in rows:
        for col in columns:
            value = row.get(col)
            if value is None or value == "":
                null_counts[col] += 1
                continue
            distinct_values[col].add(value)
            try:
                numeric_values[col].append(float(value))
            except ValueError:
                non_numeric.add(col)
    numeric = {
        col: {"min": min(vals), "max": max(vals), "avg": sum(vals) / len(vals)}
        for col, vals in numeric_values.items()
        if vals and col not in non_numeric
    }
    return {
        "row_count": len(rows),
        "columns": columns,
        "null_counts": null_counts,
        "distinct_counts": {col: len(vals) for col, vals in distinct_values.items()},
        "numeric": numeric,
    }


class AwsAthenaService:
    def __init__(self, config_repo: ConfigRepository) -> None:
        self._runtime = AwsAthenaRuntime(config_repo)
        self._athena_client_override: Any | None = None

    def _client(self, config_id: int | str) -> Any:
        return self._runtime.client(config_id, self._athena_client_override)

    def start_query(
        self,
        config_id: int,
        database: str | None,
        query: str,
        output_location: str,
        workgroup: str | None = None,
    ) -> AthenaStartQueryResponse:
        kwargs: dict[str, Any] = {
            "QueryString": query,
            "ResultConfiguration": {"OutputLocation": output_location},
        }
        if database:
            kwargs["QueryExecutionContext"] = {"Database": database}
        if workgroup:
            kwargs["WorkGroup"] = workgroup
        response = self._client(config_id).start_query_execution(**kwargs)
        return AthenaStartQueryResponse(query_execution_id=response["QueryExecutionId"])

    def get_query_status(self, config_id: int, query_execution_id: str) -> AthenaQueryStatusResponse:
        execution = self._client(config_id).get_query_execution(QueryExecutionId=query_execution_id)["QueryExecution"]
        status = execution.get("Status") or {}
        stats = execution.get("Statistics") or {}
        return AthenaQueryStatusResponse(
            query_execution_id=str(execution.get("QueryExecutionId") or query_execution_id),
            state=str(status.get("State") or "UNKNOWN"),
            state_change_reason=status.get("StateChangeReason"),
            submission_time=status.get("SubmissionDateTime"),
            completion_time=status.get("CompletionDateTime"),
            engine_execution_time_ms=stats.get("EngineExecutionTimeInMillis"),
            data_scanned_bytes=stats.get("DataScannedInBytes"),
        )

    def get_query_results(self, config_id: int, query_execution_id: str, max_rows: int = 100) -> AthenaQueryResultsResponse:
        client = self._client(config_id)
        columns: list[str] = []
        rows: list[dict[str, str | None]] = []
        next_token: str | None = None
        while len(rows) < max_rows or (max_rows <= 0 and not columns):
            remaining = max(max_rows - len(rows), 0)
            api_max_results = min(max(remaining + (0 if columns else 1), 1), 1000)
            kwargs: dict[str, Any] = {"QueryExecutionId": query_execution_id, "MaxResults": api_max_results}
            if next_token:
                kwargs["NextToken"] = next_token
            response = client.get_query_results(**kwargs)
            raw_rows = (response.get("ResultSet") or {}).get("Rows") or []
            if not raw_rows:
                break
            start_idx = 0
            if not columns:
                columns = [cell.get("VarCharValue", "") for cell in raw_rows[0].get("Data", [])]
                start_idx = 1
            for raw in raw_rows[start_idx : start_idx + remaining]:
                cells = raw.get("Data", [])
                row = {col: (cells[idx].get("VarCharValue") if idx < len(cells) and cells[idx] else None) for idx, col in enumerate(columns)}
                rows.append(row)
            next_token = response.get("NextToken")
            if not next_token:
                break
        return AthenaQueryResultsResponse(columns=columns, rows=rows)

    def run_query(
        self,
        config_id: int,
        database: str | None,
        query: str,
        output_location: str,
        workgroup: str | None = None,
        poll_interval_seconds: float = 0.2,
        max_attempts: int = 20,
        max_rows: int = 100,
    ) -> AthenaRunQueryResponse:
        started = self.start_query(config_id, database, query, output_location, workgroup)
        status: AthenaQueryStatusResponse | None = None
        for attempt in range(max_attempts):
            status = self.get_query_status(config_id, started.query_execution_id)
            if status.state in TERMINAL_STATES:
                break
            if poll_interval_seconds and attempt < max_attempts - 1:
                time.sleep(poll_interval_seconds)
        if status is None or status.state not in TERMINAL_STATES:
            raise TimeoutError(f"Athena query did not finish after {max_attempts} attempts")
        if status.state != "SUCCEEDED":
            raise AthenaQueryFailedError(status)
        results = self.get_query_results(config_id, started.query_execution_id, max_rows=max_rows)
        return AthenaRunQueryResponse(
            query_execution_id=started.query_execution_id,
            status=status,
            results=results,
            dq_metrics=compute_dq_metrics(results.rows),
        )
