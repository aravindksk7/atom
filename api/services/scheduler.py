"""In-process APScheduler wrapper for scheduled reconciliation runs."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("api.scheduler")

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _APSCHEDULER_AVAILABLE = True
except ImportError:
    _APSCHEDULER_AVAILABLE = False

_scheduler: "BackgroundScheduler | None" = None


def _job_id(schedule_id: int) -> str:
    return f"etl_schedule_{schedule_id}"


def _parse_schedule_id_from_job_id(job_id: str) -> int | None:
    prefix = "etl_schedule_"
    if not job_id.startswith(prefix):
        return None
    try:
        return int(job_id[len(prefix):])
    except ValueError:
        return None


def _iso_or_none(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def get_scheduler_runtime_snapshot() -> dict:
    """Return read-only APScheduler state for status and statistics reports."""
    if not _APSCHEDULER_AVAILABLE:
        return {
            "available": False,
            "running": False,
            "job_count": 0,
            "timezone": "UTC",
            "jobs": {},
        }
    if _scheduler is None:
        return {
            "available": True,
            "running": False,
            "job_count": 0,
            "timezone": "UTC",
            "jobs": {},
        }
    jobs = {}
    for job in _scheduler.get_jobs():
        schedule_id = _parse_schedule_id_from_job_id(job.id)
        if schedule_id is None:
            continue
        jobs[schedule_id] = {
            "job_id": job.id,
            "next_run_at": _iso_or_none(job.next_run_time),
        }
    timezone_value = getattr(_scheduler, "timezone", None)
    return {
        "available": True,
        "running": bool(getattr(_scheduler, "running", False)),
        "job_count": len(jobs),
        "timezone": str(timezone_value or "UTC"),
        "jobs": jobs,
    }


def _run_schedule(schedule_id: int, name: str) -> None:
    """Called by APScheduler; runs inside a daemon thread."""
    import uuid as _uuid
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import ScheduleRepository, JobSelectionRepository, RunRepository
    from api.routes.runs import _execute_run, _snapshot_from_trigger
    from api.schemas import RunTrigger

    db = SessionLocal()
    try:
        repo = ScheduleRepository(db)
        sched = repo.get(schedule_id)
        if sched is None or not sched.enabled:
            return

        sel_repo = JobSelectionRepository(db)
        version = sel_repo.get_version(sched.selection_id, sched.selection_version)
        if version is None:
            logger.error(
                "Schedule '%s' references missing selection %s v%s; skipping run",
                name, sched.selection_id, sched.selection_version,
            )
            return

        run_repo = RunRepository(db)
        if run_repo.has_active_run_for_selection(sched.selection_id):
            logger.info(
                "Schedule '%s' skipped because selection %s already has an active run",
                name, sched.selection_id,
            )
            return

        trigger = RunTrigger(
            source_env=sched.source_env,
            target_env=sched.target_env,
            job_sequence=version.job_sequence or [],
            run_settings=version.run_settings_json or {},
        )
        run_id = str(_uuid.uuid4())
        config_snapshot = _snapshot_from_trigger(trigger, db)
        config_snapshot["job_sequence"] = [
            s.model_dump() if hasattr(s, "model_dump") else s for s in trigger.job_sequence
        ]
        config_snapshot["run_settings"] = trigger.run_settings.model_dump()

        run_repo.create_run(
            run_id=run_id,
            source_env=trigger.source_env,
            target_env=trigger.target_env,
            config_snapshot=config_snapshot,
            selection_id=sched.selection_id,
            selection_version=sched.selection_version,
        )
        _execute_run(
            run_id=run_id,
            job_sequence=trigger.job_sequence,
            source_env=trigger.source_env,
            target_env=trigger.target_env,
            run_settings=trigger.run_settings,
            config_snapshot=config_snapshot,
        )
        repo.touch(schedule_id, last_run_at=datetime.now(timezone.utc))
        logger.info("Scheduled run '%s' started as %s", name, run_id)
    except Exception as exc:
        logger.exception("Scheduled run '%s' failed: %s", name, exc)
    finally:
        db.close()


def start() -> None:
    """Start the background scheduler and load all enabled schedules from DB."""
    global _scheduler
    if not _APSCHEDULER_AVAILABLE:
        logger.warning("APScheduler not installed — scheduling disabled.")
        return

    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import ScheduleRepository

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.start()

    from apscheduler.triggers.interval import IntervalTrigger
    _scheduler.add_job(
        _escalate_contracts,
        trigger=IntervalTrigger(minutes=15),
        id="contract_escalation",
        replace_existing=True,
        misfire_grace_time=120,
    )

    db = SessionLocal()
    try:
        schedules = ScheduleRepository(db).list_enabled()
        for s in schedules:
            _add_job(s)
        logger.info("Scheduler started with %d job(s).", len(schedules))
    finally:
        db.close()


def stop() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def _current_timezone() -> str:
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import SettingsRepository
    db = SessionLocal()
    try:
        return SettingsRepository(db).get_timezone()
    finally:
        db.close()


def _add_job(sched) -> None:
    if _scheduler is None:
        return
    try:
        trigger = CronTrigger.from_crontab(sched.cron_expr, timezone=_current_timezone())
        _scheduler.add_job(
            _run_schedule,
            trigger=trigger,
            id=_job_id(sched.id),
            args=[sched.id, sched.name],
            replace_existing=True,
            misfire_grace_time=300,
            max_instances=1,
            coalesce=True,
        )
    except Exception as exc:
        logger.warning("Failed to schedule '%s': %s", sched.name, exc)


def add_job(sched) -> None:
    _add_job(sched)


def remove_job(schedule_id: int) -> None:
    if _scheduler is None:
        return
    jid = _job_id(schedule_id)
    if _scheduler.get_job(jid):
        _scheduler.remove_job(jid)


def reload_job(sched) -> None:
    remove_job(sched.id)
    if sched.enabled:
        _add_job(sched)


def refresh_all_timezones() -> None:
    """Re-add every enabled schedule so its CronTrigger picks up the current app timezone."""
    if _scheduler is None:
        return
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import ScheduleRepository
    db = SessionLocal()
    try:
        for sched in ScheduleRepository(db).list_enabled():
            _add_job(sched)
    finally:
        db.close()


def is_available() -> bool:
    return _APSCHEDULER_AVAILABLE


def _escalate_contracts() -> None:
    """Escalate overdue contract breaches and fire webhooks. Runs every 15 minutes."""
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.contract_repository import ContractRepository
    from etl_framework.repository.repository import NotificationRepository
    from api.services.notifier import notify

    db = SessionLocal()
    try:
        repo = ContractRepository(db)
        escalated = repo.escalate_overdue()
        if not escalated:
            return
        hooks = NotificationRepository(db).list_enabled_for_event("contract.escalated")
        for breach, contract in escalated:
            notify(
                breach.run_id,
                "contract.escalated",
                extra={
                    "contract": contract.name,
                    "source_job": contract.source_job,
                    "owner": contract.owner,
                    "sla_hours": contract.sla_hours,
                    "breach_id": breach.id,
                },
                hooks=hooks,
                db_session=db,
            )
            logger.warning(
                "Contract '%s' escalated (SLA %.1fh exceeded, breach %s)",
                contract.name,
                contract.sla_hours,
                breach.id,
            )
    except Exception as exc:
        logger.exception("Contract escalation check failed: %s", exc)
    finally:
        db.close()
