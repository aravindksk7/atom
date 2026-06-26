from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from api.dependencies import get_session
from etl_framework.repository.repository import ColumnProfileRepository

router = APIRouter(tags=["profiles"])


@router.get("/jobs/{job_name}/profile")
def get_latest_profile(job_name: str, db: Session = Depends(get_session)):
    repo = ColumnProfileRepository(db)
    rows = repo.get_latest(job_name)
    if not rows:
        raise HTTPException(status_code=404, detail="No profile found for job")
    return [
        {
            "column_name": r.column_name,
            "null_rate": r.null_rate,
            "distinct_count": r.distinct_count,
            "min_val": r.min_val,
            "max_val": r.max_val,
            "mean_val": r.mean_val,
            "std_val": r.std_val,
            "p25": r.p25,
            "p50": r.p50,
            "p75": r.p75,
            "p95": r.p95,
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
        }
        for r in rows
    ]


@router.get("/jobs/{job_name}/profile/history")
def get_profile_history(job_name: str, column: str, db: Session = Depends(get_session)):
    repo = ColumnProfileRepository(db)
    rows = repo.get_history(job_name, column)
    return [
        {
            "run_id": r.run_id,
            "null_rate": r.null_rate,
            "distinct_count": r.distinct_count,
            "mean_val": r.mean_val,
            "std_val": r.std_val,
            "p25": r.p25,
            "p50": r.p50,
            "p75": r.p75,
            "p95": r.p95,
            "captured_at": r.captured_at.isoformat() if r.captured_at else None,
        }
        for r in rows
    ]


@router.post("/jobs/{job_name}/suggest-rules")
def suggest_rules(job_name: str, db: Session = Depends(get_session)):
    repo = ColumnProfileRepository(db)
    rows = repo.get_latest(job_name)
    if not rows:
        raise HTTPException(status_code=404, detail="No profile found — run a profile job first")
    suggestions = []
    for r in rows:
        if r.null_rate is not None and r.null_rate < 1.0:
            suggestions.append({
                "type": "completeness_ratio",
                "column": r.column_name,
                "min_value": round(max(0.0, (1.0 - r.null_rate) - 0.05), 3),
                "severity": "warn",
            })
        if r.min_val is not None and r.max_val is not None:
            try:
                suggestions.append({
                    "type": "column_value_between",
                    "column": r.column_name,
                    "min_value": float(r.min_val),
                    "max_value": float(r.max_val) * 1.1,
                    "severity": "warn",
                })
            except (ValueError, TypeError):
                pass
        if r.p95 is not None:
            suggestions.append({
                "type": "column_percentile",
                "column": r.column_name,
                "percentile": 95,
                "max_value": round(r.p95 * 1.2, 4),
                "severity": "warn",
            })
    return {"job_name": job_name, "suggested_rules": suggestions}
