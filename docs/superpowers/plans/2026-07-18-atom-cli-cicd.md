# Atom CI/CD CLI + JUnit Interop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Typer-based `atom` console CLI (HTTP-only client of the existing FastAPI API) that lets any CI system launch a Job Selection, wait, gate the pipeline via exit codes, and collect JUnit/JSON/HTML artifacts — plus a new server endpoint rendering a run as JUnit XML.

**Architecture:** New `etl_framework/cli/` package (`app.py` Typer commands + exit-code mapping, `client.py` `AtomClient` requests wrapper with tenacity retries, `render.py` tabulate-based output). Server side adds `api/services/junit_export.py` (pure function) and `GET /api/runs/{run_id}/junit` in `api/routes/runs.py`, mirroring the existing `markdown-summary` pattern. The CLI never imports `api.*` or `etl_framework.repository` — HTTP only.

**Tech Stack:** Typer >= 0.12, requests, tenacity, tabulate, FastAPI, SQLAlchemy 2.0, pytest.

**Spec:** [docs/superpowers/specs/2026-07-18-atom-cli-cicd-design.md](../specs/2026-07-18-atom-cli-cicd-design.md)

**Codebase facts the engineer needs (verified):**
- Auth: `Authorization: Bearer <token>` header, enforced by `api/middleware/auth.py` (`BearerTokenMiddleware`). Tests mint a token via `TokenRepository(db).create("test-runner")` — copy the `client` fixture from `tests/unit/test_run_markdown_summary.py:11-30`.
- Launch: `POST /api/selections/{id}/launch` (202) with `JobSelectionLaunchRequest` (`api/schemas.py:534`): `source_env` **required**, `target_env` defaults `""`, optional `ci_context: dict` with keys `commit_sha`, `pipeline_url`, `ref`. Returns `RunStatusOut` (`run_id`, `status`, `passed`, `failed`, `error`, `total_tests`, …).
- Poll: `GET /api/runs/{run_id}/status` → `RunStatusOut`. Terminal statuses (from `etl_framework/repository/models.py:12`): `{"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED"}`. The CLI must NOT import models — it defines its own copy of this set.
- Gate semantics (must match `etl_framework/runner/cli.py:27-54` `_gate_exit_code`): CANCELLED→2; `error>0` or ERROR→3; `failed>0` or FAILED→1; else 0.
- Results: `TestResult` has `query_name`, `status`, `effective_status` (property that respects overrides — always use this, not `status`), `duration_seconds`, `value_mismatch_count`, `missing_in_target_count`, `missing_in_source_count`, `error_message`.
- Other endpoints consumed: `GET /api/runs/{run_id}` (detail JSON), `GET /api/runs/{run_id}/export` (CSV), `GET /api/runs/{run_id}/report` (HTML FileResponse, 404 when absent), `GET /api/selections` (`JobSelectionOut`: `id`, `name`, `job_count`, `updated_at`, …), `GET /api/runs` (list of `RunStatusOut`).
- `pyproject.toml` has no `[project.scripts]` section yet. `tabulate` and `tenacity` are already runtime dependencies.

---

### Task 1: JUnit export service

**Files:**
- Create: `api/services/junit_export.py`
- Test: `tests/unit/test_junit_export.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_junit_export.py`:

```python
"""Tests for api.services.junit_export.render_junit_xml."""
from __future__ import annotations

from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
from etl_framework.repository.models import TestResult, TestRun


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_run(db, run_id="run-junit-1", results=()):
    run = TestRun(
        run_id=run_id, status="FAILED", source_env="dev", target_env="qa",
        started_at=datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 7, 18, 10, 5, tzinfo=timezone.utc),
        total_tests=len(results),
    )
    db.add(run)
    for r in results:
        db.add(TestResult(run_id=run_id, **r))
    db.commit()
    db.refresh(run)
    return run


def _parse(xml_text: str) -> ET.Element:
    root = ET.fromstring(xml_text)
    assert root.tag == "testsuites"
    return root.find("testsuite")


def test_passing_run_renders_testcases_without_failure_nodes(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="orders_recon", status="PASSED", duration_seconds=12.4),
        dict(query_name="customer_feed", status="PASSED", duration_seconds=3.2),
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("name") == "atom-run-run-junit-1"
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "0"
    assert suite.get("errors") == "0"
    cases = suite.findall("testcase")
    assert [c.get("name") for c in cases] == ["orders_recon", "customer_feed"]
    assert cases[0].find("failure") is None
    assert cases[0].get("time") == "12.400"


def test_failed_result_gets_failure_node_with_mismatch_counts(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="customer_feed", status="FAILED", duration_seconds=3.2,
             value_mismatch_count=5, missing_in_target_count=1, missing_in_source_count=0),
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("failures") == "1"
    failure = suite.find("testcase").find("failure")
    assert failure is not None
    assert "value_mismatches=5" in failure.get("message")
    assert "missing_in_target=1" in failure.get("message")


def test_error_result_gets_error_node_with_message(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="broken_job", status="ERROR", duration_seconds=0.1,
             error_message="ORA-00942: table or view does not exist"),
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("errors") == "1"
    error = suite.find("testcase").find("error")
    assert error is not None
    assert "ORA-00942" in error.get("message")


def test_overridden_failure_counts_as_pass(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[
        dict(query_name="agreed_gap", status="FAILED", duration_seconds=1.0,
             override_status="PASSED", override_reason="known gap"),
    ])
    suite = _parse(render_junit_xml(run))
    assert suite.get("failures") == "0"
    assert suite.find("testcase").find("failure") is None


def test_empty_run_renders_empty_suite(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[])
    suite = _parse(render_junit_xml(run))
    assert suite.get("tests") == "0"
    assert suite.findall("testcase") == []


def test_timestamp_and_classname_present(db):
    from api.services.junit_export import render_junit_xml

    run = _make_run(db, results=[dict(query_name="j1", status="PASSED", duration_seconds=1.0)])
    suite = _parse(render_junit_xml(run))
    assert suite.get("timestamp") == "2026-07-18T10:00:00+00:00"
    assert suite.find("testcase").get("classname") == "atom.dev"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_junit_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'api.services.junit_export'`

- [ ] **Step 3: Write the implementation**

Create `api/services/junit_export.py`:

```python
"""Render a TestRun and its results as JUnit XML for CI test-report ingestion."""
from __future__ import annotations

from xml.etree import ElementTree as ET


def _failure_message(result) -> str:
    return (
        f"value_mismatches={result.value_mismatch_count or 0} "
        f"missing_in_target={result.missing_in_target_count or 0} "
        f"missing_in_source={result.missing_in_source_count or 0}"
    )


def render_junit_xml(run) -> str:
    results = list(run.results)
    failures = sum(1 for r in results if r.effective_status == "FAILED")
    errors = sum(1 for r in results if r.effective_status == "ERROR")
    skipped = sum(1 for r in results if r.effective_status == "CANCELLED")
    total_time = sum(float(r.duration_seconds or 0.0) for r in results)

    suite = ET.Element("testsuite", {
        "name": f"atom-run-{run.run_id}",
        "tests": str(len(results)),
        "failures": str(failures),
        "errors": str(errors),
        "skipped": str(skipped),
        "time": f"{total_time:.3f}",
    })
    if run.started_at is not None:
        suite.set("timestamp", run.started_at.isoformat())

    classname = f"atom.{run.source_env or 'run'}"
    for result in results:
        case = ET.SubElement(suite, "testcase", {
            "name": result.query_name,
            "classname": classname,
            "time": f"{float(result.duration_seconds or 0.0):.3f}",
        })
        status = result.effective_status
        if status == "FAILED":
            node = ET.SubElement(case, "failure", {
                "message": _failure_message(result),
                "type": "ReconciliationFailure",
            })
            node.text = result.error_message or _failure_message(result)
        elif status == "ERROR":
            message = result.error_message or "execution error"
            node = ET.SubElement(case, "error", {
                "message": message,
                "type": "ExecutionError",
            })
            node.text = message
        elif status == "CANCELLED":
            ET.SubElement(case, "skipped")

    root = ET.Element("testsuites")
    root.append(suite)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
```

Note: `effective_status` is a property on `TestResult` that returns `override_status` when set, else `status` — this is why the override test passes without special handling here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_junit_export.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add api/services/junit_export.py tests/unit/test_junit_export.py
git commit -m "feat(api): add JUnit XML export service for runs"
```

---

### Task 2: JUnit API route

**Files:**
- Modify: `api/routes/runs.py` (add route next to `get_run_markdown_summary`, ~line 1475)
- Test: `tests/unit/test_junit_route.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_junit_route.py` (fixture copied from `tests/unit/test_run_markdown_summary.py`):

```python
"""Tests for GET /api/runs/{run_id}/junit."""
from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _make_run_with_results(run_id="run-junit-api-1"):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import TestResult

    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        repo.create_run(run_id=run_id, source_env="dev", target_env="qa")
        repo.update_run_status(run_id, "FAILED", total_tests=2, passed=1, failed=1)
        db.add(TestResult(
            run_id=run_id, query_name="orders_recon", status="PASSED",
            duration_seconds=12.4,
        ))
        db.add(TestResult(
            run_id=run_id, query_name="customer_feed", status="FAILED",
            duration_seconds=3.2, value_mismatch_count=5,
        ))
        db.commit()


def test_junit_endpoint_returns_xml_with_testcases(client):
    _make_run_with_results()
    resp = client.get("/api/runs/run-junit-api-1/junit")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(resp.text)
    suite = root.find("testsuite")
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "1"
    names = [c.get("name") for c in suite.findall("testcase")]
    assert names == ["orders_recon", "customer_feed"]


def test_junit_endpoint_unknown_run_returns_404(client):
    resp = client.get("/api/runs/does-not-exist/junit")
    assert resp.status_code == 404


def test_junit_endpoint_requires_auth(client):
    _make_run_with_results(run_id="run-junit-api-2")
    resp = client.get(
        "/api/runs/run-junit-api-2/junit",
        headers={"Authorization": ""},
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_junit_route.py -v`
Expected: first two tests FAIL with 404 mismatch / status 404 vs 200 (route missing; unknown-run test may accidentally pass — that is fine, the first must fail).

- [ ] **Step 3: Add the route**

In `api/routes/runs.py`, directly below `get_run_markdown_summary` (~line 1481), add:

```python
@router.get("/{run_id}/junit")
def get_run_junit(run_id: str, db: Session = Depends(get_session)):
    from api.services.junit_export import render_junit_xml

    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return Response(content=render_junit_xml(run), media_type="application/xml")
```

Check the imports at the top of `runs.py`: `Response` must be imported from `fastapi` (`from fastapi import Response` or add to the existing `fastapi` import line). `HTTPException`, `Depends`, `RunRepository`, `get_session` are already imported.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_junit_route.py tests/unit/test_run_markdown_summary.py -v`
Expected: all PASSED (markdown tests included to prove no regression in the shared area).

- [ ] **Step 5: Commit**

```bash
git add api/routes/runs.py tests/unit/test_junit_route.py
git commit -m "feat(api): add GET /api/runs/{run_id}/junit endpoint"
```

---

### Task 3: CLI package skeleton, typer dependency, console script

**Files:**
- Modify: `pyproject.toml`
- Create: `etl_framework/cli/__init__.py`
- Create: `etl_framework/cli/app.py`
- Test: `tests/unit/test_cli_app.py`

- [ ] **Step 1: Add dependency and console script**

In `pyproject.toml`:
1. Append `"typer>=0.12",` to the `dependencies = [...]` list.
2. Add after the `[project.optional-dependencies]` block:

```toml
[project.scripts]
atom = "etl_framework.cli.app:main"
```

Then run: `pip install -e .` (installs typer and the `atom` entry point).

- [ ] **Step 2: Write the failing smoke test**

Create `tests/unit/test_cli_app.py`:

```python
"""Tests for the atom CLI (etl_framework.cli.app)."""
from __future__ import annotations

from typer.testing import CliRunner

runner = CliRunner()


def test_help_lists_commands():
    from etl_framework.cli.app import app

    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "report", "selections", "runs"):
        assert command in result.output


def test_missing_api_url_fails():
    from etl_framework.cli.app import app

    result = runner.invoke(app, ["selections"], env={"ATOM_API_URL": ""})
    assert result.exit_code != 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_cli_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'etl_framework.cli'`

- [ ] **Step 4: Create the skeleton**

Create empty `etl_framework/cli/__init__.py`.

Create `etl_framework/cli/app.py`:

```python
"""atom - CI/CD command line client for the Atom API.

HTTP-only: this module must never import api.* or etl_framework.repository.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from etl_framework.cli.client import (
    AtomAPIError,
    AtomAuthError,
    AtomClient,
    AtomConnectionError,
    AtomNotFoundError,
)

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_CANCELLED = 2
EXIT_ERROR = 3
EXIT_NOT_FOUND = 4
EXIT_CONNECTION = 5
EXIT_TIMEOUT = 6

# Mirror of etl_framework.repository.models.TERMINAL_STATUSES (CLI is HTTP-only,
# so it must not import the models module).
TERMINAL_STATUSES = frozenset(
    {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED"}
)

app = typer.Typer(help="Atom CI/CD command line client", no_args_is_help=True)


def _make_client(api_url: str, token: Optional[str]) -> AtomClient:
    return AtomClient(api_url, token=token)


@app.callback()
def main_options(
    ctx: typer.Context,
    api_url: str = typer.Option(..., "--api-url", envvar="ATOM_API_URL",
                                help="Atom API base URL, e.g. http://atom.internal:8000"),
    token: Optional[str] = typer.Option(None, "--token", envvar="ATOM_API_TOKEN",
                                        help="Bearer token for the Atom API"),
    output: str = typer.Option("text", "--output", help="Output style: text or json"),
):
    if output not in ("text", "json"):
        raise typer.BadParameter("--output must be 'text' or 'json'")
    ctx.obj = {"client": _make_client(api_url, token), "output": output}


def _fail(output: str, code: int, message: str) -> typer.Exit:
    if output == "json":
        print(json.dumps({"error": message, "exit_code": code}), file=sys.stderr)
    else:
        print(f"ERROR: {message}", file=sys.stderr)
    return typer.Exit(code)


@app.command()
def run(ctx: typer.Context) -> None:
    """Launch a job selection, wait for it, and gate on the outcome."""
    raise typer.Exit(EXIT_PASSED)


@app.command()
def report(ctx: typer.Context) -> None:
    """Fetch results for a past run."""
    raise typer.Exit(EXIT_PASSED)


@app.command()
def selections(ctx: typer.Context) -> None:
    """List job selections."""
    raise typer.Exit(EXIT_PASSED)


@app.command()
def runs(ctx: typer.Context) -> None:
    """List recent runs."""
    raise typer.Exit(EXIT_PASSED)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

The four commands are stubs; Tasks 6-8 replace them. `client.py` does not exist yet — create a placeholder `etl_framework/cli/client.py` now so the import works:

```python
"""HTTP client for the Atom API."""
from __future__ import annotations


class AtomAPIError(Exception):
    """Generic Atom API failure."""


class AtomConnectionError(AtomAPIError):
    """Could not reach the API after retries."""


class AtomAuthError(AtomAPIError):
    """401/403 from the API."""


class AtomNotFoundError(AtomAPIError):
    """404 from the API."""


class AtomClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_app.py -v`
Expected: 2 PASSED

- [ ] **Step 6: Verify the console script works**

Run: `atom --help`
Expected: usage text listing `run`, `report`, `selections`, `runs`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml etl_framework/cli/ tests/unit/test_cli_app.py
git commit -m "feat(cli): scaffold atom CLI package with typer console script"
```

---

### Task 4: AtomClient HTTP wrapper

**Files:**
- Modify: `etl_framework/cli/client.py`
- Test: `tests/unit/test_cli_client.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cli_client.py`:

```python
"""Tests for etl_framework.cli.client.AtomClient."""
from __future__ import annotations

import pytest
import requests

from etl_framework.cli.client import (
    AtomAPIError,
    AtomAuthError,
    AtomClient,
    AtomConnectionError,
    AtomNotFoundError,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json = json_data
        self.content = content
        self.text = text or (content.decode() if content else "")

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


@pytest.fixture
def capture(monkeypatch):
    calls = []

    def install(response):
        def fake_request(self, method, url, **kwargs):
            if isinstance(response, Exception):
                raise response
            calls.append({"method": method, "url": url, **kwargs})
            return response

        monkeypatch.setattr(requests.Session, "request", fake_request)
        return calls

    return install


def test_get_json_sends_bearer_token_and_parses(capture):
    calls = capture(FakeResponse(json_data={"ok": True}))
    client = AtomClient("http://atom.test/", token="secret-token")
    assert client.get_json("/api/selections") == {"ok": True}
    assert calls[0]["url"] == "http://atom.test/api/selections"
    assert client._session.headers["Authorization"] == "Bearer secret-token"


def test_post_json_sends_payload(capture):
    calls = capture(FakeResponse(status_code=202, json_data={"run_id": "r1"}))
    client = AtomClient("http://atom.test")
    out = client.post_json("/api/selections/3/launch", {"source_env": "dev"})
    assert out == {"run_id": "r1"}
    assert calls[0]["method"] == "POST"
    assert calls[0]["json"] == {"source_env": "dev"}


def test_401_raises_auth_error(capture):
    capture(FakeResponse(status_code=401, text="unauthorized"))
    client = AtomClient("http://atom.test")
    with pytest.raises(AtomAuthError):
        client.get_json("/api/runs")


def test_404_raises_not_found(capture):
    capture(FakeResponse(status_code=404, json_data={"detail": "Run not found"}))
    client = AtomClient("http://atom.test")
    with pytest.raises(AtomNotFoundError, match="Run not found"):
        client.get_json("/api/runs/nope/status")


def test_500_raises_api_error(capture):
    capture(FakeResponse(status_code=500, text="boom"))
    client = AtomClient("http://atom.test")
    with pytest.raises(AtomAPIError):
        client.get_json("/api/runs")


def test_connection_error_raises_atom_connection_error_after_retries(capture):
    capture(requests.ConnectionError("refused"))
    client = AtomClient("http://atom.test")
    with pytest.raises(AtomConnectionError):
        client.get_json("/api/runs")


def test_get_bytes_returns_raw_content(capture):
    capture(FakeResponse(content=b"<xml/>"))
    client = AtomClient("http://atom.test")
    assert client.get_bytes("/api/runs/r1/junit") == b"<xml/>"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_cli_client.py -v`
Expected: FAIL — `AttributeError` (no `get_json` on placeholder `AtomClient`).

- [ ] **Step 3: Write the implementation**

Replace the body of `etl_framework/cli/client.py` with:

```python
"""HTTP client for the Atom API."""
from __future__ import annotations

from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class AtomAPIError(Exception):
    """Generic Atom API failure."""


class AtomConnectionError(AtomAPIError):
    """Could not reach the API after retries."""


class AtomAuthError(AtomAPIError):
    """401/403 from the API."""


class AtomNotFoundError(AtomAPIError):
    """404 from the API."""


def _detail(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text[:200]))
    except ValueError:
        return resp.text[:200]


class AtomClient:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    @retry(
        retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=5),
        reraise=True,
    )
    def _send(self, method: str, path: str, **kwargs) -> requests.Response:
        return self._session.request(
            method, f"{self.base_url}{path}", timeout=self.timeout, **kwargs
        )

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            resp = self._send(method, path, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise AtomConnectionError(
                f"cannot reach Atom API at {self.base_url}: {exc}"
            ) from exc
        if resp.status_code in (401, 403):
            raise AtomAuthError(
                "authentication failed - check ATOM_API_TOKEN / --token"
            )
        if resp.status_code == 404:
            raise AtomNotFoundError(_detail(resp))
        if resp.status_code >= 400:
            raise AtomAPIError(f"API error {resp.status_code}: {_detail(resp)}")
        return resp

    def get_json(self, path: str, **kwargs) -> Any:
        return self._request("GET", path, **kwargs).json()

    def post_json(self, path: str, payload: dict) -> Any:
        return self._request("POST", path, json=payload).json()

    def get_bytes(self, path: str) -> bytes:
        return self._request("GET", path).content
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_client.py tests/unit/test_cli_app.py -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add etl_framework/cli/client.py tests/unit/test_cli_client.py
git commit -m "feat(cli): add AtomClient HTTP wrapper with retries and error translation"
```

---

### Task 5: Output rendering helpers

**Files:**
- Create: `etl_framework/cli/render.py`
- Test: `tests/unit/test_cli_render.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_cli_render.py`:

```python
"""Tests for etl_framework.cli.render."""
from __future__ import annotations

from etl_framework.cli import render


def test_selections_table_shows_id_name_jobs():
    text = render.selections_table([
        {"id": 3, "name": "Nightly Regression", "job_count": 12,
         "updated_at": "2026-07-17T22:00:00+00:00", "archived": False},
    ])
    assert "3" in text
    assert "Nightly Regression" in text
    assert "12" in text


def test_runs_table_shows_run_id_status_counts():
    text = render.runs_table([
        {"run_id": "r-abc", "status": "FAILED", "passed": 10, "failed": 2,
         "error": 0, "started_at": "2026-07-18T09:00:00+00:00"},
    ])
    assert "r-abc" in text
    assert "FAILED" in text
    assert "2" in text


def test_run_summary_line_contains_verdict_and_counts():
    line = render.run_summary(
        {"run_id": "r-abc", "status": "PASSED", "passed": 12, "failed": 0, "error": 0},
        exit_code=0,
    )
    assert "PASSED" in line
    assert "run=r-abc" in line
    assert "exit=0" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_cli_render.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `etl_framework/cli/render.py`:

```python
"""Human-readable output formatting for the atom CLI."""
from __future__ import annotations

from tabulate import tabulate


def selections_table(items: list[dict]) -> str:
    rows = [
        [s.get("id"), s.get("name"), s.get("job_count"),
         "yes" if s.get("archived") else "no", s.get("updated_at")]
        for s in items
    ]
    return tabulate(rows, headers=["id", "name", "jobs", "archived", "updated"],
                    tablefmt="simple")


def runs_table(items: list[dict]) -> str:
    rows = [
        [r.get("run_id"), r.get("status"), r.get("passed"), r.get("failed"),
         r.get("error"), r.get("started_at")]
        for r in items
    ]
    return tabulate(rows, headers=["run_id", "status", "passed", "failed",
                                   "error", "started"], tablefmt="simple")


def run_summary(status: dict, exit_code: int) -> str:
    return (
        f"{status.get('status')} run={status.get('run_id')} "
        f"passed={status.get('passed')} failed={status.get('failed')} "
        f"error={status.get('error')} exit={exit_code}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_render.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add etl_framework/cli/render.py tests/unit/test_cli_render.py
git commit -m "feat(cli): add table and summary rendering helpers"
```

---

### Task 6: `atom selections` and `atom runs` commands

**Files:**
- Modify: `etl_framework/cli/app.py` (replace the `selections` and `runs` stubs)
- Test: `tests/unit/test_cli_app.py` (append)

All CLI command tests use this shared fake-client pattern — append it once to `tests/unit/test_cli_app.py` in this task; Tasks 7-8 reuse it:

- [ ] **Step 1: Add fake client harness and failing tests**

Append to `tests/unit/test_cli_app.py`:

```python
import json

import pytest

from etl_framework.cli.client import (
    AtomAPIError,
    AtomAuthError,
    AtomConnectionError,
    AtomNotFoundError,
)


class FakeClient:
    """Routes (method, path) to canned responses; records calls."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def _lookup(self, method, path):
        self.calls.append((method, path))
        try:
            result = self.responses[(method, path)]
        except KeyError:
            raise AtomNotFoundError(f"no fake response for {method} {path}")
        if isinstance(result, Exception):
            raise result
        if isinstance(result, list):  # sequence of responses for repeated polling
            result = result.pop(0) if len(result) > 1 else result[0]
        return result

    def get_json(self, path, **kwargs):
        return self._lookup("GET", path)

    def post_json(self, path, payload):
        self.calls.append(("PAYLOAD", payload))
        return self._lookup("POST", path)

    def get_bytes(self, path):
        return self._lookup("GET-BYTES", path)


@pytest.fixture
def fake_client(monkeypatch):
    def install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr("etl_framework.cli.app._make_client",
                            lambda api_url, token: client)
        return client

    return install


BASE_ARGS = ["--api-url", "http://atom.test", "--token", "t0k3n"]


def test_selections_lists_names(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/selections"): [
        [{"id": 3, "name": "Nightly Regression", "job_count": 12,
          "archived": False, "updated_at": "2026-07-17T22:00:00+00:00"}],
    ]})
    result = runner.invoke(app, BASE_ARGS + ["selections"])
    assert result.exit_code == 0
    assert "Nightly Regression" in result.output


def test_selections_json_output(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/selections"): [
        [{"id": 3, "name": "Nightly Regression", "job_count": 12,
          "archived": False, "updated_at": "2026-07-17T22:00:00+00:00"}],
    ]})
    result = runner.invoke(app, BASE_ARGS + ["--output", "json", "selections"])
    assert result.exit_code == 0
    assert json.loads(result.output)[0]["id"] == 3


def test_runs_respects_limit(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/runs"): [
        [{"run_id": f"r-{i}", "status": "PASSED", "passed": 1, "failed": 0,
          "error": 0, "started_at": None} for i in range(30)],
    ]})
    result = runner.invoke(app, BASE_ARGS + ["runs", "--limit", "5"])
    assert result.exit_code == 0
    assert "r-4" in result.output
    assert "r-5" not in result.output


def test_selections_connection_error_exits_5(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/selections"): AtomConnectionError("refused")})
    result = runner.invoke(app, BASE_ARGS + ["selections"])
    assert result.exit_code == 5
```

Note on `FakeClient` list handling: a value that is a *list of lists* means "sequence of poll responses" — `[response]` (single-element) is returned as-is forever; used in Task 8 for polling.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_cli_app.py -v`
Expected: new tests FAIL (stubs print nothing, exit 0 — assertions on output fail).

- [ ] **Step 3: Implement the commands**

In `etl_framework/cli/app.py`, replace the `selections` and `runs` stubs with:

```python
@app.command()
def selections(ctx: typer.Context) -> None:
    """List job selections."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    try:
        items = client.get_json("/api/selections")
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
    if output == "json":
        print(json.dumps(items, default=str))
    else:
        print(render.selections_table(items))


@app.command()
def runs(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", min=1, help="Max runs to show"),
) -> None:
    """List recent runs."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    try:
        items = client.get_json("/api/runs")[:limit]
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
    if output == "json":
        print(json.dumps(items, default=str))
    else:
        print(render.runs_table(items))
```

Add to the imports at the top of `app.py`:

```python
from etl_framework.cli import render
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_app.py -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add etl_framework/cli/app.py tests/unit/test_cli_app.py
git commit -m "feat(cli): implement atom selections and atom runs commands"
```

---

### Task 7: `atom report` command

**Files:**
- Modify: `etl_framework/cli/app.py` (replace the `report` stub)
- Test: `tests/unit/test_cli_app.py` (append)

- [x] **Step 1: Add failing tests**

Append to `tests/unit/test_cli_app.py`:

```python
def test_report_junit_writes_file(tmp_path, fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET-BYTES", "/api/runs/r-1/junit"): b"<testsuites/>"})
    out = tmp_path / "junit.xml"
    result = runner.invoke(
        app, BASE_ARGS + ["report", "r-1", "--format", "junit", "--out", str(out)]
    )
    assert result.exit_code == 0
    assert out.read_bytes() == b"<testsuites/>"


def test_report_json_defaults_to_stdout(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/runs/r-1"): {"run_id": "r-1", "status": "PASSED"}})
    result = runner.invoke(app, BASE_ARGS + ["report", "r-1"])
    assert result.exit_code == 0
    assert json.loads(result.output)["run_id"] == "r-1"


def test_report_html_requires_out(fake_client):
    from etl_framework.cli.app import app

    fake_client({})
    result = runner.invoke(app, BASE_ARGS + ["report", "r-1", "--format", "html"])
    assert result.exit_code != 0
    assert "--out" in result.output


def test_report_unknown_run_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/runs/nope"): AtomNotFoundError("Run not found")})
    result = runner.invoke(app, BASE_ARGS + ["report", "nope"])
    assert result.exit_code == 4
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_cli_app.py -k report -v`
Expected: FAIL (stub exits 0 without output).

- [ ] **Step 3: Implement the command**

Replace the `report` stub in `etl_framework/cli/app.py` with:

```python
@app.command()
def report(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., help="Run id to fetch"),
    format: str = typer.Option("json", "--format",
                               help="junit, json, csv or html"),
    out: Optional[Path] = typer.Option(None, "--out",
                                       help="Write to file instead of stdout"),
) -> None:
    """Fetch results for a past run."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    if format not in ("junit", "json", "csv", "html"):
        raise typer.BadParameter("--format must be junit, json, csv or html")
    if format == "html" and out is None:
        raise typer.BadParameter("--out is required with --format html")
    try:
        if format == "json":
            content = json.dumps(
                client.get_json(f"/api/runs/{run_id}"), indent=2, default=str
            ).encode()
        elif format == "junit":
            content = client.get_bytes(f"/api/runs/{run_id}/junit")
        elif format == "csv":
            content = client.get_bytes(f"/api/runs/{run_id}/export")
        else:  # html
            content = client.get_bytes(f"/api/runs/{run_id}/report")
    except AtomNotFoundError as exc:
        raise _fail(output, EXIT_NOT_FOUND, str(exc))
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
    if out is not None:
        out.write_bytes(content)
    else:
        sys.stdout.write(content.decode())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_app.py -v`
Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
git add etl_framework/cli/app.py tests/unit/test_cli_app.py
git commit -m "feat(cli): implement atom report command"
```

---

### Task 8: `atom run` command (launch, poll, gate, artifacts)

**Files:**
- Modify: `etl_framework/cli/app.py` (replace the `run` stub; add helpers)
- Test: `tests/unit/test_cli_app.py` (append)

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_cli_app.py`:

```python
RUN_ARGS = ["run", "3", "--source-env", "dev", "--target-env", "qa",
            "--poll-interval", "0"]


def _launch_responses(final_status, extra=None):
    responses = {
        ("POST", "/api/selections/3/launch"): {"run_id": "r-9", "status": "PENDING"},
        ("GET", "/api/runs/r-9/status"): [
            [{"run_id": "r-9", "status": "RUNNING", "passed": 0, "failed": 0, "error": 0}],
            [final_status],
        ],
    }
    responses.update(extra or {})
    return responses


def test_run_passed_exits_0(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 5, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0
    assert "PASSED" in result.output


def test_run_failed_exits_1(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "FAILED", "passed": 4, "failed": 1, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 1


def test_run_cancelled_exits_2(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "CANCELLED", "passed": 0, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 2


def test_run_error_exits_3(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "COMPLETED", "passed": 4, "failed": 0, "error": 1}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 3


def test_run_no_wait_prints_run_id_and_exits_0(fake_client):
    from etl_framework.cli.app import app

    client = fake_client({
        ("POST", "/api/selections/3/launch"): {"run_id": "r-9", "status": "PENDING"},
    })
    result = runner.invoke(app, BASE_ARGS + ["run", "3", "--source-env", "dev",
                                             "--no-wait"])
    assert result.exit_code == 0
    assert "r-9" in result.output
    assert ("GET", "/api/runs/r-9/status") not in client.calls


def test_run_resolves_selection_by_name(fake_client):
    from etl_framework.cli.app import app

    responses = _launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0})
    responses[("GET", "/api/selections")] = [
        [{"id": 3, "name": "Nightly Regression", "job_count": 1,
          "archived": False, "updated_at": None}],
    ]
    fake_client(responses)
    result = runner.invoke(app, BASE_ARGS + ["run", "Nightly Regression",
                                             "--source-env", "dev",
                                             "--poll-interval", "0"])
    assert result.exit_code == 0


def test_run_unknown_selection_name_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/selections"): [[]]})
    result = runner.invoke(app, BASE_ARGS + ["run", "Ghost", "--source-env", "dev"])
    assert result.exit_code == 4


def test_run_passes_ci_context_to_launch(fake_client):
    from etl_framework.cli.app import app

    client = fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS + [
        "--ci-commit-sha", "a1b2c3", "--ci-pipeline-url", "https://gl/p/1",
        "--ci-ref", "main"])
    assert result.exit_code == 0
    payload = next(c[1] for c in client.calls if c[0] == "PAYLOAD")
    assert payload["ci_context"] == {
        "commit_sha": "a1b2c3", "pipeline_url": "https://gl/p/1", "ref": "main"}


def test_run_writes_junit_artifact(tmp_path, fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0},
        extra={("GET-BYTES", "/api/runs/r-9/junit"): b"<testsuites/>"}))
    out = tmp_path / "junit.xml"
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS + ["--junit-out", str(out)])
    assert result.exit_code == 0
    assert out.read_bytes() == b"<testsuites/>"


def test_run_timeout_exits_6_and_prints_run_id(fake_client):
    from etl_framework.cli.app import app

    fake_client({
        ("POST", "/api/selections/3/launch"): {"run_id": "r-9", "status": "PENDING"},
        ("GET", "/api/runs/r-9/status"): [
            [{"run_id": "r-9", "status": "RUNNING", "passed": 0, "failed": 0, "error": 0}],
        ],
    })
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS + ["--timeout", "0"])
    assert result.exit_code == 6
    assert "r-9" in result.output


def test_run_json_output_emits_machine_readable_verdict(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "FAILED", "passed": 4, "failed": 1, "error": 0}))
    result = runner.invoke(app, BASE_ARGS[:4] + ["--output", "json"] + RUN_ARGS)
    assert result.exit_code == 1
    verdict = json.loads(result.output)
    assert verdict["run_id"] == "r-9"
    assert verdict["exit_code"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_cli_app.py -k "test_run_" -v`
Expected: FAIL (stub exits 0, no output).

Actual: existing Task 8 tests were already partially passing; additional missing-coverage tests failed before implementation.

- [ ] **Step 3: Implement the command**

In `etl_framework/cli/app.py`, add helpers above the commands and replace the `run` stub:

```python
class WaitTimeoutError(Exception):
    def __init__(self, run_id: str):
        super().__init__(f"timed out waiting for run {run_id}")
        self.run_id = run_id


def _resolve_selection(client: AtomClient, selection: str) -> int:
    if selection.isdigit():
        return int(selection)
    matches = [s for s in client.get_json("/api/selections")
               if s.get("name") == selection]
    if not matches:
        raise AtomNotFoundError(f"no job selection named {selection!r}")
    if len(matches) > 1:
        raise AtomAPIError(
            f"multiple selections named {selection!r}; use the numeric id"
        )
    return int(matches[0]["id"])


def _wait_for_run(client: AtomClient, run_id: str,
                  timeout: float, poll_interval: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status = client.get_json(f"/api/runs/{run_id}/status")
        if status.get("status") in TERMINAL_STATUSES:
            return status
        if time.monotonic() >= deadline:
            raise WaitTimeoutError(run_id)
        time.sleep(poll_interval)


def _gate_exit_code(status: dict) -> int:
    if status.get("status") == "CANCELLED":
        return EXIT_CANCELLED
    if (status.get("error") or 0) > 0 or status.get("status") == "ERROR":
        return EXIT_ERROR
    if (status.get("failed") or 0) > 0 or status.get("status") == "FAILED":
        return EXIT_FAILED
    return EXIT_PASSED


def _write_artifacts(client: AtomClient, run_id: str,
                     junit_out: Optional[Path], json_out: Optional[Path],
                     html_out: Optional[Path]) -> None:
    if junit_out is not None:
        junit_out.write_bytes(client.get_bytes(f"/api/runs/{run_id}/junit"))
    if json_out is not None:
        detail = client.get_json(f"/api/runs/{run_id}")
        json_out.write_text(json.dumps(detail, indent=2, default=str),
                            encoding="utf-8")
    if html_out is not None:
        try:
            html_out.write_bytes(client.get_bytes(f"/api/runs/{run_id}/report"))
        except AtomNotFoundError:
            print(f"WARNING: no HTML report available for run {run_id}",
                  file=sys.stderr)


@app.command()
def run(
    ctx: typer.Context,
    selection: str = typer.Argument(..., help="Job selection id or exact name"),
    source_env: str = typer.Option(..., "--source-env",
                                   help="Source environment name"),
    target_env: str = typer.Option("", "--target-env",
                                   help="Target environment name"),
    ci_commit_sha: Optional[str] = typer.Option(None, "--ci-commit-sha"),
    ci_pipeline_url: Optional[str] = typer.Option(None, "--ci-pipeline-url"),
    ci_ref: Optional[str] = typer.Option(None, "--ci-ref"),
    junit_out: Optional[Path] = typer.Option(None, "--junit-out",
                                             help="Write JUnit XML here"),
    json_out: Optional[Path] = typer.Option(None, "--json-out",
                                            help="Write run detail JSON here"),
    html_out: Optional[Path] = typer.Option(None, "--html-out",
                                            help="Write HTML report here"),
    timeout: float = typer.Option(3600.0, "--timeout",
                                  help="Max seconds to wait for completion"),
    poll_interval: float = typer.Option(10.0, "--poll-interval",
                                        help="Seconds between status polls"),
    no_wait: bool = typer.Option(False, "--no-wait",
                                 help="Launch, print run id, exit 0"),
) -> None:
    """Launch a job selection, wait for it, and gate on the outcome."""
    client, output = ctx.obj["client"], ctx.obj["output"]
    try:
        selection_id = _resolve_selection(client, selection)
        payload: dict = {"source_env": source_env, "target_env": target_env}
        ci_context = {k: v for k, v in {
            "commit_sha": ci_commit_sha,
            "pipeline_url": ci_pipeline_url,
            "ref": ci_ref,
        }.items() if v}
        if ci_context:
            payload["ci_context"] = ci_context
        launched = client.post_json(f"/api/selections/{selection_id}/launch",
                                    payload)
        run_id = launched["run_id"]
        if no_wait:
            print(json.dumps({"run_id": run_id}) if output == "json" else run_id)
            raise typer.Exit(EXIT_PASSED)
        status = _wait_for_run(client, run_id, timeout, poll_interval)
        _write_artifacts(client, run_id, junit_out, json_out, html_out)
        code = _gate_exit_code(status)
        if output == "json":
            print(json.dumps({
                "run_id": run_id, "verdict": status.get("status"),
                "exit_code": code, "passed": status.get("passed"),
                "failed": status.get("failed"), "error": status.get("error"),
            }))
        else:
            print(render.run_summary(status, code))
        raise typer.Exit(code)
    except WaitTimeoutError as exc:
        print(exc.run_id)
        raise _fail(output, EXIT_TIMEOUT, str(exc))
    except AtomNotFoundError as exc:
        raise _fail(output, EXIT_NOT_FOUND, str(exc))
    except (AtomConnectionError, AtomAuthError) as exc:
        raise _fail(output, EXIT_CONNECTION, str(exc))
    except AtomAPIError as exc:
        raise _fail(output, EXIT_ERROR, str(exc))
```

Note: `typer.Exit` is not a subclass of the `Atom*` exceptions, so raising it inside the `try` block is safe — it propagates.

- [x] **Step 4: Run the full unit suite**

Run: `python -m pytest tests/unit/test_cli_app.py tests/unit/test_cli_client.py tests/unit/test_cli_render.py -v`
Expected: all PASSED

Actual: `33 passed in 2.25s`.

- [ ] **Step 5: Commit** *(skipped by agent: commits require explicit request)*

```bash
git add etl_framework/cli/app.py tests/unit/test_cli_app.py
git commit -m "feat(cli): implement atom run with polling, gating and artifacts"
```

---

### Task 9: Docker integration test lane

**Files:**
- Create: `tests/integration/test_cli_against_api.py`

- [x] **Step 1: Write the integration test (env-gated, skipped by default)**

Create `tests/integration/test_cli_against_api.py`:

```python
"""End-to-end: installed atom CLI against a live Atom API.

Skipped unless ATOM_IT_API_URL is set. Bring the stack up first:

    docker compose -f docker-compose.integration.yml up -d
    ATOM_IT_API_URL=http://localhost:8000 ATOM_IT_TOKEN=<token> \
        python -m pytest tests/integration/test_cli_against_api.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

API_URL = os.environ.get("ATOM_IT_API_URL")
TOKEN = os.environ.get("ATOM_IT_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not API_URL, reason="ATOM_IT_API_URL not set; integration lane disabled"
)


def _atom(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "etl_framework.cli.app",
         "--api-url", API_URL, "--token", TOKEN, *args],
        capture_output=True, text=True, timeout=120,
    )


def test_selections_lists_without_error():
    proc = _atom("selections")
    assert proc.returncode == 0, proc.stderr


def test_runs_lists_without_error():
    proc = _atom("--output", "json", "runs", "--limit", "5")
    assert proc.returncode == 0, proc.stderr


def test_report_unknown_run_exits_4():
    proc = _atom("report", "run-id-that-does-not-exist")
    assert proc.returncode == 4, proc.stderr
```

Running `python -m etl_framework.cli.app` requires the `if __name__ == "__main__": main()` guard added in Task 3 — it is already there.

- [x] **Step 2: Verify it is skipped in a normal run**

Run: `python -m pytest tests/integration/test_cli_against_api.py -v`
Expected: 3 skipped ("ATOM_IT_API_URL not set").

Actual: `3 skipped in 0.28s`.

- [ ] **Step 3 (manual, if docker is available): run the lane for real** *(not run: live Docker API/token not configured)*

```bash
docker compose -f docker-compose.integration.yml up -d
ATOM_IT_API_URL=http://localhost:8000 ATOM_IT_TOKEN=<admin token> \
    python -m pytest tests/integration/test_cli_against_api.py -v
docker compose -f docker-compose.integration.yml down
```

Expected: 3 passed. If docker is unavailable, note it and move on — the lane is env-gated by design.

- [ ] **Step 4: Commit** *(skipped by agent: commits require explicit request)*

```bash
git add tests/integration/test_cli_against_api.py
git commit -m "test(cli): add env-gated integration lane for atom CLI"
```

---

### Task 10: Docs and final verification

**Files:**
- Create: `docs/cli.md`

- [x] **Step 1: Write the CLI docs**

Create `docs/cli.md`:

```markdown
# atom - CI/CD command line client

Thin HTTP client for the Atom API. Install with `pip install -e .` (or the
published package); the `atom` entry point is registered via
`[project.scripts]`.

## Configuration

| Option | Env var | Purpose |
|---|---|---|
| `--api-url` | `ATOM_API_URL` | Base URL of the Atom API (required) |
| `--token` | `ATOM_API_TOKEN` | Bearer token |
| `--output text|json` | – | Human vs machine output |

## Commands

### atom run SELECTION

Launch a Job Selection (by numeric id or exact name), poll until it finishes,
write artifacts, exit with the gate code.

    atom run "Nightly Regression" --source-env dev --target-env qa \
        --junit-out atom-junit.xml --json-out atom-run.json \
        --ci-commit-sha "$CI_COMMIT_SHA" --ci-pipeline-url "$CI_PIPELINE_URL" \
        --ci-ref "$CI_COMMIT_REF_NAME"

Options: `--timeout` (default 3600s), `--poll-interval` (default 10s),
`--no-wait` (launch, print run id, exit 0), `--junit-out`, `--json-out`,
`--html-out`.

### atom report RUN_ID

    atom report run-abc123 --format junit --out junit.xml

`--format junit|json|csv|html` (default json). `--out` writes to a file
(required for html).

### atom selections / atom runs

Discovery listings. `atom runs --limit N` caps the list (default 20).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Run passed / command succeeded |
| 1 | Run failed |
| 2 | Run cancelled |
| 3 | Run error |
| 4 | Selection or run not found |
| 5 | Auth or connection failure (after retries) |
| 6 | Timed out waiting for completion (run id printed to stdout) |

## GitLab CI example

    atom-tests:
      stage: test
      script:
        - pip install etl-framework
        - atom run "Nightly Regression" --source-env dev --target-env qa
            --ci-commit-sha "$CI_COMMIT_SHA"
            --ci-pipeline-url "$CI_PIPELINE_URL"
            --ci-ref "$CI_COMMIT_REF_NAME"
            --junit-out atom-junit.xml
      artifacts:
        when: always
        reports:
          junit: atom-junit.xml
```

- [x] **Step 2: Run the full test suite**

Run: `python -m pytest tests/unit -x -q`
Expected: all pass, no regressions.

Actual: `1144 passed, 2 skipped, 7 warnings in 80.75s`.

- [x] **Step 3: Manual smoke of the console script**

Run: `atom --help` and `atom --api-url http://example.invalid run --help`
Expected: full option listing matches the docs above. `--api-url` is supplied for subcommand help because Typer validates the required global option first.

Actual: both help commands rendered successfully.

- [ ] **Step 4: Commit** *(skipped by agent: commits require explicit request)*

```bash
git add docs/cli.md
git commit -m "docs: add atom CLI reference"
```

---

## Self-review notes (done at plan-writing time)

- **Spec coverage:** architecture/package layout → Tasks 3-5; junit service+endpoint → Tasks 1-2; `atom run` incl. ci_context, artifacts, `--no-wait`, timeout → Task 8; `atom report` (`--out`, html guard) → Task 7; `atom selections`/`atom runs` → Task 6; exit codes 0-6 → Tasks 6-8; retries/error translation → Task 4; docker integration lane → Task 9; GitLab example + docs → Task 10.
- **Spec deviation (intentional):** the spec's `atom run` example omitted `--source-env`; the launch API requires it, so the CLI makes it a required option. Docs updated accordingly.
- **Type consistency:** exit-code constants, `Atom*` exception names, `_make_client` seam, and `FakeClient` response-routing convention are defined once (Tasks 3-4, Task 6) and reused verbatim in later tasks.
```
