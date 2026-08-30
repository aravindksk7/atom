# Data Contracts Testing in Web UI — Design Spec

**Date:** 2026-08-30
**Status:** Approved
**Context:** ETL Test Framework (`c:/atom`)

---

## 1. Overview & Problem Statement

Data Contracts establish a formal agreement between data producers and downstream consumers regarding data structure, semantics, quality, and service levels (SLAs). 

While the ETL framework previously supported descriptive contract metadata, version bumping, and passive post-run breach logging, it lacked **interactive and automated Data Contracts Testing** under the Contracts tab in the web UI. Users had no standard mechanism to:
1. Validate data contracts against industry standards (ODCS - Open Data Contract Standard / Bitol).
2. Execute on-demand verification of schema conformance, data quality rules, SLA/freshness, and consumer impact directly from the web UI.
3. Inspect granular test assertion breakdowns with actionable pass/fail/warn diagnostics.
4. Export ODCS-compliant test reports.

---

## 2. Core Pillars of Contract Testing (ODCS Standard)

The testing engine evaluates contracts across four industry-standard pillars:

| Pillar | Category | Description | Source of Truth / Evidence |
|---|---|---|---|
| **1. Schema Conformance & Drift** | `schema` | Validates column presence, expected vs actual data types, required/nullability flags, and unexpected or missing columns. | Latest `SchemaSnapshot` for `source_job` (Source/Target/Staged). |
| **2. Data Quality Invariants** | `quality` | Verifies data assertions: unique key constraints, not-null completeness, numeric boundaries/ranges, enum/allowed values, regex formats, and row count non-zero / minimum threshold. | Latest `TestResult` metrics, mismatch summaries, and DQ rule evaluations for `source_job`. |
| **3. SLA & Freshness** | `sla` | Validates elapsed time since last successful pipeline execution against `sla_hours`, checks for open and overdue/escalated breach states. | `ContractBreach` history and latest `TestRun` timestamps. |
| **4. Consumer Compatibility** | `consumers` | Verifies that declared downstream consumer fields are preserved and non-breaking contract semantics are met. | Declared `consumers` list and active schema definitions. |

---

## 3. Architecture & Components

```text
+-------------------------------------------------------------+
|                      Contracts Web UI                       |
|   (tab-contracts.html + frontend/features/contracts.js)     |
|                                                             |
|   +-----------------------------------------------------+   |
|   |  Contract Testing & Verification Panel              |   |
|   |  - "Run Contract Test" Trigger                      |   |
|   |  - Overall Status Banner (PASSED/FAILED/WARNING)    |   |
|   |  - Summary Metrics (Total, Passed, Failed, Rate)    |   |
|   |  - Category Filter Tabs (All, Schema, DQ, SLA, ...) |   |
|   |  - Detailed Checks Table with Diagnostics           |   |
|   |  - Export ODCS Test Report (JSON)                   |   |
|   +-----------------------------------------------------+   |
+------------------------------+------------------------------+
                               |
                               | POST /api/contracts/{name}/test
                               v
+-------------------------------------------------------------+
|                     FastAPI Contracts API                   |
|                   (api/routes/contracts.py)                 |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                   ContractTestingEngine                     |
|              (api/services/contract_tester.py)              |
|                                                             |
|   1. evaluate_schema_conformance()                          |
|   2. evaluate_quality_rules()                               |
|   3. evaluate_sla_freshness()                               |
|   4. evaluate_consumer_compatibility()                      |
+------------------------------+------------------------------+
                               |
                               v
+-------------------------------------------------------------+
|                   Repository & Storage                      |
|   - ContractRepository                                      |
|   - JobRepository                                           |
|   - SchemaSnapshotRepository                                |
|   - TestRun / TestResult queries                            |
+-------------------------------------------------------------+
```

---

## 4. Detailed Specification

### 4.1 Backend Engine: `api/services/contract_tester.py`

- **Class**: `ContractTestingEngine`
- **Method**: `test_contract(contract_name: str, db: Session) -> ContractTestReport`
- **Check Model**:
  ```python
  class CheckResult(BaseModel):
      id: str
      category: str  # "schema" | "quality" | "sla" | "consumers"
      name: str
      status: str    # "PASS" | "FAIL" | "WARN"
      target: str    # column name, job name, or contract scope
      expected: Any
      actual: Any
      message: str
  ```
- **Report Model**:
  ```python
  class ContractTestReport(BaseModel):
      contract: str
      source_job: str
      version: str
      overall_status: str  # "PASSED" | "FAILED" | "WARNING"
      executed_at: str
      duration_ms: float
      summary: dict[str, Any]  # total, passed, failed, warnings, pass_rate
      checks: list[CheckResult]
  ```

#### Evaluation Rules:
1. **Schema Check**:
   - Compares snapshot columns against contract expectations and DQ params.
   - Verifies column types match standard data types (e.g. `VARCHAR`, `TEXT`, `INTEGER`, `DECIMAL`, `TIMESTAMP`, `BOOLEAN`).
   - Flags missing required fields as `FAIL`, type mismatches as `FAIL`, extra unexpected fields as `WARN`.
2. **Quality Rules Check**:
   - Evaluates `not_null` columns: `PASS` if 0 nulls, `FAIL` if nulls detected in run metrics or test results.
   - Evaluates `unique_key` columns: `PASS` if 0 duplicate keys, `FAIL` if duplicates detected.
   - Evaluates `row_count_non_zero`: `PASS` if `source_row_count > 0`, `FAIL` if 0.
   - Evaluates range/enum checks if present in job parameters.
3. **SLA & Freshness Check**:
   - Evaluates elapsed hours since `latest_run.completed_at`. If `elapsed <= sla_hours`, status is `PASS`. If `elapsed > sla_hours`, status is `FAIL` with hours overdue.
   - Checks active breaches: `FAIL` if open breach exists, `WARN` if resolved but elevated duration.
4. **Consumer Check**:
   - Checks that consumers are specified and validates that contract versioning follows semantic conventions for consumer compatibility.

### 4.2 REST API Routes: `api/routes/contracts.py`

- `POST /api/contracts/{name}/test`: Runs the contract testing engine and returns the `ContractTestReport`.
- `GET /api/contracts/{name}/test-summary`: Returns the latest evaluated test summary for badge/status display.

### 4.3 Web UI Component: `frontend/partials/tab-contracts.html`

- Added under the selected contract detail container:
  - **Testing Header**: Title "Contract Testing & Verification", "Run Contract Test" button with loading spinner, and timestamp of last execution.
  - **Summary Metric Cards**:
    - Overall Status (`PASSED`, `FAILED`, `WARNING`) with distinct color themes (emerald, rose, amber).
    - Pass Rate Percentage (e.g. `100%`, `80%`).
    - Total Checks, Passed Count, Failed Count, Warnings Count.
    - Test Execution Duration (ms).
  - **Category Tabs Filter**: Filter test assertions by `All (N)`, `Schema (N)`, `Data Quality (N)`, `SLA & Freshness (N)`, `Consumers (N)`.
  - **Test Assertions Table**:
    - Columns: Status, Category, Check Name, Target / Field, Expected, Actual, Diagnostic Details.
    - Filterable and accessible with proper ARIA attributes.
  - **ODCS Export Button**: "Export ODCS Report" downloads a JSON file formatted according to the Open Data Contract Standard test result schema.

### 4.4 Alpine.js Feature Slice: `frontend/features/contracts.js`

- **State Additions**:
  - `contractTestingLoading: false`
  - `contractTestResult: null`
  - `contractTestCategoryFilter: 'all'`
- **Methods**:
  - `async runContractTest(contractName)`: Calls `POST /api/contracts/{name}/test`, stores result in `contractTestResult`.
  - `filteredContractChecks()`: Filters `contractTestResult.checks` based on `contractTestCategoryFilter`.
  - `exportContractTestReport()`: Generates and triggers download of the ODCS JSON test report blob.

---

## 5. Verification Plan

1. **Unit Tests (`tests/unit/test_contract_tester.py`)**:
   - Test all 4 ODCS categories in isolation with mock database sessions, schema snapshots, and job test results.
   - Validate error handling when source jobs or snapshots are missing.
2. **Integration Tests (`tests/integration/test_contracts_testing_api.py`)**:
   - Test `POST /api/contracts/{name}/test` endpoint via FastAPI TestClient / HTTP.
   - Verify auth enforcement, status code handling, and response schema.
3. **Playwright E2E Tests (`tests/e2e/09-contracts.spec.ts`)**:
   - Navigate to Contracts tab.
   - Select a contract and execute "Run Contract Test".
   - Verify status banner, category filters, and check rows render accurately.
   - Verify report export button.
4. **HTML Build Validation**:
   - Run `node scripts/build-html.js` and verify clean build.
