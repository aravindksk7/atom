"""Compiling stages and flow modes to depends_on, and deriving them back."""
from __future__ import annotations

import pytest

from api.schemas import SequenceFlowInput, SequenceStage, SequenceStepRef
from api.services.sequence_validation import (
    compile_flow_mode,
    compile_stages,
    derive_flow,
    steps_from_input,
)


def _stage(mode, *ids):
    return SequenceStage(mode=mode, steps=[{"step_id": i, "job_name": "j"} for i in ids])


def _step(step_id, *parents):
    return SequenceStepRef(step_id=step_id, job_name="j", depends_on=list(parents))


def _edges(steps):
    return {s.step_id: s.depends_on for s in steps}


def test_serial_stage_is_a_chain():
    assert _edges(compile_stages([_stage("serial", "a", "b", "c")])) == {
        "a": [], "b": ["a"], "c": ["b"],
    }


def test_parallel_stage_has_no_edges_among_members():
    assert _edges(compile_stages([_stage("parallel", "a", "b")])) == {"a": [], "b": []}


@pytest.mark.parametrize("first,second,expected", [
    ("serial", "serial", {"a": [], "b": ["a"], "c": ["b"], "d": ["c"]}),
    ("serial", "parallel", {"a": [], "b": ["a"], "c": ["b"], "d": ["b"]}),
    ("parallel", "serial", {"a": [], "b": [], "c": ["a", "b"], "d": ["c"]}),
    ("parallel", "parallel", {"a": [], "b": [], "c": ["a", "b"], "d": ["a", "b"]}),
])
def test_stage_boundaries(first, second, expected):
    steps = compile_stages([_stage(first, "a", "b"), _stage(second, "c", "d")])
    assert _edges(steps) == expected


def test_hybrid_serial_parallel_serial():
    steps = compile_stages([
        _stage("serial", "a"), _stage("parallel", "b", "c"), _stage("serial", "d", "e"),
    ])
    assert [s.step_id for s in steps] == ["a", "b", "c", "d", "e"]
    assert _edges(steps) == {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"], "e": ["d"]}


def test_compile_keeps_step_fields():
    stage = SequenceStage(mode="serial", steps=[
        {"step_id": "a", "job_name": "j", "hold_after": True, "on_failure": "stop",
         "condition": {"require_status": ["PASSED", "SLOW"]}},
    ])
    step = compile_stages([stage])[0]
    assert step.hold_after is True
    assert step.on_failure == "stop"
    assert step.condition.require_status == ["PASSED", "SLOW"]


def test_compile_flow_mode():
    steps = [_step("a"), _step("b")]
    assert _edges(compile_flow_mode(steps, "serial")) == {"a": [], "b": ["a"]}
    assert _edges(compile_flow_mode(steps, "parallel")) == {"a": [], "b": []}
    assert compile_flow_mode([], "serial") == []


@pytest.mark.parametrize("stages,flow_mode", [
    ([("serial", "a")], "serial"),
    ([("serial", "a", "b", "c")], "serial"),
    ([("parallel", "a", "b")], "parallel"),
    ([("serial", "a"), ("parallel", "b", "c"), ("serial", "d", "e")], "custom"),
    ([("parallel", "a", "b"), ("parallel", "c", "d")], "custom"),
])
def test_compile_derive_round_trip(stages, flow_mode):
    steps = compile_stages([_stage(s[0], *s[1:]) for s in stages])
    derived_mode, derived = derive_flow(steps)
    assert derived_mode == flow_mode
    assert _edges(compile_stages(derived)) == _edges(steps)
    assert [(st.mode, [x.step_id for x in st.steps]) for st in derived] == [
        (s[0], list(s[1:])) for s in stages
    ]


def test_derive_drops_depends_on_from_stage_steps():
    _, stages = derive_flow(compile_stages([_stage("serial", "a", "b")]))
    assert "depends_on" not in stages[0].steps[1].model_dump()


@pytest.mark.parametrize("steps", [
    # Two independent chains: levels look like stages but the edges differ.
    [_step("a"), _step("b", "a"), _step("c"), _step("d", "c")],
    # A step skipping a level.
    [_step("a"), _step("b", "a"), _step("c", "a", "b")],
    # A join on only one group member.
    [_step("a"), _step("b"), _step("c", "a")],
])
def test_graph_that_is_not_stage_shaped_derives_custom_without_stages(steps):
    assert derive_flow(steps) == ("custom", None)


@pytest.mark.parametrize("steps", [
    [_step("a", "b"), _step("b", "a")],     # cycle
    [_step("a", "ghost")],                  # dangling parent
    [_step("a"), _step("a")],               # duplicate id
    [],
])
def test_derive_never_raises_on_invalid_graphs(steps):
    assert derive_flow(steps) == ("custom", None)


def test_steps_from_input_compiles_stages():
    body = SequenceFlowInput(stages=[_stage("serial", "a", "b")])
    steps, issues = steps_from_input(body)
    assert _edges(steps) == {"a": [], "b": ["a"]}
    assert issues == []


def test_steps_from_input_applies_flow_mode():
    body = SequenceFlowInput(flow_mode="serial", steps=[_step("a"), _step("b")])
    steps, _ = steps_from_input(body)
    assert _edges(steps) == {"a": [], "b": ["a"]}


def test_steps_from_input_leaves_raw_steps_alone():
    raw = [_step("a"), _step("b", "a")]
    steps, issues = steps_from_input(SequenceFlowInput(steps=raw))
    assert _edges(steps) == {"a": [], "b": ["a"]}
    assert issues == []


def test_root_step_cannot_use_all_failed_in_compiled_input():
    body = SequenceFlowInput(stages=[SequenceStage(mode="serial", steps=[
        {"step_id": "a", "job_name": "j", "trigger_rule": "all_failed"},
    ])])
    _, issues = steps_from_input(body)
    assert issues == [{
        "step_id": "a", "field": "trigger_rule",
        "message": "Step 'a' has no parents, so 'all_failed' would never run it",
    }]


def test_root_all_failed_is_not_flagged_for_raw_steps():
    raw = SequenceStepRef(step_id="a", job_name="j", trigger_rule="all_failed")
    assert steps_from_input(SequenceFlowInput(steps=[raw]))[1] == []


def _job_step(step_id, job, *parents):
    return SequenceStepRef(step_id=step_id, job_name=job, depends_on=list(parents))


def test_job_dependency_between_siblings_is_a_violation():
    from api.services.sequence_validation import job_dependency_violations

    steps = [_job_step("a", "loader"), _job_step("b", "recon")]
    found = job_dependency_violations(steps, {"recon": ["loader"]})
    assert [(step.step_id, dep) for step, dep in found] == [("b", "loader")]


def test_job_dependency_on_a_real_ancestor_is_fine():
    from api.services.sequence_validation import job_dependency_violations

    steps = [_job_step("a", "loader"), _job_step("m", "mid", "a"), _job_step("b", "recon", "m")]
    assert job_dependency_violations(steps, {"recon": ["loader"]}) == []


def test_job_dependency_outside_the_sequence_is_informational():
    from api.services.sequence_validation import job_dependency_violations

    assert job_dependency_violations([_job_step("b", "recon")], {"recon": ["loader"]}) == []


def test_repeated_job_needs_an_earlier_run_of_its_dependency():
    from api.services.sequence_validation import job_dependency_violations

    # [A, B, A] is fine: B has an earlier A. [B, A, B] is not: the first B has none.
    ok = [_job_step("a1", "A"), _job_step("b", "B", "a1"), _job_step("a2", "A", "b")]
    assert job_dependency_violations(ok, {"B": ["A"]}) == []
    bad = [_job_step("b1", "B"), _job_step("a", "A", "b1"), _job_step("b2", "B", "a")]
    assert [s.step_id for s, _ in job_dependency_violations(bad, {"B": ["A"]})] == ["b1"]


def test_validate_steps_reports_job_dependency_issue():
    from api.services.sequence_validation import validate_steps

    steps = [_job_step("a", "loader"), _job_step("b", "recon")]
    errors = validate_steps(steps, ["loader", "recon"], {"recon": ["loader"]})
    assert errors == [{
        "step_id": "b", "field": "depends_on",
        "message": "Job 'recon' depends on 'loader' but step 'b' does not run after it. "
                   "Add the dependency so 'loader' runs first.",
    }]


def test_validate_steps_without_job_dependencies_is_unchanged():
    from api.services.sequence_validation import validate_steps

    steps = [_job_step("a", "loader"), _job_step("b", "recon")]
    assert validate_steps(steps, ["loader", "recon"]) == []
