# AWS Athena DQ Operators Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement rich comparison operators (==, !=, >, >=, <, <=, between) with absolute and percentage tolerance for Athena data quality metric assertions.

**Architecture:** Add a new pure reusable comparator module in `etl_framework/assertions/comparators.py`, use it in `job_validation.py` and `run_executor.py`, and implement a dynamic repeatable assertion row builder in the Athena UI panel.

**Tech Stack:** Python 3.10+, Playwright, Alpine.js, HTML.

## Global Constraints

- Preserve backward compatibility with existing scalar metric assertion jobs (`{"row_count": 100}` maps to `operator: "=="`, `value: 100`).
- No modifications to `etl_framework/reconciliation/dq_engine.py`.
- Validation errors must be formatted as `ValidationIssue("params.metric_assertions.<path>", error_message)`.
- Runtime mismatched assertions must yield descriptive `MismatchRecord` entries with `mismatch_type="athena_metric_mismatch"`, rather than unhandled Python exceptions.

---

### Task 1: Core Assertion Comparator Module

**Files:**
- Create: `etl_framework/assertions/__init__.py`
- Create: `etl_framework/assertions/comparators.py`
- Create: `tests/unit/test_assertion_comparators.py`

**Interfaces:**
- Consumes: None.
- Produces: `AssertionConfig`, `ComparisonOutcome`, `normalise_assertion(raw: Any) -> AssertionConfig`, `validate_assertion(path: str, raw: Any) -> list[str]`, `evaluate_assertion(assertion: AssertionConfig, actual: Any) -> ComparisonOutcome`.

- [ ] **Step 1: Write the failing tests**

```python
# Create tests/unit/test_assertion_comparators.py
from etl_framework.assertions.comparators import (
    AssertionConfig,
    ComparisonOutcome,
    normalise_assertion,
    validate_assertion,
    evaluate_assertion,
)

def test_normalise_legacy_scalar():
    assert normalise_assertion(100) == AssertionConfig(operator="==", value=100)

def test_normalise_dict_forms():
    assert normalise_assertion({"operator": "between", "min": 10, "max": 20}) == AssertionConfig(operator="between", min_value=10, max_value=20)
    assert normalise_assertion({"operator": "==", "value": 5, "tolerance": "10%"}) == AssertionConfig(operator="==", value=5, tolerance="10%")

def test_validate_assertion():
    assert validate_assertion("path", 100) == []
    assert validate_assertion("path", {"operator": "between", "min": 20, "max": 10}) == ["between requires min <= max"]
    assert validate_assertion("path", {"operator": ">", "value": 10, "tolerance": 1}) == ["tolerance is only valid with == or !="]
    assert validate_assertion("path", {"operator": "invalid"}) == ["unsupported operator: invalid"]

def test_evaluate_scalar_equality():
    outcome = evaluate_assertion(normalise_assertion(100), 100)
    assert outcome.passed
    assert not evaluate_assertion(normalise_assertion(100), 99).passed

def test_evaluate_tolerance():
    assert evaluate_assertion(AssertionConfig("==", value=100.0, tolerance=5.0), 96.0).passed
    assert not evaluate_assertion(AssertionConfig("==", value=100.0, tolerance=5.0), 106.0).passed
    assert evaluate_assertion(AssertionConfig("==", value=200.0, tolerance="10%"), 215.0).passed
    
def test_evaluate_between():
    cfg = AssertionConfig("between", min_value=10.0, max_value=20.0)
    assert evaluate_assertion(cfg, 15.0).passed
    assert not evaluate_assertion(cfg, 9.9).passed

def test_evaluate_inequalities():
    assert evaluate_assertion(AssertionConfig(">=", value=10), 10).passed
    assert not evaluate_assertion(AssertionConfig("<", value=5), 5).passed

def test_evaluate_missing_or_non_numeric():
    from api.services.run_executor import _MISSING_METRIC # Assuming this sentinel exists, otherwise just test None
    assert evaluate_assertion(AssertionConfig(">=", value=10), None).reason == "metric is null"
    assert evaluate_assertion(AssertionConfig(">=", value=10), "abc").reason == "actual value 'abc' is not numeric"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_assertion_comparators.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3: Write minimal implementation**

```python
# Create etl_framework/assertions/__init__.py
# (Leave empty)

# Create etl_framework/assertions/comparators.py
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
    return AssertionConfig(
        operator=raw.get("operator", "=="),
        value=raw.get("value"),
        min_value=raw.get("min"),
        max_value=raw.get("max"),
        tolerance=raw.get("tolerance")
    )

def validate_assertion(path: str, raw: Any) -> list[str]:
    errors = []
    if not isinstance(raw, dict):
        return errors
    op = raw.get("operator")
    if op not in ("==", "!=", ">", ">=", "<", "<=", "between"):
        return [f"unsupported operator: {op}"]
    if op == "between":
        if raw.get("min") is None or raw.get("max") is None:
            return ["between requires min and max"]
        try:
            if float(raw["min"]) > float(raw["max"]):
                return ["between requires min <= max"]
        except (ValueError, TypeError):
            return ["between requires numeric min and max"]
    elif raw.get("value") is None:
        return [f"{op} requires value"]
        
    if raw.get("tolerance") is not None and op not in ("==", "!="):
        return ["tolerance is only valid with == or !="]
    
    if raw.get("tolerance") is not None:
        tol = raw["tolerance"]
        if isinstance(tol, str) and tol.endswith("%"):
            try:
                float(tol[:-1])
            except ValueError:
                errors.append("invalid percentage tolerance format")
        elif not isinstance(tol, (int, float)):
            errors.append("tolerance must be a number or a percentage string")
            
    return errors

def _is_numeric(val: Any) -> bool:
    if isinstance(val, bool): return False
    return isinstance(val, (int, float))

def evaluate_assertion(assertion: AssertionConfig, actual: Any) -> ComparisonOutcome:
    op = assertion.operator
    val = assertion.value
    
    # Format expected display
    disp_tol = f" \u00b1{assertion.tolerance}" if assertion.tolerance else ""
    if op == "between":
        disp = f"between {assertion.min_value} and {assertion.max_value}"
    else:
        disp = f"{op} {val}{disp_tol}"
        
    # Handle missing/null
    # If codebase has _MISSING_METRIC we check by name to avoid importing it if not strictly necessary, 
    # but the simplest is to check via str repr or type.
    if actual is None or type(actual).__name__ == "object": 
        return ComparisonOutcome(False, disp, "metric missing" if type(actual).__name__ == "object" else "metric is null")

    if op in (">", ">=", "<", "<=", "between"):
        if not _is_numeric(actual):
            return ComparisonOutcome(False, disp, f"actual value '{actual}' is not numeric")

    try:
        passed = False
        if op == "between":
            passed = float(assertion.min_value) <= float(actual) <= float(assertion.max_value)
        elif assertion.tolerance is not None and _is_numeric(actual) and _is_numeric(val):
            actual_f = float(actual)
            val_f = float(val)
            tol = assertion.tolerance
            if isinstance(tol, str) and tol.endswith("%"):
                margin = abs(val_f) * (float(tol[:-1]) / 100.0)
            else:
                margin = float(tol)
            diff = abs(actual_f - val_f)
            passed = diff <= margin if op == "==" else diff > margin
        else:
            if op == "==": passed = actual == val
            elif op == "!=": passed = actual != val
            elif op == ">": passed = float(actual) > float(val)
            elif op == ">=": passed = float(actual) >= float(val)
            elif op == "<": passed = float(actual) < float(val)
            elif op == "<=": passed = float(actual) <= float(val)
            
        return ComparisonOutcome(passed, disp)
    except Exception as e:
        return ComparisonOutcome(False, disp, f"evaluation error: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_assertion_comparators.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/assertions/ tests/unit/test_assertion_comparators.py
git commit -m "feat(assertions): add core comparator module for rich metric assertions"
```

---

### Task 2: Job Validation for Athena Metric Assertions

**Files:**
- Modify: `etl_framework/runner/job_validation.py`
- Modify: `tests/unit/test_job_validation.py`

**Interfaces:**
- Consumes: `validate_assertion` from `etl_framework.assertions.comparators`
- Produces: `_validate_aws_athena_query` handles object validation safely.

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/unit/test_job_validation.py
def test_aws_athena_query_rejects_invalid_operator_assertions():
    issues = validate_job_definition({"name": "a", "job_type": "aws_athena_query", "params": {
        "config_id": 1, "query": "q", "output_location": "o", 
        "metric_assertions": {"row_count": {"operator": "invalid"}}
    }})
    assert any(i.field == "params.metric_assertions.row_count" and "unsupported operator" in i.message for i in issues)

def test_aws_athena_query_accepts_valid_operator_assertions():
    issues = validate_job_definition({"name": "a", "job_type": "aws_athena_query", "params": {
        "config_id": 1, "query": "q", "output_location": "o", 
        "metric_assertions": {"row_count": {"operator": "between", "min": 2, "max": 5}}
    }})
    assert not any(i.field.startswith("params.metric_assertions") for i in issues)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_job_validation.py::test_aws_athena_query_rejects_invalid_operator_assertions -v`
Expected: FAIL 

- [ ] **Step 3: Write minimal implementation**

Edit `etl_framework/runner/job_validation.py`, around line 151:

```python
# Add import at top or inside the function
from etl_framework.assertions.comparators import validate_assertion

# ... existing code ...
    if "metric_assertions" in params:
        metric_assertions = params.get("metric_assertions")
        if not isinstance(metric_assertions, dict):
            issues.append(ValidationIssue("params.metric_assertions", "metric_assertions must be an object"))
        else:
            for path, raw_assert in metric_assertions.items():
                for error in validate_assertion(path, raw_assert):
                    issues.append(ValidationIssue(f"params.metric_assertions.{path}", error))
```
Note: Ensure you replace the old `if "metric_assertions" in params and not isinstance(...)` lines exactly.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_job_validation.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/job_validation.py tests/unit/test_job_validation.py
git commit -m "feat(aws-athena): validate rich metric assertions at job save time"
```

---

### Task 3: RunExecutor Athena Assertion Evaluation Integration

**Files:**
- Modify: `api/services/run_executor.py`
- Modify: `tests/unit/test_run_executor_athena.py`

**Interfaces:**
- Consumes: `AssertionConfig`, `normalise_assertion`, `evaluate_assertion` from `etl_framework.assertions.comparators`

- [ ] **Step 1: Write the failing tests**

```python
# Append to tests/unit/test_run_executor_athena.py
def test_execute_athena_query_fails_complex_assertions(monkeypatch, db_session):
    monkeypatch.setattr("api.services.run_executor.AwsAthenaService", lambda repo: SimpleNamespace(run_query=lambda *a, **k: run_response(row_count=5)))
    job_def = job({"metric_assertions": {
        "null_counts.id": {"operator": "<", "value": 0},  # will fail (actual is 0)
        "distinct_counts.id": {"operator": "between", "min": 2, "max": 4} # will fail (actual is 5)
    }})
    result = executor(db_session)._execute_aws_athena_query(job_def)
    assert result.status == TestStatus.FAILED
    mismatches = {m.mismatch_column: m.expected for m in result.mismatches}
    assert mismatches["null_counts.id"] == "< 0"
    assert mismatches["distinct_counts.id"] == "between 2 and 4"

def test_execute_athena_query_passes_complex_assertions(monkeypatch, db_session):
    monkeypatch.setattr("api.services.run_executor.AwsAthenaService", lambda repo: SimpleNamespace(run_query=lambda *a, **k: run_response(row_count=2)))
    # actual: distinct_counts.id = 2, null_counts = 0
    job_def = job({"metric_assertions": {
        "null_counts.id": {"operator": "<=", "value": 0}, 
        "distinct_counts.id": {"operator": "between", "min": 1, "max": 5}
    }})
    result = executor(db_session)._execute_aws_athena_query(job_def)
    assert result.status == TestStatus.PASSED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_run_executor_athena.py::test_execute_athena_query_fails_complex_assertions -v`
Expected: FAIL 

- [ ] **Step 3: Write minimal implementation**

Edit `api/services/run_executor.py`. Update imports and the `_athena_mismatches` function:

```python
from etl_framework.assertions.comparators import normalise_assertion, evaluate_assertion

# ... existing code ...

    def _athena_mismatches(
        self,
        job: JobDefinition,
        params: dict[str, Any],
        metrics: dict[str, Any],
        row_count: int,
    ) -> list[MismatchRecord]:
        mismatches: list[MismatchRecord] = []
        expected_status = params.get("expected_status", "SUCCEEDED")
        if metrics.get("state") != expected_status:
            mismatches.append(MismatchRecord({"job": job.name}, "status", expected_status, metrics.get("state"), "athena_status_mismatch"))
        if params.get("min_rows") not in (None, "") and row_count < int(params["min_rows"]):
            mismatches.append(MismatchRecord({"job": job.name}, "row_count", int(params["min_rows"]), row_count, "athena_row_count_below_min"))
        if params.get("max_rows_assert") not in (None, "") and row_count > int(params["max_rows_assert"]):
            mismatches.append(MismatchRecord({"job": job.name}, "row_count", int(params["max_rows_assert"]), row_count, "athena_row_count_above_max"))
            
        for path, raw_assert in (params.get("metric_assertions") or {}).items():
            actual = self._metric_path(metrics, str(path))
            assertion = normalise_assertion(raw_assert)
            outcome = evaluate_assertion(assertion, actual)
            
            if not outcome.passed:
                mismatch_actual = "<missing>" if actual is _MISSING_METRIC else actual
                mismatches.append(MismatchRecord({"job": job.name}, str(path), outcome.expected_display, mismatch_actual, "athena_metric_mismatch"))
                
        return mismatches
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_run_executor_athena.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_athena.py
git commit -m "feat(aws-athena): evaluate DQ assertions using rich comparators"
```

---

### Task 4: Athena Panel Assertion Row Builder in Frontend

**Files:**
- Modify: `frontend/partials/tab-aws.html`
- Modify: `frontend/features/aws.js`

**Interfaces:**
- Updates HTML DOM to serialize repeatable assertion inputs.

- [ ] **Step 1: Write the minimal implementation in `aws.js`**

Modify `frontend/features/aws.js`:
Add state and helpers around line 56:
```javascript
      awsAthenaMetricAssertions: [],

      awsAthenaAddAssertion() {
        this.awsAthenaMetricAssertions.push({ path: '', operator: '==', value: '', min: '', max: '', tolerance: '' });
      },
      awsAthenaRemoveAssertion(index) {
        this.awsAthenaMetricAssertions.splice(index, 1);
      },
```

Update `_awsAthenaJobParams()` around line 274:
```javascript
      _awsAthenaJobParams() {
        const params = this._awsAthenaRunQueryParams();
        const minRows = String(this.awsAthenaMinRows || '').trim();
        const maxRowsAssert = String(this.awsAthenaMaxRowsAssert || '').trim();
        if (minRows !== '') params.min_rows = Number(minRows);
        if (maxRowsAssert !== '') params.max_rows_assert = Number(maxRowsAssert);
        
        const metric_assertions = {};
        for (const ast of this.awsAthenaMetricAssertions) {
            const p = ast.path.trim();
            if (!p) continue;
            
            if (ast.operator === '==') {
                if (!ast.tolerance.trim()) {
                    metric_assertions[p] = ast.value;
                } else {
                    metric_assertions[p] = { operator: '==', value: ast.value, tolerance: isNaN(Number(ast.tolerance)) ? ast.tolerance.trim() : Number(ast.tolerance) };
                }
            } else if (ast.operator === 'between') {
                metric_assertions[p] = { operator: 'between', min: Number(ast.min), max: Number(ast.max) };
            } else if (ast.operator === '!=') {
                if (!ast.tolerance.trim()) {
                    metric_assertions[p] = { operator: '!=', value: ast.value };
                } else {
                    metric_assertions[p] = { operator: '!=', value: ast.value, tolerance: isNaN(Number(ast.tolerance)) ? ast.tolerance.trim() : Number(ast.tolerance) };
                }
            } else {
                metric_assertions[p] = { operator: ast.operator, value: Number(ast.value) };
            }
        }
        if (Object.keys(metric_assertions).length > 0) {
            params.metric_assertions = metric_assertions;
        }
        
        return params;
      },
```

- [ ] **Step 2: Update HTML in `tab-aws.html`**

Edit `frontend/partials/tab-aws.html`. Under the "Data quality bounds" section (around line 265). Replace the generic bounds area, or add right after `maxRowsAssert`:

```html
          <div class="mt-4">
            <label class="block text-sm font-medium text-slate-700 mb-2">Metric Assertions</label>
            <template x-for="(ast, index) in awsAthenaMetricAssertions" :key="index">
              <div class="flex items-center gap-2 mb-2">
                <input x-model="ast.path" class="field-input w-48" placeholder="metric path e.g. null_counts.id" />
                <select x-model="ast.operator" class="field-input w-32">
                  <option value="==">==</option>
                  <option value="!=">!=</option>
                  <option value=">">&gt;</option>
                  <option value=">=">&gt;=</option>
                  <option value="<">&lt;</option>
                  <option value="<=">&lt;=</option>
                  <option value="between">between</option>
                </select>
                
                <template x-if="ast.operator === 'between'">
                  <div class="flex items-center gap-2">
                    <input x-model="ast.min" type="number" class="field-input w-24" placeholder="min" />
                    <input x-model="ast.max" type="number" class="field-input w-24" placeholder="max" />
                  </div>
                </template>
                <template x-if="ast.operator !== 'between'">
                  <input x-model="ast.value" class="field-input w-32" placeholder="value" />
                </template>
                <template x-if="ast.operator === '==' || ast.operator === '!='">
                   <input x-model="ast.tolerance" class="field-input w-24" placeholder="tol / %" />
                </template>
                
                <button type="button" @click="awsAthenaRemoveAssertion(index)" class="text-rose-500 hover:bg-rose-50 px-2 py-1 rounded">X</button>
              </div>
            </template>
            <button type="button" @click="awsAthenaAddAssertion()" class="text-sm text-blue-600 hover:underline" data-testid="aws-athena-add-assertion-btn">+ Add Metric Assertion</button>
          </div>
```

- [ ] **Step 3: Rebuild HTML UI**

Run: `npm run build:html`
Required for Playwright and real browsers to see the changes.

- [ ] **Step 4: Commit**

```bash
git add frontend/partials/tab-aws.html frontend/features/aws.js frontend/index.html
git commit -m "feat(aws-ui): add dynamic Athena metric assertion row builder"
```

---

### Task 5: End-to-End Playwright Assertion Test

**Files:**
- Modify: `tests/e2e/20-aws-athena-tab.spec.ts`

- [ ] **Step 1: Write the passing test logic**

Modify `tests/e2e/20-aws-athena-tab.spec.ts`, around line 71:

```typescript
    await authedPage.locator('[data-testid="aws-athena-output-location-input"]').fill(' s3://athena-out/ ');
    await authedPage.locator('[data-testid="aws-athena-min-rows-input"]').fill('1');

    // Add a metric assertion using the UI
    await authedPage.locator('[data-testid="aws-athena-add-assertion-btn"]').click();
    // Assuming the first input in the new row is path
    const assertionRow = authedPage.locator('input[placeholder="metric path e.g. null_counts.id"]').first();
    await assertionRow.fill('null_counts.amount');
    
    // Select operator '<='
    await authedPage.locator('select.field-input').first().selectOption('<=');
    // Set value '0'
    await authedPage.locator('input[placeholder="value"]').first().fill('0');
```

Update the POST `/api/jobs` expectation (line 82):

```typescript
    expect(jobBody.job_type).toBe('aws_athena_query');
    expect(jobBody.params).toMatchObject({
      config_id: 7,
      database: 'curated',
      query: 'select id, amount from orders',
      output_location: 's3://athena-out/',
      min_rows: 1,
      metric_assertions: {
         'null_counts.amount': { operator: '<=', value: 0 }
      }
    });
```

- [ ] **Step 2: Run test to verify it passes**

Run: `npm run test:e2e tests/e2e/20-aws-athena-tab.spec.ts`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/20-aws-athena-tab.spec.ts
git commit -m "test(aws-athena): e2e playwright verification for new metric assertion builder"
```
