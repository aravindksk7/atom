from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.coverage_service import (
    DEFAULT_FLAKY_WINDOW,
    build_coverage,
    build_flaky_report,
)

router = APIRouter(tags=["coverage"])


@router.get("")
def get_coverage(db: Session = Depends(get_session)):
    """Test-coverage matrix: tables/columns vs jobs, rules, and coverage level."""
    return build_coverage(db)


@router.get("/flaky")
def get_flaky(
    window: int = Query(DEFAULT_FLAKY_WINDOW, ge=2, le=200),
    db: Session = Depends(get_session),
):
    """Flaky tests: status flip-flop score over the last `window` runs."""
    return build_flaky_report(db, window=window)
