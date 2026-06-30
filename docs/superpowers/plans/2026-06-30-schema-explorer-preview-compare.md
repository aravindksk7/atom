# Schema Explorer, Query Preview, Compare Diffs & Multi-format File Compare — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add schema/table browsing to the Config tab, SQL query preview to the Job form, row-level diff details + download to the Compare tab, and multi-format file compare (CSV, Excel, JSON, TSV) to the recon-file compare endpoint.

**Architecture:** Backend-first (Tasks 1–7) with TDD; frontend second (Tasks 8–12) with manual verification. All backend tasks produce working, independently testable changes. Each frontend task is a focused addition to `frontend/app.js` and/or `frontend/index.html`.

**Tech Stack:** FastAPI, SQLAlchemy, pandas, Alpine.js (vanilla SPA), pytest with SQLite in-memory + TestClient + Bearer token (matches existing test pattern in `tests/unit/test_compare_api.py`)

---

## File Map

| File | Change |
|------|--------|
| `api/services/file_source.py` | Add JSON and TSV support to `read_tabular` |
| `api/schemas.py` | Add `file_a_name`, `file_b_name`, `key_columns`, `exclude_columns` to `ReconFileCompareRequest` |
| `api/services/compare_service.py` | Store per-metric mismatch details; add tabular file detection + `_run_tabular_file_compare` |
| `api/routes/configs.py` | Add `GET /{id}/schema` and `POST /{id}/preview-query` endpoints |
| `api/routes/runs.py` | Add `GET /{run_id}/mismatches/download` endpoint |
| `frontend/app.js` | Schema explorer state+methods; job preview state+method; compare download+diff-expand methods; update `handleReconFileUpload` |
| `frontend/index.html` | Schema explorer panel in Config tab; preview bar in job modal; download bar + diff expand rows in Compare tab; update file input `accept` attrs |
| `tests/unit/test_file_source_extended.py` | New — JSON and TSV tests |
| `tests/unit/test_mismatch_storage.py` | New — verify `add_mismatch_details` called after recon-file compare |
| `tests/unit/test_tabular_file_compare.py` | New — verify tabular branch routes to `_run_tabular_file_compare` |
| `tests/unit/test_schema_endpoints.py` | New — schema explorer and preview-query endpoint tests |
| `tests/unit/test_mismatch_download.py` | New — download endpoint returns CSV and XLSX |

---

## Task 1: Extend `read_tabular` for JSON and TSV

**Files:**
- Modify: `api/services/file_source.py:68-103`
- Create: `tests/unit/test_file_source_extended.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_file_source_extended.py`:

```python
from __future__ import annotations
import base64, io, json
import pandas as pd
import pytest
from fastapi import HTTPException
from api.services.file_source import read_tabular


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_read_json_records():
    data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    df = read_tabular(content_b64=b64(json.dumps(data).encode()), file_name="data.json")
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2


def test_read_tsv():
    raw = b"id\tname\n1\tAlice\n2\tBob\n"
    df = read_tabular(content_b64=b64(raw), file_name="data.tsv")
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2


def test_read_txt_treated_as_tsv():
    raw = b"id\tname\n1\tAlice\n"
    df = read_tabular(content_b64=b64(raw), file_name="data.txt")
    assert "id" in df.columns
    assert len(df) == 1


def test_unsupported_extension_raises_400():
    raw = b"fake binary"
    with pytest.raises(HTTPException) as exc_info:
        read_tabular(content_b64=b64(raw), file_name="data.parquet")
    assert exc_info.value.status_code == 400
    assert ".parquet" in exc_info.value.detail


def test_existing_csv_still_works():
    raw = b"id,val\n1,10\n2,20\n"
    df = read_tabular(content_b64=b64(raw), file_name="data.csv")
    assert list(df.columns) == ["id", "val"]
    assert len(df) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_file_source_extended.py -v
```

Expected: FAIL on `test_read_json_records`, `test_read_tsv`, `test_read_txt_treated_as_tsv`, `test_unsupported_extension_raises_400` (wrong error message on last one).

- [ ] **Step 3: Implement JSON and TSV support**

In `api/services/file_source.py`, replace lines 68–79 (the `content_b64` extension dispatch block):

```python
    if content_b64 is not None:
        raw = base64.b64decode(content_b64)
        name = file_name or ""
        ext = Path(name).suffix.lower()
        if ext == ".csv":
            return _read_csv_bytes(raw)
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(io.BytesIO(raw))
        if ext == ".json":
            try:
                return pd.read_json(io.BytesIO(raw))
            except ValueError:
                return pd.read_json(io.BytesIO(raw), orient="records")
        if ext in (".tsv", ".txt"):
            return pd.read_csv(io.BytesIO(raw), sep="\t")
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format '{ext}'. Use .csv, .xlsx, .json, or .tsv",
        )
```

Also replace lines 93–103 (the path-based extension dispatch block):

```python
    p = resolved
    ext = p.suffix.lower()
    try:
        if ext == ".csv":
            return _read_csv_bytes(p.read_bytes())
        if ext in (".xlsx", ".xls"):
            return pd.read_excel(p)
        if ext == ".json":
            try:
                return pd.read_json(p)
            except ValueError:
                return pd.read_json(p, orient="records")
        if ext in (".tsv", ".txt"):
            return pd.read_csv(p, sep="\t")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    raise HTTPException(
        status_code=400,
        detail=f"Unsupported file format '{ext}'. Use .csv, .xlsx, .json, or .tsv",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_file_source_extended.py -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/file_source.py tests/unit/test_file_source_extended.py
git commit -m "feat(file-source): add JSON and TSV support to read_tabular"
```

---

## Task 2: Add schema fields to `ReconFileCompareRequest`

**Files:**
- Modify: `api/schemas.py:512-526`

- [ ] **Step 1: Write a failing test**

Add this file `tests/unit/test_recon_schema_fields.py`:

```python
from api.schemas import ReconFileCompareRequest


def test_file_names_accepted():
    req = ReconFileCompareRequest(
        stored_run_id="run-a",
        file_b_content_b64="abc",
        file_a_name="source.csv",
        file_b_name="target.csv",
    )
    assert req.file_a_name == "source.csv"
    assert req.file_b_name == "target.csv"


def test_key_columns_accepted():
    req = ReconFileCompareRequest(
        file_a_content_b64="abc",
        file_b_content_b64="xyz",
        file_a_name="a.csv",
        file_b_name="b.csv",
        key_columns=["id", "order_id"],
        exclude_columns=["created_at"],
    )
    assert req.key_columns == ["id", "order_id"]
    assert req.exclude_columns == ["created_at"]


def test_defaults():
    req = ReconFileCompareRequest(stored_run_id="x", stored_run_id_b="y")
    assert req.file_a_name is None
    assert req.file_b_name is None
    assert req.key_columns is None
    assert req.exclude_columns == []
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/unit/test_recon_schema_fields.py -v
```

Expected: FAIL — `ReconFileCompareRequest` doesn't accept `file_a_name`, `key_columns`, etc.

- [ ] **Step 3: Add the four new fields**

In `api/schemas.py`, find the `ReconFileCompareRequest` class (around line 512). After the `label_b` field and before the `@model_validator`, insert:

```python
    file_a_name: str | None = None
    file_b_name: str | None = None
    key_columns: list[str] | None = None
    exclude_columns: list[str] = Field(default_factory=list)
```

The class should look like:

```python
class ReconFileCompareRequest(BaseModel):
    stored_run_id: str | None = None
    stored_run_id_b: str | None = None
    file_a_path: str | None = None
    file_a_content_b64: str | None = None
    file_b_path: str | None = None
    file_b_content_b64: str | None = None
    label_a: str = "Run / File A"
    label_b: str = "Production Report"
    file_a_name: str | None = None
    file_b_name: str | None = None
    key_columns: list[str] | None = None
    exclude_columns: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sources(self) -> "ReconFileCompareRequest":
        ...
```

`Field` is already imported in `api/schemas.py` (`from pydantic import BaseModel, Field, ...`).

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_recon_schema_fields.py -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py tests/unit/test_recon_schema_fields.py
git commit -m "feat(schemas): add file_a_name, file_b_name, key_columns, exclude_columns to ReconFileCompareRequest"
```

---

## Task 3: Store per-metric `MismatchRecord` rows in `run_recon_file_compare`

**Files:**
- Modify: `api/services/compare_service.py:180-198`
- Create: `tests/unit/test_mismatch_storage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_mismatch_storage.py`:

```python
from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from api.schemas import ReconFileCompareRequest
from api.services.compare_service import CompareService


def _service_with_mock_repo():
    svc = CompareService.__new__(CompareService)
    svc._repo = MagicMock()
    svc._repo.update_run_status = MagicMock()
    svc._repo.add_test_result = MagicMock(return_value=SimpleNamespace(id=42))
    svc._repo.add_mismatch_details = MagicMock()
    return svc


def test_mismatch_details_stored_when_stats_differ(monkeypatch):
    svc = _service_with_mock_repo()

    stats = {
        "a": {"orders": {"status": "PASSED", "source_row_count": 100, "target_row_count": 100, "total_issues": 0}},
        "b": {"orders": {"status": "FAILED", "source_row_count": 100, "target_row_count": 90, "total_issues": 5}},
    }
    monkeypatch.setattr(svc, "_load_recon_source", lambda req, side: stats[side])

    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        req = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")
        svc.run_recon_file_compare(req, "run-x")

    svc._repo.add_mismatch_details.assert_called_once()
    result_id, records = svc._repo.add_mismatch_details.call_args[0]
    assert result_id == 42
    col_names = [r.column_name for r in records]
    assert "target_row_count" in col_names
    assert "total_issues" in col_names


def test_no_mismatch_details_when_stats_match(monkeypatch):
    svc = _service_with_mock_repo()

    same = {"orders": {"status": "PASSED", "source_row_count": 100, "target_row_count": 100, "total_issues": 0}}
    monkeypatch.setattr(svc, "_load_recon_source", lambda req, side: same)

    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        req = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")
        svc.run_recon_file_compare(req, "run-y")

    svc._repo.add_mismatch_details.assert_not_called()
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/unit/test_mismatch_storage.py -v
```

Expected: FAIL — `add_mismatch_details` is never called currently.

- [ ] **Step 3: Implement mismatch storage**

In `api/services/compare_service.py`, change lines 180–198 (inside `run_recon_file_compare`, the per-test loop body that currently ends with `self._repo.add_test_result(run_id, synthetic); results.append(synthetic)`):

**Replace:**
```python
                from etl_framework.reconciliation.models import ReconciliationResult
                from etl_framework.runner.state import TestStatus as TS
                synthetic = ReconciliationResult(
                    query_name=name,
                    source_env=req.label_a,
                    target_env=req.label_b,
                    source_row_count=a.get("source_row_count", 0),
                    target_row_count=b.get("source_row_count", 0),
                    matched_count=0,
                    missing_in_target_count=0,
                    missing_in_source_count=0,
                    value_mismatch_count=0 if status == "PASSED" else max(1, differences),
                    mismatches=[],
                    status=TS.PASSED if status == "PASSED" else TS.FAILED,
                    executed_at=datetime.now(timezone.utc),
                    duration_seconds=0.0,
                )
                self._repo.add_test_result(run_id, synthetic)
                results.append(synthetic)
```

**With:**
```python
                from etl_framework.reconciliation.models import ReconciliationResult, MismatchRecord
                from etl_framework.runner.state import TestStatus as TS
                synthetic = ReconciliationResult(
                    query_name=name,
                    source_env=req.label_a,
                    target_env=req.label_b,
                    source_row_count=a.get("source_row_count", 0),
                    target_row_count=b.get("source_row_count", 0),
                    matched_count=0,
                    missing_in_target_count=0,
                    missing_in_source_count=0,
                    value_mismatch_count=0 if status == "PASSED" else max(1, differences),
                    mismatches=[],
                    status=TS.PASSED if status == "PASSED" else TS.FAILED,
                    executed_at=datetime.now(timezone.utc),
                    duration_seconds=0.0,
                )
                tr = self._repo.add_test_result(run_id, synthetic)
                if status != "PASSED":
                    _mm = [
                        MismatchRecord(
                            key_values={"test_name": name},
                            column_name=metric,
                            source_value=str(a.get(metric)) if a.get(metric) is not None else "",
                            target_value=str(b.get(metric)) if b.get(metric) is not None else "",
                            mismatch_type="stat_diff",
                        )
                        for metric in compared_metrics
                        if a.get(metric) != b.get(metric)
                    ]
                    if _mm:
                        self._repo.add_mismatch_details(tr.id, _mm)
                results.append(synthetic)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_mismatch_storage.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_mismatch_storage.py
git commit -m "feat(compare): store per-metric MismatchRecord rows in run_recon_file_compare"
```

---

## Task 4: Tabular file branch in `_load_recon_source` + `_run_tabular_file_compare`

**Files:**
- Modify: `api/services/compare_service.py` (3 changes: imports at top, `_load_recon_source`, `run_recon_file_compare`, new method `_run_tabular_file_compare`)
- Create: `tests/unit/test_tabular_file_compare.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_tabular_file_compare.py`:

```python
from __future__ import annotations
import base64, io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from api.schemas import ReconFileCompareRequest
from api.services.compare_service import CompareService


def _b64csv(data: dict) -> str:
    buf = io.BytesIO()
    pd.DataFrame(data).to_csv(buf, index=False)
    return base64.b64encode(buf.getvalue()).decode()


def _svc():
    svc = CompareService.__new__(CompareService)
    svc._repo = MagicMock()
    svc._repo.update_run_status = MagicMock()
    svc._repo.add_test_result = MagicMock(return_value=SimpleNamespace(id=5))
    svc._repo.add_mismatch_details = MagicMock()
    return svc


def test_load_recon_source_returns_df_for_csv():
    svc = _svc()
    b64 = _b64csv({"id": [1, 2], "val": [10, 20]})
    req = ReconFileCompareRequest(
        file_a_content_b64=b64,
        file_a_name="data.csv",
        stored_run_id_b="some-run",
    )
    result = svc._load_recon_source(req, "a")
    assert isinstance(result, pd.DataFrame)
    assert list(result.columns) == ["id", "val"]


def test_load_recon_source_returns_dict_for_stored_run():
    svc = _svc()
    from types import SimpleNamespace
    run = SimpleNamespace(results=[
        SimpleNamespace(query_name="q1", effective_status="PASSED",
                        source_row_count=10, target_row_count=10, total_issues=0)
    ])
    svc._repo.get_run = MagicMock(return_value=run)
    req = ReconFileCompareRequest(stored_run_id="run-a", stored_run_id_b="run-b")
    result = svc._load_recon_source(req, "a")
    assert isinstance(result, dict)
    assert "q1" in result


def test_run_tabular_file_compare_stores_mismatches():
    svc = _svc()
    df_a = pd.DataFrame({"id": [1, 2], "amount": [100, 200]})
    df_b = pd.DataFrame({"id": [1, 2], "amount": [100, 210]})  # row 2 differs

    req = ReconFileCompareRequest(
        file_a_content_b64="x",
        file_a_name="a.csv",
        file_b_content_b64="y",
        file_b_name="b.csv",
        key_columns=["id"],
    )
    with patch("api.services.compare_service.MetricsWriter") as mw:
        mw.return_value.write = MagicMock()
        svc._run_tabular_file_compare(req, "run-z", df_a, df_b)

    svc._repo.add_mismatch_details.assert_called_once()
    svc._repo.update_run_status.assert_called()


def test_mixed_sources_raise_422(monkeypatch):
    from fastapi import HTTPException
    svc = _svc()

    b64 = _b64csv({"id": [1]})
    monkeypatch.setattr(svc, "_load_recon_source", lambda req, side: (
        pd.DataFrame({"id": [1]}) if side == "a" else {"q1": {}}
    ))
    svc._repo.update_run_status = MagicMock()

    req = ReconFileCompareRequest(
        file_a_content_b64=b64, file_a_name="a.csv", stored_run_id_b="run-b"
    )
    with patch("api.services.compare_service.MetricsWriter"):
        with pytest.raises(HTTPException) as exc:
            svc.run_recon_file_compare(req, "run-mixed")
    assert exc.value.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/unit/test_tabular_file_compare.py -v
```

Expected: FAIL — `_load_recon_source` doesn't detect CSV, `_run_tabular_file_compare` doesn't exist.

- [ ] **Step 3: Implement the tabular branch**

**3a. Add `pathlib.Path` import at the top of `api/services/compare_service.py`** (after line `import base64`):

```python
from pathlib import Path
```

**3b. Replace the entire `_load_recon_source` method** (currently lines 212–229):

```python
    def _load_recon_source(self, req: ReconFileCompareRequest, side: str):
        """Load one side of a recon-file compare.

        Returns a dict[str, dict] for stored-run and HTML sources,
        or a pd.DataFrame for tabular file sources (.csv, .xlsx, .json, .tsv, .txt).
        """
        stored_run_id = req.stored_run_id if side == "a" else req.stored_run_id_b
        file_path = req.file_a_path if side == "a" else req.file_b_path
        file_content_b64 = req.file_a_content_b64 if side == "a" else req.file_b_content_b64
        file_name = req.file_a_name if side == "a" else req.file_b_name

        if stored_run_id:
            run = self._repo.get_run(stored_run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=f"Stored run for Source {side.upper()} not found")
            return {
                r.query_name: {
                    "status": r.effective_status,
                    "source_row_count": r.source_row_count,
                    "target_row_count": r.target_row_count,
                    "total_issues": r.total_issues,
                }
                for r in run.results
            }

        _TABULAR_EXTS = {".csv", ".xlsx", ".xls", ".json", ".tsv", ".txt"}
        name = file_name or file_path or ""
        ext = Path(name).suffix.lower() if name else ""
        if ext in _TABULAR_EXTS:
            return read_tabular(path=file_path, content_b64=file_content_b64, file_name=name)

        return self._load_recon_html(file_path, file_content_b64)
```

**3c. In `run_recon_file_compare`, add the tabular branch immediately after loading sources** (after `stats_b = self._load_recon_source(req, "b")` at line 163, before `all_names = sorted(...)`):

```python
            import pandas as pd
            _is_df_a = isinstance(stats_a, pd.DataFrame)
            _is_df_b = isinstance(stats_b, pd.DataFrame)
            if _is_df_a != _is_df_b:
                raise HTTPException(
                    status_code=422,
                    detail="Both sources must be the same type (both tabular files or both HTML/stored runs)",
                )
            if _is_df_a:
                self._run_tabular_file_compare(req, run_id, stats_a, stats_b)
                return
```

**3d. Add `_run_tabular_file_compare` method** to the `CompareService` class (place it after `_validate_key_columns`, around line 150 area):

```python
    def _run_tabular_file_compare(
        self, req: ReconFileCompareRequest, run_id: str,
        df_a: "pd.DataFrame", df_b: "pd.DataFrame",
    ) -> None:
        key_columns = req.key_columns or self._infer_key_columns(df_a, df_b)
        self._validate_key_columns(df_a, df_b, key_columns)
        engine_a = _FrameEngine(df_a, req.label_a)
        engine_b = _FrameEngine(df_b, req.label_b)
        reconciler = ReconciliationEngine(
            engine_a, engine_b,
            key_columns=key_columns,
            exclude_columns=req.exclude_columns or [],
        )
        result = reconciler.reconcile(_SENTINEL_QUERY, req.label_a or "file_a")
        tr = self._repo.add_test_result(run_id, result)
        if result.mismatches:
            self._repo.add_mismatch_details(tr.id, result.mismatches)
        MetricsWriter(f"logs/metrics_{run_id}.json").write(run_id, [result])
        passed = 1 if result.status == TestStatus.PASSED else 0
        failed = 0 if passed else 1
        self._repo.update_run_status(
            run_id, "PASSED" if passed else "FAILED",
            completed_at=datetime.now(timezone.utc),
            total_tests=1, passed=passed, failed=failed,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_tabular_file_compare.py -v
```

Expected: all 4 PASS.

Also run the mismatch storage tests to make sure they still pass:

```
pytest tests/unit/test_mismatch_storage.py tests/unit/test_tabular_file_compare.py -v
```

- [ ] **Step 5: Commit**

```bash
git add api/services/compare_service.py tests/unit/test_tabular_file_compare.py
git commit -m "feat(compare): add tabular file branch (_run_tabular_file_compare) to recon-file compare"
```

---

## Task 5: `GET /api/configs/{id}/schema` endpoint

**Files:**
- Modify: `api/routes/configs.py`
- Create: `tests/unit/test_schema_endpoints.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_schema_endpoints.py`:

```python
from __future__ import annotations
import pytest
import pandas as pd
from types import SimpleNamespace
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import TokenRepository
from api.main import app


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def config_id(client):
    resp = client.post("/api/configs", json={
        "env_name": "dev",
        "name": "test-cfg",
        "config_data": {"db_host": "localhost", "db_password": "pass"},
    })
    assert resp.status_code == 201
    return resp.json()["id"]


def _fake_engine_cls(schema_df):
    class FakeDBEngine:
        def __init__(self, env, **kw):
            pass
        def execute_query(self, q, **kw):
            return schema_df
        def dispose(self):
            pass
    return FakeDBEngine


def test_get_schema_returns_grouped_tables(client, config_id, monkeypatch):
    schema_df = pd.DataFrame({
        "TABLE_SCHEMA": ["dbo", "dbo", "staging"],
        "TABLE_NAME": ["orders", "orders", "raw"],
        "COLUMN_NAME": ["id", "amount", "batch"],
        "DATA_TYPE": ["int", "decimal", "varchar"],
    })
    monkeypatch.setattr("etl_framework.db.engine.DBEngine", _fake_engine_cls(schema_df))

    resp = client.get(f"/api/configs/{config_id}/schema")
    assert resp.status_code == 200
    data = resp.json()
    tables = {(t["schema"], t["table"]): t for t in data}
    assert ("dbo", "orders") in tables
    assert len(tables[("dbo", "orders")]["columns"]) == 2
    assert ("staging", "raw") in tables


def test_get_schema_404_for_unknown_config(client):
    resp = client.get("/api/configs/99999/schema")
    assert resp.status_code == 404


def test_get_schema_400_on_db_error(client, config_id, monkeypatch):
    class FailEngine:
        def __init__(self, env, **kw):
            raise ConnectionError("Cannot connect")
        def dispose(self):
            pass

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", FailEngine)
    resp = client.get(f"/api/configs/{config_id}/schema")
    assert resp.status_code == 400
    assert "DB connection failed" in resp.json()["detail"]
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/unit/test_schema_endpoints.py::test_get_schema_returns_grouped_tables tests/unit/test_schema_endpoints.py::test_get_schema_404_for_unknown_config tests/unit/test_schema_endpoints.py::test_get_schema_400_on_db_error -v
```

Expected: FAIL — the route doesn't exist yet.

- [ ] **Step 3: Implement the schema endpoint**

In `api/routes/configs.py`, add these imports at the top (with the existing imports):

```python
from pydantic import BaseModel, ValidationError
```

(change `from pydantic import ValidationError` to `from pydantic import BaseModel, ValidationError`)

Then add the following route after the last existing route in the file:

```python
@router.get("/{config_id}/schema")
def get_db_schema(config_id: int, db: Session = Depends(get_session)):
    """Return all tables and columns from the database for this config."""
    repo = ConfigRepository(db)
    cfg = repo.get(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")

    default_keys = EnvironmentConfig(name="t", db_host="localhost", db_password="").model_dump(exclude={"name"})
    full_data = {**default_keys, **(cfg.config_json or {})}
    try:
        env = EnvironmentConfig(
            name=cfg.env_name or cfg.name,
            **{k: v for k, v in full_data.items() if k != "name"},
        )
        from etl_framework.db.engine import DBEngine
        engine = DBEngine(env)
        df = engine.execute_query(
            "SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION"
        )
        engine.dispose()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"DB connection failed: {exc}")

    tables: dict[tuple, list] = {}
    for _, row in df.iterrows():
        key = (str(row["TABLE_SCHEMA"]), str(row["TABLE_NAME"]))
        if key not in tables:
            tables[key] = []
        tables[key].append({"name": str(row["COLUMN_NAME"]), "type": str(row["DATA_TYPE"])})

    return [
        {"schema": k[0], "table": k[1], "columns": cols}
        for k, cols in tables.items()
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_schema_endpoints.py::test_get_schema_returns_grouped_tables tests/unit/test_schema_endpoints.py::test_get_schema_404_for_unknown_config tests/unit/test_schema_endpoints.py::test_get_schema_400_on_db_error -v
```

Expected: all 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/configs.py tests/unit/test_schema_endpoints.py
git commit -m "feat(configs): add GET /{id}/schema endpoint for database schema exploration"
```

---

## Task 6: `POST /api/configs/{id}/preview-query` endpoint

**Files:**
- Modify: `api/routes/configs.py`
- Modify: `tests/unit/test_schema_endpoints.py` (add 3 tests)

- [ ] **Step 1: Write failing tests**

Append these tests to `tests/unit/test_schema_endpoints.py`:

```python
def test_preview_query_returns_columns_and_rows(client, config_id, monkeypatch):
    result_df = pd.DataFrame({"id": [1, 2], "status": ["pending", "shipped"]})

    class FakeEngine:
        def __init__(self, env, **kw): pass
        def execute_query(self, q, **kw): return result_df
        def dispose(self): pass

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", FakeEngine)

    resp = client.post(f"/api/configs/{config_id}/preview-query", json={
        "query": "SELECT * FROM orders",
        "limit": 10,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["columns"] == ["id", "status"]
    assert data["rows"] == [[1, "pending"], [2, "shipped"]]


def test_preview_query_clamps_limit_at_200(client, config_id, monkeypatch):
    captured = {}

    class FakeEngine:
        def __init__(self, env, **kw): pass
        def execute_query(self, q, **kw):
            captured["q"] = q
            return pd.DataFrame({"id": [1]})
        def dispose(self): pass

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", FakeEngine)

    client.post(f"/api/configs/{config_id}/preview-query", json={
        "query": "SELECT * FROM t", "limit": 9999,
    })
    assert "TOP 200" in captured.get("q", "")


def test_preview_query_422_on_bad_sql(client, config_id, monkeypatch):
    class FailEngine:
        def __init__(self, env, **kw): pass
        def execute_query(self, q, **kw): raise ValueError("column 'x' not found")
        def dispose(self): pass

    monkeypatch.setattr("etl_framework.db.engine.DBEngine", FailEngine)

    resp = client.post(f"/api/configs/{config_id}/preview-query", json={
        "query": "SELECT x FROM orders", "limit": 10,
    })
    assert resp.status_code == 422
    assert "Query failed" in resp.json()["detail"]
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/unit/test_schema_endpoints.py::test_preview_query_returns_columns_and_rows tests/unit/test_schema_endpoints.py::test_preview_query_clamps_limit_at_200 tests/unit/test_schema_endpoints.py::test_preview_query_422_on_bad_sql -v
```

Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Implement the preview-query endpoint**

In `api/routes/configs.py`, add the request model and endpoint after the `get_db_schema` function (at the end of the file):

```python
class _PreviewRequest(BaseModel):
    query: str
    limit: int = 50


@router.post("/{config_id}/preview-query")
def preview_query(config_id: int, body: _PreviewRequest, db: Session = Depends(get_session)):
    """Execute a SQL query against the config's database and return the first N rows."""
    repo = ConfigRepository(db)
    cfg = repo.get(config_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="Config not found")

    limit = max(1, min(200, body.limit))
    safe_sql = f"SELECT TOP {limit} * FROM ({body.query}) AS _preview"

    default_keys = EnvironmentConfig(name="t", db_host="localhost", db_password="").model_dump(exclude={"name"})
    full_data = {**default_keys, **(cfg.config_json or {})}
    try:
        env = EnvironmentConfig(
            name=cfg.env_name or cfg.name,
            **{k: v for k, v in full_data.items() if k != "name"},
        )
        from etl_framework.db.engine import DBEngine
        engine = DBEngine(env)
        df = engine.execute_query(safe_sql)
        engine.dispose()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Query failed: {exc}")

    import json
    rows = json.loads(df.to_json(orient="values", date_format="iso"))
    return {"columns": list(df.columns), "rows": rows}
```

- [ ] **Step 4: Run all schema endpoint tests**

```
pytest tests/unit/test_schema_endpoints.py -v
```

Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add api/routes/configs.py tests/unit/test_schema_endpoints.py
git commit -m "feat(configs): add POST /{id}/preview-query endpoint for SQL query preview"
```

---

## Task 7: `GET /api/runs/{run_id}/mismatches/download` endpoint

**Files:**
- Modify: `api/routes/runs.py`
- Create: `tests/unit/test_mismatch_download.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_mismatch_download.py`:

```python
from __future__ import annotations
import csv, io
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base, get_db
from etl_framework.repository import database as _db_module
import etl_framework.repository.models  # noqa: F401
from etl_framework.repository.repository import RunRepository, TokenRepository
from api.main import app
from api.routes import runs as runs_module


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    def override_get_db():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(runs_module, "_execute_run", lambda *a, **kw: None)
    app.dependency_overrides[get_db] = override_get_db

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test")

    with TestClient(app, headers={"Authorization": f"Bearer {raw}"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def run_with_mismatches(client):
    """Create a run, add a test result, add mismatch details, return run_id."""
    from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
    from etl_framework.runner.state import TestStatus
    from datetime import datetime, timezone
    from sqlalchemy import create_engine as _ce
    from sqlalchemy.orm import Session as _S

    # Re-use the same in-memory engine via the override
    resp = client.post("/api/runs", json={
        "job_names": [],
        "run_label": "test",
        "source_env": "dev",
        "target_env": "prod",
    })
    assert resp.status_code in (200, 201, 202)
    run_id = resp.json()["run_id"]

    # Access DB directly through the app's dependency override
    with next(app.dependency_overrides[get_db]()) as db:
        repo = RunRepository(db)
        result = ReconciliationResult(
            query_name="orders_recon",
            source_env="dev", target_env="prod",
            source_row_count=100, target_row_count=95,
            matched_count=95, missing_in_target_count=5,
            missing_in_source_count=0, value_mismatch_count=0,
            mismatches=[], status=TestStatus.FAILED,
            executed_at=datetime.now(timezone.utc), duration_seconds=1.0,
        )
        tr = repo.add_test_result(run_id, result)
        repo.add_mismatch_details(tr.id, [
            MismatchRecord(
                key_values={"id": 42},
                column_name="amount",
                source_value="100",
                target_value="110",
                mismatch_type="value_diff",
            )
        ])
        db.commit()

    return run_id


def test_download_csv_returns_csv(client, run_with_mismatches):
    resp = client.get(f"/api/runs/{run_with_mismatches}/mismatches/download?format=csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) >= 1
    assert rows[0]["column_name"] == "amount"


def test_download_xlsx_returns_xlsx(client, run_with_mismatches):
    resp = client.get(f"/api/runs/{run_with_mismatches}/mismatches/download?format=xlsx")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["content-type"]
    import pandas as pd
    df = pd.read_excel(io.BytesIO(resp.content))
    assert "column_name" in df.columns


def test_download_unknown_run_returns_404(client):
    resp = client.get("/api/runs/no-such-run/mismatches/download?format=csv")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/unit/test_mismatch_download.py -v
```

Expected: FAIL on the first two (route 404), third may pass accidentally or fail differently.

- [ ] **Step 3: Implement the download endpoint**

In `api/routes/runs.py`, add the following after the `list_result_mismatches` function (currently around line 440):

```python
@router.get("/{run_id}/mismatches/download")
def download_mismatches(
    run_id: str,
    format: str = "csv",
    db: Session = Depends(get_session),
):
    """Download all mismatch details for a run as CSV, XLSX, or HTML report."""
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    if format == "html":
        service = ArtifactService(repository=repo)
        report_path = service.generate_html_report(run_id)
        return FileResponse(
            report_path,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="report_{run_id}.html"'},
        )

    rows = []
    for result in run.results:
        for m in repo.list_mismatches(result_id=result.id, limit=100_000):
            rows.append({
                "test_name": result.query_name,
                "key_values": json.dumps(m.key_values) if isinstance(m.key_values, dict) else str(m.key_values or ""),
                "column_name": m.column_name or "",
                "source_value": m.source_value or "",
                "target_value": m.target_value or "",
                "mismatch_type": m.mismatch_type or "",
            })

    _FIELDS = ["test_name", "key_values", "column_name", "source_value", "target_value", "mismatch_type"]

    if format == "xlsx":
        import pandas as pd
        buf = io.BytesIO()
        pd.DataFrame(rows, columns=_FIELDS).to_excel(buf, index=False)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="mismatches_{run_id}.xlsx"'},
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="mismatches_{run_id}.csv"'},
    )
```

Note: `io`, `csv`, `json`, `StreamingResponse`, `FileResponse`, and `ArtifactService` are all already imported in `api/routes/runs.py`.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_mismatch_download.py -v
```

Expected: all 3 PASS (the `run_with_mismatches` fixture may need adjustment if run creation endpoint differs — see note below).

> **Note:** If the `/api/runs` POST endpoint requires different fields, create the run using `RunRepository` directly in the fixture instead of calling the API. Check what the runs endpoint expects by running the test and reading the error.

- [ ] **Step 5: Commit**

```bash
git add api/routes/runs.py tests/unit/test_mismatch_download.py
git commit -m "feat(runs): add GET /{run_id}/mismatches/download endpoint for CSV/XLSX/HTML export"
```

---

## Task 8: Frontend — schema explorer state + methods (`app.js`)

**Files:**
- Modify: `frontend/app.js`

No automated JS tests. Manual verification in Step 4.

- [ ] **Step 1: Add schema explorer state to `app()`**

In `frontend/app.js`, find the block of `fileB64A`, `fileB64B`, etc. state declarations (around line 310–325). Add the following state block immediately after `fileCompareResult: null,`:

```js
    // Schema Explorer (Config tab)
    schemaExplorerId: null,
    schemaExplorerData: [],
    schemaExplorerLoading: false,
    schemaExpandedSchemas: {},
    schemaExpandedTables: {},
    schemaTablePreviews: {},
```

- [ ] **Step 2: Add schema explorer methods**

In `frontend/app.js`, find the `handleReconFileUpload` method (around line 1524). Add the following methods immediately before it:

```js
    async openSchemaExplorer(cfg) {
      if (this.schemaExplorerId === cfg.id) {
        this.closeSchemaExplorer();
        return;
      }
      this.schemaExplorerId = cfg.id;
      this.schemaExplorerData = [];
      this.schemaExpandedSchemas = {};
      this.schemaExpandedTables = {};
      this.schemaTablePreviews = {};
      this.schemaExplorerLoading = true;
      try {
        this.schemaExplorerData = await api('GET', `/api/configs/${cfg.id}/schema`);
        const schemas = [...new Set(this.schemaExplorerData.map(t => t.schema))];
        this.schemaExpandedSchemas = Object.fromEntries(schemas.map(s => [s, true]));
      } catch (e) {
        this.toast('error', 'Schema load failed', e.message);
        this.schemaExplorerId = null;
      } finally {
        this.schemaExplorerLoading = false;
      }
    },

    closeSchemaExplorer() {
      this.schemaExplorerId = null;
      this.schemaExplorerData = [];
      this.schemaTablePreviews = {};
    },

    toggleSchemaGroup(schema) {
      this.schemaExpandedSchemas[schema] = !this.schemaExpandedSchemas[schema];
    },

    toggleSchemaTable(key) {
      this.schemaExpandedTables[key] = !this.schemaExpandedTables[key];
    },

    async previewSchemaTable(configId, schema, table) {
      const key = `${schema}.${table}`;
      this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: 'loading' };
      try {
        const result = await api('POST', `/api/configs/${configId}/preview-query`, {
          query: `SELECT * FROM [${schema}].[${table}]`,
          limit: 50,
        });
        this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: result };
      } catch (e) {
        this.schemaTablePreviews = { ...this.schemaTablePreviews, [key]: `error:${e.message}` };
      }
    },

    useTableInJob(schema, table) {
      sessionStorage.setItem('etl_pending_query', `SELECT * FROM [${schema}].[${table}]`);
      this.activeTab = 'launch';
      this.$nextTick(() => this.openNewJobModal());
      this.toast('info', 'Query pre-filled', 'Finish the job setup');
    },

```

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add schema explorer state and methods to app.js"
```

- [ ] **Step 4: Manual smoke-check (defer until after Task 9 HTML is added)**

After Task 9 is complete, verify in browser: Click "Browse Schema" on a config card → spinner appears → schema tree renders → "▶ Preview" loads rows in a sub-grid → "Use in Job" pre-fills query and opens job modal.

---

## Task 9: Frontend — schema explorer HTML panel (`index.html`)

**Files:**
- Modify: `frontend/index.html:99-105` (config card buttons and explorer panel)

- [ ] **Step 1: Add "Browse Schema" button to each config card**

In `frontend/index.html`, find the config card buttons block (lines 99–102):

```html
        <div class="flex gap-2 flex-shrink-0">
          <button @click="editConfig(cfg)" class="btn-secondary btn-sm">Edit</button>
          <button @click="deleteConfig(cfg.id)" class="btn-danger btn-sm">Delete</button>
        </div>
```

Replace with:

```html
        <div class="flex gap-2 flex-shrink-0">
          <button @click="editConfig(cfg)" class="btn-secondary btn-sm">Edit</button>
          <button @click="deleteConfig(cfg.id)" class="btn-danger btn-sm">Delete</button>
          <button @click="openSchemaExplorer(cfg)" class="btn-secondary btn-sm"
                  :class="schemaExplorerId === cfg.id ? 'ring-2 ring-indigo-400' : ''">
            Browse Schema <span x-text="schemaExplorerId === cfg.id ? '▲' : '▼'"></span>
          </button>
        </div>
```

- [ ] **Step 2: Add the schema explorer panel after the config grid**

In `frontend/index.html`, find line 105 (`</template>`) which closes the config `x-for` loop, and line 106 (`</div>`) which closes the grid div. After line 106 (before `<!-- YAML Import card -->`), insert:

```html
  <!-- Schema Explorer Panel -->
  <template x-if="schemaExplorerId !== null">
    <div class="card mt-3">
      <div class="flex items-center justify-between mb-3">
        <div class="font-semibold text-slate-700">
          Schema Explorer —
          <span x-text="configs.find(c => c.id === schemaExplorerId)?.name ?? ''"></span>
        </div>
        <button @click="closeSchemaExplorer()" class="text-muted hover:text-slate-700 text-sm">✕ Close</button>
      </div>

      <div x-show="schemaExplorerLoading" class="text-muted text-sm">Loading schema…</div>

      <template x-if="!schemaExplorerLoading && schemaExplorerData.length === 0">
        <div class="text-muted text-sm">No tables found or connection failed.</div>
      </template>

      <template x-if="!schemaExplorerLoading && schemaExplorerData.length > 0">
        <div>
          <template x-for="schemaGroup in [...new Set(schemaExplorerData.map(t => t.schema))]" :key="schemaGroup">
            <div class="mb-2">
              <!-- Schema header -->
              <button @click="toggleSchemaGroup(schemaGroup)"
                      class="flex items-center gap-1 text-sm font-semibold text-slate-700 hover:text-indigo-600 w-full text-left">
                <span x-text="schemaExpandedSchemas[schemaGroup] ? '▼' : '▶'"></span>
                <span x-text="schemaGroup"></span>
                <span class="text-muted font-normal ml-1"
                      x-text="'(' + schemaExplorerData.filter(t => t.schema === schemaGroup).length + ' tables)'"></span>
              </button>

              <!-- Tables under this schema -->
              <template x-if="schemaExpandedSchemas[schemaGroup]">
                <div class="ml-4 mt-1 space-y-1">
                  <template x-for="tbl in schemaExplorerData.filter(t => t.schema === schemaGroup)" :key="tbl.schema + '.' + tbl.table">
                    <div>
                      <!-- Table row -->
                      <div class="flex items-center gap-2 py-0.5">
                        <button @click="toggleSchemaTable(tbl.schema + '.' + tbl.table)"
                                class="text-xs text-slate-600 hover:text-indigo-600 font-mono">
                          <span x-text="schemaExpandedTables[tbl.schema + '.' + tbl.table] ? '▼' : '▶'"></span>
                          <span x-text="tbl.table"></span>
                        </button>
                        <button @click="previewSchemaTable(schemaExplorerId, tbl.schema, tbl.table)"
                                class="btn-secondary btn-sm text-xs">▶ Preview</button>
                        <button @click="useTableInJob(tbl.schema, tbl.table)"
                                class="btn-secondary btn-sm text-xs">Use in Job</button>
                      </div>

                      <!-- Column list -->
                      <template x-if="schemaExpandedTables[tbl.schema + '.' + tbl.table]">
                        <div class="ml-6 text-xs text-muted font-mono">
                          <template x-for="col in tbl.columns" :key="col.name">
                            <span class="mr-3"><span x-text="col.name"></span> <span class="text-slate-400" x-text="'(' + col.type + ')'"></span></span>
                          </template>
                        </div>
                      </template>

                      <!-- Table preview grid -->
                      <template x-if="schemaTablePreviews[tbl.schema + '.' + tbl.table]">
                        <div class="mt-1 ml-4">
                          <template x-if="schemaTablePreviews[tbl.schema + '.' + tbl.table] === 'loading'">
                            <div class="text-xs text-muted">Loading preview…</div>
                          </template>
                          <template x-if="typeof schemaTablePreviews[tbl.schema + '.' + tbl.table] === 'string' && schemaTablePreviews[tbl.schema + '.' + tbl.table].startsWith('error:')">
                            <div class="text-xs text-red-500" x-text="schemaTablePreviews[tbl.schema + '.' + tbl.table].replace('error:', '')"></div>
                          </template>
                          <template x-if="schemaTablePreviews[tbl.schema + '.' + tbl.table] && typeof schemaTablePreviews[tbl.schema + '.' + tbl.table] === 'object'">
                            <div class="overflow-x-auto border rounded" style="max-height:200px;overflow-y:auto">
                              <table class="data-table text-xs">
                                <thead>
                                  <tr>
                                    <template x-for="col in schemaTablePreviews[tbl.schema + '.' + tbl.table].columns" :key="col">
                                      <th x-text="col"></th>
                                    </template>
                                  </tr>
                                </thead>
                                <tbody>
                                  <template x-for="(row, ri) in schemaTablePreviews[tbl.schema + '.' + tbl.table].rows" :key="ri">
                                    <tr>
                                      <template x-for="(cell, ci) in row" :key="ci">
                                        <td x-text="cell ?? '—'"></td>
                                      </template>
                                    </tr>
                                  </template>
                                </tbody>
                              </table>
                            </div>
                          </template>
                        </div>
                      </template>

                    </div>
                  </template>
                </div>
              </template>
            </div>
          </template>
        </div>
      </template>
    </div>
  </template>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add schema explorer panel to Config tab"
```

- [ ] **Step 3: Manual verification**

Start the server: `uvicorn api.main:app --reload`

1. Open the Config tab. Each card now has a "Browse Schema ▼" button.
2. Click "Browse Schema" on a config that connects to MSSQL → the panel expands below the card list, shows a spinner, then lists schemas and tables.
3. Click a schema name → tables collapse/expand.
4. Click a table name → columns expand inline.
5. Click "▶ Preview" → a mini grid of up to 50 rows appears under the table row.
6. Click "Use in Job" → toast "Query pre-filled", navigates to Launch tab, opens New Job modal with query pre-filled.
7. Click "✕ Close" → panel collapses.

---

## Task 10: Frontend — job form preview state + methods (`app.js`)

**Files:**
- Modify: `frontend/app.js:753-780` (openNewJobModal and openEditJobModal), plus new `previewJobQuery` method

- [ ] **Step 1: Add preview state to `openNewJobModal`**

In `frontend/app.js`, find `openNewJobModal()` (around line 753). The method assigns `this.jobModal = { ... }`. Add three fields to that object:

```js
      previewConfigId: String(this.launchSettings.config_id || ''),
      previewLoading: false,
      previewResult: null,
      previewError: '',
```

Also add, at the start of `openNewJobModal`, before `this.jobModal = {`:

```js
      const _pendingQuery = sessionStorage.getItem('etl_pending_query') || '';
      sessionStorage.removeItem('etl_pending_query');
```

And set `query: _pendingQuery,` in the `jobModal` object (replacing `query: '',`).

The modified start of `openNewJobModal` should look like:

```js
    openNewJobModal() {
      const _pendingQuery = sessionStorage.getItem('etl_pending_query') || '';
      sessionStorage.removeItem('etl_pending_query');
      this.jobModal = {
        name: '', description: '', job_type: 'reconciliation', query: _pendingQuery,
        key_columns_raw: 'id', tags_raw: '', enabled: true,
        // ... (all existing fields unchanged) ...
        previewConfigId: String(this.launchSettings.config_id || ''),
        previewLoading: false,
        previewResult: null,
        previewError: '',
      };
```

Also add the same four preview fields to `openEditJobModal`'s `this.jobModal = { ... }` assignment:

```js
        previewConfigId: String(job.config_id || this.launchSettings.config_id || ''),
        previewLoading: false,
        previewResult: null,
        previewError: '',
```

- [ ] **Step 2: Add `previewJobQuery` method**

Add this method to `frontend/app.js` (near other job modal methods, after `openEditJobModal`):

```js
    async previewJobQuery() {
      if (!this.jobModal.query || !this.jobModal.previewConfigId) return;
      this.jobModal.previewLoading = true;
      this.jobModal.previewResult = null;
      this.jobModal.previewError = '';
      try {
        const result = await api('POST', `/api/configs/${this.jobModal.previewConfigId}/preview-query`, {
          query: this.jobModal.query,
          limit: 50,
        });
        this.jobModal.previewResult = result;
      } catch (e) {
        this.jobModal.previewError = e.message;
      } finally {
        this.jobModal.previewLoading = false;
      }
    },
```

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git commit -m "feat(frontend): add query preview state and previewJobQuery method to job modal"
```

---

## Task 11: Frontend — job form preview HTML (`index.html`)

**Files:**
- Modify: `frontend/index.html:843-844` (end of the settings tab div)

- [ ] **Step 1: Add the preview bar to the settings tab**

In `frontend/index.html`, find line 843 (`</template>` closing the validate-query block) and line 844 (`</div>` closing the settings tab div). Insert the following between those two lines:

```html
        <!-- Query Preview Bar -->
        <template x-if="['reconciliation', 'freshness', 'profile', 'schema_snapshot'].includes(jobModal.job_type)">
          <div class="border-t border-slate-100 pt-3 mt-1 space-y-2">
            <div class="flex gap-2 items-center">
              <select x-model="jobModal.previewConfigId" class="field-input field-select flex-1 text-sm">
                <option value="">Preview against config…</option>
                <template x-for="c in configs" :key="c.id">
                  <option :value="String(c.id)" x-text="c.name"></option>
                </template>
              </select>
              <button @click="previewJobQuery()"
                      :disabled="!jobModal.query || !jobModal.previewConfigId || jobModal.previewLoading"
                      class="btn-secondary btn-sm flex-shrink-0">
                <span x-show="!jobModal.previewLoading">▶ Preview</span>
                <span x-show="jobModal.previewLoading">…</span>
              </button>
            </div>
            <p x-show="jobModal.previewError"
               class="text-xs text-red-500"
               x-text="jobModal.previewError"></p>
            <template x-if="jobModal.previewResult">
              <div>
                <div class="text-xs text-muted mb-1"
                     x-text="'Preview — ' + (jobModal.previewResult.rows?.length ?? 0) + ' rows'"></div>
                <div class="overflow-x-auto border rounded" style="max-height:200px;overflow-y:auto">
                  <table class="data-table text-xs">
                    <thead>
                      <tr>
                        <template x-for="col in jobModal.previewResult.columns" :key="col">
                          <th x-text="col"></th>
                        </template>
                      </tr>
                    </thead>
                    <tbody>
                      <template x-for="(row, ri) in jobModal.previewResult.rows" :key="ri">
                        <tr>
                          <template x-for="(cell, ci) in row" :key="ci">
                            <td x-text="cell ?? '—'"></td>
                          </template>
                        </tr>
                      </template>
                    </tbody>
                  </table>
                </div>
                <button @click="jobModal.previewResult = null"
                        class="text-xs text-muted mt-1 hover:text-slate-600">✕ Close preview</button>
              </div>
            </template>
          </div>
        </template>
```

- [ ] **Step 2: Commit**

```bash
git add frontend/index.html
git commit -m "feat(frontend): add query preview bar to job modal Settings tab"
```

- [ ] **Step 3: Manual verification**

1. Open the job modal (New Job or Edit Job).
2. Go to Settings tab.
3. A "Preview against config…" dropdown appears below the query textarea (for reconciliation, freshness, profile, schema_snapshot job types).
4. Enter a SQL query → select a config → click "▶ Preview" → a data grid renders with up to 50 rows.
5. A bad query shows a red inline error message.
6. "✕ Close preview" hides the grid.
7. If arriving from "Use in Job" in the schema explorer, the query textarea is pre-filled.

---

## Task 12: Frontend — compare tab multi-format upload + diff expand + download

**Files:**
- Modify: `frontend/app.js` (state, `handleReconFileUpload`, `runFileCompare`, 2 new methods)
- Modify: `frontend/index.html` (file inputs, result table rows, download bar)

- [ ] **Step 1: Add new state to `app()`**

In `frontend/app.js`, find the `fileB64A: '',` state declaration (around line 316). Add immediately after `fileB64B: '',`:

```js
    fileNameA: '',
    fileNameB: '',
    fileExpandedDiffs: {},
```

- [ ] **Step 2: Update `handleReconFileUpload` to be binary-safe and store filename**

In `frontend/app.js`, replace the entire `handleReconFileUpload` method (lines 1524–1538) with:

```js
    handleReconFileUpload(event, side) {
      const file = event.target.files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = (e) => {
        const bytes = new Uint8Array(e.target.result);
        let binary = '';
        for (let i = 0; i < bytes.length; i += 8192) {
          binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
        }
        const b64 = btoa(binary);
        if (side === 'a') {
          this.fileB64A = b64;
          this.fileNameA = file.name;
          this.fileSourceAType = 'upload';
        } else {
          this.fileB64B = b64;
          this.fileNameB = file.name;
          this.fileSourceBType = 'upload';
        }
      };
      reader.readAsArrayBuffer(file);
    },
```

- [ ] **Step 3: Update `runFileCompare` to pass filenames in payload**

In `frontend/app.js`, find the `runFileCompare` method (around line 1652). Find where `applySource` is called and the `payload` is built. After the two `applySource(...)` calls, add:

```js
        if (this.fileNameA) payload.file_a_name = this.fileNameA;
        if (this.fileNameB) payload.file_b_name = this.fileNameB;
```

Also reset `this.fileExpandedDiffs = {}` at the start of `runFileCompare` (after `this.fileCompareResult = null`):

```js
      this.fileCompareLoading = true;
      this.fileCompareResult = null;
      this.fileExpandedDiffs = {};
```

- [ ] **Step 4: Add `toggleFileDiff` and `downloadCompareResults` methods**

In `frontend/app.js`, add these two methods near `runFileCompare` (after it):

```js
    async toggleFileDiff(resultId) {
      if (this.fileExpandedDiffs[resultId] !== undefined) {
        const next = { ...this.fileExpandedDiffs };
        delete next[resultId];
        this.fileExpandedDiffs = next;
        return;
      }
      const runId = this.fileCompareResult?.run_id;
      if (!runId) return;
      try {
        const rows = await api('GET', `/api/runs/${runId}/results/${resultId}/mismatches?limit=500`);
        this.fileExpandedDiffs = { ...this.fileExpandedDiffs, [resultId]: rows };
      } catch (e) {
        this.fileExpandedDiffs = { ...this.fileExpandedDiffs, [resultId]: [] };
      }
    },

    async downloadCompareResults(runId, fmt) {
      try {
        const { blob, disposition } = await apiBlob(`/api/runs/${runId}/mismatches/download?format=${fmt}`);
        const ext = fmt === 'xlsx' ? 'xlsx' : fmt === 'html' ? 'html' : 'csv';
        const filename = disposition.match(/filename="?([^"]+)"?/)?.[1] || `mismatches_${runId}.${ext}`;
        triggerDownload(blob, filename);
      } catch (e) {
        this.toast('error', 'Download failed', e.message);
      }
    },
```

- [ ] **Step 5: Update HTML — file inputs, status labels, download bar, and diff expand rows**

**5a. Update file input `accept` attributes** (two places in `frontend/index.html`, around lines 2815 and 2845):

Replace both occurrences of:
```html
<input type="file" accept=".html,.htm" @change="handleReconFileUpload($event, 'a')" class="field-input" />
```
With (for side A):
```html
<input type="file" accept=".html,.htm,.csv,.xlsx,.xls,.json,.tsv"
       @change="handleReconFileUpload($event, 'a')" class="field-input" />
```

And for side B (line ~2845):
```html
<input type="file" accept=".html,.htm,.csv,.xlsx,.xls,.json,.tsv"
       @change="handleReconFileUpload($event, 'b')" class="field-input" />
```

**5b. Update the "HTML loaded" status labels** to show filename:

Replace:
```html
<div x-show="fileB64A" class="text-xs text-emerald-500 mt-1">HTML loaded</div>
```
With:
```html
<div x-show="fileB64A" class="text-xs text-emerald-500 mt-1" x-text="fileNameA ? fileNameA + ' loaded' : 'File loaded'"></div>
```

And the side B version:
```html
<div x-show="fileB64B" class="text-xs text-emerald-500 mt-1" x-text="fileNameB ? fileNameB + ' loaded' : 'File loaded'"></div>
```

**5c. Add download bar and update the result table** in the `<template x-if="fileCompareResult">` block (currently lines 2857–2883):

Replace the existing block:
```html
      <template x-if="fileCompareResult">
        <div class="card">
          <div class="flex items-center justify-between mb-3">
            <div class="font-semibold text-slate-700">File Comparison Results</div>
            <button @click="reportRunId = fileCompareResult.run_id; reportLoaded = false; currentView = 'reports'" class="btn-secondary btn-sm">Open in Reports →</button>
          </div>
          <div class="flex gap-2 flex-wrap mb-3">
            <span class="badge" :class="statusBadgeClass(fileCompareResult.status)" x-text="fileCompareResult.status"></span>
            <span class="compare-chip chip-improved" x-text="(fileCompareResult.passed || 0) + ' matched'"></span>
            <span class="compare-chip chip-regressed" x-text="(fileCompareResult.failed || 0) + ' differ'"></span>
          </div>
          <template x-if="fileCompareResult.results && fileCompareResult.results.length">
            <table class="data-table">
              <thead><tr><th>Test</th><th>Rows A</th><th>Rows B</th><th>Match</th></tr></thead>
              <tbody>
                <template x-for="r in fileCompareResult.results" :key="r.id">
                  <tr>
                    <td class="font-mono text-xs" x-text="r.query_name"></td>
                    <td x-text="r.source_row_count ?? '—'"></td>
                    <td x-text="r.target_row_count ?? '—'"></td>
                    <td><span class="badge" :class="statusBadgeClass(r.status)" x-text="r.status === 'PASSED' ? 'Matched' : 'Differs'"></span></td>
                  </tr>
                </template>
              </tbody>
            </table>
          </template>
        </div>
      </template>
```

With:
```html
      <template x-if="fileCompareResult">
        <div class="card">
          <div class="flex items-center justify-between mb-3">
            <div class="font-semibold text-slate-700">File Comparison Results</div>
            <div class="flex gap-2 items-center flex-wrap">
              <!-- Download bar -->
              <div class="flex gap-1 items-center border rounded px-2 py-1 text-sm">
                <span class="text-muted text-xs mr-1">↓ Download</span>
                <button @click="downloadCompareResults(fileCompareResult.run_id, 'csv')"
                        class="text-indigo-600 hover:underline text-xs">CSV</button>
                <span class="text-muted">|</span>
                <button @click="downloadCompareResults(fileCompareResult.run_id, 'xlsx')"
                        class="text-indigo-600 hover:underline text-xs">Excel</button>
                <span class="text-muted">|</span>
                <button @click="downloadCompareResults(fileCompareResult.run_id, 'html')"
                        class="text-indigo-600 hover:underline text-xs">HTML</button>
              </div>
              <button @click="reportRunId = fileCompareResult.run_id; reportLoaded = false; currentView = 'reports'"
                      class="btn-secondary btn-sm">Open in Reports →</button>
            </div>
          </div>
          <div class="flex gap-2 flex-wrap mb-3">
            <span class="badge" :class="statusBadgeClass(fileCompareResult.status)" x-text="fileCompareResult.status"></span>
            <span class="compare-chip chip-improved" x-text="(fileCompareResult.passed || 0) + ' matched'"></span>
            <span class="compare-chip chip-regressed" x-text="(fileCompareResult.failed || 0) + ' differ'"></span>
          </div>
          <template x-if="fileCompareResult.results && fileCompareResult.results.length">
            <table class="data-table">
              <thead><tr><th>Test</th><th>Rows A</th><th>Rows B</th><th>Match</th></tr></thead>
              <tbody>
                <template x-for="r in fileCompareResult.results" :key="r.id">
                  <template>
                    <!-- Main result row -->
                    <tr>
                      <td class="font-mono text-xs">
                        <button x-show="r.status !== 'PASSED'"
                                @click="toggleFileDiff(r.id)"
                                class="mr-1 text-slate-400 hover:text-indigo-600 text-xs">
                          <span x-text="fileExpandedDiffs[r.id] !== undefined ? '▼' : '▶'"></span>
                        </button>
                        <span x-text="r.query_name"></span>
                      </td>
                      <td x-text="r.source_row_count ?? '—'"></td>
                      <td x-text="r.target_row_count ?? '—'"></td>
                      <td>
                        <span class="badge" :class="statusBadgeClass(r.status)"
                              x-text="r.status === 'PASSED' ? 'Matched' : 'Differs'"></span>
                      </td>
                    </tr>
                    <!-- Diff detail rows (expanded) -->
                    <template x-if="fileExpandedDiffs[r.id] !== undefined">
                      <tr>
                        <td colspan="4" class="bg-slate-50 p-0">
                          <template x-if="!Array.isArray(fileExpandedDiffs[r.id])">
                            <div class="text-xs text-muted p-2">Loading…</div>
                          </template>
                          <template x-if="Array.isArray(fileExpandedDiffs[r.id]) && fileExpandedDiffs[r.id].length === 0">
                            <div class="text-xs text-muted p-2">No detail records found.</div>
                          </template>
                          <template x-if="Array.isArray(fileExpandedDiffs[r.id]) && fileExpandedDiffs[r.id].length > 0">
                            <table class="data-table text-xs w-full">
                              <thead>
                                <tr class="bg-slate-100">
                                  <th>Field</th><th>Value A</th><th>Value B</th>
                                </tr>
                              </thead>
                              <tbody>
                                <template x-for="diff in fileExpandedDiffs[r.id]" :key="diff.id">
                                  <tr>
                                    <td class="font-mono" x-text="diff.column_name"></td>
                                    <td x-text="diff.source_value ?? '—'"></td>
                                    <td x-text="diff.target_value ?? '—'"></td>
                                  </tr>
                                </template>
                              </tbody>
                            </table>
                          </template>
                        </td>
                      </tr>
                    </template>
                  </template>
                </template>
              </tbody>
            </table>
          </template>
        </div>
      </template>
```

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(frontend): multi-format file upload, diff expand, and download bar in Compare tab"
```

- [ ] **Step 7: Manual verification**

1. Open Compare tab → Recon-file section.
2. Upload a `.csv` file → filename appears ("data.csv loaded").
3. Upload a `.json` or `.tsv` file → filename appears.
4. Run the compare → results table appears.
5. Download bar shows CSV | Excel | HTML links. Clicking CSV triggers browser download.
6. A "Differs" row has a "▶" toggle. Clicking it expands a sub-table showing field, value A, value B.
7. Clicking the "▶" again collapses it.
8. "Matched" rows have no toggle.

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task(s) |
|-----------------|---------|
| Query preview returns multiple rows, not just 1 | Task 5 (GET schema), Task 6 (POST preview-query), Tasks 10–11 (job form UI) |
| Schema/table explorer in Config tab | Tasks 5, 8, 9 |
| Compare results show diff details | Task 3 (store mismatches), Task 12 (expand rows) |
| Save differences as CSV/Excel/HTML | Tasks 7, 12 (download bar) |
| Recon-file compare accepts CSV, Excel, JSON, TSV | Tasks 1, 2, 4, 12 (file inputs) |

**Placeholder scan:** None found — all steps include complete code.

**Type consistency check:**
- `schemaTablePreviews[key]` type is `'loading' | string (error:...) | {columns: string[], rows: any[][]}` — handled with three separate `x-if` checks in Task 9.
- `fileExpandedDiffs[resultId]` is `undefined` (closed) or `MismatchOut[]` (open, possibly empty) — handled with `!== undefined` check and `Array.isArray` guards.
- `MismatchRecord` fields used in Task 3 match the dataclass definition: `key_values: dict`, `column_name: str`, `source_value: Any`, `target_value: Any`, `mismatch_type: str` — all present.
- `add_mismatch_details(tr.id, _mm)` — `tr.id` is `int` (from `add_test_result` return), matching `add_mismatch_details(test_result_id: int, ...)`.
- `_run_tabular_file_compare` uses `_FrameEngine`, `ReconciliationEngine`, `MetricsWriter`, `TestStatus` — all already imported at module level in `compare_service.py`.
- `req.exclude_columns` — added to `ReconFileCompareRequest` in Task 2 with `default_factory=list`. In `_run_tabular_file_compare`, used as `req.exclude_columns or []` (safe even if empty list).

**Note on Task 7 test fixture:** The `run_with_mismatches` fixture calls `client.post("/api/runs", ...)`. If this endpoint requires specific fields not present in the fixture, replace the API call with a direct `RunRepository.create_run(...)` call. Check the actual run-creation endpoint signature in `api/routes/runs.py` before running.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-30-schema-explorer-preview-compare.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review output between tasks, iterate fast. Each task runs in isolation with full context. Use `superpowers:subagent-driven-development`.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, with checkpoints for review after each task.

**Which approach?**
