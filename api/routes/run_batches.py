from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import RunBatchOut
from api.services.batch_launch import batch_out
from etl_framework.repository.repository import RunBatchRepository

router = APIRouter(tags=["run-batches"])


@router.get("/{batch_id}", response_model=RunBatchOut)
def get_batch(batch_id: str, db: Session = Depends(get_session)):
    batch = RunBatchRepository(db).get(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Run batch not found")
    return batch_out(db, batch)


@router.post("/{batch_id}/cancel", response_model=RunBatchOut)
def cancel_batch(batch_id: str, db: Session = Depends(get_session)):
    batch = RunBatchRepository(db).stop(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Run batch not found")
    return batch_out(db, batch)
