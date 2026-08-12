"""Trigger-rule and parent-condition evaluation."""
from __future__ import annotations

import pytest


class _Result:
    """Minimal stand-in for ReconciliationResult."""
    def __init__(self, status="PASSED", value=0, missing_t=0, missing_s=0, rows=100):
        self.status = status
        self.value_mismatch_count = value
        self.missing_in_target_count = missing_t
        self.missing_in_source_count = missing_s
        self.source_row_count = rows


def _outcome(status="PASSED", **kw):
    from api.services.sequence_conditions import ParentOutcome
    return ParentOutcome(status=status, result=_Result(status=status, **kw))


# --- evaluate_condition -----------------------------------------------------

def test_evaluate_condition_accepts_matching_status():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import evaluate_condition
    assert evaluate_condition(StepCondition(require_status=["PASSED"]), _Result()) is True


def test_evaluate_condition_rejects_other_status():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import evaluate_condition
    assert evaluate_condition(StepCondition(require_status=["PASSED"]), _Result("FAILED")) is False


def test_evaluate_condition_enforces_max_mismatch_count():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import evaluate_condition
    cond = StepCondition(require_status=["PASSED"], max_mismatch_count=2)
    assert evaluate_condition(cond, _Result(value=1, missing_t=1)) is True
    assert evaluate_condition(cond, _Result(value=2, missing_t=1)) is False


def test_evaluate_condition_enforces_row_bounds():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import evaluate_condition
    assert evaluate_condition(StepCondition(min_row_count=50), _Result(rows=10)) is False
    assert evaluate_condition(StepCondition(max_row_count=5), _Result(rows=10)) is False


# --- parent_satisfies -------------------------------------------------------

def test_parent_satisfies_defaults_to_passed_when_no_condition():
    from api.services.sequence_conditions import parent_satisfies
    assert parent_satisfies(None, _outcome("PASSED")) is True
    assert parent_satisfies(None, _outcome("FAILED")) is False


def test_parent_satisfies_treats_skipped_as_not_success():
    from api.services.sequence_conditions import parent_satisfies
    assert parent_satisfies(None, _outcome("SKIPPED")) is False


def test_parent_satisfies_with_no_result_falls_back_to_status():
    from api.schemas import StepCondition
    from api.services.sequence_conditions import ParentOutcome, parent_satisfies
    outcome = ParentOutcome(status="PASSED", result=None)
    assert parent_satisfies(StepCondition(require_status=["PASSED"], max_mismatch_count=0), outcome) is True


# --- trigger_fires ----------------------------------------------------------

@pytest.mark.parametrize("satisfied,expected", [
    ([], True), ([True], True), ([True, True], True),
    ([True, False], False), ([False], False),
])
def test_all_success(satisfied, expected):
    from api.services.sequence_conditions import trigger_fires
    assert trigger_fires("all_success", satisfied) is expected


@pytest.mark.parametrize("satisfied", [[], [True], [False], [True, False]])
def test_all_done_always_fires(satisfied):
    from api.services.sequence_conditions import trigger_fires
    assert trigger_fires("all_done", satisfied) is True


@pytest.mark.parametrize("satisfied,expected", [
    ([], True), ([True, False], True), ([False, False], False),
])
def test_any_success(satisfied, expected):
    from api.services.sequence_conditions import trigger_fires
    assert trigger_fires("any_success", satisfied) is expected


@pytest.mark.parametrize("satisfied,expected", [
    ([], False), ([False], True), ([False, False], True), ([True, False], False),
])
def test_all_failed(satisfied, expected):
    from api.services.sequence_conditions import trigger_fires
    assert trigger_fires("all_failed", satisfied) is expected
