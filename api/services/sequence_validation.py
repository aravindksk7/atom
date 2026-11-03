"""Pure validation and ordering for saved execution sequences.

No database access and no HTTP concerns live here so the rules can be unit
tested directly and reused by both the CRUD routes and the resolver.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover
    from api.schemas import SequenceFlowInput, SequenceStage, SequenceStepRef


class SequenceCycleError(Exception):
    """Raised when a dependency graph cannot be topologically ordered."""

    def __init__(self, step_ids: list[str]) -> None:
        self.step_ids = step_ids
        super().__init__(f"Dependency cycle among steps: {', '.join(sorted(step_ids))}")


def _issue(step_id: str | None, field: str, message: str) -> dict:
    return {"step_id": step_id, "field": field, "message": message}


def _topological_levels(steps: list["SequenceStepRef"]) -> list[list[str]]:
    """Group step_ids by depth, each level in declared order.

    Kahn's algorithm, so the same saved sequence always produces the same run
    order. A cycle or a parent that does not exist leaves steps that never
    become ready, which raises SequenceCycleError.
    """
    position = {s.step_id: i for i, s in enumerate(steps)}
    remaining = {s.step_id: set(s.depends_on) for s in steps}
    levels: list[list[str]] = []
    while remaining:
        ready = sorted(
            (sid for sid, deps in remaining.items() if not deps),
            key=lambda sid: position[sid],
        )
        if not ready:
            raise SequenceCycleError(list(remaining))
        levels.append(ready)
        for sid in ready:
            del remaining[sid]
        for deps in remaining.values():
            deps.difference_update(ready)
    return levels


def topological_order(steps: list["SequenceStepRef"]) -> list[str]:
    """Return step_ids parents-first."""
    return [sid for level in _topological_levels(steps) for sid in level]


def compile_stages(stages: list["SequenceStage"]) -> list["SequenceStepRef"]:
    """Turn stages into steps with explicit edges, in stage then declared order.

    A stage's entry steps depend on the previous stage's exit steps. In a serial
    stage those are its first and last step; every step of a parallel stage is both.
    """
    from api.schemas import SequenceStepRef

    compiled: list[SequenceStepRef] = []
    exits: list[str] = []
    for stage in stages:
        if stage.mode == "parallel":
            for step in stage.steps:
                compiled.append(SequenceStepRef(**step.model_dump(), depends_on=list(exits)))
            exits = [step.step_id for step in stage.steps]
        else:
            parents = exits
            for step in stage.steps:
                compiled.append(SequenceStepRef(**step.model_dump(), depends_on=list(parents)))
                parents = [step.step_id]
            exits = parents
    return compiled


def _as_stage(mode: str, steps: list) -> "SequenceStage":
    from api.schemas import SequenceStage, SequenceStageStep

    return SequenceStage(
        mode=mode,
        steps=[SequenceStageStep(**s.model_dump(exclude={"depends_on"})) for s in steps],
    )


def compile_flow_mode(steps: list["SequenceStepRef"], mode: str) -> list["SequenceStepRef"]:
    return compile_stages([_as_stage(mode, steps)]) if steps else []


def steps_from_input(body: "SequenceFlowInput") -> tuple[list["SequenceStepRef"], list[dict]]:
    """Resolve a request body to its steps, plus issues only compiled input can have."""
    if body.stages is not None:
        steps = compile_stages(body.stages)
    elif body.flow_mode:
        steps = compile_flow_mode(body.steps, body.flow_mode)
    else:
        return list(body.steps), []
    issues = [
        _issue(s.step_id, "trigger_rule", f"Step '{s.step_id}' has no parents, so 'all_failed' would never run it")
        for s in steps
        if not s.depends_on and s.trigger_rule == "all_failed"
    ]
    return steps, issues


def derive_flow(steps: list["SequenceStepRef"]) -> tuple[str, list["SequenceStage"] | None]:
    """Describe stored steps as a flow mode and stages, for display and editing.

    A graph only counts as stage-shaped if compiling the derived stages gives back
    the same edges. Invalid graphs return ("custom", None): reporting them is
    validate_steps' job.
    """
    by_id = {s.step_id: s for s in steps}
    if not steps or len(by_id) != len(steps):
        return "custom", None
    try:
        levels = _topological_levels(steps)
    except SequenceCycleError:
        return "custom", None

    groups: list[tuple[str, list]] = []
    for level in levels:
        members = [by_id[sid] for sid in level]
        if len(members) > 1:
            groups.append(("parallel", members))
        elif groups and groups[-1][0] == "serial":
            groups[-1][1].extend(members)
        else:
            groups.append(("serial", members))
    stages = [_as_stage(mode, members) for mode, members in groups]

    edges = {s.step_id: set(s.depends_on) for s in steps}
    if {s.step_id: set(s.depends_on) for s in compile_stages(stages)} != edges:
        return "custom", None
    return (stages[0].mode if len(stages) == 1 else "custom"), stages


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
