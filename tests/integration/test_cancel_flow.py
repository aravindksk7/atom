from __future__ import annotations

import threading
import time

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from etl_framework.repository.database import Base
from etl_framework.repository.repository import JobRepository, RunRepository
from api.schemas import RunSettings
from api.services.run_executor import RunExecutor


def _make_engine(db_path: str):
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )


def _seed(db_path: str, run_id: str) -> None:
    engine = _make_engine(db_path)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        repo = JobRepository(db)
        for name in ("cancel-job-a", "cancel-job-b"):
            repo.create({
                "name": name,
                "description": "",
                "tags": [],
                "job_type": "reconciliation",
                "query": f"SELECT * FROM {name}",
                "key_columns": ["id"],
                "exclude_columns": [],
                "source_env": None,
                "target_env": None,
                "params": {"source_rows": [{"id": 1}], "target_rows": [{"id": 1}]},
                "enabled": True,
            })
        RunRepository(db).create_run(run_id, "dev", "prod")
        db.commit()
    engine.dispose()


def test_cancel_flow_ends_in_cancelled(tmp_path):
    db_path = str(tmp_path / "integration.db")
    run_id = "int-cancel-1"
    _seed(db_path, run_id)

    running_event = threading.Event()
    errors: list[Exception] = []

    def executor_thread() -> None:
        try:
            engine = _make_engine(db_path)
            with Session(engine) as db:
                RunExecutor(
                    db=db,
                    run_id=run_id,
                    source_env="dev",
                    target_env="prod",
                    job_sequence=["cancel-job-a", "cancel-job-b"],
                    run_settings=RunSettings(metrics_enabled=False),
                ).execute()
        except Exception as exc:
            errors.append(exc)
        finally:
            engine.dispose()

    def cancel_thread() -> None:
        try:
            # Poll until RUNNING (executor sets this before any jobs run)
            poll_engine = _make_engine(db_path)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                with Session(poll_engine) as db:
                    run = RunRepository(db).get_run(run_id)
                    if run and run.status == "RUNNING":
                        running_event.set()
                        RunRepository(db).request_cancel(run_id)
                        break
                time.sleep(0.01)
            poll_engine.dispose()
        except Exception as exc:
            errors.append(exc)

    t_exec = threading.Thread(target=executor_thread, name="executor")
    t_cancel = threading.Thread(target=cancel_thread, name="cancel")

    t_exec.start()
    t_cancel.start()
    t_exec.join(timeout=15)
    t_cancel.join(timeout=5)

    assert not t_exec.is_alive(), "executor thread did not finish in time"
    assert not t_cancel.is_alive(), "cancel thread did not finish in time"
    assert not errors, f"thread errors: {errors}"
    assert running_event.is_set(), "executor never reached RUNNING status"

    verify_engine = _make_engine(db_path)
    with Session(verify_engine) as db:
        run = RunRepository(db).get_run(run_id)
        assert run is not None
        assert run.status == "CANCELLED", f"expected CANCELLED, got {run.status}"
    verify_engine.dispose()
