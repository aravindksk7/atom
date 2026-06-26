from __future__ import annotations
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, NotificationDelivery, ScheduledRun, JobLineageEdge, AuditEvent,
    RunStep,
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
import hmac as _hmac
import os
import secrets as _secrets

_TOKEN_MAX_TTL_DAYS = 730  # hard cap: no token lives longer than 2 years

# HMAC key bound to this server installation.  Set TOKEN_HMAC_SECRET in the
# environment to a long random value (e.g. `openssl rand -hex 32`).
# Without it, a DB-only compromise would let an attacker verify token hashes;
# with it, the attacker also needs this secret.
_HMAC_SECRET: bytes = os.environ.get("TOKEN_HMAC_SECRET", "").encode() or b""


class TokenRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    @staticmethod
    def _hash(raw: str) -> str:
        """Return HMAC-SHA256 when TOKEN_HMAC_SECRET is set, else plain SHA-256."""
        if _HMAC_SECRET:
            return _hmac.new(_HMAC_SECRET, raw.encode(), hashlib.sha256).hexdigest()
        return hashlib.sha256(raw.encode()).hexdigest()

    def create(self, name: str, expires_at: datetime | None = None, is_admin: bool = False) -> tuple[str, ApiToken]:
        if expires_at is not None:
            cap = datetime.now(timezone.utc) + timedelta(days=_TOKEN_MAX_TTL_DAYS)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > cap:
                expires_at = cap
        raw = "etl_" + _secrets.token_hex(32)
        token = ApiToken(
            token_hash=self._hash(raw),
            name=name,
            expires_at=expires_at,
            is_admin=is_admin,
            token_hint=raw[-8:],
        )
        self._db.add(token)
        self._db.commit()
        self._db.refresh(token)
        return raw, token

    def verify(self, raw: str) -> ApiToken | None:
        h = self._hash(raw)
        token = self._db.query(ApiToken).filter_by(token_hash=h, enabled=True).first()

        # Migration path: if TOKEN_HMAC_SECRET is set but the stored hash is the
        # old plain-SHA256 value, re-hash and update the row transparently.
        if token is None and _HMAC_SECRET:
            legacy_h = hashlib.sha256(raw.encode()).hexdigest()
            token = self._db.query(ApiToken).filter_by(token_hash=legacy_h, enabled=True).first()
            if token is not None:
                token.token_hash = h  # upgrade to HMAC hash
                self._db.commit()

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

    def revoke(self, token_id: int) -> str | None:
        token = self._db.get(ApiToken, token_id)
        if token is None:
            return None
        token_hash = token.token_hash
        token.enabled = False
        self._db.commit()
        return token_hash

    def count(self) -> int:
        return self._db.query(ApiToken).count()


# ---------------------------------------------------------------------------
# P0 — Alerting: notification hook repository
# ---------------------------------------------------------------------------

class NotificationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, name: str, url: str, events: list[str],
               secret: str | None = None) -> NotificationHook:
        from api.services.secret_store import encrypt_secret
        stored_secret = encrypt_secret(secret) if secret else secret
        hook = NotificationHook(name=name, url=url, events=events, secret=stored_secret)
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
# P0 — Alerting: notification delivery repository
# ---------------------------------------------------------------------------

class NotificationDeliveryRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create_delivery_attempt(
        self,
        hook_id: int,
        run_id: str,
        event: str,
        status: str = "pending",
        error_message: str | None = None,
        response_status_code: int | None = None,
        response_body: str | None = None
    ) -> NotificationDelivery:
        delivery = NotificationDelivery(
            hook_id=hook_id,
            run_id=run_id,
            event=event,
            status=status,
            attempt_count=1,
            last_attempt_at=datetime.now(timezone.utc),
            error_message=error_message,
            response_status_code=response_status_code,
            response_body=response_body
        )
        self._db.add(delivery)
        self._db.commit()
        self._db.refresh(delivery)
        return delivery

    def update_delivery_status(
        self,
        delivery_id: int,
        status: str,
        error_message: str | None = None,
        response_status_code: int | None = None,
        response_body: str | None = None
    ) -> NotificationDelivery | None:
        delivery = self._db.get(NotificationDelivery, delivery_id)
        if delivery is None:
            return None

        delivery.status = status
        delivery.last_attempt_at = datetime.now(timezone.utc)
        if status == "success":
            delivery.delivered_at = datetime.now(timezone.utc)

        if error_message is not None:
            delivery.error_message = error_message
        if response_status_code is not None:
            delivery.response_status_code = response_status_code
        if response_body is not None:
            delivery.response_body = response_body

        self._db.commit()
        self._db.refresh(delivery)
        return delivery

    def list_deliveries_for_run(
        self,
        run_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> list[NotificationDelivery]:
        return (
            self._db.query(NotificationDelivery)
            .filter(NotificationDelivery.run_id == run_id)
            .order_by(NotificationDelivery.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def list_deliveries_for_hook(
        self,
        hook_id: int,
        limit: int = 50,
        offset: int = 0
    ) -> list[NotificationDelivery]:
        return (
            self._db.query(NotificationDelivery)
            .filter(NotificationDelivery.hook_id == hook_id)
            .order_by(NotificationDelivery.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def get_latest_delivery(
        self,
        hook_id: int,
        run_id: str,
        event: str
    ) -> NotificationDelivery | None:
        return (
            self._db.query(NotificationDelivery)
            .filter(
                NotificationDelivery.hook_id == hook_id,
                NotificationDelivery.run_id == run_id,
                NotificationDelivery.event == event
            )
            .order_by(NotificationDelivery.id.desc())
            .first()
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


class RunStepRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def materialize_steps(self, run_id: str, steps: list) -> list[RunStep]:
        rows: list[RunStep] = []
        for i, step in enumerate(steps):
            cond = step.condition.model_dump() if step.condition is not None else None
            row = RunStep(
                run_id=run_id,
                job_name=step.job_name,
                step_index=i,
                status="PENDING",
                hold_after=step.hold_after,
                condition=cond,
                wait_seconds=step.wait_seconds,
            )
            self._db.add(row)
            rows.append(row)
        self._db.commit()
        for row in rows:
            self._db.refresh(row)
        return rows

    def get_step(self, run_id: str, step_index: int) -> RunStep | None:
        return (
            self._db.query(RunStep)
            .filter(RunStep.run_id == run_id, RunStep.step_index == step_index)
            .first()
        )

    def list_steps(self, run_id: str) -> list[RunStep]:
        return (
            self._db.query(RunStep)
            .filter(RunStep.run_id == run_id)
            .order_by(RunStep.step_index)
            .all()
        )

    def update_status(
        self, run_id: str, step_index: int, status: str, **kwargs
    ) -> RunStep | None:
        step = self.get_step(run_id, step_index)
        if step is None:
            return None
        step.status = status
        for k, v in kwargs.items():
            setattr(step, k, v)
        self._db.commit()
        self._db.refresh(step)
        return step

    def release_step(
        self,
        run_id: str,
        step_index: int,
        action: str,
        note: str,
        released_by: str,
    ) -> RunStep | None:
        step = self.get_step(run_id, step_index)
        if step is None or step.status != "HELD":
            return None
        _status_map = {"approve": "APPROVED", "skip": "SKIPPED", "cancel": "CANCELLED"}
        step.status = _status_map.get(action.lower(), action.upper())
        step.release_action = action
        step.release_note = note
        step.released_by = released_by
        step.released_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(step)
        return step

    def cancel_remaining(self, run_id: str, from_index: int) -> None:
        (
            self._db.query(RunStep)
            .filter(
                RunStep.run_id == run_id,
                RunStep.step_index >= from_index,
                RunStep.status == "PENDING",
            )
            .update({"status": "CANCELLED"})
        )
        self._db.commit()


class ColumnProfileRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def save(
        self, job_name: str, run_id: str | None, column_name: str,
        null_rate: float | None, distinct_count: int | None,
        min_val: str | None, max_val: str | None,
        mean_val: float | None, std_val: float | None,
        p25: float | None, p50: float | None, p75: float | None, p95: float | None,
    ) -> None:
        from etl_framework.repository.models import ColumnProfile
        row = ColumnProfile(
            job_name=job_name, run_id=run_id, column_name=column_name,
            null_rate=null_rate, distinct_count=distinct_count,
            min_val=min_val, max_val=max_val,
            mean_val=mean_val, std_val=std_val,
            p25=p25, p50=p50, p75=p75, p95=p95,
            captured_at=datetime.now(timezone.utc),
        )
        self._db.add(row)

    def get_latest(self, job_name: str) -> list:
        from etl_framework.repository.models import ColumnProfile
        from sqlalchemy import func
        max_captured = (
            self._db.query(
                ColumnProfile.column_name,
                func.max(ColumnProfile.captured_at).label("max_captured"),
            )
            .filter(ColumnProfile.job_name == job_name)
            .group_by(ColumnProfile.column_name)
            .subquery()
        )
        return (
            self._db.query(ColumnProfile)
            .join(
                max_captured,
                (ColumnProfile.column_name == max_captured.c.column_name)
                & (ColumnProfile.captured_at == max_captured.c.max_captured),
            )
            .filter(ColumnProfile.job_name == job_name)
            .all()
        )

    def get_history(self, job_name: str, column_name: str) -> list:
        from etl_framework.repository.models import ColumnProfile
        return (
            self._db.query(ColumnProfile)
            .filter(
                ColumnProfile.job_name == job_name,
                ColumnProfile.column_name == column_name,
            )
            .order_by(ColumnProfile.captured_at.asc())
            .all()
        )

    def get_latest_for_run(self, job_name: str, run_id: str) -> list:
        from etl_framework.repository.models import ColumnProfile
        return (
            self._db.query(ColumnProfile)
            .filter(ColumnProfile.job_name == job_name, ColumnProfile.run_id == run_id)
            .all()
        )


class SchemaSnapshotRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def save(self, job_name: str, run_id: str | None, environment: str, columns: list[dict]) -> None:
        from etl_framework.repository.models import SchemaSnapshot
        row = SchemaSnapshot(
            job_name=job_name, run_id=run_id, environment=environment,
            columns=columns, captured_at=datetime.now(timezone.utc),
        )
        self._db.add(row)

    def get_latest(self, job_name: str, environment: str):
        from etl_framework.repository.models import SchemaSnapshot
        return (
            self._db.query(SchemaSnapshot)
            .filter(SchemaSnapshot.job_name == job_name, SchemaSnapshot.environment == environment)
            .order_by(SchemaSnapshot.captured_at.desc())
            .first()
        )

    def get_history(self, job_name: str, environment: str) -> list:
        from etl_framework.repository.models import SchemaSnapshot
        return (
            self._db.query(SchemaSnapshot)
            .filter(SchemaSnapshot.job_name == job_name, SchemaSnapshot.environment == environment)
            .order_by(SchemaSnapshot.captured_at.asc())
            .all()
        )
