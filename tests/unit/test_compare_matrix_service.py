from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import DataSourceSpec, MatrixCompareRequest
from etl_framework.reconciliation.data_sources import extract_data_source
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository, RunRepository


def _make_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_run_matrix_comparison_success_matching_files(tmp_path) -> None:
    from api.services.compare_service import CompareService

    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    file_a.write_text("id,val,num\n1,alpha,10.0\n2,beta,20.0\n", encoding="utf-8")
    file_b.write_text("id,val,num\n1,alpha,10.0\n2,beta,20.0\n", encoding="utf-8")

    db = _make_db()
    try:
        run_id = "run-matrix-success"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="matrix")

        req = MatrixCompareRequest(
            source_a=DataSourceSpec(source_type="file", file_path=str(file_a)),
            source_b=DataSourceSpec(source_type="file", file_path=str(file_b)),
            key_columns=["id"],
            label_a="Source CSV A",
            label_b="Source CSV B",
        )
        svc = CompareService(db, ConfigRepository(db))
        svc.run_matrix_comparison(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "PASSED"
        assert run.total_tests == 1
        assert run.passed == 1
        assert run.failed == 0
        assert len(run.results) == 1
        assert run.results[0].status == "PASSED"
    finally:
        db.close()


def test_run_matrix_comparison_mismatch_files(tmp_path) -> None:
    from api.services.compare_service import CompareService

    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    file_a.write_text("id,val,num\n1,alpha,10.0\n2,beta,20.0\n", encoding="utf-8")
    file_b.write_text("id,val,num\n1,alpha,10.0\n2,GAMMA,20.0\n", encoding="utf-8")

    db = _make_db()
    try:
        run_id = "run-matrix-mismatch"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="matrix")

        req = MatrixCompareRequest(
            source_a=DataSourceSpec(source_type="file", file_path=str(file_a)),
            source_b=DataSourceSpec(source_type="file", file_path=str(file_b)),
            key_columns=["id"],
        )
        svc = CompareService(db, ConfigRepository(db))
        svc.run_matrix_comparison(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "FAILED"
        assert run.total_tests == 1
        assert run.passed == 0
        assert run.failed == 1
        assert len(run.results) == 1
        assert run.results[0].status == "FAILED"
        assert run.results[0].value_mismatch_count > 0
    finally:
        db.close()


def test_run_matrix_comparison_ignore_case_and_tolerance(tmp_path) -> None:
    from api.services.compare_service import CompareService

    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    file_a.write_text("id,val,num\n1,ALPHA,10.001\n", encoding="utf-8")
    file_b.write_text("id,val,num\n1,alpha,10.002\n", encoding="utf-8")

    db = _make_db()
    try:
        run_id = "run-matrix-options"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="matrix")

        req = MatrixCompareRequest(
            source_a=DataSourceSpec(source_type="file", file_path=str(file_a)),
            source_b=DataSourceSpec(source_type="file", file_path=str(file_b)),
            key_columns=["id"],
            numeric_tolerance=0.01,
            ignore_case=True,
            trim_whitespace=True,
        )
        svc = CompareService(db, ConfigRepository(db))
        svc.run_matrix_comparison(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "PASSED"
        assert run.passed == 1
    finally:
        db.close()


def test_run_matrix_comparison_invalid_source_persists_error(tmp_path) -> None:
    from api.services.compare_service import CompareService

    db = _make_db()
    try:
        run_id = "run-matrix-error"
        RunRepository(db).create_run(run_id=run_id, source_env="Source A", target_env="Source B", run_type="matrix")

        req = MatrixCompareRequest(
            source_a=DataSourceSpec(source_type="file", file_path=str(tmp_path / "nonexistent.csv")),
            source_b=DataSourceSpec(source_type="file", file_path=str(tmp_path / "nonexistent2.csv")),
        )
        svc = CompareService(db, ConfigRepository(db))
        with pytest.raises(Exception):
            svc.run_matrix_comparison(req, run_id)

        run = RunRepository(db).get_run(run_id)
        assert run.status == "ERROR"
        assert run.error == 1
    finally:
        db.close()


def test_data_source_spec_accepts_athena_and_glue_resolution_fields() -> None:
    spec = DataSourceSpec(
        source_type="aws_athena",
        config_id=1,
        query_or_table="SELECT * FROM db.tbl",
        athena_database="db",
        athena_output_location="s3://bucket/athena-output/",
        athena_workgroup="primary",
    )
    assert spec.athena_database == "db"
    assert spec.athena_output_location == "s3://bucket/athena-output/"
    assert spec.athena_workgroup == "primary"

    glue_spec = DataSourceSpec(
        source_type="aws_glue",
        config_id=1,
        glue_database="raw",
        glue_table="orders",
    )
    assert glue_spec.glue_database == "raw"
    assert glue_spec.glue_table == "orders"


class _FakeAthenaClient:
    """Mirrors tests/unit/test_aws_athena_service.py's FakeAthenaClient, trimmed
    to a single-page, always-SUCCEEDED result so these tests stay focused on
    _resolve_source_spec's wiring rather than re-testing AwsAthenaService itself."""

    def __init__(self, rows: list[dict[str, str]]):
        self._rows = rows

    def start_query_execution(self, **kwargs):
        self.output_location = kwargs["ResultConfiguration"]["OutputLocation"]
        self.database = (kwargs.get("QueryExecutionContext") or {}).get("Database")
        self.workgroup = kwargs.get("WorkGroup")
        return {"QueryExecutionId": "qid-1"}

    def get_query_execution(self, QueryExecutionId: str):
        return {"QueryExecution": {"QueryExecutionId": QueryExecutionId, "Status": {"State": "SUCCEEDED"}, "Statistics": {}}}

    def get_query_results(self, QueryExecutionId: str, MaxResults: int = 100, NextToken: str | None = None):
        header = {"Data": [{"VarCharValue": k} for k in self._rows[0].keys()]}
        body = [{"Data": [{"VarCharValue": str(v)} for v in row.values()]} for row in self._rows]
        return {"ResultSet": {"Rows": [header, *body]}}


def test_resolve_source_spec_builds_real_athena_query_runner_from_config_id(tmp_path) -> None:
    from api.services.compare_service import CompareService

    db = _make_db()
    try:
        config_repo = ConfigRepository(db)
        cfg = config_repo.create("aws-athena-e2e", "dev", {"aws_region": "us-east-1"})
        svc = CompareService(db, config_repo)

        fake_client = _FakeAthenaClient([{"id": "1", "amount": "10.0"}])
        import api.services.aws_athena_service as athena_service_module
        original_client = athena_service_module.AwsAthenaService._client
        athena_service_module.AwsAthenaService._client = lambda self, config_id: fake_client
        try:
            spec = {
                "source_type": "aws_athena",
                "config_id": cfg.id,
                "query_or_table": "SELECT * FROM raw.orders",
                "athena_database": "raw",
                "athena_output_location": "s3://bucket/athena-output/",
                "athena_workgroup": "primary",
            }
            resolved = svc._resolve_source_spec(spec)
            assert "query_runner" in resolved
            df = extract_data_source(resolved)
            assert list(df.columns) == ["id", "amount"]
            assert df.iloc[0]["id"] == "1"
            assert fake_client.database == "raw"
            assert fake_client.output_location == "s3://bucket/athena-output/"
            assert fake_client.workgroup == "primary"
        finally:
            athena_service_module.AwsAthenaService._client = original_client
    finally:
        db.close()


def test_resolve_source_spec_athena_requires_output_location() -> None:
    from api.services.compare_service import CompareService

    db = _make_db()
    try:
        config_repo = ConfigRepository(db)
        cfg = config_repo.create("aws-athena-e2e-2", "dev", {"aws_region": "us-east-1"})
        svc = CompareService(db, config_repo)
        spec = {"source_type": "aws_athena", "config_id": cfg.id, "query_or_table": "SELECT 1"}
        with pytest.raises(HTTPException) as err:
            svc._resolve_source_spec(spec)
        assert err.value.status_code == 422
        assert "athena_output_location" in err.value.detail
    finally:
        db.close()


def test_resolve_source_spec_builds_rows_from_glue_table(tmp_path) -> None:
    from api.services.compare_service import CompareService

    csv_bytes = b"id,amount\n1,10.0\n2,20.0\n"

    db = _make_db()
    try:
        config_repo = ConfigRepository(db)
        cfg = config_repo.create("aws-glue-e2e", "dev", {"aws_region": "us-east-1"})
        svc = CompareService(db, config_repo)

        fake_glue_client = MagicMock()
        fake_glue_client.get_table.return_value = {
            "Table": {
                "Name": "orders",
                "StorageDescriptor": {
                    "Location": "s3://bucket/raw/orders/",
                    "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
                    "Columns": [{"Name": "id", "Type": "int"}, {"Name": "amount", "Type": "double"}],
                },
                "PartitionKeys": [],
            }
        }

        class _FakeS3Boto:
            def get_paginator(self, name):
                assert name == "list_objects_v2"
                class _P:
                    def paginate(self, Bucket, Prefix):
                        return [{"Contents": [{"Key": "raw/orders/part-0.csv"}]}]
                return _P()

            def get_object(self, Bucket, Key):
                assert Bucket == "bucket"
                assert Key == "raw/orders/part-0.csv"
                return {"Body": MagicMock(read=lambda: csv_bytes)}

        import api.services.aws_glue_service as glue_service_module
        import api.services.aws_s3_runtime as s3_runtime_module
        original_glue_client = glue_service_module.AwsGlueService._client
        original_s3_client = s3_runtime_module.AwsS3Runtime.client
        glue_service_module.AwsGlueService._client = lambda self, config_id: fake_glue_client

        def _client_with_fake_s3(self, config_id, override=None):
            from etl_framework.aws_s3.client import S3Client
            c = S3Client.__new__(S3Client)
            c._s3 = _FakeS3Boto()
            return c

        s3_runtime_module.AwsS3Runtime.client = _client_with_fake_s3
        try:
            spec = {
                "source_type": "aws_glue",
                "config_id": cfg.id,
                "glue_database": "raw",
                "glue_table": "orders",
            }
            resolved = svc._resolve_source_spec(spec)
            assert "rows" in resolved
            df = extract_data_source(resolved)
            assert list(df.columns) == ["id", "amount"]
            assert df.iloc[0]["id"] == 1
            assert df.iloc[1]["amount"] == 20.0
        finally:
            glue_service_module.AwsGlueService._client = original_glue_client
            s3_runtime_module.AwsS3Runtime.client = original_s3_client
    finally:
        db.close()


def test_resolve_source_spec_glue_unrecognized_input_format_raises_422() -> None:
    """An input_format outside the known parquet/orc/json/text set (e.g. Avro)
    must raise a clear 422 rather than silently falling back to a CSV parse
    that would either error opaquely on binary bytes or, worse, "succeed" with
    garbage data.
    """
    from api.services.compare_service import CompareService

    db = _make_db()
    try:
        config_repo = ConfigRepository(db)
        cfg = config_repo.create("aws-glue-e2e-avro", "dev", {"aws_region": "us-east-1"})
        svc = CompareService(db, config_repo)

        fake_glue_client = MagicMock()
        fake_glue_client.get_table.return_value = {
            "Table": {
                "Name": "orders",
                "StorageDescriptor": {
                    "Location": "s3://bucket/raw/orders/",
                    "InputFormat": "org.apache.hadoop.hive.ql.io.avro.AvroContainerInputFormat",
                    "Columns": [{"Name": "id", "Type": "int"}, {"Name": "amount", "Type": "double"}],
                },
                "PartitionKeys": [],
            }
        }

        fake_s3_client = MagicMock()

        import api.services.aws_glue_service as glue_service_module
        import api.services.aws_s3_runtime as s3_runtime_module
        original_glue_client = glue_service_module.AwsGlueService._client
        original_s3_client = s3_runtime_module.AwsS3Runtime.client
        glue_service_module.AwsGlueService._client = lambda self, config_id: fake_glue_client
        s3_runtime_module.AwsS3Runtime.client = lambda self, config_id, override=None: fake_s3_client
        try:
            spec = {
                "source_type": "aws_glue",
                "config_id": cfg.id,
                "glue_database": "raw",
                "glue_table": "orders",
            }
            with pytest.raises(HTTPException) as err:
                svc._resolve_source_spec(spec)
            assert err.value.status_code == 422
            assert "Avro" in err.value.detail
            fake_s3_client.get_object.assert_not_called()
        finally:
            glue_service_module.AwsGlueService._client = original_glue_client
            s3_runtime_module.AwsS3Runtime.client = original_s3_client
    finally:
        db.close()
