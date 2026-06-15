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


def _run_schedule(schedule_id: int, name: str) -> None:
    """Called by APScheduler; runs inside a daemon thread."""
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.repository import ScheduleRepository
    from api.routes.runs import _execute_run
    from api.schemas import RunSettings

    db = SessionLocal()
    try:
        repo = ScheduleRepository(db)
        sched = repo.get(schedule_id)
        if sched is None or not sched.enabled:
            return
        run_id = str(uuid.uuid4())
        settings = RunSettings(**(sched.run_settings_json or {}))
        _execute_run(
            run_id=run_id,
            job_sequence=sched.job_sequence or [],
            source_env=sched.source_env,
            target_env=sched.target_env,
            run_settings=settings,
            config_snapshot=sched.run_settings_json or {},
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


def _add_job(sched) -> None:
    if _scheduler is None:
        return
    try:
        trigger = CronTrigger.from_crontab(sched.cron_expr, timezone="UTC")
        _scheduler.add_job(
            _run_schedule,
            trigger=trigger,
            id=_job_id(sched.id),
            args=[sched.id, sched.name],
            replace_existing=True,
            misfire_grace_time=300,
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


def is_available() -> bool:
    return _APSCHEDULER_AVAILABLE
