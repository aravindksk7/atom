# Compare Documentation and Help Refresh Design

**Date:** 2026-08-12
**Status:** Draft

## Goal

Update both the project README and the web UI Help Center so users can understand every Compare tab option, choose the right Compare subtab for each workflow, and know when and how to use advanced compare options. The refresh must explicitly document comparing SAP BO reports across all tabs in a document.

## Scope

In scope:
- Update `README.md` Compare Tab documentation.
- Update searchable web UI help content in `frontend/help-content.js`.
- Cover all Compare subtabs: BO Report, Reconciliation, SQL, Column Stats, Mismatch Diff, and Multi-File.
- Document BO live source `All tabs (whole document)` usage and API semantics.
- Explain advanced options with practical use cases, not only field definitions.
- Validate changed JavaScript syntax and confirm key terms are searchable.

Out of scope:
- Changing Compare tab runtime behavior.
- Redesigning the Help Center layout or renderer.
- Adding markdown rendering inside the Help Center.
- Adding new Compare API options.
- Creating new automated tests unless an existing targeted help test is already easy to extend.

## Existing Context

The Compare UI is implemented across `frontend/partials/tab-compare.html` and `frontend/features/compare.js`. The Help Center renders plain structured content from `frontend/help-content.js`, where each section has `id`, `title`, `intro`, and `steps[]`, and each step supports `title`, `text`, `where`, `tip`, and `warn`.

`README.md` already has a Compare Tab section, but the overview still describes three first-class modes even though the UI now exposes six subtabs. Advanced options are present, but they need more detailed usage guidance. The BO live report selector supports `All tabs (whole document)`, represented in the UI as `reportId === '*'` and sent to the API as an empty `report_id`; this should be documented because it changes the compare scope from one report/page to the full document export.

## README Design

Update the Compare Tab section in `README.md` as a content-only refresh:

- Replace the current overview with a subtab-oriented overview that covers BO Report, Reconciliation, SQL, Column Stats, Mismatch Diff, and Multi-File.
- Add a compact use-case table mapping each subtab to the situation it solves and the primary output users should expect.
- In BO Report Compare, document each source type shown in the UI: `live`, `path`, `upload`, `api`, and `run` if supported by the current payload path.
- Add explicit BO all-tabs guidance:
  - Use `All tabs (whole document)` when the business check is document-level parity across all report tabs/pages.
  - Use a specific report/page when users only care about one tab or want smaller/faster comparisons.
  - Warn that all-tabs exports can be larger, slower, and may include sheet/tab-specific columns that require key/exclude tuning.
  - Describe API semantics clearly: UI `All tabs (whole document)` maps to the whole-document export; callers should omit/empty `report_id` only when intentionally requesting that behavior.
- Keep the existing API examples, but add notes or examples that show advanced options and all-tabs semantics where they are relevant.

## Advanced Options Design

Expand `### Advanced Compare Options` into two parts: a field reference and a use-case guide.

The field reference should continue documenting:
- `comparison_backend`
- `float_tolerance`
- `column_tolerances`
- `datetime_tolerance_seconds`
- `case_insensitive_columns`
- `whitespace_normalize_columns`
- `mismatch_row_limit`
- `sample_frac`
- `parallel_columns`
- `parallel_workers`

The use-case guide should explain:
- Use `pandas` as the safest default.
- Use `polars` for large row counts when the optional dependency is installed.
- Use `duckdb` for very wide or SQL-friendly tabular workloads when installed.
- Use global float tolerance for general numeric noise.
- Use per-column tolerances for currency, percentages, weights, or metrics with different precision rules.
- Use datetime tolerance for timestamp rounding or timezone-normalized extract differences.
- Use case-insensitive and whitespace normalization for human-entered strings and vendor exports with inconsistent casing/spacing.
- Use sampling for fast smoke checks before running full comparisons.
- Use mismatch row limit to control stored detail volume while preserving aggregate counts.
- Use parallel column comparison for wide tables, not as a default for small/narrow compares.

## Web UI Help Design

Add or expand a dedicated Compare section in `frontend/help-content.js` using the existing Help Center data model. Keep steps concise and searchable because the UI renders plain text, not full markdown.

Suggested help steps:
- Choose the right Compare subtab.
- Compare two BO report sources.
- Compare all tabs in a BO document.
- Pick key and exclude columns.
- Tune Advanced Options.
- Use SQL Direct Compare.
- Use Column Stats for large tables.
- Use Mismatch Diff across prior runs.
- Use Multi-File for folder-to-folder checks.
- Save templates or save a compare as a job, while noting current template coverage limitations where relevant.

The Help Center text should prioritize end-user wording: where to click, what the option means, when to use it, and important cautions. It should avoid implementation-only terms unless they help API users search for the feature.

## Error Handling and Caveats

Because this is documentation-only, no runtime error handling changes are required. The docs should call out existing user-facing caveats:
- All-tabs BO compares can be slower and larger than single-report compares.
- Advanced backends require optional dependencies; if not installed, users should stay on `pandas`.
- Sampling is for smoke testing and should not be treated as a full reconciliation result.
- Normalization options can intentionally hide casing or whitespace differences, so users should only enable them for columns where those differences are non-material.
- Multi-File Compare remains local-source-only in the Compare tab and runs pairs sequentially.

## Validation

After implementation:
- Run a JavaScript syntax check for `frontend/help-content.js`, such as `node --check frontend/help-content.js`, if Node is available.
- Search `README.md` and `frontend/help-content.js` for required terms: `All tabs`, `Advanced Options`, `Column Stats`, `Mismatch Diff`, `Multi-File`, `sample_frac`, and `parallel_columns`.
- Optionally run any existing Help Center E2E test if the local Playwright setup is ready and the change extends that coverage.

## Acceptance Criteria

- `README.md` documents every Compare subtab exposed in the UI.
- `README.md` explains every advanced compare option and gives a clear use case for each.
- `README.md` documents BO all-tabs compare behavior and when to use it.
- `frontend/help-content.js` includes searchable end-user guidance for Compare workflows and advanced options.
- The Help Center content remains valid JavaScript.
- No runtime Compare behavior changes are introduced.
