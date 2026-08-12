"""Pure DAG validation for saved execution sequences."""
from __future__ import annotations

import pytest


def _step(step_id, job_name="orders_recon", depends_on=None, **kw):
    from api.schemas import SequenceStepRef
    return SequenceStepRef(
        step_id=step_id, job_name=job_name, depends_on=depends_on or [], **kw
    )


JOBS = {"orders_recon", "customers_recon", "load_orders"}


def test_valid_chain_reports_no_errors():
    from api.services.sequence_validation import validate_steps
    steps = [_step("a"), _step("b", depends_on=["a"])]
    assert validate_steps(steps, JOBS) == []


def test_empty_sequence_is_rejected():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([], JOBS)
    assert len(errors) == 1
    assert errors[0]["field"] == "steps"


def test_duplicate_step_id_is_reported():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([_step("a"), _step("a")], JOBS)
    assert any(e["field"] == "step_id" and "Duplicate" in e["message"] for e in errors)


def test_unknown_job_name_is_reported():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([_step("a", job_name="nope")], JOBS)
    assert any(e["field"] == "job_name" and e["step_id"] == "a" for e in errors)


def test_unknown_dependency_is_reported():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([_step("a", depends_on=["ghost"])], JOBS)
    assert any(e["field"] == "depends_on" and "ghost" in e["message"] for e in errors)


def test_self_dependency_is_reported():
    from api.services.sequence_validation import validate_steps
    errors = validate_steps([_step("a", depends_on=["a"])], JOBS)
    assert any("itself" in e["message"] for e in errors)


def test_cycle_is_reported():
    from api.services.sequence_validation import validate_steps
    steps = [_step("a", depends_on=["b"]), _step("b", depends_on=["a"])]
    errors = validate_steps(steps, JOBS)
    assert any(e["field"] == "depends_on" and "cycle" in e["message"].lower() for e in errors)


def test_same_job_may_appear_under_two_step_ids():
    from api.services.sequence_validation import validate_steps
    steps = [
        _step("recon_before", job_name="orders_recon"),
        _step("load", job_name="load_orders", depends_on=["recon_before"]),
        _step("recon_after", job_name="orders_recon", depends_on=["load"]),
    ]
    assert validate_steps(steps, JOBS) == []


def test_topological_order_is_declaration_stable():
    from api.services.sequence_validation import topological_order
    # 'b' and 'c' are both ready at level 2; declared order must decide.
    steps = [_step("a"), _step("c", depends_on=["a"]), _step("b", depends_on=["a"])]
    assert topological_order(steps) == ["a", "c", "b"]


def test_topological_order_places_parents_first():
    from api.services.sequence_validation import topological_order
    steps = [_step("late", depends_on=["early"]), _step("early")]
    assert topological_order(steps) == ["early", "late"]


def test_topological_order_raises_on_cycle():
    from api.services.sequence_validation import SequenceCycleError, topological_order
    steps = [_step("a", depends_on=["b"]), _step("b", depends_on=["a"])]
    with pytest.raises(SequenceCycleError) as exc:
        topological_order(steps)
    assert set(exc.value.step_ids) == {"a", "b"}


def test_trigger_rules_are_allowed_from_phase2():
    from api.services.sequence_validation import phase1_unsupported
    for rule in ("all_success", "all_done", "any_success", "all_failed"):
        assert phase1_unsupported([_step("a", trigger_rule=rule)], None) == []


def test_phase1_rejects_retry_and_on_failure():
    from api.services.sequence_validation import phase1_unsupported
    errors = phase1_unsupported(
        [_step("a", max_retries=2, on_failure="stop")], None
    )
    assert {e["field"] for e in errors} == {"max_retries", "on_failure"}


def test_phase1_rejects_preconditions():
    from api.schemas import SequencePrecondition
    from api.services.sequence_validation import phase1_unsupported
    errors = phase1_unsupported([_step("a")], SequencePrecondition(weekdays=[0]))
    assert any(e["field"] == "preconditions" for e in errors)


def test_phase1_allows_defaults():
    from api.services.sequence_validation import phase1_unsupported
    assert phase1_unsupported([_step("a", hold_after=True, wait_seconds=5)], None) == []
