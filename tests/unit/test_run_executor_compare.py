"""RunExecutor dispatch for the `compare` job type."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor


def _session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _executor(db) -> RunExecutor:
    return RunExecutor(
        db=db,
        run_id="run-1",
        source_env="Source A",
        target_env="Source B",
        job_sequence=[],
        run_settings=RunSettings(),
        config_snapshot={},
    )


def _allow(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))


def test_compare_job_runs_a_bo_compare_and_names_the_result_after_the_job(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": {
            "source_a": {"source_type": "path", "file_path": str(tmp_path / "a.csv")},
            "source_b": {"source_type": "path", "file_path": str(tmp_path / "b.csv")},
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_compare"
    assert result.value_mismatch_count == 1


def test_compare_job_runs_a_recon_file_compare(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,alpha\n2,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_file_diff",
        job_type="compare",
        params={"compare_type": "recon_file", "request": {
            "file_a_path": str(tmp_path / "a.csv"),
            "file_b_path": str(tmp_path / "b.csv"),
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_file_diff"
    assert result.status.value == "PASSED"


def test_compare_job_runs_a_matrix_compare_and_names_the_result_after_the_job(tmp_path, monkeypatch):
    _allow(tmp_path, monkeypatch)
    (tmp_path / "a.csv").write_text("id,value\n1,alpha\n", encoding="utf-8")
    (tmp_path / "b.csv").write_text("id,value\n1,beta\n", encoding="utf-8")

    job = JobDefinition(
        name="nightly_matrix",
        job_type="compare",
        params={"compare_type": "matrix", "request": {
            "source_a": {"source_type": "file", "file_path": str(tmp_path / "a.csv")},
            "source_b": {"source_type": "file", "file_path": str(tmp_path / "b.csv")},
            "key_columns": ["id"],
        }},
    )

    result = _executor(_session())._build_case(job)()

    assert result.query_name == "nightly_matrix"
    assert result.value_mismatch_count == 1


def test_compare_job_with_an_unknown_compare_type_raises():
    job = JobDefinition.model_construct(
        name="broken",
        job_type="compare",
        description="",
        tags=[],
        query="",
        key_columns=[],
        exclude_columns=[],
        source_env=None,
        target_env=None,
        params={"compare_type": "bogus", "request": {}},
        enabled=True,
        rules=[],
        depends_on=[],
        pass_condition=None,
    )

    with pytest.raises(ValueError, match="unknown compare_type"):
        _executor(_session())._build_case(job)()


def test_compare_job_with_live_source_respects_live_connections_setting():
    job = JobDefinition(
        name="nightly_compare",
        job_type="compare",
        params={"compare_type": "bo", "request": {
            "source_a": {"source_type": "live", "config_id": 1, "doc_id": "doc-1"},
            "source_b": {"source_type": "path", "file_path": "/data/b.csv"},
            "key_columns": ["id"],
        }},
    )

    with pytest.raises(ValueError, match="live connections"):
        _executor(_session())._build_case(job)()


def test_compare_job_with_matrix_live_source_respects_live_connections_setting():
    job = JobDefinition(
        name="nightly_matrix",
        job_type="compare",
        params={"compare_type": "matrix", "request": {
            "source_a": {"source_type": "sql", "config_id": 1, "query_or_table": "SELECT * FROM t"},
            "source_b": {"source_type": "file", "file_path": "/data/b.csv"},
            "key_columns": ["id"],
        }},
    )

    with pytest.raises(ValueError, match="live connections"):
        _executor(_session())._build_case(job)()


class _FrameDBEngine:
    """Stands in for DBEngine: serves a DataFrame per db_name, no batching."""

    frames: dict = {}

    def __init__(self, env) -> None:
        self._env = env
        self._frame = _FrameDBEngine.frames[env.db_name]

    def execute_query(self, query: str, params=None):
        return self._frame.copy()

    def dispose(self) -> None:
        pass


def _seed_sql_configs(db) -> tuple[int, int]:
    from etl_framework.repository.repository import ConfigRepository

    repo = ConfigRepository(db)
    base = {"db_host": "h", "db_user": "u", "db_password": "p"}
    a = repo.create("src_cfg", "dev", {**base, "db_type": "mssql", "db_name": "src"})
    b = repo.create("tgt_cfg", "dev", {**base, "db_type": "postgresql", "db_name": "tgt"})
    return a.id, b.id


def _live_executor(db) -> RunExecutor:
    return RunExecutor(
        db=db, run_id="run-1", source_env="Source A", target_env="Source B",
        job_sequence=[], run_settings=RunSettings(use_live_connections=True), config_snapshot={},
    )


def test_compare_job_runs_a_sql_compare_and_names_the_result_after_the_job(monkeypatch):
    import pandas as pd

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _FrameDBEngine)
    _FrameDBEngine.frames = {
        "src": pd.DataFrame({"id": [1, 2], "amount": [10.0, 20.0]}),
        "tgt": pd.DataFrame({"id": [1, 2], "amount": [10.0, 25.0]}),
    }
    db = _session()
    id_a, id_b = _seed_sql_configs(db)
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": {
            "config_id_a": id_a, "config_id_b": id_b,
            "query_a": "SELECT id, amount FROM dbo.orders",
            "query_b": "SELECT id, amount FROM public.orders",
            "key_columns": ["id"],
        }},
    )

    result = _live_executor(db)._build_case(job)()

    assert result.query_name == "orders_cmp"
    assert result.value_mismatch_count == 1


def test_compare_sql_aligns_column_name_case_between_sides(monkeypatch):
    import pandas as pd

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _FrameDBEngine)
    _FrameDBEngine.frames = {
        "src": pd.DataFrame({"order_id": [1, 2], "amount": [10.0, 20.0]}),
        "tgt": pd.DataFrame({"ORDER_ID": [1, 2], "AMOUNT": [10.0, 25.0]}),
    }
    db = _session()
    id_a, id_b = _seed_sql_configs(db)
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": {
            "config_id_a": id_a, "config_id_b": id_b,
            "query_a": "SELECT order_id, amount FROM t",
            "query_b": "SELECT order_id, amount FROM t",
            "key_columns": ["ORDER_ID"],
        }},
    )

    result = _live_executor(db)._build_case(job)()

    assert result.value_mismatch_count == 1


def test_align_column_case_skips_renames_that_would_collide():
    import pandas as pd
    from api.services.compare_service import _align_column_case

    df = pd.DataFrame({"a": [1], "A": [2]})
    assert list(_align_column_case(df, ["A"]).columns) == ["a", "A"]
    df2 = pd.DataFrame({"a": [1], "b": [2]})
    assert list(_align_column_case(df2, ["A", "B"]).columns) == ["A", "B"]


def test_compare_sql_job_requires_live_connections():
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": {
            "config_id_a": 1, "config_id_b": 2,
            "query_a": "SELECT 1 AS id", "query_b": "SELECT 1 AS id",
        }},
    )
    with pytest.raises(ValueError, match="live connections"):
        _executor(_session())._build_case(job)()


def test_compare_sql_job_rejects_mutating_query_before_building_engines(monkeypatch):
    """A stored job whose request bypassed save-time validation is still guarded at run time."""
    def _explode(*args, **kwargs):
        raise AssertionError("DBEngine must not be constructed for a mutating query")

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _explode)
    db = _session()
    id_a, id_b = _seed_sql_configs(db)
    job = JobDefinition.model_construct(
        name="bad_cmp",
        job_type="compare",
        description="",
        tags=[],
        query="",
        key_columns=[],
        exclude_columns=[],
        source_env=None,
        target_env=None,
        params={"compare_type": "sql", "request": {
            "config_id_a": id_a, "config_id_b": id_b,
            "query_a": "SELECT id FROM dbo.orders",
            "query_b": "SELECT 1; DROP TABLE x",
        }},
        enabled=True,
        rules=[],
        depends_on=[],
        pass_condition=None,
    )

    with pytest.raises(ValueError, match="read-only SELECT or WITH"):
        _live_executor(db)._build_case(job)()


def test_compare_sql_job_with_missing_config_raises(monkeypatch):
    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _FrameDBEngine)
    job = JobDefinition(
        name="orders_cmp", job_type="compare",
        params={"compare_type": "sql", "request": {
            "config_id_a": 999, "config_id_b": 998,
            "query_a": "SELECT 1 AS id", "query_b": "SELECT 1 AS id",
        }},
    )
    with pytest.raises(Exception, match="Config A not found"):
        _live_executor(_session())._build_case(job)()
