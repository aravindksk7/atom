# SAP BO Report Run-Date Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user filter the Adapters tab's SAP BO document list down to documents that ran on a specific day, composable with the existing text search.

**Architecture:** A new `BORestClient` method keyset-paginates a CeQL query against `/biprws/v1/cmsquery` for instances (`SI_INSTANCE=1`) whose `SI_STARTTIME` falls in the selected day, collecting the distinct `SI_PARENTID` (owning document) values. A new service method and route expose this as `GET /api/adapters/sap-bo/documents/ran-on?config_id=X&run_date=YYYY-MM-DD`. The frontend adds a date input next to the existing search box, ANDing the returned document-id set into the existing `filteredBODocs` computed list.

**Tech Stack:** Python (FastAPI, Pydantic, requests), Alpine.js frontend, pytest.

**Reference:** `docs/superpowers/specs/2026-07-25-sapbo-report-run-date-filter-design.md`

---

### Task 1: `BORestClient.list_document_ids_with_runs_on`

**Files:**
- Modify: `etl_framework/sap_bo/client.py:1-8` (imports), `etl_framework/sap_bo/client.py` (new method after `_list_documents_via_cms_query`, which currently ends around line 440 with `return results`)
- Test: `tests/unit/test_bo_rest_client.py`

- [ ] **Step 1: Write the failing tests**

Add to the top of `tests/unit/test_bo_rest_client.py`, alongside the existing `from unittest.mock import MagicMock, patch, PropertyMock` line, add:

```python
from datetime import date
```

Then add these tests after `test_list_documents_does_not_query_cms_when_pagination_succeeds_normally` (the last test in the CMS-query section, right before `test_list_documents_dedupes_overlapping_pages`):

```python
# ---------------------------------------------------------------------------
# list_document_ids_with_runs_on
# ---------------------------------------------------------------------------

def test_list_document_ids_with_runs_on_returns_distinct_parent_ids(authenticated_client):
    """Powers the Adapters tab's run-date filter: find every instance
    (a scheduled/saved run) that started on the given day and return the
    distinct set of documents (SI_PARENTID) those instances belong to."""
    batch = [
        {"SI_ID": "1", "SI_PARENTID": "500"},
        {"SI_ID": "2", "SI_PARENTID": "501"},
        {"SI_ID": "3", "SI_PARENTID": "500"},  # same document ran twice that day
    ]
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"documents": batch}
    with patch.object(authenticated_client._session, "post", return_value=resp) as mock_post:
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert ids == ["500", "501"]
    query = mock_post.call_args[1]["json"]["query"]
    assert "SI_INSTANCE=1" in query
    assert "SI_STARTTIME >= @2026.07.20.00.00.00" in query
    assert "SI_STARTTIME < @2026.07.21.00.00.00" in query
    assert "SI_ID >" not in query


def test_list_document_ids_with_runs_on_pages_via_keyset(authenticated_client):
    """Same keyset-pagination shape as _list_documents_via_cms_query: a busy
    day's instance count can exceed one CeQL default batch."""
    batch1 = [{"SI_ID": str(i), "SI_PARENTID": f"doc{i}"} for i in range(1, 201)]
    batch2 = [{"SI_ID": str(i), "SI_PARENTID": f"doc{i}"} for i in range(201, 210)]
    responses = []
    for batch in (batch1, batch2):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"documents": batch}
        responses.append(resp)
    with patch.object(authenticated_client._session, "post", side_effect=responses) as mock_post:
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert len(ids) == 209
    assert mock_post.call_count == 2
    second_query = mock_post.call_args_list[1][1]["json"]["query"]
    assert "SI_ID > 200" in second_query


def test_list_document_ids_with_runs_on_returns_none_when_endpoint_unavailable(authenticated_client):
    """First-call failure means the CMS query endpoint itself isn't
    available -- distinct from "zero documents ran that day" (an empty
    list), so the caller (the service layer) can tell them apart and report
    `supported: false` instead of an empty result."""
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "not found"
    with patch.object(authenticated_client._session, "post", return_value=resp):
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert ids is None


def test_list_document_ids_with_runs_on_keeps_partial_data_on_later_failure(authenticated_client):
    """A failure on a later page (the endpoint clearly works -- we already
    got real data from it) keeps what was already collected instead of
    discarding it or returning None."""
    batch1 = [{"SI_ID": str(i), "SI_PARENTID": f"doc{i}"} for i in range(1, 201)]
    ok_resp = MagicMock()
    ok_resp.status_code = 200
    ok_resp.json.return_value = {"documents": batch1}
    fail_resp = MagicMock()
    fail_resp.status_code = 503
    fail_resp.text = "service unavailable"
    with patch.object(authenticated_client._session, "post", side_effect=[ok_resp, fail_resp]):
        ids = authenticated_client.list_document_ids_with_runs_on(date(2026, 7, 20))
    assert len(ids) == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_bo_rest_client.py -k "list_document_ids_with_runs_on" -v`

Expected: 4 failures with `AttributeError: 'BORestClient' object has no attribute 'list_document_ids_with_runs_on'`

- [ ] **Step 3: Write the implementation**

In `etl_framework/sap_bo/client.py`, change the top-of-file imports from:

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

to:

```python
import logging
import time
from datetime import date, timedelta
import requests
import pandas as pd
from urllib.parse import urlparse
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import BOAPIError, ReportNotFoundError
from etl_framework.runner.state import TestStatus
```

Then find the end of `_list_documents_via_cms_query` (it ends with the `return results` line, immediately followed by `def list_documents(self) -> list[dict]:`). Insert this new method between them:

```python
    def list_document_ids_with_runs_on(self, day: date) -> list[str] | None:
        """Keyset-paginated CeQL query for the distinct set of WebI document
        ids that had at least one run ("instance") on `day`. Powers the
        Adapters tab's run-date filter (see the 2026-07-25 design spec).

        An instance's `SI_PARENTID` is the CMS id of the document it belongs
        to (an instance's parent in the CMS hierarchy is the report that
        owns it). Collects distinct `SI_PARENTID` values across all pages,
        keeping first-seen order.

        Returns None (not an empty list) when the *first* query fails --
        same "unsupported vs. zero results" distinction as
        `_list_documents_via_cms_query` -- so the caller can tell the two
        apart. A failure on a later page keeps whatever distinct ids were
        already collected instead of discarding them.
        """
        url = f"{self._base_url}{self.CMS_QUERY_ENDPOINT}"
        day_start = f"@{day.year}.{day.month:02d}.{day.day:02d}.00.00.00"
        next_day = day + timedelta(days=1)
        day_end = f"@{next_day.year}.{next_day.month:02d}.{next_day.day:02d}.00.00.00"
        date_clause = f"SI_INSTANCE=1 AND SI_STARTTIME >= {day_start} AND SI_STARTTIME < {day_end}"

        document_ids: list[str] = []
        seen: set[str] = set()
        last_id: int | None = None
        page_count = 0
        while page_count < self._MAX_PAGES:
            where = date_clause if last_id is None else f"{date_clause} AND SI_ID > {last_id}"
            query = (
                f"SELECT TOP {self._PAGE_REQUEST_SIZE} SI_ID, SI_PARENTID "
                f"FROM CI_INFOOBJECTS WHERE {where} ORDER BY SI_ID"
            )
            response = self._session.post(
                url,
                json={"query": query},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache, no-store",
                    "Pragma": "no-cache",
                },
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
            if response.status_code >= 400:
                if page_count == 0:
                    logger.warning(
                        "SAP BO CMS query endpoint unavailable for run-date filtering (HTTP %d)",
                        response.status_code,
                    )
                    return None
                logger.warning(
                    "SAP BO CMS query for run-date filtering failed past SI_ID %d (HTTP %d) -- "
                    "keeping the %d document id(s) already collected",
                    last_id, response.status_code, len(document_ids),
                )
                break
            batch = _unwrap_collection(response.json(), "documents", "document", "entries")
            if not batch:
                break
            for entry in batch:
                parent_id = str(entry.get("SI_PARENTID", ""))
                if parent_id and parent_id not in seen:
                    seen.add(parent_id)
                    document_ids.append(parent_id)
            try:
                last_id = max(int(entry.get("SI_ID", 0)) for entry in batch)
            except (TypeError, ValueError):
                logger.warning(
                    "SAP BO CMS query for run-date filtering returned a non-numeric SI_ID -- "
                    "stopping keyset pagination with %d document id(s) collected",
                    len(document_ids),
                )
                break
            if len(batch) < self._PAGE_REQUEST_SIZE:
                break
            page_count += 1
        return document_ids

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_bo_rest_client.py -v`

Expected: all tests pass (60 total: 56 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add etl_framework/sap_bo/client.py tests/unit/test_bo_rest_client.py
git commit -m "feat(sap-bo): add BORestClient.list_document_ids_with_runs_on"
```

---

### Task 2: `AdapterService.list_bo_document_ids_with_runs_on` + `BODocRanOnOut` schema

**Files:**
- Modify: `api/schemas.py:603-613` (add new schema after `BOReportOut`)
- Modify: `api/services/adapter_service.py:1-17` (imports), `api/services/adapter_service.py:230-242` (new method after `list_bo_documents`)
- Test: `tests/unit/test_adapter_service.py`

- [ ] **Step 1: Write the failing tests**

In `api/schemas.py`, this test refers to a schema that doesn't exist yet — that's fine, Step 3 adds it before Step 4 runs. First, add to `tests/unit/test_adapter_service.py`: change the import line

```python
from datetime import datetime, timezone
```

to:

```python
from datetime import datetime, timezone, date
```

Then add these tests after `test_list_bo_documents_empty` (before the `# list_bo_reports` section comment):

```python
# ---------------------------------------------------------------------------
# list_bo_document_ids_with_runs_on
# ---------------------------------------------------------------------------

def test_list_bo_document_ids_with_runs_on_returns_supported_result(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_document_ids_with_runs_on.return_value = ["500", "501"]
        result = service.list_bo_document_ids_with_runs_on(config_id=1, day=date(2026, 7, 20))
    assert result.supported is True
    assert result.document_ids == ["500", "501"]
    MockClient.return_value.list_document_ids_with_runs_on.assert_called_once_with(date(2026, 7, 20))


def test_list_bo_document_ids_with_runs_on_reports_unsupported_when_client_returns_none(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_document_ids_with_runs_on.return_value = None
        result = service.list_bo_document_ids_with_runs_on(config_id=1, day=date(2026, 7, 20))
    assert result.supported is False
    assert result.document_ids == []


def test_list_bo_document_ids_with_runs_on_404_config_raises(service, mock_config_repo):
    mock_config_repo.get.return_value = None
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        service.list_bo_document_ids_with_runs_on(config_id=99, day=date(2026, 7, 20))
    assert exc_info.value.status_code == 404


def test_list_bo_document_ids_with_runs_on_wraps_errors_as_502(service):
    with patch("api.services.adapter_service.BORestClient") as MockClient:
        MockClient.return_value.list_document_ids_with_runs_on.side_effect = Exception("boom")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            service.list_bo_document_ids_with_runs_on(config_id=1, day=date(2026, 7, 20))
    assert exc_info.value.status_code == 502
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_adapter_service.py -k "runs_on" -v`

Expected: failures with `AttributeError: 'AdapterService' object has no attribute 'list_bo_document_ids_with_runs_on'`

- [ ] **Step 3: Write the implementation**

In `api/schemas.py`, change:

```python
class BOReportOut(BaseModel):
    id: str
    name: str
    report_index: int = 0


class AdapterTestOut(BaseModel):
```

to:

```python
class BOReportOut(BaseModel):
    id: str
    name: str
    report_index: int = 0


class BODocRanOnOut(BaseModel):
    document_ids: list[str]
    supported: bool = True


class AdapterTestOut(BaseModel):
```

In `api/services/adapter_service.py`, change the import line:

```python
from api.schemas import AdapterTestOut, AutomicJobStatusOut, BOAuthSessionOut, BODocOut, BOReportOut
```

to:

```python
from api.schemas import AdapterTestOut, AutomicJobStatusOut, BOAuthSessionOut, BODocOut, BODocRanOnOut, BOReportOut
```

and add, right after the `import` block at the top of the file (after `from dataclasses import dataclass`):

```python
from datetime import date
```

Then, right after `list_bo_documents` (the method ending with `return [BODocOut(id=d["id"], name=d["name"], folder=d.get("folder", "")) for d in raw]`), add:

```python

    def list_bo_document_ids_with_runs_on(
        self,
        config_id: int,
        day: date,
        auth: SAPBOAuthContext | None = None,
    ) -> BODocRanOnOut:
        env = self._get_env_config(config_id)
        with _bo_lock:
            client = self._client_for_auth(env, auth)
            try:
                self._authenticate_if_needed(client, auth)
                document_ids = client.list_document_ids_with_runs_on(day)
            except Exception as exc:
                auth_type = auth.auth_type if auth and auth.auth_type else env.bo_auth_type
                raise HTTPException(status_code=502, detail=_friendly_error(exc, auth_type=auth_type)) from exc
            finally:
                client.logout()
        if document_ids is None:
            return BODocRanOnOut(document_ids=[], supported=False)
        return BODocRanOnOut(document_ids=document_ids, supported=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_adapter_service.py -v`

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/schemas.py api/services/adapter_service.py tests/unit/test_adapter_service.py
git commit -m "feat(sap-bo): add AdapterService.list_bo_document_ids_with_runs_on"
```

---

### Task 3: Route `GET /api/adapters/sap-bo/documents/ran-on`

**Files:**
- Modify: `api/routes/adapters.py:1-33` (imports), `api/routes/adapters.py:123-130` (new route after `list_bo_documents`)
- Test: `tests/unit/test_adapters_routes.py`

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_adapters_routes.py`, change the import line:

```python
from api.schemas import (
    AdapterTestOut, BODocOut, BOAuthSessionOut, BOReportOut,
    AutomicJobStatusOut, JobDefinition,
)
```

to:

```python
from api.schemas import (
    AdapterTestOut, BODocOut, BODocRanOnOut, BOAuthSessionOut, BOReportOut,
    AutomicJobStatusOut, JobDefinition,
)
```

Find the existing test `test_list_bo_documents_returns_list` (around line 141) and add these new tests right after it:

```python
def test_list_bo_document_ids_with_runs_on_returns_result(client, mock_adapter_service):
    mock_adapter_service.list_bo_document_ids_with_runs_on.return_value = BODocRanOnOut(
        document_ids=["500", "501"], supported=True,
    )
    resp = client.get("/api/adapters/sap-bo/documents/ran-on?config_id=1&run_date=2026-07-20")
    assert resp.status_code == 200
    assert resp.json() == {"document_ids": ["500", "501"], "supported": True}
    from datetime import date
    mock_adapter_service.list_bo_document_ids_with_runs_on.assert_called_once()
    args, kwargs = mock_adapter_service.list_bo_document_ids_with_runs_on.call_args
    assert args[0] == 1
    assert args[1] == date(2026, 7, 20)


def test_list_bo_document_ids_with_runs_on_reports_unsupported(client, mock_adapter_service):
    mock_adapter_service.list_bo_document_ids_with_runs_on.return_value = BODocRanOnOut(
        document_ids=[], supported=False,
    )
    resp = client.get("/api/adapters/sap-bo/documents/ran-on?config_id=1&run_date=2026-07-20")
    assert resp.status_code == 200
    assert resp.json() == {"document_ids": [], "supported": False}


def test_list_bo_document_ids_with_runs_on_rejects_malformed_date(client):
    resp = client.get("/api/adapters/sap-bo/documents/ran-on?config_id=1&run_date=not-a-date")
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_adapters_routes.py -k "runs_on" -v`

Expected: failures with 404 (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

In `api/routes/adapters.py`, change the schemas import block from:

```python
from api.schemas import (
    AdapterTestOut,
    AutomicBulkImportRequest,
    AutomicBulkImportResponse,
    AutomicJobStatusOut,
    AutomicJobSummary,
    AutomicJobCreateRequest,
    AutomicLookupRequest,
    BODocOut,
    BOAuthSessionOut,
    BOJobCreateRequest,
    BOLogoffRequest,
    BOLogonRequest,
    BOReportOut,
    JobDefinition,
    BOTestRequest,
    RestApiPreviewRequest,
    RestApiTestRequest,
)
```

to:

```python
from datetime import date

from api.schemas import (
    AdapterTestOut,
    AutomicBulkImportRequest,
    AutomicBulkImportResponse,
    AutomicJobStatusOut,
    AutomicJobSummary,
    AutomicJobCreateRequest,
    AutomicLookupRequest,
    BODocOut,
    BODocRanOnOut,
    BOAuthSessionOut,
    BOJobCreateRequest,
    BOLogoffRequest,
    BOLogonRequest,
    BOReportOut,
    JobDefinition,
    BOTestRequest,
    RestApiPreviewRequest,
    RestApiTestRequest,
)
```

Then find the existing route:

```python
@router.get("/sap-bo/documents", response_model=list[BODocOut])
def list_bo_documents(
    config_id: int,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.list_bo_documents(config_id, _sap_bo_auth_from_request(request))
```

and add this new route right after it:

```python

@router.get("/sap-bo/documents/ran-on", response_model=BODocRanOnOut)
def list_bo_document_ids_with_runs_on(
    config_id: int,
    run_date: date,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.list_bo_document_ids_with_runs_on(config_id, run_date, _sap_bo_auth_from_request(request))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_adapters_routes.py -v`

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/routes/adapters.py tests/unit/test_adapters_routes.py
git commit -m "feat(sap-bo): add GET /api/adapters/sap-bo/documents/ran-on route"
```

---

### Task 4: Frontend — date filter UI

**Files:**
- Modify: `frontend/features/adapters.js:12-24` (state), `frontend/features/adapters.js:71-113` (`loadBODocuments`, `filteredBODocs`)
- Modify: `frontend/partials/tab-adapters.html:55-59` (search box block)

This task has no existing JS unit-test harness to extend (this codebase's frontend tests are Playwright e2e only, and the design spec explicitly scoped e2e coverage for this feature out — the mock server doesn't simulate CMS query/instance data). Verification is a syntax check plus manual reasoning through the two changed code paths; no TDD red/green cycle applies here.

- [ ] **Step 1: Add state and reset it in `loadBODocuments`**

In `frontend/features/adapters.js`, change:

```javascript
    boDocs: [],
    expandedBODocs: [],
    boReports: {},         // doc.id → list of reports
    boFilterQuery: '',
```

to:

```javascript
    boDocs: [],
    expandedBODocs: [],
    boReports: {},         // doc.id → list of reports
    boFilterQuery: '',
    boRanOnDate: '',
    boRanOnDocIds: null,   // Set<string> | null — null means no date filter active
    boRanOnSupported: true,
```

Then change `loadBODocuments`:

```javascript
    async loadBODocuments() {
      if (!this.boConfigId) return;
      this.boLoading = true;
      this.boDocs = [];
      this.expandedBODocs = [];
      this.boReports = {};
      this.boFilterQuery = '';
      try {
        this.boDocs = await api('GET', `/api/adapters/sap-bo/documents?config_id=${this.boConfigId}`);
        this.toast('success', `${this.boDocs.length} documents loaded`);
      } catch (e) {
        this.toast('error', 'Load failed', e.message);
      } finally {
        this.boLoading = false;
      }
    },
```

to:

```javascript
    async loadBODocuments() {
      if (!this.boConfigId) return;
      this.boLoading = true;
      this.boDocs = [];
      this.expandedBODocs = [];
      this.boReports = {};
      this.boFilterQuery = '';
      this.boRanOnDate = '';
      this.boRanOnDocIds = null;
      this.boRanOnSupported = true;
      try {
        this.boDocs = await api('GET', `/api/adapters/sap-bo/documents?config_id=${this.boConfigId}`);
        this.toast('success', `${this.boDocs.length} documents loaded`);
      } catch (e) {
        this.toast('error', 'Load failed', e.message);
      } finally {
        this.boLoading = false;
      }
    },

    async loadBORanOnDocIds() {
      if (!this.boRanOnDate) {
        this.boRanOnDocIds = null;
        this.boRanOnSupported = true;
        return;
      }
      if (!this.boConfigId) return;
      try {
        const result = await api('GET',
          `/api/adapters/sap-bo/documents/ran-on?config_id=${this.boConfigId}&run_date=${this.boRanOnDate}`);
        this.boRanOnSupported = result.supported;
        this.boRanOnDocIds = new Set(result.document_ids);
      } catch (e) {
        this.toast('error', 'Run-date filter failed', e.message);
        this.boRanOnDocIds = null;
      }
    },
```

- [ ] **Step 2: AND the date filter into `filteredBODocs`**

In `frontend/features/adapters.js`, change:

```javascript
    get filteredBODocs() {
      if (!this.boFilterQuery.trim()) return this.boDocs;
      return this.boDocs.filter(doc => this.boDocMatchesQuery(doc) || this.boDocHasMatchingReport(doc));
    },
```

to:

```javascript
    get filteredBODocs() {
      const dateFiltered = this.boRanOnDocIds
        ? this.boDocs.filter(doc => this.boRanOnDocIds.has(doc.id))
        : this.boDocs;
      if (!this.boFilterQuery.trim()) return dateFiltered;
      return dateFiltered.filter(doc => this.boDocMatchesQuery(doc) || this.boDocHasMatchingReport(doc));
    },
```

`boDocMatchesQuery`/`boDocHasMatchingReport` are unchanged — the date filter narrows the base list before the existing text-search OR logic runs, so date and text search compose (AND) instead of the date filter being bypassable via a report-name match.

- [ ] **Step 3: Syntax-check the file**

Run: `cd c:\atom && node --check frontend/features/adapters.js`

Expected: no output (exit code 0)

- [ ] **Step 4: Add the date input to the Adapters tab**

In `frontend/partials/tab-adapters.html`, change:

```html
        <div class="mb-3">
          <input x-model="boFilterQuery" class="field-input" placeholder="Search documents and reports…"
                 data-testid="bo-doc-search-input" />
          <p class="text-xs text-muted mt-1">Matches document name/folder/id, and report names once a document has been expanded.</p>
        </div>
```

to:

```html
        <div class="mb-3">
          <input x-model="boFilterQuery" class="field-input" placeholder="Search documents and reports…"
                 data-testid="bo-doc-search-input" />
          <p class="text-xs text-muted mt-1">Matches document name/folder/id, and report names once a document has been expanded.</p>
        </div>
        <div class="mb-3">
          <label class="field-label">Ran on date</label>
          <input type="date" x-model="boRanOnDate" @change="loadBORanOnDocIds()" class="field-input"
                 data-testid="bo-doc-ran-on-input" />
          <template x-if="boRanOnDate && !boRanOnSupported">
            <p class="text-xs text-rose-600 mt-1">Run-date filtering isn't available against this SAP BO server.</p>
          </template>
        </div>
```

- [ ] **Step 5: Manual verification**

`.html` partials have no syntax checker in this project. Read
`frontend/partials/tab-adapters.html` back and confirm: the new
`<div class="mb-3">` block is a sibling of the search box's div, both
inside the same parent `<div class="card" x-show="boDocs.length > 0">`,
with matching indentation and a closing tag.

- [ ] **Step 6: Commit**

```bash
cd c:\atom
git add frontend/features/adapters.js frontend/partials/tab-adapters.html
git commit -m "feat(adapters): add SAP BO run-date filter to Browse Documents"
```

---

### Task 5: Push

- [ ] **Step 1: Run the full sap_bo/adapter test sweep**

Run: `cd c:\atom && python -m pytest tests/unit/ -k "sap_bo or bo_rest or adapter" -q`

Expected: all pass (105 existing + 4 + 4 + 3 new = 116)

- [ ] **Step 2: Push to origin master**

```bash
cd c:\atom
git push origin master
```
