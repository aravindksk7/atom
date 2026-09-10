from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from etl_framework.repository.models import JobSelection, TestRun
from etl_framework.repository.repository import ci_context_present_filter, run_target_predicates


@dataclass(frozen=True)
class CiRunsFilters:
    days: int = 30
    target_type: Literal["selection", "sequence"] | None = None
    status: str | None = None


class CiRunsReportingService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def summary(self, filters: CiRunsFilters) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        start_date = now - timedelta(days=filters.days)

        base_filters = [
            *ci_context_present_filter(),
            TestRun.started_at >= start_date,
        ]
        if filters.status:
            base_filters.append(TestRun.status == filters.status)

        selection_target, sequence_target = run_target_predicates()
        query = (
            self._db.query(
                func.count(TestRun.id),
                func.sum(case((TestRun.status == "PASSED", 1), else_=0)),
                func.sum(case((TestRun.status == "FAILED", 1), else_=0)),
                func.sum(case((TestRun.status == "ERROR", 1), else_=0)),
                func.sum(case((TestRun.status == "CANCELLED", 1), else_=0)),
                func.sum(case((selection_target, 1), else_=0)),
                func.sum(case((sequence_target, 1), else_=0)),
            )
            .select_from(TestRun)
            .outerjoin(JobSelection, TestRun.selection_id == JobSelection.id)
            .filter(*base_filters)
        )
        if filters.target_type == "selection":
            query = query.filter(selection_target)
        elif filters.target_type == "sequence":
            query = query.filter(sequence_target)
        total, passed, failed, error, cancelled, selections, sequences = query.one()
        total = int(total or 0)
        counts = {
            "passed": int(passed or 0),
            "failed": int(failed or 0),
            "error": int(error or 0),
            "cancelled": int(cancelled or 0),
        }
        by_target_type = {
            "selection": int(selections or 0),
            "sequence": int(sequences or 0),
        }
        pass_rate = counts["passed"] / total if total else 0

        return {
            "total": total,
            "passed": counts["passed"],
            "failed": counts["failed"],
            "error": counts["error"],
            "cancelled": counts["cancelled"],
            "pass_rate": pass_rate,
            "by_target_type": by_target_type,
        }
