# SAP BO report run-date filter — design

## Problem

The Adapters tab's SAP BO browse (`frontend/features/adapters.js`,
`BORestClient.list_documents`/`list_reports`) shows every WebI document in
the repository, with a client-side text search over name/folder/id
(`boFilterQuery` / `filteredBODocs`). There is no way to narrow that list to
"documents that ran on a particular day" — a user chasing down a specific
day's report output has to scroll/search a flat list of everything instead.

SAP BO doesn't store a "last run date" directly on the WebI document
InfoObject. Runs are separate CMS objects ("instances"), each with its own
`SI_STARTTIME`, linked back to the document that owns them via `SI_PARENTID`
(an instance's parent in the CMS hierarchy is the report it belongs to).
Filtering "by run date" therefore means: find instances whose `SI_STARTTIME`
falls within the selected day, and surface the distinct set of documents
those instances belong to.

## Approach

Add a date input next to the existing search box in the Adapters tab. On
change, the frontend calls a new endpoint that queries SAP BO's CMS query
service (`/biprws/v1/cmsquery`, already integrated for the CMS-query
document-listing fallback — see `_list_documents_via_cms_query` and
`_paginate_biprws_collection`'s docstring for why this specific deployment
needs it) for instances started that day, returning the distinct parent
document ids. The frontend ANDs that id set into the existing
`filteredBODocs` computation alongside the existing text search, rather than
replacing it — a user can combine "ran on 2026-07-20" with a name search.

This reuses the already-proven keyset pagination pattern
(`TOP N ... WHERE SI_ID > :cursor`) from `_list_documents_via_cms_query`
rather than inventing a new mechanism, since a busy day's instance count for
a 5000+ document repository could plausibly exceed one CeQL default batch.

**Known-uncertain pieces** (can't be verified without live access to the
on-prem server, flagged explicitly rather than silently assumed):
- CeQL date-literal syntax: `@yyyy.MM.dd.HH.mm.ss` per SAP BOE CeQL
  documentation/convention. If the live server rejects this syntax, the
  query itself will 400 and the fallback (below) kicks in immediately,
  making this diagnosable on first use rather than silently wrong.
- `SI_PARENTID` as the instance→document link. This follows BOE's InfoStore
  hierarchy model (an instance's parent IS the report that owns it) but has
  not been confirmed against this specific deployment's actual instance
  data.

**Out of scope:** filtering `list_reports` (report tabs within one
document) by run date — report tabs aren't separately-scheduled CMS objects
with their own run history; only the parent WebI document has instances.
Out of scope: a standalone run-history/audit view (every instance as its
own row); this only narrows the existing document tree, per user's explicit
choice during design.

## Components

1. **`etl_framework/sap_bo/client.py` — `BORestClient`**

   New method, following this file's existing lazy-auth / soft-fail-on-CMS-
   query-unavailable conventions (mirrors `_list_documents_via_cms_query`):

   - `list_document_ids_with_runs_on(day: date) -> list[str] | None` —
     keyset-paginated CeQL query:
     ```sql
     SELECT TOP 200 SI_ID, SI_PARENTID FROM CI_INFOOBJECTS
     WHERE SI_INSTANCE=1
       AND SI_STARTTIME >= @2026.07.20.00.00.00
       AND SI_STARTTIME < @2026.07.21.00.00.00
       AND SI_ID > :last_seen_id
     ORDER BY SI_ID
     ```
     (no `SI_ID > :cursor` clause on the first page, same convention as
     `_list_documents_via_cms_query`). Collects distinct `SI_PARENTID`
     values across all pages, deduping as it goes. Returns `None` if the
     *first* query fails (endpoint unavailable/unsupported — same
     "unsupported vs. zero results" distinction as the existing CMS-query
     method); a failure on a later page keeps whatever distinct ids were
     already collected.

2. **`api/services/adapter_service.py` / `api/routes/adapters.py`**

   - `AdapterService.list_bo_document_ids_with_runs_on(config_id, day, auth)`
     — same connect/authenticate/logout-in-finally shape as
     `list_bo_documents`. Raises the existing `_friendly_error`-wrapped
     `HTTPException(502)` on failure (consistent with every other adapter
     endpoint) — no soft-fail at this layer; `None` from the client (query
     unsupported) is surfaced as an explicit empty result with a
     `supported: false` flag so the frontend can tell "no matches" apart
     from "date filtering isn't available on this server" instead of
     guessing from an empty list.
   - New route: `GET /api/adapters/sap-bo/documents/ran-on` —
     query params `config_id`, `date` (ISO `YYYY-MM-DD`, parsed with
     `date.fromisoformat`, 422 on bad format via FastAPI's own validation).
     Response: `{"document_ids": [...], "supported": true}`.

3. **`frontend/features/adapters.js`, `frontend/partials/tab-adapters.html`**

   - New state: `boRanOnDate` (bound to a new `<input type="date">` next to
     the existing search box), `boRanOnDocIds` (`Set<string> | null`),
     `boRanOnSupported` (`bool`, default `true`).
   - `loadBORanOnDate()` — fires on date input change (debounced the same
     way the existing search doesn't need to be, since this is a discrete
     "on change" event, not a keystroke stream). Empty date clears
     `boRanOnDocIds` back to `null` (no filtering).
   - `boDocMatchesQuery(doc)` gains an additional AND clause: when
     `boRanOnDocIds` is not null, `doc.id` must be in it. Combines with the
     existing name/folder/id text match — both must pass.
   - When `boRanOnSupported` is `false` (server returned it), show an inline
     note near the date input ("run-date filtering isn't available against
     this SAP BO server") instead of silently filtering to nothing.

## Data Flow

1. User picks a date in the new input.
2. Frontend calls `GET /api/adapters/sap-bo/documents/ran-on?config_id=X&date=2026-07-20`.
3. Route → service → `BORestClient.list_document_ids_with_runs_on(date(2026,7,20))`.
4. Client keyset-paginates the CeQL instance query, collects distinct
   `SI_PARENTID`s, returns the list (or `None` if query itself unsupported).
5. Service returns `{"document_ids": [...], "supported": true}` (or
   `supported: false` with an empty list if the client returned `None`).
6. Frontend stores the id set, re-evaluates `filteredBODocs` (existing
   computed property, now ANDing text search with the id-set membership
   check).

## Error Handling

- CMS query endpoint unavailable at all (first page fails): service returns
  `supported: false`, frontend shows the inline note, does not filter.
- CMS query fails partway through (later page fails): keep whatever distinct
  ids were already collected (matches `_list_documents_via_cms_query`'s
  existing partial-data-on-later-failure behavior) — an incomplete-but-real
  filter beats none.
- Invalid `date` query param: FastAPI's own type validation returns 422
  before reaching the service.
- `config_id` not found / SAP BO auth failure: same `_friendly_error`-wrapped
  502 as every other adapter route — no new error handling needed here.

## Testing

- `tests/unit/test_bo_rest_client.py`: keyset pagination across multiple
  instance batches (mirrors `test_list_documents_cms_query_pages_past_its_own_default_result_cap`),
  dedup of repeated `SI_PARENTID`s within/across batches, `None` on first-page
  failure, partial-collection-kept on later-page failure, correct CeQL query
  text (date literal format, `SI_ID > :cursor` on non-first pages only).
- `tests/unit/test_adapters_routes.py`: route wiring, `supported: false`
  passthrough, 422 on malformed date, config-not-found/auth-failure 502.
- `tests/unit/test_adapter_service.py`: service-layer unit coverage of the
  new method, consistent with existing `list_bo_documents` test shape.
- No new Playwright/e2e coverage planned — the existing `05-adapters.spec.ts`
  live-mock suite doesn't simulate CMS query/instance data at all yet; adding
  that is a larger mock-server change out of scope for this feature unless
  the plan phase decides otherwise.
