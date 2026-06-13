from __future__ import annotations
import uuid
import threading
import time
import random
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from api.schemas import RunTrigger, RunStatusOut, RunDetailOut, TestResultOut, MismatchOut
from api.dependencies import get_session
from etl_framework.repository.repository import RunRepository
from etl_framework.repository.models import TestResult

router = APIRouter(tags=["runs"])


def _simulate_run(run_id: str, job_names: list[str], source_env: str, target_env: str) -> None:
    """Background simulation of test execution — replaces real engine calls in dev mode."""
    from etl_framework.repository.database import SessionLocal
    db = SessionLocal()
    try:
        repo = RunRepository(db)
        repo.update_run_status(run_id, "RUNNING", started_at=datetime.now(timezone.utc))
        time.sleep(1)  # simulate startup

        statuses = ["PASSED", "PASSED", "PASSED", "FAILED", "SLOW"]
        passed = failed = slow = error = 0

        for job in (job_names or ["sample_query"]):
            time.sleep(random.uniform(0.3, 1.2))
            status = random.choice(statuses)
            if status == "PASSED":
                passed += 1
            elif status == "FAILED":
                failed += 1
            elif status == "SLOW":
                slow += 1

            src_rows = random.randint(50, 500)
            tgt_rows = src_rows if status != "FAILED" else src_rows - random.randint(1, 5)
            mismatches = random.randint(0, 3) if status == "FAILED" else 0

            tr = TestResult(
                run_id=run_id,
                query_name=job,
                status=status,
                duration_seconds=round(random.uniform(0.1, 3.0), 3),
                source_row_count=src_rows,
                target_row_count=tgt_rows,
                value_mismatch_count=mismatches,
                missing_in_target_count=max(0, src_rows - tgt_rows),
                missing_in_source_count=0,
                executed_at=datetime.now(timezone.utc),
            )
            db.add(tr)
            db.commit()

        total = passed + failed + slow + error
        final_status = "FAILED" if failed > 0 else ("SLOW" if slow > 0 else "PASSED")
        repo.update_run_status(
            run_id, final_status,
            completed_at=datetime.now(timezone.utc),
            total_tests=total, passed=passed, failed=failed, slow=slow, error=error,
        )
    except Exception:
        import traceback
        try:
            RunRepository(db).update_run_status(run_id, "ERROR",
                                                 completed_at=datetime.now(timezone.utc))
        except Exception:
            pass
        traceback.print_exc()
    finally:
        db.close()


@router.get("", response_model=list[RunStatusOut])
def list_runs(limit: int = 50, offset: int = 0, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    runs = repo.list_runs(limit=limit, offset=offset)
    return [RunStatusOut(
        run_id=r.run_id, status=r.status,
        started_at=r.started_at, completed_at=r.completed_at,
        total_tests=r.total_tests, passed=r.passed,
        failed=r.failed, slow=r.slow, error=r.error,
    ) for r in runs]


@router.post("", response_model=RunStatusOut, status_code=202)
def trigger_run(body: RunTrigger, background_tasks: BackgroundTasks,
                db: Session = Depends(get_session)):
    run_id = str(uuid.uuid4())
    repo = RunRepository(db)
    config_snapshot = body.config_data if body.config_data else None
    repo.create_run(
        run_id=run_id,
        source_env=body.source_env,
        target_env=body.target_env,
        config_snapshot=config_snapshot,
    )
    background_tasks.add_task(
        _simulate_run, run_id, body.job_names, body.source_env, body.target_env
    )
    return RunStatusOut(run_id=run_id, status="PENDING")


@router.get("/{run_id}/status", response_model=RunStatusOut)
def get_run_status(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunStatusOut(
        run_id=run.run_id, status=run.status,
        started_at=run.started_at, completed_at=run.completed_at,
        total_tests=run.total_tests, passed=run.passed,
        failed=run.failed, slow=run.slow, error=run.error,
    )


@router.get("/{run_id}", response_model=RunDetailOut)
def get_run_detail(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    results = [TestResultOut(
        id=r.id, query_name=r.query_name, status=r.status,
        duration_seconds=r.duration_seconds,
        source_row_count=r.source_row_count, target_row_count=r.target_row_count,
        value_mismatch_count=r.value_mismatch_count,
        missing_in_target_count=r.missing_in_target_count,
        missing_in_source_count=r.missing_in_source_count,
        error_message=r.error_message, executed_at=r.executed_at,
    ) for r in run.results]
    return RunDetailOut(
        run_id=run.run_id, status=run.status,
        started_at=run.started_at, completed_at=run.completed_at,
        total_tests=run.total_tests, passed=run.passed,
        failed=run.failed, slow=run.slow, error=run.error,
        source_env=run.source_env, target_env=run.target_env,
        config_snapshot=run.config_snapshot, results=results,
    )
