from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AssertionConfig:
    operator: str
    value: Any | None = None
    min_value: float | None = None
    max_value: float | None = None
    tolerance: float | str | None = None


@dataclass(frozen=True)
class ComparisonOutcome:
    passed: bool
    expected_display: str
    reason: str | None = None


def normalise_assertion(raw: Any) -> AssertionConfig:
    if not isinstance(raw, dict):
        return AssertionConfig(operator="==", value=raw)

    min_val = raw.get("min") if "min" in raw else raw.get("min_value")
    max_val = raw.get("max") if "max" in raw else raw.get("max_value")
    return AssertionConfig(
        operator=raw.get("operator", "=="),
        value=raw.get("value"),
        min_value=min_val,
        max_value=max_val,
        tolerance=raw.get("tolerance"),
    )


def validate_assertion(path: str, raw: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return errors

    op_val = raw.get("operator") if "operator" in raw else "=="
    if op_val not in ("==", "!=", ">", ">=", "<", "<=", "between"):
        return [f"unsupported operator: {op_val}"]

    op = op_val
    if op == "between":
        min_val = raw.get("min") if "min" in raw else raw.get("min_value")
        max_val = raw.get("max") if "max" in raw else raw.get("max_value")
        if min_val is None or max_val is None:
            return ["between requires min and max"]
        try:
            if isinstance(min_val, bool) or isinstance(max_val, bool):
                return ["between requires numeric min and max"]
            f_min = float(min_val)
            f_max = float(max_val)
            if f_min > f_max:
                return ["between requires min <= max"]
        except (ValueError, TypeError):
            return ["between requires numeric min and max"]
    else:
        if raw.get("value") is None:
            return [f"{op} requires value"]

    if raw.get("tolerance") is not None:
        if op not in ("==", "!="):
            errors.append("tolerance is only valid with == or !=")
        else:
            tol = raw["tolerance"]
            if isinstance(tol, str) and tol.endswith("%"):
                try:
                    float(tol[:-1])
                except ValueError:
                    errors.append("invalid percentage tolerance format")
            elif not (isinstance(tol, (int, float)) and not isinstance(tol, bool)):
                errors.append("tolerance must be a number or a percentage string")

    return errors


def _is_numeric(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    return isinstance(val, (int, float))


def evaluate_assertion(assertion: AssertionConfig, actual: Any) -> ComparisonOutcome:
    op = assertion.operator
    val = assertion.value

    disp_tol = f" ±{assertion.tolerance}" if assertion.tolerance is not None else ""
    if op == "between":
        disp = f"between {assertion.min_value} and {assertion.max_value}"
    else:
        disp = f"{op} {val}{disp_tol}"

    if actual is None:
        return ComparisonOutcome(passed=False, expected_display=disp, reason="metric is null")
    if type(actual) is object or type(actual).__name__ == "object" or str(actual) == "<missing>":
        return ComparisonOutcome(passed=False, expected_display=disp, reason="metric missing")

    if op in (">", ">=", "<", "<=", "between"):
        if not _is_numeric(actual):
            return ComparisonOutcome(passed=False, expected_display=disp, reason=f"actual value '{actual}' is not numeric")

    try:
        passed = False
        if op == "between":
            passed = float(assertion.min_value) <= float(actual) <= float(assertion.max_value)
        elif assertion.tolerance is not None:
            if not _is_numeric(actual) or not _is_numeric(val):
                return ComparisonOutcome(
                    passed=False,
                    expected_display=disp,
                    reason=f"actual value '{actual}' is not numeric" if not _is_numeric(actual) else f"evaluation error: non-numeric expected value '{val}'",
                )
            actual_f = float(actual)
            val_f = float(val)
            tol = assertion.tolerance
            if isinstance(tol, str) and tol.endswith("%"):
                margin = abs(val_f) * (float(tol[:-1]) / 100.0)
            else:
                margin = float(tol)
            diff = abs(actual_f - val_f)
            if op == "==":
                passed = diff <= margin
            elif op == "!=":
                passed = diff > margin
        else:
            if op == "==":
                passed = actual == val
            elif op == "!=":
                passed = actual != val
            elif op == ">":
                passed = float(actual) > float(val)
            elif op == ">=":
                passed = float(actual) >= float(val)
            elif op == "<":
                passed = float(actual) < float(val)
            elif op == "<=":
                passed = float(actual) <= float(val)

        return ComparisonOutcome(passed=passed, expected_display=disp)
    except Exception as e:
        return ComparisonOutcome(passed=False, expected_display=disp, reason=f"evaluation error: {e}")
