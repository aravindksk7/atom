# Timestamp UTC/Local Consistency Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three places where the app's timestamp convention (store UTC, display local) is broken: an HTML report that shows raw UTC with no conversion, a JSON log formatter that mislabels local time as UTC, and two naive `datetime.now()` call sites that silently produce local-time values in fields the rest of the codebase treats as timezone-aware UTC.

**Architecture:** No changes to the overall convention (UTC storage, local display) or to `frontend/app.js`, which already converts correctly. Each fix is independent and localized: two call-site tz-awareness fixes, one `datefmt` string fix, and one new Jinja filter used by the report template.

**Tech Stack:** Python stdlib `datetime`/`logging`, Jinja2 (via the existing `ReportGenerator`), pytest.

---

### Task 1: Make `ReconciliationEngine.reconcile()`'s `executed_at` timezone-aware UTC

**Files:**
- Modify: `etl_framework/reconciliation/engine.py:77`
- Test: `tests/unit/test_reconciliation.py`

**Context:** `engine.py:77` sets `executed_at = datetime.now()` (naive, local time). Every other producer of `executed_at` in the codebase (e.g. `api/services/run_executor.py:545`, `etl_framework/reconciliation/column_stats.py:100`) uses `datetime.now(timezone.utc)`, and the DB column (`etl_framework/repository/models.py:110`) is `DateTime(timezone=True)`. A naive value here gets silently treated as UTC downstream, skewing it by the server's UTC offset. `engine.py` already imports `timezone` from `datetime` (line 7), so no import changes are needed.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_reconciliation.py` (near the other `reconcile()`-based tests):

```python
def test_executed_at_is_timezone_aware():
    df = pd.DataFrame({"id": [1], "val": ["x"]})
    engine = _make_engine(df, df.copy())
    result = engine.reconcile("SELECT 1", "q")
    assert result.executed_at.tzinfo is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reconciliation.py::test_executed_at_is_timezone_aware -v`
Expected: FAIL with `AssertionError: assert None is not None` (current `executed_at` is naive).

- [ ] **Step 3: Fix the implementation**

In `etl_framework/reconciliation/engine.py`, change line 77:

```python
            executed_at = datetime.now(timezone.utc)
```

(replacing `executed_at = datetime.now()`)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_reconciliation.py::test_executed_at_is_timezone_aware -v`
Expected: PASS

- [ ] **Step 5: Run the full reconciliation test file to check for regressions**

Run: `pytest tests/unit/test_reconciliation.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/reconciliation/engine.py tests/unit/test_reconciliation.py
git commit -m "fix: make ReconciliationEngine.executed_at timezone-aware UTC"
```

---

### Task 2: Make `AutomicClient`'s `checked_at` timezone-aware UTC

**Files:**
- Modify: `etl_framework/automic/client.py:3,73,82,86`
- Test: Create `tests/unit/test_automic_client.py`

**Context:** `client.py` sets `checked_at=datetime.now()` (naive, local time) in three places (lines 73, 82, 86), while `api/schemas.py:393` declares `checked_at: datetime` expecting a timezone-aware UTC value like everywhere else in the app. Needs `timezone` added to the existing `from datetime import datetime` import (line 3).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_automic_client.py`:

```python
from etl_framework.automic.client import AutomicClient
from etl_framework.config.models import EnvironmentConfig


def _make_client():
    env = EnvironmentConfig(
        name="test-env",
        db_host="localhost",
        db_password="",
        automic_url="https://automic.test",
    )
    return AutomicClient(env)


def test_get_status_by_run_id_checked_at_is_timezone_aware(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_request", lambda method, url: {"status": "ENDED_OK"})
    status = client.get_status_by_run_id("run-123")
    assert status.checked_at.tzinfo is not None


def test_get_status_by_job_name_checked_at_is_timezone_aware(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        client, "_request", lambda method, url: {"data": [{"status": "ENDED_OK"}]}
    )
    status = client.get_status_by_job_name("job-1")
    assert status.checked_at.tzinfo is not None


def test_get_status_by_job_name_no_executions_checked_at_is_timezone_aware(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_request", lambda method, url: {"data": []})
    status = client.get_status_by_job_name("job-1")
    assert status.checked_at.tzinfo is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_automic_client.py -v`
Expected: all 3 FAIL with `AssertionError: assert None is not None`.

- [ ] **Step 3: Fix the implementation**

In `etl_framework/automic/client.py`:

Line 3, change:
```python
from datetime import datetime, timezone
```

Line 73, change:
```python
            checked_at=datetime.now(timezone.utc), raw_response=data
```

Line 82, change:
```python
            return JobStatus(identifier=job_name, identifier_type="job_name", status=TestStatus.FAILED, environment=self._env_name, checked_at=datetime.now(timezone.utc), raw_response=data)
```

Line 86, change:
```python
        return JobStatus(identifier=job_name, identifier_type="job_name", status=status, environment=self._env_name, checked_at=datetime.now(timezone.utc), raw_response=latest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_automic_client.py -v`
Expected: all 3 PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/automic/client.py tests/unit/test_automic_client.py
git commit -m "fix: make AutomicClient.checked_at timezone-aware UTC"
```

---

### Task 3: Fix JSON log formatter's mislabeled `Z` suffix

**Files:**
- Modify: `etl_framework/utils/logging.py:46`
- Test: `tests/unit/test_logging.py`

**Context:** `logging.Formatter`'s default time converter is `time.localtime` (not `time.gmtime`), so `%(asctime)s` is already local server time. The JSON formatter hardcodes `datefmt="%Y-%m-%dT%H:%M:%SZ"`, which falsely labels that local-time value as UTC. Switching to `%z` makes `strftime` emit the real numeric UTC offset (e.g. `-0400`) instead of a fake `Z`. This was verified to work correctly with `time.strftime('%z', time.localtime())` on this platform.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_logging.py` (add `import re` and `from datetime import timedelta` to the top imports):

```python
import re
```

Then add the test:

```python
def test_json_log_timestamp_has_real_utc_offset_not_fake_z(tmp_path):
    log_file = str(tmp_path / "json_tz.log")
    configure_logging(level="INFO", log_file=log_file, log_format="json")
    logging.getLogger("etl_framework.tz_test").info("tz message")
    lines = (tmp_path / "json_tz.log").read_text().strip().splitlines()
    record = json.loads(lines[-1])
    assert re.search(r"[+-]\d{4}$", record["timestamp"]), record["timestamp"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_logging.py::test_json_log_timestamp_has_real_utc_offset_not_fake_z -v`
Expected: FAIL — current timestamp ends with literal `Z`, which doesn't match `[+-]\d{4}$`.

- [ ] **Step 3: Fix the implementation**

In `etl_framework/utils/logging.py`, change line 46:

```python
                datefmt="%Y-%m-%dT%H:%M:%S%z",
```

(replacing `datefmt="%Y-%m-%dT%H:%M:%SZ",`)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_logging.py::test_json_log_timestamp_has_real_utc_offset_not_fake_z -v`
Expected: PASS

- [ ] **Step 5: Run the full logging test file to check for regressions**

Run: `pytest tests/unit/test_logging.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/utils/logging.py tests/unit/test_logging.py
git commit -m "fix: stop JSON log formatter from mislabeling local time as UTC"
```

---

### Task 4: Convert `accepted_at` to local time in the HTML report template

**Files:**
- Modify: `etl_framework/reporting/generator.py`
- Modify: `etl_framework/reporting/templates/report.html.j2:348`
- Test: `tests/unit/test_report_template.py`, new `tests/unit/test_reporting_generator.py`

**Context:** `report.html.j2:348` renders `mm.accepted_at` (a UTC-aware `datetime` from `etl_framework/repository/repository.py:268`) via raw `strftime`, with no conversion. This is a static, server-rendered HTML file with no client-side JS — there's no browser to do a per-viewer conversion, so "local" here means the server's local time zone. Add a `to_local` Jinja filter to `ReportGenerator` that converts via `.astimezone()` (Python's standard UTC→system-local conversion) and appends a zone abbreviation so the value is unambiguous.

- [ ] **Step 1: Write the failing unit tests for the new filter**

Create `tests/unit/test_reporting_generator.py`:

```python
from datetime import datetime, timezone

from etl_framework.reporting.generator import to_local


def test_to_local_converts_utc_datetime_to_local_with_zone_abbreviation():
    utc_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    result = to_local(utc_dt)
    assert result == utc_dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")


def test_to_local_returns_empty_string_for_none():
    assert to_local(None) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reporting_generator.py -v`
Expected: FAIL with `ImportError: cannot import name 'to_local'` (function doesn't exist yet).

- [ ] **Step 3: Add the `to_local` filter function and register it**

In `etl_framework/reporting/generator.py`, after the `logger = ...` line and before `class ReportGenerator:`, add:

```python
def to_local(value):
    """Jinja filter: render an aware UTC datetime as local wall-clock time with a zone abbreviation."""
    if value is None:
        return ""
    return value.astimezone().strftime("%Y-%m-%d %H:%M %Z")
```

Then in `ReportGenerator.__init__`, right after `self._jinja_env = Environment(loader=loader, autoescape=True)`, add:

```python
        self._jinja_env.filters["to_local"] = to_local
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_reporting_generator.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing integration test for the template**

Add to `tests/unit/test_report_template.py`:

```python
def test_accepted_at_rendered_via_to_local_filter(tmp_path):
    accepted_dt = datetime(2026, 7, 1, 18, 30, 0, tzinfo=timezone.utc)
    mm = _make_mm("amount", "100.00", "100.01")
    mm.accepted = True
    mm.accepted_by = "alice"
    mm.accepted_at = accepted_dt
    html = _render(_make_suite([mm]), tmp_path)
    expected = accepted_dt.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    assert expected in html
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/unit/test_report_template.py::test_accepted_at_rendered_via_to_local_filter -v`
Expected: FAIL — template currently renders `accepted_dt.strftime('%Y-%m-%d %H:%M')` (no zone abbreviation, no conversion), which won't match `expected`.

- [ ] **Step 7: Update the template**

In `etl_framework/reporting/templates/report.html.j2`, change line 348:

```
                    ✓ Accepted{% if mm.accepted_by %} by {{ mm.accepted_by }}{% endif %}{% if mm.accepted_at %} on {{ mm.accepted_at | to_local }}{% endif %}{% if mm.accepted_note %} — {{ mm.accepted_note }}{% endif %}
```

(replacing `{{ mm.accepted_at.strftime('%Y-%m-%d %H:%M') }}` with `{{ mm.accepted_at | to_local }}`)

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/unit/test_report_template.py::test_accepted_at_rendered_via_to_local_filter -v`
Expected: PASS

- [ ] **Step 9: Run the full report template test file to check for regressions**

Run: `pytest tests/unit/test_report_template.py tests/unit/test_reporting_generator.py -v`
Expected: all PASS

- [ ] **Step 10: Commit**

```bash
git add etl_framework/reporting/generator.py etl_framework/reporting/templates/report.html.j2 tests/unit/test_report_template.py tests/unit/test_reporting_generator.py
git commit -m "fix: render report's accepted_at in local time with zone label"
```

---

### Task 5: Full regression check

**Files:** None (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all PASS, no failures introduced by Tasks 1-4.

- [ ] **Step 2: Confirm no leftover naive `datetime.now()` timestamp producers**

Run: `grep -rn "datetime.now()" etl_framework api --include=*.py`
Expected: no matches (all producers of stored/returned timestamp fields now use `datetime.now(timezone.utc)`).
