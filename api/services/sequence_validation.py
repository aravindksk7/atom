"""Pure validation and ordering for saved execution sequences.

No database access and no HTTP concerns live here so the rules can be unit
tested directly and reused by both the CRUD routes and the resolver.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover
    from api.schemas import SequencePrecondition, SequenceStepRef


class SequenceCycleError(Exception):
    """Raised when a dependency graph cannot be topologically ordered."""

    def __init__(self, step_ids: list[str]) -> None:
        self.step_ids = step_ids
        super().__init__(f"Dependency cycle among steps: {', '.join(sorted(step_ids))}")


def _issue(step_id: str | None, field: str, message: str) -> dict:
    return {"step_id": step_id, "field": field, "message": message}


def topological_order(steps: list["SequenceStepRef"]) -> list[str]:
    """Return step_ids parents-first.

    Kahn's algorithm, emitting each ready level in declared order so the same
    saved sequence always produces the same run order.
    """
    position = {s.step_id: i for i, s in enumerate(steps)}
    remaining = {s.step_id: set(s.depends_on) for s in steps}
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            (sid for sid, deps in remaining.items() if not deps),
            key=lambda sid: position[sid],
        )
        if not ready:
            raise SequenceCycleError(list(remaining))
        for sid in ready:
            ordered.append(sid)
            del remaining[sid]
        for deps in remaining.values():
            deps.difference_update(ready)
    return ordered


def validate_steps(
    steps: list["SequenceStepRef"], known_job_names: Iterable[str]
) -> list[dict]:
    """Return a list of issues; an empty list means the sequence is valid."""
    known = set(known_job_names)
    errors: list[dict] = []

    if not steps:
        return [_issue(None, "steps", "A sequence must contain at least one step")]

    seen: set[str] = set()
    for step in steps:
        if step.step_id in seen:
            errors.append(_issue(step.step_id, "step_id", f"Duplicate step_id '{step.step_id}'"))
        seen.add(step.step_id)

    for step in steps:
        if step.job_name not in known:
            errors.append(
                _issue(step.step_id, "job_name", f"Unknown or disabled job '{step.job_name}'")
            )
        for dep in step.depends_on:
            if dep == step.step_id:
                errors.append(
                    _issue(step.step_id, "depends_on", f"Step '{step.step_id}' cannot depend on itself")
                )
            elif dep not in seen:
                errors.append(
                    _issue(step.step_id, "depends_on", f"Step '{step.step_id}' depends on unknown step '{dep}'")
                )

    if not errors:
        try:
            topological_order(steps)
        except SequenceCycleError as exc:
            errors.append(_issue(None, "depends_on", str(exc).replace("Dependency cycle", "Dependency cycle detected")))

    return errors


# --- Phase gating -----------------------------------------------------------
# Phase 3/4 features remain gated until their executor support arrives.

def phase1_unsupported(
    steps: list["SequenceStepRef"], preconditions: "SequencePrecondition | None"
) -> list[dict]:
    """Fields the executor cannot honour yet.

    Trigger rules opened in Phase 2; retry and failure policy in Phase 3. Only
    sequence preconditions remain, and they arrive in Phase 4.
    """
    if preconditions is not None:
        return [_issue(None, "preconditions", "Sequence preconditions arrive in Phase 4")]
    return []
