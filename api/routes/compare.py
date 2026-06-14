from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor

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

    def _run_single(run_id: str, source_env: str, target_env: str) -> None:
        db = SessionLocal()
        try:
            from api.services.run_executor import RunExecutor
            RunExecutor(
                db=db,
                run_id=run_id,
                source_env=source_env,
                target_env=target_env,
                job_sequence=req.job_names,
                run_settings=req.run_settings,
                config_snapshot={"job_sequence": req.job_names},
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
        fa = ex.submit(_run_single, run_id_a, req.source_env_a, req.target_env_a)
        fb = ex.submit(_run_single, run_id_b, req.source_env_b, req.target_env_b)
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
    if db.get(SavedConfig, body.config_id_a) is None:
        raise HTTPException(status_code=404, detail="Config A not found")
    if db.get(SavedConfig, body.config_id_b) is None:
        raise HTTPException(status_code=404, detail="Config B not found")

    repo = RunRepository(db)
    pair_id = str(uuid.uuid4())
    run_id_a = str(uuid.uuid4())
    run_id_b = str(uuid.uuid4())

    repo.create_run(
        run_id=run_id_a,
        source_env=body.source_env_a,
        target_env=body.target_env_a,
        run_type="dual_env",
        pair_id=pair_id,
    )
    repo.create_run(
        run_id=run_id_b,
        source_env=body.source_env_b,
        target_env=body.target_env_b,
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
