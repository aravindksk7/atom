from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from api.schemas import (
    BOCompareRequest,
    DualEnvLaunchOut,
    DualEnvLaunchRequest,
    PairSummaryOut,
    ReconFileCompareRequest,
    RunStatusOut,
)
from etl_framework.repository.database import get_db
from etl_framework.repository.models import SavedConfig
from etl_framework.repository.repository import RunRepository

router = APIRouter(tags=["compare"])
logger = logging.getLogger("api.routes.compare")


def _status_out(r) -> RunStatusOut:
    return RunStatusOut(
        run_id=r.run_id,
        status=r.status,
        started_at=r.started_at,
        completed_at=r.completed_at,
        total_tests=r.total_tests or 0,
        passed=r.passed or 0,
        failed=r.failed or 0,
        slow=r.slow or 0,
        error=r.error or 0,
        run_type=r.run_type,
        pair_id=r.pair_id,
    )


def _snapshot_for_config(
    cfg: SavedConfig,
    source_env: str,
    target_env: str,
    job_names: list[str],
    run_settings,
) -> dict[str, Any]:
    config_data = dict(cfg.config_json or {})
    snapshot: dict[str, Any] = {
        "config_id": cfg.id,
        "config_name": cfg.name,
        "env_name": cfg.env_name,
        "config_data": config_data,
        "job_sequence": list(job_names),
        "run_settings": run_settings.model_dump(),
    }
    if "source_credentials" in config_data or "target_credentials" in config_data:
        if "source_credentials" in config_data:
            snapshot["source_credentials"] = config_data["source_credentials"]
        if "target_credentials" in config_data:
            snapshot["target_credentials"] = config_data["target_credentials"]
    else:
        snapshot["source_credentials"] = {"name": source_env, **config_data}
        snapshot["target_credentials"] = {"name": target_env, **config_data}
    if "bo_credentials" in config_data:
        snapshot["bo_credentials"] = config_data["bo_credentials"]
    if "automic_credentials" in config_data:
        snapshot["automic_credentials"] = config_data["automic_credentials"]
    return snapshot


# ---------------------------------------------------------------------------
# Module-level background functions — monkeypatched in tests
# ---------------------------------------------------------------------------

def _run_bo_bg(req: BOCompareRequest, run_id: str) -> None:
    from etl_framework.repository.database import SessionLocal
    db = SessionLocal()
    try:
        from api.services.compare_service import CompareService
        from etl_framework.repository.repository import ConfigRepository
        svc = CompareService(db, ConfigRepository(db))
        svc.run_bo_comparison(req, run_id)
    except Exception:
        logger.exception("BO comparison background task failed for run_id=%s", run_id)
    finally:
        db.close()


def _run_recon_file_bg(req: ReconFileCompareRequest, run_id: str) -> None:
    from etl_framework.repository.database import SessionLocal
    db = SessionLocal()
    try:
        from api.services.compare_service import CompareService
        from etl_framework.repository.repository import ConfigRepository
        svc = CompareService(db, ConfigRepository(db))
        svc.run_recon_file_compare(req, run_id)
    except Exception:
        logger.exception("Recon-file comparison background task failed for run_id=%s", run_id)
    finally:
        db.close()


def _launch_dual_env_bg(run_id_a: str, run_id_b: str, req: DualEnvLaunchRequest) -> None:
    from etl_framework.repository.database import SessionLocal

    def _run_single(run_id: str, source_env: str, target_env: str, config_id: int) -> None:
        db = SessionLocal()
        try:
            from api.services.run_executor import RunExecutor
            cfg = db.get(SavedConfig, config_id)
            if cfg is None:
                raise HTTPException(status_code=404, detail=f"Config {config_id} not found")
            config_snapshot = _snapshot_for_config(
                cfg,
                source_env=source_env,
                target_env=target_env,
                job_names=req.job_names,
                run_settings=req.run_settings,
            )
            RunExecutor(
                db=db,
                run_id=run_id,
                source_env=source_env,
                target_env=target_env,
                job_sequence=req.job_names,
                run_settings=req.run_settings,
                config_snapshot=config_snapshot,
            ).execute()
        except ImportError:
            logger.warning(
                "RunExecutor not available; dual-env leg skipped for run_id=%s", run_id
            )
        except Exception:
            logger.exception("Dual-env leg failed for run_id=%s", run_id)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as ex:
        fa = ex.submit(_run_single, run_id_a, req.source_env_a, req.target_env_a, req.config_id_a)
        fb = ex.submit(_run_single, run_id_b, req.source_env_b, req.target_env_b, req.config_id_b)
        for f in (fa, fb):
            try:
                f.result()
            except Exception:
                logger.exception("Dual-env leg raised an unhandled exception")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/bo-report", response_model=RunStatusOut, status_code=202)
def compare_bo_report(
    body: BOCompareRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> RunStatusOut:
    run_id = str(uuid.uuid4())
    repo = RunRepository(db)
    repo.create_run(
        run_id=run_id,
        source_env=body.label_a,
        target_env=body.label_b,
        config_snapshot=None,
        run_type="bo_comparison",
    )
    background_tasks.add_task(_run_bo_bg, body, run_id)
    run = repo.get_run(run_id)
    return _status_out(run)


@router.post("/dual-env", response_model=DualEnvLaunchOut, status_code=202)
def launch_dual_env(
    body: DualEnvLaunchRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> DualEnvLaunchOut:
    cfg_a = db.get(SavedConfig, body.config_id_a)
    if cfg_a is None:
        raise HTTPException(status_code=404, detail="Config A not found")
    cfg_b = db.get(SavedConfig, body.config_id_b)
    if cfg_b is None:
        raise HTTPException(status_code=404, detail="Config B not found")

    repo = RunRepository(db)
    pair_id = str(uuid.uuid4())
    run_id_a = str(uuid.uuid4())
    run_id_b = str(uuid.uuid4())

    repo.create_run(
        run_id=run_id_a,
        source_env=body.source_env_a,
        target_env=body.target_env_a,
        config_snapshot=_snapshot_for_config(
            cfg_a,
            source_env=body.source_env_a,
            target_env=body.target_env_a,
            job_names=body.job_names,
            run_settings=body.run_settings,
        ),
        run_type="dual_env",
        pair_id=pair_id,
    )
    repo.create_run(
        run_id=run_id_b,
        source_env=body.source_env_b,
        target_env=body.target_env_b,
        config_snapshot=_snapshot_for_config(
            cfg_b,
            source_env=body.source_env_b,
            target_env=body.target_env_b,
            job_names=body.job_names,
            run_settings=body.run_settings,
        ),
        run_type="dual_env",
        pair_id=pair_id,
    )

    background_tasks.add_task(_launch_dual_env_bg, run_id_a, run_id_b, body)
    return DualEnvLaunchOut(pair_id=pair_id, run_id_a=run_id_a, run_id_b=run_id_b)


@router.post("/recon-file", response_model=RunStatusOut, status_code=202)
def compare_recon_file(
    body: ReconFileCompareRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> RunStatusOut:
    run_id = str(uuid.uuid4())
    repo = RunRepository(db)
    repo.create_run(
        run_id=run_id,
        source_env=body.label_a,
        target_env=body.label_b,
        run_type="recon_file",
    )
    background_tasks.add_task(_run_recon_file_bg, body, run_id)
    run = repo.get_run(run_id)
    return _status_out(run)


@router.get("/pairs", response_model=list[PairSummaryOut])
def list_pairs(db: Session = Depends(get_db)) -> list[PairSummaryOut]:
    repo = RunRepository(db)
    pair_ids = repo.list_pairs()
    result: list[PairSummaryOut] = []
    for pid in pair_ids:
        runs = repo.get_pair_runs(pid)
        if len(runs) >= 2:
            result.append(
                PairSummaryOut(
                    pair_id=pid,
                    run_a=_status_out(runs[0]),
                    run_b=_status_out(runs[1]),
                )
            )
    return result


@router.get("/pairs/{pair_id}", response_model=PairSummaryOut)
def get_pair(pair_id: str, db: Session = Depends(get_db)) -> PairSummaryOut:
    repo = RunRepository(db)
    runs = repo.get_pair_runs(pair_id)
    if len(runs) < 2:
        raise HTTPException(status_code=404, detail="Pair not found")
    return PairSummaryOut(
        pair_id=pair_id,
        run_a=_status_out(runs[0]),
        run_b=_status_out(runs[1]),
    )
