# Troubleshooting a SAP BO report download

Written against the on-premises deployment, 2026-08-03. Ordered cheapest-first:
each step either explains the symptom or rules out a whole class of cause.

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

**Test it in a minute:** re-run the download with every prompt filled with a
real value. If rows appear, that was the cause.

## 3. Check the prompt answer types

```
types=['DateTime','string']
```

The answer PUT's accepted vocabulary is capitalised (`String`, `DateTime`). The
parameters *listing* uses a different vocabulary per prompt kind — `"Text"` and
lowercase `"string"` have both been observed. `_ANSWER_TYPE_ALIASES` in
`etl_framework/sap_bo/parameters.py` maps only exact `"Text"` → `"String"`;
anything else passes through verbatim. A type BO does not recognise can be
accepted with a 200 and then ignored.

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

Every tabular export then probes the live session and logs four lines:

```
SAP BO blank-export diagnostic [dataproviders] ...
SAP BO blank-export diagnostic [occurence-0-cold] ...
SAP BO blank-export diagnostic [open-document] ...
SAP BO blank-export diagnostic [occurence-0-after-open] ...
```

Read them as:

| Observation | Meaning |
|---|---|
| `dataproviders` shows a recent execution / row count | Data ran and genuinely returned nothing — go back to steps 2–5 |
| `dataproviders` shows no execution | The document never refreshed on this path |
| `occurence-0-cold` is 200 | Occurrence 0 is addressable without a viewing session |
| cold 404, `after-open` 200 | Occurrence 0 is created by opening the document — the open is a required step we skip |
| both 404 | Occurrences are portal-only; the answer is a document-level refresh |

Note BO's own spelling: `occurences`, single r.

## 7. Known traps

- **The mock cannot falsify a server assumption.** `docker/sapbo-mock` is
  written from the same spec as the client. It has now hidden five separate
  live behaviours (pagination, CeQL literals, nested `answer.type`, the
  occurrence 404 generalisation, and non-date prompt answers, which
  `_answered_date` records and then ignores). A green e2e against the mock is
  not evidence about the server.
- **Do not copy session-scoped ids out of a browser trace.** A captured trace
  is authoritative for the request *body* and worthless for any id the viewing
  session minted for itself. An `occurrences/1` copied this way 404s.
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
