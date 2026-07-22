# Multi-File Reconciliation (1:M / M:N) — Architecture & Design Document

**Author role:** Principal Automation Architect review of `atom`'s ETL testing framework
**Status:** Design accepted for phased implementation. See companion phase plans in this directory (naming convention `2026-07-22-multi-file-reconciliation-phase<N>-*.md`); only Phase 1 is written in full task-by-task detail so far.
**Audience:** QA engineers configuring jobs, and engineers implementing the phases.

---

## 1. Problem statement

Today a reconciliation job in `atom` is hard-wired to exactly one source file and exactly one target file. This is enforced in three independent places that must stay in sync:

- `api/schemas.py`, `JobDefinition.validate_reconciliation_contract` (lines 439-496) — the Pydantic contract checked at job-save time.
- `etl_framework/runner/job_validation.py`, `validate_job_definition()` — the dry-run check behind `POST /api/jobs/{name}/validate`.
- `api/services/run_executor.py`, `RunExecutor._job_file_value` / `_has_file_source` / `_load_job_file_frame` / `_build_file_engines` (lines 1241-1296) — the actual execution-time file resolution.

All three read `params["source_file_path"]` / `params["target_file_path"]` (or the legacy `file_a_*`/`file_b_*` aliases) as single scalar strings. There is no discovery step — the QA engineer must know the exact file name in advance — and no concept of "this job produced 6 files, compare each to its counterpart."

We need a job to be able to say: "discover every file matching this pattern on the source side, discover every file matching this pattern on the target side, pair them up by shared key tokens (region, date, shard, ...), and give me one reconciliation result per pair plus a roll-up."

## 2. Architecture overview

```
                    ┌─────────────────────────────────────────────────────────────┐
                    │                        Job Definition                        │
                    │   params.file_mapping: { strategy, source, target, ... }     │
                    └───────────────────────────────┬───────────────────────────────┘
                                                      │
                                                      ▼
 ┌───────────────┐   parsed FileMappingSpec  ┌────────────────────┐
 │  Config Loader │─────────────────────────▶│  File Discoverer    │
 │ (JobDefinition │                          │  (per FileSource:    │
 │  .validate_*   │                          │   local / s3 / sftp  │
 │  + file_mapping│                          │   / bo_live)         │
 │  parser)       │                          └──────────┬──────────┘
 └───────────────┘                                      │ list[DiscoveredFile]
                                                          │ (per side)
                                                          ▼
                                              ┌────────────────────────┐
                                              │      Smart Mapper       │
                                              │  explicit match_on keys │
                                              │  or automated structural│
                                              │  similarity fallback    │
                                              └────────────┬────────────┘
                                                            │ FileMappingResult
                                                            │ (pairs + unmatched)
                                                            ▼
                                              ┌────────────────────────┐
                                              │    Execution Engine     │
                                              │  for each FilePair:      │
                                              │   read_tabular per file, │
                                              │   concat FileGroup,      │
                                              │   ReconciliationEngine   │
                                              │     .reconcile() (1x)    │
                                              └────────────┬────────────┘
                                                            │ list[ReconciliationResult]
                                                            ▼
                                              ┌────────────────────────┐
                                              │        Reporter         │
                                              │  aggregate status +      │
                                              │  per-pair breakdown       │
                                              │  ("5 of 6 pairs matched") │
                                              └────────────────────────┘
```

**Design principle — extend, don't rewrite:** `ReconciliationEngine.reconcile()` (`etl_framework/reconciliation/engine.py:77`) already does exactly the right thing at exactly the right granularity: one source dataset in, one target dataset in, one `ReconciliationResult` out. Nothing about that class changes. Multi-file support is a new layer *above* it that calls it once per resolved pair and aggregates the list of results — mirroring how `RunReportSnapshot` already rolls many per-job `ReportResult`s up into one run-level status (`api/services/run_report.py`, `_snapshot_status`). We are replicating a pattern the codebase already trusts, one level down (job → file pairs), not inventing a new one.

## 3. Component responsibilities

### 3.1 Config Loader
Extends `JobDefinition` (`api/schemas.py`) with a new `source_mode: "multi_file"` value, validated by a new branch in `validate_reconciliation_contract` and parsed by a new `FileMappingSpec.from_params(job.params)` in the shared module described in §3.5. Both the REST API and the CLI use this same schema — see §6.

### 3.2 File Discoverer
Given a `FileSource` spec (`kind: local | s3 | sftp | bo_live`, a `root`, and a `pattern`), returns `list[DiscoveredFile]`. Pattern tokens (`{region}`, `{date:%Y%m%d}`) are compiled to a named-group regex once and applied to every candidate file name; unmatched candidates are dropped, matched ones carry their extracted `tokens: dict[str, str]`.

- **Phase 1** ships the `local` discoverer only (glob + regex over `SERVER_FILE_ALLOWED_DIRS`, reusing the allow-listing model from `api/services/file_source.py:_resolve_allowed_path`).
- **Later phases** add `s3` and `sftp` discoverers behind the same `FileSource` protocol, plus a `bo_live` discoverer that iterates `BORestClient.list_reports()` (`etl_framework/sap_bo/client.py:243`) instead of walking a filesystem.

### 3.3 Smart Mapper
Groups each side's `DiscoveredFile` list by the tuple of token values named in `match_on`, producing a `FileGroup` per key (a group, not a single file, so that several shards sharing one key collapse into one logical dataset — this is what gives you true M:N rather than only 1:1 pairs). It then joins source groups to target groups on that key:

- Matched on both sides → a `FilePair`.
- Present on one side only → recorded in `unmatched_sources` / `unmatched_targets`, disposed of per `unmatched_policy` (`fail` | `warn` | `ignore`).

**Automated mapping** (Phase 2) is a fallback used when no `match_on` is configured: derive a structural signature per file (column names + dtypes from a cheap header-only read, or filename token overlap via `difflib.SequenceMatcher`), and pair files whose signatures exceed `similarity_threshold`. This mirrors how Great Expectations profiles a batch before validating it, rather than requiring the user to hand-write every rule.

### 3.4 Execution Engine
For each `FilePair`: load every file in `pair.source.files` and `pair.target.files` via the existing `read_tabular()` (`api/services/file_source.py:462`), `pd.concat` each side into one frame, wrap in `FrameEngine`, and call the **existing, unmodified** `RunExecutor._run_reconciliation_job()` (`api/services/run_executor.py:583`) exactly as `_build_case_file_reconciliation` does today for the single-pair case. Collect the list of results.

### 3.5 Shared file-mapping module
A new `etl_framework/reconciliation/file_mapping.py` is the single place that owns `DiscoveredFile`, `FileGroup`, `FilePair`, `FileMappingResult`, the token-pattern compiler, the local discoverer, and the grouping/pairing algorithm. All three current file-source call sites (`api/schemas.py`, `etl_framework/runner/job_validation.py`, `api/services/run_executor.py`) import from here instead of re-deriving file-path logic a fourth time. This directly addresses the triplication flagged during architecture review.

### 3.6 Reporter
Introduces an aggregate result shape (`MultiFileReconciliationResult`, §Phase 1 plan) carrying `pair_results: list[ReconciliationResult]`, `unmatched_sources`, `unmatched_targets`, and roll-up counters (`pairs_total`, `pairs_passed`, `pairs_failed`). Phase 1 persists this as today's single `TestResult` row (summed counts, worst-of status) with the structured breakdown embedded in the existing `mismatch_summary` JSON column — no DB migration, no breaking change to any current consumer. Later phases give the breakdown first-class surfacing in `TestResultOut`, the HTML report template, JUnit export, and the difference-export CSVs (see roadmap, §7).

## 4. Configuration schema (production-ready example)

Everything lives under `params.file_mapping` — no new top-level `JobDefinition` fields, no DB migration, consistent with how `bo_live` was added. Both the Web UI and the CLI submit this same shape: the CLI (`etl_framework/cli/`) is HTTP-only and never renders or parses job definitions locally (see §6), so there is exactly one schema to document, not two.

```yaml
# Example: reconcile every regional sales export for a given day.
# One physical BO document may spool several regional files; the target
# side is a matching set of baseline files already sitting on SFTP.
name: daily_sales_reconciliation
job_type: reconciliation
description: "Reconcile per-region daily sales extracts: live BO spool vs. SFTP baseline"
key_columns: [store_id, sku]     # per-pair row key, same meaning as today
exclude_columns: []
params:
  source_mode: multi_file

  file_mapping:
    # "explicit" requires match_on and named tokens in both patterns.
    # "automated" (Phase 2) infers pairs by structural similarity instead.
    strategy: explicit

    match_on: [region, date]      # token names used to build the pairing key

    source:
      kind: local                 # local | s3 | sftp | bo_live
      root: "/spool/bo_exports"
      pattern: "sales_data_{region}_{date:%Y%m%d}.csv"

    target:
      kind: sftp                  # Phase 3+: requires credentials_ref below
      root: "/exports/finance/sales"
      pattern: "financials_{region}_{date:%Y%m%d}.dat"
      credentials_ref: sftp_finance_prod   # resolved via EnvironmentConfig, same
                                            # pattern as existing bo_credentials /
                                            # source_credentials in config_snapshot

    unmatched_policy: fail        # fail | warn | ignore — what to do with a
                                   # region/date that only exists on one side

    automated_mapping:             # only consulted when strategy: automated
      enabled: false
      similarity_threshold: 0.82
      signals: [filename_tokens, column_signature, row_count_ratio]

  comparison_defaults:            # applied to every pair unless a pair-level
    float_tolerance: 0.0001       # override is added (future extension point,
    mismatch_row_limit: 200       # not needed for Phase 1)

rules: []
pass_condition:
  # Phase 1 roll-up rule: "all_pairs_pass" (default, strictest) or
  # "majority_pass" (>50% of pairs must pass). Custom per-pair thresholds
  # are a later-phase extension, not required for the MVP.
  mode: all_pairs_pass
```

A minimal **1:1** job (today's behavior) is expressible in the same schema with a pattern that has no tokens at all (`pattern: "orders.csv"`, no `match_on`) — the discoverer just finds the one file, the mapper produces exactly one pair, execution and reporting are identical to a single-pair job in shape. This is intentionally the same code path at every multiplicity: **1:1 is not a special case, it's the N=1 case of N:M.**

Bare glob wildcards (`*`, `?`) are also supported directly in a pattern, for jobs that need dynamic discovery but no pairing key at all — e.g. `pattern: "sales_data_*.csv"` with `match_on` omitted. In that case every matched file on a side collapses into a single group (an empty `match_on` always yields the same grouping key), which is the "many shards, one logical dataset per side" shape from the requirements, expressed with zero configuration beyond the pattern itself.

## 5. Industry best-practice patterns adopted

| Pattern | Borrowed from | How it's used here |
|---|---|---|
| Manifest / lineage artifact | dbt (`manifest.json`) | Each multi-file run's discovery + pairing decisions are written to `logs/file_mapping_manifest_{run_id}.json`: every discovered file, its extracted tokens, which pair it landed in (or why it was unmatched), and the mapping method (`explicit`/`automated`, plus similarity score if automated). This is the audit trail a QA engineer needs when a pair "silently" fails to match. |
| Batch/asset profiling before validation | Great Expectations (`Validator`, Data Docs) | The automated-mapping fallback (Phase 2) profiles each candidate file's column signature before pairing, instead of requiring hand-written glue for every file — same spirit as GE inferring expectations from a batch profile. |
| Readiness sensing before validating | Airflow (`FileSensor`, `ExternalTaskSensor`) | Live-spool sources (`bo_live`, or a `local` root that a DB job is actively writing into) support a `readiness.expected_count` + `readiness.poll_interval_seconds`/`timeout_seconds` block (Phase 3) so the execution engine waits for all expected files to land before comparing, rather than racing a partial spool. |
| Severity-graded failure handling | dbt (`severity: warn|error` on tests), GE (`result_format`) | `unmatched_policy: fail\|warn\|ignore` gives the same three-tier semantics for "a file showed up on only one side" that dbt gives for a failing test. |
| N-results-roll-up-to-one-status | Already in this codebase | `RunReportSnapshot._snapshot_status` (`api/services/run_report.py`) rolls per-job statuses into one run status. The new `MultiFileReconciliationResult` roll-up (job → pairs) is a direct copy of that existing, already-reviewed pattern — no new design risk introduced. |
| Backward-compatible schema evolution | dbt (new config defaults never break existing `dbt_project.yml`) | Existing jobs using scalar `source_file_path`/`target_file_path` keep working unmodified forever; `multi_file` is strictly additive. No forced migration, no deprecation of the 1:1 shape. |

## 6. Web UI and CLI

- **Web UI** (`frontend/partials/tab-launch.html` + `frontend/features/launch.js`): the job editor's "Input Source" selector gains a fourth option, `multi_file`, alongside today's `sql`/`files`/`bo_live` (`tab-launch.html:352`). Its fields block is a repeater (add/remove row) for `match_on` tokens and the two `FileSource` specs, modeled directly on the existing DQ-rule repeater (`newDQRule`/`addDQRule`/`removeDQRule`, `launch.js:172-198,344-350`) — the closest existing "add/remove row" pattern in this codebase. This is UI-only work; it assembles the exact same `params.file_mapping` JSON shown in §4. (Scheduled for the frontend phase, §7.)
- **CLI** (`etl_framework/cli/`): no schema work needed. The CLI is explicitly HTTP-only (`etl_framework/cli/app.py` docstring: "this module must never import `api.*` or `etl_framework.repository`") and only triggers runs by job name and polls/downloads results (`atom run`, `atom report`) — it never constructs or parses a job definition locally. Whatever `params.file_mapping` shape a job was saved with via the API/UI is exactly what the CLI launches. The only CLI-side change worth making is enriching `etl_framework/cli/render.py`'s text output with a per-pair summary line when a result carries pair breakdown data — a small, optional, later-phase nicety, not a blocker for the feature to work end-to-end.

## 7. Phased roadmap

Each phase produces working, independently testable software; later phases are not yet written in full task detail and get their own dated plan file when picked up.

1. **Phase 1 — Foundation** *(fully detailed in `2026-07-22-multi-file-reconciliation-phase1-foundation.md`)*: shared `file_mapping.py` module, local-filesystem explicit discovery + pairing (true M:N via `FileGroup`), `multi_file` source_mode schema + validation, `RunExecutor` execution loop over pairs, `MultiFileReconciliationResult` aggregate embedded in the existing `mismatch_summary` JSON (no DB migration). Ships a real end-to-end multi-file job runnable today, reported through existing surfaces.
2. **Phase 2 — Smart mapping**: automated/structural-similarity fallback matcher, `file_mapping_manifest_{run_id}.json` lineage artifact, property-based tests for pairing correctness (`tests/property/`).
3. **Phase 3 — Execution hardening**: per-pair parallelism (reuse `TestRunner`'s worker pool), per-pair exception isolation (one bad pair doesn't fail the whole job), live-spool readiness/polling for `bo_live` and actively-written local roots.
4. **Phase 4 — Reporting rollups**: first-class `file_pairs: list[FilePairSummaryOut]` on `TestResultOut` (`api/schemas.py:284`), HTML report template update, JUnit export and difference export iterating N pairs per job, mismatch UI pair drill-down.
5. **Phase 5 — Remote file sources**: S3 and SFTP discoverers behind the `FileSource` protocol, credential handling via `EnvironmentConfig`, allow-listing analogous to `_resolve_allowed_path`.
6. **Phase 6 — Frontend UI**: job editor repeater for `file_mapping` (per §6), automated-mapping preview endpoint, per-pair result view in the Compare tab, new Playwright e2e coverage following the `data-testid` convention used throughout `tab-launch.html`.

---

*Companion document: `2026-07-22-multi-file-reconciliation-phase1-foundation.md` (task-by-task TDD implementation plan for Phase 1).*
