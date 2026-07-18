from __future__ import annotations

import csv
import asyncio
import io
import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, FileResponse, HTMLResponse, JSONResponse, StreamingResponse, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.routes.configs import _preserve_masked_secrets
from api.schemas import (
    BulkDecisionOut,
    BulkMismatchAcceptRequest,
    BulkMismatchDecisionOut,
    BulkMismatchDecisionRequest,
    BulkOverrideRequest,
    ColumnMismatchStatOut,
    DifferenceExportRequest,
    DifferenceExportStatusOut,
    DrilldownOut,
    DrilldownRequest,
    DrilldownRow,
    GeneratedArtifactOut,
    MismatchAcceptOut,
    MismatchAcceptRequest,
    MismatchColumnInsight,
    MismatchDecisionOut,
    MismatchOut,
    MismatchRejectRequest,
    MismatchSortField,
    MismatchStatusFilter,
    MismatchTestInsight,
    MismatchTypeFilter,
    RunCompareOut,
    RunDetailOut,
    RunMismatchInsightsOut,
    RunProgressOut,
    RunStatusOut,
    RunStepOut,
    RunStepReleaseRequest,
    RunTrigger,
    TestCompareOut,
    TestResultOut,
    TestResultOverrideRequest,
    TestSuiteTrigger,
)
from api.services.run_executor import RunExecutor
from api.services.pytest_runner import PytestRunExecutor
from etl_framework.repository.repository import ConfigRepository, JobRepository, RunRepository, RunStepRepository
from api.services.artifact_service import ArtifactService
from api.services.artifact_views import render_logs_html, render_metrics_html
from api.services.audit_service import AuditService
from api.services.log_parser import detect_log_level, parse_log_events, filter_log_events
from api.services.mismatch_export import (
    collect_mismatch_rows,
    mismatch_csv_response,
    mismatch_xlsx_response,
)
from api.services.difference_export import (
    accepted_counts,
    create_or_reuse_export_job,
    export_filename,
    export_status_out,
    media_type_for,
    run_difference_export_job,
    stored_completeness_summary,
    stored_detail_counts,
    stored_rows_are_complete,
    validate_difference_format,
    write_stored_differences,
)
from api.services.run_report import build_run_report_snapshot
from etl_framework.config.models import resolve_connection as _resolve_connection
from etl_framework.repository.models import TERMINAL_STATUSES as _TERMINAL
from etl_framework.runner.job_validation import validate_job_definition

router = APIRouter(tags=["runs"])


_TREND_CACHE_TTL_SECONDS = 30
_TREND_CACHE: dict[tuple, tuple[float, dict]] = {}


def _test_result_out(result) -> TestResultOut:
    mismatch_summary = getattr(result, "mismatch_summary", None)
    return TestResultOut(
        id=result.id,
        query_name=result.query_name,
        status=result.status,
        effective_status=result.effective_status,
        duration_seconds=result.duration_seconds,
        source_row_count=result.source_row_count,
        target_row_count=result.target_row_count,
        value_mismatch_count=result.value_mismatch_count,
        missing_in_target_count=result.missing_in_target_count,
        missing_in_source_count=result.missing_in_source_count,
        error_message=result.error_message,
        executed_at=result.executed_at,
        source_file_name=result.source_file_name,
        target_file_name=result.target_file_name,
        override_reason=result.override_reason,
        overridden_by=result.override_by,
        override_at=result.override_at,
        sample_rows=result.sample_rows,
        segment_summary=result.segment_summary,
        mismatch_summary=mismatch_summary,
        column_stats=_column_stats_from_summary(
            mismatch_summary,
            source_row_count=result.source_row_count,
            target_row_count=result.target_row_count,
        ),
    )


def _column_stats_from_summary(
    mismatch_summary: dict | None,
    source_row_count: int = 0,
    target_row_count: int = 0,
) -> list[ColumnMismatchStatOut]:
    if not isinstance(mismatch_summary, dict):
        return []
    raw_mismatches = mismatch_summary.get("by_column") or {}
    raw_compared = mismatch_summary.get("compared_rows_by_column") or {}
    if not isinstance(raw_mismatches, dict):
        raw_mismatches = {}
    if not isinstance(raw_compared, dict):
        raw_compared = {}
    columns = set(str(col) for col in raw_compared) | set(str(col) for col in raw_mismatches)
    fallback_compared = max(int(source_row_count or 0), int(target_row_count or 0))
    rows: list[ColumnMismatchStatOut] = []
    for column in columns:
        try:
            mismatch_count = int(raw_mismatches.get(column, 0) or 0)
        except (TypeError, ValueError):
            mismatch_count = 0
        try:
            compared_rows = int(raw_compared.get(column, fallback_compared) or 0)
        except (TypeError, ValueError):
            compared_rows = fallback_compared
        if compared_rows <= 0 and mismatch_count > 0:
            compared_rows = max(fallback_compared, mismatch_count)
        match_pct = None
        if compared_rows > 0:
            match_pct = round(max(0.0, 100.0 * (1.0 - (mismatch_count / compared_rows))), 4)
        rows.append(ColumnMismatchStatOut(
            column=column,
            mismatch_count=mismatch_count,
            compared_rows=compared_rows,
            match_pct=match_pct,
        ))
    rows.sort(key=lambda item: (-item.mismatch_count, item.column == "<row>", item.column.lower()))
    return rows


def _wants_html(request: Request, fmt: str | None) -> bool:
    if fmt:
        return fmt.lower() == "html"
    user_agent = request.headers.get("user-agent", "").lower()
    if "testclient" in user_agent:
        return False
    return "text/html" in request.headers.get("accept", "").lower()




def _metrics_from_run(run) -> dict:
    return build_run_report_snapshot(run).to_metrics()


def _run_status_out(run) -> RunStatusOut:
    snapshot = build_run_report_snapshot(run)
    return RunStatusOut(
        run_id=snapshot.run_id,
        status=snapshot.status,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        total_tests=snapshot.total_tests,
        passed=snapshot.passed,
        failed=snapshot.failed,
        slow=snapshot.slow,
        error=snapshot.error,
        run_type=snapshot.run_type,
        pair_id=snapshot.pair_id,
    )


def _validate_connection_name(cfg, name: str | None, field: str) -> None:
    if name is None or cfg is None:
        return
    available = list((cfg.config_json or {}).get("connections", {}).keys())
    if name not in available:
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"{field} '{name}' not found in config connections",
                "available": available,
            },
        )


def _resolve_connection_or_422(source: dict, name: str | None, env_name: str, field: str):
    try:
        return _resolve_connection(source, name, env_name=env_name)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"message": f"{field} '{name}' failed validation", "errors": exc.errors()},
        ) from exc


def _snapshot_from_trigger(body: RunTrigger, db: Session) -> dict:
    cfg_data = dict(body.config_data or {})
    cfg = ConfigRepository(db).get(body.config_id) if body.config_id is not None else None
    if body.config_id is not None and cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")
    if cfg is not None:
        # config_data may be an echo of the masked GET /api/configs response
        # (e.g. the Launch page's Saved Config dropdown) — restore the real
        # stored secret wherever the client sent back the display mask.
        cfg_data = _preserve_masked_secrets(cfg_data, cfg.config_json)
        cfg_data = {**(cfg.config_json or {}), **cfg_data}

    snapshot = dict(cfg_data)
    if cfg is not None:
        snapshot.update({
            "config_id": cfg.id,
            "config_name": cfg.name,
            "env_name": cfg.env_name,
        })

    if "source_credentials" not in snapshot:
        _validate_connection_name(cfg, body.source_connection, "source_connection")
        if body.source_connection:
            src = _resolve_connection_or_422(
                cfg.config_json if cfg else cfg_data,
                body.source_connection,
                body.source_env,
                "source_connection",
            )
            snapshot["source_credentials"] = {**src.model_dump(), "name": body.source_env}
        else:
            snapshot["source_credentials"] = {
                "name": body.source_env,
                **{k: v for k, v in cfg_data.items() if k != "connections"},
            }
    if "target_credentials" not in snapshot:
        _validate_connection_name(cfg, body.target_connection, "target_connection")
        if body.target_connection:
            tgt = _resolve_connection_or_422(
                cfg.config_json if cfg else cfg_data,
                body.target_connection,
                body.target_env,
                "target_connection",
            )
            snapshot["target_credentials"] = {**tgt.model_dump(), "name": body.target_env}
        else:
            snapshot["target_credentials"] = {
                "name": body.target_env,
                **{k: v for k, v in cfg_data.items() if k != "connections"},
            }
    if "bo_credentials" not in snapshot:
        snapshot["bo_credentials"] = {"name": "bo", **cfg_data}
    if "automic_credentials" not in snapshot:
        snapshot["automic_credentials"] = {"name": "automic", **cfg_data}
    return snapshot


def _job_name_from_sequence_item(item) -> str:
    return item.job_name if hasattr(item, "job_name") else str(item)


def _saved_job_to_validation_dict(job) -> dict:
    return {
        "name": job.name,
        "description": job.description,
        "job_type": job.job_type,
        "query": job.query,
        "key_columns": job.key_columns or [],
        "exclude_columns": job.exclude_columns or [],
        "source_env": job.source_env,
        "target_env": job.target_env,
        "params": job.params or {},
        "enabled": job.enabled,
    }


def _validate_saved_jobs_for_launch(db: Session, ordered_jobs: list) -> None:
    job_repo = JobRepository(db)
    errors: list[dict] = []
    for item in ordered_jobs:
        name = _job_name_from_sequence_item(item)
        saved = job_repo.get(name)
        if saved is None:
            continue
        for issue in validate_job_definition(_saved_job_to_validation_dict(saved)):
            if issue.severity.value == "error":
                errors.append({
                    "job_name": name,
                    "field": issue.field,
                    "message": issue.message,
                    "severity": issue.severity.value,
                })
    if errors:
        raise HTTPException(status_code=422, detail=errors)


def _execute_run(
    run_id: str,
    job_sequence: list[str],
    source_env: str,
    target_env: str,
    run_settings,
    config_snapshot: dict | None,
    session_factory: Callable[[], Session] | None = None,
) -> None:
    from etl_framework.repository.database import SessionLocal
    from etl_framework.utils.context import set_run_id

    set_run_id(run_id)

    db = (session_factory or SessionLocal)()
    try:
        RunExecutor(
            db=db,
            run_id=run_id,
            source_env=source_env,
            target_env=target_env,
            job_sequence=job_sequence,
            run_settings=run_settings,
            config_snapshot=config_snapshot,
        ).execute()
    finally:
        db.close()


def _run_pytest(
    run_id: str,
    pytest_args: list[str],
    session_factory=None,
) -> None:
    from etl_framework.repository.database import SessionLocal
    from etl_framework.utils.context import set_run_id

    set_run_id(run_id)
    db = (session_factory or SessionLocal)()
    try:
        PytestRunExecutor(db=db, run_id=run_id, pytest_args=pytest_args).execute()
    finally:
        db.close()


@router.get("", response_model=list[RunStatusOut])
def list_runs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    run_type: str | None = None,
    db: Session = Depends(get_session),
):
    repo = RunRepository(db)
    runs = repo.list_runs(limit=limit, offset=offset, status=status, run_type=run_type)
    return [_run_status_out(r) for r in runs]


@router.post("/test-suite", response_model=RunStatusOut, status_code=202)
def trigger_test_suite(
    body: TestSuiteTrigger,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    run_id = str(uuid.uuid4())
    RunRepository(db).create_run(
        run_id=run_id,
        source_env=None,
        target_env=None,
        run_type="test_suite",
    )
    background_tasks.add_task(_run_pytest, run_id, body.pytest_args)
    return RunStatusOut(run_id=run_id, status="PENDING", run_type="test_suite")


@router.post("", response_model=RunStatusOut, status_code=202)
def trigger_run(
    body: RunTrigger,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_session),
):
    run_id = str(uuid.uuid4())
    repo = RunRepository(db)
    ordered_jobs = body.job_sequence or body.job_names
    _validate_saved_jobs_for_launch(db, ordered_jobs)
    run_settings = body.run_settings.model_dump()
    config_snapshot = _snapshot_from_trigger(body, db)
    if ordered_jobs:
        # Serialize SequenceStep objects to plain dicts for JSON storage
        config_snapshot["job_sequence"] = [
            s.model_dump() if hasattr(s, "model_dump") else s
            for s in ordered_jobs
        ]
    config_snapshot["run_settings"] = run_settings
    repo.create_run(
        run_id=run_id,
        source_env=body.source_env,
        target_env=body.target_env,
        config_snapshot=config_snapshot or None,
    )
    AuditService(db).log(
        request,
        "run.created",
        "run",
        run_id,
        {
            "source_env": body.source_env,
            "target_env": body.target_env,
            "job_sequence": config_snapshot.get("job_sequence"),
            "config_id": body.config_id,
        },
    )
    background_tasks.add_task(
        _execute_run,
        run_id,
        ordered_jobs,
        body.source_env,
        body.target_env,
        body.run_settings,
        config_snapshot,
    )
    return RunStatusOut(run_id=run_id, status="PENDING")


@router.post("/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    accepted = repo.request_cancel(run_id)
    return {"run_id": run_id, "cancel_requested": accepted}


@router.get("/{run_id}/status", response_model=RunStatusOut)
def get_run_status(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_status_out(run)


@router.get("/{run_id}/artifacts", response_model=list[GeneratedArtifactOut])
def list_run_artifacts(run_id: str, db: Session = Depends(get_session)):
    run = RunRepository(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    snapshot = build_run_report_snapshot(run)
    artifacts = []

    artifacts.append(
        GeneratedArtifactOut(
            name=f"report_{run_id}.html",
            artifact_type="report",
            path=f"/api/runs/{run_id}/report",
            created_at=datetime.now(timezone.utc)
        )
    )

    metrics_path = Path("logs") / f"metrics_{run_id}.json"
    if metrics_path.exists() or snapshot.has_result_rows:
        artifacts.append(
            GeneratedArtifactOut(
                name=metrics_path.name if metrics_path.exists() else f"metrics_{run_id}.json",
                artifact_type="metrics",
                path=f"/api/runs/{run_id}/metrics",
                created_at=datetime.fromtimestamp(
                    metrics_path.stat().st_mtime, tz=timezone.utc
                ) if metrics_path.exists() else datetime.now(timezone.utc),
            )
        )
    log_path = Path("logs") / "etl_framework.log"
    if log_path.exists():
        artifacts.append(
            GeneratedArtifactOut(
                name=log_path.name,
                artifact_type="log",
                path=f"/api/runs/{run_id}/logs",
                created_at=datetime.fromtimestamp(
                    log_path.stat().st_mtime, tz=timezone.utc
                ),
            )
        )
    return artifacts


@router.get("/{run_id}/metrics")
def get_run_metrics(
    run_id: str,
    request: Request,
    format: str | None = None,
    db: Session = Depends(get_session),
):
    run = RunRepository(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    snapshot = build_run_report_snapshot(run)
    metrics_path = Path("logs") / f"metrics_{run_id}.json"
    if snapshot.has_result_rows:
        metrics = snapshot.to_metrics()
    elif metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8", errors="replace"))
    elif snapshot.raw_status in _TERMINAL and snapshot.raw_total_tests > 0:
        metrics = snapshot.to_metrics()
    else:
        raise HTTPException(status_code=404, detail="Metrics not found")
    if _wants_html(request, format):
        return HTMLResponse(render_metrics_html(metrics))
    return metrics


@router.get("/{run_id}/logs")
def get_run_logs(
    run_id: str,
    request: Request,
    q: str = "",
    level: str = "",
    limit: int = 500,
    scope: str = "run",
    format: str | None = None,
    db: Session = Depends(get_session),
):
    if RunRepository(db).get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    log_path = Path("logs") / "etl_framework.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    scope_l = scope.lower().strip() or "run"
    if scope_l not in {"run", "all"}:
        raise HTTPException(status_code=400, detail="scope must be 'run' or 'all'")
    total_events = len(parse_log_events(text))
    lines = filter_log_events(
        text,
        run_id=run_id if scope_l == "run" else "",
        query=q,
        level=level,
        limit=limit,
    )
    fmt = (format or "").lower()
    if fmt == "json":
        return {
            "run_id": run_id,
            "query": q,
            "level": level,
            "scope": scope_l,
            "total_lines": len(text.splitlines()),
            "total_events": total_events,
            "matched_lines": len(lines),
            "lines": lines,
        }
    if _wants_html(request, format):
        return HTMLResponse(render_logs_html(
            run_id=run_id,
            lines=lines,
            query=q,
            level=level.upper().strip(),
            total_lines=len(text.splitlines()),
            total_events=total_events,
            scope=scope_l,
        ))
    if q or level or scope_l == "run":
        return PlainTextResponse("\n".join(row["text"] for row in lines))
    return PlainTextResponse(text)


@router.get("/{run_id}/report", response_class=FileResponse)
def get_run_report(run_id: str, db: Session = Depends(get_session)):
    service = ArtifactService(repository=RunRepository(db))
    report_path = service.generate_html_report(run_id)
    return FileResponse(report_path, media_type="text/html")


@router.get("/{run_id}/progress", response_model=RunProgressOut)
def get_run_progress(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    total = run.total_tests or 0
    completed = repo.count_completed_results(run_id)
    percent = int(completed / total * 100) if total > 0 else 0
    return RunProgressOut(
        run_id=run.run_id,
        status=run.status,
        total_tests=total,
        completed_tests=completed,
        current_job=repo.get_current_job(run_id),
        percent_complete=min(percent, 100),
    )


@router.get("/{run_id}/results/{result_id}/mismatches", response_model=list[MismatchOut])
def list_result_mismatches(
    run_id: str,
    result_id: int,
    response: Response,
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
    column: str | None = None,
    mismatch_type: MismatchTypeFilter | None = None,
    accepted: bool | None = None,
    rejected: bool | None = None,
    status: MismatchStatusFilter | None = None,
    sort: MismatchSortField = MismatchSortField.id,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    mismatch_type_value = mismatch_type.value if mismatch_type else None
    status_value = status.value if status else None
    rows = repo.list_mismatches(
        result_id=result_id,
        limit=limit,
        offset=offset,
        search=search,
        column=column,
        mismatch_type=mismatch_type_value,
        accepted=accepted,
        rejected=rejected,
        status=status_value,
        sort=sort.value,
    )
    total = repo.count_mismatches(
        result_id=result_id,
        search=search,
        column=column,
        mismatch_type=mismatch_type_value,
        accepted=accepted,
        rejected=rejected,
        status=status_value,
    )
    response.headers["X-Total-Count"] = str(total)

    test_result = db.get(TestResult, result_id)
    if test_result is not None and test_result.run_id == run_id:
        stored_total = repo.count_mismatches(result_id=result_id)
        stored_complete = stored_total >= int(test_result.total_issues or 0)
        response.headers["X-Stored-Complete"] = "true" if stored_complete else "false"

    return [
        MismatchOut(
            id=m.id,
            column_name=m.column_name,
            key_values=m.key_values,
            source_value=m.source_value,
            target_value=m.target_value,
            mismatch_type=m.mismatch_type,
            delta=m.delta,
            relative_delta=m.relative_delta,
            accepted=m.accepted,
            accepted_note=m.accepted_note,
            accepted_at=m.accepted_at,
            accepted_by=m.accepted_by,
            rejected=m.rejected,
            rejected_note=m.rejected_note,
            rejected_at=m.rejected_at,
            rejected_by=m.rejected_by,
        )
        for m in rows
    ]


@router.get("/{run_id}/mismatches/insights", response_model=RunMismatchInsightsOut)
def get_run_mismatch_insights(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    snapshot = build_run_report_snapshot(run)
    stored_counts = stored_detail_counts(db, run_id)

    column_totals: dict[str, int] = {}
    type_totals: dict[str, int] = {"value_diff": 0, "missing_in_target": 0, "missing_in_source": 0}
    tests: list[MismatchTestInsight] = []

    for result in snapshot.results:
        for column, count in result.mismatch_by_column.items():
            column_totals[column] = column_totals.get(column, 0) + count
        for mtype, count in result.mismatch_by_type.items():
            type_totals[mtype] = type_totals.get(mtype, 0) + count
        stored_rows = stored_counts.get(result.id, 0) if result.id is not None else 0
        tests.append(MismatchTestInsight(
            result_id=result.id or 0,
            query_name=result.query_name,
            total_issues=result.total_issues,
            stored_rows=stored_rows,
            stored_complete=stored_rows >= result.total_issues,
        ))

    top_columns = sorted(column_totals.items(), key=lambda kv: -kv[1])[:10]
    accepted = accepted_counts(db, run_id)

    return RunMismatchInsightsOut(
        run_id=run_id,
        top_columns=[MismatchColumnInsight(column=c, count=n) for c, n in top_columns],
        type_totals=type_totals,
        accepted_count=accepted["accepted"],
        open_count=accepted["open"],
        tests=tests,
    )


@router.get("/{run_id}/mismatches/download")
def download_mismatches(
    run_id: str,
    format: str = "csv",
    db: Session = Depends(get_session),
):
    """Download all mismatch details for a run as CSV, XLSX, or HTML report."""
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    snapshot = build_run_report_snapshot(run)

    if format == "html":
        service = ArtifactService(repository=repo)
        report_path = service.generate_html_report(run_id)
        return FileResponse(
            report_path,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="report_{run_id}.html"'},
        )

    rows = collect_mismatch_rows(repo, snapshot)
    if format == "xlsx":
        return mismatch_xlsx_response(run_id, snapshot, rows)
    return mismatch_csv_response(run_id, rows)


@router.get("/{run_id}/differences/download")
def download_all_differences(
    run_id: str,
    format: str = "csv",
    db: Session = Depends(get_session),
):
    """Download all differences when stored DB rows are known to be complete."""
    fmt = validate_difference_format(format)
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not stored_rows_are_complete(db, run):
        summary = stored_completeness_summary(db, run)
        return JSONResponse(
            status_code=202,
            content={
                "requires_export_job": True,
                "run_id": run_id,
                "format": fmt,
                **summary,
            },
        )
    try:
        path, _ = write_stored_differences(db, run, fmt)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type=media_type_for(fmt),
        headers={"Content-Disposition": f'attachment; filename="{export_filename(run_id, fmt)}"'},
    )


@router.post("/{run_id}/exports", response_model=DifferenceExportStatusOut, status_code=202)
def create_difference_export(
    run_id: str,
    body: DifferenceExportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
):
    fmt = body.format
    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    job, created = create_or_reuse_export_job(db, run_id, fmt)
    if created and job.status == "PENDING":
        background_tasks.add_task(run_difference_export_job, job.export_id)
    return export_status_out(job)


@router.get("/{run_id}/exports/{export_id}", response_model=DifferenceExportStatusOut)
def get_difference_export(
    run_id: str,
    export_id: str,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import DifferenceExportJob

    job = (
        db.query(DifferenceExportJob)
        .filter(DifferenceExportJob.run_id == run_id, DifferenceExportJob.export_id == export_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")
    return export_status_out(job)


@router.get("/{run_id}/exports/{export_id}/download")
def download_difference_export(
    run_id: str,
    export_id: str,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import DifferenceExportJob

    job = (
        db.query(DifferenceExportJob)
        .filter(DifferenceExportJob.run_id == run_id, DifferenceExportJob.export_id == export_id)
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Export job not found")
    if job.status != "COMPLETED" or not job.artifact_path:
        raise HTTPException(status_code=409, detail=f"Export job is {job.status}")
    path = Path(job.artifact_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Export artifact not found")
    return FileResponse(
        path,
        media_type=media_type_for(job.format),
        headers={"Content-Disposition": f'attachment; filename="{export_filename(run_id, job.format, export_id)}"'},
    )


@router.patch(
    "/{run_id}/results/{result_id}/mismatches/{mismatch_id}/accept",
    response_model=MismatchAcceptOut,
)
def accept_mismatch(
    run_id: str,
    result_id: int,
    mismatch_id: int,
    body: MismatchAcceptRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import MismatchDetail, TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    tr = db.get(TestResult, result_id)
    if tr is None or tr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")
    md = db.get(MismatchDetail, mismatch_id)
    if md is None or md.test_result_id != result_id:
        raise HTTPException(status_code=404, detail="Mismatch not found")
    updated, status_changed = repo.accept_mismatch(mismatch_id, body.note, body.accepted_by)
    AuditService(db).log(
        request,
        "mismatch.accepted",
        "mismatch",
        mismatch_id,
        {
            "run_id": run_id,
            "result_id": result_id,
            "note": body.note,
            "accepted_by": body.accepted_by,
            "result_status_updated": status_changed,
        },
        actor=body.accepted_by,
    )
    return MismatchAcceptOut(
        id=updated.id,
        accepted=updated.accepted,
        accepted_note=updated.accepted_note,
        accepted_at=updated.accepted_at,
        accepted_by=updated.accepted_by,
        result_status_updated=status_changed,
    )


@router.patch(
    "/{run_id}/results/{result_id}/mismatches/{mismatch_id}/reject",
    response_model=MismatchDecisionOut,
)
def reject_mismatch(
    run_id: str,
    result_id: int,
    mismatch_id: int,
    body: MismatchRejectRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import MismatchDetail, TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    tr = db.get(TestResult, result_id)
    if tr is None or tr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")
    md = db.get(MismatchDetail, mismatch_id)
    if md is None or md.test_result_id != result_id:
        raise HTTPException(status_code=404, detail="Mismatch not found")
    updated, status_changed = repo.reject_mismatch(mismatch_id, body.note, body.rejected_by)
    AuditService(db).log(
        request,
        "mismatch.rejected",
        "mismatch",
        mismatch_id,
        {
            "run_id": run_id,
            "result_id": result_id,
            "note": body.note,
            "rejected_by": body.rejected_by,
        },
        actor=body.rejected_by,
    )
    return MismatchDecisionOut(
        id=updated.id,
        accepted=updated.accepted,
        accepted_note=updated.accepted_note,
        accepted_at=updated.accepted_at,
        accepted_by=updated.accepted_by,
        rejected=updated.rejected,
        rejected_note=updated.rejected_note,
        rejected_at=updated.rejected_at,
        rejected_by=updated.rejected_by,
        result_status_updated=status_changed,
    )


@router.post(
    "/{run_id}/results/{result_id}/mismatches/bulk-decide",
    response_model=BulkMismatchDecisionOut,
)
def bulk_decide_mismatches(
    run_id: str,
    result_id: int,
    body: BulkMismatchDecisionRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    tr = db.get(TestResult, result_id)
    if tr is None or tr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")

    summary = repo.bulk_decide_mismatches(
        result_id,
        decision=body.decision,
        note=body.note,
        decided_by=body.decided_by,
        search=body.search,
        column=body.column,
        mismatch_type=body.mismatch_type.value if body.mismatch_type else None,
        status=body.status.value if body.status else None,
    )
    AuditService(db).log(
        request,
        "mismatch.bulk_decided",
        "test_result",
        result_id,
        {
            "run_id": run_id,
            "decision": body.decision,
            "note": body.note,
            "decided_by": body.decided_by,
            "filters": {
                "search": body.search,
                "column": body.column,
                "mismatch_type": body.mismatch_type.value if body.mismatch_type else None,
                "status": body.status.value if body.status else None,
            },
            "matched_count": summary["matched_count"],
            "decided_count": summary["decided_count"],
        },
        actor=body.decided_by,
    )
    return BulkMismatchDecisionOut(**summary)


@router.post("/{run_id}/results/bulk-accept", response_model=BulkDecisionOut)
def bulk_accept_mismatches(
    run_id: str,
    body: BulkMismatchAcceptRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    for result_id in body.result_ids:
        tr = db.get(TestResult, result_id)
        if tr is None or tr.run_id != run_id:
            raise HTTPException(status_code=404, detail=f"Result {result_id} not found")

    summary = repo.bulk_accept_mismatches(body.result_ids, body.note, body.accepted_by)
    AuditService(db).log(
        request,
        "mismatch.bulk_accepted",
        "run",
        run_id,
        {
            "result_ids": body.result_ids,
            "note": body.note,
            "accepted_by": body.accepted_by,
            "accepted_mismatch_count": summary["accepted_mismatch_count"],
            "result_status_updated": summary["result_status_updated"],
        },
        actor=body.accepted_by,
    )
    return BulkDecisionOut(**summary)


@router.post("/{run_id}/results/bulk-override", response_model=list[TestResultOut])
def bulk_set_test_result_override(
    run_id: str,
    body: BulkOverrideRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    updated_results = []
    actor = AuditService.actor_from_request(request) or "unknown"
    for result_id in body.result_ids:
        tr = db.get(TestResult, result_id)
        if tr is None or tr.run_id != run_id:
            raise HTTPException(status_code=404, detail=f"Result {result_id} not found")
        if tr.status != "FAILED":
            raise HTTPException(
                status_code=409,
                detail=f"Test result {result_id} is not FAILED (status: {tr.status})",
            )
        tr.override_status = "PASSED"
        tr.override_reason = body.reason
        tr.override_by = actor
        tr.override_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(tr)
        AuditService(db).log(
            request,
            "test_result.override_set",
            "test_result",
            result_id,
            {
                "run_id": run_id,
                "query_name": tr.query_name,
                "original_status": tr.status,
                "override_status": "PASSED",
                "reason": body.reason,
                "overridden_by": actor,
            },
            actor=actor,
        )
        updated_results.append(_test_result_out(tr))

    return updated_results


@router.post("/{run_id}/results/{result_id}/drilldown", response_model=DrilldownOut)
def drilldown_result(
    run_id: str,
    result_id: int,
    payload: DrilldownRequest,
    db: Session = Depends(get_session),
):
    """Re-query both sides grouped by a segment column — live counts.

    Unlike the stored `segment_summary` attached at run completion (see
    ReconciliationEngine._attach_segment_values), this re-runs both sides'
    queries fresh so the caller can catch data drift since the original run.
    """
    from etl_framework.repository.models import TestResult, TestRun, SavedJob
    from api.services.run_executor import RunExecutor
    from api.schemas import RunSettings

    tr = db.get(TestResult, result_id)
    if tr is None or tr.run_id != run_id:
        raise HTTPException(status_code=404, detail="Result not found")
    run = db.query(TestRun).filter(TestRun.run_id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    saved = db.query(SavedJob).filter(SavedJob.name == tr.query_name).first()
    if saved is None:
        raise HTTPException(status_code=404, detail=f"Job '{tr.query_name}' not found")
    if saved.job_type != "reconciliation":
        raise HTTPException(status_code=400,
                            detail="Drill-down is only supported for reconciliation jobs")

    snapshot = run.config_snapshot or {}
    ex = RunExecutor(
        db=db, run_id=f"drilldown-{run_id}",
        source_env=run.source_env or "source",
        target_env=run.target_env or "target",
        job_sequence=[],
        run_settings=RunSettings(
            use_live_connections=bool(snapshot.get("source_credentials")),
        ),
        config_snapshot=snapshot,
    )
    job_def = ex._job_to_definition(saved)
    seg = payload.segment_column

    try:
        src_engine, tgt_engine = ex._build_engines(job_def)
        df_src = src_engine.execute_query(job_def.query, job_def.params)
        df_tgt = tgt_engine.execute_query(job_def.query, job_def.params)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Drill-down query failed: {exc}")

    def counts(df) -> dict[str, int]:
        if df is None or df.empty or seg not in df.columns:
            return {}
        grouped = df.groupby(df[seg].astype(object).where(df[seg].notna(), "(null)")).size()
        return {str(k): int(v) for k, v in grouped.items()}

    src_counts = counts(df_src)
    tgt_counts = counts(df_tgt)
    if not src_counts and not tgt_counts:
        raise HTTPException(status_code=400,
                            detail=f"Segment column '{seg}' not present in either side")

    rows = []
    for value in sorted(set(src_counts) | set(tgt_counts)):
        s, t = src_counts.get(value, 0), tgt_counts.get(value, 0)
        rows.append(DrilldownRow(value=value, source_count=s, target_count=t, delta=t - s))
    rows.sort(key=lambda r: -abs(r.delta))
    return DrilldownOut(segment_column=seg, job_name=tr.query_name, rows=rows)


@router.get("/trends")
def get_trends(
    job_name: str,
    metric: str = "mismatch_rate",
    window: int = 30,
    db: Session = Depends(get_session),
):
    from datetime import timedelta
    import statistics
    from sqlalchemy import func
    from etl_framework.repository.models import TestRun, TestResult

    signature = (
        db.query(func.count(TestResult.id), func.max(TestResult.id))
        .join(TestRun, TestRun.run_id == TestResult.run_id)
        .filter(TestResult.query_name == job_name)
        .first()
    )
    cache_key = (id(db.bind), job_name, metric, window, signature[0], signature[1])
    cached = _TREND_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < _TREND_CACHE_TTL_SECONDS:
        return cached[1]
    if len(_TREND_CACHE) > 500:
        cutoff = now - _TREND_CACHE_TTL_SECONDS
        for k in [k for k, (ts, _) in _TREND_CACHE.items() if ts < cutoff]:
            _TREND_CACHE.pop(k, None)

    cutoff = datetime.now(timezone.utc) - timedelta(days=window)
    rows = (
        db.query(TestResult.value_mismatch_count, TestResult.missing_in_target_count,
                 TestResult.missing_in_source_count, TestResult.source_row_count,
                 TestResult.duration_seconds, TestRun.completed_at)
        .join(TestRun, TestRun.run_id == TestResult.run_id)
        .filter(TestResult.query_name == job_name)
        .filter(TestRun.completed_at.isnot(None))
        .order_by(TestRun.completed_at)
        .all()
    )

    points = []
    for r in rows:
        completed = r.completed_at
        if completed and completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        if completed and completed < cutoff:
            continue
        total_issues = (r.value_mismatch_count or 0) + (r.missing_in_target_count or 0) + (r.missing_in_source_count or 0)
        src = r.source_row_count or 0
        if metric == "mismatch_rate":
            value = total_issues / src if src else 0.0
        elif metric == "row_count_delta":
            value = float(r.missing_in_target_count or 0) - float(r.missing_in_source_count or 0)
        elif metric == "duration_seconds":
            value = float(r.duration_seconds or 0)
        else:
            value = float(total_issues)
        date_str = completed.strftime("%Y-%m-%d") if completed else "unknown"
        points.append({"date": date_str, "value": round(value, 6)})

    drift_detected = False
    if len(points) >= 3:
        vals = [p["value"] for p in points]
        mean = statistics.mean(vals[:-1])
        try:
            stdev = statistics.stdev(vals[:-1])
            last = vals[-1]
            if stdev > 0:
                drift_detected = (last - mean) > 2 * stdev
            elif last != mean:
                drift_detected = True
        except statistics.StatisticsError:
            pass

    payload = {"job_name": job_name, "metric": metric, "window": window, "points": points, "drift_detected": drift_detected}
    _TREND_CACHE[cache_key] = (now, payload)
    return payload


@router.get("/{run_id}/stream")
async def stream_run(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def events():
        last_payload = ""
        step_repo = RunStepRepository(db)
        while True:
            db.expire_all()  # force re-fetch; identity map caches stale status without this
            run = repo.get_run(run_id)
            if run is None:
                yield "event: error\ndata: {\"detail\":\"Run not found\"}\n\n"
                break
            total = run.total_tests or 0
            completed = repo.count_completed_results(run_id)
            percent = int(completed / total * 100) if total > 0 else (100 if run.status in _TERMINAL else 0)

            # Determine held/current step from run_steps table
            held_step: int | None = None
            current_step: int | None = None
            steps = step_repo.list_steps(run_id)
            for s in steps:
                if s.status == "HELD":
                    held_step = s.step_index
                    current_step = s.step_index
                    break
                if s.status == "RUNNING":
                    current_step = s.step_index

            payload = {
                "run_id": run.run_id,
                "status": run.status,
                "total_tests": total,
                "completed_tests": completed,
                "current_job": repo.get_current_job(run_id),
                "percent_complete": min(percent, 100),
                "current_step": current_step,
                "held_step": held_step,
            }
            payload_text = json.dumps(payload, default=str)
            if payload_text != last_payload:
                yield f"event: progress\ndata: {payload_text}\n\n"
                last_payload = payload_text
            if run.status in _TERMINAL:
                yield f"event: done\ndata: {payload_text}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/compare", response_model=RunCompareOut)
def compare_runs(run_a: str, run_b: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    ra = repo.get_run(run_a)
    rb = repo.get_run(run_b)
    if ra is None or rb is None:
        raise HTTPException(status_code=404, detail="One or both runs not found")

    def _status_out(r: object) -> RunStatusOut:
        return RunStatusOut(
            run_id=r.run_id, status=r.status,
            started_at=r.started_at, completed_at=r.completed_at,
            total_tests=r.total_tests, passed=r.passed,
            failed=r.failed, slow=r.slow, error=r.error,
            run_type=r.run_type, pair_id=r.pair_id,
        )

    tests_a = {r.query_name: r for r in ra.results}
    tests_b = {r.query_name: r for r in rb.results}
    all_names = sorted(set(tests_a) | set(tests_b))

    improved = regressed = unchanged = only_a = only_b = 0
    tests: list[TestCompareOut] = []
    for name in all_names:
        a = tests_a.get(name)
        b = tests_b.get(name)
        sa = a.effective_status if a else None
        sb = b.effective_status if b else None

        if a and b:
            if sa == "PASSED" and sb != "PASSED":
                regressed += 1
            elif sa != "PASSED" and sb == "PASSED":
                improved += 1
            else:
                unchanged += 1
        elif a:
            only_a += 1
        else:
            only_b += 1

        def _mm(r: object) -> int:
            return (r.value_mismatch_count or 0) + (r.missing_in_target_count or 0) + (r.missing_in_source_count or 0)

        tests.append(TestCompareOut(
            test_name=name,
            status_a=sa,
            status_b=sb,
            duration_a=a.duration_seconds if a else None,
            duration_b=b.duration_seconds if b else None,
            mismatches_a=_mm(a) if a else None,
            mismatches_b=_mm(b) if b else None,
            result_id_a=a.id if a else None,
            result_id_b=b.id if b else None,
        ))

    return RunCompareOut(
        run_a=_status_out(ra),
        run_b=_status_out(rb),
        tests=tests,
        summary={"improved": improved, "regressed": regressed, "unchanged": unchanged,
                 "only_in_a": only_a, "only_in_b": only_b},
    )


@router.get("/{run_id}", response_model=RunDetailOut)
def get_run_detail(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    snapshot = build_run_report_snapshot(run)
    results = [_test_result_out(r) for r in snapshot.results]
    return RunDetailOut(
        run_id=snapshot.run_id,
        status=snapshot.status,
        started_at=snapshot.started_at,
        completed_at=snapshot.completed_at,
        total_tests=snapshot.total_tests,
        passed=snapshot.passed,
        failed=snapshot.failed,
        slow=snapshot.slow,
        error=snapshot.error,
        run_type=snapshot.run_type,
        pair_id=snapshot.pair_id,
        source_env=snapshot.source_env,
        target_env=snapshot.target_env,
        config_snapshot=snapshot.config_snapshot,
        file_name_a=snapshot.file_name_a,
        file_name_b=snapshot.file_name_b,
        results=results,
    )


@router.get("/{run_id}/export")
def export_run_csv(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "run_id", "query_name", "status", "effective_status", "agreed_actions",
        "overridden_by", "override_at", "duration_seconds",
        "source_row_count", "target_row_count",
        "value_mismatch_count", "missing_in_target_count", "missing_in_source_count",
        "executed_at",
    ])
    for r in run.results:
        writer.writerow([
            run_id, r.query_name, r.status, r.effective_status, r.override_reason,
            r.override_by, r.override_at,
            r.duration_seconds, r.source_row_count, r.target_row_count,
            r.value_mismatch_count or 0,
            r.missing_in_target_count or 0,
            r.missing_in_source_count or 0,
            r.executed_at,
        ])
    buf.seek(0)
    filename = f"run_{run_id[:8]}_results.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_STATUS_EMOJI = {"PASSED": "✅", "FAILED": "❌", "ERROR": "❌", "SLOW": "⚠️", "CANCELLED": "⚠️"}


def _sanitize_ci_value(value) -> str:
    text = str(value)
    text = text.replace("\n", " ").replace("\r", " ").replace("`", "'")
    # Neutralize HTML comment delimiters so a malicious ci_context value can't
    # forge a `<!-- ATOM:JOB-STATUS:END -->`-style marker and corrupt a
    # downstream regex-based splice (see Task 6 of the GitLab CI/CD plan).
    return text.replace("<!--", "< !--").replace("-->", "-- >")


def _render_markdown_summary(run) -> str:
    if run.ci_context:
        trigger_line = (
            f"_Last run: {run.completed_at or run.started_at} via GitLab CI "
            f"(commit {_sanitize_ci_value(run.ci_context.get('commit_sha', '?'))}, "
            f"[pipeline]({_sanitize_ci_value(run.ci_context.get('pipeline_url', ''))}), "
            f"ref `{_sanitize_ci_value(run.ci_context.get('ref', '?'))}`)_"
        )
    else:
        trigger_line = f"_Last run: {run.completed_at or run.started_at} (manual)_"

    lines = [
        "## Job Status (auto-updated)",
        "",
        trigger_line,
        "",
        "| Job | Status | Duration |",
        "|-----|--------|----------|",
    ]
    for result in run.results:
        emoji = _STATUS_EMOJI.get(result.effective_status, result.effective_status)
        lines.append(f"| {result.query_name} | {emoji} {result.effective_status} | {result.duration_seconds:.1f}s |")
    lines.append("")
    lines.append(f"[View full run in Atom](/#/runs/{run.run_id})")
    return "\n".join(lines)


@router.get("/{run_id}/markdown-summary", response_class=PlainTextResponse)
def get_run_markdown_summary(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return PlainTextResponse(_render_markdown_summary(run), media_type="text/markdown")


@router.delete("/{run_id}", status_code=204)
def delete_run(run_id: str, request: Request, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    if not repo.delete_run(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    AuditService(db).log(request, "run.deleted", "run", run_id)


# Test result override endpoints
@router.patch("/{run_id}/results/{result_id}/override", response_model=TestResultOut)
def set_test_result_override(
    run_id: str,
    result_id: int,
    body: TestResultOverrideRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    """Set an override on a test result to mark it as passing even when it fails."""
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    test_result = db.get(TestResult, result_id)
    if test_result is None or test_result.run_id != run_id:
        raise HTTPException(status_code=404, detail="Test result not found")

    if test_result.status != "FAILED":
        raise HTTPException(
            status_code=409,
            detail="Only failed test results can be passed with agreed actions",
        )

    actor = AuditService.actor_from_request(request) or "unknown"
    test_result.override_status = body.status
    test_result.override_reason = body.reason
    test_result.override_by = actor
    test_result.override_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(test_result)

    # Log the override action
    AuditService(db).log(
        request,
        "test_result.override_set",
        "test_result",
        result_id,
        {
            "run_id": run_id,
            "query_name": test_result.query_name,
            "original_status": test_result.status,
            "override_status": body.status,
            "reason": body.reason,
            "overridden_by": actor,
        },
        actor=actor,
    )

    return _test_result_out(test_result)


@router.get("/{run_id}/results/{result_id}/override", response_model=dict)
def get_test_result_override(
    run_id: str,
    result_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    """Get the current override for a test result, if any."""
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    test_result = db.get(TestResult, result_id)
    if test_result is None or test_result.run_id != run_id:
        raise HTTPException(status_code=404, detail="Test result not found")

    if test_result.override_status is None:
        return {"override": None}

    return {
        "override": {
            "status": test_result.override_status,
            "reason": test_result.override_reason,
            "overridden_by": test_result.override_by,
            "override_at": test_result.override_at,
        }
    }


@router.delete("/{run_id}/results/{result_id}/override", response_model=TestResultOut)
def delete_test_result_override(
    run_id: str,
    result_id: int,
    request: Request,
    db: Session = Depends(get_session),
):
    """Remove the override from a test result, restoring its original status."""
    from etl_framework.repository.models import TestResult

    repo = RunRepository(db)
    if repo.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    test_result = db.get(TestResult, result_id)
    if test_result is None or test_result.run_id != run_id:
        raise HTTPException(status_code=404, detail="Test result not found")

    # Remove the override
    original_status = test_result.status
    test_result.override_status = None
    test_result.override_reason = None
    test_result.override_by = None
    test_result.override_at = None

    db.commit()
    db.refresh(test_result)

    # Log the override removal
    AuditService(db).log(
        request,
        "test_result.override_removed",
        "test_result",
        result_id,
        {
            "run_id": run_id,
            "query_name": test_result.query_name,
            "restored_status": original_status,
        },
    )

    return _test_result_out(test_result)


# ---------------------------------------------------------------------------
# P2 – Badge SVG
# ---------------------------------------------------------------------------

_BADGE_COLORS = {
    "PASSED":    "#4ade80",
    "FAILED":    "#fb7185",
    "SLOW":      "#fbbf24",
    "ERROR":     "#f43f5e",
    "RUNNING":   "#38bdf8",
    "PENDING":   "#94a3b8",
    "COMPLETED": "#4ade80",
}


def _badge_svg(label: str, status: str) -> str:
    color = _BADGE_COLORS.get(status, "#94a3b8")
    label_w = len(label) * 6 + 10
    val_w = len(status) * 7 + 10
    total_w = label_w + val_w
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="20">'
        f'<rect width="{label_w}" height="20" fill="#555"/>'
        f'<rect x="{label_w}" width="{val_w}" height="20" fill="{color}"/>'
        f'<text x="{label_w // 2}" y="14" fill="#fff" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" '
        f'font-size="11" text-anchor="middle">{label}</text>'
        f'<text x="{label_w + val_w // 2}" y="14" fill="#fff" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" '
        f'font-size="11" text-anchor="middle">{status}</text>'
        f'</svg>'
    )


from fastapi.responses import Response  # noqa: E402 (local import avoids circular at top)


@router.get("/{run_id}/badge")
def run_badge(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    status = run.status if run else "UNKNOWN"
    svg = _badge_svg("ETL", status)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-cache"})


@router.get("/latest/badge")
def latest_badge(job_name: str = "", db: Session = Depends(get_session)):
    from etl_framework.repository.models import TestRun, TestResult
    q = db.query(TestRun).join(TestResult, TestResult.run_id == TestRun.run_id)
    if job_name:
        q = q.filter(TestResult.query_name == job_name)
    run = q.order_by(TestRun.id.desc()).first()
    status = run.status if run else "UNKNOWN"
    svg = _badge_svg(job_name or "ETL", status)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# P2 – Baseline pinning
# ---------------------------------------------------------------------------

@router.post("/{run_id}/set-baseline", status_code=204)
def set_baseline(run_id: str, request: Request, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.set_baseline(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    AuditService(db).log(request, "run.baseline_set", "run", run_id)


@router.get("/{run_id}/vs-baseline")
def vs_baseline(run_id: str, db: Session = Depends(get_session)):
    from api.schemas import RunCompareOut
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    baseline = repo.get_baseline(run.source_env or "", run.target_env or "")
    if baseline is None:
        raise HTTPException(status_code=404, detail="No baseline set for this env pair")
    if baseline.run_id == run_id:
        raise HTTPException(status_code=400, detail="Run is already the baseline")
    # Delegate to existing compare logic
    from api.routes.runs import compare_runs
    return compare_runs(run_a=baseline.run_id, run_b=run_id, db=db)


# ---------------------------------------------------------------------------
# P2 – Mismatch distribution
# ---------------------------------------------------------------------------

@router.get("/{run_id}/results/{result_id}/mismatch-distribution")
def mismatch_distribution(run_id: str, result_id: int, top_n: int = 10,
                           db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"result_id": result_id, "distribution": repo.mismatch_distribution(result_id, top_n)}


# ---------------------------------------------------------------------------
# Execution Sequence Scheduler — step endpoints
# ---------------------------------------------------------------------------

@router.get("/{run_id}/steps", response_model=list[RunStepOut])
def list_run_steps(run_id: str, db: Session = Depends(get_session)):
    run = RunRepository(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunStepRepository(db).list_steps(run_id)


@router.post(
    "/{run_id}/steps/{step_index}/release",
    response_model=RunStepOut,
)
def release_run_step(
    run_id: str,
    step_index: int,
    body: RunStepReleaseRequest,
    db: Session = Depends(get_session),
):
    run = RunRepository(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    step = RunStepRepository(db).get_step(run_id, step_index)
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")
    if step.status != "HELD":
        raise HTTPException(
            status_code=409,
            detail=f"Step is not held (current status: {step.status})",
        )

    released = RunStepRepository(db).release_step(
        run_id=run_id,
        step_index=step_index,
        action=body.action,
        note=body.note,
        released_by=body.released_by,
    )
    if released is None:
        raise HTTPException(status_code=409, detail="Step could not be released")
    return released
