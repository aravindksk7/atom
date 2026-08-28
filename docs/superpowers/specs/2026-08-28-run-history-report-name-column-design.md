# Run History Report Name Column & Consistent Download Filenames — Design Specification

**Date:** 2026-08-28
**Status:** Approved

## Overview
This specification adds a human-readable "Report Name" column to the Web UI run history page, and makes all HTML/CSV/JSON/Parquet report download filenames follow the same naming convention as that report name. Today the history table has no way to identify *which* job/config a run belongs to at a glance (only Run ID, Status, Environments, Started, P/F/S), and downloaded report filenames use two inconsistent conventions (`all_differences_<run_id>_<export_id>.<ext>` from the exports flow, `report_<run_id>.html` from the legacy direct-download routes) — neither includes any job-identifying information.

## 1. Report Name Computation (`api/services/run_label.py`)
- Add a new function `report_name_base(run) -> str` alongside the existing `run_display_label()`.
- Source string:
  - If `config_snapshot.config_name` is present and non-empty, use it.
  - Otherwise fall back to `"{source_env}_to_{target_env}"` (mirrors the existing fallback pattern already used by `run_display_label()`).
- Sanitize the source string: lowercase, replace any run of non-alphanumeric characters with a single underscore, strip leading/trailing underscores.
- Format: `f"{sanitized_source}_{run.started_at:%Y-%m-%d_%H-%M-%S}"`.
  - Uses `started_at` (not export/download time), so the same run always produces the same report name across repeated downloads and across the history column.
  - Example: `nightly_recon_2026-08-28_14-30-05`, or `dev_to_prod_2026-08-28_14-30-05` for a run with no config name.
- This is a computed value, not persisted — no database migration required, matching how `run_display_label()` already works.

## 2. API Schema (`api/schemas.py`)
- Add `report_name: str` field to `RunStatusOut`, populated the same way `label` is populated wherever `RunStatusOut` instances are built (`api/routes/runs.py`, `list_runs` and anywhere else runs are serialized).
- `RunDetailOut` inherits the field automatically since it extends `RunStatusOut`.

## 3. Download Filename Convention (`api/services/difference_export.py`)
- Change `export_filename(run_id, fmt, export_id=None)` to `export_filename(run, fmt, export_id=None)`, taking the run object (or `report_name` string) instead of just `run_id`.
- New convention: `f"{report_name_base(run)}_{run.run_id[:8]}.{suffix}"` — e.g. `nightly_recon_2026-08-28_14-30-05_00a638ef.html`.
  - The 8-char run-id suffix guards against filename collisions when two runs share the same config name and start second (matches the existing short-id format already shown in `run_display_label()`).
  - Applied uniformly to all export formats (html/csv/json/parquet), not just HTML, so one helper and one convention covers every download.
- Apply this same helper to all three existing HTML-serving download paths so they converge on one convention:
  - `POST /{run_id}/exports` → `GET /{run_id}/exports/{export_id}/download` (`api/routes/runs.py:848-872`) — already uses `export_filename`, just update the call site for the new signature.
  - `GET /{run_id}/mismatches/download` (`api/routes/runs.py:737-757`) — currently hardcodes `filename="report_{run_id}.html"`; switch to the shared helper.
  - `GET /{run_id}/report` (`api/routes/runs.py:594-598`) — currently has no explicit `Content-Disposition` header (browser defaults to the on-disk filename); add an explicit header using the shared helper.
- **On-disk file paths and the report generator (`etl_framework/reporting/generator.py`) are not changed.** Only the `Content-Disposition` filename presented to the browser at download time changes. This avoids touching artifact storage/lookup logic and keeps the change low-risk.

## 4. Frontend — Run History Table (`frontend/partials/tab-history.html`, mirrored in `frontend/index.html`)
- Add a new "Report Name" column between "Run ID" and "Status" in the history table header and row template, bound to `run.report_name` (already present in the `/api/runs` response per section 2 — no new frontend fetch logic needed).

## 5. Frontend — Download Handler (`frontend/features/compare.js`)
- No functional change to `downloadFullHtmlReport()` — it already reads the filename from the server's `Content-Disposition` header, which now reflects the new convention automatically.
- Update the hardcoded fallback string at `compare.js:1267` (used only if the header is ever missing) from `` `all_differences_${runId}_${exportId}.html` `` to match the new convention pattern for consistency, e.g. `` `report_${runId}.html` `` using only client-known values (the fallback cannot compute `report_name_base` client-side since it doesn't have `config_snapshot`/`started_at` loaded at that point) — this remains a rarely-hit fallback path, not the primary naming source.

## 6. Testing
- Unit tests for `report_name_base()`: config_name present, config_name missing (env-pair fallback), sanitization of special characters, timestamp formatting.
- Unit tests for `export_filename()`: new signature, all four formats, short run-id suffix present.
- API test asserting `RunStatusOut`/`RunDetailOut` responses include `report_name`.
- Integration/route tests for all three download routes asserting the `Content-Disposition` header uses the new convention.
- Existing Playwright history/download specs updated to assert the new column renders and downloaded filenames match the new pattern.
