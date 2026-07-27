# SAP BO Report Date-Prompt Answering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Answer a WebI report's prompts (date prompts first-class, others pass-through) via the occurrence-0 parameters PUT before exporting, for both interactive Adapters-tab download and saved BO-report job execution.

**Architecture:** A pure timezone-aware answer-builder (`sap_bo/parameters.py`) plus two new `BORestClient` methods (`get_document_parameters`, `answer_document_parameters`). The service/route answer-then-download for interactive use; `run_executor` answers fixed `job.params["bo_parameters"]` before its two `download_report` calls. Timezone comes from the app settings.

**Tech Stack:** Python, FastAPI, pydantic, requests (mocked in unit tests), pytest, Alpine.js frontend, Python http.server mock (`docker/sapbo-mock`).

**Design spec:** `docs/superpowers/specs/2026-07-27-sapbo-report-date-prompt-answering-design.md`

---

## File Structure

- Create: `etl_framework/sap_bo/parameters.py` — pure answer-builder (tz conversion).
- Create: `tests/unit/test_sap_bo_parameters.py`.
- Modify: `etl_framework/sap_bo/client.py` — add `get_document_parameters`, `answer_document_parameters`.
- Modify: `tests/unit/test_bo_rest_client.py`.
- Modify: `api/schemas.py` — `BOParamOut`, `BOParamAnswer`, `BOReportDownloadRequest`.
- Modify: `api/services/adapter_service.py` — `get_bo_document_parameters`, extend `download_bo_report`.
- Modify: `tests/unit/test_adapter_service.py`.
- Modify: `api/routes/adapters.py` — GET parameters + POST download endpoints.
- Modify: `tests/unit/test_adapters_routes.py`.
- Modify: `api/services/run_executor.py` — answer `bo_parameters` in `_build_case_bo_report`, `_build_case_bo_live_recon`.
- Modify: `docker/sapbo-mock/server.py` — GET parameters + PUT occ-0 handlers.
- Modify: `tests/integration/test_sapbo_mock_container.py` (or peer) — discover→answer→download.
- Modify: `frontend/features/adapters.js`, `frontend/partials/tab-adapters.html` — interactive prompt inputs.
- Modify: job editor partial/feature (BO-report job) — `bo_parameters` section.

**Note:** `_build_case_bo_job` uses `schedule_object` (scheduled-instance run with its own `schedule_params`), NOT `download_report` — it is OUT of scope for parameter answering.

---

## Task 1: Timezone-aware answer builder

**Files:**
- Create: `etl_framework/sap_bo/parameters.py`
- Test: `tests/unit/test_sap_bo_parameters.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sap_bo_parameters.py
"""Tests for the SAP BO prompt answer-builder."""
from __future__ import annotations

from etl_framework.sap_bo.parameters import build_parameter_answers


def test_datetime_date_converts_local_midnight_to_utc_on_fixed_plus1_zone():
    # Etc/GMT-1 is a fixed UTC+1 zone (POSIX sign inversion). Local midnight of
    # 2026-06-02 is 2026-06-01T23:00:00Z -- exactly what the real BO UI sent.
    built = build_parameter_answers(
        [{"id": 0, "type": "DateTime", "value": "2026-06-02"}], "Etc/GMT-1"
    )
    assert built == [
        {"id": 0, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}
    ]


def test_datetime_date_is_dst_aware_on_a_dst_zone():
    # Europe/Paris is +2 in June (CEST), so local midnight -> 22:00Z, not 23:00Z.
    built = build_parameter_answers(
        [{"id": 0, "type": "DateTime", "value": "2026-06-02"}], "Europe/Paris"
    )
    assert built[0]["value"] == "2026-06-01T22:00:00.000Z"


def test_non_date_prompt_passes_value_through_verbatim():
    built = build_parameter_answers(
        [{"id": 3, "type": "String", "value": "EMEA"}], "Etc/GMT-1"
    )
    assert built == [{"id": 3, "type": "String", "value": "EMEA"}]


def test_full_datetime_value_is_not_reconverted():
    built = build_parameter_answers(
        [{"id": 1, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}],
        "Etc/GMT-1",
    )
    assert built[0]["value"] == "2026-06-01T23:00:00.000Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_sap_bo_parameters.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: etl_framework.sap_bo.parameters`.

- [ ] **Step 3: Write minimal implementation**

```python
# etl_framework/sap_bo/parameters.py
"""Build SAP BO prompt answers, converting date-only DateTime prompts from a
local calendar date to the UTC instant BO expects.

The real BO web UI answers a report's date prompt with local-midnight of the
picked day expressed in UTC (e.g. picking 2026-06-02 on a UTC+1 server sends
"2026-06-01T23:00:00.000Z"). This mirrors that using ZoneInfo, so the result
follows whatever the configured app timezone resolves to.

DST note: ZoneInfo is DST-aware. A summer date under a DST zone (Europe/Paris
-> +2) yields "...22:00Z", while the observed server used a fixed +1 (no DST)
giving "...23:00Z". To match a fixed-offset server, set the app timezone to a
fixed zone such as "Etc/GMT-1"; the builder stays faithful to ZoneInfo.
"""
from __future__ import annotations

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC = ZoneInfo("UTC")


def build_parameter_answers(answers: list[dict], tz: str) -> list[dict]:
    """Return prompt answers with each `value` finalized for the BO PUT body.

    `answers`: list of {"id": int, "type": str, "value": str}. For a DateTime
    prompt whose value is a bare ISO date (YYYY-MM-DD), convert local midnight
    in `tz` to a UTC "...000Z" string. Everything else passes through verbatim.
    """
    zone = ZoneInfo(tz)
    built: list[dict] = []
    for answer in answers:
        value = answer["value"]
        if answer.get("type") == "DateTime" and _DATE_ONLY.match(str(value)):
            local_midnight = datetime.combine(
                datetime.strptime(value, "%Y-%m-%d").date(),
                time(0, 0),
                tzinfo=zone,
            )
            value = local_midnight.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        built.append({"id": answer["id"], "type": answer["type"], "value": value})
    return built
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_sap_bo_parameters.py -q -p no:cacheprovider`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/sap_bo/parameters.py tests/unit/test_sap_bo_parameters.py
git commit -m "feat(sap-bo): add tz-aware prompt answer-builder"
```

---

## Task 2: Client — discover report parameters

**Files:**
- Modify: `etl_framework/sap_bo/client.py` (add method near `list_reports`)
- Test: `tests/unit/test_bo_rest_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_bo_rest_client.py — append near the list_reports tests
def test_get_document_parameters_parses_nested_prompt_collection(authenticated_client):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"parameters": {"parameter": [
        {"id": 0, "name": "Start Date", "type": "DateTime", "mandatory": True},
        {"id": 1, "name": "Region", "type": "String"},
    ]}}
    with patch.object(authenticated_client._session, "get", return_value=resp) as mock_get:
        params = authenticated_client.get_document_parameters("124267")
    assert params == [
        {"id": 0, "name": "Start Date", "type": "DateTime", "mandatory": True},
        {"id": 1, "name": "Region", "type": "String", "mandatory": False},
    ]
    assert mock_get.call_args[0][0].endswith("/documents/124267/parameters")


def test_get_document_parameters_404_raises_report_not_found(authenticated_client):
    from etl_framework.exceptions import ReportNotFoundError
    resp = MagicMock()
    resp.status_code = 404
    resp.text = "not found"
    with patch.object(authenticated_client._session, "get", return_value=resp):
        with pytest.raises(ReportNotFoundError):
            authenticated_client.get_document_parameters("999")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bo_rest_client.py -k get_document_parameters -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: 'BORestClient' object has no attribute 'get_document_parameters'`.

- [ ] **Step 3: Write minimal implementation**

Add after `list_reports` in `etl_framework/sap_bo/client.py`:

```python
    def get_document_parameters(self, doc_id: str) -> list[dict]:
        """GET …/documents/{doc_id}/parameters — list a report's prompts.

        Returns one dict per prompt: {"id", "name", "type", "mandatory"}.
        Unwraps both the flat {"parameters":[…]} and nested
        {"parameters":{"parameter":[…]}} BIP shapes (same convention as
        list_documents).
        """
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}/parameters"
        response = self._session.get(
            url,
            headers={"Accept": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code == 404:
            raise ReportNotFoundError(report_id=doc_id, env_name=self._base_url)
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=doc_id,
                http_status=response.status_code,
                response_body=response.text,
            )
        raw = _unwrap_collection(response.json(), "parameters", "parameter")
        return [
            {
                "id": p.get("id"),
                "name": p.get("name", ""),
                "type": p.get("type", p.get("@type", "")),
                "mandatory": bool(p.get("mandatory", False)),
            }
            for p in raw
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bo_rest_client.py -k get_document_parameters -q -p no:cacheprovider`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/sap_bo/client.py tests/unit/test_bo_rest_client.py
git commit -m "feat(sap-bo): discover report prompt parameters"
```

---

## Task 3: Client — answer occurrence-0 parameters

**Files:**
- Modify: `etl_framework/sap_bo/client.py`
- Test: `tests/unit/test_bo_rest_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_bo_rest_client.py — append
def test_answer_document_parameters_puts_trace_shaped_body(authenticated_client):
    resp = MagicMock()
    resp.status_code = 200
    with patch.object(authenticated_client._session, "put", return_value=resp) as mock_put:
        authenticated_client.answer_document_parameters(
            "124267",
            [{"id": 0, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}],
        )
    url = mock_put.call_args[0][0]
    assert url.endswith("/documents/124267/occurences/0/parameters")
    assert mock_put.call_args[1]["params"] == {
        "dataproviderScope": "accessible", "lovinfo": "false", "prepare": "false",
    }
    assert mock_put.call_args[1]["json"] == {"parameters": {"parameter": [
        {"id": 0, "answer": {"values": {"value": [
            {"$": "2026-06-01T23:00:00.000Z", "@type": "DateTime"}]}}}]}}


def test_answer_document_parameters_raises_on_http_error(authenticated_client):
    from etl_framework.exceptions import BOAPIError
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "bad prompt"
    with patch.object(authenticated_client._session, "put", return_value=resp):
        with pytest.raises(BOAPIError):
            authenticated_client.answer_document_parameters(
                "124267", [{"id": 0, "type": "DateTime", "value": "x"}])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_bo_rest_client.py -k answer_document_parameters -q -p no:cacheprovider`
Expected: FAIL — attribute error (`answer_document_parameters` missing).

- [ ] **Step 3: Write minimal implementation**

Add after `get_document_parameters`:

```python
    def answer_document_parameters(self, doc_id: str, built_answers: list[dict]) -> None:
        """PUT …/documents/{doc_id}/occurences/0/parameters — answer prompts.

        `built_answers` is a list of already-finalized
        {"id", "type", "value"} (date conversion done by
        etl_framework.sap_bo.parameters.build_parameter_answers). Logs the
        answered prompt ids so a deployment where export ignores occurrence-0
        answers is diagnosable rather than silently wrong.
        """
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}/occurences/0/parameters"
        body = {"parameters": {"parameter": [
            {"id": a["id"], "answer": {"values": {"value": [
                {"$": a["value"], "@type": a["type"]}]}}}
            for a in built_answers
        ]}}
        logger.info(
            "SAP BO answering %d parameter(s) on document %s occurrence 0 (ids=%s)",
            len(built_answers), doc_id, [a["id"] for a in built_answers],
        )
        response = self._session.put(
            url,
            params={"dataproviderScope": "accessible", "lovinfo": "false", "prepare": "false"},
            json=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=doc_id,
                http_status=response.status_code,
                response_body=response.text,
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_bo_rest_client.py -k answer_document_parameters -q -p no:cacheprovider`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/sap_bo/client.py tests/unit/test_bo_rest_client.py
git commit -m "feat(sap-bo): answer occurrence-0 report parameters"
```

---

## Task 4: API schemas

**Files:**
- Modify: `api/schemas.py` (after `BODocRanOnOut`, near line 646)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_new_schemas.py — append (or create a focused test)
def test_bo_report_download_request_defaults():
    from api.schemas import BOReportDownloadRequest
    req = BOReportDownloadRequest()
    assert req.format == "xlsx"
    assert req.parameters == []


def test_bo_param_answer_roundtrip():
    from api.schemas import BOParamAnswer
    a = BOParamAnswer(id=0, type="DateTime", value="2026-06-02")
    assert a.model_dump() == {"id": 0, "type": "DateTime", "value": "2026-06-02"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_new_schemas.py -k "bo_report_download or bo_param_answer" -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'BOReportDownloadRequest'`.

- [ ] **Step 3: Write minimal implementation**

Add to `api/schemas.py` after `BODocRanOnOut`:

```python
class BOParamOut(BaseModel):
    id: int
    name: str = ""
    type: str = ""
    mandatory: bool = False


class BOParamAnswer(BaseModel):
    id: int
    type: str
    value: str


class BOReportDownloadRequest(BaseModel):
    format: str = "xlsx"
    parameters: list[BOParamAnswer] = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_new_schemas.py -k "bo_report_download or bo_param_answer" -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_new_schemas.py
git commit -m "feat(sap-bo): add prompt/download API schemas"
```

---

## Task 5: Service — discover + answer-then-download

**Files:**
- Modify: `api/services/adapter_service.py` (add import; new method; extend `download_bo_report`)
- Test: `tests/unit/test_adapter_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_adapter_service.py — append
def test_download_bo_report_answers_parameters_before_downloading(monkeypatch):
    from api.services.adapter_service import AdapterService
    calls = []
    fake = MagicMock()
    fake.answer_document_parameters.side_effect = lambda *a, **k: calls.append("answer")
    fake.download_report.side_effect = lambda *a, **k: calls.append("download") or b"XLSXBYTES"

    svc = AdapterService(MagicMock())
    monkeypatch.setattr(svc, "_get_env_config", lambda cid: MagicMock(bo_auth_type="secEnterprise"))
    monkeypatch.setattr(svc, "_client_for_auth", lambda env, auth: fake)
    monkeypatch.setattr(svc, "_authenticate_if_needed", lambda c, a: None)

    out = svc.download_bo_report(
        1, "124267", "R1", "xlsx", auth=None,
        parameters=[{"id": 0, "type": "DateTime", "value": "2026-06-02"}],
        timezone="Etc/GMT-1",
    )
    assert out == b"XLSXBYTES"
    assert calls == ["answer", "download"]  # answer strictly before download
    built = fake.answer_document_parameters.call_args[0][1]
    assert built[0]["value"] == "2026-06-01T23:00:00.000Z"  # tz-converted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_adapter_service.py -k answers_parameters_before -q -p no:cacheprovider`
Expected: FAIL — `download_bo_report()` has no `parameters`/`timezone` kwargs (TypeError).

- [ ] **Step 3: Write minimal implementation**

At the top of `api/services/adapter_service.py`, add:

```python
from etl_framework.sap_bo.parameters import build_parameter_answers
```

Add the discovery method after `list_bo_reports`:

```python
    def get_bo_document_parameters(
        self,
        config_id: int,
        doc_id: str,
        auth: SAPBOAuthContext | None = None,
    ) -> list[BOParamOut]:
        env = self._get_env_config(config_id)
        with _bo_lock:
            client = self._client_for_auth(env, auth)
            try:
                self._authenticate_if_needed(client, auth)
                raw = client.get_document_parameters(doc_id)
            except Exception as exc:
                auth_type = auth.auth_type if auth and auth.auth_type else env.bo_auth_type
                raise HTTPException(status_code=502, detail=_friendly_error(exc, auth_type=auth_type)) from exc
            finally:
                client.logout()
        return [
            BOParamOut(id=p["id"], name=p["name"], type=p["type"], mandatory=p["mandatory"])
            for p in raw
        ]
```

Replace `download_bo_report` with:

```python
    def download_bo_report(
        self,
        config_id: int,
        doc_id: str,
        report_id: str,
        fmt: str,
        auth: SAPBOAuthContext | None = None,
        parameters: list[dict] | None = None,
        timezone: str | None = None,
    ) -> bytes:
        env = self._get_env_config(config_id)
        with _bo_lock:
            client = self._client_for_auth(env, auth)
            try:
                self._authenticate_if_needed(client, auth)
                if parameters:
                    built = build_parameter_answers(parameters, timezone or "UTC")
                    client.answer_document_parameters(doc_id, built)
                return client.download_report(doc_id, report_id, fmt)
            except Exception as exc:
                auth_type = auth.auth_type if auth and auth.auth_type else env.bo_auth_type
                raise HTTPException(status_code=502, detail=_friendly_error(exc, auth_type=auth_type)) from exc
            finally:
                client.logout()
```

Add `BOParamOut` to the `api.schemas` import block at the top of the file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_adapter_service.py -k answers_parameters_before -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/adapter_service.py tests/unit/test_adapter_service.py
git commit -m "feat(sap-bo): service answers prompts before download"
```

---

## Task 6: Routes — parameters + POST download

**Files:**
- Modify: `api/routes/adapters.py`
- Test: `tests/unit/test_adapters_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_adapters_routes.py — append (follow this file's TestClient setup)
def test_get_document_parameters_route(client, adapter_service):
    from api.schemas import BOParamOut
    adapter_service.get_bo_document_parameters.return_value = [
        BOParamOut(id=0, name="Start Date", type="DateTime", mandatory=True),
    ]
    resp = client.get("/api/adapters/sap-bo/documents/124267/parameters?config_id=1")
    assert resp.status_code == 200
    assert resp.json()[0]["type"] == "DateTime"
    assert adapter_service.get_bo_document_parameters.call_args[0][:2] == (1, "124267")


def test_post_download_with_parameters_threads_timezone(client, adapter_service, monkeypatch):
    import api.routes.adapters as mod
    monkeypatch.setattr(mod, "SettingsRepository", lambda db: MagicMock(get_timezone=lambda: "Etc/GMT-1"))
    adapter_service.download_bo_report.return_value = b"XLSX"
    resp = client.post(
        "/api/adapters/sap-bo/documents/124267/reports/R1/download?config_id=1",
        json={"format": "xlsx", "parameters": [{"id": 0, "type": "DateTime", "value": "2026-06-02"}]},
    )
    assert resp.status_code == 200
    kwargs = adapter_service.download_bo_report.call_args[1]
    assert kwargs["timezone"] == "Etc/GMT-1"
    assert kwargs["parameters"] == [{"id": 0, "type": "DateTime", "value": "2026-06-02"}]
```

> If `test_adapters_routes.py` does not already expose `client`/`adapter_service` fixtures, mirror the dependency-override + `TestClient` pattern used by the existing `ran-on` route tests in that file.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_adapters_routes.py -k "document_parameters_route or post_download_with_parameters" -q -p no:cacheprovider`
Expected: FAIL — 404 (routes not registered).

- [ ] **Step 3: Write minimal implementation**

In `api/routes/adapters.py`: add imports

```python
from api.dependencies import get_session
from etl_framework.repository.repository import ConfigRepository, JobRepository, SettingsRepository
from api.schemas import (
    ...,
    BOParamOut,
    BOReportDownloadRequest,
)
```

(`ConfigRepository`, `JobRepository` already imported — extend the line with `SettingsRepository`; `get_session` already imported.)

Add the GET parameters route after `list_bo_reports`:

```python
@router.get("/sap-bo/documents/{doc_id}/parameters", response_model=list[BOParamOut])
def list_bo_document_parameters(
    doc_id: str,
    config_id: int,
    request: Request,
    service: AdapterService = Depends(get_adapter_service),
):
    return service.get_bo_document_parameters(config_id, doc_id, _sap_bo_auth_from_request(request))
```

Add the POST download route after the existing GET download:

```python
@router.post("/sap-bo/documents/{doc_id}/reports/{report_id}/download")
def download_bo_report_with_parameters(
    doc_id: str,
    report_id: str,
    config_id: int,
    body: BOReportDownloadRequest,
    request: Request,
    db: Session = Depends(get_session),
    service: AdapterService = Depends(get_adapter_service),
):
    tz = SettingsRepository(db).get_timezone()
    content = service.download_bo_report(
        config_id,
        doc_id,
        report_id,
        fmt=body.format,
        auth=_sap_bo_auth_from_request(request),
        parameters=[p.model_dump() for p in body.parameters],
        timezone=tz,
    )
    mime = _MIME_MAP.get(body.format, "application/octet-stream")
    ext = _EXT_MAP.get(body.format, "bin")
    return Response(
        content=content,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="report_{doc_id}_{report_id}.{ext}"'},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_adapters_routes.py -k "document_parameters_route or post_download_with_parameters" -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/adapters.py tests/unit/test_adapters_routes.py
git commit -m "feat(sap-bo): parameters + parameterized-download routes"
```

---

## Task 7: Job execution — answer fixed `bo_parameters`

**Files:**
- Modify: `api/services/run_executor.py` (`_build_case_bo_report`, `_build_case_bo_live_recon`)
- Test: `tests/unit/test_run_executor_live.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_run_executor_live.py — append (reuse this file's executor fixtures)
def test_bo_report_answers_fixed_parameters_before_download(monkeypatch, bo_report_executor):
    """bo_report jobs with params.bo_parameters answer them (tz-converted from
    the app setting) before download_report is called."""
    calls = []
    fake = MagicMock()
    fake.answer_document_parameters.side_effect = lambda *a, **k: calls.append(("answer", a[1]))
    fake.download_report.side_effect = lambda *a, **k: calls.append(("download", a)) or _tiny_xlsx_bytes()
    monkeypatch.setattr("api.services.run_executor.BORestClient", lambda env: fake)
    monkeypatch.setattr(
        "api.services.run_executor.SettingsRepository",
        lambda db: MagicMock(get_timezone=lambda: "Etc/GMT-1"),
    )

    job = _bo_report_job(params={
        "report_id": "124267", "bo_report_id": "R1", "format": "xlsx",
        "bo_parameters": [{"id": 0, "type": "DateTime", "value": "2026-06-02"}],
    })
    bo_report_executor._build_case_bo_report(job)()

    assert [c[0] for c in calls] == ["answer", "download"]
    assert calls[0][1][0]["value"] == "2026-06-01T23:00:00.000Z"
```

> Use whatever helpers this test module already provides for building a
> bo_report job and a tiny xlsx frame (`_bo_report_job`, `_tiny_xlsx_bytes`);
> if absent, add minimal local helpers mirroring existing bo_report tests.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_run_executor_live.py -k answers_fixed_parameters -q -p no:cacheprovider`
Expected: FAIL — no answering happens; `calls == [("download", …)]`.

- [ ] **Step 3: Write minimal implementation**

At the top of `api/services/run_executor.py` add:

```python
from etl_framework.sap_bo.parameters import build_parameter_answers
from etl_framework.repository.repository import SettingsRepository
```

In BOTH `_build_case_bo_report` and `_build_case_bo_live_recon`, replace the
download block

```python
            try:
                data = client.download_report(doc_id, report_id, fmt)
            finally:
                client.logout()
```

with

```python
            try:
                bo_parameters = job.params.get("bo_parameters")
                if bo_parameters:
                    tz = SettingsRepository(self._db).get_timezone()
                    client.answer_document_parameters(
                        doc_id, build_parameter_answers(bo_parameters, tz)
                    )
                data = client.download_report(doc_id, report_id, fmt)
            finally:
                client.logout()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_run_executor_live.py -k answers_fixed_parameters -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_live.py
git commit -m "feat(sap-bo): answer fixed job prompts before report download"
```

---

## Task 8: Mock server — parameters GET + occ-0 PUT

**Files:**
- Modify: `docker/sapbo-mock/server.py`
- Test: `tests/integration/test_sapbo_mock_container.py` (or `tests/integration/test_sapbo_mock_pagination.py` peer)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_sapbo_mock_container.py — append (reuse the module's
# running-mock fixture + BORestClient construction against the mock base URL)
def test_mock_discover_answer_download_flow(mock_client):
    params = mock_client.get_document_parameters("124267")
    assert any(p["type"] == "DateTime" for p in params)
    # Answer must not raise (mock returns 200 and records it).
    mock_client.answer_document_parameters(
        "124267", [{"id": 0, "type": "DateTime", "value": "2026-06-01T23:00:00.000Z"}]
    )
    data = mock_client.download_report("124267", "R1", "xlsx")
    assert data  # non-empty export
```

> `mock_client` = a `BORestClient` pointed at the running mock, authenticated —
> mirror the existing integration fixtures in that module. Ensure the mock's
> `DOCUMENTS`/`REPORTS` seed includes doc `124267` with report `R1`; add it to
> the seed data if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/integration/test_sapbo_mock_container.py -k discover_answer_download -q -p no:cacheprovider`
Expected: FAIL — mock returns 404/405 for `/parameters` GET and the occ-0 PUT.

- [ ] **Step 3: Write minimal implementation**

In `do_GET` (add alongside the existing `reports` regex handling, before the final 404):

```python
        params_match = re.fullmatch(
            r"/biprws/raylight/v1/documents/([^/]+)/parameters", path
        )
        if params_match:
            if not self._require_token():
                return
            doc_id = params_match.group(1)
            self._send_json(
                HTTPStatus.OK,
                {"parameters": {"parameter": PARAMETERS.get(doc_id, [])}},
            )
            return
```

Add a `do_PUT` method on the handler class (mirrors `do_POST` structure):

```python
    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        answer_match = re.fullmatch(
            r"/biprws/raylight/v1/documents/([^/]+)/occurences/0/parameters", path
        )
        if answer_match:
            if not self._require_token():
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            _ = self.rfile.read(length) if length else b"{}"  # body accepted, not validated
            self._send_json(HTTPStatus.OK, {"success": True})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"no PUT handler for {path}"})
```

Add a `PARAMETERS` seed near `DOCUMENTS`/`REPORTS`:

```python
PARAMETERS = {
    "124267": [
        {"id": 0, "name": "Start Date", "type": "DateTime", "mandatory": True},
        {"id": 1, "name": "Region", "type": "String", "mandatory": False},
    ],
}
```

Ensure `DOCUMENTS` includes `{"id": "124267", ...}` with a `REPORTS["124267"]`
entry `{"id": "R1", ...}` and a `DATASETS[("124267", "R1")]` row list; add them
if missing.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/integration/test_sapbo_mock_container.py -k discover_answer_download -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docker/sapbo-mock/server.py tests/integration/test_sapbo_mock_container.py
git commit -m "test(sap-bo): mock report parameters GET + occ-0 PUT"
```

---

## Task 9: Frontend — interactive prompt inputs (Adapters tab)

**Files:**
- Modify: `frontend/features/adapters.js`, `frontend/partials/tab-adapters.html`

- [ ] **Step 1: Add state + discovery/download methods**

In `frontend/features/adapters.js` add state near `boReports`:

```javascript
      boReportParams: {},   // { [doc_id]: [{id,name,type,mandatory}] }
      boParamValues: {},    // { [doc_id]: { [param_id]: value } }
```

Add methods:

```javascript
    async loadBOReportParams(doc) {
      if (this.boReportParams[doc.id]) return this.boReportParams[doc.id];
      try {
        const params = await api('GET',
          `/api/adapters/sap-bo/documents/${doc.id}/parameters?config_id=${this.boConfigId}`);
        this.boReportParams[doc.id] = params;
        this.boParamValues[doc.id] = this.boParamValues[doc.id] || {};
        return params;
      } catch (e) {
        this.toast('error', 'Could not load report parameters', e.message);
        this.boReportParams[doc.id] = [];
        return [];
      }
    },

    async downloadBOReport(doc, report, format) {
      const params = this.boReportParams[doc.id] || [];
      if (!params.length) {
        window.location = `/api/adapters/sap-bo/documents/${doc.id}/reports/${report.id}/download?config_id=${this.boConfigId}&format=${format}`;
        return;
      }
      const values = this.boParamValues[doc.id] || {};
      const parameters = params.map(p => ({ id: p.id, type: p.type, value: values[p.id] || '' }));
      const resp = await fetch(
        `/api/adapters/sap-bo/documents/${doc.id}/reports/${report.id}/download?config_id=${this.boConfigId}`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ format, parameters }) });
      if (!resp.ok) { this.toast('error', 'Download failed', await resp.text()); return; }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `report_${doc.id}_${report.id}.${format}`;
      a.click(); URL.revokeObjectURL(url);
    },
```

- [ ] **Step 2: Wire the partial**

In `frontend/partials/tab-adapters.html`, when a document row is expanded,
call `loadBOReportParams(doc)` and render an input per prompt:

```html
<template x-for="p in (boReportParams[doc.id] || [])" :key="p.id">
  <label class="block text-xs">
    <span x-text="p.name || ('Prompt ' + p.id)"></span>
    <input x-show="p.type === 'DateTime'" type="date"
           x-model="boParamValues[doc.id][p.id]" class="input input-xs" />
    <input x-show="p.type !== 'DateTime'" type="text"
           x-model="boParamValues[doc.id][p.id]" class="input input-xs"
           :placeholder="p.type" />
  </label>
</template>
```

Change each report's download control to call `downloadBOReport(doc, report, fmt)`
instead of a bare GET link. Trigger `loadBOReportParams(doc)` inside the
existing `toggleBODoc(doc)` expand path.

- [ ] **Step 3: Manual verification**

Run the app against the mock (`/run` skill or the project's dev command).
Expand doc `124267`, confirm a Start Date picker + Region text field appear,
pick a date, download the report, confirm the network tab shows a POST with
the `parameters` body.

- [ ] **Step 4: Commit**

```bash
git add frontend/features/adapters.js frontend/partials/tab-adapters.html
git commit -m "feat(sap-bo): interactive report prompt inputs on Adapters tab"
```

---

## Task 10: Frontend — job editor `bo_parameters`

**Files:**
- Modify: the BO-report job editor feature/partial (search: `bo_report` in `frontend/`)

- [ ] **Step 1: Locate the BO-report job editor**

Run: `rg -n "bo_report" frontend/` to find the job-editor partial + feature
that builds `params` for a `bo_report` job.

- [ ] **Step 2: Add a "Report parameters" section**

Add editor state `jobBoParameters: []` (list of `{id, type, value}`) and UI to
add rows: an id field, a type select (`DateTime`/`String`), and a value input
(date picker when type is `DateTime`, else text). On save, include
`bo_parameters: this.jobBoParameters` in the job `params`. On load of an
existing job, hydrate `jobBoParameters` from `params.bo_parameters || []`.

```javascript
    addBoParameter() { this.jobBoParameters.push({ id: 0, type: 'DateTime', value: '' }); },
    removeBoParameter(i) { this.jobBoParameters.splice(i, 1); },
```

Ensure the saved job `params` object carries `bo_parameters` verbatim (the
backend already stores arbitrary `params`; `run_executor` reads
`job.params["bo_parameters"]`).

- [ ] **Step 3: Manual verification**

Create/edit a `bo_report` job, add a `DateTime` parameter id 0 with a fixed
date, save, reopen — confirm the value persists. Run the job against the mock
and confirm (mock logs / no error) the answer PUT fires before download.

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "feat(sap-bo): fixed report parameters on BO-report jobs"
```

---

## Final verification

- [ ] Run the full affected unit + integration suites:

```bash
python -m pytest tests/unit/test_sap_bo_parameters.py tests/unit/test_bo_rest_client.py \
  tests/unit/test_adapter_service.py tests/unit/test_adapters_routes.py \
  tests/unit/test_run_executor_live.py tests/unit/test_new_schemas.py \
  tests/integration/test_sapbo_mock_container.py -q -p no:cacheprovider
```

Expected: all pass (verify with raw pytest output, not a cached summary).

- [ ] Confirm the known-uncertain (occ-0 answer vs. session-scoped export)
  against the live server on first real use; the `answer_document_parameters`
  info-log makes a mismatch diagnosable.
