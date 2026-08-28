import base64
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from etl_framework.reconciliation.data_sources import extract_data_source


def test_extract_local_file_source(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("id,val\n1,100\n2,200\n", encoding="utf-8")

    spec = {
        "source_type": "file",
        "file_path": str(csv_file),
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert list(df.columns) == ["id", "val"]
    assert df.iloc[0]["id"] == 1
    assert df.iloc[0]["val"] == 100


def test_extract_base64_file_source():
    raw_csv = b"id,val\n10,500\n"
    b64_str = base64.b64encode(raw_csv).decode("ascii")

    spec = {
        "source_type": "file",
        "content_b64": b64_str,
        "file_name": "data.csv",
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert list(df.columns) == ["id", "val"]
    assert df.iloc[0]["id"] == 10


def test_extract_sql_query_fallback():
    spec = {
        "source_type": "sql",
        "query_or_table": "SELECT 1 as id, 'A' as label",
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert list(df.columns) == ["id", "label"]
    assert df.iloc[0]["id"] == 1
    assert df.iloc[0]["label"] == "A"


def test_extract_sql_session_and_table():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE test_table (id INT, name TEXT)"))
        conn.execute(text("INSERT INTO test_table VALUES (1, 'Alice'), (2, 'Bob')"))

    Session = sessionmaker(bind=engine)
    session = Session()

    spec = {
        "source_type": "sql",
        "query_or_table": "test_table",
    }
    df = extract_data_source(spec, db_session=session)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert list(df.columns) == ["id", "name"]
    session.close()


def test_extract_sql_connection_string():
    spec = {
        "source_type": "sql",
        "query_or_table": "SELECT 42 as answer",
        "connection_string": "sqlite:///:memory:",
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["answer"] == 42


def test_extract_aws_athena_mock_runner():
    mock_runner = MagicMock()
    mock_runner.run_query.return_value = pd.DataFrame([{"col_a": "x", "col_b": 10}])

    spec = {
        "source_type": "aws_athena",
        "query": "SELECT * FROM athena_db.athena_table",
        "query_runner": mock_runner,
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert list(df.columns) == ["col_a", "col_b"]
    mock_runner.run_query.assert_called_once_with("SELECT * FROM athena_db.athena_table")


def test_extract_api_source():
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {"item": "A", "price": 10.5},
        {"item": "B", "price": 20.0},
    ]

    with patch("requests.request", return_value=mock_response) as mock_req:
        spec = {
            "source_type": "api",
            "url": "https://api.example.com/items",
            "method": "GET",
        }
        df = extract_data_source(spec)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == ["item", "price"]
        mock_req.assert_called_once()


def test_extract_api_source_nested_key():
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "success",
        "records": [{"code": 100}, {"code": 200}],
    }

    with patch("requests.request", return_value=mock_response):
        spec = {
            "source_type": "api",
            "url": "https://api.example.com/data",
            "data_key": "records",
        }
        df = extract_data_source(spec)
        assert len(df) == 2
        assert list(df.columns) == ["code"]


def test_extract_sap_bo_mock_client():
    mock_client = MagicMock()
    mock_client.fetch_report_data.return_value = pd.DataFrame([{"rpt_col": "val1"}])

    spec = {
        "source_type": "sap_bo",
        "doc_id": "doc_123",
        "report_id": "rpt_1",
        "bo_client": mock_client,
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["rpt_col"] == "val1"


def test_extract_sap_bo_snapshot(tmp_path):
    bo_file = tmp_path / "bo_export.csv"
    bo_file.write_text("bo_id,metric\nB1,999\n", encoding="utf-8")

    spec = {
        "source_type": "sap_bo",
        "snapshot_path": str(bo_file),
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["bo_id"] == "B1"


def test_extract_invalid_source_type():
    with pytest.raises(ValueError, match="Unsupported source_type"):
        extract_data_source({"source_type": "unknown_type"})
