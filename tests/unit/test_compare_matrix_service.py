from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import DataSourceSpec, MatrixCompareRequest
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
