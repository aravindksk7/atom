# Compare Multi-File File Types and Dynamic Names Design

## Problem

The Compare tab's Multi-File sub-tab is narrower than the existing Reconciliation > Run/File vs Report workflow. It presents both sides as "local only", gives CSV-centric examples, and hardcodes local file mapping in `frontend/features/compare.js`. The backend already reads several tabular file types, but the UI does not make that clear. Filename matching also supports only generic tokens, date-style tokens, and glob characters; users need stronger matching for source and target filenames that differ by prefix, suffix, number formatting, region omission, and alpha-numeric business identifiers.

## Goals

- Let ad-hoc multi-file compare support the same practical file formats as Run/File vs Report: CSV, Excel, JSON, XML, TSV, and delimited text where the existing reader can parse it.
- Keep multi-file compare as a background `TestRun` that persists one aggregate `TestResult`, so Reports and run-status APIs keep working unchanged.
- Expand source/target filename patterns to support dynamic names with different shapes on each side.
- Add explicit token specs for numeric, alpha, alpha-numeric, wildcard, date, and custom regex matching.
- Preserve existing patterns such as `sales_{region}.csv`, `{date:%Y%m%d}`, `*`, and `?`.
- Avoid adding ad-hoc remote S3/SFTP run support in this change because credential sourcing for unsaved runs is still unresolved.

## Non-Goals

- Do not add S3/SFTP ad-hoc run selectors to the Compare tab.
- Do not change the saved-job multi-file reconciliation contract except through shared filename-pattern improvements.
- Do not add a new result schema or database migration.
- Do not redesign the full Compare tab.

## Root Cause Summary

- `frontend/features/compare.js` builds multi-file mappings as `{ kind: 'local', root, pattern }` for both source and target.
- `frontend/partials/tab-compare.html` labels the multi-file source and target panels as local-only and uses CSV-focused placeholders.
- `etl_framework/reconciliation/file_mapping.py` uses `_spec_to_regex()` to interpret token specs, but only supports no-spec tokens and limited strftime width tokens.
- `api/services/file_source.py` already supports `.csv`, `.xlsx`, `.xls`, `.json`, `.xml`, and `.tsv` via `read_tabular()`, so the backend format gap is smaller than the UI suggests.

## Architecture

The change keeps the current ad-hoc execution model: `POST /api/compare/multi-file` creates a `TestRun`, queues background work, and `CompareService.run_multi_file_compare()` persists one aggregate result. The implementation expands the shared filename-pattern compiler and improves the Compare tab UI copy/examples around local file sets and supported formats. Because saved jobs and ad-hoc compare both call the same `FileMappingSpec` and pairing functions, dynamic matching improvements apply to both flows.

## Filename Pattern Contract

Existing syntax remains valid:

- `{region}` captures any non-separator token that excludes `_`, `.`, `/`, and `\`.
- `{date:%Y%m%d}` captures a fixed-width date-like token such as `20260728`.
- `*` and `?` work as glob wildcards outside token braces.

New token specs:

- `{batch:num}` captures one or more digits with `\d+`.
- `{code:alpha}` captures one or more ASCII letters with `[A-Za-z]+`.
- `{id:alnum}` captures one or more ASCII letters or digits with `[A-Za-z0-9]+`.
- `{anything:any}` captures one or more characters except path separators with `[^/\\]+`.
- `{suffix:regex([A-Z]{2}\d{4})}` captures with the provided custom regex.

Custom regex is constrained to a single named token capture. The compiler wraps the supplied pattern in the token's named capture group, rejects empty regex, rejects nested named groups, and rejects patterns that fail to compile.

Examples:

- Source `sales_{region:alpha}_{batch:num}.xlsx` can match `sales_WEST_001.xlsx`.
- Target `financials-{region:alpha}-B{batch:num}.xlsx` can match `financials-WEST-B001.xlsx`.
- Source `extract_{id:alnum}.json` can match `extract_AB12.json`.
- Target `prod_{id:regex([A-Z]{2}\d{2})}.json` can match `prod_AB12.json`.
- If source names have no region but target names do, users can omit region from `match_on` and match on another shared token such as `{batch:num}` or use automated matching.

## UI Behavior

The Multi-File sub-tab remains a local file-set form. It should no longer imply CSV-only support. The source and target sections should say "Server file set" and explain that the root is a server-accessible directory. Placeholders should show different file types and naming schemes.

Add inline pattern help near the source/target pattern inputs:

- Supported formats: `.csv`, `.xlsx`, `.xls`, `.json`, `.xml`, `.tsv`, `.txt`.
- Pattern examples: `{region}`, `{batch:num}`, `{code:alnum}`, `{date:%Y%m%d}`, `{id:regex([A-Z]{2}\d{4})}`, `*`, `?`.
- Source and target patterns may differ as long as the configured `match_on` tokens are present on both sides.

The Compare tab does not add upload-directory support in this change. Browser uploads provide individual files, while the current multi-file backend discovers files from a server directory. Adding true multi-file uploads would require temporary upload set storage, cleanup, and a new request contract. That should be a separate design if needed.

## Data Flow

1. User enters local source root/pattern and target root/pattern.
2. UI builds the existing `file_mapping` object with `kind: "local"` for both sides.
3. Preview calls `POST /api/jobs/preview-file-mapping` with the mapping.
4. Run calls `POST /api/compare/multi-file` with labels, key columns, exclude columns, and the mapping.
5. Backend compiles patterns, discovers local files, pairs by explicit tokens or automated similarity, reads each pair through `read_tabular()`, reconciles rows, and persists one aggregate result.
6. UI polls run status, fetches run detail, and renders per-pair summaries.

## Error Handling

- Invalid token specs return clear validation errors from the pattern compiler.
- Custom regex compile errors include the token name and regex cause.
- Explicit matching fails when a `match_on` token is missing from a side's pattern.
- Unsupported file extensions continue to return the existing `read_tabular()` unsupported-format error.
- Pattern help tells users to use automated matching or a shared token if source/target names do not expose the same region or business segment.

## Testing

- Unit tests for `compile_token_pattern()` cover `num`, `alpha`, `alnum`, `any`, custom regex, old date specs, glob wildcards, invalid custom regex, and invalid empty regex.
- Unit tests for `CompareService.run_multi_file_compare()` cover non-CSV file pairs with different source and target filenames.
- E2E tests cover preview and run from the Compare tab using non-CSV patterns and different source/target naming.
- Existing multi-file saved-job tests must keep passing to prove backward compatibility.

## Spec Self-Review

- Placeholder scan: no placeholders or deferred implementation details are present.
- Consistency check: the design keeps local-only ad-hoc runs while expanding supported local formats and pattern syntax.
- Scope check: S3/SFTP ad-hoc run support and browser multi-file upload sets are explicitly out of scope.
- Ambiguity check: token syntax, examples, errors, and test expectations are specified.
