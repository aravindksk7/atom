from __future__ import annotations
import logging
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.schemas import BOCompareRequest, ReconFileCompareRequest
from api.services.file_source import read_tabular
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.repository.repository import ConfigRepository, RunRepository
from etl_framework.runner.state import TestStatus

logger = logging.getLogger("api.services.compare_service")

_SENTINEL_QUERY = "__file_source__"


class _FrameEngine:
    """Wrap a pre-loaded DataFrame so ReconciliationEngine can consume it."""

    def __init__(self, df, env_name: str):
        import types
        self._df = df
        self._env = types.SimpleNamespace(name=env_name)

    def execute_query(self, query: str, params=None):
        return self._df


class CompareService:
    def __init__(self, db: Session, config_repo: ConfigRepository) -> None:
        self._db = db
        self._repo = RunRepository(db)
        self._config_repo = config_repo

    # ------------------------------------------------------------------
    # BO Report comparison
    # ------------------------------------------------------------------

    def run_bo_comparison(self, req: BOCompareRequest, run_id: str) -> None:
        """Execute BO comparison and persist as TestRun/TestResult/MismatchDetail."""
        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))
            df_a = self._load_bo_source(req.source_a, req.doc_id, req.report_id)
            df_b = self._load_bo_source(req.source_b, req.doc_id, req.report_id)

            engine_a = _FrameEngine(df_a, req.label_a)
            engine_b = _FrameEngine(df_b, req.label_b)
            reconciler = ReconciliationEngine(
                engine_a, engine_b,
                key_columns=req.key_columns or [],
                exclude_columns=req.exclude_columns or [],
            )
            result = reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "bo_comparison")

            tr = self._repo.add_test_result(run_id, result)
            if result.mismatches:
                self._repo.add_mismatch_details(tr.id, result.mismatches)

            passed = 1 if result.status == TestStatus.PASSED else 0
            failed = 0 if passed else 1
            self._repo.update_run_status(
                run_id, "PASSED" if passed else "FAILED",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, passed=passed, failed=failed,
            )
        except Exception as exc:
            logger.exception("BO comparison failed for run %s", run_id)
            self._repo.update_run_status(
                run_id, "ERROR",
                completed_at=datetime.now(timezone.utc),
                error=1,
            )
            raise

    def _load_bo_source(self, src, doc_id, report_id):
        if src.source_type == "live":
            cfg = self._config_repo.get(src.config_id)
            if cfg is None:
                raise HTTPException(status_code=404, detail="Config not found")
            from etl_framework.config.models import EnvironmentConfig
            env = EnvironmentConfig(name=cfg.env_name, **cfg.config_json)
            from etl_framework.sap_bo.client import BORestClient
            client = BORestClient(env)
            try:
                return client.fetch_report_data(doc_id or report_id or "")
            finally:
                client.logout()
        return read_tabular(
            path=src.file_path,
            content_b64=src.file_content_b64,
            file_name=src.file_name,
        )

    # ------------------------------------------------------------------
    # Reconciliation file comparison
    # ------------------------------------------------------------------

    def run_recon_file_compare(self, req: ReconFileCompareRequest, run_id: str) -> None:
        """Diff a production HTML report against a stored run or another file."""
        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))
            stats_a = self._load_recon_source_a(req)
            stats_b = self._load_recon_html(req.file_b_path, req.file_b_content_b64)

            all_names = sorted(set(stats_a) | set(stats_b))
            passed = failed = 0
            for name in all_names:
                a = stats_a.get(name, {})
                b = stats_b.get(name, {})
                status = "PASSED" if a.get("status") == b.get("status") == "PASSED" else "FAILED"
                if status == "PASSED":
                    passed += 1
                else:
                    failed += 1
                from etl_framework.reconciliation.models import ReconciliationResult
                from etl_framework.runner.state import TestStatus as TS
                synthetic = ReconciliationResult(
                    query_name=name,
                    source_env=req.label_a,
                    target_env=req.label_b,
                    source_row_count=a.get("source_row_count", 0),
                    target_row_count=b.get("source_row_count", 0),
                    matched_count=0,
                    missing_in_target_count=0,
                    missing_in_source_count=0,
                    value_mismatch_count=0 if status == "PASSED" else 1,
                    mismatches=[],
                    status=TS.PASSED if status == "PASSED" else TS.FAILED,
                    executed_at=datetime.now(timezone.utc),
                    duration_seconds=0.0,
                )
                self._repo.add_test_result(run_id, synthetic)

            overall = "PASSED" if failed == 0 else "FAILED"
            self._repo.update_run_status(
                run_id, overall,
                completed_at=datetime.now(timezone.utc),
                total_tests=len(all_names), passed=passed, failed=failed,
            )
        except Exception:
            logger.exception("Recon file compare failed for run %s", run_id)
            self._repo.update_run_status(run_id, "ERROR", completed_at=datetime.now(timezone.utc), error=1)
            raise

    def _load_recon_source_a(self, req: ReconFileCompareRequest) -> dict:
        if req.stored_run_id:
            run = self._repo.get_run(req.stored_run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Stored run not found")
            return {
                r.query_name: {
                    "status": r.status,
                    "source_row_count": r.source_row_count,
                }
                for r in run.results
            }
        return self._load_recon_html(req.file_a_path, req.file_a_content_b64)

    @staticmethod
    def _load_recon_html(path: str | None, b64: str | None) -> dict[str, dict]:
        if b64:
            import base64
            html = base64.b64decode(b64).decode("utf-8", errors="replace")
        elif path:
            from pathlib import Path
            try:
                html = Path(path).read_text(encoding="utf-8")
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"File not found: {path}")
        else:
            return {}
        return CompareService._parse_html_report(html)

    @staticmethod
    def _parse_html_report(html: str) -> dict[str, dict]:
        """
        Extract per-test stats from a framework-generated HTML report.
        Returns {test_name: {status, source_row_count}}.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise HTTPException(
                status_code=500,
                detail="beautifulsoup4 not installed — cannot parse HTML reports",
            )
        soup = BeautifulSoup(html, "html.parser")
        results: dict[str, dict] = {}
        for row in soup.select("table tr"):
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cells) >= 2:
                name, status = cells[0], cells[1].upper()
                if status in ("PASSED", "FAILED", "ERROR", "SLOW"):
                    results[name] = {
                        "status": status,
                        "source_row_count": int(cells[2]) if len(cells) > 2 and cells[2].isdigit() else 0,
                    }
        if not results:
            raise HTTPException(
                status_code=422,
                detail="Cannot parse reconciliation report — not a framework-generated report",
            )
        return results
