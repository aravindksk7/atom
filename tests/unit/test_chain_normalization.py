"""A plain sequence becomes an explicit chain DAG."""
from __future__ import annotations

from api.schemas import SequenceStep, SequenceStepRef, StepCondition
from api.services.run_executor import normalize_to_dag


def test_strings_become_a_chain():
    steps = normalize_to_dag(["a", "b", "c"])
    assert [s.step_id for s in steps] == ["step_0", "step_1", "step_2"]
    assert [s.depends_on for s in steps] == [[], ["step_0"], ["step_1"]]
    assert [s.job_name for s in steps] == ["a", "b", "c"]


def test_dicts_and_models_normalize_too():
    steps = normalize_to_dag([{"job_name": "a"}, SequenceStep(job_name="b")])
    assert [s.job_name for s in steps] == ["a", "b"]
    assert steps[1].depends_on == ["step_0"]


def test_hold_condition_and_wait_are_preserved():
    steps = normalize_to_dag([
        SequenceStep(job_name="a"),
        SequenceStep(job_name="b", hold_after=True, wait_seconds=7,
                     condition=StepCondition(require_status=["PASSED"], max_mismatch_count=2)),
    ])
    assert steps[1].hold_after is True
    assert steps[1].wait_seconds == 7
    assert steps[1].condition.max_mismatch_count == 2


def test_step_refs_pass_through_untouched():
    given = [
        SequenceStepRef(step_id="root", job_name="a"),
        SequenceStepRef(step_id="left", job_name="b", depends_on=["root"]),
        SequenceStepRef(step_id="right", job_name="c", depends_on=["root"]),
    ]
    steps = normalize_to_dag(given)
    assert [s.step_id for s in steps] == ["root", "left", "right"]
    assert steps[2].depends_on == ["root"]


def test_defaults_are_all_success_and_skip_downstream():
    step = normalize_to_dag(["a"])[0]
    assert step.trigger_rule == "all_done"
    assert step.on_failure == "skip_downstream"
    assert step.max_retries is None
