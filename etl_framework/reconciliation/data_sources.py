from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from fastapi import HTTPException
from sqlalchemy import create_engine, text

from api.services.file_source import read_tabular


def extract_data_source(spec: dict[str, Any], db_session: Any | None = None) -> pd.DataFrame:
    """Extract tabular data into a pandas DataFrame from various source types.

    Supported source_type:
    - "file": local filesystem path or base64 encoded data
    - "sql": database query or table execution
    - "aws_athena": S3/Athena query execution or runner
    - "api": HTTP REST endpoint payload
    - "sap_bo": SAP BO report snapshot or client fetch
    """
    source_type = spec.get("source_type") or spec.get("type")
    if not source_type:
        raise ValueError("Missing 'source_type' in data source specification")

    source_type = str(source_type).lower()

    if source_type == "file":
        return _extract_file_source(spec)
    elif source_type == "sql":
        return _extract_sql_source(spec, db_session=db_session)
    elif source_type == "aws_athena":
        return _extract_athena_source(spec)
    elif source_type == "api":
        return _extract_api_source(spec)
    elif source_type == "sap_bo":
        return _extract_sap_bo_source(spec)
    else:
        raise ValueError(f"Unsupported source_type: '{source_type}'")


def _extract_file_source(spec: dict[str, Any]) -> pd.DataFrame:
    path = spec.get("file_path") or spec.get("path") or spec.get("filepath")
    content_b64 = spec.get("content_b64") or spec.get("b64_content") or spec.get("file_b64")
    file_name = spec.get("file_name") or spec.get("filename")
    combine_sheets = spec.get("combine_sheets", False)

    if not path and not content_b64:
        raise ValueError("File data source requires 'file_path' or 'content_b64'")

    # Try read_tabular from file_source service first
    try:
        return read_tabular(
            path=str(path) if path else None,
            content_b64=content_b64,
            file_name=file_name,
            combine_sheets=combine_sheets,
        )
    except (HTTPException, ValueError):
        # Fallback to direct pandas reading for paths outside default allowed dirs
        if path and Path(path).exists():
            p = Path(path)
            suffix = p.suffix.lower()
            if suffix in (".csv", ".txt"):
                return pd.read_csv(p)
            elif suffix in (".xlsx", ".xls"):
                return pd.read_excel(p)
            elif suffix in (".json", ".jsonl"):
                return pd.read_json(p)
            elif suffix == ".parquet":
                return pd.read_parquet(p)
        raise


def _extract_sql_source(spec: dict[str, Any], db_session: Any | None = None) -> pd.DataFrame:
    query_or_table = (
        spec.get("query_or_table")
        or spec.get("query")
        or spec.get("table")
        or spec.get("table_name")
        or spec.get("sql")
    )
    if not query_or_table:
        raise ValueError("SQL data source requires 'query_or_table' or 'query'")

    session = db_session or spec.get("db_session")
    conn_str = spec.get("connection_string") or spec.get("conn_str") or spec.get("db_url")

    query_str = str(query_or_table).strip()
    is_query = query_str.upper().startswith(("SELECT", "WITH", "EXPLAIN", "SHOW", "EXEC")) or " " in query_str

    if session is not None:
        bind = getattr(session, "bind", session)
        if hasattr(bind, "connect"):
            with bind.connect() as conn:
                if is_query:
                    return pd.read_sql_query(text(query_str), conn)
                return pd.read_sql_table(query_str, conn)
        else:
            if is_query:
                return pd.read_sql_query(text(query_str), bind)
            return pd.read_sql_table(query_str, bind)

    if conn_str:
        engine = create_engine(conn_str)
        with engine.connect() as conn:
            if is_query:
                return pd.read_sql_query(text(query_str), conn)
            return pd.read_sql_table(query_str, conn)

    # SQLite fallback
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        if is_query:
            return pd.read_sql_query(text(query_str), conn)
        return pd.read_sql_table(query_str, conn)


def _extract_athena_source(spec: dict[str, Any]) -> pd.DataFrame:
    query = spec.get("query") or spec.get("query_or_table") or spec.get("sql")
    query_runner = (
        spec.get("query_runner")
        or spec.get("athena_service")
        or spec.get("athena_runtime")
        or spec.get("runner")
    )

    if query_runner is not None:
        if hasattr(query_runner, "run_query"):
            res = query_runner.run_query(query)
        elif hasattr(query_runner, "execute"):
            res = query_runner.execute(query)
        elif callable(query_runner):
            res = query_runner(query)
        else:
            res = query_runner

        if isinstance(res, pd.DataFrame):
            return res
        elif isinstance(res, (list, dict)):
            return pd.DataFrame(res)

    if "df" in spec or "data" in spec or "rows" in spec:
        raw_data = spec.get("df") or spec.get("data") or spec.get("rows")
        return pd.DataFrame(raw_data)

    raise ValueError("AWS Athena data source requires 'query_runner', 'query', or mock data in spec")


def _extract_api_source(spec: dict[str, Any]) -> pd.DataFrame:
    url = spec.get("url") or spec.get("endpoint")
    if not url:
        raise ValueError("API data source requires 'url'")

    method = spec.get("method", "GET").upper()
    headers = spec.get("headers")
    params = spec.get("params")
    json_data = spec.get("json") or spec.get("json_data")
    data_key = spec.get("data_key")
    timeout = spec.get("timeout", 60)

    response = requests.request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        json=json_data,
        timeout=timeout,
    )
    response.raise_for_status()

    try:
        payload = response.json()
        if data_key and isinstance(payload, dict):
            payload = payload.get(data_key, payload)

        if isinstance(payload, (list, dict)):
            return pd.DataFrame(payload)
    except Exception:
        pass

    # Fallback to tabular text / bytes parsing
    return read_tabular(
        content_b64=base64.b64encode(response.content).decode("ascii"),
        combine_sheets=spec.get("combine_sheets", False),
    )


def _extract_sap_bo_source(spec: dict[str, Any]) -> pd.DataFrame:
    snapshot_path = spec.get("snapshot_path") or spec.get("file_path") or spec.get("path")
    content_b64 = spec.get("content_b64") or spec.get("b64_content")

    if snapshot_path or content_b64:
        return _extract_file_source(
            {
                "file_path": snapshot_path,
                "content_b64": content_b64,
                "combine_sheets": spec.get("combine_sheets", True),
            }
        )

    bo_client = spec.get("bo_client") or spec.get("client")
    if bo_client is not None:
        doc_id = spec.get("doc_id") or spec.get("document_id")
        report_id = spec.get("report_id")

        if hasattr(bo_client, "fetch_report_data"):
            res = bo_client.fetch_report_data(doc_id=doc_id, report_id=report_id)
        elif hasattr(bo_client, "download_report"):
            res = bo_client.download_report(doc_id=doc_id, report_id=report_id)
        elif callable(bo_client):
            res = bo_client(spec)
        else:
            res = bo_client

        if isinstance(res, pd.DataFrame):
            return res
        elif isinstance(res, (list, dict)):
            return pd.DataFrame(res)

    if "df" in spec or "data" in spec or "rows" in spec:
        raw_data = spec.get("df") or spec.get("data") or spec.get("rows")
        return pd.DataFrame(raw_data)

    raise ValueError("SAP BO data source requires 'snapshot_path', 'bo_client', or report specification")
