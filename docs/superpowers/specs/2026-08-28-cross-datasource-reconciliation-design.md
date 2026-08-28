# Design Spec: Cross-Datasource Comparison and Reconciliation

**Date**: 2026-08-28  
**Status**: Approved  
**Topic**: Heterogeneous Data Source Matrix Reconciliation & Web UI Integration  

---

## 1. Executive Summary

This feature adds comprehensive cross-datasource comparison and reconciliation capabilities to the platform. Users can compare and reconcile data across heterogeneous data sources—such as SQL queries/tables vs. local files, S3/Athena queries, SAP BO reports, and HTTP REST APIs—in any combination. The solution standardizes extraction via a universal Data Adapter interface, feeds the extracted data into the `ReconciliationEngine`, and exposes full interactive controls in a dedicated Web UI tab (**Cross-Source Matrix**).

---

## 2. Architecture & Data Flow

```
+-------------------------------------------------------------------------------+
|                                  Web UI                                       |
|               (Compare Tab -> Cross-Source Matrix Sub-tab)                     |
+-------------------------------------------------------------------------------+
       |                                                               |
       | Source A Spec                                                 | Source B Spec
       v                                                               v
+-------------------------------------------------------------------------------+
|                       POST /api/compare/matrix                                |
+-------------------------------------------------------------------------------+
                                       |
                                       v
+-------------------------------------------------------------------------------+
|                             CompareService                                    |
|               (Extract via DataSourceAdapter Registry)                        |
|                                                                               |
|   +--------------+  +--------------+  +--------------+  +---------------+  |
|   |  SQLAdapter  |  |  FileAdapter |  | AthenaAdapter|  |  API/BOAdapter|  |
|   +--------------+  +--------------+  +--------------+  +---------------+  |
+-------------------------------------------------------------------------------+
                                       |
                                       v  (DataFrames / Streams)
+-------------------------------------------------------------------------------+
|                            ReconciliationEngine                               |
|        - Schema Alignment & Data Type Coercion                                |
|        - Primary / Composite Key Indexing & Row Matching                      |
|        - Value Diff & Mismatch Aggregation                                    |
+-------------------------------------------------------------------------------+
                                       |
                                       v
+-------------------------------------------------------------------------------+
|                         RunRepository & Audit Log                             |
|          - Status: PASSED / FAILED / MISMATCH                                 |
|          - Artifact Stores & Report Visualizers                               |
+-------------------------------------------------------------------------------+
```

---

## 3. Detailed Components & Data Models

### 3.1 Backend Pydantic Schemas (`api/schemas.py`)

#### `DataSourceSpec`
Represents the configuration for extracting tabular data from any supported data source:
- `source_type`: `sql` | `file` | `aws_athena` | `aws_glue` | `sap_bo` | `api`
- `config_id`: Optional[int] — Saved DB or system config reference
- `connection_name`: Optional[str] — Named database connection within config
- `query_or_table`: Optional[str] — SQL string or database table name
- `file_path`: Optional[str] — Local path or S3 key
- `file_b64`: Optional[str] — Base64-encoded file content (for direct web uploads)
- `file_name`: Optional[str] — File display name
- `endpoint_url`: Optional[str] — REST API URL
- `http_method`: Optional[str] — `GET` or `POST`
- `headers`: Optional[dict[str, str]] — Custom headers
- `bo_doc_id`: Optional[str] / `bo_report_id`: Optional[str] — SAP BO identifiers

#### `MatrixCompareRequest`
Payload sent to `POST /api/compare/matrix`:
- `source_a`: `DataSourceSpec`
- `source_b`: `DataSourceSpec`
- `label_a`: str — Custom display label for Source A (default: "Source A")
- `label_b`: str — Custom display label for Source B (default: "Source B")
- `key_columns`: list[str] — Primary or composite join keys
- `exclude_columns`: list[str] — Columns ignored during comparison
- `numeric_tolerance`: float — Allowed variance for float/decimal fields (default: 0.0)
- `ignore_case`: bool — Case-insensitive string matching (default: false)
- `trim_whitespace`: bool — Trim string spaces before matching (default: true)

---

### 3.2 Backend Service Layer (`api/services/compare_service.py` & `etl_framework/reconciliation/data_sources.py`)

1. **`DataSourceAdapter` Registry**:
   - `SQLAdapter`: Executes query against target engine (Postgres, SQL Server, Oracle, SQLite, MySQL) via SQLAlchemy.
   - `FileAdapter`: Reads local files (CSV, Excel, Parquet, JSON, TSV) using pandas and chunked file loaders.
   - `AthenaAdapter`: Uses `AWSAthenaRuntime` to run SQL query on AWS Athena and stream result set.
   - `APIAdapter`: Makes HTTP GET/POST calls to external APIs, unwraps JSON responses into tabular DataFrames.
   - `BOAdapter`: Fetches BO report snapshot or archived output.

2. **Reconciliation Pipeline**:
   - Asynchronously resolves and extracts Source A and Source B DataFrames.
   - Normalizes column naming and type differences.
   - Calls `ReconciliationEngine.compare_dataframes()` to generate key-based mismatch metrics:
     - `matched_count`
     - `missing_in_target_count`
     - `missing_in_source_count`
     - `value_mismatch_count`
   - Stores summary report snapshot and returns `RunStatusOut` with standard async background polling pattern.

---

### 3.3 Web UI Layer (`frontend/partials/tab-compare.html` & `frontend/features/compare.js`)

1. **Cross-Source Matrix Sub-tab**:
   - Added as sub-tab pill `Matrix` (`compareSubTab === 'matrix'`).
   - Side-by-side **Source A** and **Source B** configuration cards with dynamic mode selectors (`SQL`, `File`, `AWS Athena`, `SAP BO`, `API`).
   - SQL query editor textareas for live query input when `SQL` or `AWS Athena` mode is active.
   - Drag-and-drop file upload zone for `File` upload mode.
   - Global reconciliation options form: Key Columns, Exclude Columns, Numeric Tolerance, Trim Whitespace.

2. **Action Handlers & Interactivity**:
   - `runMatrixCompare()` method attached to the **Run Matrix Compare** button.
   - Sends payload to `POST /api/compare/matrix`, polls job status via `boComparePollInterval` mechanism.
   - Displays real-time status banner, summary metric tiles, and structured table diff viewer.
   - Template Integration: Allows saving and loading matrix comparison settings through `loadCompareTemplate()` / `saveCompareTemplate()`.

---

## 4. Error Handling & Edge Cases

1. **Missing / Unreachable Data Source**: Clear 400/422 status response with adapter label, detail message, and connection check instructions.
2. **Schema Mismatch**: Auto-align common columns; highlight missing or extra columns in mismatch summary.
3. **Empty Result Sets**: Flag warnings if Source A or Source B produces 0 rows without failing the pipeline execution.
4. **Large Data Volumes**: Stream chunked frames for SQL and large files to bound memory overhead.

---

## 5. Verification & Testing Strategy

1. **Unit Tests**: Test `DataSourceAdapter` extraction for SQL, File, API, and Athena mocks in `tests/test_compare_matrix.py`.
2. **Integration Tests**: Verify `POST /api/compare/matrix` endpoint for SQL-to-File, File-to-API, and SQL-to-Athena combinations.
3. **Frontend E2E Smoke Tests**: Validate UI sub-tab navigation, dynamic input rendering, and run submit flow using Playwright.
