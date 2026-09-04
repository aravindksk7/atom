"""Tests for assertion comparators module."""
from dataclasses import is_dataclass
import pytest
from etl_framework.assertions.comparators import (
    AssertionConfig,
    ComparisonOutcome,
    normalise_assertion,
    validate_assertion,
    evaluate_assertion,
)


def test_assertion_config_dataclass():
    cfg = AssertionConfig(operator="==", value=10)
    assert is_dataclass(cfg)
    assert cfg.operator == "=="
    assert cfg.value == 10
    assert cfg.min_value is None
    assert cfg.max_value is None
    assert cfg.tolerance is None


def test_comparison_outcome_dataclass():
    outcome = ComparisonOutcome(passed=True, expected_display="== 10")
    assert is_dataclass(outcome)
    assert outcome.passed is True
    assert outcome.expected_display == "== 10"
    assert outcome.reason is None


def test_normalise_legacy_scalar():
    assert normalise_assertion(100) == AssertionConfig(operator="==", value=100)
    assert normalise_assertion(100.5) == AssertionConfig(operator="==", value=100.5)
    assert normalise_assertion("passed") == AssertionConfig(operator="==", value="passed")
    assert normalise_assertion(True) == AssertionConfig(operator="==", value=True)


def test_normalise_dict_forms():
    assert normalise_assertion({"operator": "between", "min": 10, "max": 20}) == AssertionConfig(
        operator="between", min_value=10, max_value=20
    )
    assert normalise_assertion({"operator": "==", "value": 5, "tolerance": "10%"}) == AssertionConfig(
        operator="==", value=5, tolerance="10%"
    )
    assert normalise_assertion({"value": 42}) == AssertionConfig(operator="==", value=42)
    assert normalise_assertion({"operator": ">=", "value": 10}) == AssertionConfig(operator=">=", value=10)
    assert normalise_assertion({"operator": "between", "min_value": 5, "max_value": 15}) == AssertionConfig(
        operator="between", min_value=5, max_value=15
    )


def test_validate_assertion_valid_cases():
    assert validate_assertion("path", 100) == []
    assert validate_assertion("path", "hello") == []
    assert validate_assertion("path", {"operator": "==", "value": 10}) == []
    assert validate_assertion("path", {"operator": "!=", "value": 0}) == []
    assert validate_assertion("path", {"operator": ">", "value": 5}) == []
    assert validate_assertion("path", {"operator": ">=", "value": 5}) == []
    assert validate_assertion("path", {"operator": "<", "value": 5}) == []
    assert validate_assertion("path", {"operator": "<=", "value": 5}) == []
    assert validate_assertion("path", {"operator": "between", "min": 5, "max": 10}) == []
    assert validate_assertion("path", {"operator": "==", "value": 10, "tolerance": 2}) == []
    assert validate_assertion("path", {"operator": "==", "value": 10, "tolerance": "5%"}) == []
    assert validate_assertion("path", {"operator": "!=", "value": 10, "tolerance": 0.5}) == []


def test_validate_assertion_invalid_operator():
    assert validate_assertion("path", {"operator": "invalid"}) == ["unsupported operator: invalid"]
    assert validate_assertion("path", {"operator": "LIKE"}) == ["unsupported operator: LIKE"]


def test_validate_assertion_between_constraints():
    assert validate_assertion("path", {"operator": "between"}) == ["between requires min and max"]
    assert validate_assertion("path", {"operator": "between", "min": 10}) == ["between requires min and max"]
    assert validate_assertion("path", {"operator": "between", "max": 10}) == ["between requires min and max"]
    assert validate_assertion("path", {"operator": "between", "min": 20, "max": 10}) == ["between requires min <= max"]
    assert validate_assertion("path", {"operator": "between", "min": "abc", "max": 10}) == ["between requires numeric min and max"]
    assert validate_assertion("path", {"operator": "between", "min": 10, "max": "xyz"}) == ["between requires numeric min and max"]


def test_validate_assertion_missing_value():
    assert validate_assertion("path", {"operator": "=="}) == ["== requires value"]
    assert validate_assertion("path", {"operator": "!="}) == ["!= requires value"]
    assert validate_assertion("path", {"operator": ">"}) == ["> requires value"]
    assert validate_assertion("path", {"operator": ">="}) == [">= requires value"]
    assert validate_assertion("path", {"operator": "<"}) == ["< requires value"]
    assert validate_assertion("path", {"operator": "<="}) == ["<= requires value"]


def test_validate_assertion_tolerance_constraints():
    assert validate_assertion("path", {"operator": ">", "value": 10, "tolerance": 1}) == ["tolerance is only valid with == or !="]
    assert validate_assertion("path", {"operator": "<=", "value": 10, "tolerance": "5%"}) == ["tolerance is only valid with == or !="]
    assert validate_assertion("path", {"operator": "between", "min": 1, "max": 5, "tolerance": 1}) == ["tolerance is only valid with == or !="]
    assert validate_assertion("path", {"operator": "==", "value": 10, "tolerance": "invalid%"}) == ["invalid percentage tolerance format"]
    assert validate_assertion("path", {"operator": "==", "value": 10, "tolerance": "bad"}) == ["tolerance must be a number or a percentage string"]
    assert validate_assertion("path", {"operator": "==", "value": 10, "tolerance": [1, 2]}) == ["tolerance must be a number or a percentage string"]


def test_evaluate_scalar_equality():
    outcome = evaluate_assertion(normalise_assertion(100), 100)
    assert outcome.passed is True
    assert outcome.expected_display == "== 100"

    outcome_fail = evaluate_assertion(normalise_assertion(100), 99)
    assert outcome_fail.passed is False
    assert outcome_fail.expected_display == "== 100"

    # String equality
    assert evaluate_assertion(AssertionConfig("==", value="SUCCESS"), "SUCCESS").passed is True
    assert evaluate_assertion(AssertionConfig("==", value="SUCCESS"), "FAILED").passed is False


def test_evaluate_inequality():
    assert evaluate_assertion(AssertionConfig("!=", value=0), 5).passed is True
    assert evaluate_assertion(AssertionConfig("!=", value=0), 0).passed is False
    assert evaluate_assertion(AssertionConfig("!=", value="A"), "B").passed is True
    assert evaluate_assertion(AssertionConfig("!=", value="A"), "A").passed is False


def test_evaluate_ordering_operators():
    # >
    assert evaluate_assertion(AssertionConfig(">", value=10), 15).passed is True
    assert evaluate_assertion(AssertionConfig(">", value=10), 10).passed is False
    assert evaluate_assertion(AssertionConfig(">", value=10), 5).passed is False

    # >=
    assert evaluate_assertion(AssertionConfig(">=", value=10), 15).passed is True
    assert evaluate_assertion(AssertionConfig(">=", value=10), 10).passed is True
    assert evaluate_assertion(AssertionConfig(">=", value=10), 9).passed is False

    # <
    assert evaluate_assertion(AssertionConfig("<", value=10), 5).passed is True
    assert evaluate_assertion(AssertionConfig("<", value=10), 10).passed is False
    assert evaluate_assertion(AssertionConfig("<", value=10), 15).passed is False

    # <=
    assert evaluate_assertion(AssertionConfig("<=", value=10), 5).passed is True
    assert evaluate_assertion(AssertionConfig("<=", value=10), 10).passed is True
    assert evaluate_assertion(AssertionConfig("<=", value=10), 15).passed is False


def test_evaluate_between():
    cfg = AssertionConfig("between", min_value=10.0, max_value=20.0)
    assert evaluate_assertion(cfg, 15.0).passed is True
    assert evaluate_assertion(cfg, 10.0).passed is True
    assert evaluate_assertion(cfg, 20.0).passed is True
    assert evaluate_assertion(cfg, 9.9).passed is False
    assert evaluate_assertion(cfg, 20.1).passed is False
    assert evaluate_assertion(cfg, 15.0).expected_display == "between 10.0 and 20.0"


def test_evaluate_tolerance_numeric():
    cfg_eq = AssertionConfig("==", value=100.0, tolerance=5.0)
    assert evaluate_assertion(cfg_eq, 100.0).passed is True
    assert evaluate_assertion(cfg_eq, 95.0).passed is True
    assert evaluate_assertion(cfg_eq, 105.0).passed is True
    assert evaluate_assertion(cfg_eq, 94.9).passed is False
    assert evaluate_assertion(cfg_eq, 105.1).passed is False
    assert evaluate_assertion(cfg_eq, 100.0).expected_display == "== 100.0 ±5.0"

    cfg_ne = AssertionConfig("!=", value=100.0, tolerance=5.0)
    assert evaluate_assertion(cfg_ne, 106.0).passed is True
    assert evaluate_assertion(cfg_ne, 94.0).passed is True
    assert evaluate_assertion(cfg_ne, 103.0).passed is False
    assert evaluate_assertion(cfg_ne, 97.0).passed is False


def test_evaluate_tolerance_percentage():
    cfg_pct = AssertionConfig("==", value=200.0, tolerance="10%")
    assert evaluate_assertion(cfg_pct, 200.0).passed is True
    assert evaluate_assertion(cfg_pct, 220.0).passed is True
    assert evaluate_assertion(cfg_pct, 180.0).passed is True
    assert evaluate_assertion(cfg_pct, 220.1).passed is False
    assert evaluate_assertion(cfg_pct, 179.9).passed is False
    assert evaluate_assertion(cfg_pct, 215.0).passed is True
    assert evaluate_assertion(cfg_pct, 200.0).expected_display == "== 200.0 ±10%"

    cfg_ne_pct = AssertionConfig("!=", value=200.0, tolerance="10%")
    assert evaluate_assertion(cfg_ne_pct, 230.0).passed is True
    assert evaluate_assertion(cfg_ne_pct, 170.0).passed is True
    assert evaluate_assertion(cfg_ne_pct, 190.0).passed is False


def test_evaluate_missing_or_null():
    from api.services.run_executor import _MISSING_METRIC

    # None metric
    outcome_null = evaluate_assertion(AssertionConfig(">=", value=10), None)
    assert outcome_null.passed is False
    assert outcome_null.reason == "metric is null"

    outcome_null_eq = evaluate_assertion(AssertionConfig("==", value=10), None)
    assert outcome_null_eq.passed is False
    assert outcome_null_eq.reason == "metric is null"

    # Missing metric sentinel
    outcome_missing = evaluate_assertion(AssertionConfig(">=", value=10), _MISSING_METRIC)
    assert outcome_missing.passed is False
    assert outcome_missing.reason == "metric missing"

    # Plain object or string <missing>
    assert evaluate_assertion(AssertionConfig("==", value=10), object()).reason == "metric missing"
    assert evaluate_assertion(AssertionConfig("==", value=10), "<missing>").reason == "metric missing"


def test_evaluate_non_numeric_actual_for_ordering():
    outcome_str = evaluate_assertion(AssertionConfig(">=", value=10), "abc")
    assert outcome_str.passed is False
    assert outcome_str.reason == "actual value 'abc' is not numeric"

    outcome_bool = evaluate_assertion(AssertionConfig("<", value=5), True)
    assert outcome_bool.passed is False
    assert outcome_bool.reason == "actual value 'True' is not numeric"

    outcome_between = evaluate_assertion(AssertionConfig("between", min_value=1, max_value=10), "xyz")
    assert outcome_between.passed is False
    assert outcome_between.reason == "actual value 'xyz' is not numeric"


def test_evaluate_exception_handling():
    # If val is non-numeric when doing ordering
    cfg = AssertionConfig(">", value="not_a_number")
    outcome = evaluate_assertion(cfg, 10)
    assert outcome.passed is False
    assert "evaluation error:" in (outcome.reason or "")
