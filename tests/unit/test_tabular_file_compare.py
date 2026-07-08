from __future__ import annotations
import base64, io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from api.schemas import ReconFileCompareRequest
from api.services.compare_service import CompareService


def _b64csv(data: dict) -> str:
    buf = io.BytesIO()
    pd.DataFrame(data).to_csv(buf, index=False)
    return base64.b64encode(buf.getvalue()).decode()


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _svc():
    svc = CompareService.__new__(CompareService)
    svc._db = MagicMock()
    svc._repo = MagicMock()
    svc._repo.update_run_status = MagicMock()
    svc._repo.add_test_result = MagicMock(return_value=SimpleNamespace(id=5))
    svc._repo.add_mismatch_details = MagicMock()
    return svc


def test_load_recon_source_returns_df_for_csv():
    svc = _svc()
    b64 = _b64csv({"id": [1, 2], "val": [10, 20]})
    req = ReconFileCompareRequest(
        file_a_content_b64=b64,
        file_a_name="data.csv",
        stored_run_id_b="some-run",
    )
    result = svc._load_recon_source(req, "a")
    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["id", "val"]


def test_load_recon_source_returns_df_for_xml():
    svc = _svc()
    raw = b"""<dataset>
  <record><id>1</id><val>10</val></record>
  <record><id>2</id><val>20</val></record>
</dataset>"""
    req = ReconFileCompareRequest(
        file_a_content_b64=_b64(raw),
        file_a_name="data.xml",
        stored_run_id_b="some-run",
    )
    result = svc._load_recon_source(req, "a")
    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["id", "val"]


def test_load_recon_source_returns_dict_for_stored_run():
    svc = _svc()
    run = SimpleNamespace(results=[
        SimpleNamespace(query_name="q1", effective_status="PASSED",
                        source_row_count=10, target_row_count=10, total_issues=0)
    ])
    svc._repo.get_run = MagicMock(return_value=run)
    req = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")
    result = svc._load_recon_source(req, "a")
    assert isinstance(result, dict)
    assert "q1" in result


def test_run_tabular_file_compare_stores_mismatches():
    svc = _svc()
    df_a = pd.DataFrame({"id": [1, 2], "amount": [100, 200]})
    df_b = pd.DataFrame({"id": [1, 2], "amount": [100, 210]})  # row 2 differs

    req = ReconFileCompareRequest(
        file_a_content_b64="x",
        file_a_name="a.csv",
        file_b_content_b64="y",
        file_b_name="b.csv",
        key_columns=["id"],
    )
    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        svc._run_tabular_file_compare(req, "run-z", df_a, df_b)

    svc._repo.add_mismatch_details.assert_called_once()
    svc._repo.update_run_status.assert_called()


def test_run_tabular_file_compare_sorts_before_positional_fallback():
    svc = _svc()
    captured = {}

    def add_test_result(run_id, result):
        captured["result"] = result
        return SimpleNamespace(id=5)

    svc._repo.add_test_result = MagicMock(side_effect=add_test_result)
    df_a = pd.DataFrame({
        "Product": ["Widgets", "Gadgets"],
        "Revenue": [100, 200],
        "Sequence Number": [1, 2],
    })
    df_b = pd.DataFrame({
        "Product": ["Gadgets", "Widgets"],
        "Revenue": [200, 100],
        "Sequence Number": [99, 98],
    })

    req = ReconFileCompareRequest(
        file_a_content_b64="x",
        file_a_name="a.csv",
        file_b_content_b64="y",
        file_b_name="b.csv",
        exclude_columns=["sequence_number"],
    )
    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        svc._run_tabular_file_compare(req, "run-sorted", df_a, df_b)

    assert captured["result"].status.value == "PASSED"
    assert captured["result"].value_mismatch_count == 0
    svc._repo.add_mismatch_details.assert_not_called()


def test_run_tabular_file_compare_backend_keeps_service_detail_limit():
    svc = _svc()
    captured = {}

    def add_test_result(run_id, result):
        captured["result"] = result
        return SimpleNamespace(id=5)

    svc._repo.add_test_result = MagicMock(side_effect=add_test_result)
    n = 1200
    df_a = pd.DataFrame({"id": list(range(n)), "amount": [100] * n})
    df_b = pd.DataFrame({"id": list(range(n)), "amount": [200] * n})

    req = ReconFileCompareRequest(
        file_a_content_b64="x",
        file_a_name="a.csv",
        file_b_content_b64="y",
        file_b_name="b.csv",
        key_columns=["id"],
    )
    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        svc._run_tabular_file_compare(req, "run-cap", df_a, df_b)

    assert captured["result"].value_mismatch_count == n
    assert len(captured["result"].mismatches) == n
    assert len(svc._repo.add_mismatch_details.call_args.args[1]) == n


def test_mixed_sources_raise_422(monkeypatch):
    from fastapi import HTTPException
    svc = _svc()

    monkeypatch.setattr(svc, "_load_recon_source", lambda req, side: (
        pd.DataFrame({"id": [1]}) if side == "a" else {"q1": {}}
    ))
    svc._repo.update_run_status = MagicMock()

    req = ReconFileCompareRequest(
        file_a_content_b64=_b64csv({"id": [1]}), file_a_name="a.csv",
        stored_run_id_b="run-b",
    )
    with patch("api.services.compare_service.MetricsWriter"):
        with pytest.raises(HTTPException) as exc:
            svc.run_recon_file_compare(req, "run-mixed")
    assert exc.value.status_code == 422


def test_load_bo_source_dispatches_to_api_source():
    from api.schemas import SourceConfig

    svc = _svc()
    svc._config_repo = MagicMock()
    svc._config_repo.get.return_value = SimpleNamespace(
        config_json={"api_endpoints": {"orders": {"base_url": "https://api.example.com/orders"}}}
    )
    src = SourceConfig(source_type="api", config_id=1, api_endpoint_name="orders")
    fake_df = pd.DataFrame({"id": [1, 2]})

    with patch("etl_framework.rest_api.client.APIEndpointClient") as MockClient:
        MockClient.return_value.fetch_dataframe.return_value = fake_df
        result = svc._load_bo_source(src, None, None)

    assert result is fake_df
    MockClient.return_value.fetch_dataframe.assert_called_once_with()


def test_load_api_source_404_when_config_missing():
    from fastapi import HTTPException
    from api.schemas import SourceConfig

    svc = _svc()
    svc._config_repo = MagicMock()
    svc._config_repo.get.return_value = None
    src = SourceConfig(source_type="api", config_id=999, api_endpoint_name="orders")

    with pytest.raises(HTTPException) as exc:
        svc._load_bo_source(src, None, None)
    assert exc.value.status_code == 404


def test_load_api_source_404_when_endpoint_missing():
    from fastapi import HTTPException
    from api.schemas import SourceConfig

    svc = _svc()
    svc._config_repo = MagicMock()
    svc._config_repo.get.return_value = SimpleNamespace(config_json={"api_endpoints": {}})
    src = SourceConfig(source_type="api", config_id=1, api_endpoint_name="missing")

    with pytest.raises(HTTPException) as exc:
        svc._load_bo_source(src, None, None)
    assert exc.value.status_code == 404
