from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.schemas import (
    BOCompareRequest,
    DifferenceExportStatusOut,
    ReconFileCompareRequest,
    RunSettings,
    SQLCompareRequest,
)
from api.services.compare_service import CompareService, _load_in_chunks
from api.services.file_source import read_tabular
from api.services.frame_engine import FrameEngine
from api.services.run_executor import RunExecutor
from etl_framework.config.models import resolve_connection
from etl_framework.db.engine import DBEngine
from etl_framework.reconciliation.compare_utils import (
    normalize_string_columns,
    numeric_delta,
    value_mismatch_mask,
)
from etl_framework.reconciliation.normalizer import TypeNormalizer
from etl_framework.repository.database import SessionLocal
from etl_framework.repository.models import (
    DifferenceExportJob,
    MismatchDetail,
    SavedJob,
    TestResult,
    TestRun,
)
from etl_framework.repository.repository import ConfigRepository
from etl_framework.utils.serialization import csv_safe, json_safe


DIFFERENCE_FIELDS = [
    "test_name",
    "key_values",
    "column_name",
    "source_value",
    "target_value",
    "mismatch_type",
    "delta",
    "relative_delta",
]
EXPORT_ROOT = Path("reports/exports")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(json_safe(value), ensure_ascii=False)


def _cell_text(value: Any) -> str:
    return csv_safe(value)


def _column_key(column: object) -> str:
    return "".join(ch for ch in str(column).lower() if ch.isalnum())


class DifferenceWriter:
    def __init__(self, path: Path, fmt: str, batch_size: int = 5000) -> None:
        self.path = path
        self.format = fmt
        self.row_count = 0
        self._batch_size = batch_size
        self._file = None
        self._csv_writer = None
        self._parquet_writer = None
        self._batch: list[dict[str, Any]] = []

        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "csv":
            self._file = path.open("w", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(self._file, fieldnames=DIFFERENCE_FIELDS)
            self._csv_writer.writeheader()
        elif fmt == "json":
            self._file = path.open("w", encoding="utf-8", newline="")
        elif fmt == "parquet":
            try:
                import pyarrow as pa  # noqa: F401
                import pyarrow.parquet as pq  # noqa: F401
            except ImportError as exc:
                raise RuntimeError(
                    "pyarrow is required for Parquet exports. Install it with: pip install pyarrow"
                ) from exc
        else:
            raise ValueError(f"Unsupported export format: {fmt}")

    def write(self, row: dict[str, Any]) -> None:
        normalized = {
            field: row.get(field)
            for field in DIFFERENCE_FIELDS
        }
        normalized["key_values"] = _json_text(normalized.get("key_values") or {})
        normalized["source_value"] = _cell_text(normalized.get("source_value"))
        normalized["target_value"] = _cell_text(normalized.get("target_value"))
        if self.format == "csv":
            assert self._csv_writer is not None
            self._csv_writer.writerow(normalized)
        elif self.format == "json":
            assert self._file is not None
            self._file.write(json.dumps(json_safe(normalized), ensure_ascii=False) + "\n")
        else:
            self._batch.append(normalized)
            if len(self._batch) >= self._batch_size:
                self._flush_parquet()
        self.row_count += 1

    def _flush_parquet(self) -> None:
        if not self._batch:
            return
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pylist(self._batch)
        if self._parquet_writer is None:
            self._parquet_writer = pq.ParquetWriter(self.path, table.schema)
        self._parquet_writer.write_table(table)
        self._batch = []

    def close(self) -> None:
        if self.format == "parquet":
            self._flush_parquet()
            if self._parquet_writer is None:
                import pyarrow as pa
                import pyarrow.parquet as pq
                schema = pa.schema([
                    ("test_name", pa.string()),
                    ("key_values", pa.string()),
                    ("column_name", pa.string()),
                    ("source_value", pa.string()),
                    ("target_value", pa.string()),
                    ("mismatch_type", pa.string()),
                    ("delta", pa.float64()),
                    ("relative_delta", pa.float64()),
                ])
                pq.write_table(pa.Table.from_pylist([], schema=schema), self.path)
            else:
                self._parquet_writer.close()
        if self._file is not None:
            self._file.close()

    def __enter__(self) -> "DifferenceWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


def validate_difference_format(fmt: str) -> str:
    normalized = fmt.lower().strip()
    if normalized not in {"csv", "parquet", "json"}:
        raise HTTPException(status_code=422, detail="format must be csv, parquet, or json")
    return normalized


def stored_detail_counts(db: Session, run_id: str) -> dict[int, int]:
    rows = (
        db.query(MismatchDetail.test_result_id, func.count(MismatchDetail.id))
        .join(TestResult, TestResult.id == MismatchDetail.test_result_id)
        .filter(TestResult.run_id == run_id)
        .group_by(MismatchDetail.test_result_id)
        .all()
    )
    return {int(result_id): int(count) for result_id, count in rows}


def stored_rows_are_complete(db: Session, run: TestRun) -> bool:
    counts = stored_detail_counts(db, run.run_id)
    return all(int(result.total_issues or 0) <= counts.get(result.id, 0) for result in run.results)


def stored_completeness_summary(db: Session, run: TestRun) -> dict[str, int]:
    counts = stored_detail_counts(db, run.run_id)
    stored = sum(counts.values())
    total = sum(int(result.total_issues or 0) for result in run.results)
    return {"stored_rows": stored, "total_issues": total}


def accepted_counts(db: Session, run_id: str) -> dict[str, int]:
    rows = (
        db.query(MismatchDetail.accepted, func.count(MismatchDetail.id))
        .join(TestResult, TestResult.id == MismatchDetail.test_result_id)
        .filter(TestResult.run_id == run_id)
        .group_by(MismatchDetail.accepted)
        .all()
    )
    counts = {"accepted": 0, "open": 0}
    for accepted, count in rows:
        counts["accepted" if accepted else "open"] += int(count)
    return counts


def export_dir(run_id: str) -> Path:
    path = EXPORT_ROOT / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_type_for(fmt: str) -> str:
    if fmt == "parquet":
        return "application/vnd.apache.parquet"
    if fmt == "json":
        return "application/x-ndjson"
    return "text/csv"


def export_filename(run_id: str, fmt: str, export_id: str | None = None) -> str:
    if fmt == "parquet":
        suffix = "parquet"
    elif fmt == "json":
        suffix = "jsonl"
    else:
        suffix = "csv"
    stem = f"all_differences_{run_id}"
    if export_id:
        stem += f"_{export_id}"
    return f"{stem}.{suffix}"


def write_stored_differences(db: Session, run: TestRun, fmt: str) -> tuple[Path, int]:
    path = export_dir(run.run_id) / export_filename(run.run_id, fmt, f"stored_{uuid.uuid4().hex[:8]}")
    with DifferenceWriter(path, fmt) as writer:
        for result in run.results:
            for mismatch in (
                db.query(MismatchDetail)
                .filter(MismatchDetail.test_result_id == result.id)
                .order_by(MismatchDetail.id)
                .yield_per(1000)
            ):
                writer.write({
                    "test_name": result.query_name,
                    "key_values": mismatch.key_values or {},
                    "column_name": mismatch.column_name or "",
                    "source_value": mismatch.source_value,
                    "target_value": mismatch.target_value,
                    "mismatch_type": mismatch.mismatch_type or "",
                    "delta": mismatch.delta,
                    "relative_delta": mismatch.relative_delta,
                })
        return path, writer.row_count


def create_or_reuse_export_job(db: Session, run_id: str, fmt: str) -> tuple[DifferenceExportJob, bool]:
    existing = (
        db.query(DifferenceExportJob)
        .filter(
            DifferenceExportJob.run_id == run_id,
            DifferenceExportJob.format == fmt,
            DifferenceExportJob.status.in_(["PENDING", "RUNNING"]),
        )
        .order_by(DifferenceExportJob.created_at.desc())
        .first()
    )
    if existing is not None:
        return existing, False
    completed = (
        db.query(DifferenceExportJob)
        .filter(
            DifferenceExportJob.run_id == run_id,
            DifferenceExportJob.format == fmt,
            DifferenceExportJob.status == "COMPLETED",
        )
        .order_by(DifferenceExportJob.completed_at.desc())
        .first()
    )
    if completed is not None and completed.artifact_path and Path(completed.artifact_path).exists():
        return completed, False
    job = DifferenceExportJob(
        export_id=str(uuid.uuid4()),
        run_id=run_id,
        format=fmt,
        status="PENDING",
        row_count=0,
        created_at=_utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job, True


def export_status_out(job: DifferenceExportJob) -> DifferenceExportStatusOut:
    return DifferenceExportStatusOut(
        export_id=job.export_id,
        run_id=job.run_id,
        format=job.format,
        status=job.status,
        row_count=int(job.row_count or 0),
        error_message=job.error_message,
        artifact_path=job.artifact_path,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        recomputed_at=job.recomputed_at,
        metadata=job.metadata_json,
    )


def run_difference_export_job(export_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.query(DifferenceExportJob).filter(DifferenceExportJob.export_id == export_id).first()
        if job is None or job.status == "COMPLETED":
            return
        job.status = "RUNNING"
        job.started_at = _utcnow()
        job.error_message = None
        db.commit()

        run = db.query(TestRun).filter(TestRun.run_id == job.run_id).first()
        if run is None:
            raise RuntimeError("Run not found")

        path = export_dir(job.run_id) / export_filename(job.run_id, job.format, job.export_id)
        row_count = write_recomputed_differences(db, run, job.format, path)
        job.status = "COMPLETED"
        job.artifact_path = str(path)
        job.row_count = row_count
        job.completed_at = _utcnow()
        job.recomputed_at = job.completed_at
        job.metadata_json = {
            "recomputed_at": job.recomputed_at.isoformat(),
            "warning": "Sources were rebuilt at export time; live/API data may have drifted since the original run.",
        }
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.query(DifferenceExportJob).filter(DifferenceExportJob.export_id == export_id).first()
        if job is not None:
            job.status = "FAILED"
            job.error_message = _friendly_export_error(exc)
            job.completed_at = _utcnow()
            db.commit()
    finally:
        db.close()


def _friendly_export_error(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    return str(exc) or exc.__class__.__name__


def write_recomputed_differences(db: Session, run: TestRun, fmt: str, path: Path) -> int:
    snapshot = run.config_snapshot or {}
    request_type = snapshot.get("compare_request_type")
    payload = snapshot.get("request") if isinstance(snapshot.get("request"), dict) else None
    with DifferenceWriter(path, fmt) as writer:
        if request_type == "sql":
            _write_sql_compare(db, payload or {}, writer)
        elif request_type == "bo_report":
            _write_bo_compare(db, payload or {}, writer)
        elif request_type == "recon_file":
            _write_recon_file_compare(db, payload or {}, writer)
        else:
            _write_reconciliation_run(db, run, writer)
        return writer.row_count


def _write_sql_compare(db: Session, payload: dict[str, Any], writer: DifferenceWriter) -> None:
    req = SQLCompareRequest(**payload)
    cfg_repo = ConfigRepository(db)
    cfg_a = cfg_repo.get(req.config_id_a)
    cfg_b = cfg_repo.get(req.config_id_b)
    if cfg_a is None or cfg_b is None:
        raise RuntimeError("Saved SQL compare config was deleted")
    env_a = resolve_connection(cfg_a.config_json or {}, req.connection_a, env_name=cfg_a.env_name or "")
    env_b = resolve_connection(cfg_b.config_json or {}, req.connection_b, env_name=cfg_b.env_name or "")
    engine_a = DBEngine(env_a)
    engine_b = DBEngine(env_b)
    try:
        df_a = _load_in_chunks(engine_a, req.query_a, req.key_columns or [], req.chunk_size)
        df_b = _load_in_chunks(engine_b, req.query_b, req.key_columns or [], req.chunk_size)
    finally:
        engine_a.dispose()
        engine_b.dispose()
    _write_tabular_differences(
        df_a,
        df_b,
        key_columns=req.key_columns or [],
        exclude_columns=req.exclude_columns or [],
        options=req.advanced,
        test_name=req.label_a or "file_a",
        writer=writer,
    )


def _write_bo_compare(db: Session, payload: dict[str, Any], writer: DifferenceWriter) -> None:
    req = BOCompareRequest(**payload)
    svc = CompareService(db, ConfigRepository(db))
    df_a = svc._load_bo_source(req.source_a, req.doc_id, req.report_id)
    df_b = svc._load_bo_source(req.source_b, req.doc_id, req.report_id)
    _write_tabular_differences(
        df_a,
        df_b,
        key_columns=req.key_columns or [],
        exclude_columns=req.exclude_columns or [],
        options=req.advanced,
        test_name=req.label_a or "bo_comparison",
        writer=writer,
    )


def _write_recon_file_compare(db: Session, payload: dict[str, Any], writer: DifferenceWriter) -> None:
    req = ReconFileCompareRequest(**payload)
    svc = CompareService(db, ConfigRepository(db))
    source_a = svc._load_recon_source(req, "a")
    source_b = svc._load_recon_source(req, "b")
    if isinstance(source_a, pd.DataFrame) and isinstance(source_b, pd.DataFrame):
        _write_tabular_differences(
            source_a,
            source_b,
            key_columns=req.key_columns or [],
            exclude_columns=req.exclude_columns or [],
            options=req.advanced,
            test_name=req.label_a or "file_a",
            writer=writer,
        )
        return
    if isinstance(source_a, dict) and isinstance(source_b, dict):
        for test_name in sorted(set(source_a) | set(source_b)):
            left = source_a.get(test_name, {})
            right = source_b.get(test_name, {})
            for metric in ("status", "source_row_count", "target_row_count", "total_issues"):
                if left.get(metric) != right.get(metric):
                    writer.write({
                        "test_name": test_name,
                        "key_values": {"test_name": test_name},
                        "column_name": metric,
                        "source_value": left.get(metric),
                        "target_value": right.get(metric),
                        "mismatch_type": "stat_diff",
                    })
        return
    raise RuntimeError("Both recompute sources must resolve to the same type")


def _write_reconciliation_run(db: Session, run: TestRun, writer: DifferenceWriter) -> None:
    snapshot = run.config_snapshot or {}
    job_names = snapshot.get("job_sequence") or [result.query_name for result in run.results]
    settings_data = snapshot.get("run_settings") or {}
    settings = RunSettings(**settings_data) if isinstance(settings_data, dict) else RunSettings()
    executor = RunExecutor(
        db=db,
        run_id=f"export-{run.run_id}",
        source_env=run.source_env or "source",
        target_env=run.target_env or "target",
        job_sequence=[],
        run_settings=settings,
        config_snapshot=snapshot,
    )
    for item in job_names:
        job_name = item.get("job_name") if isinstance(item, dict) else str(item)
        saved = db.query(SavedJob).filter(SavedJob.name == job_name).first()
        if saved is None or saved.job_type != "reconciliation":
            continue
        job = executor._job_to_definition(saved)
        src_engine, tgt_engine = executor._build_engines(job)
        df_a = src_engine.execute_query(job.query, job.params)
        df_b = tgt_engine.execute_query(job.query, job.params)
        _write_tabular_differences(
            df_a,
            df_b,
            key_columns=job.key_columns or settings.key_columns,
            exclude_columns=job.exclude_columns or settings.exclude_columns,
            options=settings,
            test_name=job.name,
            writer=writer,
        )


def _read_file_path(path: str) -> pd.DataFrame:
    return read_tabular(path=path)


def _write_tabular_differences(
    df_source: pd.DataFrame,
    df_target: pd.DataFrame,
    key_columns: list[str],
    exclude_columns: list[str],
    options: Any,
    test_name: str,
    writer: DifferenceWriter,
) -> None:
    key_columns = list(key_columns or [])
    if not key_columns:
        try:
            key_columns = CompareService._infer_key_columns(df_source, df_target)
        except HTTPException:
            df_source, df_target = CompareService._sort_for_positional_compare(
                df_source,
                df_target,
                exclude_columns or [],
            )
            df_source = df_source.copy()
            df_target = df_target.copy()
            df_source.insert(0, "__row__", range(1, len(df_source) + 1))
            df_target.insert(0, "__row__", range(1, len(df_target) + 1))
            key_columns = ["__row__"]
    CompareService._validate_key_columns(df_source, df_target, key_columns)

    normalizer = TypeNormalizer()
    df_source = normalizer.normalize(df_source)
    df_target = normalizer.normalize(df_target)
    df_source, df_target = _preprocess(df_source, df_target, options)

    excluded = {_column_key(col) for col in (exclude_columns or [])}
    common_columns = [
        col for col in df_source.columns
        if col in df_target.columns and _column_key(col) not in excluded
    ]
    for key in key_columns:
        if key not in common_columns:
            common_columns.insert(0, key)
    df_source = df_source[common_columns]
    df_target = df_target[common_columns]

    merged = pd.merge(
        df_source,
        df_target,
        on=key_columns,
        how="outer",
        indicator=True,
        suffixes=("_src", "_tgt"),
    )
    for _, row in merged[merged["_merge"] == "left_only"].iterrows():
        writer.write({
            "test_name": test_name,
            "key_values": {key: row.get(key) for key in key_columns},
            "column_name": "<row>",
            "source_value": "present",
            "target_value": "missing",
            "mismatch_type": "missing_in_target",
        })
    for _, row in merged[merged["_merge"] == "right_only"].iterrows():
        writer.write({
            "test_name": test_name,
            "key_values": {key: row.get(key) for key in key_columns},
            "column_name": "<row>",
            "source_value": "missing",
            "target_value": "present",
            "mismatch_type": "missing_in_source",
        })

    both = merged[merged["_merge"] == "both"]
    for col in [c for c in df_source.columns if c not in key_columns]:
        src_col = f"{col}_src" if f"{col}_src" in both.columns else col
        tgt_col = f"{col}_tgt" if f"{col}_tgt" in both.columns else col
        if src_col not in both.columns or tgt_col not in both.columns:
            continue
        mismatch_mask = _value_mismatch_mask(
            both,
            src_col,
            tgt_col,
            df_source[col],
            options,
            str(col),
        )
        for _, row in both.loc[mismatch_mask].iterrows():
            sv = row[src_col]
            tv = row[tgt_col]
            delta, relative_delta = numeric_delta(sv, tv)
            writer.write({
                "test_name": test_name,
                "key_values": {key: row.get(key) for key in key_columns},
                "column_name": col,
                "source_value": sv,
                "target_value": tv,
                "mismatch_type": "value_diff",
                "delta": delta,
                "relative_delta": relative_delta,
            })


def _preprocess(
    df_source: pd.DataFrame,
    df_target: pd.DataFrame,
    options: Any,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    return (
        normalize_string_columns(
            df_source,
            getattr(options, "case_insensitive_columns", []) or [],
            getattr(options, "whitespace_normalize_columns", []) or [],
        ),
        normalize_string_columns(
            df_target,
            getattr(options, "case_insensitive_columns", []) or [],
            getattr(options, "whitespace_normalize_columns", []) or [],
        ),
    )


def _value_mismatch_mask(
    both: pd.DataFrame,
    src_col: str,
    tgt_col: str,
    source_series: pd.Series,
    options: Any,
    column_name: str,
) -> pd.Series:
    null_equals_null = bool(getattr(options, "null_equals_null", True))
    float_tol = float(getattr(options, "float_tolerance", 1e-9) or 1e-9)
    column_tolerances = getattr(options, "column_tolerances", None) or {}
    tol = float(column_tolerances.get(column_name, float_tol))
    datetime_tolerance_seconds = float(getattr(options, "datetime_tolerance_seconds", 0.0) or 0.0)
    return value_mismatch_mask(
        both,
        src_col,
        tgt_col,
        source_series,
        null_equals_null=null_equals_null,
        float_tolerance=float_tol,
        column_tolerance=tol,
        datetime_tolerance_seconds=datetime_tolerance_seconds,
    )


