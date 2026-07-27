# SAP BO report date-prompt answering — design

## Problem

The ETL framework can list and download SAP BusinessObjects (WebI) reports,
but it cannot **answer a report's prompts** before exporting. Many WebI
reports are parameterized — most relevantly by a **date prompt** — and SAP
BO's own web UI answers such prompts with a PUT to the document's occurrence
before running/exporting:

```
PUT /biprws/raylight/v1/documents/{doc_id}/occurences/0/parameters
    ?dataproviderScope=accessible&lovinfo=false&prepare=false
Body:
{"parameters":{"parameter":[
  {"id":0,"answer":{"values":{"value":[
    {"$":"2026-06-01T23:00:00.000Z","@type":"DateTime"}]}}}]}}
```

Because our `download_report` issues a bare `GET .../reports/{report_id}`
with no prompt answering, any parameterized report either exports with stale
default prompt values or fails outright when a mandatory prompt is
unanswered. Users perceive this as "the date filter doesn't work."

Note the captured value: the user picked a June date, and the UI sent
`2026-06-01T23:00:00.000Z` — i.e. **local-midnight expressed in UTC**, with
a **fixed +1** offset (no DST — a June date under a DST zone would be `+2`).
Date handling must convert the picked local date to the UTC instant BO
expects.

This is a **new feature**, distinct from the Adapters-tab *run-date filter*
(which documents ran on day X — see
`2026-07-25-sapbo-report-run-date-filter-design.md`). That is a document-list
filter; this is per-report prompt answering.

## Scope (decided during brainstorming)

- **Both** entry points: interactive download/preview from the Adapters tab,
  AND saved BO-report job execution.
- **Date prompts get first-class handling; non-date prompts pass through** —
  full date-picker UX for `DateTime` prompts; other prompt types are surfaced
  and accept a raw user-supplied value so mandatory prompts can still be
  answered and the export won't fail.
- **Saved jobs carry a fixed date** (not relative tokens) in
  `job.params["bo_parameters"]`.
- **Timezone** for the local→UTC conversion comes from the existing global
  **app timezone setting** (single source of truth; UTC+1 for this
  deployment).

## Approach (chosen: A — discover + answer, then export)

Three small, independently testable units:

1. Discover a report's prompts (`get_document_parameters`).
2. Answer occurrence 0's parameters via the trace's PUT
   (`answer_document_parameters`), fed by a pure, tested answer-builder that
   owns the timezone conversion.
3. Reuse the existing `download_report` GET unchanged — callers answer first,
   then export.

Rejected: (B) folding answering into `download_report` — couples answering to
export, hides the two round-trips, and can't feed the UI's discovery step.
(C) routing through `schedule_object` (scheduled-instance run) — diverges
from the captured interactive trace and the current synchronous-download
model for a larger change than needed.

**Known-uncertain (cannot verify without the live server, flagged rather than
silently assumed):** whether answering occurrence 0's parameters via PUT
persists so the subsequent stateless `download_report` GET reflects them, or
whether the export must go through the same occurrence/session. This design
implements the trace's sequence (answer occ-0 → export). If the export
ignores occ-0 answers on this deployment, the failure is made **diagnosable**
(the answered parameters and the export request are logged) rather than
silently wrong — the same posture the CeQL date-literal syntax took in the
run-date-filter spec.

## Components

### 1. `etl_framework/sap_bo/client.py` — `BORestClient`

Following this file's lazy-auth / unwrap / raise-on-HTTP-error conventions:

- `get_document_parameters(doc_id: str) -> list[dict]` —
  `GET {base}/biprws/raylight/v1/documents/{doc_id}/parameters`. Unwrap via
  the existing `_unwrap_collection` (handles both the flat
  `{"parameters":[...]}` and the nested
  `{"parameters":{"parameter":[...]}}` BIP convention, exactly like
  `list_documents`). Returns one dict per prompt:
  `{"id": int, "name": str, "type": str, "mandatory": bool}` — `type` read
  from `@type`/`type`, `mandatory` from the prompt's flag (default `False`
  when absent). `404` → `ReportNotFoundError(doc_id)`; other `>=400` →
  `BOAPIError`.

- `answer_document_parameters(doc_id: str, built_answers: list[dict]) -> None`
  — `PUT {base}/biprws/raylight/v1/documents/{doc_id}/occurences/0/parameters`
  with query `dataproviderScope=accessible&lovinfo=false&prepare=false` and
  body:
  ```json
  {"parameters":{"parameter":[
    {"id":<id>,"answer":{"values":{"value":[
      {"$":"<value>","@type":"<type>"}]}}}]}}
  ```
  `built_answers` is a list of already-final
  `{"id": int, "type": str, "value": str}` (the builder below has already
  done any date conversion). `>=400` → `BOAPIError`.

- `download_report` — **unchanged**.

### 2. `etl_framework/sap_bo/parameters.py` — new, pure, no I/O

- `build_parameter_answers(answers: list[dict], tz: str) -> list[dict]` —
  input `{"id", "type", "value"}` per prompt; output the same shape with
  `value` finalized:
  - `type == "DateTime"` and `value` is a bare ISO date (`YYYY-MM-DD`):
    interpret as **local midnight** in `ZoneInfo(tz)`, convert to UTC, format
    `"%Y-%m-%dT%H:%M:%S.000Z"`.
  - any already-full ISO datetime, or any non-`DateTime` type: pass `value`
    through verbatim.
  - This is the single place the timezone rule lives, so both the interactive
    service path and the job-execution path share one tested implementation.

  DST note captured in code comments: `ZoneInfo` is DST-aware, so a summer
  date under `Europe/Paris` yields `+2` (`22:00Z`), while the observed server
  used fixed `+1` (`23:00Z`). To match a fixed-offset server, the app timezone
  should be a fixed zone such as `Etc/GMT-1`. The builder stays faithful to
  whatever `ZoneInfo(tz)` resolves; matching the server is a **configuration**
  choice, not a hardcode.

### 3. `api/services/adapter_service.py` — `AdapterService`

- `get_bo_document_parameters(config_id, doc_id, auth) -> list[BOParamOut]` —
  same connect / `_authenticate_if_needed` / `logout()`-in-finally shape as
  `list_bo_reports`.
- `download_bo_report(config_id, doc_id, report_id, fmt, auth, parameters=None,
  timezone=None)` — when `parameters` is provided: `build_parameter_answers(
  parameters, timezone)` → `client.answer_document_parameters(doc_id, built)`
  → then the existing `client.download_report(...)`. Order is asserted in
  tests (answer strictly before export). When `parameters` is `None`,
  behaviour is exactly as today.

The service does not read settings itself; the **route** supplies `timezone`
(keeps `AdapterService` constructed from `ConfigRepository` only).

### 4. `api/routes/adapters.py`

- `GET /sap-bo/documents/{doc_id}/parameters?config_id=` →
  `list[BOParamOut]`.
- `POST /sap-bo/documents/{doc_id}/reports/{report_id}/download` — body
  `BOReportDownloadRequest{format, parameters: list[BOParamAnswer]}`. Route
  reads the app timezone from `SettingsRepository` and passes it to
  `download_bo_report`. Returns the file `Response` with the same
  `Content-Disposition` / MIME mapping as the existing GET.
- The existing **GET** download endpoint is kept for reports without prompts
  (backward compatible; the frontend uses POST only when prompts exist).
- New schemas (`api/schemas.py`): `BOParamOut{id:int, name:str, type:str,
  mandatory:bool}`, `BOParamAnswer{id:int, type:str, value:str}`,
  `BOReportDownloadRequest{format:str, parameters:list[BOParamAnswer]}`.

### 5. `api/services/run_executor.py` — job execution

`_build_case_bo_report`, `_build_case_bo_live_recon`, `_build_case_bo_job`
each currently do `client.download_report(doc_id, report_id, fmt)`. Before
that call, when `job.params.get("bo_parameters")` is present: resolve the app
timezone (via `SettingsRepository`), `build_parameter_answers`, then
`client.answer_document_parameters(doc_id, built)`. Then download as today.

`job.params["bo_parameters"]` is a list of fixed `{"id", "type", "value"}`.
`BOJobCreateRequest` and the job `params` schema gain an optional
`bo_parameters`.

### 6. Frontend

- **Adapters tab** (`frontend/features/adapters.js`,
  `frontend/partials/tab-adapters.html`): when the user initiates a report
  download, `GET .../parameters` on demand. If the report has prompts, render
  an input per prompt — a date picker for `DateTime`, a text field
  (labelled with the prompt name) for pass-through types — collect values,
  and POST to the new download endpoint. No prompts → existing GET download.
- **Job editor** (BO-report job): a "Report parameters" section with the same
  input shapes (date picker + text), persisted to `params.bo_parameters`.

## Data flow

**Interactive**
1. User opens a report to download.
2. Frontend `GET /sap-bo/documents/{doc_id}/parameters`.
3. Prompts present → render inputs → user fills → `POST
   .../reports/{report_id}/download {format, parameters}`.
4. Route reads app tz → service `build_parameter_answers` (date→UTC Z) →
   `answer_document_parameters` (PUT occ-0) → `download_report` (GET export).
5. File returned.

**Job**
1. Job saved with fixed `params.bo_parameters`.
2. run_executor resolves app tz → builds answers → PUT occ-0 → download →
   compare.

## Error handling

- Discovery `404` → `ReportNotFoundError`; other `>=400` on discovery or
  answer → `BOAPIError` → the existing `_friendly_error`-wrapped
  `HTTPException(502)`.
- Invalid date input → FastAPI 422 before the service (pydantic validation on
  the request body / date parsing).
- Occ-0-answer-vs-session uncertainty: answered parameters and the export
  request are logged so a deployment where export ignores occ-0 answers is
  diagnosable, not silently wrong.

## Testing

- **client** (`tests/unit/test_bo_rest_client.py`): `get_document_parameters`
  parse (flat + nested wrap, `type`/`mandatory` extraction, 404→
  `ReportNotFoundError`); `answer_document_parameters` builds the exact PUT
  URL, query string, and body from the captured trace.
- **parameters util** (`tests/unit/test_sap_bo_parameters.py`, new):
  `DateTime` date→UTC-Z conversion, fixed-offset vs DST zone behaviour,
  full-datetime and non-date pass-through.
- **service** (`tests/unit/test_adapter_service.py`): download with
  parameters answers strictly before download (call-order assertion); tz
  threaded from the route value.
- **routes** (`tests/unit/test_adapters_routes.py`): parameters endpoint
  wiring; POST download with parameters; 422 on malformed date;
  config-not-found / auth-failure 502.
- **run_executor** (`tests/unit/test_run_executor_live.py` or peer): all
  three BO cases answer `bo_parameters` before `download_report`, and skip
  answering when absent.
- **mock server** (`docker/sapbo-mock/server.py`): add a `parameters` GET
  handler and an occurrence-0 `parameters` PUT handler; an integration test
  covering discover → answer → download.

## Out of scope (YAGNI)

- Relative date tokens (`today`/`T-1`) — fixed date chosen.
- LOV value pickers / validation — non-date prompts are raw pass-through.
- Answering any occurrence other than `0`.
