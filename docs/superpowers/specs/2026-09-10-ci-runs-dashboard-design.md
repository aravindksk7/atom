# CI Runs Dashboard — Design Spec

**Date:** 2026-09-10
**Status:** Approved (brainstorming session)
**Sub-project:** 4 of 4 (final) in the GitLab CI/CD uplift, following sub-project 1
(`2026-09-09-sequence-cicd-launch-parity-design.md`), 2
(`2026-09-10-gitlab-native-ci-signals-design.md`), and 3
(`2026-09-10-scoped-ci-trigger-tokens-design.md`).

## Problem

Every CI-triggered run has carried a `ci_context` (`commit_sha`, `pipeline_url`, `ref`,
`triggered_by`) since the original CI integration, but nothing in the product surfaces
it as a distinct view. `GET /api/runs`'s list response (`RunStatusOut`) doesn't even
include `ci_context` — a user browsing the History tab cannot tell which runs came from a
pipeline versus a human clicking Launch, let alone which commit or pipeline a given run
correlates to, without opening each run's detail payload individually. There is no way to
answer "how has CI been doing this week" at a glance.

## Solution

Surface CI-triggered runs as their own read-side view: a `ci_only` filter added to the
existing run list machinery (no new table, no duplicated listing logic), a small summary
endpoint for aggregate stats, and a new **CI Runs** tab in the frontend — mirroring how
**Scheduler Reports** already exists as a specialized analytics view alongside the
generic **History** tab, for a different trigger source (schedules instead of CI).

This is purely additive and read-only. No launch endpoint, CLI, or CI script changes —
`ci_context` has been populated on every CI-triggered run since sub-project A/B; this
sub-project only makes that data visible.

---

## 1. Data Model

No new columns, no new tables. `TestRun.ci_context` (existing, nullable JSON) is the
sole signal: a run is "CI-triggered" iff `ci_context IS NOT NULL`.

---

## 2. API

### `RunRepository.list_runs` — one new filter

```python
def list_runs(
    self, limit: int = 50, offset: int = 0,
    status: str | None = None, run_type: str | None = None,
    ci_only: bool = False,
) -> list[TestRun]:
    q = self._db.query(TestRun)
    if status:
        q = q.filter(TestRun.status == status)
    if run_type:
        q = q.filter(TestRun.run_type == run_type)
    if ci_only:
        q = q.filter(TestRun.ci_context.isnot(None))
    return apply_pagination(q.order_by(TestRun.id.desc()), limit, offset).all()
```

`GET /api/runs` (`api/routes/runs.py::list_runs`) gains a matching `ci_only: bool = False`
query parameter, passed straight through — same shape as its existing `status`/`run_type`
params, no new dependency-injection pattern needed for one boolean.

### `RunStatusOut` — three new fields

```python
ci_context: dict[str, Any] | None = None
target_type: Literal["selection", "sequence"] | None = None
target_name: str | None = None
```

`_run_status_out` (the function every list/detail caller already goes through) computes
`target_type`/`target_name`:

- `config_snapshot["sequence"]` present → `("sequence", config_snapshot["sequence"]["name"])`
  — free, already stored per-run, no query.
- else `run.selection_id` set → `("selection", <looked up name>)`.
- else `(None, None)` (a manual or scheduled run, or a CI-triggered run using the legacy
  direct-`/api/runs` path from `.github/workflows/ci.yml`, which has no selection/sequence
  to name).

Selection-name lookup cannot run per-row (N+1 on a list of 50). `list_runs` (the route,
not the repository method) collects the distinct `selection_id`s across the page **once**
and calls a new batched repository method:

```python
def names_by_ids(self, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = self._db.query(JobSelection.id, JobSelection.name).filter(JobSelection.id.in_(ids)).all()
    return dict(rows)
```

on `JobSelectionRepository`, then passes the resulting `{id: name}` map into
`_run_status_out` (or a thin wrapper) for that page's rows.

### `GET /api/runs/ci-summary` (new)

New thin route in `api/routes/runs.py`, backed by a new
`api/services/ci_runs_reporting.py` — mirroring the existing
`api/routes/scheduler_reports.py` / `SchedulerReportingService` split (route stays thin,
the aggregation logic is testable in isolation).

```
GET /api/runs/ci-summary?days=30&target_type=selection|sequence&status=...
```

```python
@dataclass
class CiRunsFilters:
    days: int = 30           # 1..365
    target_type: str | None = None
    status: str | None = None
```

Response:

```json
{
  "total": 42,
  "passed": 30, "failed": 8, "error": 2, "cancelled": 2,
  "pass_rate": 0.71,
  "by_target_type": {"selection": 35, "sequence": 7}
}
```

Computed over `TestRun.ci_context IS NOT NULL AND started_at >= now - days`, with the
same optional `target_type`/`status` narrowing the list endpoint supports (so the summary
strip and the table below it can be driven by the same filter state in the frontend).
`pass_rate` is `passed / total` (0 when `total == 0`, never a division error).

---

## 3. Frontend

New tab, `id: 'ci-runs'`, `label: 'CI Runs'`, `group: 'analysis'` (next to `history` and
`scheduler-reports` in `frontend/app.js`'s tab list) — `frontend/features/ci-runs.js` +
`frontend/partials/tab-ci-runs.html`, following the project's existing feature-slice
module pattern.

**Summary strip** (top): total CI runs, pass rate, a small breakdown by target type —
same visual weight as Scheduler Reports' existing summary card, not a new visual
language.

**Filters**: `days` (default 30, matching the summary endpoint's default), `target_type`
(all/selection/sequence), `status`. Changing any filter re-fetches both the summary and
the table.

**Table**: status (existing emoji convention), target (`target_type` + `target_name`,
linking to the Sequences tab or the Launch tab's selection list — reusing existing
navigation, not a new detail page), short commit sha (`ci_context.commit_sha[:8]`),
pipeline (external link icon, `href="ci_context.pipeline_url"`, opens in a new tab), ref
(`ci_context.ref`), started, duration. Row click opens the same run-detail view History
already uses (reuse `selectedRun`/whatever mechanism `frontend/features/history.js`
already has — no new detail UI is built).

A run whose `ci_context` is present but has no `pipeline_url`/`ref` (possible if a caller
sets `ci_context` manually via the API rather than through the CI scripts) shows an em
dash for the missing field rather than a broken link.

---

## 4. Error Handling

| Situation | Behaviour |
|---|---|
| `days` out of `1..365` | `422` (matches `scheduler_reports.py`'s existing `Query(..., ge=1, le=365)` convention). |
| No CI-triggered runs exist yet | Summary returns `total: 0`, `pass_rate: 0`; table renders the existing empty-state pattern other tabs already use. |
| A CI-triggered run has no resolvable `target_name` (selection since deleted, or the legacy direct-`/api/runs` path) | `target_type`/`target_name` are `null`; the table shows "—" rather than erroring. |
| `pipeline_url` missing or malformed | Link is simply not rendered (em dash), never a broken/unsafe `href`. |

---

## 5. Testing

- **Unit:** `list_runs`'s `ci_only` filter (with/without other filters combined);
  `names_by_ids` batching (empty list, partial matches, no N+1 — assert query count if
  the test harness supports it, otherwise assert correctness); `ci-summary`'s aggregation
  math (`pass_rate` including the `total == 0` case) and its `days`/`target_type`/`status`
  filtering.
- **E2E (Playwright, live backend):** seed one CI-triggered run (via the existing
  selection-launch-with-`ci_context` pattern already used in
  `tests/e2e/40-live-docker-gitlab-retry.spec.ts`'s style) and one manual run, open the
  CI Runs tab, and assert only the CI-triggered run appears, with the correct commit/ref/
  target columns.

---

## Out of Scope

- Grid/timeline/export/prune views (Scheduler Reports has these; this first cut is
  list + summary only, per the brainstorming decision — can be added later if wanted,
  following the same service-class pattern).
- Any change to `.github/workflows/ci.yml`'s direct-`/api/runs` path to also carry a
  resolvable target name — it has none today (raw `job_sequence`, no selection/sequence),
  out of scope for a read-only dashboard.
- A per-run deep-link route (`#/runs/<id>`) — the same pre-existing frontend gap noted as
  out of scope in sub-project 2's spec; this dashboard reuses History's existing
  run-detail mechanism, whatever it currently is.
