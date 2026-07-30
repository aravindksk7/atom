"""Prompt (report parameter) answering on the ad-hoc BO compare path.

The Adaptors tab answers a document's prompts before downloading it
(AdapterService.download_bo_report), and so do bo_report / bo_live jobs. The
Compare tab's live source did neither, so a prompted report — e.g. one with a
run-date prompt — came back built from whatever answers were last saved on the
document rather than the date the user picked.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from api.schemas import SourceConfig
from api.services.compare_service import CompareService


def _svc(tz: str = "UTC"):
    svc = CompareService.__new__(CompareService)
    svc._db = MagicMock()
    svc._repo = MagicMock()
    svc._config_repo = MagicMock()
    svc._config_repo.get.return_value = SimpleNamespace(
        env_name="bo",
        config_json={
            "db_host": "db-host", "db_password": "db-secret",
            "bo_url": "http://bo-server", "bo_user": "admin", "bo_password": "s3cr3t",
        },
    )
    svc._app_timezone = lambda: tz
    return svc


def _live_source(**kwargs) -> SourceConfig:
    kwargs.setdefault("format", "xlsx")
    return SourceConfig(source_type="live", config_id=1, doc_id="101", report_id="1", **kwargs)


def test_source_config_accepts_bo_parameters():
    src = _live_source(bo_parameters=[{"id": 5, "type": "DateTime", "value": "2026-06-02"}])
    assert src.bo_parameters[0].id == 5
    assert src.bo_parameters[0].value == "2026-06-02"


def test_source_config_defaults_bo_parameters_to_empty():
    assert _live_source().bo_parameters == []


def test_load_bo_source_answers_date_prompt_before_downloading(monkeypatch):
    """A date-only DateTime prompt must be converted with the app timezone and
    PUT before the export — the same order AdapterService.download_bo_report uses."""
    svc = _svc(tz="Etc/GMT-1")
    calls: list[str] = []

    with patch("etl_framework.sap_bo.client.BORestClient") as MockBO:
        client = MockBO.return_value
        client.answer_document_parameters.side_effect = lambda *a, **k: calls.append("answer")

        def download(*_a, **_k):
            calls.append("download")
            return b"id,value\n1,alpha\n"

        client.download_report.side_effect = download
        src = _live_source(
            format="csv",
            bo_parameters=[{"id": 5, "type": "DateTime", "value": "2026-06-02"}],
        )
        frame = svc._load_bo_source(src, None, None)

    assert calls == ["answer", "download"]
    doc_id, built = client.answer_document_parameters.call_args.args
    assert doc_id == "101"
    assert built == [{"id": 5, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}]
    assert isinstance(frame, pd.DataFrame)


def test_load_bo_source_maps_listing_type_vocabulary(monkeypatch):
    """BO's parameter listing calls a string prompt "Text" but the answer PUT
    wants "String" — the compare path must go through build_parameter_answers
    rather than hand-rolling the body."""
    svc = _svc()

    with patch("etl_framework.sap_bo.client.BORestClient") as MockBO:
        client = MockBO.return_value
        client.download_report.return_value = b"id,value\n1,alpha\n"
        svc._load_bo_source(
            _live_source(format="csv", bo_parameters=[{"id": 2, "type": "Text", "value": "EMEA"}]),
            None, None,
        )

    _doc, built = client.answer_document_parameters.call_args.args
    assert built == [{"id": 2, "type": "String", "value": "EMEA"}]


def test_load_bo_source_does_not_answer_when_no_prompts_given():
    svc = _svc()

    with patch("etl_framework.sap_bo.client.BORestClient") as MockBO:
        client = MockBO.return_value
        client.download_report.return_value = b"id,value\n1,alpha\n"
        svc._load_bo_source(_live_source(format="csv"), None, None)

    client.answer_document_parameters.assert_not_called()


def test_load_bo_source_logs_out_even_when_answering_fails():
    """A prompt PUT failure must not leak the BO session."""
    svc = _svc()

    with patch("etl_framework.sap_bo.client.BORestClient") as MockBO:
        client = MockBO.return_value
        client.answer_document_parameters.side_effect = RuntimeError("prompt rejected")
        with pytest.raises(RuntimeError):
            svc._load_bo_source(
                _live_source(bo_parameters=[{"id": 5, "type": "DateTime", "value": "2026-06-02"}]),
                None, None,
            )

    client.logout.assert_called_once()
