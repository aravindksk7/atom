from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from api.dependencies import get_session
from etl_framework.repository.repository import SchemaSnapshotRepository
from api.services.schema_snapshot_service import diff_schemas

router = APIRouter(tags=["schema-snapshots"])


@router.get("/jobs/{job_name}/schema-history")
def get_schema_history(
    job_name: str,
    environment: str = "source",
    db: Session = Depends(get_session),
):
    repo = SchemaSnapshotRepository(db)
    rows = repo.get_history(job_name, environment)
    result = []
    for i, row in enumerate(rows):
        prev_cols = rows[i - 1].columns if i > 0 else []
        diff = diff_schemas(row.columns, prev_cols)
        result.append({
            "id": row.id,
            "run_id": row.run_id,
            "captured_at": row.captured_at.isoformat() if row.captured_at else None,
            "environment": row.environment,
            "columns": row.columns,
            "diff": diff,
        })
    return result
