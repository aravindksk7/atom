from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, ScheduledRun, JobLineageEdge, AuditEvent,
)


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
        self._sync_lineage_edges(job)
        return job

    def upsert(self, job_data: dict) -> SavedJob:
        existing = self.get(job_data["name"])
        if existing is None:
            result = self.create(job_data)
        else:
            updated = self.update(existing.name, job_data)
            assert updated is not None
            result = updated
        self._sync_lineage_edges(result)
        return result

    def _sync_lineage_edges(self, job: SavedJob) -> None:
        params = job.params or {}
        depends_on: list[str] = params.get("depends_on", [])
        self._db.query(JobLineageEdge).filter_by(downstream_job=job.name).delete()
        for upstream in depends_on:
            self._db.add(JobLineageEdge(upstream_job=upstream, downstream_job=job.name))
        self._db.commit()

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
        run_type: str = "reconciliation",
        pair_id: str | None = None,
    ) -> TestRun:
        run = TestRun(
            run_id=run_id,
            status="PENDING",
            source_env=source_env,
            target_env=target_env,
            config_snapshot=config_snapshot,
            run_type=run_type,
            pair_id=pair_id,
        )
        self._db.add(run)
        self._db.commit()
        self._db.refresh(run)
        return run

    def get_run(self, run_id: str) -> TestRun | None:
        return self._db.query(TestRun).filter(TestRun.run_id == run_id).first()

    def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        run_type: str | None = None,
    ) -> list[TestRun]:
        q = self._db.query(TestRun)
        if status:
            q = q.filter(TestRun.status == status)
        if run_type:
            q = q.filter(TestRun.run_type == run_type)
        return q.order_by(TestRun.id.desc()).offset(offset).limit(limit).all()

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

    def accept_mismatch(
        self,
        mismatch_id: int,
        note: str,
        accepted_by: str | None,
    ) -> tuple[MismatchDetail, bool]:
        md = self._db.get(MismatchDetail, mismatch_id)
        if md is None:
            raise ValueError(f"MismatchDetail {mismatch_id} not found")
        md.accepted = True
        md.accepted_note = note
        md.accepted_at = datetime.now(timezone.utc)
        md.accepted_by = accepted_by
        self._db.commit()
        self._db.refresh(md)

        unaccepted = (
            self._db.query(MismatchDetail)
            .filter(
                MismatchDetail.test_result_id == md.test_result_id,
                MismatchDetail.accepted == False,  # noqa: E712
            )
            .count()
        )
        status_changed = False
        if unaccepted == 0:
            tr = self._db.get(TestResult, md.test_result_id)
            if tr and tr.status != "PASSED":
                tr.status = "PASSED"
                run = self.get_run(tr.run_id)
                if run:
                    run.passed = max(0, (run.passed or 0) + 1)
                    run.failed = max(0, (run.failed or 0) - 1)
                self._db.commit()
                status_changed = True
        return md, status_changed

    def count_unaccepted_mismatches(self, result_id: int) -> int:
        return (
            self._db.query(MismatchDetail)
            .filter(
                MismatchDetail.test_result_id == result_id,
                MismatchDetail.accepted == False,  # noqa: E712
            )
            .count()
        )

    def get_pair_runs(self, pair_id: str) -> list[TestRun]:
        return (
            self._db.query(TestRun)
            .filter(TestRun.pair_id == pair_id)
            .all()
        )

    def delete_run(self, run_id: str) -> bool:
        run = self.get_run(run_id)
        if run is None:
            return False
        self._db.delete(run)
        self._db.commit()
        return True

    def list_pairs(self) -> list[str]:
        rows = (
            self._db.query(TestRun.pair_id)
            .filter(TestRun.pair_id.isnot(None))
            .distinct()
            .all()
        )
        return [r[0] for r in rows]

    def set_baseline(self, run_id: str) -> TestRun | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        self._db.query(TestRun).filter_by(
            source_env=run.source_env, target_env=run.target_env
        ).update({"is_baseline": False})
        run.is_baseline = True
        self._db.commit()
        self._db.refresh(run)
        return run

    def get_baseline(self, source_env: str, target_env: str) -> TestRun | None:
        return (
            self._db.query(TestRun)
            .filter_by(source_env=source_env, target_env=target_env, is_baseline=True)
            .first()
        )

    def mismatch_distribution(
        self, result_id: int, top_n: int = 10
    ) -> list[dict]:
        from sqlalchemy import func
        rows = (
            self._db.query(
                MismatchDetail.column_name,
                MismatchDetail.source_value,
                MismatchDetail.target_value,
                func.count(MismatchDetail.id).label("count"),
            )
            .filter(MismatchDetail.test_result_id == result_id)
            .group_by(
                MismatchDetail.column_name,
                MismatchDetail.source_value,
                MismatchDetail.target_value,
            )
            .order_by(func.count(MismatchDetail.id).desc())
            .limit(top_n)
            .all()
        )
        return [
            {"column": r.column_name, "source": r.source_value, "target": r.target_value, "count": r.count}
            for r in rows
        ]


# ---------------------------------------------------------------------------
# P0 — Auth: API token repository
# ---------------------------------------------------------------------------

import hashlib
import secrets as _secrets


class TokenRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256(raw.encode()).hexdigest()

    def create(self, name: str, expires_at: datetime | None = None) -> tuple[str, ApiToken]:
        raw = "etl_" + _secrets.token_hex(32)
        token = ApiToken(token_hash=self._hash(raw), name=name, expires_at=expires_at)
        self._db.add(token)
        self._db.commit()
        self._db.refresh(token)
        return raw, token

    def verify(self, raw: str) -> ApiToken | None:
        h = self._hash(raw)
        token = self._db.query(ApiToken).filter_by(token_hash=h, enabled=True).first()
        if token is None:
            return None
        exp = token.expires_at
        if exp:
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return None
        token.last_used_at = datetime.now(timezone.utc)
        self._db.commit()
        return token

    def list(self) -> list[ApiToken]:
        return self._db.query(ApiToken).order_by(ApiToken.created_at.desc()).all()

    def revoke(self, token_id: int) -> bool:
        token = self._db.get(ApiToken, token_id)
        if token is None:
            return False
        token.enabled = False
        self._db.commit()
        return True


# ---------------------------------------------------------------------------
# P0 — Alerting: notification hook repository
# ---------------------------------------------------------------------------

class NotificationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, name: str, url: str, events: list[str],
               secret: str | None = None) -> NotificationHook:
        hook = NotificationHook(name=name, url=url, events=events, secret=secret)
        self._db.add(hook)
        self._db.commit()
        self._db.refresh(hook)
        return hook

    def list(self) -> list[NotificationHook]:
        return self._db.query(NotificationHook).order_by(NotificationHook.id).all()

    def get(self, hook_id: int) -> NotificationHook | None:
        return self._db.get(NotificationHook, hook_id)

    def delete(self, hook_id: int) -> bool:
        hook = self._db.get(NotificationHook, hook_id)
        if hook is None:
            return False
        self._db.delete(hook)
        self._db.commit()
        return True

    def list_enabled_for_event(self, event: str) -> list[NotificationHook]:
        return (
            self._db.query(NotificationHook)
            .filter(NotificationHook.enabled.is_(True))
            .all()
        )


# ---------------------------------------------------------------------------
# P0 — Scheduling: schedule repository
# ---------------------------------------------------------------------------

class ScheduleRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, data: dict) -> ScheduledRun:
        sched = ScheduledRun(**{k: v for k, v in data.items()
                                if hasattr(ScheduledRun, k)})
        self._db.add(sched)
        self._db.commit()
        self._db.refresh(sched)
        return sched

    def get(self, schedule_id: int) -> ScheduledRun | None:
        return self._db.get(ScheduledRun, schedule_id)

    def get_by_name(self, name: str) -> ScheduledRun | None:
        return self._db.query(ScheduledRun).filter_by(name=name).first()

    def list(self) -> list[ScheduledRun]:
        return self._db.query(ScheduledRun).order_by(ScheduledRun.name).all()

    def list_enabled(self) -> list[ScheduledRun]:
        return self._db.query(ScheduledRun).filter_by(enabled=True).all()

    def update(self, schedule_id: int, data: dict) -> ScheduledRun | None:
        sched = self._db.get(ScheduledRun, schedule_id)
        if sched is None:
            return None
        for k, v in data.items():
            if hasattr(sched, k):
                setattr(sched, k, v)
        self._db.commit()
        self._db.refresh(sched)
        return sched

    def delete(self, schedule_id: int) -> bool:
        sched = self._db.get(ScheduledRun, schedule_id)
        if sched is None:
            return False
        self._db.delete(sched)
        self._db.commit()
        return True

    def touch(self, schedule_id: int, last_run_at: datetime,
              next_run_at: datetime | None = None) -> None:
        sched = self._db.get(ScheduledRun, schedule_id)
        if sched:
            sched.last_run_at = last_run_at
            if next_run_at:
                sched.next_run_at = next_run_at
            self._db.commit()


# ---------------------------------------------------------------------------
# P3 — Job Lineage repository
# ---------------------------------------------------------------------------

class LineageRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def job_graph(self) -> dict:
        jobs = self._db.query(SavedJob).all()
        edges = self._db.query(JobLineageEdge).all()
        nodes = [{"name": j.name, "job_type": j.job_type} for j in jobs]
        edge_list = [
            {"from": e.upstream_job, "to": e.downstream_job, "type": e.edge_type}
            for e in edges
        ]
        return {"nodes": nodes, "edges": edge_list}

    def get_upstream(self, job_name: str) -> list[str]:
        rows = (
            self._db.query(JobLineageEdge.upstream_job)
            .filter_by(downstream_job=job_name)
            .all()
        )
        return [r[0] for r in rows]

    def get_downstream(self, job_name: str) -> list[str]:
        rows = (
            self._db.query(JobLineageEdge.downstream_job)
            .filter_by(upstream_job=job_name)
            .all()
        )
        return [r[0] for r in rows]


class AuditRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def log(
        self,
        actor: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        diff: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            diff=diff,
        )
        self._db.add(event)
        self._db.commit()
        self._db.refresh(event)
        return event

    def list(
        self,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        q = self._db.query(AuditEvent)
        if resource_type:
            q = q.filter(AuditEvent.resource_type == resource_type)
        if resource_id:
            q = q.filter(AuditEvent.resource_id == resource_id)
        q = q.order_by(AuditEvent.created_at.desc())
        return q.offset(offset).limit(limit).all()
