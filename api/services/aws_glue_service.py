from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from api.services.aws_glue_runtime import AwsGlueRuntime
from etl_framework.aws_s3.formats import normalize_schema_type
from etl_framework.repository.repository import ConfigRepository


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

    def _client(self, config_id: int) -> Any:
        return self._runtime.client(config_id, self._glue_client_override)

    def list_databases(self, config_id: int) -> GlueDatabasesResponse:
        client = self._client(config_id)
        if hasattr(client, "get_paginator"):
            pages = client.get_paginator("get_databases").paginate()
            return GlueDatabasesResponse(databases=[d["Name"] for page in pages for d in page.get("DatabaseList", [])])
        data = client.get_databases()
        return GlueDatabasesResponse(databases=[d["Name"] for d in data.get("DatabaseList", [])])

    def list_tables(self, config_id: int, database: str) -> GlueTablesResponse:
        client = self._client(config_id)
        if hasattr(client, "get_paginator"):
            pages = client.get_paginator("get_tables").paginate(DatabaseName=database)
            return GlueTablesResponse(database=database, tables=[t["Name"] for page in pages for t in page.get("TableList", [])])
        data = client.get_tables(DatabaseName=database)
        return GlueTablesResponse(database=database, tables=[t["Name"] for t in data.get("TableList", [])])

    def describe_table(self, config_id: int, database: str, table: str) -> GlueTableResponse:
        raw = self._client(config_id).get_table(DatabaseName=database, Name=table)["Table"]
        return GlueTableResponse(**normalize_glue_table(raw, database))

    def compare_tables(
        self,
        config_id: int,
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
