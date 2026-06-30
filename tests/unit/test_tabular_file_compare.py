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


def _svc():
    svc = CompareService.__new__(CompareService)
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
