from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.services.aws_glue_runtime import AwsGlueRuntime
from api.services.aws_glue_service import AwsGlueService, compare_glue_tables, normalize_glue_table, normalize_glue_type
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository


class FakeGlueClient:
    def __init__(self):
        self.databases = [{"Name": "raw"}, {"Name": "curated"}]
        self.tables = {
            ("raw", "orders"): {
                "Name": "orders", "DatabaseName": "raw", "TableType": "EXTERNAL_TABLE",
                "StorageDescriptor": {
                    "Location": "s3://lake/raw/orders/",
                    "InputFormat": "TextInputFormat",
                    "OutputFormat": "HiveIgnoreKeyTextOutputFormat",
                    "Columns": [{"Name": "id", "Type": "int"}, {"Name": "amount", "Type": "decimal(12, 2)"}],
                },
                "PartitionKeys": [{"Name": "dt", "Type": "string"}],
            },
            ("curated", "orders"): {
                "Name": "orders", "DatabaseName": "curated", "TableType": "EXTERNAL_TABLE",
                "StorageDescriptor": {
                    "Location": "s3://lake/curated/orders/",
                    "InputFormat": "ParquetInputFormat",
                    "OutputFormat": "MapredParquetOutputFormat",
                    "Columns": [{"Name": "id", "Type": "bigint"}, {"Name": "status", "Type": "string"}],
                },
                "PartitionKeys": [{"Name": "region", "Type": "string"}],
            },
        }

    def get_databases(self):
        return {"DatabaseList": self.databases}

    def get_tables(self, DatabaseName: str):
        return {"TableList": [{"Name": name} for (db, name) in self.tables if db == DatabaseName]}

    def get_table(self, DatabaseName: str, Name: str):
        return {"Table": self.tables[(DatabaseName, Name)]}


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return self.pages


class FakePaginatedGlueClient(FakeGlueClient):
    def get_paginator(self, operation_name: str):
        if operation_name == "get_databases":
            return FakePaginator([
                {"DatabaseList": [{"Name": "raw"}]},
                {"DatabaseList": [{"Name": "curated"}]},
            ])
        if operation_name == "get_tables":
            return FakePaginator([
                {"TableList": [{"Name": "orders"}]},
                {"TableList": [{"Name": "customers"}]},
            ])
        raise ValueError(operation_name)


@pytest.fixture
def config_repo():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield ConfigRepository(db)


def test_normalize_glue_type_preserves_complex_types_and_aliases():
    assert normalize_glue_type(" INT ") == "int64"
    assert normalize_glue_type("bigint") == "int64"
    assert normalize_glue_type("decimal(12, 2)") == "decimal(12,2)"
    assert normalize_glue_type("array< string >") == "array<string>"


def test_compare_glue_tables_reports_catalog_drift():
    source = normalize_glue_table(FakeGlueClient().tables[("raw", "orders")], "raw")
    target = normalize_glue_table(FakeGlueClient().tables[("curated", "orders")], "curated")
    result = compare_glue_tables(source, target)
    assert result["match"] is False
    assert result["diff"]["missing_columns"] == ["amount"]
    assert result["diff"]["extra_columns"] == ["status"]
    assert result["diff"]["partition_key_mismatches"]
    assert result["diff"]["location_mismatch"] == {"source": "s3://lake/raw/orders/", "target": "s3://lake/curated/orders/"}
    assert result["diff"]["format_mismatch"]


def test_compare_glue_tables_reports_missing_location_mismatch():
    source = normalize_glue_table(FakeGlueClient().tables[("raw", "orders")], "raw")
    target = {**source, "location": None}
    result = compare_glue_tables(source, target)
    assert result["match"] is False
    assert result["diff"]["location_mismatch"] == {"source": "s3://lake/raw/orders/", "target": None}


def test_compare_glue_tables_reports_incompatible_shared_column_types():
    source = {"columns": [{"name": "id", "type": "int64"}], "partition_keys": []}
    target = {"columns": [{"name": "id", "type": "string"}], "partition_keys": []}
    result = compare_glue_tables(source, target, compare_location=False, compare_formats=False)
    assert result["match"] is False
    assert result["diff"]["type_mismatches"] == [{"column": "id", "expected_type": "int64", "actual_type": "string"}]


def test_glue_service_lists_and_compares_tables(config_repo):
    cfg = config_repo.create("aws", "dev", {"aws_region": "us-east-1"})
    service = AwsGlueService(config_repo)
    service._glue_client_override = FakeGlueClient()
    assert service.list_databases(cfg.id).databases == ["raw", "curated"]
    assert service.list_tables(cfg.id, "raw").tables == ["orders"]
    described = service.describe_table(cfg.id, "raw", "orders")
    assert described.table == "orders"
    compared = service.compare_tables(cfg.id, "raw", "orders", "curated", "orders")
    assert compared.match is False
    assert compared.diff["missing_columns"] == ["amount"]


def test_glue_service_lists_with_paginators(config_repo):
    cfg = config_repo.create("aws", "dev", {"aws_region": "us-east-1"})
    service = AwsGlueService(config_repo)
    service._glue_client_override = FakePaginatedGlueClient()
    assert service.list_databases(cfg.id).databases == ["raw", "curated"]
    assert service.list_tables(cfg.id, "raw").tables == ["orders", "customers"]


def test_glue_runtime_missing_config_maps_to_404(config_repo):
    with pytest.raises(HTTPException) as err:
        AwsGlueRuntime(config_repo).env(999)
    assert err.value.status_code == 404


def test_glue_runtime_resolves_named_config(config_repo):
    cfg = config_repo.create("aws-dev", "dev", {"db_host": "localhost", "db_password": "unused", "aws_region": "us-east-1"})
    runtime = AwsGlueRuntime(config_repo)
    assert runtime.config_id("aws-dev") == cfg.id
    assert runtime.env("aws-dev").name == "dev"
