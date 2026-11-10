"""A Matrix SQL source must read the database it names, never ATOM's own."""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.schemas import DataSourceSpec, MatrixCompareRequest
from api.services.compare_service import CompareService
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository

_CONFIG_JSON = {
    "db_type": "postgresql",
    "db_host": "default-host",
    "db_port": 5432,
    "db_name": "default_db",
    "db_user": "reader",
    "db_password": "secret",
    "connections": {"reporting": {"db_host": "reporting-host", "db_name": "reporting_db"}},
}


class _FakeDBEngine:
    """Stands in for DBEngine, recording which connection each query reached."""

    calls: list = []

    def __init__(self, env_config) -> None:
        self._host = env_config.db_host

    def execute_query(self, query, params=None):
        _FakeDBEngine.calls.append((self._host, query))
        return pd.DataFrame({"id": [1], "source": [self._host]})

    def dispose(self) -> None:
        pass


@pytest.fixture
def fake_db_engine(monkeypatch):
    _FakeDBEngine.calls = []
    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _FakeDBEngine)
    return _FakeDBEngine


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def config_id(db):
    return ConfigRepository(db).create("qa", "qa", dict(_CONFIG_JSON)).id


def _service(db) -> CompareService:
    return CompareService(db, ConfigRepository(db))


def test_reads_the_saved_config_and_named_connection(db, config_id, fake_db_engine) -> None:
    req = MatrixCompareRequest(
        source_a=DataSourceSpec(source_type="sql", config_id=config_id, query_or_table="SELECT id, source FROM a"),
        source_b=DataSourceSpec(
            source_type="sql", config_id=config_id, connection_name="reporting",
            query_or_table="SELECT id, source FROM b",
        ),
        key_columns=["id"],
    )

    df_a, df_b = _service(db)._load_matrix_frames(req)

    assert df_a["source"].tolist() == ["default-host"]
    assert df_b["source"].tolist() == ["reporting-host"]
    assert fake_db_engine.calls == [
        ("default-host", "SELECT id, source FROM a"),
        ("reporting-host", "SELECT id, source FROM b"),
    ]


def test_accepts_a_bare_table_name(db, config_id, fake_db_engine) -> None:
    spec = {"source_type": "sql", "config_id": config_id, "query_or_table": "sales.orders"}

    _service(db)._resolve_source_spec(spec)

    assert fake_db_engine.calls == [("default-host", "SELECT * FROM sales.orders")]


def test_without_a_connection_is_refused(db, fake_db_engine) -> None:
    """It used to run against ATOM's own database, tokens table included."""
    spec = {"source_type": "sql", "query_or_table": "SELECT * FROM api_tokens"}

    with pytest.raises(HTTPException) as exc:
        _service(db)._resolve_source_spec(spec)

    assert exc.value.status_code == 422
    assert "config_id" in exc.value.detail
    assert fake_db_engine.calls == []


@pytest.mark.parametrize(
    ("overrides", "status"),
    [
        ({"config_id": 9999}, 404),
        ({"connection_name": "no_such_connection"}, 422),
        ({"query_or_table": "DELETE FROM orders"}, 422),
        ({"query_or_table": None}, 422),
        ({"query_or_table": "orders; DROP TABLE orders"}, 422),
    ],
)
def test_rejects_bad_input(db, config_id, fake_db_engine, overrides, status) -> None:
    spec = {"source_type": "sql", "config_id": config_id, "query_or_table": "SELECT 1", **overrides}

    with pytest.raises(HTTPException) as exc:
        _service(db)._resolve_source_spec(spec)

    assert exc.value.status_code == status
    assert fake_db_engine.calls == []


def test_connection_string_still_works(db, tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'data.db').as_posix()}"
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INT)"))
        conn.execute(text("INSERT INTO t VALUES (7)"))
    engine.dispose()
    req = MatrixCompareRequest(
        source_a=DataSourceSpec(source_type="sql", query_or_table="SELECT id FROM t", connection_string=url),
        source_b=DataSourceSpec(source_type="sql", query_or_table="SELECT id FROM t", connection_string=url),
        key_columns=["id"],
    )

    df_a, _ = _service(db)._load_matrix_frames(req)

    assert df_a["id"].tolist() == [7]


def test_difference_export_reads_from_the_saved_config(db, config_id, fake_db_engine, monkeypatch) -> None:
    import api.services.difference_export as export_module

    captured = {}
    monkeypatch.setattr(
        export_module, "_write_tabular_differences",
        lambda df_a, df_b, **kwargs: captured.update(a=df_a, b=df_b),
    )
    payload = {
        "source_a": {"source_type": "sql", "config_id": config_id, "query_or_table": "SELECT 1"},
        "source_b": {"source_type": "sql", "config_id": config_id, "query_or_table": "SELECT 2"},
        "key_columns": ["id"],
    }

    export_module._write_matrix_compare(db, payload, MagicMock())

    assert captured["a"]["source"].tolist() == ["default-host"]
    assert len(fake_db_engine.calls) == 2
