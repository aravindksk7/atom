"""Per-job progress of a difference export, and the thread that publishes it."""
from __future__ import annotations

import copy
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from etl_framework.repository.models import DifferenceExportJob

logger = logging.getLogger("api.services.export_progress")

HEARTBEAT_INTERVAL_SECONDS = 30.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExportProgress:
    """What each job of an export is doing. Safe to read from another thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: list[dict[str, Any]] = []
        self.on_change: Callable[[], None] | None = None

    def set_jobs(self, names: list[str]) -> None:
        with self._lock:
            self._jobs = [
                {
                    "name": name, "state": "waiting", "rows": 0,
                    "started_at": None, "finished_at": None, "error": None,
                }
                for name in names
            ]
        self._changed()

    def start(self, name: str) -> None:
        self._update(name, "waiting", state="running", started_at=_now_iso())

    def finish(self, name: str, rows: int) -> None:
        self._update(name, "running", state="done", rows=rows, finished_at=_now_iso())

    def fail(self, name: str, error: str, rows: int = 0) -> None:
        self._update(name, "running", state="failed", rows=rows, error=error, finished_at=_now_iso())

    @property
    def failed_jobs(self) -> list[str]:
        with self._lock:
            return [job["name"] for job in self._jobs if job["state"] == "failed"]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"jobs": copy.deepcopy(self._jobs)}

    def _update(self, name: str, from_state: str, **fields: Any) -> None:
        # A DAG may run one job as two steps, so match the first entry in the
        # state this change moves on from, not simply the first with this name.
        with self._lock:
            for job in self._jobs:
                if job["name"] == name and job["state"] == from_state:
                    job.update(fields)
                    break
        self._changed()

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()


class ExportHeartbeat:
    """Shows an export is alive, and publishes its progress, from its own session.

    Runs on a thread because a single slow query can outlast the staleness limit
    with nothing else happening. A failed write is logged and tried again on the
    next beat: reporting progress must never fail the export it reports on.
    """

    def __init__(
        self,
        export_id: str,
        progress: ExportProgress,
        session_factory: Callable[[], Any],
        interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self._export_id = export_id
        self._progress = progress
        self._session_factory = session_factory
        self._interval = interval
        self._wake = threading.Event()
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"export-heartbeat-{export_id}")

    def __enter__(self) -> "ExportHeartbeat":
        self._progress.on_change = self._wake.set
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._progress.on_change = None
        self._stopping = True
        self._wake.set()
        self._thread.join(timeout=5)
        return False

    def _loop(self) -> None:
        while True:
            self._wake.wait(self._interval)
            self._wake.clear()
            if self._stopping:
                return
            self._beat()

    def _beat(self) -> None:
        db = None
        try:
            db = self._session_factory()
            # Only while RUNNING, so a beat that lands late cannot overwrite the
            # final metadata of an export that has just finished.
            db.query(DifferenceExportJob).filter(
                DifferenceExportJob.export_id == self._export_id,
                DifferenceExportJob.status == "RUNNING",
            ).update(
                {
                    "heartbeat_at": datetime.now(timezone.utc),
                    "metadata_json": {"progress": self._progress.snapshot()},
                },
                synchronize_session=False,
            )
            db.commit()
        except Exception:
            logger.warning("Heartbeat failed for export %s", self._export_id, exc_info=True)
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    logger.warning("Rollback after a failed heartbeat also failed", exc_info=True)
        finally:
            if db is not None:
                db.close()
