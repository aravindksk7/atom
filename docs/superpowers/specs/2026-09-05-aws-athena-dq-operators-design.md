# AWS Athena DQ Assertion Operators — Design

**Date:** 2026-09-05
**Status:** Approved (design), pending implementation plan
**Author:** Principal data engineer (via brainstorming)
**Depends on:** `docs/superpowers/specs/2026-07-26-aws-athena-query-runner-design.md`

## Context

The AWS Athena query runner enables ad-hoc execution and tracked `aws_athena_query` jobs that evaluate data quality metrics extracted from query result sets (`row_count`, `null_counts.<col>`, `distinct_counts.<col>`, `numeric.<col>.min`, `numeric.<col>.max`, `numeric.<col>.avg`).

Currently, `metric_assertions` only supports exact scalar equality comparisons (`{"null_counts.id": 0}`). This limitation makes it impossible to express common DQ rules such as:
- Null counts below a threshold (`<= 5`)
- Average transaction amount within an expected range (`between 100 and 500`)
- Row count greater than minimum threshold (`> 1000`)
- Metric approximate equality within absolute or relative tolerance (`== 99.5 ±0.5` or `== 1000 ±5%`)

This specification defines rich comparison operators for Athena metric assertions, including schema validation, execution semantics, frontend assertion builder UI, and test coverage, while maintaining full backward compatibility with existing scalar assertion jobs.

## Goals

- Support rich comparison operators (`==`, `!=`, `>`, `>=`, `<`, `<=`, `between`) for Athena metric assertions.
- Support absolute numeric tolerance and relative percentage tolerance (`"5%"`) on equality assertions (`==`, `!=`).
- Preserve backward compatibility with existing scalar metric assertion jobs (`{"row_count": 100}` maps to `operator: "=="`, `value: 100`).
- Create an isolated, reusable comparator module in `etl_framework/assertions/comparators.py`.
- Validate assertion structures at job save time in `etl_framework/runner/job_validation.py` with structured `ValidationIssue` paths.
- Integrate assertion evaluation into `api/services/run_executor.py` (`_athena_mismatches`), formatting human-readable mismatch records without runtime crashes.
- Enhance the Athena UI tab (`frontend/partials/tab-aws.html`, `frontend/features/aws.js`) with an interactive repeatable assertion row builder.
- Add comprehensive unit tests, executor integration tests, and Playwright e2e test coverage.

## Non-Goals

- Refactoring or modifying `etl_framework/reconciliation/dq_engine.py` (kept separate to prevent regression risk).
- Adding complex SQL mini-languages, regex DSLs, or free-form expressions.
- Extending assertion operators to other non-Athena job types in this phase.
- Modifying Athena query execution, polling, or AWS client runtime logic.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Schema format** | Polymorphic dict: scalar value or `{operator, value \| min, max, tolerance}` | Zero-migration backward compatibility; clean JSON schema; predictable per-path lookup. |
| **Operator set** | `==`, `!=`, `>`, `>=`, `<`, `<=`, `between`, with optional tolerance on `==`/`!=` | Covers standard data quality bounds, thresholds, ranges, and floating-point/drift tolerances. |
| **Module location** | Pure module `etl_framework/assertions/comparators.py` | Clean separation of concerns; no external dependencies; independently testable. |
| **Validation timing** | Job save/update in `job_validation.py` | Catches malformed operators or invalid tolerance syntax early before execution. |
| **Runtime safety** | Graceful mismatch generation on missing/non-numeric actuals | Queries execute and report structured mismatch records rather than unhandled python exceptions. |
| **UI interaction** | Dynamic repeatable assertion list in Athena panel | Allows users to visually configure metric path, operator, value/bounds, and tolerance. |

## Assertion Specification & Schema

### Supported Operators

| Operator | Allowed Operands | Description | Example |
|---|---|---|---|
| `==` | `value` (any scalar), optional `tolerance` (numeric or `"N%"`) | Equal to value (with optional tolerance) | `{"operator": "==", "value": 100, "tolerance": "5%"}` |
| `!=` | `value` (any scalar), optional `tolerance` (numeric or `"N%"`) | Not equal to value (outside tolerance) | `{"operator": "!=", "value": 0}` |
| `>` | `value` (numeric) | Strictly greater than value | `{"operator": ">", "value": 0}` |
| `>=` | `value` (numeric) | Greater than or equal to value | `{"operator": ">=", "value": 10}` |
| `<` | `value` (numeric) | Strictly less than value | `{"operator": "<", "value": 100}` |
| `<=` | `value` (numeric) | Less than or equal to value | `{"operator": "<=", "value": 0}` |
| `between` | `min` (numeric), `max` (numeric) | Inclusive range `min <= actual <= max` | `{"operator": "between", "min": 10, "max": 50}` |

### Tolerance Semantics

- **Absolute tolerance**: Numeric value `tol >= 0`. Equality passes when `|actual - value| <= tol`.
- **Percentage tolerance**: String `"N%"` where `N >= 0`. Calculated as `tol = |value| * (N / 100)`. Equality passes when `|actual - value| <= tol`.
- Tolerance is only valid on `==` and `!=` operators. Specifying `tolerance` on `>`, `>=`, `<`, `<=`, or `between` produces a validation error.

### Valid Assertion Formats

```jsonc
{
  "metric_assertions": {
    // 1. Legacy scalar form (treated as operator: "==")
    "row_count": 100,

    // 2. Threshold comparisons
    "null_counts.email": { "operator": "<=", "value": 0 },
    "distinct_counts.region": { "operator": ">=", "value": 5 },

    // 3. Range comparison
    "numeric.amount.avg": { "operator": "between", "min": 100.0, "max": 250.0 },

    // 4. Equality with absolute tolerance
    "numeric.temperature.max": { "operator": "==", "value": 98.6, "tolerance": 0.5 },

    // 5. Equality with percentage tolerance
    "numeric.latency.avg": { "operator": "==", "value": 200, "tolerance": "10%" }
  }
}
```

## Component Architecture

### 1. Comparator Module (`etl_framework/assertions/comparators.py`)

Defines the core data structures and pure evaluation logic:

```python
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

def normalise_assertion(raw: Any) -> AssertionConfig: ...
def validate_assertion(path: str, raw: Any) -> list[str]: ...
def evaluate_assertion(assertion: AssertionConfig, actual: Any) -> ComparisonOutcome: ...
```

#### Evaluation Rules
1. If `actual` is `_MISSING_METRIC` or `None` (for ordering operators), outcome is `passed=False` with reason `"metric missing"` or `"metric is null"`.
2. For `>`, `>=`, `<`, `<=`, `between`, both `actual` and targets must be numeric (`int` or `float`). If `actual` cannot be parsed as numeric, outcome is `passed=False` with reason `"actual value '<actual>' is not numeric"`.
3. For `between`, passes if `min_value <= actual <= max_value`. If `min_value > max_value`, validation rejects at save time.
4. For `==` / `!=` without tolerance, performs standard Python equality (`actual == value` or `actual != value`).
5. For `==` / `!=` with tolerance, computes numeric difference and checks against computed delta.

### 2. Job Validation (`etl_framework/runner/job_validation.py`)

Extend `_validate_aws_athena_query(params, issues)`:
- Validate `params["metric_assertions"]` is a dict.
- For each `path, raw_assertion` in `metric_assertions.items()`:
  - Call `validate_assertion(path, raw_assertion)`.
  - Append any validation errors as `ValidationIssue(f"params.metric_assertions.{path}", error_message)`.

### 3. Run Executor (`api/services/run_executor.py`)

Update `_athena_mismatches`:
- For each `path, raw_assertion` in `params.get("metric_assertions", {}).items()`:
  - Retrieve `actual = self._metric_path(metrics, str(path))`.
  - Normalise assertion via `normalise_assertion(raw_assertion)`.
  - Evaluate outcome via `evaluate_assertion(assertion, actual)`.
  - If `not outcome.passed`:
    - Format `mismatch_actual = "<missing>" if actual is _MISSING_METRIC else actual`.
    - Append `MismatchRecord(mismatch_type="athena_metric_mismatch", expected=outcome.expected_display, actual=mismatch_actual, ...)`.

### 4. Frontend Athena UI (`tab-aws.html` & `aws.js`)

#### Template (`frontend/partials/tab-aws.html`)
Replace fixed scalar assertion inputs with an assertion builder section:
- Dynamic table / row list bound to `awsAthenaMetricAssertions: [{ path: '', operator: '==', value: '', min: '', max: '', tolerance: '' }]`.
- Add Assertion button (`@click="awsAthenaAddAssertion()"`).
- Remove Assertion button per row (`@click="awsAthenaRemoveAssertion(index)"`).
- Operator dropdown: `==`, `!=`, `>`, `>=`, `<`, `<=`, `between`.
- Responsive inputs based on chosen operator:
  - When `between`: render Min and Max numeric inputs.
  - Otherwise: render Value input.
  - When `==` or `!=`: render optional Tolerance input.

#### Controller (`frontend/features/aws.js`)
- Initialize `awsAthenaMetricAssertions: []` in Alpine component state.
- Helper methods:
  - `awsAthenaAddAssertion()`: Appends `{ path: '', operator: '==', value: '', min: '', max: '', tolerance: '' }`.
  - `awsAthenaRemoveAssertion(index)`: Splices item from array.
- Serialization in `_awsAthenaJobParams()`:
  - Convert `awsAthenaMetricAssertions` into `metric_assertions` dictionary.
  - Convert numbers and tolerances to appropriate types.
  - Emit clean JSON structures matching backend expectations.
- Rebuild `frontend/index.html` via `npm run build:html`.

## Error Handling & Edge Cases

| Scenario | Behavior |
|---|---|
| Malformed assertion object at job save (e.g. `operator: "invalid"`) | Validation fails with 400 and `params.metric_assertions.<path>` issue. |
| `between` with `min > max` | Validation fails with descriptive error. |
| Tolerance on invalid operator (e.g. `> 10` with `tolerance: 2`) | Validation fails with descriptive error. |
| Metric path missing from query DQ metrics at runtime | Generates mismatch record with `actual="<missing>"` and `expected="<operator> <val>"`. |
| Non-numeric metric value for numeric operator (`>`, `<`) | Generates mismatch record with `reason="actual value is not numeric"` without throwing exception. |
| Terminal query status `FAILED` or `CANCELLED` | Handled status-only as today (skipped metric assertions if matching `expected_status`). |

## Test Plan

### 1. Unit Tests (`tests/unit/test_assertion_comparators.py`)
- Test `normalise_assertion` with legacy scalars (int, float, str, bool) and structured dicts.
- Test `validate_assertion` for all valid and invalid operand combinations.
- Test `evaluate_assertion` across all 7 operators (`==`, `!=`, `>`, `>=`, `<`, `<=`, `between`).
- Test absolute numeric tolerance and percentage tolerance calculation and boundary conditions.
- Test edge cases: `_MISSING_METRIC`, `None`, non-numeric strings, zero division.

### 2. Job Validation Tests (`tests/unit/test_job_validation.py`)
- Test valid Athena jobs containing rich operator metric assertions.
- Test validation failures on invalid operators, missing required keys (`min`/`max` for `between`), invalid tolerance formats.

### 3. Run Executor Tests (`tests/unit/test_run_executor_athena.py`)
- Verify execution of Athena query jobs with various operator assertions.
- Verify backward compatibility for scalar metric assertions.
- Verify mismatch record formatting and summary calculations.

### 4. E2E Playwright Tests (`tests/e2e/20-aws-athena-tab.spec.ts`)
- Interact with Athena tab in browser UI.
- Add an assertion row with operator (e.g. `between` or `<=`), fill in inputs.
- Click "Create Athena Query Job" and assert mocked POST `/api/jobs` payload structure contains `metric_assertions`.

## Rollout Sequence

1. Implement `etl_framework/assertions/comparators.py` and unit tests `tests/unit/test_assertion_comparators.py`.
2. Update `etl_framework/runner/job_validation.py` and unit tests `tests/unit/test_job_validation.py`.
3. Update `api/services/run_executor.py` and unit tests `tests/unit/test_run_executor_athena.py`.
4. Update `frontend/partials/tab-aws.html`, `frontend/features/aws.js`, and run `npm run build:html`.
5. Update `tests/e2e/20-aws-athena-tab.spec.ts` and run full regression suite.
