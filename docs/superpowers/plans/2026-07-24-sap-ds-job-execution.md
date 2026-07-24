# SAP Data Services Job Execution ("ds_job") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `ds_job` job type that logs into SAP Data Services' Administrator/Management Console API, triggers a named batch job in a named repository, and waits for it to finish, so it can run as a step in an ETL framework job sequence alongside every other job type (`bo_job`, `automic_job`, `bo_report`, etc.), chained via the existing `depends_on` mechanism.

**Architecture:** Mirrors the existing `bo_job`/`BORestClient` pattern exactly: new `EnvironmentConfig` fields (`ds_*`), a new `DSRestClient` REST client module (`etl_framework/sap_ds/`) with lazy session-token auth, a `_build_case_ds_job` step builder wired into `RunExecutor`'s existing dispatcher, `ds_credentials` config-snapshot assembly (mirroring `bo_credentials`), `ds_job` added to the single-environment job-type set so it launches without a `target_env`, and job-modal + config-tab UI wiring. No new orchestration engine, no new API routes, no new CLI commands.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, pytest, requests, Alpine.js (frontend config/job modals), a local HTTP mock SAP DS server for integration tests.

**Spec:** `docs/superpowers/specs/2026-07-24-sap-ds-job-execution-design.md`

**API shape caveat (applies throughout):** SAP DS Administrator's REST API is not officially standardized the way SAP BO's `biprws` is. Every endpoint path, header name, and payload shape below is **best-effort**, modeled after commonly documented SAP DS Administrator conventions, and explicitly **not verified against a live SAP DS instance**. Every task below carries this caveat forward into code comments/docstrings — don't strip it out during implementation. This is the same approach `bo_job`'s schedule-status parsing already shipped with successfully.

---

## Task 1: `EnvironmentConfig` — add `ds_*` fields

**Files:**
- Modify: `etl_framework/config/models.py:10-13` (`SECRET_FIELDS`), `etl_framework/config/models.py:36-42` (field block, insert after `bo_*` fields), `etl_framework/config/models.py:72-77` (add a `ds_timeout` validator alongside `bo_timeout`'s)
- Test: `tests/unit/test_config_models_ds.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_config_models_ds.py`:

```python
from __future__ import annotations

import pytest

from etl_framework.config.models import EnvironmentConfig, SECRET_FIELDS


def test_ds_fields_default_to_empty():
    cfg = EnvironmentConfig(name="test", db_host="localhost", db_password="secret")
    assert cfg.ds_url == ""
    assert cfg.ds_user == ""
    assert cfg.ds_password == ""
    assert cfg.ds_repository == ""
    assert cfg.ds_timeout == 60
    assert cfg.ds_verify_ssl is True
    assert cfg.ds_proxy_url == ""


def test_ds_fields_can_be_set():
    cfg = EnvironmentConfig(
        name="test", db_host="localhost", db_password="secret",
        ds_url="http://ds-server:8080", ds_user="admin", ds_password="dspass",
        ds_repository="DS_REPO", ds_timeout=30, ds_verify_ssl=False,
        ds_proxy_url="http://proxy:8080",
    )
    assert cfg.ds_url == "http://ds-server:8080"
    assert cfg.ds_repository == "DS_REPO"
    assert cfg.ds_timeout == 30
    assert cfg.ds_verify_ssl is False


def test_ds_password_is_a_secret_field():
    assert "ds_password" in SECRET_FIELDS


def test_ds_timeout_must_be_positive():
    with pytest.raises(ValueError, match="must be > 0"):
        EnvironmentConfig(name="test", db_host="localhost", db_password="secret", ds_timeout=0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_config_models_ds.py -v`
Expected: FAIL — `ds_url` etc. are not valid `EnvironmentConfig` fields.

- [ ] **Step 3: Add the `ds_*` fields**

In `etl_framework/config/models.py`, change `SECRET_FIELDS` (lines 10-13):

```python
SECRET_FIELDS = frozenset({
    "db_password", "automic_password", "bo_password", "ds_password",
    "api_key", "bearer_token", "basic_password", "sap_bo_logon_token",
})
```

Then add the `ds_*` field block immediately after the existing `bo_*` block (after line 42, `bo_verify_ssl: bool = True`):

```python
    bo_url: str = ""
    bo_user: str = ""
    bo_password: str = ""
    bo_auth_type: str = "secEnterprise"
    bo_timeout: int = 60
    bo_proxy_url: str = ""
    bo_verify_ssl: bool = True
    ds_url: str = ""
    ds_user: str = ""
    ds_password: str = ""
    ds_repository: str = ""
    ds_timeout: int = 60
    ds_proxy_url: str = ""
    ds_verify_ssl: bool = True
```

- [ ] **Step 4: Add the `ds_timeout` validator**

In `etl_framework/config/models.py`, add a new validator right after `validate_bo_timeout` (after line 77):

```python
    @field_validator("bo_timeout")
    @classmethod
    def validate_bo_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v

    @field_validator("ds_timeout")
    @classmethod
    def validate_ds_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"must be > 0, got {v}")
        return v
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_config_models_ds.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Run the full unit suite to check for regressions**

Run: `pytest tests/unit -q`
Expected: PASS, no regressions (adding optional fields with defaults doesn't change existing `EnvironmentConfig` construction anywhere).

- [ ] **Step 7: Commit**

```bash
git add etl_framework/config/models.py tests/unit/test_config_models_ds.py
git commit -m "feat(config): add SAP DS connection fields to EnvironmentConfig"
```

---

## Task 2: `DSAPIError` exception

**Files:**
- Modify: `etl_framework/exceptions.py` (add after `BOAPIError`, around line 58-59)
- Test: `tests/unit/test_exceptions_ds.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_exceptions_ds.py`:

```python
from __future__ import annotations

from etl_framework.exceptions import DSAPIError, ETLFrameworkError


def test_ds_api_error_message_and_attrs():
    exc = DSAPIError(job_name="nightly_load", http_status=404, response_body="not found")
    assert exc.job_name == "nightly_load"
    assert exc.http_status == 404
    assert exc.response_body == "not found"
    assert "404" in str(exc)
    assert "nightly_load" in str(exc)
    assert isinstance(exc, ETLFrameworkError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_exceptions_ds.py -v`
Expected: FAIL — `ImportError: cannot import name 'DSAPIError'`

- [ ] **Step 3: Add `DSAPIError`**

In `etl_framework/exceptions.py`, add immediately after `BOAPIError` (after line 58):

```python
class BOAPIError(ETLFrameworkError):
    def __init__(self, report_id: str, http_status: int, response_body: str) -> None:
        self.report_id = report_id
        self.http_status = http_status
        self.response_body = response_body
        super().__init__(f"SAP BO API error {http_status} for report '{report_id}'")


class DSAPIError(ETLFrameworkError):
    def __init__(self, job_name: str, http_status: int, response_body: str) -> None:
        self.job_name = job_name
        self.http_status = http_status
        self.response_body = response_body
        super().__init__(f"SAP DS API error {http_status} for job '{job_name}'")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_exceptions_ds.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/exceptions.py tests/unit/test_exceptions_ds.py
git commit -m "feat(sap-ds): add DSAPIError exception"
```

---

## Task 3: `JobDefinition` schema — add `ds_job` job type

**Files:**
- Modify: `api/schemas.py:447-451` (job_type Literal), `api/schemas.py:476-478` (add branch after the `bo_job` branch in `validate_reconciliation_contract`)
- Test: `tests/unit/test_job_schema_ds_job.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_job_schema_ds_job.py`:

```python
from __future__ import annotations

import pytest

from api.schemas import JobDefinition


def test_ds_job_requires_job_name():
    with pytest.raises(ValueError, match="ds_job jobs require 'job_name' in params"):
        JobDefinition(name="nightly_load", job_type="ds_job", params={})


def test_ds_job_valid_with_job_name():
    job = JobDefinition(name="nightly_load", job_type="ds_job", params={"job_name": "DS_NIGHTLY_LOAD"})
    assert job.params["job_name"] == "DS_NIGHTLY_LOAD"


def test_ds_job_accepts_optional_repository_and_params():
    job = JobDefinition(
        name="nightly_load",
        job_type="ds_job",
        params={
            "job_name": "DS_NIGHTLY_LOAD",
            "repository": "DS_REPO_2",
            "job_params": {"$G_RUN_DATE": "2026-07-24"},
            "poll_interval_s": 2,
            "timeout_s": 120,
        },
    )
    assert job.params["repository"] == "DS_REPO_2"
    assert job.params["job_params"] == {"$G_RUN_DATE": "2026-07-24"}
    assert job.params["poll_interval_s"] == 2
    assert job.params["timeout_s"] == 120
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_job_schema_ds_job.py -v`
Expected: FAIL — `ds_job` is not a valid `job_type` literal.

- [ ] **Step 3: Add `ds_job` to the `job_type` Literal**

In `api/schemas.py`, change (lines 447-451):

```python
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile", "api_reconciliation",
        "bo_job",
    ] = "reconciliation"
```

to:

```python
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile", "api_reconciliation",
        "bo_job", "ds_job",
    ] = "reconciliation"
```

- [ ] **Step 4: Add the `ds_job` validation branch**

In `api/schemas.py`, in `validate_reconciliation_contract`, add a new `elif` branch immediately after the existing `bo_job` branch (lines 476-478):

```python
        elif self.job_type == "bo_job":
            if not self.params.get("object_id"):
                raise ValueError("bo_job jobs require 'object_id' in params")
        elif self.job_type == "ds_job":
            if not self.params.get("job_name"):
                raise ValueError("ds_job jobs require 'job_name' in params")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_job_schema_ds_job.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add api/schemas.py tests/unit/test_job_schema_ds_job.py
git commit -m "feat(jobs): add ds_job job type to JobDefinition schema"
```

---

## Task 4: `job_validation.py` — add `ds_job` branch

**Files:**
- Modify: `etl_framework/runner/job_validation.py:83-85` (add branch after the `bo_job` branch)
- Test: `tests/unit/test_job_validation.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_job_validation.py`:

```python
def test_ds_job_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "nightly_load",
        "job_type": "ds_job",
        "params": {"job_name": "DS_NIGHTLY_LOAD"},
    })
    assert issues == []


def test_ds_job_requires_job_name():
    issues = validate_job_definition({
        "name": "nightly_load",
        "job_type": "ds_job",
        "params": {},
    })
    assert any(issue.field == "params.job_name" for issue in issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_job_validation.py -v -k ds_job`
Expected: FAIL — `ds_job` isn't recognized, no issue is produced for the missing-name case.

- [ ] **Step 3: Add the `ds_job` branch**

In `etl_framework/runner/job_validation.py`, add a new `elif` branch immediately after the existing `bo_job` branch (lines 83-85):

```python
    elif job_type == "bo_job":
        if not params.get("object_id"):
            issues.append(ValidationIssue("params.object_id", "bo_job jobs require object_id"))
    elif job_type == "ds_job":
        if not params.get("job_name"):
            issues.append(ValidationIssue("params.job_name", "ds_job jobs require job_name"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_job_validation.py -v -k ds_job`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/job_validation.py tests/unit/test_job_validation.py
git commit -m "feat(jobs): validate ds_job params in job_validation"
```

---

## Task 5: `DSRestClient` — module scaffold, login/logout, trigger_job

**Files:**
- Create: `etl_framework/sap_ds/__init__.py` (empty, mirrors `etl_framework/sap_bo/__init__.py`)
- Create: `etl_framework/sap_ds/client.py`
- Test: `tests/unit/test_ds_rest_client.py` (create)

- [ ] **Step 1: Create the empty package file**

Create `etl_framework/sap_ds/__init__.py` with no content (empty file, matching `etl_framework/sap_bo/__init__.py`).

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_ds_rest_client.py`:

```python
"""Tests for DSRestClient SAP Data Services Administrator API methods.

Endpoint paths, header names, and payload shapes here are best-effort,
modeled after commonly documented SAP DS Administrator conventions -- not
verified against a live SAP DS instance. Verify and adjust when a real
server is available, the same way etl_framework/sap_bo/client.py's biprws
quirks were discovered and documented over time.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from etl_framework.config.models import EnvironmentConfig


@pytest.fixture
def env_config():
    return EnvironmentConfig(
        name="test",
        db_host="localhost",
        db_password="secret",
        ds_url="http://ds.example.com",
        ds_user="admin",
        ds_password="dspass",
        ds_repository="DS_REPO",
        ds_timeout=30,
    )


@pytest.fixture
def authenticated_client(env_config):
    from etl_framework.sap_ds.client import DSRestClient
    client = DSRestClient(env_config)
    client._token = "fake-ds-token-123"
    client._session.headers.update({"X-DS-SessionToken": "fake-ds-token-123"})
    return client


def test_client_requires_url_scheme(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_url": "ds.example.com"})
    with pytest.raises(ValueError, match="must include http:// or https://"):
        DSRestClient(cfg)


def test_client_applies_proxy_and_ssl_verification_config(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(
        update={"ds_proxy_url": "http://proxy.example.com:8080", "ds_verify_ssl": False}
    )
    client = DSRestClient(cfg)

    assert client._session.proxies["https"] == "http://proxy.example.com:8080"
    assert client._session.proxies["http"] == "http://proxy.example.com:8080"
    assert client._verify_ssl is False


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

def test_login_posts_credentials_and_stores_token(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.headers = {"X-DS-SessionToken": "tok"}
    with patch.object(client._session, "post", return_value=mock_response) as mock_post:
        token = client.login()

    assert token == "tok"
    assert client._token == "tok"
    called_url = mock_post.call_args[0][0]
    assert called_url == "http://ds.example.com/Login"
    sent_payload = mock_post.call_args[1]["json"]
    assert sent_payload == {"userName": "admin", "password": "dspass"}


def test_login_raises_ds_api_error_on_http_failure(env_config):
    from etl_framework.exceptions import DSAPIError
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = "invalid credentials"
    with patch.object(client._session, "post", return_value=mock_response):
        with pytest.raises(DSAPIError) as exc_info:
            client.login()
    assert exc_info.value.http_status == 401


def test_logout_posts_logoff_and_clears_token(authenticated_client):
    authenticated_client._owns_token = True
    mock_response = MagicMock()
    mock_response.status_code = 200
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.logout()

    mock_post.assert_called_once()
    assert authenticated_client._token is None
    assert "X-DS-SessionToken" not in authenticated_client._session.headers


def test_logout_is_noop_when_not_authenticated(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    with patch.object(client._session, "post") as mock_post:
        client.logout()
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# trigger_job
# ---------------------------------------------------------------------------

def test_trigger_job_posts_to_execute_endpoint_using_default_repository(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-42"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        run_id = authenticated_client.trigger_job("DS_NIGHTLY_LOAD")

    assert run_id == "run-42"
    called_url = mock_post.call_args[0][0]
    assert called_url == "http://ds.example.com/BatchJob/DS_REPO/DS_NIGHTLY_LOAD/Execute"


def test_trigger_job_uses_explicit_repository_override(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-43"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.trigger_job("DS_NIGHTLY_LOAD", repository="OTHER_REPO")

    called_url = mock_post.call_args[0][0]
    assert called_url == "http://ds.example.com/BatchJob/OTHER_REPO/DS_NIGHTLY_LOAD/Execute"


def test_trigger_job_sends_job_params_as_json_body(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-44"}
    with patch.object(authenticated_client._session, "post", return_value=mock_response) as mock_post:
        authenticated_client.trigger_job("DS_NIGHTLY_LOAD", job_params={"$G_RUN_DATE": "2026-07-24"})

    assert mock_post.call_args[1]["json"] == {"$G_RUN_DATE": "2026-07-24"}


def test_trigger_job_authenticates_first_if_no_token(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    client = DSRestClient(env_config)
    login_response = MagicMock()
    login_response.status_code = 200
    login_response.headers = {"X-DS-SessionToken": "tok"}
    trigger_response = MagicMock()
    trigger_response.status_code = 200
    trigger_response.json.return_value = {"id": "run-1"}
    with patch.object(client._session, "post", side_effect=[login_response, trigger_response]):
        run_id = client.trigger_job("DS_NIGHTLY_LOAD")

    assert run_id == "run-1"
    assert client._token == "tok"


def test_trigger_job_raises_ds_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "job not found"
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(DSAPIError):
            authenticated_client.trigger_job("does-not-exist")


def test_trigger_job_raises_ds_api_error_when_response_has_no_id(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {}
    with patch.object(authenticated_client._session, "post", return_value=mock_response):
        with pytest.raises(DSAPIError):
            authenticated_client.trigger_job("DS_NIGHTLY_LOAD")


def test_trigger_job_raises_value_error_when_no_repository_available(env_config):
    from etl_framework.sap_ds.client import DSRestClient

    cfg = env_config.model_copy(update={"ds_repository": ""})
    client = DSRestClient(cfg)
    client._token = "tok"
    with pytest.raises(ValueError, match="repository"):
        client.trigger_job("DS_NIGHTLY_LOAD")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_ds_rest_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_framework.sap_ds.client'`

- [ ] **Step 4: Implement `DSRestClient` (login/logout/trigger_job)**

Create `etl_framework/sap_ds/client.py`:

```python
import logging
import time
import requests
from urllib.parse import urlparse
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import DSAPIError
from etl_framework.runner.state import TestStatus

logger = logging.getLogger("etl_framework.sap_ds.client")


class DSRestClient:
    """Client for SAP Data Services' Administrator/Management Console API.

    Endpoint paths, the session-token header name, and request/response
    payload shapes are best-effort, modeled after commonly documented SAP DS
    Administrator conventions -- not verified against a live SAP DS
    instance. Verify and adjust while integrating against a real server, the
    same way etl_framework/sap_bo/client.py's on-premises biprws quirks
    (_unwrap_collection, _paginate_biprws_collection) were discovered and
    documented over time rather than assumed correct up front.
    """

    LOGIN_ENDPOINT = "/Login"
    TRIGGER_ENDPOINT = "/BatchJob/{repository}/{job_name}/Execute"
    STATUS_ENDPOINT = "/BatchJob/{repository}/status/{run_id}"
    SESSION_TOKEN_HEADER = "X-DS-SessionToken"

    STATUS_MAP: dict[str, TestStatus] = {
        "COMPLETED": TestStatus.PASSED,
        "SUCCESS": TestStatus.PASSED,
        "ERROR": TestStatus.FAILED,
        "FAILED": TestStatus.FAILED,
        "CANCELLED": TestStatus.FAILED,
        "RUNNING": TestStatus.RUNNING,
        "PENDING": TestStatus.RUNNING,
        "QUEUED": TestStatus.RUNNING,
    }

    def __init__(self, env_config: EnvironmentConfig):
        self._base_url = env_config.ds_url.rstrip("/")
        if self._base_url and not urlparse(self._base_url).scheme:
            raise ValueError("SAP DS URL must include http:// or https://")
        self._user = env_config.ds_user
        self._password = env_config.ds_password
        self._default_repository = env_config.ds_repository
        self._timeout = env_config.ds_timeout
        self._token: str | None = None
        self._owns_token = False
        self._session = requests.Session()
        self._verify_ssl = env_config.ds_verify_ssl
        proxy_url = env_config.ds_proxy_url.strip()
        if proxy_url:
            self._session.proxies.update({"http": proxy_url, "https": proxy_url})

    def login(self, username: str | None = None, password: str | None = None) -> str | None:
        url = f"{self._base_url}{self.LOGIN_ENDPOINT}"
        payload = {
            "userName": self._user if username is None else username,
            "password": self._password if password is None else password,
        }
        logger.debug("Authenticating with SAP DS Administrator API")
        response = self._session.post(
            url,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise DSAPIError(
                job_name="<login>", http_status=response.status_code, response_body=response.text,
            )
        self._token = response.headers.get(self.SESSION_TOKEN_HEADER)
        if self._token:
            self._owns_token = True
            self._session.headers.update({self.SESSION_TOKEN_HEADER: self._token})
        return self._token

    def logout(self) -> None:
        if self._token and self._owns_token:
            self._session.post(
                f"{self._base_url}/Logout",
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
        if self._token:
            self._session.headers.pop(self.SESSION_TOKEN_HEADER, None)
        self._token = None
        self._owns_token = False

    def trigger_job(
        self, job_name: str, repository: str | None = None, job_params: dict | None = None,
    ) -> str:
        """POST {repository}/{job_name}/Execute -- trigger a SAP DS batch job
        run in the given repository (falling back to the EnvironmentConfig's
        ds_repository if none is given). job_params is passed through as the
        JSON body for job substitution/global variables. Returns the new run
        id.

        Response shape is best-effort, assumes {"id": "<run_id>"}, matching
        the convention BORestClient.schedule_object already uses.
        """
        if not self._token:
            self.login()
        repo = repository or self._default_repository
        if not repo:
            raise ValueError(
                "ds_job requires a repository: set 'ds_repository' in the environment config "
                "or 'repository' in the job's params",
            )
        url = f"{self._base_url}{self.TRIGGER_ENDPOINT.format(repository=repo, job_name=job_name)}"
        response = self._session.post(
            url,
            json=job_params or {},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise DSAPIError(
                job_name=job_name, http_status=response.status_code, response_body=response.text,
            )
        run_id = str(response.json().get("id", ""))
        if not run_id:
            raise DSAPIError(
                job_name=job_name, http_status=response.status_code,
                response_body="trigger response missing 'id'",
            )
        return run_id
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_ds_rest_client.py -v`
Expected: PASS (13 passed) — note `STATUS_MAP`/`TestStatus`/`time` are unused by this task's own tests but are already in place for Task 6.

- [ ] **Step 6: Commit**

```bash
git add etl_framework/sap_ds/__init__.py etl_framework/sap_ds/client.py tests/unit/test_ds_rest_client.py
git commit -m "feat(sap-ds): add DSRestClient login/logout/trigger_job"
```

---

## Task 6: `DSRestClient` — `get_job_status` + `wait_for_completion`

**Files:**
- Modify: `etl_framework/sap_ds/client.py`
- Test: `tests/unit/test_ds_rest_client.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_ds_rest_client.py`:

```python
# ---------------------------------------------------------------------------
# get_job_status / wait_for_completion
# ---------------------------------------------------------------------------

from etl_framework.runner.state import TestStatus


@pytest.mark.parametrize("raw_status,expected", [
    ("Completed", TestStatus.PASSED),
    ("completed", TestStatus.PASSED),
    ("Success", TestStatus.PASSED),
    ("Error", TestStatus.FAILED),
    ("Failed", TestStatus.FAILED),
    ("Cancelled", TestStatus.FAILED),
    ("Running", TestStatus.RUNNING),
    ("Pending", TestStatus.RUNNING),
    ("Queued", TestStatus.RUNNING),
])
def test_get_job_status_maps_known_statuses(authenticated_client, raw_status, expected):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-42", "status": raw_status}
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        status = authenticated_client.get_job_status("run-42")

    assert status == expected
    called_url = mock_get.call_args[0][0]
    assert called_url == "http://ds.example.com/BatchJob/DS_REPO/status/run-42"


def test_get_job_status_uses_repository_override(authenticated_client):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-42", "status": "Completed"}
    with patch.object(authenticated_client._session, "get", return_value=mock_response) as mock_get:
        authenticated_client.get_job_status("run-42", repository="OTHER_REPO")

    called_url = mock_get.call_args[0][0]
    assert called_url == "http://ds.example.com/BatchJob/OTHER_REPO/status/run-42"


def test_get_job_status_treats_unrecognized_status_as_running(authenticated_client, caplog):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "run-42", "status": "SomeNewDSStatus"}
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with caplog.at_level("WARNING"):
            status = authenticated_client.get_job_status("run-42")

    assert status == TestStatus.RUNNING
    assert "SomeNewDSStatus" in caplog.text


def test_get_job_status_raises_ds_api_error_on_http_failure(authenticated_client):
    from etl_framework.exceptions import DSAPIError

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "server error"
    with patch.object(authenticated_client._session, "get", return_value=mock_response):
        with pytest.raises(DSAPIError):
            authenticated_client.get_job_status("run-42")


def test_wait_for_completion_returns_immediately_on_success(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.PASSED) as mock_get:
        status = authenticated_client.wait_for_completion("run-42", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    mock_get.assert_called_once_with("run-42", repository=None)


def test_wait_for_completion_polls_until_terminal_status(authenticated_client):
    with patch.object(
        authenticated_client, "get_job_status",
        side_effect=[TestStatus.RUNNING, TestStatus.RUNNING, TestStatus.PASSED],
    ) as mock_get:
        status = authenticated_client.wait_for_completion("run-42", timeout_s=5, poll_interval_s=0.01)

    assert status == TestStatus.PASSED
    assert mock_get.call_count == 3


def test_wait_for_completion_raises_timeout_error_when_never_terminal(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.RUNNING):
        with pytest.raises(TimeoutError, match="run-42"):
            authenticated_client.wait_for_completion("run-42", timeout_s=0.05, poll_interval_s=0.01)


def test_wait_for_completion_passes_repository_override_through(authenticated_client):
    with patch.object(authenticated_client, "get_job_status", return_value=TestStatus.PASSED) as mock_get:
        authenticated_client.wait_for_completion("run-42", repository="OTHER_REPO", timeout_s=5, poll_interval_s=0.01)

    mock_get.assert_called_once_with("run-42", repository="OTHER_REPO")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_ds_rest_client.py -v -k "job_status or wait_for_completion"`
Expected: FAIL — `AttributeError: 'DSRestClient' object has no attribute 'get_job_status'`

- [ ] **Step 3: Implement `get_job_status` and `wait_for_completion`**

In `etl_framework/sap_ds/client.py`, add both methods right after `trigger_job` (before end of class):

```python
    def _normalise_job_status(self, raw_status: str) -> TestStatus:
        mapped = self.STATUS_MAP.get(raw_status.upper())
        if mapped is None:
            logger.warning(
                "Unrecognized SAP DS job status %r, treating as still running", raw_status,
            )
            return TestStatus.RUNNING
        return mapped

    def get_job_status(self, run_id: str, repository: str | None = None) -> TestStatus:
        """GET {repository}/status/{run_id} -- fetch the current status of a
        triggered batch job run and map it to TestStatus. Non-terminal DS
        states (Running/Pending/Queued) and any unrecognized status string
        both map to TestStatus.RUNNING, so callers keep polling instead of
        mis-reading an unknown state as done."""
        if not self._token:
            self.login()
        repo = repository or self._default_repository
        if not repo:
            raise ValueError(
                "ds_job requires a repository: set 'ds_repository' in the environment config "
                "or 'repository' in the job's params",
            )
        url = f"{self._base_url}{self.STATUS_ENDPOINT.format(repository=repo, run_id=run_id)}"
        response = self._session.get(
            url,
            headers={"Accept": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise DSAPIError(
                job_name=run_id, http_status=response.status_code, response_body=response.text,
            )
        return self._normalise_job_status(str(response.json().get("status", "")))

    def wait_for_completion(
        self, run_id: str, repository: str | None = None,
        timeout_s: float = 600, poll_interval_s: float = 5,
    ) -> TestStatus:
        """Poll get_job_status until it returns a terminal status
        (PASSED/FAILED) or timeout_s elapses. Raises TimeoutError if the run
        never reaches a terminal status in time -- callers treat that as a
        run error, not a job failure."""
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.get_job_status(run_id, repository=repository)
            if status != TestStatus.RUNNING:
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"SAP DS job run '{run_id}' did not complete within {timeout_s}s",
                )
            time.sleep(poll_interval_s)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_ds_rest_client.py -v`
Expected: PASS (all tests in the file, including Task 5's earlier ones)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/sap_ds/client.py tests/unit/test_ds_rest_client.py
git commit -m "feat(sap-ds): add DSRestClient.get_job_status and wait_for_completion"
```

---

## Task 7: `RunExecutor` dispatch + `ds_credentials` snapshot + single-env job type

This task bundles three small, tightly-related wiring pieces that together make `ds_job` runnable through the exact same generic paths every other job type uses — split into separate steps below but committed together since they're only meaningful in combination (a prior similar feature, `bo_job`, initially missed the second and third pieces and needed a follow-up fix once discovered in final review; doing all three from the start here avoids repeating that gap).

**Files:**
- Modify: `api/services/run_executor.py:461-470` (dispatcher), add new method after `_build_case_bo_job` (after line 1296)
- Modify: `api/routes/runs.py:276-279` (`ds_credentials` snapshot assembly, alongside `bo_credentials`/`automic_credentials`)
- Modify: `api/routes/selections.py:27` (`_SINGLE_ENV_JOB_TYPES`)
- Test: `tests/unit/test_run_executor_live.py` (append), `tests/unit/test_selections_routes.py` (append)

- [ ] **Step 1: Write the failing `RunExecutor` dispatch tests**

Append to `tests/unit/test_run_executor_live.py` (reuses `_LIVE_SNAPSHOT`, `_make_executor`, `_session` already defined at the top of this file). First, add a `ds_credentials` entry to `_LIVE_SNAPSHOT` (module-level dict, around line 29-61):

```python
_LIVE_SNAPSHOT = {
    "source_credentials": {
        "name": "dev",
        "db_host": "dev-sql",
        "db_port": 1433,
        "db_name": "etl_db",
        "db_user": "sa",
        "db_password": "secret",
    },
    "target_credentials": {
        "name": "prod",
        "db_host": "prod-sql",
        "db_port": 1433,
        "db_name": "etl_db",
        "db_user": "sa",
        "db_password": "secret",
    },
    "bo_credentials": {
        "name": "bo",
        "db_host": "bo-host",
        "db_password": "bo-secret",
        "bo_url": "http://bo-server",
        "bo_user": "admin",
    },
    "automic_credentials": {
        "name": "ac",
        "db_host": "ac-host",
        "db_password": "ac-secret",
        "automic_url": "http://automic",
        "automic_user": "admin",
        "automic_password": "pass",
    },
    "ds_credentials": {
        "name": "ds",
        "db_host": "ds-host",
        "db_password": "ds-secret",
        "ds_url": "http://ds-server",
        "ds_user": "admin",
        "ds_password": "ds-secret",
        "ds_repository": "DS_REPO",
    },
}
```

Then append these tests to the file:

```python
# ---------------------------------------------------------------------------
# ds_job dispatch
# ---------------------------------------------------------------------------

def test_ds_job_returns_passed_on_success():
    db = _session()
    RunRepository(db).create_run("r-dsj", "dev", "prod", {})
    JobRepository(db).create({
        "name": "nightly_load",
        "description": "",
        "tags": [],
        "job_type": "ds_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"job_name": "DS_NIGHTLY_LOAD"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-dsj", ["nightly_load"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.DSRestClient") as MockDS:
        inst = MockDS.return_value
        inst.trigger_job.return_value = "run-1"
        inst.wait_for_completion.return_value = TestStatus.PASSED
        executor.execute()

    run = RunRepository(db).get_run("r-dsj")
    assert run.results[0].status == TestStatus.PASSED.value
    inst.trigger_job.assert_called_once_with("DS_NIGHTLY_LOAD", None, None)
    inst.wait_for_completion.assert_called_once_with("run-1", repository=None, timeout_s=600, poll_interval_s=5)
    inst.logout.assert_called_once()


def test_ds_job_returns_failed_when_ds_reports_failure():
    db = _session()
    RunRepository(db).create_run("r-dsj-fail", "dev", "prod", {})
    JobRepository(db).create({
        "name": "nightly_load",
        "description": "",
        "tags": [],
        "job_type": "ds_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"job_name": "DS_NIGHTLY_LOAD"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-dsj-fail", ["nightly_load"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.DSRestClient") as MockDS:
        inst = MockDS.return_value
        inst.trigger_job.return_value = "run-2"
        inst.wait_for_completion.return_value = TestStatus.FAILED
        executor.execute()

    run = RunRepository(db).get_run("r-dsj-fail")
    assert run.results[0].status == TestStatus.FAILED.value


def test_ds_job_returns_error_on_timeout():
    db = _session()
    RunRepository(db).create_run("r-dsj-timeout", "dev", "prod", {})
    JobRepository(db).create({
        "name": "nightly_load",
        "description": "",
        "tags": [],
        "job_type": "ds_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"job_name": "DS_NIGHTLY_LOAD", "timeout_s": 1, "poll_interval_s": 1},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-dsj-timeout", ["nightly_load"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.DSRestClient") as MockDS:
        inst = MockDS.return_value
        inst.trigger_job.return_value = "run-3"
        inst.wait_for_completion.side_effect = TimeoutError("did not complete")
        executor.execute()

    run = RunRepository(db).get_run("r-dsj-timeout")
    assert run.results[0].status == TestStatus.ERROR.value
    inst.wait_for_completion.assert_called_once_with("run-3", repository=None, timeout_s=1, poll_interval_s=1)


def test_ds_job_passes_repository_and_job_params_through():
    db = _session()
    RunRepository(db).create_run("r-dsj-params", "dev", "prod", {})
    JobRepository(db).create({
        "name": "nightly_load",
        "description": "",
        "tags": [],
        "job_type": "ds_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {
            "job_name": "DS_NIGHTLY_LOAD",
            "repository": "OTHER_REPO",
            "job_params": {"$G_RUN_DATE": "2026-07-24"},
        },
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-dsj-params", ["nightly_load"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.DSRestClient") as MockDS:
        inst = MockDS.return_value
        inst.trigger_job.return_value = "run-4"
        inst.wait_for_completion.return_value = TestStatus.PASSED
        executor.execute()

    inst.trigger_job.assert_called_once_with(
        "DS_NIGHTLY_LOAD", "OTHER_REPO", {"$G_RUN_DATE": "2026-07-24"},
    )
    inst.wait_for_completion.assert_called_once_with(
        "run-4", repository="OTHER_REPO", timeout_s=600, poll_interval_s=5,
    )


def test_ds_job_fails_fast_when_live_connections_disabled():
    db = _session()
    RunRepository(db).create_run("r-dsj-nolive", "dev", "prod", {})
    JobRepository(db).create({
        "name": "nightly_load",
        "description": "",
        "tags": [],
        "job_type": "ds_job",
        "query": "",
        "key_columns": [],
        "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"job_name": "DS_NIGHTLY_LOAD"},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-dsj-nolive", ["nightly_load"],
        RunSettings(use_live_connections=False, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.DSRestClient") as MockDS:
        executor.execute()
        MockDS.assert_not_called()

    run = RunRepository(db).get_run("r-dsj-nolive")
    assert run.results[0].status == TestStatus.ERROR.value


def test_ds_job_chains_after_dependency_via_depends_on():
    db = _session()
    RunRepository(db).create_run("r-dsj-chain", "dev", "prod", {})
    JobRepository(db).create({
        "name": "nightly_load", "description": "", "tags": [],
        "job_type": "ds_job", "query": "", "key_columns": [], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"job_name": "DS_NIGHTLY_LOAD"}, "enabled": True,
    })
    JobRepository(db).create({
        "name": "check_automic", "description": "", "tags": [],
        "job_type": "automic_job", "query": "", "key_columns": [], "exclude_columns": [],
        "source_env": None, "target_env": None,
        "params": {"job_name": "ETL_NIGHTLY", "depends_on": ["nightly_load"]},
        "enabled": True,
    })
    executor = _make_executor(
        db, "r-dsj-chain", ["nightly_load", "check_automic"],
        RunSettings(use_live_connections=True, metrics_enabled=False),
        snapshot=_LIVE_SNAPSHOT,
    )

    with patch("api.services.run_executor.DSRestClient") as MockDS, \
         patch("api.services.run_executor.AutomicClient") as MockAC:
        ds_inst = MockDS.return_value
        ds_inst.trigger_job.return_value = "run-5"
        ds_inst.wait_for_completion.return_value = TestStatus.PASSED
        ac_status = MagicMock()
        ac_status.status = TestStatus.PASSED
        MockAC.return_value.get_status_by_job_name.return_value = ac_status
        executor.execute()

    run = RunRepository(db).get_run("r-dsj-chain")
    assert [r.query_name for r in run.results] == ["nightly_load", "check_automic"]
    assert all(r.status == TestStatus.PASSED.value for r in run.results)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_run_executor_live.py -v -k ds_job`
Expected: FAIL — `ds_job` isn't dispatched to any case builder; `DSRestClient` isn't imported in `run_executor.py` yet.

- [ ] **Step 3: Wire the dispatcher and implement `_build_case_ds_job`**

In `api/services/run_executor.py`, add the import near the existing `BORestClient` import (check the import block around line 23; add alongside it):

```python
from etl_framework.sap_bo.client import BORestClient
from etl_framework.sap_ds.client import DSRestClient
```

Add the dispatch branch right after the `bo_job` branch in `_build_case` (lines 465-470):

```python
        if job.job_type == "bo_job":
            if not self._settings.use_live_connections:
                def run_job() -> ReconciliationResult:
                    raise ValueError("bo_job jobs require live connections to be enabled")
                return run_job
            return self._build_case_bo_job(job)
        if job.job_type == "ds_job":
            if not self._settings.use_live_connections:
                def run_job() -> ReconciliationResult:
                    raise ValueError("ds_job jobs require live connections to be enabled")
                return run_job
            return self._build_case_ds_job(job)
```

Then add `_build_case_ds_job` right after `_build_case_bo_job` (after line 1296, before `_build_case_automic`):

```python
    def _build_case_ds_job(self, job: JobDefinition):
        def run_job() -> ReconciliationResult:
            t0 = time.monotonic()
            creds = self._config_snapshot.get("ds_credentials", {})
            env = EnvironmentConfig(name=creds.get("name", "ds"), **{
                k: v for k, v in creds.items() if k != "name"
            })
            client = DSRestClient(env)
            client.login()
            try:
                run_id = client.trigger_job(
                    job.params["job_name"],
                    job.params.get("repository"),
                    job.params.get("job_params"),
                )
                status = client.wait_for_completion(
                    run_id,
                    repository=job.params.get("repository"),
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

- [ ] **Step 4: Add `ds_credentials` snapshot assembly**

In `api/routes/runs.py`, add next to the existing `bo_credentials`/`automic_credentials` assembly (lines 276-279):

```python
    if "bo_credentials" not in snapshot:
        snapshot["bo_credentials"] = {"name": "bo", **cfg_data}
    if "automic_credentials" not in snapshot:
        snapshot["automic_credentials"] = {"name": "automic", **cfg_data}
    if "ds_credentials" not in snapshot:
        snapshot["ds_credentials"] = {"name": "ds", **cfg_data}
    return snapshot
```

- [ ] **Step 5: Add `ds_job` to `_SINGLE_ENV_JOB_TYPES`**

In `api/routes/selections.py`, change line 27:

```python
_SINGLE_ENV_JOB_TYPES = {"bo_report", "freshness", "profile", "automic_job", "dbt_artifact", "schema_snapshot", "bo_job", "ds_job"}
```

- [ ] **Step 6: Write the failing single-env selection test**

In `tests/unit/test_selections_routes.py`, the `client` fixture (lines 11-46) seeds three jobs via `JobRepository(db).create({...})` inside a `with Session(engine) as db:` block (lines 28-44), ending with the `bo_job_trigger` job (lines 40-44). Add a fourth job right after it, inside the same `with` block:

```python
        JobRepository(db).create({
            "name": "bo_job_trigger", "description": "", "tags": [],
            "job_type": "bo_job", "query": "", "key_columns": [],
            "exclude_columns": [], "params": {"object_id": "3001"}, "enabled": True,
        })
        JobRepository(db).create({
            "name": "ds_job_trigger", "description": "", "tags": [],
            "job_type": "ds_job", "query": "", "key_columns": [],
            "exclude_columns": [], "params": {"job_name": "DS_NIGHTLY_LOAD"}, "enabled": True,
        })
```

Then append this test at the end of the file, mirroring `test_launch_bo_job_job_type_succeeds_without_target` (lines 115-118) exactly:

```python
def test_launch_ds_job_job_type_succeeds_without_target(client):
    created = _create_selection(client, name="ds-job-only", jobs=["ds_job_trigger"])
    resp = client.post(f"/api/selections/{created['id']}/launch", json={"source_env": "dev"})
    assert resp.status_code == 202
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/unit/test_run_executor_live.py tests/unit/test_selections_routes.py -v`
Expected: PASS (all tests, including pre-existing ones)

- [ ] **Step 8: Run the full unit suite to check for regressions**

Run: `pytest tests/unit -q`
Expected: PASS, no regressions.

- [ ] **Step 9: Commit**

```bash
git add api/services/run_executor.py api/routes/runs.py api/routes/selections.py tests/unit/test_run_executor_live.py tests/unit/test_selections_routes.py
git commit -m "feat(jobs): wire ds_job into RunExecutor dispatch, credentials snapshot, and single-env launch"
```

---

## Task 8: Frontend — Config tab SAP DS connection fields

**Files:**
- Modify: `frontend/partials/tab-config.html:602-621` (new SAP DS fields block, inserted after the existing SAP BO block)
- Modify: `frontend/features/config.js` — defaults (`openNewConfigModal`, ~line 57-71), edit hydration (`editConfig`, ~line 73-132), payload building (`_configDataFromModal`, ~line 134+)

- [ ] **Step 1: Add the SAP DS fields block to the Config tab partial**

In `frontend/partials/tab-config.html`, immediately after the existing SAP BO `grid-2` block (after line 621, before the next `<div class="divider"></div>` at line 622), add:

```html
        <div class="divider"></div>
        <div class="grid-2">
          <div><label class="field-label">SAP DS URL</label><input x-model="configModal.ds_url" class="field-input" placeholder="http://ds-server:8080" /></div>
          <div><label class="field-label">DS User</label><input x-model="configModal.ds_user" class="field-input" /></div>
          <div><label class="field-label">DS Password</label><input x-model="configModal.ds_password" type="password" class="field-input" /></div>
          <div><label class="field-label">DS Repository</label><input x-model="configModal.ds_repository" class="field-input" placeholder="DS_REPO" /></div>
          <div><label class="field-label">DS Timeout (s)</label><input x-model="configModal.ds_timeout" type="number" class="field-input" placeholder="60" /></div>
          <div><label class="field-label">DS Proxy URL</label><input x-model="configModal.ds_proxy_url" class="field-input" placeholder="http://proxy.company:8080" /></div>
          <label class="flex items-center gap-2 text-sm text-slate-700 mt-6">
            <input x-model="configModal.ds_verify_ssl" type="checkbox" class="rounded border-slate-300" />
            Verify DS SSL certificate
          </label>
        </div>
```

- [ ] **Step 2: Regenerate `frontend/index.html` from the partial**

Run: `node scripts/build-html.js`

This is a **generated file** — never hand-edit `frontend/index.html` directly. A prior feature on this codebase (`bo_job`) accidentally did exactly that and had to be fixed in a follow-up commit once CI's drift check caught it; don't repeat that mistake. All HTML edits in this task go into `frontend/partials/tab-config.html` only; `index.html` is a build output.

- [ ] **Step 3: Add defaults in `openNewConfigModal`**

In `frontend/features/config.js`, in `openNewConfigModal` (around line 57-71), add next to the existing `bo_*` defaults:

```javascript
        bo_url: '', bo_user: '', bo_password: '', bo_auth_type: 'secEnterprise', bo_timeout: 60,
        bo_proxy_url: '', bo_verify_ssl: true,
        ds_url: '', ds_user: '', ds_password: '', ds_repository: '', ds_timeout: 60,
        ds_proxy_url: '', ds_verify_ssl: true,
        automic_url: '', automic_user: '', automic_password: '',
```

- [ ] **Step 4: Add hydration in `editConfig`**

In `frontend/features/config.js`, in `editConfig` (around line 73-132), add next to the existing `bo_*` hydration:

```javascript
        bo_url: d.bo_url || '', bo_user: d.bo_user || '', bo_password: d.bo_password || '',
        bo_auth_type: d.bo_auth_type || 'secEnterprise',
        bo_timeout: d.bo_timeout || 60,
        bo_proxy_url: d.bo_proxy_url || '',
        bo_verify_ssl: d.bo_verify_ssl !== false,
        ds_url: d.ds_url || '', ds_user: d.ds_user || '', ds_password: d.ds_password || '',
        ds_repository: d.ds_repository || '',
        ds_timeout: d.ds_timeout || 60,
        ds_proxy_url: d.ds_proxy_url || '',
        ds_verify_ssl: d.ds_verify_ssl !== false,
```

- [ ] **Step 5: Add payload building in `_configDataFromModal`**

In `frontend/features/config.js`, in `_configDataFromModal` (around line 134+), add next to the existing `bo_*` payload fields:

```javascript
        bo_url: m.bo_url || '', bo_user: m.bo_user || '',
        bo_password: m.bo_password || '',
        bo_auth_type: m.bo_auth_type || 'secEnterprise',
        bo_timeout: Number(m.bo_timeout) || 60,
        bo_proxy_url: m.bo_proxy_url || '',
        bo_verify_ssl: m.bo_verify_ssl !== false,
        ds_url: m.ds_url || '', ds_user: m.ds_user || '',
        ds_password: m.ds_password || '',
        ds_repository: m.ds_repository || '',
        ds_timeout: Number(m.ds_timeout) || 60,
        ds_proxy_url: m.ds_proxy_url || '',
        ds_verify_ssl: m.ds_verify_ssl !== false,
```

- [ ] **Step 6: Confirm `frontend/index.html` still matches the partial after your JS edits**

Run: `node scripts/build-html.js && git diff --stat frontend/index.html`
Expected: whatever diff appears reflects only Step 1's new SAP DS block (JS changes don't affect the generated HTML). If `index.html` shows unrelated changes, investigate before committing.

- [ ] **Step 7: Manually verify in the browser**

Run: `python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload`

In the browser, open `http://127.0.0.1:8000`: go to the Config tab, open/create a config, confirm the new SAP DS URL/User/Password/Repository/Timeout/Proxy/Verify SSL fields appear below the SAP BO fields, fill them in, save, reopen for editing, confirm the fields round-trip.

- [ ] **Step 8: Commit**

```bash
git add frontend/partials/tab-config.html frontend/index.html frontend/features/config.js
git commit -m "feat(ui): add SAP DS connection fields to config editor"
```

---

## Task 9: Frontend — job modal support for `ds_job`

**Files:**
- Modify: `frontend/partials/tab-launch.html:341-342` (job type select), `frontend/partials/tab-launch.html:646-663` (new conditional param block, inserted after the `bo_job` block)
- Modify: `frontend/features/launch.js` — defaults (`openNewJobModal`, ~line 143-145), edit hydration (`openEditJobModal`, ~line 278-281), payload building (`_buildJobRequestBody`, ~line 528-539), save-gate (`canSaveJob`, ~line 670)

- [ ] **Step 1: Add `ds_job` to the Job Type select**

In `frontend/partials/tab-launch.html`, in the Job Type `<select>` (around line 340-343), add the new option next to `bo_job`:

```html
            <option value="bo_job">bo_job</option>
            <option value="ds_job">ds_job</option>
            <option value="automic_job">automic_job</option>
```

- [ ] **Step 2: Add the `ds_job` param block**

In `frontend/partials/tab-launch.html`, immediately after the existing `bo_job` block (after line 663), add:

```html
        <div x-show="jobModal.job_type === 'ds_job'" class="grid-2">
          <div>
            <label class="field-label">DS Job Name</label>
            <input x-model="jobModal.ds_job_name" class="field-input" placeholder="DS_NIGHTLY_LOAD" data-testid="job-modal-ds-job-name-input" />
          </div>
          <div>
            <label class="field-label">Repository (optional, falls back to config)</label>
            <input x-model="jobModal.ds_job_repository" class="field-input" placeholder="DS_REPO" />
          </div>
          <div>
            <label class="field-label">Job Params (JSON, optional)</label>
            <input x-model="jobModal.ds_job_params_raw" class="field-input font-mono text-xs" placeholder='{"$G_RUN_DATE": "2026-07-24"}' />
          </div>
          <div>
            <label class="field-label">Poll Interval (seconds)</label>
            <input x-model="jobModal.ds_job_poll_interval_s" type="number" min="1" class="field-input" placeholder="5" />
          </div>
          <div>
            <label class="field-label">Timeout (seconds)</label>
            <input x-model="jobModal.ds_job_timeout_s" type="number" min="1" class="field-input" placeholder="600" />
          </div>
        </div>
```

- [ ] **Step 3: Regenerate `frontend/index.html` from the partial**

Run: `node scripts/build-html.js`

Same warning as Task 8, Step 2 — never hand-edit `frontend/index.html`.

- [ ] **Step 4: Add defaults in `openNewJobModal`**

In `frontend/features/launch.js`, in `openNewJobModal` (around line 143-145), add next to the existing `bo_job_*` defaults:

```javascript
        automic_job_name: '', automic_run_id: '',
        bo_job_object_id: '', bo_job_schedule_params_raw: '',
        bo_job_poll_interval_s: '', bo_job_timeout_s: '',
        ds_job_name: '', ds_job_repository: '', ds_job_params_raw: '',
        ds_job_poll_interval_s: '', ds_job_timeout_s: '',
```

- [ ] **Step 5: Add hydration in `openEditJobModal`**

In `frontend/features/launch.js`, in `openEditJobModal` (around line 278-281), add next to the existing `bo_job_*` hydration:

```javascript
        automic_job_name: job.job_type === 'automic_job' ? (job.params?.job_name || '') : '',
        automic_run_id: job.params?.run_id || '',
        bo_job_object_id: job.params?.object_id || '',
        bo_job_schedule_params_raw: job.params?.schedule_params ? JSON.stringify(job.params.schedule_params) : '',
        bo_job_poll_interval_s: job.params?.poll_interval_s ?? '',
        bo_job_timeout_s: job.params?.timeout_s ?? '',
        ds_job_name: job.job_type === 'ds_job' ? (job.params?.job_name || '') : '',
        ds_job_repository: job.params?.repository || '',
        ds_job_params_raw: job.params?.job_params ? JSON.stringify(job.params.job_params) : '',
        ds_job_poll_interval_s: job.job_type === 'ds_job' ? (job.params?.poll_interval_s ?? '') : '',
        ds_job_timeout_s: job.job_type === 'ds_job' ? (job.params?.timeout_s ?? '') : '',
```

`job_name` is now written by two different job types' params (`automic_job`'s `job_name`/run-id pair, and `ds_job`'s new `job_name`). Without a `job_type` guard, editing a `ds_job` would also pre-fill the hidden `automic_job_name` field with the DS job's name (harmless while the modal shows `ds_job`'s fields, but would leak into view if the user then switched the Job Type dropdown to `automic_job` in the same modal session without saving). The `job.job_type === '...' ? ... : ''` guard on `automic_job_name` (changed above) and `ds_job_name`/`ds_job_poll_interval_s`/`ds_job_timeout_s` closes this for both directions. `bo_job_poll_interval_s`/`bo_job_timeout_s` don't need the same guard: `poll_interval_s`/`timeout_s` are shared generic param names across `bo_job` and `ds_job`, and showing a `ds_job`'s poll interval pre-filled in the hidden `bo_job` field is the same class of cosmetic-only leak already accepted for `bo_job` prior to this feature — not worth guarding retroactively here since it's outside this task's scope (`bo_job`'s own files aren't part of this plan's file list).

- [ ] **Step 6: Add payload building in `_buildJobRequestBody`**

In `frontend/features/launch.js`, in `_buildJobRequestBody` (around line 528-539), add a new block next to the existing `bo_job` block:

```javascript
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
      if (m.job_type === 'ds_job') {
        params.job_name = m.ds_job_name;
        if (m.ds_job_repository) params.repository = m.ds_job_repository;
        if (m.ds_job_params_raw) {
          try {
            params.job_params = JSON.parse(m.ds_job_params_raw);
          } catch (e) {
            throw new Error('Job Params must be valid JSON');
          }
        }
        if (m.ds_job_poll_interval_s !== '') params.poll_interval_s = Number(m.ds_job_poll_interval_s) || 5;
        if (m.ds_job_timeout_s !== '') params.timeout_s = Number(m.ds_job_timeout_s) || 600;
      }
```

- [ ] **Step 7: Add the save-gate check in `canSaveJob`**

In `frontend/features/launch.js`, in `canSaveJob` (around line 670-671), add next to the existing `bo_job`/`automic_job` checks:

```javascript
      if (m.job_type === 'bo_job') return Boolean(m.bo_job_object_id);
      if (m.job_type === 'ds_job') return Boolean(m.ds_job_name);
      if (m.job_type === 'automic_job') return Boolean(m.automic_job_name || m.automic_run_id);
```

- [ ] **Step 8: Confirm `frontend/index.html` still matches the partials**

Run: `node scripts/build-html.js && git diff --stat frontend/index.html`
Expected: diff reflects only Step 1-2's new content. Investigate before committing if anything else changed.

- [ ] **Step 9: Manually verify in the browser**

Run: `python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload`

In the browser: Launch tab → New Job → Job Type = `ds_job` → confirm the 5 fields appear and Save stays disabled until Job Name is filled → fill Job Name `DS_NIGHTLY_LOAD`, save, reopen for editing, confirm round-trip. Also create/edit a `bo_job` and an `automic_job` in the same session to confirm Step 5's guard doesn't cross-contaminate their fields.

- [ ] **Step 10: Commit**

```bash
git add frontend/partials/tab-launch.html frontend/index.html frontend/features/launch.js
git commit -m "feat(ui): add ds_job job type to job editor modal"
```

---

## Task 10: SAP DS mock server for testing

**Files:**
- Create: `docker/sapds-mock/Dockerfile` (copy `docker/sapbo-mock/Dockerfile`, adjust the entrypoint script name if it references `server.py`'s module path — read the BO one first to confirm what needs to change, likely nothing beyond the build context)
- Create: `docker/sapds-mock/server.py`
- Modify: `docker-compose.integration.yml` (new `sapds` service, sibling to `sapbo`)

- [ ] **Step 1: Read the SAP BO mock server and Dockerfile as the template**

Read `docker/sapbo-mock/server.py` and `docker/sapbo-mock/Dockerfile` in full before writing anything — the DS mock mirrors this file's structure (a single `BaseHTTPRequestHandler` subclass, `_send_json`/`_send_bytes` helpers, `_require_token`-style gating, module-level fixture dicts, `re.fullmatch` path routing) with DS-specific fixtures and paths instead of BO's.

- [ ] **Step 2: Create the Dockerfile**

Create `docker/sapds-mock/Dockerfile` as a copy of `docker/sapbo-mock/Dockerfile`, with any BO-specific naming (image labels, comments) adjusted to say "SAP DS" instead of "SAP BO" — the underlying Python/cert/entrypoint mechanics should be identical.

- [ ] **Step 3: Create the mock server**

Create `docker/sapds-mock/server.py`:

```python
from __future__ import annotations

import json
import os
import re
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


HOST = os.getenv("SAPDS_MOCK_HOST", "0.0.0.0")
PORT = int(os.getenv("SAPDS_MOCK_PORT", "8444"))
USER = os.getenv("SAPDS_MOCK_USER", "administrator")
PASSWORD = os.getenv("SAPDS_MOCK_PASSWORD", "Password1")
CERT_FILE = os.getenv("SAPDS_MOCK_CERT_FILE", "/certs/sapds-mock.crt")
KEY_FILE = os.getenv("SAPDS_MOCK_KEY_FILE", "/certs/sapds-mock.key")
TOKEN = "mock-sapds-token"

# Batch jobs that can be triggered via POST /BatchJob/{repository}/{job_name}/Execute.
# Each entry's outcome is reached after JOB_POLLS_TO_TERMINAL polls of
# GET /BatchJob/{repository}/status/{run_id} -- first poll(s) return "Running"
# to exercise the client's poll loop, not just its terminal-status parsing.
REPOSITORY = "DS_REPO"
SCHEDULABLE_JOBS = {
    "DS_NIGHTLY_LOAD": "Completed",
    "DS_BAD_LOAD": "Error",
}
JOB_POLLS_TO_TERMINAL = 2

# run_id -> {"job_name": str, "polls_seen": int}
_JOB_RUNS: dict[str, dict] = {}
_next_run_id = [0]


class SAPDSMockHandler(BaseHTTPRequestHandler):
    server_version = "ATOMSAPDSMock/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("%s - - %s" % (self.address_string(), fmt % args), flush=True)

    def _send_json(self, status: HTTPStatus, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _require_token(self) -> bool:
        if self.headers.get("X-DS-SessionToken") == TOKEN:
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "missing or invalid X-DS-SessionToken"})
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if not self._require_token():
            return

        status_match = re.fullmatch(r"/BatchJob/([^/]+)/status/([^/]+)", path)
        if status_match:
            _repository, run_id = status_match.groups()
            run = _JOB_RUNS.get(run_id)
            if run is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"run {run_id} not found"})
                return
            run["polls_seen"] += 1
            if run["polls_seen"] < JOB_POLLS_TO_TERMINAL:
                self._send_json(HTTPStatus.OK, {"id": run_id, "status": "Running"})
            else:
                terminal_status = SCHEDULABLE_JOBS[run["job_name"]]
                self._send_json(HTTPStatus.OK, {"id": run_id, "status": terminal_status})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/Login":
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            if payload.get("userName") != USER or payload.get("password") != PASSWORD:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid credentials"})
                return
            self._send_json(HTTPStatus.OK, {"success": True}, headers={"X-DS-SessionToken": TOKEN})
            return

        if path == "/Logout":
            self._send_json(HTTPStatus.OK, {"success": True})
            return

        trigger_match = re.fullmatch(r"/BatchJob/([^/]+)/([^/]+)/Execute", path)
        if trigger_match:
            if not self._require_token():
                return
            _repository, job_name = trigger_match.groups()
            if job_name not in SCHEDULABLE_JOBS:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"job {job_name} not found"})
                return
            _next_run_id[0] += 1
            run_id = f"run-{_next_run_id[0]}"
            _JOB_RUNS[run_id] = {"job_name": job_name, "polls_seen": 0}
            self._send_json(HTTPStatus.OK, {"id": run_id})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), SAPDSMockHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"SAP DS mock listening on https://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add the `sapds` service to `docker-compose.integration.yml`**

In `docker-compose.integration.yml`, add a new service after the existing `sapbo` service (after its `healthcheck` block, before `sqlserver`):

```yaml
  sapbo:
    build:
      context: ./docker/sapbo-mock
    image: atom-sapbo-mock:latest
    container_name: atom-sapbo-integration
    environment:
      SAPBO_MOCK_USER: "administrator"
      SAPBO_MOCK_PASSWORD: "Password1"
    ports:
      - "18443:8443"
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - >-
          import ssl, urllib.request; ctx=ssl._create_unverified_context(); urllib.request.urlopen('https://127.0.0.1:8443/health', timeout=2, context=ctx).read()
      interval: 5s
      timeout: 3s
      retries: 20

  sapds:
    build:
      context: ./docker/sapds-mock
    image: atom-sapds-mock:latest
    container_name: atom-sapds-integration
    environment:
      SAPDS_MOCK_USER: "administrator"
      SAPDS_MOCK_PASSWORD: "Password1"
    ports:
      - "18444:8444"
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - >-
          import ssl, urllib.request; ctx=ssl._create_unverified_context(); urllib.request.urlopen('https://127.0.0.1:8444/health', timeout=2, context=ctx).read()
      interval: 5s
      timeout: 3s
      retries: 20

  sqlserver:
```

(Only the `sapds:` block through its `healthcheck` is new — `sapbo:` and `sqlserver:` are shown for placement context, don't duplicate them.)

- [ ] **Step 5: Build and smoke-test the mock container**

Run:
```bash
docker compose -f docker-compose.integration.yml build sapds
docker compose -f docker-compose.integration.yml up -d sapds
```
Then confirm it's healthy: `docker compose -f docker-compose.integration.yml ps sapds` shows `healthy`, and:
```bash
docker compose -f docker-compose.integration.yml down
```

If Docker isn't available in your environment, run `python -m py_compile docker/sapds-mock/server.py` instead and note in your report that the Docker build/health check wasn't verified.

- [ ] **Step 6: Commit**

```bash
git add docker/sapds-mock/ docker-compose.integration.yml
git commit -m "feat(sap-ds-mock): add SAP DS mock server for integration tests"
```

---

## Task 11: Integration test against the SAP DS mock server

**Files:**
- Create: `tests/integration/test_sapds_mock_container.py`

- [ ] **Step 1: Read the SAP BO integration test as the template**

Read `tests/integration/test_sapbo_mock_container.py` in full — the new file mirrors its structure exactly (skip-gate, `_wait_for_sapds()`, `_env()`, per-test inline client construction, no shared fixture).

- [ ] **Step 2: Write the integration tests**

Create `tests/integration/test_sapds_mock_container.py`:

```python
from __future__ import annotations

import os
import time

import pytest
import requests
import urllib3

from etl_framework.config.models import EnvironmentConfig
from etl_framework.runner.state import TestStatus
from etl_framework.sap_ds.client import DSRestClient


pytestmark = [
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_SAPDS_TESTS") != "1",
        reason="set RUN_LIVE_SAPDS_TESTS=1 and start docker-compose.integration.yml sapds",
    ),
    pytest.mark.filterwarnings("ignore:Unverified HTTPS request"),
]


HOST = os.getenv("LIVE_SAPDS_HOST", "127.0.0.1")
PORT = int(os.getenv("LIVE_SAPDS_PORT", "18444"))
USER = os.getenv("LIVE_SAPDS_USER", "administrator")
PASSWORD = os.getenv("LIVE_SAPDS_PASSWORD", "Password1")
BASE_URL = f"https://{HOST}:{PORT}"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _wait_for_sapds() -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=2, verify=False)
            response.raise_for_status()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise AssertionError(f"SAP DS mock did not become ready: {last_error}")


def _env() -> EnvironmentConfig:
    return EnvironmentConfig(
        name="sapds-mock",
        db_host="unused",
        db_password="unused",
        ds_url=BASE_URL,
        ds_user=USER,
        ds_password=PASSWORD,
        ds_repository="DS_REPO",
        ds_timeout=5,
        ds_verify_ssl=False,
    )


def test_trigger_and_wait_for_completion_success():
    _wait_for_sapds()
    client = DSRestClient(_env())
    client.login()

    run_id = client.trigger_job("DS_NIGHTLY_LOAD")
    status = client.wait_for_completion(run_id, timeout_s=5, poll_interval_s=0.1)
    assert status == TestStatus.PASSED


def test_trigger_and_wait_for_completion_failure():
    _wait_for_sapds()
    client = DSRestClient(_env())
    client.login()

    run_id = client.trigger_job("DS_BAD_LOAD")
    status = client.wait_for_completion(run_id, timeout_s=5, poll_interval_s=0.1)
    assert status == TestStatus.FAILED


def test_trigger_unknown_job_raises_ds_api_error():
    from etl_framework.exceptions import DSAPIError

    _wait_for_sapds()
    client = DSRestClient(_env())
    client.login()

    with pytest.raises(DSAPIError) as exc_info:
        client.trigger_job("does-not-exist")
    assert exc_info.value.http_status == 404


def test_login_rejects_wrong_credentials():
    from etl_framework.exceptions import DSAPIError

    _wait_for_sapds()
    cfg = _env().model_copy(update={"ds_password": "wrong"})
    client = DSRestClient(cfg)

    with pytest.raises(DSAPIError) as exc_info:
        client.login()
    assert exc_info.value.http_status == 401
```

- [ ] **Step 3: Run the integration tests**

Run:
```bash
docker compose -f docker-compose.integration.yml up -d sapds
RUN_LIVE_SAPDS_TESTS=1 pytest tests/integration/test_sapds_mock_container.py -v
docker compose -f docker-compose.integration.yml down
```
Expected: PASS (4 passed)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_sapds_mock_container.py
git commit -m "test(sap-ds): cover DSRestClient against mock server"
```

---

## Task 12: Full regression pass

- [ ] **Step 1: Run the full unit + integration suite**

Run: `pytest tests/unit -q`
Expected: PASS, no regressions in any previously-passing test.

If Docker is available, also run:
```bash
docker compose -f docker-compose.integration.yml up -d sapbo sapds
RUN_LIVE_SAPBO_TESTS=1 RUN_LIVE_SAPDS_TESTS=1 pytest tests/integration -v
docker compose -f docker-compose.integration.yml down
```
Expected: both mock-backed integration suites pass together (confirms the two new services don't collide on ports/containers).

- [ ] **Step 2: Regenerate `frontend/index.html` one final time and confirm zero drift**

Run: `node scripts/build-html.js && git diff --stat frontend/index.html`
Expected: no output (zero diff) — confirms every HTML change across Tasks 8-9 went into the correct partial files, not the generated file, so CI's drift check will pass.

- [ ] **Step 3: Spot-check that `bo_job`/`bo_report`/`automic_job` are untouched**

Run: `pytest tests/unit -q -k "bo_job or bo_report or automic_job"`
Expected: PASS — confirms this feature is purely additive and didn't alter any existing job type's behavior.
