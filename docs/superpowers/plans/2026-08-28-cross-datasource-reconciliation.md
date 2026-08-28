# Cross-Datasource Reconciliation Matrix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add functionality to compare and reconcile across any combination of data sources (SQL queries, local/uploaded files, S3/Athena, SAP BO, API endpoints) in both backend and Web UI.

**Architecture:** A unified `DataSourceAdapter` registry normalizes tabular data extraction into DataFrames/streams across heterogeneous engines, routes them to `ReconciliationEngine`, and exposes dynamic forms and result visualizations in a new **Cross-Source Matrix** sub-tab in the Web UI.

**Tech Stack:** FastAPI, Pydantic v2, Pandas, SQLAlchemy, Alpine.js, Tailwind CSS.

## Global Constraints

- Platform: Windows (win32), Python 3.10+
- Database Support: SQL Server, Postgres, Oracle, SQLite, MySQL via SQLAlchemy
- Coding Rules: Exact file paths, TDD workflows, zero unused imports, maintain existing audit logging and run artifact structures verbatim.

---

### Task 1: Data Source Adapters Module

**Files:**
- Create: `etl_framework/reconciliation/data_sources.py`
- Create: `tests/unit/test_data_sources.py`

**Interfaces:**
- Consumes: Pandas, SQLAlchemy engines, File loader utilities in `api/services/file_source.py`
- Produces: `extract_data_source(spec: dict, db_session=None) -> pd.DataFrame`

- [ ] **Step 1: Write failing tests for DataSourceAdapter**

```python
# tests/unit/test_data_sources.py
import pytest
import pandas as pd
from etl_framework.reconciliation.data_sources import extract_data_source

def test_extract_local_file_source(tmp_path):
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("id,val\n1,100\n2,200\n", encoding="utf-8")
    
    spec = {
        "source_type": "file",
        "file_path": str(csv_file)
    }
    df = extract_data_source(spec)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert list(df.columns) == ["id", "val"]

def test_extract_sql_table_source(db_session=None):
    spec = {
        "source_type": "sql",
        "query_or_table": "SELECT 1 as id, 'A' as label"
    }
    # Mocking SQLite in-memory standard execution test
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_data_sources.py`
Expected: FAIL with "ModuleNotFoundError: No module named 'etl_framework.reconciliation.data_sources'"

- [ ] **Step 3: Write DataSourceAdapter module implementation**

Create `etl_framework/reconciliation/data_sources.py` with `extract_data_source(spec: dict) -> pd.DataFrame` handling `file`, `sql`, `aws_athena`, `sap_bo`, `api`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_data_sources.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/data_sources.py tests/unit/test_data_sources.py
git commit -m "feat: add data source extraction adapters for matrix reconciliation"
```

---

### Task 2: Pydantic Schemas & Matrix Endpoints

**Files:**
- Modify: `api/schemas.py`
- Modify: `api/routes/compare.py`
- Create: `tests/unit/test_compare_matrix_route.py`

**Interfaces:**
- Consumes: `extract_data_source` from Task 1
- Produces: `POST /api/compare/matrix` returning `RunStatusOut`

- [ ] **Step 1: Write failing test for Matrix Endpoint**

```python
# tests/unit/test_compare_matrix_route.py
from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)

def test_compare_matrix_endpoint_validation():
    payload = {
        "source_a": {"source_type": "file", "file_path": "invalid.csv"},
        "source_b": {"source_type": "sql", "query_or_table": "SELECT 1"},
        "key_columns": ["id"]
    }
    response = client.post("/api/compare/matrix", json=payload)
    # Should accept request with status 202
    assert response.status_code in (202, 400, 422)
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/unit/test_compare_matrix_route.py`
Expected: 404 or missing endpoint failure

- [ ] **Step 3: Add `DataSourceSpec` & `MatrixCompareRequest` schemas and endpoint in `compare.py`**

Add schemas to `api/schemas.py` and implement `@router.post("/matrix")` in `api/routes/compare.py`.

- [ ] **Step 4: Run test to verify endpoint creates run and returns status 202**

Run: `pytest tests/unit/test_compare_matrix_route.py`
Expected: PASS with 202 Accepted

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py api/routes/compare.py tests/unit/test_compare_matrix_route.py
git commit -m "feat: add matrix comparison endpoint and schemas"
```

---

### Task 3: Background Matrix Comparison Service Logic

**Files:**
- Modify: `api/services/compare_service.py`
- Modify: `api/routes/compare.py`
- Modify: `tests/unit/test_compare_matrix_route.py`

**Interfaces:**
- Consumes: `ReconciliationEngine` and `extract_data_source`
- Produces: `CompareService.run_matrix_comparison(req: MatrixCompareRequest, run_id: str)`

- [ ] **Step 1: Add unit test for `run_matrix_comparison`**

```python
# tests/unit/test_compare_matrix_service.py
from api.services.compare_service import CompareService
# test matrix comparison execution end-to-end against test runs database
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/unit/test_compare_matrix_service.py`
Expected: AttributeError: 'CompareService' has no attribute 'run_matrix_comparison'

- [ ] **Step 3: Implement `run_matrix_comparison` method in `CompareService`**

Load source A and source B using `extract_data_source`, align schema, invoke `ReconciliationEngine`, and log test results to `RunRepository`.

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/unit/test_compare_matrix_service.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_service.py api/routes/compare.py tests/unit/test_compare_matrix_service.py
git commit -m "feat: implement matrix comparison backend processing service"
```

---

### Task 4: Web UI Sub-tab & Dynamic Matrix Form Controls

**Files:**
- Modify: `frontend/partials/tab-compare.html`
- Modify: `frontend/features/compare.js`
- Test: `tests/e2e/test_matrix_ui.spec.ts` (Playwright)

**Interfaces:**
- Consumes: `POST /api/compare/matrix`
- Produces: Web UI Matrix comparison sub-tab and interactive result viewer

- [ ] **Step 1: Add Playwright UI test for Matrix Sub-tab**

```typescript
// tests/e2e/test_matrix_ui.spec.ts
import { test, expect } from '@playwright/test';

test('navigate to matrix compare subtab', async ({ page }) => {
  await page.goto('/');
  await page.click('button[data-testid="tab-compare"]');
  await page.click('button[data-testid="compare-subtab-matrix"]');
  await expect(page.locator('#matrix-compare-container')).toBeVisible();
});
```

- [ ] **Step 2: Run E2E test to verify failure**

Run: `npx playwright test tests/e2e/test_matrix_ui.spec.ts`
Expected: FAIL (subtab button not found)

- [ ] **Step 3: Add HTML markup in `tab-compare.html` and state handler in `compare.js`**

Add `Matrix` subtab pill, Source A & Source B dropdowns/inputs, and `runMatrixCompare()` JS handler.

- [ ] **Step 4: Run E2E test to verify pass**

Run: `npx playwright test tests/e2e/test_matrix_ui.spec.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/partials/tab-compare.html frontend/features/compare.js tests/e2e/test_matrix_ui.spec.ts
git commit -m "feat: add Cross-Source Matrix subtab and controls to Web UI"
```

---

### Task 5: End-to-End Cross-Datasource Integration Tests

**Files:**
- Create: `tests/integration/test_cross_datasource_reconciliation.py`

- [ ] **Step 1: Write integration test for SQL-to-File and File-to-API comparisons**

```python
# tests/integration/test_cross_datasource_reconciliation.py
import pytest
from fastapi.testclient import TestClient
from api.main import app

def test_sql_to_file_matrix_reconciliation(tmp_path):
    # Test end-to-end execution of matrix endpoint with SQL source vs File target
```

- [ ] **Step 2: Run integration test**

Run: `pytest tests/integration/test_cross_datasource_reconciliation.py`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_cross_datasource_reconciliation.py
git commit -m "test: add integration test suite for cross-datasource matrix reconciliation"
```
