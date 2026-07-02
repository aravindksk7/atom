from __future__ import annotations
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository
from api.main import app
from api.routes import runs as runs_module


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *a, **kw: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


def test_bo_compare_rejects_bad_source(client):
    resp = client.post("/api/compare/bo-report", json={
        "source_a": {"source_type": "live"},   # missing config_id → 422
        "source_b": {"source_type": "path", "file_path": "/tmp/x.csv"},
    })
    assert resp.status_code == 422


def test_bo_compare_upload_returns_202(client, monkeypatch, tmp_path):
    import base64, io, pandas as pd
    buf = io.BytesIO()
    pd.DataFrame({"id": [1], "v": [1]}).to_csv(buf, index=False)
    b64a = base64.b64encode(buf.getvalue()).decode()
    buf2 = io.BytesIO()
    pd.DataFrame({"id": [1], "v": [1]}).to_csv(buf2, index=False)
    b64b = base64.b64encode(buf2.getvalue()).decode()

    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_run_bo_bg", lambda *a, **kw: None)

    resp = client.post("/api/compare/bo-report", json={
        "source_a": {"source_type": "upload", "file_content_b64": b64a, "file_name": "a.csv"},
        "source_b": {"source_type": "upload", "file_content_b64": b64b, "file_name": "b.csv"},
        "key_columns": ["id"],
        "label_a": "Env A", "label_b": "Env B",
    })
    assert resp.status_code == 202
    data = resp.json()
    assert "run_id" in data
    assert data["run_type"] == "bo_comparison"


def test_bo_live_source_downloads_selected_doc_report():
    from api.schemas import SourceConfig
    from api.services.compare_service import CompareService

    cfg_repo = MagicMock()
    cfg_repo.get.return_value = SimpleNamespace(
        env_name="bo-dev",
        config_json={
            "db_host": "localhost",
            "db_password": "secret",
            "bo_url": "http://bo.example",
            "bo_user": "bo_user",
            "bo_password": "bo_password",
        },
    )
    src = SourceConfig(
        source_type="live",
        config_id=1,
        doc_id="DOC-A",
        report_id="RPT-1",
        format="csv",
    )
    mock_client = MagicMock()
    mock_client.download_report.return_value = b"id,value\n1,ok\n"

    with patch("etl_framework.sap_bo.client.BORestClient", return_value=mock_client):
        df = CompareService(MagicMock(), cfg_repo)._load_bo_source(src, "OLD-DOC", "OLD-RPT")

    mock_client.download_report.assert_called_once_with("DOC-A", "RPT-1", "csv")
    assert df.to_dict("records") == [{"id": 1, "value": "ok"}]


def test_bo_compare_error_records_error_result_with_sapbo_body():
    from api.schemas import BOCompareRequest, SourceConfig
    from api.services.compare_service import CompareService
    from etl_framework.exceptions import BOAPIError
    from etl_framework.repository.repository import RunRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        repo = RunRepository(db)
        repo.create_run("bo-error-run", "Mock A", "Mock B", run_type="bo_comparison")
        service = CompareService(db, MagicMock())
        service._load_bo_source = MagicMock(
            side_effect=BOAPIError("rpt-missing", 404, '{"error":"report not found"}')
        )
        request = BOCompareRequest(
            source_a=SourceConfig(source_type="live", config_id=1, doc_id="1001", report_id="rpt-missing"),
            source_b=SourceConfig(source_type="live", config_id=1, doc_id="1001", report_id="rpt-sales"),
            label_a="Mock A",
            label_b="Mock B",
        )

        with pytest.raises(BOAPIError):
            service.run_bo_comparison(request, "bo-error-run")

        db.expire_all()
        run = repo.get_run("bo-error-run")
        assert run.status == "ERROR"
        assert run.total_tests == 1
        assert run.error == 1
        assert len(run.results) == 1
        assert run.results[0].status == "ERROR"
        assert "report not found" in run.results[0].error_message


def test_bo_compare_infers_employee_id_key_for_files():
    import pandas as pd
    from api.services.compare_service import CompareService

    df_a = pd.DataFrame({
        "Employee ID": ["EM1092", "EM1432"],
        "Total Revenue": [7500, 2400],
    })
    df_b = pd.DataFrame({
        "Employee ID": ["EM1092", "EM1432"],
        "Department": ["IT", "IT"],
    })

    assert CompareService._infer_key_columns(df_a, df_b) == ["Employee ID"]


def test_recon_compare_requires_exactly_one_source_per_side():
    from pydantic import ValidationError
    from api.schemas import ReconFileCompareRequest

    valid = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")
    assert valid.stored_run_id_b == "run-b"

    with pytest.raises(ValidationError, match="Source A requires exactly one"):
        ReconFileCompareRequest(
            stored_run_id="run-a",
            file_a_path="a.html",
            stored_run_id_b="run-b",
        )


def test_recon_html_parser_reads_report_metrics_from_correct_columns():
    from api.services.compare_service import CompareService

    html = """
    <table><tbody><tr>
      <td>orders</td><td>PASSED</td><td>0.25s</td>
      <td>1,200</td><td>1,199</td><td>3</td>
    </tr></tbody></table>
    """
    assert CompareService._parse_html_report(html)["orders"] == {
        "status": "PASSED",
        "source_row_count": 1200,
        "target_row_count": 1199,
        "total_issues": 3,
    }


@pytest.mark.parametrize(
    ("rows_b", "expected_status"),
    [(10, "PASSED"), (11, "FAILED")],
)
def test_recon_stored_runs_compare_status_and_metrics(rows_b, expected_status):
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    result_a = SimpleNamespace(
        query_name="orders", effective_status="FAILED",
        source_row_count=10, target_row_count=9, total_issues=1,
    )
    result_b = SimpleNamespace(
        query_name="orders", effective_status="FAILED",
        source_row_count=rows_b, target_row_count=9, total_issues=1,
    )
    repo = MagicMock()
    repo.get_run.side_effect = [
        SimpleNamespace(results=[result_a]),
        SimpleNamespace(results=[result_b]),
    ]
    service = CompareService.__new__(CompareService)
    service._repo = repo
    request = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")

    with patch("api.services.compare_service.MetricsWriter.write"):
        service.run_recon_file_compare(request, "comparison-run")

    comparison_result = repo.add_test_result.call_args.args[1]
    assert comparison_result.status.value == expected_status


def test_recon_stored_runs_compare_reports_correct_target_row_count():
    """The synthetic result's target_row_count must come from Source B's
    target_row_count, not its source_row_count."""
    from api.schemas import ReconFileCompareRequest
    from api.services.compare_service import CompareService

    result_a = SimpleNamespace(
        query_name="orders", effective_status="PASSED",
        source_row_count=10, target_row_count=10, total_issues=0,
    )
    result_b = SimpleNamespace(
        query_name="orders", effective_status="PASSED",
        source_row_count=10, target_row_count=8, total_issues=0,
    )
    repo = MagicMock()
    repo.get_run.side_effect = [
        SimpleNamespace(results=[result_a]),
        SimpleNamespace(results=[result_b]),
    ]
    service = CompareService.__new__(CompareService)
    service._repo = repo
    request = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")

    with patch("api.services.compare_service.MetricsWriter.write"):
        service.run_recon_file_compare(request, "comparison-run")

    comparison_result = repo.add_test_result.call_args.args[1]
    assert comparison_result.target_row_count == 8


def test_dual_env_launch_returns_pair(client, monkeypatch):
    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_launch_dual_env_bg", lambda *a, **kw: None)

    c1 = client.post("/api/configs", json={"name": "cfg-a", "env_name": "a", "config_data": {}})
    c2 = client.post("/api/configs", json={"name": "cfg-b", "env_name": "b", "config_data": {}})
    cid_a, cid_b = c1.json()["id"], c2.json()["id"]

    resp = client.post("/api/compare/dual-env", json={
        "config_id_a": cid_a, "config_id_b": cid_b,
        "source_env_a": "src-a", "target_env_a": "tgt-a",
        "source_env_b": "src-b", "target_env_b": "tgt-b",
        "job_names": [],
    })
    assert resp.status_code == 202
    data = resp.json()
    assert "pair_id" in data
    assert "run_id_a" in data
    assert "run_id_b" in data
    detail_a = client.get(f"/api/runs/{data['run_id_a']}").json()
    detail_b = client.get(f"/api/runs/{data['run_id_b']}").json()
    assert detail_a["config_snapshot"]["config_id"] == cid_a
    assert detail_b["config_snapshot"]["config_id"] == cid_b
    assert detail_a["config_snapshot"]["job_sequence"] == []


def test_get_pair_runs(client, monkeypatch):
    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_launch_dual_env_bg", lambda *a, **kw: None)
    c1 = client.post("/api/configs", json={"name": "cfg-c", "env_name": "c", "config_data": {}})
    c2 = client.post("/api/configs", json={"name": "cfg-d", "env_name": "d", "config_data": {}})
    cid_a, cid_b = c1.json()["id"], c2.json()["id"]
    launch = client.post("/api/compare/dual-env", json={
        "config_id_a": cid_a, "config_id_b": cid_b,
        "source_env_a": "s", "target_env_a": "t",
        "source_env_b": "s2", "target_env_b": "t2",
        "job_names": [],
    })
    pair_id = launch.json()["pair_id"]
    resp = client.get(f"/api/compare/pairs/{pair_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pair_id"] == pair_id
    assert "run_a" in data and "run_b" in data


def test_list_pairs(client, monkeypatch):
    import api.routes.compare as cmp_module
    monkeypatch.setattr(cmp_module, "_launch_dual_env_bg", lambda *a, **kw: None)
    c1 = client.post("/api/configs", json={"name": "cfg-e", "env_name": "e", "config_data": {}})
    c2 = client.post("/api/configs", json={"name": "cfg-f", "env_name": "f", "config_data": {}})
    cid_a, cid_b = c1.json()["id"], c2.json()["id"]
    client.post("/api/compare/dual-env", json={
        "config_id_a": cid_a, "config_id_b": cid_b,
        "source_env_a": "s", "target_env_a": "t",
        "source_env_b": "s2", "target_env_b": "t2",
        "job_names": [],
    })
    resp = client.get("/api/compare/pairs")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


_SQL_CFG_JSON = {
    "db_host": "server",
    "db_port": 1433,
    "db_name": "db",
    "db_user": "u",
    "db_password": "p",
    "db_driver": "ODBC Driver 17 for SQL Server",
    "db_pool_size": 5,
    "db_pool_overflow": 10,
    "db_pool_timeout": 30,
    "db_pool_recycle": 3600,
    "db_connect_timeout": 15,
    "automic_url": "",
    "automic_user": "",
    "automic_password": "",
    "automic_timeout": 30,
    "automic_max_retries": 3,
    "bo_url": "",
    "bo_user": "",
    "bo_password": "",
    "bo_timeout": 60,
    "connections": {"hr_db": {"db_host": "hr-server", "db_name": "HR",
                              "db_user": "u", "db_password": "p"}},
}


def test_sql_compare_unknown_connection_returns_422(client, monkeypatch):
    """connection_a that does not exist in config.connections must return 422."""
    import api.routes.compare as compare_module
    monkeypatch.setattr(compare_module, "_run_sql_bg", lambda *a, **kw: None)

    c = client.post("/api/configs", json={
        "name": "sql-cfg", "env_name": "prod", "config_data": _SQL_CFG_JSON,
    })
    cid = c.json()["id"]

    resp = client.post("/api/compare/sql", json={
        "config_id_a": cid,
        "config_id_b": cid,
        "query_a": "SELECT 1",
        "query_b": "SELECT 1",
        "connection_a": "does_not_exist",
    })
    assert resp.status_code == 422


def test_sql_compare_valid_connection_accepted(client, monkeypatch):
    """A valid named connection_a should result in 202, not 422."""
    import api.routes.compare as compare_module
    monkeypatch.setattr(compare_module, "_run_sql_bg", lambda *a, **kw: None)

    c = client.post("/api/configs", json={
        "name": "sql-cfg2", "env_name": "prod", "config_data": _SQL_CFG_JSON,
    })
    cid = c.json()["id"]

    resp = client.post("/api/compare/sql", json={
        "config_id_a": cid,
        "config_id_b": cid,
        "query_a": "SELECT 1",
        "query_b": "SELECT 1",
        "connection_a": "hr_db",
        "connection_b": "hr_db",
    })
    assert resp.status_code == 202


# ---------------------------------------------------------------------------
# _load_in_chunks unit tests
# ---------------------------------------------------------------------------

def test_load_in_chunks_issues_paginated_queries():
    """When chunk_size and key_cols are provided, queries are issued in pages."""
    import pandas as pd
    from unittest.mock import MagicMock
    from api.services.compare_service import _load_in_chunks

    chunk1 = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    chunk2 = pd.DataFrame({"id": [4, 5, 6], "val": ["d", "e", "f"]})
    empty = pd.DataFrame({"id": [], "val": []})

    mock_engine = MagicMock()
    mock_engine.execute_query.side_effect = [chunk1, chunk2, empty]

    result = _load_in_chunks(mock_engine, "SELECT * FROM t", ["id"], chunk_size=3)

    assert len(result) == 6
    assert list(result["id"]) == [1, 2, 3, 4, 5, 6]
    assert mock_engine.execute_query.call_count == 3


def test_load_in_chunks_stops_early_on_partial_chunk():
    """A chunk smaller than chunk_size signals the last page — no extra query needed."""
    import pandas as pd
    from unittest.mock import MagicMock
    from api.services.compare_service import _load_in_chunks

    chunk1 = pd.DataFrame({"id": [1, 2, 3], "val": ["a", "b", "c"]})
    chunk2 = pd.DataFrame({"id": [4, 5], "val": ["d", "e"]})  # 2 < 3 → last page

    mock_engine = MagicMock()
    mock_engine.execute_query.side_effect = [chunk1, chunk2]

    result = _load_in_chunks(mock_engine, "SELECT * FROM t", ["id"], chunk_size=3)

    assert len(result) == 5
    assert mock_engine.execute_query.call_count == 2


def test_load_in_chunks_falls_back_without_key_columns():
    """With no key columns, chunking is skipped and a single full query is issued."""
    import pandas as pd
    from unittest.mock import MagicMock
    from api.services.compare_service import _load_in_chunks

    full = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = full

    result = _load_in_chunks(mock_engine, "SELECT * FROM t", [], chunk_size=1000)

    mock_engine.execute_query.assert_called_once_with("SELECT * FROM t")
    assert len(result) == 2


def test_load_in_chunks_falls_back_when_chunk_size_zero():
    """chunk_size=0 disables chunking regardless of key columns."""
    import pandas as pd
    from unittest.mock import MagicMock
    from api.services.compare_service import _load_in_chunks

    full = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
    mock_engine = MagicMock()
    mock_engine.execute_query.return_value = full

    result = _load_in_chunks(mock_engine, "SELECT * FROM t", ["id"], chunk_size=0)

    mock_engine.execute_query.assert_called_once_with("SELECT * FROM t")
    assert len(result) == 2
