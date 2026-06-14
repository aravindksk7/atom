# Fix Tracker

Identified via ruflo / code-review-graph analysis on 2026-06-14.

| # | Area | Description | Status |
|---|------|-------------|--------|
| 1 | Code quality | Delete duplicate `artifact_service.py` at repo root (identical to `api/services/artifact_service.py`) | ✅ done |
| 2 | Backend + UI | Add `DELETE /api/runs/{run_id}` endpoint and History tab delete button | ✅ done |
| 3 | UI | Add YAML config import textarea/button in Config tab | ✅ done |
| 4 | UI | Add direct job creation form in Launch tab (bypasses BO/Automic adapter requirement) | ✅ done |
| 5 | Backend + UI | Add History tab filters: status and run_type dropdowns | ✅ done |
| 6 | UI | Compare diff drill-in: clicking improved/regressed row opens mismatch drawer | ✅ done |
| 7 | UI | Inline mismatch Load More in History detail (currently capped at 50 rows, no paging) | ✅ done |
| 8 | Backend + UI | Add `GET /api/runs/{run_id}/export?format=csv` endpoint and download button in History | ✅ done |

---

## Detail

### Fix 1 — Duplicate artifact_service.py
- **File to delete:** `c:\atom\artifact_service.py`  
- **Canonical copy:** `c:\atom\api\services\artifact_service.py`  
- Root copy was never imported by any module; it's dead code from an earlier scaffolding pass.

### Fix 2 — Run delete
- **Backend:** `DELETE /api/runs/{run_id}` → `repo.delete_run(run_id)` (new repo method)  
- **Frontend:** trash icon per row in History table; soft-confirm before calling DELETE

### Fix 3 — YAML config import UI
- **Backend:** `POST /api/configs/import-yaml` already exists  
- **Frontend:** collapsible "Import YAML" card in Config tab with a `<textarea>` and Import button

### Fix 4 — Direct job creation form
- **Backend:** `POST /api/jobs` and `PUT /api/jobs/{name}` already exist  
- **Frontend:** "New Job" button in Launch tab Job Catalog card → modal with name, query, key_columns, job_type, tags

### Fix 5 — History filters
- **Backend:** add `status` and `run_type` query params to `GET /api/runs`  
- **Frontend:** two `<select>` dropdowns above the History table; clear button resets to all

### Fix 6 — Compare diff drill-in
- **Frontend only:** in the compare-runs result table, clicking a row's query name (or a new Detail button) opens `openMismatchDrawer` for the run that owns that result

### Fix 7 — Inline mismatch Load More
- **Frontend only:** add a "Load More" button below the inline expanded mismatch rows, mirroring the drawer's `loadMoreMismatches()` pattern; tracked per result_id via `expandedMismatchOffset[result_id]`

### Fix 8 — Run export CSV
- **Backend:** `GET /api/runs/{run_id}/export` streams a CSV of results + per-row mismatch summary  
- **Frontend:** download button in History run detail (uses `apiBlob()` + `triggerDownload()` which already exist)
