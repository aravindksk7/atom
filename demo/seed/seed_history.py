"""Inserts earlier nightly runs, so the recorded run's progress bars have history to estimate from.

ATOM estimates a job's percent from its last 10 results and needs at least 3. A fresh database has
none, and every bar would show moving stripes with no percent. Running the regression several times
first would add an hour to each take, so these runs are written directly. demo/README.md and the
video's opening card both say they are seeded.

Reads a JSON plan on stdin. DEMO_ATOM_DB_URL names ATOM's database.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from etl_framework.repository.models import TestResult, TestRun  # noqa: E402

_START_HOUR_UTC = 1


def _vary(seconds: float, night: int, position: int) -> float:
    """Up to 10% either side, the same on every take so a recording can be repeated."""
    step = (night * 7 + position * 3) % 10
    return round(seconds * (0.9 + 0.2 * step / 9), 1)


def seed_history(session: Session, plan: dict, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    jobs = plan["jobs"]
    # Oldest first: lists order runs newest first by id.
    for night in range(plan["nights"], 0, -1):
        started = (now - timedelta(days=night)).replace(
            hour=_START_HOUR_UTC, minute=0, second=0, microsecond=0,
        )
        completed = started + timedelta(seconds=_vary(plan["run_seconds"], night, 0))
        run_id = str(uuid.uuid4())
        session.add(TestRun(
            run_id=run_id,
            status="PASSED",
            started_at=started,
            completed_at=completed,
            source_env="qa",
            target_env="prod",
            sequence_id=plan["sequence_id"],
            config_snapshot={"sequence": {
                "id": plan["sequence_id"], "name": plan["sequence_name"], "version": 1,
            }},
            total_tests=len(jobs),
            passed=len(jobs),
        ))
        session.flush()
        for position, (job_name, seconds) in enumerate(jobs.items()):
            session.add(TestResult(
                run_id=run_id,
                query_name=job_name,
                status="PASSED",
                duration_seconds=_vary(seconds, night, position),
                executed_at=completed,
            ))
    session.commit()


def main() -> None:
    engine = create_engine(os.environ["DEMO_ATOM_DB_URL"])
    with sessionmaker(bind=engine)() as session:
        seed_history(session, json.load(sys.stdin))
    engine.dispose()


if __name__ == "__main__":
    main()
