# Config Export/Import — Design Spec

**Date:** 2026-09-17
**Status:** Approved

## Problem

There is no way to move configs, file servers, jobs, job selections, or execution
sequences between environments (e.g. dev → prod, or between two installs) without
manually recreating each record through the UI. The only existing mechanism —
`POST /api/configs/import-yaml` (`api/routes/configs.py`) — covers configs only, has
no export counterpart, and does not touch file servers, jobs, job selections, or
sequences at all.

"Adapters" (Automic, SAP BO, SAP DS, REST API, AWS) are not a standalone entity —
their settings live embedded inside `SavedConfig.config_json`
(`etl_framework/config/models.py`), so exporting configs already covers them. No
separate adapter export is needed.

## Solution

A single bundle-based export/import feature covering five entity types: **file
servers, configs, jobs, job selections, execution sequences** (in that dependency
order). One backend module assembles/applies a versioned JSON bundle; one new UI page
lets the user pick what to export and upload a bundle to import, with a per-item
result summary.

Secrets are never included in the bundle. Imported configs/file servers are created
with secret fields blank; the user re-enters credentials after import. On import,
existing records are matched by name — a match is **skipped**, not overwritten, and
reported as such. Nothing is overwritten silently.

---

## 1. Data Model / Dependency Order

No new DB tables. The bundle is a transient JSON document, not a persisted entity.

Cross-references found in the existing model (`etl_framework/repository/models.py`,
`api/schemas.py`) fix the required processing order:

| Stage | Entity | References |
|---|---|---|
| 1 | File servers (`FileServerProfile`) | none — referenced by *name* from job `params` |
| 2 | Configs (`SavedConfig`) | none |
| 3 | Jobs (`SavedJob`) | references file server by name (loose, in `params`) |
| 4 | Execution sequences (`ExecutionSequence`/`Version`) | `steps_json` references jobs by `job_name`; `defaults_json.config_id` references a config by numeric id |
| 5 | Job selections (`JobSelection`/`Version`) | `config_id` (numeric), `sequence_ref` (`{sequence_id, sequence_version}`) |

Because sequences and selections hold **numeric** config/sequence IDs, import must
remap: when stage 2 creates a new config, record `old_id → new_id`; when applying
stage 4/5, rewrite any `config_id`/`sequence_id` found in the bundle to the new local
id if the parent was imported in this run, or resolve to the existing local record's
id if it was skipped as a duplicate. Name-based references (`job_name`, file server
name) need no remapping — they resolve naturally as long as stage order is respected.

## 2. Bundle Format

Single JSON file, downloaded from export and uploaded for import:

```json
{
  "bundle_version": 1,
  "exported_at": "2026-09-17T12:00:00Z",
  "file_servers": [ { "name": "...", ... } ],
  "configs": [ { "env_name": "...", "config_json": { ... secrets stripped ... } } ],
  "jobs": [ { "name": "...", "params": { ... } } ],
  "sequences": [ { "name": "...", "versions": [ { "steps_json": [...], "defaults_json": {...} } ] } ],
  "selections": [ { "name": "...", "versions": [ { ... } ] } ]
}
```

Each entity's secret fields are stripped using the same field list already
maintained for masking (`SECRET_FIELDS` in `etl_framework/config/models.py`,
mirrored logic in `_mask`/`_preserve_masked_secrets`, `api/routes/configs.py`
L28-60, and `FileServerProfileRepository._encrypt_fields`). An unrecognized or
missing `bundle_version` is rejected before any DB write.

## 3. Backend

New module `api/services/config_bundle.py`:

- `build_bundle(entity_ids: dict[str, list[int]]) -> dict` — loads the selected
  records via existing repository list/get methods, serializes, strips secrets.
- `apply_bundle(bundle: dict) -> BundleImportResult` — validates `bundle_version`
  up front (reject whole bundle on mismatch, no partial writes), then applies stage
  1→5 in a single DB transaction per stage batch. Per-item outcome
  (`created` / `skipped` / `error` + reason) is collected into a result object;
  a single item error does not abort the rest of the import.

New router `api/routes/bundle.py`:

- `GET /api/bundle/list` — returns `{name, id, type}` for every file server, config,
  job, selection, and sequence, for populating the export checklist. Reuses existing
  repository `list()` calls; no new queries.
- `POST /api/bundle/export` — body `{entity_ids: {...}}`, returns the bundle JSON
  (frontend triggers file download from the response).
- `POST /api/bundle/import` — body is the uploaded bundle JSON, returns
  `BundleImportResult` (list of `{type, name, status, reason?}`).

Audit log entries follow the existing `config.imported` pattern
(`api/routes/configs.py`) — one `bundle.imported` entry per import call, with the
result summary as detail.

## 4. Frontend

New page `frontend/features/import-export.js`, route `/import-export`, linked from
the main nav alongside other admin pages.

- **Export panel**: checklist grouped by entity type (file servers / configs / jobs /
  job selections / sequences), populated from `GET /api/bundle/list`. "Export
  Selected" posts to `/api/bundle/export` and downloads the response as
  `bundle-<timestamp>.json`.
- **Import panel**: file picker → reads JSON client-side for a quick shape check →
  `POST /api/bundle/import` → renders a result table (type, name, status, reason)
  grouped by status so skipped/error items are easy to scan.

## 5. Error Handling

- Malformed JSON or unrecognized `bundle_version` → import rejected outright, no DB
  writes, clear error message returned to the UI.
- A sequence/selection referencing a job or config not present in the bundle **and**
  not found locally by name → that one item marked `error` with reason; the rest of
  the import proceeds.
- Duplicate name (already exists locally) → `skipped`, not an error.

## 6. Testing

- Unit tests for `config_bundle.py`: stage ordering, ID remap on create, skip-on-
  duplicate, secret stripping, bundle_version rejection.
- API tests for the three new routes (`list`, `export`, `import`), including a
  round-trip test (export selection → import into a clean DB → verify records match
  minus secrets).
- One Playwright e2e covering the UI round trip: select items, export, re-import into
  the same environment, verify the result table shows all items as `skipped`
  (matches repo convention of e2e coverage for admin config flows, e.g. recent
  live-connections work).

## Out of Scope

- Scheduled runs (`ScheduledRun`) — environment-specific, not included.
- Overwrite-on-import / per-conflict resolution UI — always skip-on-duplicate for
  now; can be added later if needed.
- Encrypted secret transport (passphrase-protected export) — redaction only for
  this iteration.
