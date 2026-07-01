from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.services.log_parser import parse_log_events, filter_log_events

router = APIRouter(tags=["logs"])


@router.get("")
def get_logs(
    run_id: str = "",
    q: str = "",
    level: str = "",
    limit: int = 500,
):
    log_path = Path("logs") / "etl_framework.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = filter_log_events(text, run_id=run_id, query=q, level=level, limit=limit)
    return {
        "run_id": run_id,
        "query": q,
        "level": level,
        "total_lines": len(text.splitlines()),
        "total_events": len(parse_log_events(text)),
        "matched_lines": len(lines),
        "lines": lines,
    }
