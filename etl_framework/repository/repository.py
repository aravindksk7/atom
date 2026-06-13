from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository.models import SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail


class ConfigRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, name: str, env_name: str, config_data: dict) -> SavedConfig:
        cfg = SavedConfig(name=name, env_name=env_name, config_json=config_data)
        self._db.add(cfg)
        self._db.commit()
        self._db.refresh(cfg)
        return cfg

    def get(self, config_id: int) -> SavedConfig | None:
        return self._db.get(SavedConfig, config_id)

    def get_by_name(self, name: str) -> SavedConfig | None:
        return self._db.query(SavedConfig).filter(SavedConfig.name == name).first()

    def list(self) -> list[SavedConfig]:
        return self._db.query(SavedConfig).order_by(SavedConfig.name).all()

    def update(self, config_id: int, **kwargs) -> SavedConfig | None:
        cfg = self.get(config_id)
        if cfg is None:
            return None
        if "config_data" in kwargs:
            cfg.config_json = kwargs["config_data"]
        if "name" in kwargs:
            cfg.name = kwargs["name"]
        if "env_name" in kwargs:
            cfg.env_name = kwargs["env_name"]
        cfg.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(cfg)
        return cfg

    def delete(self, config_id: int) -> bool:
        cfg = self.get(config_id)
        if cfg is None:
            return False
        self._db.delete(cfg)
        self._db.commit()
        return True


class JobRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, job_data: dict) -> SavedJob:
        job = SavedJob(**job_data)
        self._db.add(job)
        self._db.commit()
        self._db.refresh(job)
        return job

    def get(self, name: str) -> SavedJob | None:
        return self._db.query(SavedJob).filter(SavedJob.name == name).first()

    def list(self) -> list[SavedJob]:
        return self._db.query(SavedJob).order_by(SavedJob.name).all()

    def update(self, name: str, job_data: dict) -> SavedJob | None:
        job = self.get(name)
        if job is None:
            return None
        for key, value in job_data.items():
            setattr(job, key, value)
        job.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(job)
        return job

    def upsert(self, job_data: dict) -> SavedJob:
        existing = self.get(job_data["name"])
        if existing is None:
            return self.create(job_data)
        updated = self.update(existing.name, job_data)
        assert updated is not None
        return updated

    def delete(self, name: str) -> bool:
        job = self.get(name)
        if job is None:
            return False
        self._db.delete(job)
        self._db.commit()
        return True


class RunRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_run(
        self,
        run_id: str,
        source_env: str,
        target_env: str,
        config_snapshot: dict | None = None,
    ) -> TestRun:
        run = TestRun(
            run_id=run_id,
            status="PENDING",
            source_env=source_env,
            target_env=target_env,
            config_snapshot=config_snapshot,
        )
        self._db.add(run)
        self._db.commit()
        self._db.refresh(run)
        return run

    def get_run(self, run_id: str) -> TestRun | None:
        return self._db.query(TestRun).filter(TestRun.run_id == run_id).first()

    def list_runs(self, limit: int = 50, offset: int = 0) -> list[TestRun]:
        return (
            self._db.query(TestRun)
            .order_by(TestRun.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def update_run_status(self, run_id: str, status: str, **kwargs) -> TestRun | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        run.status = status
        for key in ("started_at", "completed_at", "total_tests",
                    "passed", "failed", "slow", "error"):
            if key in kwargs:
                setattr(run, key, kwargs[key])
        self._db.commit()
        self._db.refresh(run)
        return run

    def add_test_result(self, run_id: str, result: ReconciliationResult) -> TestResult:
        status_val = result.status.value if hasattr(result.status, "value") else str(result.status)
        tr = TestResult(
            run_id=run_id,
            query_name=result.query_name,
            status=status_val,
            duration_seconds=result.duration_seconds,
            source_row_count=result.source_row_count,
            target_row_count=result.target_row_count,
            value_mismatch_count=result.value_mismatch_count,
            missing_in_target_count=result.missing_in_target_count,
            missing_in_source_count=result.missing_in_source_count,
            executed_at=result.executed_at,
        )
        self._db.add(tr)
        self._db.commit()
        self._db.refresh(tr)
        return tr

    def add_mismatch_details(
        self, test_result_id: int, mismatches: list[MismatchRecord]
    ) -> None:
        for m in mismatches:
            detail = MismatchDetail(
                test_result_id=test_result_id,
                key_values=m.key_values,
                column_name=m.column_name,
                source_value=str(m.source_value) if m.source_value is not None else None,
                target_value=str(m.target_value) if m.target_value is not None else None,
                mismatch_type=m.mismatch_type,
            )
            self._db.add(detail)
        self._db.commit()

    def count_completed_results(self, run_id: str) -> int:
        return (
            self._db.query(TestResult)
            .filter(
                TestResult.run_id == run_id,
                TestResult.status.notin_(["PENDING", "RUNNING"]),
            )
            .count()
        )

    def get_current_job(self, run_id: str) -> str | None:
        result = (
            self._db.query(TestResult.query_name)
            .filter(TestResult.run_id == run_id, TestResult.status == "RUNNING")
            .order_by(TestResult.id.desc())
            .first()
        )
        return result[0] if result else None

    def list_mismatches(
        self, result_id: int, limit: int = 100, offset: int = 0
    ) -> list[MismatchDetail]:
        return (
            self._db.query(MismatchDetail)
            .filter(MismatchDetail.test_result_id == result_id)
            .order_by(MismatchDetail.id)
            .offset(offset)
            .limit(limit)
            .all()
        )
