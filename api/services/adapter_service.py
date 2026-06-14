from __future__ import annotations

import re
import time

from fastapi import HTTPException

from api.schemas import AdapterTestOut, AutomicJobStatusOut, BODocOut, BOReportOut
from etl_framework.automic.client import AutomicClient
from etl_framework.config.models import EnvironmentConfig
from etl_framework.repository.repository import ConfigRepository
from etl_framework.sap_bo.client import BORestClient


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    exc_type = type(exc).__name__
    if "NameResolutionError" in msg or "getaddrinfo failed" in msg or "Name or service not known" in msg:
        m = re.search(r"resolve '([^']+)'", msg)
        host = m.group(1) if m else "host"
        return f"Cannot resolve '{host}' — check the server URL in your config"
    if "Connection refused" in msg or "ConnectionRefusedError" in msg:
        return "Connection refused — verify the server is running and the port is correct"
    if "timed out" in msg.lower() or "Timeout" in exc_type:
        return "Connection timed out — server may be unreachable or behind a firewall"
    if "Max retries exceeded" in msg:
        m = re.search(r"host='([^']+)', port=(\d+)", msg)
        target = f"{m.group(1)}:{m.group(2)}" if m else "server"
        return f"Cannot reach {target} — check network connectivity and firewall rules"
    if "Unauthorized" in msg or "401" in msg:
        return "Authentication failed — check username and password"
    if "Forbidden" in msg or "403" in msg:
        return "Access denied (403) — check service account permissions"
    return msg


class AdapterService:
    def __init__(self, config_repo: ConfigRepository) -> None:
        self._config_repo = config_repo

    def _get_env_config(self, config_id: int) -> EnvironmentConfig:
        cfg = self._config_repo.get(config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        return EnvironmentConfig(name=cfg.env_name, **cfg.config_json)

    # ------------------------------------------------------------------
    # SAP BO
    # ------------------------------------------------------------------

    def test_bo_connection(self, config_id: int) -> AdapterTestOut:
        env = self._get_env_config(config_id)
        t0 = time.monotonic()
        try:
            client = BORestClient(env)
            client.authenticate()
            latency_ms = int((time.monotonic() - t0) * 1000)
            return AdapterTestOut(ok=True, message="Connection successful", latency_ms=latency_ms)
        except Exception as exc:
            return AdapterTestOut(ok=False, message=_friendly_error(exc), latency_ms=0)

    def list_bo_documents(self, config_id: int) -> list[BODocOut]:
        env = self._get_env_config(config_id)
        try:
            client = BORestClient(env)
            client.authenticate()
            raw = client.list_documents()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc
        return [BODocOut(id=d["id"], name=d["name"], folder=d.get("folder", "")) for d in raw]

    def list_bo_reports(self, config_id: int, doc_id: str) -> list[BOReportOut]:
        env = self._get_env_config(config_id)
        try:
            client = BORestClient(env)
            client.authenticate()
            raw = client.list_reports(doc_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc
        return [BOReportOut(id=r["id"], name=r["name"], report_index=r.get("reportIndex", 0)) for r in raw]

    def download_bo_report(self, config_id: int, doc_id: str, report_id: str, fmt: str) -> bytes:
        env = self._get_env_config(config_id)
        try:
            client = BORestClient(env)
            client.authenticate()
            return client.download_report(doc_id, report_id, fmt)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc

    # ------------------------------------------------------------------
    # Automic
    # ------------------------------------------------------------------

    def lookup_automic_job(self, config_id: int, identifier: str, id_type: str) -> AutomicJobStatusOut:
        env = self._get_env_config(config_id)
        try:
            client = AutomicClient(env)
            if id_type == "run_id":
                status = client.get_status_by_run_id(identifier)
            else:
                status = client.get_status_by_job_name(identifier)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=_friendly_error(exc)) from exc
        return AutomicJobStatusOut(
            identifier=status.identifier,
            identifier_type=status.identifier_type,
            status=status.status.value,
            environment=status.environment,
            checked_at=status.checked_at,
        )
