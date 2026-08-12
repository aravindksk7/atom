"""The single place that turns a SequenceRef into something runnable.

Every caller -- ad-hoc launch, selection launch, and the scheduler -- goes
through resolve(), so nothing else in the codebase learns how sequences are
stored or ordered.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from api.schemas import (
    SequenceDefaults,
    SequencePrecondition,
    SequenceRef,
    SequenceStep,
    SequenceStepRef,
)
from api.services.sequence_validation import SequenceCycleError, topological_order
from etl_framework.repository.repository import JobRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository


class SequenceResolutionError(Exception):
    """A saved sequence cannot be turned into a runnable step list."""


@dataclass(frozen=True)
class ResolvedSequence:
    sequence_id: int
    sequence_name: str
    version_number: int
    steps: list[SequenceStepRef]          # topologically ordered, parents first
    preconditions: SequencePrecondition | None
    defaults: SequenceDefaults

    def as_linear_steps(self) -> list[SequenceStep]:
        """Downgrade to the shape the existing linear executor consumes.

        DAG-only fields are dropped on purpose: Phase 1 rejects any non-default
        value for them at save time, so nothing is silently lost here.
        """
        return [
            SequenceStep(
                job_name=step.job_name,
                hold_after=step.hold_after,
                condition=step.condition,
                wait_seconds=step.wait_seconds,
            )
            for step in self.steps
        ]

    def snapshot_meta(self) -> dict:
        return {
            "id": self.sequence_id,
            "name": self.sequence_name,
            "version": self.version_number,
        }


def resolve(db: Session, ref: SequenceRef) -> ResolvedSequence:
    repo = ExecutionSequenceRepository(db)
    sequence = repo.get(ref.sequence_id)
    if sequence is None:
        raise SequenceResolutionError(f"Execution sequence {ref.sequence_id} not found")

    version = (
        repo.get_version(ref.sequence_id, ref.sequence_version)
        if ref.sequence_version is not None
        else repo.latest_version(ref.sequence_id)
    )
    if version is None:
        raise SequenceResolutionError(
            f"Execution sequence '{sequence.name}' has no version "
            f"{ref.sequence_version if ref.sequence_version is not None else '(latest)'}"
        )

    steps = [SequenceStepRef.model_validate(s) for s in (version.steps_json or [])]
    if not steps:
        raise SequenceResolutionError(
            f"Execution sequence '{sequence.name}' v{version.version_number} has no steps"
        )

    # Fail fast and completely: a job deleted or disabled after the sequence was
    # saved must never produce a half-executed run.
    known = {j.name for j in JobRepository(db).list() if j.enabled}
    missing = sorted({s.job_name for s in steps if s.job_name not in known})
    if missing:
        raise SequenceResolutionError(
            f"Execution sequence '{sequence.name}' v{version.version_number} references "
            f"unknown or disabled jobs: {', '.join(missing)}"
        )

    try:
        order = topological_order(steps)
    except SequenceCycleError as exc:
        raise SequenceResolutionError(
            f"Execution sequence '{sequence.name}' v{version.version_number}: {exc}"
        ) from exc

    by_id = {s.step_id: s for s in steps}
    preconditions = (
        SequencePrecondition.model_validate(version.preconditions_json)
        if version.preconditions_json else None
    )

    return ResolvedSequence(
        sequence_id=sequence.id,
        sequence_name=sequence.name,
        version_number=version.version_number,
        steps=[by_id[sid] for sid in order],
        preconditions=preconditions,
        defaults=SequenceDefaults.model_validate(version.defaults_json or {}),
    )
