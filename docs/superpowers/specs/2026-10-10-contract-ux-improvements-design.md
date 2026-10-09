# Contract UX Improvements — Design

Date: 2026-10-10
Status: Approved

## Goal

Make the Data Contracts page easier to use correctly:

1. Source job picked from the list of defined jobs (free text still allowed).
2. Consumers entered as chips with suggestions.
3. Breaches can be resolved manually with a required note, unblocking the gate.
4. Contracts can be exported to and imported from YAML.

Out of scope: pause/active toggle UI, clone, jobs-without-contract coverage view, acknowledge-before-resolve workflow.

## 1. Source job dropdown + free text

Current: plain text input (`frontend/index.html`, `contract-modal-source-job-input`), no validation anywhere.

Change:
- Replace with `<input list="contract-source-job-options">` plus `<datalist id="contract-source-job-options">` rendered from `this.jobs` (`<option :value="j.name" :label="j.job_type">`).
- `openContractModal` (create and edit) calls `await this.loadJobs()` when `this.jobs` is empty.
- Computed helper `contractSourceJobKnown(name)` returns true when `name` matches a `jobs[].name`.
- When the modal's `source_job` is non-empty and not known, show an amber inline note under the field: "Job not defined yet — contract will be saved anyway." (`data-testid="contract-modal-source-job-warning"`). Save is not blocked.
- Contract list row shows a small amber "job missing" badge when `jobs` is loaded and `source_job` is not known (`data-testid="contract-job-missing-badge"`).
- Field stays disabled while editing (source_job is immutable, unchanged behavior).
- No backend change.

## 2. Consumers as chips

Current: `consumers_raw` comma-separated text, split on save.

Change:
- `contractModal.consumers` becomes an array; `consumers_raw` removed. A transient `contractConsumerDraft` string holds the input.
- Enter or `,` in the input adds the trimmed draft if non-empty and not already present; Backspace on an empty draft removes the last chip; each chip has a × button.
- Blur with a non-empty draft adds it (so typing then clicking Save does not lose input).
- Suggestions: `<datalist>` of the distinct consumers across `this.contracts`, minus ones already chosen.
- Save payload sends `consumers` array directly. API unchanged.
- Test ids: `contract-modal-consumers-input` (kept on the text input), `contract-consumer-chip`, `contract-consumer-chip-remove`.
- `contract-examples.js` apply path updated to set the array.

## 3. Breach resolve with note

### Data
Add nullable columns to `contract_breaches` via `ensure_column` in `etl_framework/repository/database.py`:
- `resolved_by VARCHAR(255)`
- `resolution_note TEXT`

Add both to the `ContractBreach` model and to `BreachOut`.

### Repository
`ContractRepository.resolve_breach(contract_name, breach_id, note, resolved_by) -> ContractBreach`
- Raises not-found when the contract or the breach (scoped to that contract) does not exist.
- Raises already-resolved when `resolved_at` is set.
- Sets `resolved_at = now`, `duration_hours = (resolved_at - opened_at) hours`, `resolution_note`, `resolved_by`. `resolution_run_id` stays null (manual).

`ContractRepository.resolve_open_breaches(contract_name, note, resolved_by) -> int` — same for every open breach of the contract; returns count (0 is fine).

### API (`api/routes/contracts.py`)
- `POST /api/contracts/{name}/breaches/{breach_id}/resolve` body `BreachResolveIn {note: str, resolved_by: str | None}` → `BreachOut`.
  - 422 when `note.strip()` is empty (pydantic validator).
  - 404 unknown contract/breach; 409 already resolved.
- `POST /api/contracts/{name}/breaches/resolve-all` same body → `{"resolved": int}`. 404 unknown contract.

The gate (`gate_service.py`) already filters `resolved_at IS NULL`, so no gate change.

### UI
- Breach history table: "Resolve" button on rows with no `resolved_at` (`data-testid="contract-breach-resolve-btn"`); header button "Resolve all open" shown when any open (`contract-breach-resolve-all-btn`).
- Both open a small modal: required note textarea, optional "Resolved by" input prefilled from last value (localStorage, try/catch), Confirm disabled until note non-blank (`contract-resolve-modal`, `contract-resolve-note-input`, `contract-resolve-confirm-btn`).
- On success: reload breach history and status map; toast.
- Resolved rows show "resolved by X — note" (truncated, full text in title).

## 4. YAML export / import

### Format
```yaml
contracts:
  - name: orders_contract
    version: "1.2"
    source_job: orders_etl
    owner: team@co.com
    sla_hours: 4
    consumers: [finance, ops]
    breach_severity: error
    active: true
```

### Export
`GET /api/contracts/export` with optional repeated `name` query params. No names → all contracts. Response `application/x-yaml`, `Content-Disposition: attachment; filename="contracts.yaml"`. 404 if any requested name is unknown.

### Import
`POST /api/contracts/import` body `{yaml: str, dry_run: bool = true}`.
- Parse with `yaml.safe_load`. 422 on parse error, missing top-level `contracts` list, or any item failing `ContractIn`-style validation (error message names the item index/name).
- Duplicate names inside the file → 422.
- Per item, plan action:
  - `create` — name not in DB.
  - `error` — name exists and `source_job` differs (reason: "source_job is immutable").
  - `unchanged` — name exists, all of owner/sla_hours/consumers/breach_severity/active equal.
  - `update` — otherwise. If file `version` differs from DB, add `warning: "version differs; use bump to change version"`; version is not changed.
- Response: `{"dry_run": bool, "applied": bool, "rows": [{name, action, reason?, warning?}]}`.
- When `dry_run=false`: if any row is `error`, nothing is applied (`applied: false`, HTTP 200 with rows). Otherwise all creates/updates run in one DB transaction and `applied: true`. Created contracts use the file's `version` (default "1.0").

### UI
- Per-contract "Export YAML" button in detail header (`contract-export-btn`); list header "Export all" (`contracts-export-all-btn`). Download via fetch + blob (auth header needed).
- List header "Import" button (`contracts-import-btn`) opens modal: textarea + file picker (reads file into textarea), "Preview" runs dry run, table of rows with action badges, "Apply" enabled only when preview has no `error` rows and at least one create/update (`contract-import-modal`, `contract-import-textarea`, `contract-import-preview-btn`, `contract-import-apply-btn`, `contract-import-row`).
- After apply: reload contracts, toast with counts.

## Error handling summary

| Case | Result |
|------|--------|
| Unknown source job in modal | Amber warning, save allowed |
| Blank resolve note | 422; button disabled in UI |
| Resolve already-resolved breach | 409, toast |
| Bad YAML / schema | 422 with message shown in import modal |
| Import row source_job mismatch | Row `error`; apply blocked |

## Testing

- Unit (`tests/unit/test_contracts.py`): `resolve_breach` sets fields and duration; already-resolved raises; wrong contract scope raises; `resolve_open_breaches` count; import planner create/update/unchanged/error/version-warning; duplicate names rejected.
- Route (`tests/integration/test_contracts_integration.py`): resolve 200/404/409/422; resolve-all; export all and by name (404 unknown); export→import round trip yields all `unchanged`; apply blocked when an error row exists; apply transactional.
- Migration: existing DB without new columns gets them via `ensure_column`.
- E2E (`tests/e2e/09-contracts.spec.ts`): datalist lists seeded jobs and warning appears for unknown name; chips add/remove and persist; resolve breach with note clears open status; export then import preview shows `unchanged`.
- Update any e2e/a11y specs that type into the consumers input expecting comma text (`21-accessibility.spec.ts`).
- Rebuild `frontend/index.html` from template/partials after edits.
