# GitLab CI/CD Job Integration — Design Spec

**Date:** 2026-07-05
**Status:** Approved

## Problem

Job Selections (see [2026-07-04-job-selections-design.md](2026-07-04-job-selections-design.md)) let a user save and reuse a named, versioned list of jobs, but there is currently no way to trigger a selection from an external CI/CD pipeline (e.g. GitLab), have the pipeline fail/pass based on the run's outcome, and surface the latest result inside the repo itself. Today, results only live inside Atom's UI/API — nothing pushes a summary back into source control where a reviewer would naturally look (the README).

## Solution

Add a CI-friendly launch path on top of the existing Job Selection run flow, plus a new read-only endpoint that renders a run's results as markdown. A `.gitlab-ci.yml` pipeline stage (backed by a checked-in shell script) calls Atom's API to launch a selection, polls until the run reaches a terminal state, fetches the markdown summary, splices it into `README.md` between marker comments, and commits/pushes the update. The pipeline stage exits non-zero on run failure, so GitLab's own pass/fail UI reflects the Atom run — no GitLab-side API calls (commit status, MR comments) are needed for v1.

This is additive to the existing Job Selections and run-execution machinery: no new persisted CI config, no new token-scoping system, no changes to `JobSelection`/`JobSelectionVersion`.

---

## 1. Data Model

### `TestRun` (existing table — additive change)

One new nullable column:

| Column | Type | Notes |
|---|---|---|
| `ci_context` | json | nullable; `{commit_sha, pipeline_url, ref, triggered_by}` — set only when launched via the CI launch endpoint |

Runs launched manually or via `ScheduledRun` leave this column null, matching today's behavior for `selection_id`/`selection_version`. No changes to any other table.

---

## 2. API Changes

### `POST /api/selections/{id}/launch`

Existing/planned launch endpoint from the Job Selections spec, extended with an optional field:

```json
{
  "environment": "prod",
  "ci_context": {
    "commit_sha": "a1b2c3d",
    "pipeline_url": "https://gitlab.example.com/team/proj/-/pipelines/4821",
    "ref": "main",
    "triggered_by": "gitlab-ci"
  }
}
```

Returns `{"run_id": 123}` immediately (async — matches today's launch behavior). `ci_context`, if present, is stored verbatim on the created `TestRun`.

### `GET /api/runs/{run_id}/markdown-summary` (new)

Read-only. Reuses the per-job result data already computed for the existing run-detail view — no new aggregation logic. Renders:

- Header line: run timestamp, trigger source (`ci_context.pipeline_url`/`commit_sha` if present, else "manual" or "scheduled")
- A table: step #, job name, status (✅/❌/⚠️), duration, mismatch count (if applicable)
- A link back to the full run in Atom's UI

Both endpoints reuse the existing bearer-token auth middleware already applied to all `/api/*` routes — no new auth code.

---

## 3. Frontend: CI/CD Integration Panel

A new tab, "CI/CD Integration," on the Job Selection detail view (alongside existing Overview/Versions/Run History tabs). Contents:

1. **Create an API token** — shortcut to the existing `/api/tokens` creation flow (reused as-is; no new token scoping).
2. **GitLab CI/CD variables** to set (`ATOM_API_URL`, `ATOM_API_TOKEN`), shown as a copyable block.
3. **`.gitlab-ci.yml` snippet**, pre-filled with the selection's real ID, referencing a checked-in helper script (`scripts/ci/run-atom-selection.sh <selection_id>`).

This panel is purely generative — it renders a static template with the real ID substituted in. It does not persist any CI configuration server-side; there is no new database table backing it.

---

## 4. CI Script and README Update Mechanics

`scripts/ci/run-atom-selection.sh` (checked into the repo being tested, e.g. Atom's own repo) performs:

1. `POST /api/selections/{id}/launch` with `ci_context` populated from GitLab's predefined variables (`CI_COMMIT_SHA`, `CI_PIPELINE_URL`, `CI_COMMIT_REF_NAME`) → capture `run_id`.
2. Poll `GET /api/runs/{run_id}` every 10s until status is terminal (`passed`/`failed`/`error`/`cancelled`) or a configurable timeout (default 30 min) elapses.
3. `GET /api/runs/{run_id}/markdown-summary` → capture markdown.
4. Splice the markdown between two marker comments already checked into `README.md`:
   ```
   <!-- ATOM:JOB-STATUS:START -->
   ...replaced on each run...
   <!-- ATOM:JOB-STATUS:END -->
   ```
5. `git pull --rebase`, commit with `[skip ci]` in the message (to prevent a retrigger loop), push using the GitLab-provided job/deploy token. Retry the push once after a fresh rebase if it's rejected (handles a concurrent pipeline race).
6. Exit `0` if the run passed; exit non-zero otherwise, independent of whether the README push itself succeeded.

---

## 5. Error Handling

| Failure | Behavior |
|---|---|
| Launch call fails (bad token/selection id) | Script exits non-zero immediately; prints the API's error body. |
| Poll exceeds timeout | Script exits non-zero with a clear "run did not complete in time" message. The run itself is **not** cancelled — it keeps executing in Atom for later inspection. |
| Run reaches terminal state but `markdown-summary` fetch fails | Pipeline pass/fail is still determined by the already-known run status; the README-update step failing does not flip a passed run to failed. |
| README push rejected (race, branch protection) | One retry after `git pull --rebase`; if it fails again, logged as a warning — does not change the pipeline's pass/fail outcome. |

---

## 6. Testing

- **Unit:** `markdown-summary` endpoint formatting — various status combinations, missing `ci_context`, mismatch counts.
- **Integration:** launch → poll → fetch flow, using the same test-run fixtures as the Job Selections feature.
- **Script test:** marker-splice logic against sample README content — covers "no existing markers" (fails clearly) and "markers already present" (replaces only the content between them).
- **Manual:** run the actual `.gitlab-ci.yml` snippet against a real (or sandbox) GitLab project pointed at a local Atom instance.

---

## Out of Scope (v1)

- GitLab Commit Status API / MR comment integration (exit code is sufficient for now).
- Atom-initiated triggering of GitLab pipelines (reverse direction).
- Scoped/limited-permission CI tokens (reuses existing global bearer tokens).
- Persisting CI configuration server-side (the frontend panel is purely generative).
