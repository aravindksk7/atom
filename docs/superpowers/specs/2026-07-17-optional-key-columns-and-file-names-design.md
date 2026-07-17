# Optional key columns for file compares + real file names in reports

Date: 2026-07-17
Status: Approved

## Problem

1. **Job compare requires an ID column even for file/XML comparisons.** `JobDefinition.validate_reconciliation_contract` unconditionally rejects a `reconciliation` job with no `key_columns`, even when the job is file-backed (`source_mode == "files"` or file params present). The ad-hoc Compare tab (`POST /api/compare/recon-file`) has no such restriction — it infers a common ID column, or falls back to row-position matching when none exists. Job definitions can't reach that fallback because schema validation blocks them before they're ever saved.

2. **The HTML report never shows the actual files that were compared.** The report's "Environments: X → Y" line renders `run.source_env` / `run.target_env`, which for file compares is just the free-text `label_a`/`label_b` (defaults: "Source A" / "Production Report"). The real uploaded file name (`file_a_name`/`file_b_name`) is captured by the frontend and sent to the backend but never surfaces anywhere in the report.

## Goals

- File-backed `reconciliation` jobs can be created and run without `key_columns`, using the same infer-then-positional-fallback behavior the Compare tab already has.
- Non-file (query-based) reconciliation jobs and `api_reconciliation` jobs keep requiring `key_columns` — positional fallback doesn't make sense for arbitrary SQL result sets or live API pulls.
- The HTML report shows the real file name(s) compared, in addition to (not replacing) the existing environment/source labels.
- No behavior change for existing key-columns-supplied jobs or comparisons.

## Non-goals

- Changing key-column requirements for SQL-to-SQL or live API reconciliation.
- Changing the meaning or display of `label_a`/`label_b` / `source_env`/`target_env`.
- Reworking the upload/storage pipeline for compare files.

## Design

### A. Shared key-resolution logic

Today `CompareService` has three static helpers used only by the ad-hoc Compare tab (`api/services/compare_service.py`):

- `_infer_key_columns(df_a, df_b)` — finds a common ID-like column (by name candidates, or the single shared column), raises `HTTPException` if none found.
- `_validate_key_columns(df_a, df_b, key_columns)` — checks the chosen keys exist on both sides.
- `_sort_for_positional_compare(df_a, df_b, exclude_columns)` — sorts both frames by their common columns so a positional (`__row__`) compare is stable.

These move to `etl_framework/reconciliation/compare_utils.py` (already the shared framework-level home for reconciliation utility functions — see `normalize_string_columns`, `value_columns`, etc.) as plain functions, framework-exception based instead of `HTTPException`-based (the API layer catches and translates as needed). A new function `resolve_key_columns(df_a, df_b, key_columns, exclude_columns)` wraps the infer → positional-fallback → `__row__`-injection sequence currently inlined in `CompareService._run_tabular_file_compare` (compare_service.py:335-350), returning `(df_a, df_b, resolved_key_columns)`.

`CompareService._run_tabular_file_compare` calls `resolve_key_columns` instead of its own inline logic. Behavior for the ad-hoc Compare tab is unchanged.

### B. Schema: file-backed reconciliation jobs don't require key_columns

`api/schemas.py`, `JobDefinition.validate_reconciliation_contract`, `reconciliation` branch:

```python
elif self.job_type == "reconciliation":
    uses_files = (...)
    if uses_files:
        _validate_job_file_source(self.params, "source")
        _validate_job_file_source(self.params, "target")
        if not _has_job_file_source(...) or not _has_job_file_source(...):
            raise ValueError("file-backed reconciliation jobs require source and target files")
        # key_columns requirement REMOVED for this branch
    elif not self.query.strip():
        raise ValueError("reconciliation jobs require a query")
    else:
        if not self.key_columns:
            raise ValueError("reconciliation jobs require key_columns")
```

The `if not self.key_columns: raise` moves inside the `else` (query-based) branch only. `api_reconciliation`'s existing key_columns requirement is untouched.

### C. Execution: resolve keys for file-backed job runs

`api/services/run_executor.py`:

- `_build_file_engines(job)` — after loading `source_df`/`target_df`, if `job.key_columns` is empty, call `resolve_key_columns(source_df, target_df, [], job.exclude_columns or [])` and use the returned (possibly `__row__`-augmented) frames and resolved key list. Return `(FrameEngine(source_df, ...), FrameEngine(target_df, ...), resolved_key_columns)` — a third tuple element.
- `_build_engines(job)` — its other callers (non-file paths) don't need the third element; only the file-reconciliation path consumes it.
- `_run_reconciliation_job(...)` gains a `key_columns: list[str] | None = None` param; when given, it's used verbatim instead of recomputing `job.key_columns or self._settings.key_columns`.
- `_build_case_file_reconciliation` passes the resolved key columns from `_build_file_engines` into `_run_reconciliation_job`.

Net effect: a file-backed reconciliation job with empty `key_columns` infers an ID column from the data, or falls back to positional (`__row__`) row matching — identical behavior to the Compare tab.

### D. Report: show real file names

**`api/services/run_report.py`**:

- `RunReportSnapshot` gains `file_name_a: str | None` and `file_name_b: str | None`.
- `build_run_report_snapshot` populates them from `run.config_snapshot["request"]` when `run.run_type` is a file-based compare type (`recon_file`, `sql_comparison`, `bo_comparison`): prefer `file_a_name`/`file_b_name`, else the basename of `file_a_path`/`file_b_path` (skip synthetic paths like `"__sql__"`), else `None`.
- `ReportResult` gains the same two optional fields, populated per-result for job-sequence runs: look up the `SavedJob` matching `result.query_name` (same pattern as `drilldown_result` in `api/routes/runs.py`) and read its file params (`source_file_name`/`target_file_name` or path basename) when the job is file-backed. `None` for non-file jobs — no visual change for those rows.

**`etl_framework/reporting/templates/report.html.j2`**:

- Under the existing `<p><strong>Environments:</strong> ...</p>` line, add a conditional line: `{% if suite.file_name_a or suite.file_name_b %}<p><strong>Files:</strong> {{ suite.file_name_a or '—' }} vs {{ suite.file_name_b or '—' }}</p>{% endif %}`.
- In the results table, under each job name, add a small subtext line showing `result.file_name_a` / `result.file_name_b` when either is set.

This is purely additive: existing `source_env`/`target_env` labels keep rendering exactly as they do today.

## Testing

- Unit: `resolve_key_columns` in `compare_utils.py` — infer success, positional fallback, key validation failure.
- Unit: `JobDefinition` validation — file-backed reconciliation job with no `key_columns` now valid; query-based reconciliation job with no `key_columns` still rejected; `api_reconciliation` unaffected.
- Unit: `run_executor` file-reconciliation path — job with no `key_columns` runs to completion (inferred key and positional fallback cases).
- Unit: `build_run_report_snapshot` — `file_name_a`/`file_name_b` populated for `recon_file`/`sql_comparison`/`bo_comparison` run types from upload, from path, and absent for stored-run/live comparisons.
- E2E (Playwright): extend an existing compare spec (e.g. `08b-compare-reconciliation.spec.ts`) to create a file-backed job without key columns and confirm it runs and passes; extend `06-reports.spec.ts` or a compare spec to assert the rendered report contains the uploaded file names.
