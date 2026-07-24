# SAP BO Job Execution ("bo_job") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `bo_job` job type that schedules a SAP BO InfoStore object (WebI/Crystal/Publication) via the BOE REST API and waits for it to finish, so it can run as a step in an ETL framework job sequence alongside every other job type, chained via the existing `depends_on` mechanism.

**Architecture:** `BORestClient` (`etl_framework/sap_bo/client.py`) gains two new REST methods (`schedule_object`, `wait_for_completion`) reusing its existing lazy-auth/`BOAPIError` conventions. `RunExecutor` (`api/services/run_executor.py`) gains a `_build_case_bo_job` step builder wired into the existing dispatcher, following the exact pattern `_build_case_bo_report`/`_build_case_automic` already use. No new orchestration engine, API routes, or CLI commands — `bo_job` jobs are created/saved/sequenced/launched through the same generic `JobDefinition` paths every other job type uses.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, pytest, requests, Alpine.js (frontend job modal), a local HTTP mock SAP BO server (`docker/sapbo-mock/server.py`) for integration tests.

**Spec:** `docs/superpowers/specs/2026-07-24-sap-bo-job-execution-design.md`

---

## Task 1: `JobDefinition` schema — add `bo_job` job type

**Files:**
- Modify: `api/schemas.py:446-449` (job_type Literal), `api/schemas.py:461-490` (`validate_reconciliation_contract`)
- Test: `tests/unit/test_job_schema_bo_job.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_job_schema_bo_job.py`:

```python
from __future__ import annotations

import pytest

from api.schemas import JobDefinition


def test_bo_job_requires_object_id():
    with pytest.raises(ValueError, match="bo_job jobs require 'object_id' in params"):
        JobDefinition(name="refresh_sales", job_type="bo_job", params={})


def test_bo_job_valid_with_object_id():
    job = JobDefinition(
        name="refresh_sales",
        job_type="bo_job",
        params={"object_id": "3001"},
    )
    assert job.params["object_id"] == "3001"


def test_bo_job_accepts_optional_schedule_params_and_polling_overrides():
    job = JobDefinition(
        name="refresh_sales",
        job_type="bo_job",
        params={
            "object_id": "3001",
            "schedule_params": {"prompt_values": {"region": "EMEA"}},
            "poll_interval_s": 2,
            "timeout_s": 120,
        },
    )
    assert job.params["schedule_params"] == {"prompt_values": {"region": "EMEA"}}
    assert job.params["poll_interval_s"] == 2
    assert job.params["timeout_s"] == 120
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_job_schema_bo_job.py -v`
Expected: FAIL — `bo_job` is not a valid `job_type` literal (pydantic `ValidationError`, not the expected `ValueError` message).

- [ ] **Step 3: Add `bo_job` to the `job_type` Literal**

In `api/schemas.py`, change (around line 446-449):

```python
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile", "api_reconciliation",
    ] = "reconciliation"
```

to:

```python
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile", "api_reconciliation",
        "bo_job",
    ] = "reconciliation"
```

- [ ] **Step 4: Add the `bo_job` validation branch**

In `api/schemas.py`, in `validate_reconciliation_contract`, add a new `elif` branch immediately after the existing `automic_job` branch (around line 471-473):

```python
        elif self.job_type == "automic_job":
            if not self.params.get("job_name") and not self.params.get("run_id"):
                raise ValueError("automic_job jobs require 'job_name' or 'run_id' in params")
        elif self.job_type == "bo_job":
            if not self.params.get("object_id"):
                raise ValueError("bo_job jobs require 'object_id' in params")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_job_schema_bo_job.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py tests/unit/test_job_schema_bo_job.py
git commit -m "feat(jobs): add bo_job job type to JobDefinition schema"
```

---

## Task 2: `job_validation.py` — add `bo_job` branch

The generic pre-save validator (`etl_framework/runner/job_validation.py`) duplicates the pydantic contract checks as friendlier `ValidationIssue` objects for the UI's inline validation. It already has separate branches for every job type (`bo_report`, `automic_job`, etc. — see `etl_framework/runner/job_validation.py:77-85`), so `bo_job` needs the same treatment here.

**Files:**
- Modify: `etl_framework/runner/job_validation.py:80-82`
- Test: `tests/unit/test_job_validation.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_job_validation.py`:

```python
def test_bo_job_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "refresh_sales",
        "job_type": "bo_job",
        "params": {"object_id": "3001"},
    })
    assert issues == []


def test_bo_job_requires_object_id():
    issues = validate_job_definition({
        "name": "refresh_sales",
        "job_type": "bo_job",
        "params": {},
    })
    assert any(issue.field == "params.object_id" for issue in issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_job_validation.py -v -k bo_job`
Expected: FAIL — `test_bo_job_valid_job_has_no_issues` fails because `validate_job_definition` doesn't recognize `bo_job` and returns no issues either way, but pair it with the second test: `test_bo_job_requires_object_id` fails because no `params.object_id` issue is produced (job_type falls through every `elif` unmatched, `issues` stays empty).

- [ ] **Step 3: Add the `bo_job` branch**

In `etl_framework/runner/job_validation.py`, add a new `elif` branch immediately after the existing `automic_job` branch (around line 80-82):

```python
    elif job_type == "automic_job":
        if not params.get("job_name") and not params.get("run_id"):
            issues.append(ValidationIssue("params", "automic_job jobs require job_name or run_id"))
    elif job_type == "bo_job":
        if not params.get("object_id"):
            issues.append(ValidationIssue("params.object_id", "bo_job jobs require object_id"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_job_validation.py -v -k bo_job`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/job_validation.py tests/unit/test_job_validation.py
git commit -m "feat(jobs): validate bo_job params in job_validation"
```

---

## Task 3: `BORestClient.schedule_object`

**Files:**
- Modify: `etl_framework/sap_bo/client.py`
- Test: `tests/unit/test_bo_rest_client.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_bo_rest_client.py`:

```python
# ---------------------------------------------------------------------------
# schedule_object
# ---------------------------------------------------------------------------

def test_schedule_object_posts_to_infostore_schedules_endpoint(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "inst-42"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        instance_id = authenticated_client.schedule_object("3001")

    assert instance_id == "inst-42"
    called_url = mock_post.call_args[0][0]
    assert called_url == "http://bo.example.com/biprws/infostore/3001/schedules"


def test_schedule_object_sends_schedule_params_as_json_body(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "inst-42"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.schedule_object("3001", {"prompt_values": {"region": "EMEA"}})

    assert mock_post.call_args[1]["json"] == {"prompt_values": {"region": "EMEA"}}


def test_schedule_object_authenticates_first_if_no_token(env_config):
    from etl_framework.sap_bo.client import BORestClient

    client = BORestClient(env_config)
    auth_response = MagicMock()
    auth_response.status_code = 200
    auth_response.headers = {"X-SAP-LogonToken": "tok"}
    schedule_response = MagicMock()
    schedule_response.status_code = 200
    schedule_response.json.return_value = {"id": "inst-1"}
    with patch.object(client._session, "post", side_effect=[auth_response, schedule_response]):
        instance_id = client.schedule_object("3001")

    assert instance_id == "inst-1"
    assert client.logon_token == "tok"


def test_schedule_object_raises_bo_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import BOAPIError

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "object not found"
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.schedule_object("does-not-exist")


def test_schedule_object_raises_bo_api_error_when_response_has_no_id(authenticated_client):
    from etl_framework.exceptions import BOAPIError

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.schedule_object("3001")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_bo_rest_client.py -v -k schedule_object`
Expected: FAIL — `AttributeError: 'BORestClient' object has no attribute 'schedule_object'`

- [ ] **Step 3: Implement `schedule_object`**

In `etl_framework/sap_bo/client.py`, add a class attribute next to the existing endpoint constants (around line 59-60):

```python
class BORestClient:
    LOGON_ENDPOINT = "/biprws/logon/long"
    REPORT_ENDPOINT = "/biprws/raylight/v1/documents/{doc_id}/reports"
    SCHEDULE_ENDPOINT = "/biprws/infostore/{object_id}/schedules"
    INSTANCE_ENDPOINT = "/biprws/infostore/{instance_id}"
```

Then add the method after `download_report` (before `logout`, around line 291):

```python
    def schedule_object(self, object_id: str, schedule_params: dict | None = None) -> str:
        """POST /biprws/infostore/{object_id}/schedules — schedule any BOE
        InfoStore object (WebI document, Crystal Report, or Publication) to
        run now. `schedule_params` is passed through as the JSON body for
        object-specific run parameters (e.g. prompt values). Returns the new
        schedule instance id.

        Response shape is best-effort pending verification against a live
        biprws server: assumes {"id": "<instance_id>"}, matching every other
        biprws entity this client parses (list_documents/list_reports).
        """
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}{self.SCHEDULE_ENDPOINT.format(object_id=object_id)}"
        response = self._session.post(
            url,
            json=schedule_params or {},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=object_id, http_status=response.status_code, response_body=response.text,
            )
        instance_id = str(response.json().get("id", ""))
        if not instance_id:
            raise BOAPIError(
                report_id=object_id, http_status=response.status_code,
                response_body="schedule response missing 'id'",
            )
        return instance_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bo_rest_client.py -v -k schedule_object`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/sap_bo/client.py tests/unit/test_bo_rest_client.py
git commit -m "feat(sap-bo): add BORestClient.schedule_object"
```

---

## Task 4: `BORestClient.get_schedule_status` + `wait_for_completion`

**Files:**
- Modify: `etl_framework/sap_bo/client.py`
- Test: `tests/unit/test_bo_rest_client.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_bo_rest_client.py`:

```python
# ---------------------------------------------------------------------------
# get_schedule_status / wait_for_completion
# ---------------------------------------------------------------------------

from etl_framework.runner.state import TestStatus


@pytest.mark.parametrize("raw_status,expected", [
    ("Success", TestStatus.PASSED),
    ("success", TestStatus.PASSED),
    ("Failed", TestStatus.FAILED),
    ("Running", TestStatus.RUNNING),
    ("Pending", TestStatus.RUNNING),
    ("Recurring", TestStatus.RUNNING),
    ("Paused", TestStatus.RUNNING),
])
def test_get_schedule_status_maps_known_statuses(authenticated_client, raw_status, expected):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "inst-42", "status": raw_status}
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        status = authenticated_client.get_schedule_status("inst-42")

    assert status == expected
    called_url = mock_get.call_args[0][0]
    assert called_url == "http://bo.example.com/biprws/infostore/inst-42"


def test_get_schedule_status_treats_unrecognized_status_as_running(authenticated_client, caplog):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "inst-42", "status": "SomeNewBOEStatus"}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with caplog.at_level("WARNING"):
            status = authenticated_client.get_schedule_status("inst-42")

    assert status == TestStatus.RUNNING
    assert "SomeNewBOEStatus" in caplog.text


def test_get_schedule_status_raises_bo_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import BOAPIError

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "server error"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(BOAPIError):
            authenticated_client.get_schedule_status("inst-42")


def test_wait_for_completion_returns_immediately_on_success(authenticated_client):
    with patch.object(authenticated_client, "get_schedule_status", return_value=TestStatus.PASSED) as mock_get:
        status = authenticated_client.wait_for_completion("inst-42", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    mock_get.assert_called_once_with("inst-42")


def test_wait_for_completion_polls_until_terminal_status(authenticated_client):
    with patch.object(
        authenticated_client, "get_schedule_status",
        side_effect=[TestStatus.RUNNING, TestStatus.RUNNING, TestStatus.PASSED],
    ) as mock_get:
        status = authenticated_client.wait_for_completion("inst-42", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    assert mock_get.call_count == 3


def test_wait_for_completion_raises_timeout_error_when_never_terminal(authenticated_client):
    with patch.object(authenticated_client, "get_schedule_status", return_value=TestStatus.RUNNING):
        with pytest.raises(TimeoutError, match="inst-42"):
            authenticated_client.wait_for_completion("inst-42", timeout_s=0.05, poll_interval_s=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_bo_rest_client.py -v -k "schedule_status or wait_for_completion"`
Expected: FAIL — `AttributeError: 'BORestClient' object has no attribute 'get_schedule_status'`

- [ ] **Step 3: Implement `get_schedule_status` and `wait_for_completion`**

In `etl_framework/sap_bo/client.py`, add the `import time` at the top of the file (alongside the existing imports, around line 1-6):

```python
import logging
import time
import requests
import pandas as pd
from urllib.parse import urlparse
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import BOAPIError, ReportNotFoundError
from etl_framework.runner.state import TestStatus
```

Add a class attribute next to `SCHEDULE_ENDPOINT`/`INSTANCE_ENDPOINT` (from Task 3):

```python
    STATUS_MAP: dict[str, TestStatus] = {
        "SUCCESS": TestStatus.PASSED,
        "FAILED": TestStatus.FAILED,
        "RUNNING": TestStatus.RUNNING,
        "PENDING": TestStatus.RUNNING,
        "RECURRING": TestStatus.RUNNING,
        "PAUSED": TestStatus.RUNNING,
    }
```

Then add both methods right after `schedule_object` (from Task 3):

```python
    def _normalise_schedule_status(self, raw_status: str) -> TestStatus:
        mapped = self.STATUS_MAP.get(raw_status.upper())
        if mapped is None:
            logger.warning(
                "Unrecognized SAP BO schedule status %r, treating as still running", raw_status,
            )
            return TestStatus.RUNNING
        return mapped

    def get_schedule_status(self, instance_id: str) -> TestStatus:
        """GET /biprws/infostore/{instance_id} — fetch the current status of
        a scheduled instance and map it to TestStatus. Non-terminal BOE
        states (Running/Pending/Recurring/Paused) and any unrecognized
        status string both map to TestStatus.RUNNING, so callers keep
        polling instead of mis-reading an unknown state as done."""
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}{self.INSTANCE_ENDPOINT.format(instance_id=instance_id)}"
        response = self._session.get(
            url,
            headers={"Accept": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=instance_id, http_status=response.status_code, response_body=response.text,
            )
        return self._normalise_schedule_status(str(response.json().get("status", "")))

    def wait_for_completion(
        self, instance_id: str, timeout_s: float = 600, poll_interval_s: float = 5,
    ) -> TestStatus:
        """Poll get_schedule_status until it returns a terminal status
        (PASSED/FAILED) or timeout_s elapses. Raises TimeoutError if the
        instance never reaches a terminal status in time -- callers treat
        that as a run error, not a job failure."""
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.get_schedule_status(instance_id)
            if status != TestStatus.RUNNING:
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"SAP BO schedule instance '{instance_id}' did not complete within {timeout_s}s",
                )
            time.sleep(poll_interval_s)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_bo_rest_client.py -v`
Expected: PASS (all tests in the file, including Tasks 3-4's new ones)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/sap_bo/client.py tests/unit/test_bo_rest_client.py
git commit -m "feat(sap-bo): add BORestClient.get_schedule_status and wait_for_completion"
```

---

## Task 5: `RunExecutor._build_case_bo_job` + dispatcher wiring

**Files:**
- Modify: `api/services/run_executor.py:450-479` (`_build_case` dispatcher), add new method near `_build_case_bo_report` (after line 1253)
- Test: `tests/unit/test_run_executor_live.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_run_executor_live.py` (reuses `_LIVE_SNAPSHOT`, `_make_executor`, `_session` already defined at the top of this file):

```python
# ---------------------------------------------------------------------------
# bo_job dispatch
# ---------------------------------------------------------------------------

def test_bo_job_returns_passed_on_success():
    db = _session()
    RunRepository(db).create_run("r-boj", "dev", "prod", {})
    JobRepository(db).create({
        "name": "refresh_sales",
        "description": "",
        "tags": [],
        "job_type": "bo_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"object_id": "3001"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-boj", ["refresh_sales"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.schedule_object.return_value = "inst-1"
        inst.wait_for_completion.return_value = TestStatus.PASSED
        executor.execute()

    run = RunRepository(db).get_run("r-boj")
    assert run.results[0].status == TestStatus.PASSED.value
    inst.schedule_object.assert_called_once_with("3001", None)
    inst.wait_for_completion.assert_called_once_with("inst-1", timeout_s=600, poll_interval_s=5)
    inst.logout.assert_called_once()


def test_bo_job_returns_failed_when_boe_reports_failure():
    db = _session()
    RunRepository(db).create_run("r-boj-fail", "dev", "prod", {})
    JobRepository(db).create({
        "name": "refresh_sales",
        "description": "",
        "tags": [],
        "job_type": "bo_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"object_id": "3001"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-boj-fail", ["refresh_sales"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.schedule_object.return_value = "inst-2"
        inst.wait_for_completion.return_value = TestStatus.FAILED
        executor.execute()

    run = RunRepository(db).get_run("r-boj-fail")
    assert run.results[0].status == TestStatus.FAILED.value


def test_bo_job_returns_error_on_timeout():
    db = _session()
    RunRepository(db).create_run("r-boj-timeout", "dev", "prod", {})
    JobRepository(db).create({
        "name": "refresh_sales",
        "description": "",
        "tags": [],
        "job_type": "bo_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"object_id": "3001", "timeout_s": 1, "poll_interval_s": 1},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-boj-timeout", ["refresh_sales"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.schedule_object.return_value = "inst-3"
        inst.wait_for_completion.side_effect = TimeoutError("did not complete")
        executor.execute()

    run = RunRepository(db).get_run("r-boj-timeout")
    assert run.results[0].status == TestStatus.ERROR.value
    inst.wait_for_completion.assert_called_once_with("inst-3", timeout_s=1, poll_interval_s=1)


def test_bo_job_passes_schedule_params_through():
    db = _session()
    RunRepository(db).create_run("r-boj-params", "dev", "prod", {})
    JobRepository(db).create({
        "name": "refresh_sales",
        "description": "",
        "tags": [],
        "job_type": "bo_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"object_id": "3001", "schedule_params": {"prompt_values": {"region": "EMEA"}}},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-boj-params", ["refresh_sales"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.schedule_object.return_value = "inst-4"
        inst.wait_for_completion.return_value = TestStatus.PASSED
        executor.execute()

    inst.schedule_object.assert_called_once_with("3001", {"prompt_values": {"region": "EMEA"}})


def test_bo_job_fails_fast_when_live_connections_disabled():
    db = _session()
    RunRepository(db).create_run("r-boj-nolive", "dev", "prod", {})
    JobRepository(db).create({
        "name": "refresh_sales",
        "description": "",
        "tags": [],
        "job_type": "bo_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"object_id": "3001"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-boj-nolive", ["refresh_sales"],
        RunSettings(use_live_connections=False, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        executor.execute()
        MockBO.assert_not_called()

    run = RunRepository(db).get_run("r-boj-nolive")
    assert run.results[0].status == TestStatus.ERROR.value


def test_bo_job_chains_after_dependency_via_depends_on():
    db = _session()
    RunRepository(db).create_run("r-boj-chain", "dev", "prod", {})
    JobRepository(db).create({
        "name": "refresh_sales", "description": "", "tags": [],
        "job_type": "bo_job", "query": "", "key_columns": [], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"object_id": "3001"}, "enabled": True,
    })
    JobRepository(db).create({
        "name": "validate_sales", "description": "", "tags": [],
        "job_type": "bo_report", "query": "", "key_columns": [], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"report_id": "1001", "bo_report_id": "rpt-sales", "format": "csv"},
        "enabled": True, "depends_on": ["refresh_sales"],
    })
    executor = _make_executor(
        db, "r-boj-chain", ["refresh_sales", "validate_sales"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.BORestClient") as MockBO:
        inst = MockBO.return_value
        inst.schedule_object.return_value = "inst-5"
        inst.wait_for_completion.return_value = TestStatus.PASSED
        inst.download_report.return_value = b"id,sku\n1,A100\n"
        executor.execute()

    run = RunRepository(db).get_run("r-boj-chain")
    assert [r.query_name for r in run.results] == ["refresh_sales", "validate_sales"]
    assert all(r.status == TestStatus.PASSED.value for r in run.results)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_run_executor_live.py -v -k bo_job`
Expected: FAIL — `bo_job` isn't dispatched to any case builder; jobs either error with an unhandled path or hit the generic reconciliation fallback and fail on missing query/params.

- [ ] **Step 3: Wire the dispatcher and implement `_build_case_bo_job`**

In `api/services/run_executor.py`, add the dispatch branch right after the `automic_job` branch in `_build_case` (around line 463-464):

```python
        if job.job_type == "automic_job" and self._settings.use_live_connections:
            return self._build_case_automic(job)
        if job.job_type == "bo_job":
            if not self._settings.use_live_connections:
                def run_job() -> ReconciliationResult:
                    raise ValueError("bo_job jobs require live connections to be enabled")
                return run_job
            return self._build_case_bo_job(job)
```

Then add `_build_case_bo_job` right after `_build_case_bo_report` (after line 1253, before `_build_case_automic`):

```python
    def _build_case_bo_job(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            t0 = time.monotonic()
            creds = self._config_snapshot.get("bo_credentials", {})
            env = EnvironmentConfig(name=creds.get("name", "bo"), **{
                k: v for k, v in creds.items() if k != "name"
            })
            client = BORestClient(env)
            client.authenticate()
            try:
                instance_id = client.schedule_object(
                    job.params["object_id"], job.params.get("schedule_params"),
                )
                status = client.wait_for_completion(
                    instance_id,
                    timeout_s=job.params.get("timeout_s", 600),
                    poll_interval_s=job.params.get("poll_interval_s", 5),
                )
            finally:
                client.logout()
            return ReconciliationResult(
                query_name=job.name,
                source_env=self._source_env,
                target_env=self._target_env,
                source_row_count=0,
                target_row_count=0,
                matched_count=0,
                missing_in_target_count=0,
                missing_in_source_count=0,
                value_mismatch_count=0,
                mismatches=[],
                status=status,
                executed_at=datetime.now(timezone.utc),
                duration_seconds=time.monotonic() - t0,
            )
        return run_job
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_run_executor_live.py -v`
Expected: PASS (all tests in the file, including the pre-existing `bo_report`/`automic_job` ones and the new `bo_job` ones)

- [ ] **Step 5: Run the full unit suite to check for regressions**

Run: `pytest tests/unit -v`
Expected: PASS (no regressions in other job-type dispatch tests)

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_live.py
git commit -m "feat(jobs): wire bo_job into RunExecutor step dispatch"
```

---

## Task 6: Frontend — job modal support for `bo_job`

**Files:**
- Modify: `frontend/index.html:1172-1183` (job type select), `frontend/index.html:1469-1478` (new conditional param block, inserted after the `automic_job` block)
- Modify: `frontend/features/launch.js` — defaults (`openNewJobModal`, ~line 143), edit hydration (`openEditJobModal`, ~line 274), payload building (`_buildJobRequestBody`, ~line 518-521), save-gate (`canSaveJob`, ~line 652)

- [ ] **Step 1: Add `bo_job` to the Job Type select**

In `frontend/index.html`, in the Job Type `<select>` (around line 1172-1182), add the new option next to `automic_job`:

```html
          <select x-model="jobModal.job_type" class="field-input field-select" data-testid="job-modal-type-select">
            <option value="reconciliation">reconciliation</option>
            <option value="bo_report">bo_report</option>
            <option value="bo_job">bo_job</option>
            <option value="automic_job">automic_job</option>
            <option value="dbt_artifact">dbt_artifact</option>
            <option value="freshness">freshness</option>
```

(Leave the remaining existing `<option>` entries after `freshness` untouched.)

- [ ] **Step 2: Add the `bo_job` param block**

In `frontend/index.html`, immediately after the existing `automic_job` block (lines 1469-1478), add:

```html
        <div x-show="jobModal.job_type === 'bo_job'" class="grid-2">
          <div>
            <label class="field-label">BO Object ID (CUID)</label>
            <input x-model="jobModal.bo_job_object_id" class="field-input" placeholder="3001" data-testid="job-modal-bo-job-object-id-input" />
          </div>
          <div>
            <label class="field-label">Schedule Params (JSON, optional)</label>
            <input x-model="jobModal.bo_job_schedule_params_raw" class="field-input font-mono text-xs" placeholder='{"prompt_values": {"region": "EMEA"}}' />
          </div>
          <div>
            <label class="field-label">Poll Interval (seconds)</label>
            <input x-model="jobModal.bo_job_poll_interval_s" type="number" min="1" class="field-input" placeholder="5" />
          </div>
          <div>
            <label class="field-label">Timeout (seconds)</label>
            <input x-model="jobModal.bo_job_timeout_s" type="number" min="1" class="field-input" placeholder="600" />
          </div>
        </div>
```

- [ ] **Step 3: Add defaults in `openNewJobModal`**

In `frontend/features/launch.js`, in `openNewJobModal` (around line 143), add the new fields next to the existing `automic_job_name`/`automic_run_id` defaults:

```javascript
        automic_job_name: '', automic_run_id: '',
        bo_job_object_id: '', bo_job_schedule_params_raw: '',
        bo_job_poll_interval_s: '', bo_job_timeout_s: '',
```

- [ ] **Step 4: Add hydration in `openEditJobModal`**

In `frontend/features/launch.js`, in `openEditJobModal` (around line 274), add next to the existing `automic_job_name`/`automic_run_id` hydration:

```javascript
        automic_job_name: job.params?.job_name || '',
        automic_run_id: job.params?.run_id || '',
        bo_job_object_id: job.params?.object_id || '',
        bo_job_schedule_params_raw: job.params?.schedule_params ? JSON.stringify(job.params.schedule_params) : '',
        bo_job_poll_interval_s: job.params?.poll_interval_s ?? '',
        bo_job_timeout_s: job.params?.timeout_s ?? '',
```

- [ ] **Step 5: Add payload building in `_buildJobRequestBody`**

In `frontend/features/launch.js`, in `_buildJobRequestBody` (around line 518-521), add a new block next to the existing `automic_job` block:

```javascript
      if (m.job_type === 'automic_job') {
        if (m.automic_job_name) params.job_name = m.automic_job_name;
        if (m.automic_run_id) params.run_id = m.automic_run_id;
      }
      if (m.job_type === 'bo_job') {
        params.object_id = m.bo_job_object_id;
        if (m.bo_job_schedule_params_raw) {
          try {
            params.schedule_params = JSON.parse(m.bo_job_schedule_params_raw);
          } catch (e) {
            throw new Error('Schedule Params must be valid JSON');
          }
        }
        if (m.bo_job_poll_interval_s !== '') params.poll_interval_s = Number(m.bo_job_poll_interval_s) || 5;
        if (m.bo_job_timeout_s !== '') params.timeout_s = Number(m.bo_job_timeout_s) || 600;
      }
```

- [ ] **Step 6: Add the save-gate check in `canSaveJob`**

In `frontend/features/launch.js`, in `canSaveJob` (around line 651-652), add next to the existing `bo_report`/`automic_job` checks:

```javascript
      if (m.job_type === 'bo_report') return Boolean(m.bo_report_id && m.bo_page_id);
      if (m.job_type === 'bo_job') return Boolean(m.bo_job_object_id);
      if (m.job_type === 'automic_job') return Boolean(m.automic_job_name || m.automic_run_id);
```

- [ ] **Step 7: Manually verify in the browser**

Run: `python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload` (frontend is served directly by the FastAPI app — no separate dev server)

In the browser, open `http://127.0.0.1:8000`: go to the Launch tab, click "New Job", select Job Type = `bo_job`, confirm the BO Object ID / Schedule Params / Poll Interval / Timeout fields appear and the Save button stays disabled until Object ID is filled in. Fill in Object ID `3001`, save, reopen the job for editing, confirm the fields round-trip.

- [ ] **Step 8: Commit**

```bash
git add frontend/index.html frontend/features/launch.js
git commit -m "feat(ui): add bo_job job type to job editor modal"
```

---

## Task 7: SAP BO mock server — InfoStore schedule endpoints

The existing mock server (`docker/sapbo-mock/server.py`) backs `tests/integration/test_sapbo_mock_container.py` (gated by `RUN_LIVE_SAPBO_TESTS=1` + `docker-compose.integration.yml`). It needs InfoStore schedule/status endpoints so Task 8's integration test can exercise `schedule_object`/`wait_for_completion` against something that behaves like a real biprws server (multi-poll transition, not an instantly-terminal status).

**Files:**
- Modify: `docker/sapbo-mock/server.py`

- [ ] **Step 1: Add in-memory schedulable objects and instance state**

In `docker/sapbo-mock/server.py`, add near the other fixture data (after the `DATASETS` dict, around line 74):

```python
# Objects that can be scheduled via POST /biprws/infostore/{id}/schedules.
# Each entry's outcome is reached after SCHEDULE_POLLS_TO_TERMINAL polls of
# GET /biprws/infostore/{instance_id} -- first poll(s) return "Running" to
# exercise the client's poll loop, not just its terminal-status parsing.
SCHEDULABLE_OBJECTS = {
    "3001": "Success",
    "3002": "Failed",
}
SCHEDULE_POLLS_TO_TERMINAL = 2

# instance_id -> {"object_id": str, "polls_seen": int}
_SCHEDULE_INSTANCES: dict[str, dict] = {}
_next_instance_id = [0]
```

- [ ] **Step 2: Add the schedule POST handler**

In `docker/sapbo-mock/server.py`, in `do_POST` (around line 265-296), add a new branch before the final `self._send_json(HTTPStatus.NOT_FOUND, ...)` fallback:

```python
        schedule_match = re.fullmatch(r"/biprws/infostore/([^/]+)/schedules", path)
        if schedule_match:
            if not self._require_token():
                return
            object_id = schedule_match.group(1)
            if object_id not in SCHEDULABLE_OBJECTS:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"object {object_id} not found"})
                return
            _next_instance_id[0] += 1
            instance_id = f"inst-{_next_instance_id[0]}"
            _SCHEDULE_INSTANCES[instance_id] = {"object_id": object_id, "polls_seen": 0}
            self._send_json(HTTPStatus.OK, {"id": instance_id})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
```

(This replaces the previous final line of `do_POST` — the new branch goes immediately above it, and the fallback `self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})` stays as the last line of the method.)

- [ ] **Step 3: Add the instance-status GET handler**

In `docker/sapbo-mock/server.py`, in `do_GET` (around line 191-263), add a new branch before the final `self._send_json(HTTPStatus.NOT_FOUND, ...)` fallback (after the `content_match` block, around line 263):

```python
        instance_match = re.fullmatch(r"/biprws/infostore/([^/]+)", path)
        if instance_match:
            instance_id = instance_match.group(1)
            instance = _SCHEDULE_INSTANCES.get(instance_id)
            if instance is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"instance {instance_id} not found"})
                return
            instance["polls_seen"] += 1
            if instance["polls_seen"] < SCHEDULE_POLLS_TO_TERMINAL:
                self._send_json(HTTPStatus.OK, {"id": instance_id, "status": "Running"})
            else:
                terminal_status = SCHEDULABLE_OBJECTS[instance["object_id"]]
                self._send_json(HTTPStatus.OK, {"id": instance_id, "status": terminal_status})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
```

- [ ] **Step 4: Rebuild the mock container image**

Run: `docker compose -f docker-compose.integration.yml build sapbo`
Expected: build succeeds (no syntax errors in `server.py`)

- [ ] **Step 5: Commit**

```bash
git add docker/sapbo-mock/server.py
git commit -m "feat(sap-bo-mock): add InfoStore schedule/status endpoints"
```

---

## Task 8: Integration test against the SAP BO mock server

**Files:**
- Test: `tests/integration/test_sapbo_mock_container.py` (append — follow this file's existing `RUN_LIVE_SAPBO_TESTS` skip-gate pattern; read the top of the file first to match its exact fixture/base-URL setup before writing these tests)

- [ ] **Step 1: Write the integration tests**

This file has no shared client fixture — each existing test builds its own client inline via `_env()` + `BORestClient(_env())` + `client.authenticate()` (see `test_sapbo_mock_supports_bo_rest_client_flows`, lines 58-78). Append to `tests/integration/test_sapbo_mock_container.py` following that exact pattern:

```python
def test_schedule_and_wait_for_completion_success():
    _wait_for_sapbo()
    client = BORestClient(_env())
    client.authenticate()

    instance_id = client.schedule_object("3001")
    status = client.wait_for_completion(instance_id, timeout_s=5, poll_interval_s=0.1)
    assert status == TestStatus.PASSED


def test_schedule_and_wait_for_completion_failure():
    _wait_for_sapbo()
    client = BORestClient(_env())
    client.authenticate()

    instance_id = client.schedule_object("3002")
    status = client.wait_for_completion(instance_id, timeout_s=5, poll_interval_s=0.1)
    assert status == TestStatus.FAILED


def test_schedule_unknown_object_raises_bo_api_error():
    from etl_framework.exceptions import BOAPIError

    _wait_for_sapbo()
    client = BORestClient(_env())
    client.authenticate()

    with pytest.raises(BOAPIError):
        client.schedule_object("does-not-exist")
```

Add `from etl_framework.runner.state import TestStatus` to the file's existing import block at the top (after `from etl_framework.sap_bo.client import BORestClient`, line 11).

- [ ] **Step 2: Run the integration tests**

Run:
```bash
docker compose -f docker-compose.integration.yml up -d sapbo
RUN_LIVE_SAPBO_TESTS=1 pytest tests/integration/test_sapbo_mock_container.py -v -k schedule
docker compose -f docker-compose.integration.yml down
```
Expected: PASS (3 passed)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_sapbo_mock_container.py
git commit -m "test(sap-bo): cover schedule_object/wait_for_completion against mock server"
```

---

## Task 9: Full regression pass

- [ ] **Step 1: Run the full unit + integration suite**

Run: `pytest tests/unit tests/integration -v`
Expected: PASS, no regressions in any previously-passing test.

- [ ] **Step 2: Fix the spec doc's incorrect E2E commit references**

The spec (`docs/superpowers/specs/2026-07-24-sap-bo-job-execution-design.md`, Testing section) cites commits `e8eb057`/`75ccf0d` as prior SAP BO Playwright coverage — those commits are actually about live S3/SFTP multi-file reconciliation coverage, unrelated to SAP BO. Update that bullet to reference the real existing SAP BO e2e spec instead:

```markdown
- E2E: extend the existing SAP BO Playwright coverage
  (`tests/e2e/08a-compare-bo-report.spec.ts`) with a case that creates a
  `bo_job`, chains a dependent `bo_report` job after it via `depends_on`,
  and launches the sequence -- asserting the `bo_job` step completes before
  the `bo_report` step starts. (Superseded in practice by Task 8's
  integration-level coverage against the SAP BO mock server, which exercises
  the same schedule-and-wait behavior without needing browser automation;
  add the Playwright case only if UI-level coverage of the job modal's
  `bo_job` fields is also desired.)
```

- [ ] **Step 3: Commit the spec correction**

```bash
git add docs/superpowers/specs/2026-07-24-sap-bo-job-execution-design.md
git commit -m "docs(spec): correct SAP BO e2e coverage reference"
```
