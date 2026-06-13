from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository.models import SavedConfig, TestRun, TestResult, MismatchDetail


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
