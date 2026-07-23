from __future__ import annotations
import logging
import base64
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.orm import Session

from api.schemas import (
    BOCompareRequest, ReconFileCompareRequest, SQLCompareRequest,
    ColumnStatsRequest, ColumnStatsOut, ColumnStatsDiffOut,
    MismatchDiffRequest, MismatchDiffOut, MismatchRecordOut,
    AdvancedCompareOptions, MultiFileCompareRequest,
)
from api.services.file_source import read_tabular
from api.services.frame_engine import FrameEngine
from etl_framework.reconciliation.chunker import load_in_chunks
from etl_framework.reconciliation.engine import ReconciliationEngine
from etl_framework.repository.models import TestResult
from etl_framework.repository.repository import ConfigRepository, RunRepository
from etl_framework.reporting.metrics import MetricsWriter
from etl_framework.runner.state import TestStatus


def _load_in_chunks(
    db_engine: "DBEngine",
    query: str,
    key_cols: list[str],
    chunk_size: int,
) -> pd.DataFrame:
    return load_in_chunks(db_engine, query, key_cols, chunk_size)

logger = logging.getLogger("api.services.compare_service")

_SENTINEL_QUERY = "__file_source__"
_DEFAULT_COMPARE_MISMATCH_ROW_LIMIT = 5000
_KEY_CANDIDATES = (
    "id",
    "employee id",
    "employee_id",
    "order id",
    "order_id",
    "customer id",
    "customer_id",
    "account id",
    "account_id",
)


def _column_key(column: object) -> str:
    return "".join(ch for ch in str(column).lower() if ch.isalnum())


def _compare_mismatch_row_limit(adv: "AdvancedCompareOptions | None") -> int:
    return getattr(adv, "mismatch_row_limit", _DEFAULT_COMPARE_MISMATCH_ROW_LIMIT)


def _build_engine(
    engine_a,
    engine_b,
    key_columns: list[str],
    exclude_columns: list[str],
    mismatch_row_limit: int,
    adv: "AdvancedCompareOptions | None" = None,
) -> "ReconciliationEngine":
    """Construct a ReconciliationEngine, optionally with advanced options."""
    from etl_framework.reconciliation.backends import PandasBackend, PolarsBackend, DuckDBBackend, SamplingBackend

    if adv is None:
        return ReconciliationEngine(
            engine_a, engine_b,
            key_columns=key_columns,
            exclude_columns=exclude_columns,
            mismatch_row_limit=mismatch_row_limit,
        )

    backend_name = adv.comparison_backend or "pandas"
    common_kwargs = dict(
        key_columns=key_columns,
        float_tolerance=adv.float_tolerance,
        mismatch_row_limit=mismatch_row_limit,
        column_tolerances=adv.column_tolerances or None,
        datetime_tolerance_seconds=adv.datetime_tolerance_seconds,
    )

    if backend_name == "duckdb":
        backend = DuckDBBackend(**common_kwargs)
    elif backend_name == "polars":
        backend = PolarsBackend(**common_kwargs)
    else:
        backend = PandasBackend(
            **common_kwargs,
            case_insensitive_columns=adv.case_insensitive_columns or None,
            whitespace_normalize_columns=adv.whitespace_normalize_columns or None,
        )

    if adv.sample_frac is not None:
        backend = SamplingBackend(backend, sample_frac=adv.sample_frac)

    return ReconciliationEngine(
        engine_a, engine_b,
        key_columns=key_columns,
        exclude_columns=exclude_columns,
        mismatch_row_limit=mismatch_row_limit,
        float_tolerance=adv.float_tolerance,
        column_tolerances=adv.column_tolerances or None,
        datetime_tolerance_seconds=adv.datetime_tolerance_seconds,
        case_insensitive_columns=adv.case_insensitive_columns or None,
        whitespace_normalize_columns=adv.whitespace_normalize_columns or None,
        parallel_columns=adv.parallel_columns,
        parallel_workers=adv.parallel_workers,
        backend=backend,
    )


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
            key_columns = req.key_columns
            if not key_columns:
                try:
                    key_columns = self._infer_key_columns(df_a, df_b)
                except HTTPException:
                    df_a = df_a.copy()
                    df_b = df_b.copy()
                    df_a.insert(0, "__row__", range(1, len(df_a) + 1))
                    df_b.insert(0, "__row__", range(1, len(df_b) + 1))
                    key_columns = ["__row__"]
            self._validate_key_columns(df_a, df_b, key_columns)

            engine_a = FrameEngine(df_a, req.label_a)
            engine_b = FrameEngine(df_b, req.label_b)
            reconciler = _build_engine(
                engine_a, engine_b,
                key_columns=key_columns,
                exclude_columns=req.exclude_columns or [],
                mismatch_row_limit=_compare_mismatch_row_limit(getattr(req, "advanced", None)),
                adv=getattr(req, "advanced", None),
            )
            result = reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "bo_comparison")

            tr = self._repo.add_test_result(run_id, result)
            if result.mismatches:
                self._repo.add_mismatch_details(tr.id, result.mismatches)
            MetricsWriter(f"logs/metrics_{run_id}.json").write(run_id, [result])

            passed = 1 if result.status == TestStatus.PASSED else 0
            failed = 0 if passed else 1
            self._repo.update_run_status(
                run_id, "PASSED" if passed else "FAILED",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, passed=passed, failed=failed,
            )
        except Exception as exc:
            logger.exception("BO comparison failed for run %s", run_id)
            self._add_error_result(run_id, req.label_a or "bo_comparison", exc)
            self._repo.update_run_status(
                run_id, "ERROR",
                completed_at=datetime.now(timezone.utc),
                total_tests=1,
                error=1,
            )
            raise

    def _add_error_result(self, run_id: str, query_name: str, exc: Exception) -> None:
        from api.services.adapter_service import _friendly_error

        result = TestResult(
            run_id=run_id,
            query_name=query_name,
            status=TestStatus.ERROR.value,
            duration_seconds=0.0,
            source_row_count=0,
            target_row_count=0,
            value_mismatch_count=0,
            missing_in_target_count=0,
            missing_in_source_count=0,
            error_message=_friendly_error(exc),
            executed_at=datetime.now(timezone.utc),
        )
        self._db.add(result)
        self._db.commit()

    def _load_bo_source(self, src, fallback_doc_id: str | None, fallback_report_id: str | None):
        if src.source_type == "live":
            doc_id = src.doc_id or fallback_doc_id
            report_id = src.report_id or fallback_report_id
            if not doc_id or not report_id:
                raise HTTPException(
                    status_code=422,
                    detail="doc_id and report_id are required for live BO sources",
                )
            cfg = self._config_repo.get(src.config_id)
            if cfg is None:
                raise HTTPException(status_code=404, detail="Config not found")
            from etl_framework.config.models import EnvironmentConfig
            env = EnvironmentConfig(name=cfg.env_name, **cfg.config_json)
            from etl_framework.sap_bo.client import BORestClient
            client = BORestClient(env)
            try:
                raw = client.download_report(doc_id, report_id, src.format)
                return read_tabular(
                    content_b64=base64.b64encode(raw).decode("ascii"),
                    file_name=f"bo_report_{doc_id}_{report_id}.{src.format}",
                )
            finally:
                client.logout()
        if src.source_type == "api":
            return self._load_api_source(src)
        return read_tabular(
            path=src.file_path,
            content_b64=src.file_content_b64,
            file_name=src.file_name,
        )

    def _load_api_source(self, src) -> pd.DataFrame:
        cfg = self._config_repo.get(src.config_id)
        if cfg is None:
            raise HTTPException(status_code=404, detail="Config not found")
        from etl_framework.config.models import resolve_api_endpoint
        from etl_framework.rest_api.client import APIEndpointClient
        try:
            entry = resolve_api_endpoint(cfg.config_json or {}, src.api_endpoint_name or "")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return APIEndpointClient(entry).fetch_dataframe()

    @staticmethod
    def _infer_key_columns(df_a, df_b) -> list[str]:
        common_by_lower = {
            str(col).strip().lower(): str(col)
            for col in df_a.columns
            if str(col).strip().lower() in {str(c).strip().lower() for c in df_b.columns}
        }
        for candidate in _KEY_CANDIDATES:
            if candidate in common_by_lower:
                return [common_by_lower[candidate]]
        if len(common_by_lower) == 1:
            return [next(iter(common_by_lower.values()))]
        raise HTTPException(
            status_code=422,
            detail="key_columns are required when no unique common ID column can be inferred",
        )

    @staticmethod
    def _validate_key_columns(df_a, df_b, key_columns: list[str]) -> None:
        missing_a = [col for col in key_columns if col not in df_a.columns]
        missing_b = [col for col in key_columns if col not in df_b.columns]
        if missing_a or missing_b:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Selected key_columns must exist in both sources",
                    "missing_in_source_a": missing_a,
                    "missing_in_source_b": missing_b,
                },
            )

    @staticmethod
    def _sort_for_positional_compare(
        df_a: "pd.DataFrame",
        df_b: "pd.DataFrame",
        exclude_columns: list[str] | None,
    ) -> tuple["pd.DataFrame", "pd.DataFrame"]:
        """Sort both sides before row-position fallback alignment."""
        excluded = {_column_key(col) for col in (exclude_columns or [])}
        common_columns = [
            col for col in df_a.columns
            if col in df_b.columns and _column_key(col) not in excluded
        ]
        if not common_columns:
            return df_a.reset_index(drop=True), df_b.reset_index(drop=True)

        def _sort_frame(df: "pd.DataFrame") -> "pd.DataFrame":
            try:
                return df.sort_values(
                    by=common_columns,
                    kind="mergesort",
                    na_position="first",
                ).reset_index(drop=True)
            except TypeError:
                sort_keys = pd.DataFrame(index=df.index)
                for idx, col in enumerate(common_columns):
                    sort_keys[f"__sort_{idx}"] = df[col].map(
                        lambda value: "" if pd.isna(value) else str(value)
                    )
                order = sort_keys.sort_values(
                    by=list(sort_keys.columns),
                    kind="mergesort",
                    na_position="first",
                ).index
                return df.loc[order].reset_index(drop=True)

        return _sort_frame(df_a), _sort_frame(df_b)

    def _run_tabular_file_compare(
        self, req: ReconFileCompareRequest, run_id: str,
        df_a: "pd.DataFrame", df_b: "pd.DataFrame",
    ) -> None:
        """Compare two DataFrames via ReconciliationEngine and store results."""
        import pandas as pd
        key_columns = req.key_columns
        if not key_columns:
            try:
                key_columns = self._infer_key_columns(df_a, df_b)
            except HTTPException:
                # No identifiable key column — compare row-by-row using position
                df_a, df_b = self._sort_for_positional_compare(
                    df_a,
                    df_b,
                    req.exclude_columns or [],
                )
                df_a = df_a.copy()
                df_b = df_b.copy()
                df_a.insert(0, "__row__", range(1, len(df_a) + 1))
                df_b.insert(0, "__row__", range(1, len(df_b) + 1))
                key_columns = ["__row__"]
        self._validate_key_columns(df_a, df_b, key_columns)
        engine_a = FrameEngine(df_a, req.label_a)
        engine_b = FrameEngine(df_b, req.label_b)
        reconciler = _build_engine(
            engine_a, engine_b,
            key_columns=key_columns,
            exclude_columns=req.exclude_columns or [],
            mismatch_row_limit=_compare_mismatch_row_limit(getattr(req, "advanced", None)),
            adv=getattr(req, "advanced", None),
        )
        result = reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "file_a")
        tr = self._repo.add_test_result(run_id, result)
        if result.mismatches:
            self._repo.add_mismatch_details(tr.id, result.mismatches)
        MetricsWriter(f"logs/metrics_{run_id}.json").write(run_id, [result])
        passed = 1 if result.status == TestStatus.PASSED else 0
        failed = 0 if passed else 1
        self._repo.update_run_status(
            run_id, "PASSED" if passed else "FAILED",
            completed_at=datetime.now(timezone.utc),
            total_tests=1, passed=passed, failed=failed,
        )

    # ------------------------------------------------------------------
    # SQL-to-SQL comparison
    # ------------------------------------------------------------------

    def run_sql_comparison(self, req: SQLCompareRequest, run_id: str) -> None:
        """Execute two SQL queries against their respective DB configs and diff the results."""
        from etl_framework.db.engine import DBEngine
        from etl_framework.config.models import resolve_connection

        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))

            cfg_a = self._config_repo.get(req.config_id_a)
            if cfg_a is None:
                raise HTTPException(status_code=404, detail="Config A not found")
            cfg_b = self._config_repo.get(req.config_id_b)
            if cfg_b is None:
                raise HTTPException(status_code=404, detail="Config B not found")

            env_a = resolve_connection(cfg_a.config_json or {}, req.connection_a, env_name=cfg_a.env_name or "")
            env_b = resolve_connection(cfg_b.config_json or {}, req.connection_b, env_name=cfg_b.env_name or "")
            engine_a = DBEngine(env_a)
            engine_b = DBEngine(env_b)
            key_cols = req.key_columns or []
            try:
                df_a = _load_in_chunks(engine_a, req.query_a, key_cols, req.chunk_size)
                df_b = _load_in_chunks(engine_b, req.query_b, key_cols, req.chunk_size)
            finally:
                engine_a.dispose()
                engine_b.dispose()

            recon_req = ReconFileCompareRequest(
                file_a_path="__sql__",
                file_b_path="__sql__",
                label_a=req.label_a,
                label_b=req.label_b,
                key_columns=req.key_columns or None,
                exclude_columns=req.exclude_columns,
                advanced=req.advanced,
            )
            self._run_tabular_file_compare(recon_req, run_id, df_a, df_b)
        except Exception as exc:
            logger.exception("SQL comparison failed for run %s", run_id)
            self._add_error_result(run_id, req.label_a or "sql_comparison", exc)
            self._repo.update_run_status(
                run_id, "ERROR",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, error=1,
            )
            raise

    # ------------------------------------------------------------------
    # Reconciliation file comparison
    # ------------------------------------------------------------------

    def run_recon_file_compare(self, req: ReconFileCompareRequest, run_id: str) -> None:
        """Diff a production HTML report against a stored run or another file."""
        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))
            stats_a = self._load_recon_source(req, "a")
            stats_b = self._load_recon_source(req, "b")

            import pandas as pd
            _is_df_a = isinstance(stats_a, pd.DataFrame)
            _is_df_b = isinstance(stats_b, pd.DataFrame)
            if _is_df_a != _is_df_b:
                raise HTTPException(
                    status_code=422,
                    detail="Both sources must be the same type (both tabular files or both HTML/stored runs)",
                )
            if _is_df_a:
                self._run_tabular_file_compare(req, run_id, stats_a, stats_b)
                return

            all_names = sorted(set(stats_a) | set(stats_b))
            passed = failed = 0
            results = []
            for name in all_names:
                a = stats_a.get(name, {})
                b = stats_b.get(name, {})
                compared_metrics = (
                    "status", "source_row_count", "target_row_count", "total_issues",
                )
                differences = sum(a.get(metric) != b.get(metric) for metric in compared_metrics)
                status = "PASSED" if a and b and differences == 0 else "FAILED"
                if status == "PASSED":
                    passed += 1
                else:
                    failed += 1
                from etl_framework.reconciliation.models import ReconciliationResult, MismatchRecord
                from etl_framework.runner.state import TestStatus as TS
                synthetic = ReconciliationResult(
                    query_name=name,
                    source_env=req.label_a,
                    target_env=req.label_b,
                    source_row_count=a.get("source_row_count", 0),
                    target_row_count=b.get("target_row_count", 0),
                    matched_count=0,
                    missing_in_target_count=0,
                    missing_in_source_count=0,
                    value_mismatch_count=0 if status == "PASSED" else max(1, differences),
                    mismatches=[],
                    status=TS.PASSED if status == "PASSED" else TS.FAILED,
                    executed_at=datetime.now(timezone.utc),
                    duration_seconds=0.0,
                    mismatch_summary={
                        "by_column": {
                            metric: 1
                            for metric in compared_metrics
                            if a.get(metric) != b.get(metric)
                        },
                        "compared_rows_by_column": {
                            metric: 1
                            for metric in compared_metrics
                        },
                        "by_type": {
                            "value_diff": max(0, differences),
                            "missing_in_target": 0,
                            "missing_in_source": 0,
                        },
                    },
                )
                tr = self._repo.add_test_result(run_id, synthetic)
                if status != "PASSED":
                    _mm = [
                        MismatchRecord(
                            key_values={"test_name": name},
                            column_name=metric,
                            source_value=str(a.get(metric)) if a.get(metric) is not None else "",
                            target_value=str(b.get(metric)) if b.get(metric) is not None else "",
                            mismatch_type="stat_diff",
                        )
                        for metric in compared_metrics
                        if a.get(metric) != b.get(metric)
                    ]
                    if _mm:
                        self._repo.add_mismatch_details(tr.id, _mm)
                results.append(synthetic)

            overall = "PASSED" if failed == 0 else "FAILED"
            self._repo.update_run_status(
                run_id, overall,
                completed_at=datetime.now(timezone.utc),
                total_tests=len(all_names), passed=passed, failed=failed,
            )
            MetricsWriter(f"logs/metrics_{run_id}.json").write(run_id, results)
        except Exception as exc:
            logger.exception("Recon file compare failed for run %s", run_id)
            self._add_error_result(run_id, req.label_a or "recon_file", exc)
            self._repo.update_run_status(
                run_id, "ERROR",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, error=1,
            )
            raise

    def _load_recon_source(self, req: ReconFileCompareRequest, side: str):
        """Load one side of a recon-file compare.

        Returns dict[str, dict] for stored-run and HTML sources,
        or pd.DataFrame for tabular file sources (.csv, .xlsx, .json, .xml, .tsv, .txt).
        """
        stored_run_id = req.stored_run_id if side == "a" else req.stored_run_id_b
        file_path = req.file_a_path if side == "a" else req.file_b_path
        file_content_b64 = req.file_a_content_b64 if side == "a" else req.file_b_content_b64
        file_name = req.file_a_name if side == "a" else req.file_b_name

        if stored_run_id:
            run = self._repo.get_run(stored_run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Stored run for Source {side.upper()} not found")
            return {
                r.query_name: {
                    "status": r.effective_status,
                    "source_row_count": r.source_row_count,
                    "target_row_count": r.target_row_count,
                    "total_issues": r.total_issues,
                }
                for r in run.results
            }

        _TABULAR_EXTS = {".csv", ".xlsx", ".xls", ".json", ".xml", ".tsv", ".txt"}
        name = file_name or file_path or ""
        ext = Path(name).suffix.lower() if name else ""
        if ext in _TABULAR_EXTS:
            return read_tabular(path=file_path, content_b64=file_content_b64, file_name=name)

        return self._load_recon_html(file_path, file_content_b64)

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
        Returns per-test status, source/target row counts, and mismatch totals.
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
                name, status_text = cells[0], cells[1].upper()
                status = next(
                    (candidate for candidate in ("PASSED", "FAILED", "ERROR", "SLOW") if status_text.startswith(candidate)),
                    None,
                )
                if status is not None:
                    def parse_int(index: int) -> int:
                        if len(cells) <= index:
                            return 0
                        try:
                            return int(cells[index].replace(",", ""))
                        except ValueError:
                            return 0

                    results[name] = {
                        "status": status,
                        "source_row_count": parse_int(3),
                        "target_row_count": parse_int(4),
                        "total_issues": parse_int(5),
                    }
        if not results:
            raise HTTPException(
                status_code=422,
                detail="Cannot parse reconciliation report — not a framework-generated report",
            )
        return results

    # ------------------------------------------------------------------
    # Multi-file reconciliation (ad-hoc)
    # ------------------------------------------------------------------

    def run_multi_file_compare(self, req: MultiFileCompareRequest, run_id: str) -> None:
        """Ad-hoc multi-file reconciliation: discover, pair, reconcile every
        pair sequentially, then persist ONE aggregate TestResult -- the same
        result shape RunExecutor's saved-job multi_file path already
        produces, so the Reports-tab rendering (Phase 4) works unchanged.
        """
        from etl_framework.reconciliation.compare_utils import resolve_key_columns
        from etl_framework.reconciliation.file_mapping import (
            FileMappingSpec,
            aggregate_reconciliation_results,
            pair_files,
            pair_files_automated,
        )
        from api.services.multi_file_remote import RemoteFileSourceSession

        try:
            self._repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))

            spec = FileMappingSpec.from_params({"file_mapping": req.file_mapping})
            if spec.source.kind != "local" or spec.target.kind != "local":
                raise ValueError(
                    "Ad-hoc multi-file compare only supports 'local' source/target kinds; "
                    "save a job instead for s3/sftp sources."
                )

            with RemoteFileSourceSession({}) as session:
                source_files = session.discover(spec.source)
                target_files = session.discover(spec.target)

                if spec.strategy == "automated":
                    source_frames = {f.path: session.read_file(f, spec.source) for f in source_files}
                    target_frames = {f.path: session.read_file(f, spec.target) for f in target_files}
                    mapping, _ = pair_files_automated(
                        source_files, source_frames, target_files, target_frames, spec.automated,
                    )
                else:
                    mapping = pair_files(source_files, target_files, spec.match_on)

                if mapping.unmatched_sources or mapping.unmatched_targets:
                    # NOTE: parenthesize the OR before the AND -- `a or b and c`
                    # evaluates as `a or (b and c)` in Python, which would raise
                    # on ANY unmatched source regardless of policy. Keep this as
                    # two separate ifs (as below), not one combined expression.
                    if spec.unmatched_policy == "fail":
                        raise ValueError(
                            f"multi-file compare has {len(mapping.unmatched_sources)} unmatched source "
                            f"group(s) and {len(mapping.unmatched_targets)} unmatched target group(s)"
                        )
                    if spec.unmatched_policy == "warn":
                        logger.warning(
                            "multi-file compare for run '%s' proceeding with %d unmatched source "
                            "group(s) and %d unmatched target group(s)",
                            run_id, len(mapping.unmatched_sources), len(mapping.unmatched_targets),
                        )
                if not mapping.pairs:
                    raise ValueError("multi-file compare matched zero file pairs")

                pair_results = []
                for pair in mapping.pairs:
                    source_df = pd.concat(
                        [session.read_file(f, spec.source) for f in pair.source.files], ignore_index=True,
                    )
                    target_df = pd.concat(
                        [session.read_file(f, spec.target) for f in pair.target.files], ignore_index=True,
                    )
                    source_df, target_df, resolved_keys = resolve_key_columns(
                        source_df, target_df, req.key_columns or [], req.exclude_columns or [],
                    )
                    engine_a = FrameEngine(source_df, req.label_a)
                    engine_b = FrameEngine(target_df, req.label_b)
                    reconciler = _build_engine(
                        engine_a, engine_b,
                        key_columns=resolved_keys,
                        exclude_columns=req.exclude_columns or [],
                        mismatch_row_limit=_compare_mismatch_row_limit(req.advanced),
                        adv=req.advanced,
                    )
                    pair_results.append(reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "multi_file_compare"))

            result = aggregate_reconciliation_results(req.label_a or "multi_file_compare", mapping, pair_results)
            tr = self._repo.add_test_result(run_id, result)
            if result.mismatches:
                self._repo.add_mismatch_details(tr.id, result.mismatches)
            MetricsWriter(f"logs/metrics_{run_id}.json").write(run_id, [result])
            passed = 1 if result.status == TestStatus.PASSED else 0
            failed = 0 if passed else 1
            self._repo.update_run_status(
                run_id, "PASSED" if passed else "FAILED",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, passed=passed, failed=failed,
            )
        except Exception as exc:
            # Unlike run_sql_comparison/run_bo_comparison, this does NOT
            # re-raise after recording the error: it's called directly (no
            # surrounding try/except) by both the background task and by
            # tests that assert on the persisted ERROR TestResult afterward.
            logger.exception("Multi-file comparison failed for run %s", run_id)
            self._add_error_result(run_id, req.label_a or "multi_file_compare", exc)
            self._repo.update_run_status(
                run_id, "ERROR",
                completed_at=datetime.now(timezone.utc),
                total_tests=1, error=1,
            )

    # ------------------------------------------------------------------
    # Column Stats comparison
    # ------------------------------------------------------------------

    def run_column_stats(self, req: "ColumnStatsRequest") -> "ColumnStatsOut":
        """Compute aggregate column stats for two sources and return drift diffs."""
        from etl_framework.reconciliation.column_stats import ColumnStatsComparer
        from datetime import timezone

        df_a = self._load_bo_source(req.source_a, req.doc_id, req.report_id)
        df_b = self._load_bo_source(req.source_b, req.doc_id, req.report_id)

        comparer = ColumnStatsComparer(
            float_tolerance=req.float_tolerance,
            row_count_tolerance=req.row_count_tolerance,
        )
        result = comparer.compare(
            df_a, df_b,
            query_name=req.query_name,
            source_env=req.label_a,
            target_env=req.label_b,
        )

        diffs_out = [
            ColumnStatsDiffOut(
                column=d.column,
                metric=d.metric,
                source_value=d.source_value,
                target_value=d.target_value,
                delta=d.delta,
            )
            for d in result.diffs
        ]
        diff_by_col: dict[str, list] = {}
        for d in diffs_out:
            diff_by_col.setdefault(d.column, []).append(d)

        return ColumnStatsOut(
            query_name=result.query_name,
            source_env=result.source_env,
            target_env=result.target_env,
            executed_at=result.executed_at,
            diffs=diffs_out,
            has_diffs=result.has_diffs,
            diff_by_column=diff_by_col,
        )

    # ------------------------------------------------------------------
    # Mismatch Diff (cross-run)
    # ------------------------------------------------------------------

    def run_mismatch_diff(self, req: "MismatchDiffRequest") -> "MismatchDiffOut":
        """Diff the mismatch sets from two stored runs."""
        from etl_framework.reconciliation.mismatch_diff import diff_mismatches
        from etl_framework.reconciliation.models import MismatchRecord

        run_a = self._repo.get_run(req.run_id_a)
        if run_a is None:
            raise HTTPException(status_code=404, detail="Run A not found")
        run_b = self._repo.get_run(req.run_id_b)
        if run_b is None:
            raise HTTPException(status_code=404, detail="Run B not found")

        def _load_mismatches(run, query_name_filter: str | None) -> list[MismatchRecord]:
            records: list[MismatchRecord] = []
            for tr in run.results:
                if query_name_filter and tr.query_name != query_name_filter:
                    continue
                for mm in (tr.mismatches or []):
                    records.append(MismatchRecord(
                        key_values=mm.key_values or {},
                        column_name=mm.column_name or "",
                        source_value=str(mm.source_value) if mm.source_value is not None else None,
                        target_value=str(mm.target_value) if mm.target_value is not None else None,
                        mismatch_type=mm.mismatch_type or "value_diff",
                    ))
            return records

        mismatches_a = _load_mismatches(run_a, req.query_name)
        mismatches_b = _load_mismatches(run_b, req.query_name)

        diff = diff_mismatches(
            mismatches_a, mismatches_b,
            query_name=req.query_name or "",
            run_a_label=req.run_a_label,
            run_b_label=req.run_b_label,
        )

        def _to_out(m: MismatchRecord) -> "MismatchRecordOut":
            return MismatchRecordOut(
                column_name=m.column_name,
                key_values=m.key_values,
                source_value=str(m.source_value) if m.source_value is not None else None,
                target_value=str(m.target_value) if m.target_value is not None else None,
                mismatch_type=m.mismatch_type,
                delta=m.delta,
                relative_delta=m.relative_delta,
            )

        return MismatchDiffOut(
            query_name=diff.query_name,
            run_a_label=diff.run_a_label,
            run_b_label=diff.run_b_label,
            compared_at=diff.compared_at,
            new=[_to_out(m) for m in diff.new],
            resolved=[_to_out(m) for m in diff.resolved],
            persistent=[_to_out(m) for m in diff.persistent],
            summary=diff.summary,
            has_regressions=diff.has_regressions,
        )
