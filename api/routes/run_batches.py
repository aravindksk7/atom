from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import RunBatchOut
from etl_framework.repository.repository import RunBatchRepository

router = APIRouter(tags=["run-batches"])


def _batch_out(db: Session, batch) -> RunBatchOut:
    runs = RunBatchRepository(db).member_runs(batch.batch_id)
    return RunBatchOut(
        **{k: getattr(batch, k) for k in (
            "batch_id", "target_type", "target_id", "status", "variable_name", "start_value",
            "iterations", "step_days", "weekend_policy", "stop_on_failure", "completed",
            "current_iteration", "current_value", "created_at", "completed_at",
        )},
        runs=[{"run_id": r.run_id, "status": r.status, "started_at": r.started_at, "completed_at": r.completed_at} for r in runs],
    )


@router.get("/{batch_id}", response_model=RunBatchOut)
def get_batch(batch_id: str, db: Session = Depends(get_session)):
    batch = RunBatchRepository(db).get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Run batch not found")
    return _batch_out(db, batch)


@router.post("/{batch_id}/cancel", response_model=RunBatchOut)
def cancel_batch(batch_id: str, db: Session = Depends(get_session)):
    batch = RunBatchRepository(db).stop(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Run batch not found")
    return _batch_out(db, batch)
