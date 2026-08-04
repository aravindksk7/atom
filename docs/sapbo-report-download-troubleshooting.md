# Troubleshooting a SAP BO report download

Written against the on-premises deployment, 2026-08-03; root cause added
2026-08-04. Ordered cheapest-first: each step either explains the symptom or
rules out a whole class of cause.

## The cause found on 2026-08-04 — check this first

A UI trace of document 124313 showed the on-premises web UI doing exactly two
requests, no snapshot and no schedule, **both against occurrence 0**:

```
PUT  …/documents/124313/occurrences/0/parameters
       ?dataproviderScope=accessible&lovInfo=false&prepare=false
     -> 200 {"success":{…,"details":{"property":[
             {"@key":"allDataprovidersRefreshed","$":"true"}]}}}

GET  …/documents/124313/occurrences/0?dpi=96&optimized=true&reportIds=1
     Accept: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
```

This client had been answering the **document-level** `…/documents/{id}/parameters`
and exporting from `…/documents/{id}/reports/{id}`. Both return 200. Neither
refreshes the data providers the export reads — hence a valid workbook with
column headers and no data rows. Fixed 2026-08-04 in
`etl_framework/sap_bo/client.py` (`answer_document_parameters`, `download_report`).

`allDataprovidersRefreshed: "true"` is the only positive evidence this flow
produces. The client now logs a warning whenever the answer PUT comes back
without it — if you see that warning, the export after it will be blank.

## What the client sends around those two requests

SAP's published Raylight sequence brackets the pair above with two steps this
client did not have. Both were added 2026-08-04; neither can fail a download:

```
GET  …/documents/{id}                       <- opens the document (session cache)
PUT  …/documents/{id}/occurrences/0/parameters
POST …/documents/{id}/parameters   body {}  <- only if the PUT did not refresh
GET  …/documents/{id}/occurrences/0?reportIds={r}
```

- **The open** (`_open_document`) is what SAP documents before any parameter
  write. The client had been writing straight to occurrence 0, which worked
  only because occurrence 0 was reachable cold here — the question
  `probe_occurrence.py` exists to settle. A deployment that refuses the open
  logs `could not open document … to seed the session cache` and the answer PUT
  still runs; that PUT's own error is the one that surfaces.
- **The refresh POST** (`_trigger_parameter_refresh`) is SAP's documented
  trigger, used here only as an escalation: it fires when the PUT came back
  without `allDataprovidersRefreshed=true`, i.e. in the state that used to
  export layout with no rows. It never raises — a download already headed for a
  blank export is more useful as an inspectable empty workbook than as an
  opaque error.

One documented step is deliberately unimplemented: the document-level
`PUT …/documents/{id}/parameters` is the resource this deployment accepts with
200 and then ignores, so it is never written to.

### Whole-document exports

Naming no report exports every tab in one file — SAP's primary step 5, where
`…/reports/{id}` is only its single-tab alternative. `reportIds` is *omitted*
rather than sent empty, which BO would read as the tab called `""`:

| Scope | UI | Route | Occurrence GET | 404 fallback |
|---|---|---|---|---|
| One tab | per-report **XLSX**/**PDF** | `…/documents/{id}/reports/{r}/download` | `?reportIds={r}` | `…/documents/{id}/reports/{r}` |
| Every tab | **All tabs** row, shown when a document has >1 tab and no filter is active | `…/documents/{id}/download` | no `reportIds` | `…/documents/{id}` |

A whole-document workbook is **one sheet per tab**, and
`pd.read_excel(…)` reads only the first. Anything that turns such an export
into a frame therefore passes `combine_sheets=True`
([`read_tabular`](../api/services/file_source.py)), which unions every sheet —
columns merged, blanks where a tab lacks one. It is opt-in: uploads,
reconciliation targets and multi-file baselines have always compared sheet 1,
and changing that silently would change what already-saved jobs compare.

Whole-document is selectable everywhere a single tab is:

| Surface | How to ask for it |
|---|---|
| Adapters tab | **All tabs** row → XLSX / PDF / **+ Job** |
| Compare tab | report select → **All tabs (whole document)** |
| Column stats | Report ID field → `*` |
| Jobs (`bo_report`, `bo_live`) | omit `bo_report_id`; `report_id` (the document) stays required |

The UI keeps `*` distinct from an untouched select: a blank report is still
rejected with "Select a report", so forgetting to choose one can never quietly
become a whole-document pull.

### The occurrence path is spelled two ways

BO has shipped both `occurrences` (the captured trace, and current docs) and
the misspelled `occurences`. Both the answer PUT and the export try the
documented spelling first and retry the other on a 404; the first non-404
pins the spelling for the rest of that client's life, so the probe costs one
extra request per process on an affected deployment, not per download. A 404
from *both* still raises — the retry is for a spelling difference, not for a
genuinely missing occurrence.

Both entry points share this code, so both are fixed together:

| Entry point | Path |
|---|---|
| Web UI, prompted report | `POST /api/adapters/sap-bo/documents/{id}/reports/{r}/download` → `AdapterService.download_bo_report` → answer + export |
| Web UI, report with no prompts | `GET …/download` → export only |
| Scheduled ETL job (`bo_report`/`bo_live`) | `run_executor` → `job.params["bo_parameters"]` → answer + export |

A report **with no prompts** is exported with no answer PUT before it, so
nothing guarantees it has an occurrence. That download falls back to
`…/documents/{id}/reports/{id}` on a 404 and logs `occurrence 0 unavailable`.
Seeing that line for a *prompted* report means the export is about to be
blank — treat it as the failure, not as a recovery.

The dominant failure on this deployment is **not** an error. The download
returns HTTP 200, the file opens, and the report is empty. Every layer reports
success. So the first rule is:

> **HTTP 200 and a well-formed file are not evidence the pull worked.**
> Assert on row counts and cell contents, never on status codes.

## 0. Establish what "blank" means

Open the downloaded file and classify it. The three shapes have different
causes and the fix differs for each:

| Shape | Meaning |
|---|---|
| 0 bytes, or not a zip | Transport / auth / error page returned as a file |
| Valid workbook, **no** headers | Wrong report tab (`report_id`), or empty report |
| Valid workbook, headers present, **no data rows** | The common case — read on |

The third is what this deployment produces. The layout exported fine; only the
data is missing.

## 1. Read the export log line

Every export logs one line (`etl_framework.sap_bo.client`):

```
SAP BO export doc=124313 report=1 fmt=xlsx -> HTTP 200,
  content-type=...spreadsheetml.sheet, bytes=5783, rows=17
SAP BO export doc=124313 report=1 cell preview: <first cell strings>
```

`rows` counts `<row>` elements, **including layout rows**. A WebI sheet carries
a title row, filter-summary rows and column headers even over an empty table —
an observed data-less pull reported `rows=17`. So `rows` alone never proves
data is present.

**The cell preview is what decides it.** If every string is a title, a filter
caption or a column name, the table is empty regardless of the row count.

## 2. Check how many prompts the report has

```
SAP BO answering 2 parameter(s) on document 124313 (ids=[0,1], types=['DateTime','string'])
```

If this says more than one parameter, check the extra prompts **before**
anything else. The most common cause of a headers-only export is an
optional prompt that the user left blank.

The UI sends *every* prompt, and blanks become empty strings
(`frontend/features/adapters.js`, `value: values[p.id] || ''`). Only
**mandatory** prompts are blocked when empty. An optional prompt therefore
goes to BO as `""`.

Answering a prompt with `""` is **not** the same as leaving it unanswered. BO
applies it as a filter, matches nothing, and exports the layout with zero rows.

This is a *hypothesis* — it was never confirmed live, and the 2026-08-04
occurrence-0 finding explains the blank exports on its own. The client now
names the offending prompts before the export:

```
SAP BO document 124313: prompt(s) [1] answered with an empty value.
```

**Test it in a minute:** re-run the download with every prompt filled with a
real value. If rows appear, that was the cause.

## 3. Check the prompt answer types

```
types=['DateTime','string']
```

The answer PUT's accepted vocabulary is capitalised (`String`, `DateTime`). The
parameters *listing* uses a different vocabulary per prompt kind — `"Text"` and
lowercase `"string"` have both been observed. `_ANSWER_TYPE_ALIASES` in
`etl_framework/sap_bo/parameters.py` now matches case-insensitively and maps
`text`/`string` → `String` and `datetime` → `DateTime`; anything else passes
through verbatim. A type BO does not recognise can be accepted with a 200 and
then ignored, so an unmapped type in this log line is still worth chasing.

## 4. Read what the server echoed back

```
SAP BO answer PUT for document 124313 -> HTTP 200: <body>
```

This is the server restating what it stored. Compare each answer against what
was sent. An answer that is missing, empty, or coerced here explains an empty
report without any further investigation.

## 5. Check the date value

`build_parameter_answers` converts a bare `YYYY-MM-DD` DateTime prompt to
local-midnight-as-UTC using the configured app timezone. This deployment is
~UTC+1, and two captured traces disagree on whether the offset is applied
(see the module docstring). A date shifted by one day returns a valid, empty
report.

Controls:
- Answer with the value BO itself reports as the prompt's current answer. Still
  empty → the value format is not the problem.
- Export the same document and date from the BI launchpad by hand. Empty there
  too → the date genuinely has no data, and nothing in this app is at fault.

## 6. Force the session diagnostics

If steps 1–5 do not explain it, run one pull with:

```powershell
$env:ATOM_BO_EXPORT_DIAGNOSTICS = "1"
```

Every tabular export then probes the document's data providers:

```
SAP BO blank-export diagnostic [dataproviders] ...
```

Read it as:

| Observation | Meaning |
|---|---|
| shows a recent execution / row count | Data ran and genuinely returned nothing — go back to steps 2–5 |
| shows no execution | The document never refreshed — check the answer PUT logged `allDataprovidersRefreshed=true`, and read the refresh POST that followed it |

The occurrence-0 probes that used to run here are gone: the export itself now
reads occurrence 0, so a missing occurrence raises on the export instead of
hiding in a probe line.

## 7. Known traps

- **The mock cannot falsify a server assumption.** `docker/sapbo-mock` is
  written from the same spec as the client. It has now hidden five separate
  live behaviours (pagination, CeQL literals, nested `answer.type`, the
  occurrence 404 generalisation, and non-date prompt answers, which
  `_answered_date` records and then ignores). A green e2e against the mock is
  not evidence about the server.
- **Do not copy session-scoped ids out of a browser trace.** A captured trace
  is authoritative for the request *body* and worthless for any id the viewing
  session minted for itself. An `occurrences/1` copied this way 404s — while
  occurrence **0**, the document's persisted one, is the correct target.
- **One 404 is not a rule.** That `occurrences/1` 404 was generalised to "a
  stateless client has no occurrence", which sent both the answer and the
  export to document-level paths and caused the blank exports for a week.
- **A 502 from this app is usually our own `_friendly_error` wrapper** around
  BO's real status. Read the response body for the true code.
- **`c=<epoch>` in captured UI URLs is a cache buster** — it decodes to
  wall-clock at capture time, not the report date. It carries no meaning.

## 8. Deeper probe

For questions the app's own logging cannot answer, a standalone probe runs the
same pull several ways (cold vs opened document, `prepare` true/false,
occurrence 0 vs document-level export, xlsx vs csv) and reports the row count
of each. Ask for `bo_probe.py`; it is read-only against the repository — it
never saves a document, never DELETEs, never schedules.
