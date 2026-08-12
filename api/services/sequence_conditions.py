"""Pure condition and trigger-rule evaluation for DAG execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from api.schemas import StepCondition

DEFAULT_REQUIRE_STATUS = ("PASSED",)


@dataclass(frozen=True)
class ParentOutcome:
    """What a finished step produced, as the coordinator remembers it.

    `status` is the job's own outcome (PASSED/FAILED/SLOW/ERROR/SKIPPED) and NOT
    the run_steps row status, which release_step overwrites with APPROVED etc.
    """
    status: str
    result: Any | None = None


def _status_of(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def evaluate_condition(condition: "StepCondition", result) -> bool:
    """Does `result` satisfy `condition`? Mirrors RunExecutor._check_condition."""
    status = _status_of(result.status)
    if condition.require_status and status not in condition.require_status:
        return False
    if condition.max_mismatch_count is not None:
        total = (
            result.value_mismatch_count
            + result.missing_in_target_count
            + result.missing_in_source_count
        )
        if total > condition.max_mismatch_count:
            return False
    if condition.min_row_count is not None and result.source_row_count < condition.min_row_count:
        return False
    if condition.max_row_count is not None and result.source_row_count > condition.max_row_count:
        return False
    if condition.max_value_mismatches is not None and result.value_mismatch_count > condition.max_value_mismatches:
        return False
    if condition.max_missing_in_target is not None and result.missing_in_target_count > condition.max_missing_in_target:
        return False
    if condition.max_missing_in_source is not None and result.missing_in_source_count > condition.max_missing_in_source:
        return False
    return True


def parent_satisfies(condition: "StepCondition | None", outcome: ParentOutcome) -> bool:
    """Does this parent meet the child's entry requirement?

    With no condition the requirement is simply that the parent PASSED. When the
    parent produced no result object (an ERROR, or a skipped hold) only the
    status is checked, since the numeric gates have nothing to read.
    """
    required = tuple(condition.require_status) if (condition and condition.require_status) else DEFAULT_REQUIRE_STATUS
    if outcome.status not in required:
        return False
    if condition is None or outcome.result is None:
        return True
    return evaluate_condition(condition, outcome.result)


def trigger_fires(rule: str, satisfied: list[bool]) -> bool:
    """Should a step run, given whether each parent satisfied its condition?

    A step with no parents always fires, except under `all_failed`, which needs
    at least one parent to have failed in order to mean anything.
    """
    if rule == "all_done":
        return True
    if rule == "any_success":
        return True if not satisfied else any(satisfied)
    if rule == "all_failed":
        return bool(satisfied) and not any(satisfied)
    return all(satisfied)   # all_success, the default
