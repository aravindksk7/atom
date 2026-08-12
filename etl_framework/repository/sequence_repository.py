"""Persistence for saved execution sequences.

Lives in its own module rather than repository.py, which is already large.
Mirrors JobSelectionRepository so the two read the same way.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from etl_framework.repository.models import (
    ExecutionSequence,
    ExecutionSequenceVersion,
    JobSelection,
    JobSelectionVersion,
    ScheduledRun,
)


class ExecutionSequenceRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # --- reads --------------------------------------------------------------

    def get(self, sequence_id: int) -> ExecutionSequence | None:
        return self._db.get(ExecutionSequence, sequence_id)

    def get_by_name(self, name: str) -> ExecutionSequence | None:
        return self._db.query(ExecutionSequence).filter_by(name=name).first()

    def list(self, include_archived: bool = False) -> list[ExecutionSequence]:
        q = self._db.query(ExecutionSequence)
        if not include_archived:
            q = q.filter(ExecutionSequence.archived.is_(False))
        return q.order_by(ExecutionSequence.name).all()

    def latest_version(self, sequence_id: int) -> ExecutionSequenceVersion | None:
        return (
            self._db.query(ExecutionSequenceVersion)
            .filter_by(sequence_id=sequence_id)
            .order_by(ExecutionSequenceVersion.version_number.desc())
            .first()
        )

    def get_version(self, sequence_id: int, version_number: int) -> ExecutionSequenceVersion | None:
        return (
            self._db.query(ExecutionSequenceVersion)
            .filter_by(sequence_id=sequence_id, version_number=version_number)
            .first()
        )

    # --- writes -------------------------------------------------------------

    def create(
        self, name: str, description: str, tags: list[str], steps: list,
        preconditions: dict | None = None, defaults: dict | None = None,
    ) -> ExecutionSequence:
        sequence = ExecutionSequence(name=name, description=description, tags=tags or [])
        self._db.add(sequence)
        self._db.flush()
        self._db.add(ExecutionSequenceVersion(
            sequence_id=sequence.id, version_number=1, steps_json=steps or [],
            preconditions_json=preconditions, defaults_json=defaults or {},
        ))
        self._db.commit()
        self._db.refresh(sequence)
        return sequence

    def create_new_version(
        self, sequence_id: int, steps: list,
        preconditions: dict | None = None, defaults: dict | None = None,
    ) -> ExecutionSequenceVersion | None:
        sequence = self.get(sequence_id)
        if sequence is None:
            return None
        current = self.latest_version(sequence_id)
        version = ExecutionSequenceVersion(
            sequence_id=sequence_id,
            version_number=(current.version_number + 1 if current else 1),
            steps_json=steps or [],
            preconditions_json=preconditions,
            defaults_json=defaults if defaults is not None else (current.defaults_json if current else {}),
        )
        self._db.add(version)
        sequence.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(version)
        return version

    def update_metadata(
        self, sequence_id: int, name: str | None = None, description: str | None = None,
        tags: list[str] | None = None, archived: bool | None = None,
    ) -> ExecutionSequence | None:
        sequence = self.get(sequence_id)
        if sequence is None:
            return None
        if name is not None:
            sequence.name = name
        if description is not None:
            sequence.description = description
        if tags is not None:
            sequence.tags = tags
        if archived is not None:
            sequence.archived = archived
        sequence.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(sequence)
        return sequence

    def archive_or_raise(self, sequence_id: int) -> ExecutionSequence | None:
        sequence = self.get(sequence_id)
        if sequence is None:
            return None
        if self.active_schedule_count(sequence_id) > 0:
            raise ValueError("Cannot archive: an enabled schedule still references this sequence")
        sequence.archived = True
        self._db.commit()
        self._db.refresh(sequence)
        return sequence

    # --- usage --------------------------------------------------------------

    def active_schedule_count(self, sequence_id: int) -> int:
        return (
            self._db.query(ScheduledRun)
            .filter(ScheduledRun.sequence_id == sequence_id, ScheduledRun.enabled.is_(True))
            .count()
        )

    def usage(self, sequence_id: int) -> dict:
        """Who references this sequence.

        Schedules resolve through an indexed column. Selections keep their
        reference inside a JSON column, so that side is a scan -- acceptable at
        this table size and only used by the UI and the archive guard.
        """
        schedules = [
            {"id": s.id, "name": s.name, "version": s.sequence_version}
            for s in self._db.query(ScheduledRun)
            .filter(ScheduledRun.sequence_id == sequence_id)
            .order_by(ScheduledRun.name)
            .all()
        ]

        selections: list[dict] = []
        rows = (
            self._db.query(JobSelectionVersion, JobSelection)
            .join(JobSelection, JobSelection.id == JobSelectionVersion.selection_id)
            .filter(JobSelectionVersion.sequence_ref.isnot(None))
            .all()
        )
        seen: set[int] = set()
        for version, selection in rows:
            ref = version.sequence_ref or {}
            if ref.get("sequence_id") != sequence_id or selection.id in seen:
                continue
            seen.add(selection.id)
            selections.append({
                "id": selection.id, "name": selection.name,
                "version": ref.get("sequence_version"),
            })
        selections.sort(key=lambda s: s["name"])

        return {"schedules": schedules, "selections": selections}
