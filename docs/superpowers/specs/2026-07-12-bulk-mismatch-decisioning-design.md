# Bulk Mismatch Decisioning on Filtered/All Rows — Design

## Goal

Today's bulk decisioning (History → Run Detail → Decision, shipped in a prior
change) only operates at the *test-result* granularity: check one or more
failed test rows, then "Accept all mismatches" / "Pass with actions" acts on
every mismatch inside those results. There is no way to decide a *subset* of
mismatches within a result by search/column/type — you either accept
everything in a result or nothing.

This change adds mismatch-level bulk decisioning, scoped by the same
search/column/type filters the Differences Explorer already exposes, with
"all rows in this result" as the natural zero-filter case of the same
control. It also introduces a real `reject` decision — today the frontend
references `m.rejected` in one place (the mismatch drawer's status filter)
but no backend concept of rejection exists; that filter branch is dead code.

## Non-goals

- No change to the existing result-level bulk-accept / bulk-override
  endpoints or their UI (Run Detail table checkboxes) — those stay as-is.
- No change to `accepted_counts` / run-level insights aggregation semantics.
  A rejected mismatch continues to count as "open" there; teaching those
  aggregates about rejection explicitly is a follow-up, not required for
  this change to be usable.
- No change to the static HTML report.
- No bulk-undo ("un-decide") action — deciding a row again (accept after
  reject, or vice versa) overwrites the prior decision, which is sufficient.

## 1. Data model

Add four columns to `MismatchDetail` (`etl_framework/repository/models.py`),
mirroring the existing `accepted_*` columns:

```python
rejected      = Column(Boolean, nullable=False, default=False)
rejected_note = Column(Text, nullable=True)
rejected_at   = Column(DateTime(timezone=True), nullable=True)
rejected_by   = Column(String(255), nullable=True)
```

Add the matching sqlite backward-compat `ALTER TABLE` block to
`_ensure_compare_columns` in `etl_framework/repository/database.py`, using
the exact same guarded pattern already used for `accepted`/`accepted_note`/
`accepted_at`/`accepted_by`.

**Invariant:** a mismatch is in exactly one of three states — `pending`
(`accepted=False, rejected=False`), `accepted` (`accepted=True`), or
`rejected` (`rejected=True`). Any code path that sets `accepted=True` must
clear `rejected` (and its note/at/by) to `False`/`None`, and vice versa. This
applies to the existing single-accept path, the existing result-level
bulk-accept path, and the new endpoints below.

## 2. Repository layer

`etl_framework/repository/repository.py`:

- Add `rejected: bool | None = None` parameter to `_apply_mismatch_filters`,
  `list_mismatches`, and `count_mismatches`, filtering
  `MismatchDetail.rejected == rejected` when not `None` — symmetric with the
  existing `accepted` parameter.
- Add a `status: str | None = None` convenience parameter to the same three
  methods (`"pending" | "accepted" | "rejected"`), translated internally to
  the equivalent `accepted`/`rejected` boolean pair:
  - `pending` → `accepted=False, rejected=False`
  - `accepted` → `accepted=True`
  - `rejected` → `rejected=True`
  `status` and the raw `accepted`/`rejected` params are mutually exclusive
  inputs at the route layer (route validates only one style is used); the
  repository just needs to accept whichever resolved boolean pair it's
  given. (Keeping both param styles avoids breaking the existing `accepted`
  query param that Differences Explorer and its tests already depend on.)
- Add `reject_mismatch(mismatch_id, note, rejected_by) -> tuple[MismatchDetail, bool]`,
  mirroring `accept_mismatch`: sets `rejected=True` (+note/at/by), clears
  `accepted`/`accepted_note`/`accepted_at`/`accepted_by`. Returns
  `(row, status_changed)` — `status_changed` is always `False` here since
  rejecting never flips a `TestResult` to PASSED (see §4).
- Update `accept_mismatch` to also clear `rejected`/`rejected_note`/
  `rejected_at`/`rejected_by` when accepting (enforces the invariant).
- Add `bulk_decide_mismatches(result_id, decision, note, decided_by, *, search=None, column=None, mismatch_type=None, status=None) -> dict`:
  - Builds its row-set via `_apply_mismatch_filters` (same helper the list/
    count endpoints use — one source of truth for "what does this filter
    match").
  - For `decision="accept"`: only touches rows where `accepted is False`
    (matching today's `bulk_accept_mismatches` semantics — already-accepted
    rows are left alone, but previously-rejected rows in the filter get
    flipped to accepted per the overwrite rule above).
  - For `decision="reject"`: only touches rows where `rejected is False`.
  - Returns `{"decided_count": int, "result_status_updated": bool, "matched_count": int}`.
    `matched_count` is the filtered row count *before* excluding
    already-in-that-state rows (what the confirm-modal count should show);
    `decided_count` is how many rows actually changed.

## 3. API layer

`api/schemas.py`:

- `MismatchStatusFilter(str, Enum)`: `pending`, `accepted`, `rejected`.
- `MismatchRejectRequest(BaseModel)`: `note: str = Field(min_length=1, max_length=1000)`,
  `rejected_by: str | None = None` — mirrors `MismatchAcceptRequest`.
- Extend `MismatchOut` with `rejected: bool`, `rejected_note: str | None`,
  `rejected_at: datetime | None`, `rejected_by: str | None` (additive).
- `MismatchDecisionOut(BaseModel)`: `id`, `accepted`, `accepted_note`,
  `accepted_at`, `accepted_by`, `rejected`, `rejected_note`, `rejected_at`,
  `rejected_by` — replaces the narrower `MismatchAcceptOut` as the response
  model for *both* `/accept` and the new `/reject` single-row endpoints
  (`MismatchAcceptOut` is kept as a type alias of `MismatchDecisionOut` for
  compatibility with anything importing the old name).
- `BulkMismatchDecisionRequest(BaseModel)`: `decision: Literal["accept", "reject"]`,
  `note: str = Field(min_length=1, max_length=1000)`, `decided_by: str | None = None`,
  `search: str | None = None`, `column: str | None = None`,
  `mismatch_type: MismatchTypeFilter | None = None`,
  `status: MismatchStatusFilter | None = None`.
- `BulkMismatchDecisionOut(BaseModel)`: `decision: str`, `decided_count: int`,
  `matched_count: int`, `result_status_updated: bool`.

`api/routes/runs.py`:

- `POST /{run_id}/results/{result_id}/mismatches/{mismatch_id}/reject`
  (mirrors `accept_mismatch`): 404 if run/result/mismatch not found or
  mismatch doesn't belong to `result_id`; calls
  `repo.reject_mismatch(...)`; audit-logs `mismatch.rejected`; returns
  `MismatchDecisionOut`.
- `POST /{run_id}/results/{result_id}/mismatches/bulk-decide`: 404 if run or
  result not found (same pattern as `list_result_mismatches`); calls
  `repo.bulk_decide_mismatches(...)`; audit-logs `mismatch.bulk_decided`
  with `decision`, filters used, `decided_count`, `matched_count`; returns
  `BulkMismatchDecisionOut`.
- Extend `GET /{run_id}/results/{result_id}/mismatches` (existing endpoint)
  with the same optional `rejected: bool | None` and `status: MismatchStatusFilter | None`
  query params, forwarded to `list_mismatches`/`count_mismatches` — this is
  what lets the Differences Explorer filter by rejection status and get an
  accurate `X-Total-Count` for the bulk-decide confirm modal.

## 4. Status-flip semantics

Unchanged for accept: once every mismatch in a result is `accepted`, the
`TestResult` flips to `PASSED` (same logic as today's
`bulk_accept_mismatches`, reused via a shared helper rather than duplicated).

Reject never flips a result to `PASSED` — a rejected mismatch represents a
confirmed real discrepancy, so a result containing any rejected row can
never auto-pass via decisioning. This also means rejecting is a one-way
brake: if a user rejects one row in an otherwise-fully-accepted result, the
result stays FAILED until that row is re-decided as accepted.

## 5. Frontend — Differences Explorer tab

`frontend/app.js` / `frontend/index.html`, "Differences" tab (`frontend/index.html`
around the existing `diffAccepted` filter select):

- Replace the `diffAccepted` ("Accepted + open" / "Accepted only" / "Open
  only") select with `diffStatus` (`''` / `pending` / `accepted` / `rejected`),
  sent as the `status` query param.
- Add a bulk-decide bar above the results table (same visual pattern as the
  Run Detail bulk-decision bar): shows `diffTotal` ("N matching rows") and
  two buttons, "Accept all N filtered" / "Reject all N filtered" — the
  button label already reads correctly as "accept/reject all rows" when no
  filters are set, since `diffTotal` is the unfiltered count in that case.
  No separate "all rows" mode/toggle needed.
- Clicking either button opens the same kind of note-entry confirm modal
  used by Run Detail bulk decisions (mode `'diff-accept'` / `'diff-reject'`),
  posting to the new `bulk-decide` endpoint with the current
  `diffSearch`/`diffColumn`/`diffType`/`diffStatus` values as filters. On
  success: close modal, toast with `decided_count`/`matched_count`, refetch
  `fetchDifferenceRows()` and `loadDifferenceInsights()`.

## 6. Frontend — mismatch drawer (Compare tab)

`frontend/app.js`, around `filteredMismatches` / `acceptAllVisibleMismatches`
(~line 3856-3891):

- `acceptAllVisibleMismatches` currently loops one API call per row over
  `this.drawer.rows` already paged into the drawer (capped at whatever's
  been loaded via "Load More" — not actually "all", despite the name).
  Replace it with `decideAllDrawerMismatches(decision)` that calls the new
  `bulk-decide` endpoint once, with `status: 'pending'` (matching "visible +
  not yet decided" intent) and a note, using `this.drawer.runId` /
  `this.drawer.result.id`. This makes the action correct for the *entire*
  result server-side, not bounded by pagination.
- After a successful bulk-decide, reset `this.drawer.offset = 0` and re-run
  the drawer's existing row-fetch to reload from the server (so `accepted`/
  `rejected` reflect the new state) rather than patching rows client-side.
- Add a mirrored "Reject all pending" button next to the existing "Accept
  all pending" button; both open the same small note-prompt used elsewhere
  (reuse the `bulkDecisionForm` pattern rather than inventing a new modal).
- The drawer's `mismatchStatusFilter` (`ALL`/`ACCEPTED`/`REJECTED`/`PENDING`)
  starts working correctly for `REJECTED` once `m.rejected` is a real field
  coming back from the API instead of always-undefined.

## 7. Tests

- Repository: `rejected`/`status` filter tests in
  `tests/unit/test_mismatch_search.py` (mirroring the existing
  `accepted`-filter tests); `reject_mismatch` test; mutual-exclusion tests
  (accept-after-reject clears rejected fields and vice versa);
  `bulk_decide_mismatches` tests for both decisions, including the
  "rejected row blocks auto-pass" case and the "matched_count vs
  decided_count" distinction when some matched rows are already in the
  target state.
- Route: `tests/unit/test_bulk_decisioning.py` — extend with `/reject`
  single-row tests and `/mismatches/bulk-decide` tests (filtered subset,
  zero-filter "all rows", 404s, decision literal validation).
- No new frontend test tooling exists in this repo (manual browser
  verification only, per the existing pattern) — verification step will
  drive both the Differences Explorer bulk bar and the drawer's bulk
  accept/reject buttons in a real browser against seeded mismatch data,
  same as the prior bulk-decisioning verification.

## Open items intentionally deferred

- `accepted_counts` / run insights / static report treating `rejected` as
  its own bucket (currently folds into "open") — noted as non-goal above.
- Bulk-decide across *multiple* results in one call (Differences Explorer
  and the drawer both operate on a single `result_id` today) — not needed
  since neither surface lets you pick more than one result at a time.
