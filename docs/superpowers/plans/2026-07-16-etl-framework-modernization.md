# ETL Framework Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close five enterprise-grade gaps: rules-as-code expectation suites with schema-compat classification, an isolated DuckDB transform-testing harness, a Write-Audit-Publish gate endpoint, a secret-provider abstraction with config overlays, and CI gate exit codes with a shadow run profile.

**Architecture:** Five independent phases, each shippable alone, ordered by priority. New pure-logic modules live in `etl_framework/` (no `api` imports); API-layer glue (routes, services needing `api.schemas`) lives in `api/`. Every phase follows existing patterns: Pydantic models, `JobRepository`/SQLAlchemy persistence, FastAPI routers registered in `api/main.py`, pytest unit tests in `tests/unit/`.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, SQLAlchemy 2, DuckDB, PyYAML, pytest.

**Verified anchors (do not guess — these exist today):**
- `api/schemas.py:103` `DQRule`, `:67` `RunSettings`, `:413` `JobDefinition`
- `etl_framework/repository/repository.py` `JobRepository` with `get/create/update/upsert/list`
- `etl_framework/repository/models.py:83` `TestRun`, `:134` `TestResult` (`query_name`, `status`)
- `etl_framework/repository/contract_models.py` `Contract` (`source_job`, `active`), `ContractBreach` (`resolved_at`)
- `etl_framework/repository/database.py` `SessionLocal`, `init_db()`, `get_db()`
- `api/services/schema_snapshot_service.py` `capture_schema`, `diff_schemas` → `{added, removed, changed}`
- `etl_framework/reconciliation/backends/duckdb_backend.py:18` `DuckDBBackend(key_columns, float_tolerance=1e-9, null_equals_null=True, mismatch_row_limit=1000, ...)`
- `etl_framework/reconciliation/backends/sampling_backend.py:8` `SamplingBackend(inner, sample_frac=0.1, seed=42)`
- `api/services/run_executor.py:1101` `RunExecutor._build_backend`
- `etl_framework/config/loader.py` `ConfigLoader.load` + `_resolve_env_vars`
- `api/services/secret_store.py` `encrypt_secret`/`decrypt_secret`
- `etl_framework/runner/cli.py` argparse CLI returning int exit code

Run the full suite before starting: `python -m pytest tests/unit -q` — record the baseline pass count.

---

## Phase 1 — Rules-as-Code Expectation Suites + Schema-Compat Classifier

### Task 1: ExpectationSuite YAML model

**Files:**
- Create: `etl_framework/expectations/__init__.py`
- Create: `etl_framework/expectations/suite.py`
- Test: `tests/unit/test_expectation_suite.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_expectation_suite.py
from pathlib import Path

import pytest

from etl_framework.expectations.suite import ExpectationSuite, load_suite, dump_suite


def test_suite_roundtrip(tmp_path: Path) -> None:
    suite = ExpectationSuite(
        job="orders_reconciliation",
        rules=[
            {"type": "not_null", "column": "id", "severity": "error"},
            {"type": "row_count_min", "min_value": 1},
        ],
    )
    path = tmp_path / "orders_reconciliation.yml"
    dump_suite(suite, path)
    loaded = load_suite(path)
    assert loaded == suite


def test_load_suite_rejects_missing_job(tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("rules: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="job"):
        load_suite(path)


def test_load_suite_rejects_non_mapping(tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_suite(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_expectation_suite.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'etl_framework.expectations'`

- [ ] **Step 3: Write minimal implementation**

```python
# etl_framework/expectations/__init__.py
```
(empty file)

```python
# etl_framework/expectations/suite.py
"""Versioned expectation suites: DQ rules as YAML files under source control.

A suite file maps 1:1 to a job. Format:

    job: orders_reconciliation
    rules:
      - type: not_null
        column: id
        severity: error

Rule dicts are validated against ``api.schemas.DQRule`` at sync time (API
layer) — this module stays free of ``api`` imports so it can be used from
scripts and CI without the web app.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ExpectationSuite(BaseModel):
    job: str = Field(min_length=1)
    rules: list[dict[str, Any]] = Field(default_factory=list)


def load_suite(path: str | Path) -> ExpectationSuite:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: suite file must be a YAML mapping")
    if not raw.get("job"):
        raise ValueError(f"{path}: suite file must set 'job'")
    return ExpectationSuite.model_validate(raw)


def dump_suite(suite: ExpectationSuite, path: str | Path) -> None:
    Path(path).write_text(
        yaml.safe_dump(suite.model_dump(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def load_suites(directory: str | Path) -> list[ExpectationSuite]:
    """Load every ``*.yml``/``*.yaml`` file in *directory*, sorted by filename."""
    dir_path = Path(directory)
    suites = []
    for path in sorted(list(dir_path.glob("*.yml")) + list(dir_path.glob("*.yaml"))):
        suites.append(load_suite(path))
    return suites
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_expectation_suite.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add etl_framework/expectations tests/unit/test_expectation_suite.py
git commit -m "feat: add ExpectationSuite YAML model for rules-as-code"
```

### Task 2: Suite sync service (YAML → job rules in DB)

**Files:**
- Create: `api/services/expectations_service.py`
- Test: `tests/unit/test_expectations_sync.py`

Semantics: declarative — the suite **replaces** the job's `rules` list. Jobs without a suite file are untouched. Suite naming a nonexistent job is reported, not an error. Invalid rules fail that one suite with a per-suite error entry.

- [ ] **Step 1: Write the failing test**

Look at `tests/unit/test_job_selections_repository.py` for the existing in-memory SQLite session fixture pattern and reuse it. The test below assumes a `db` fixture yielding a SQLAlchemy `Session` bound to a fresh in-memory DB with `Base.metadata.create_all` — copy the fixture from that file if no shared conftest fixture exists.

```python
# tests/unit/test_expectations_sync.py
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.expectations_service import export_suites, sync_suites
from etl_framework.expectations.suite import ExpectationSuite, dump_suite, load_suite
from etl_framework.repository.database import Base
from etl_framework.repository.repository import JobRepository


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_job(db, name: str = "orders_reconciliation"):
    return JobRepository(db).create({
        "name": name,
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "params": {"key_columns": ["id"], "rules": [{"type": "unique", "column": "id"}]},
        "enabled": True,
    })


def test_sync_replaces_job_rules(db, tmp_path: Path) -> None:
    _seed_job(db)
    dump_suite(
        ExpectationSuite(job="orders_reconciliation",
                         rules=[{"type": "not_null", "column": "id", "severity": "error"}]),
        tmp_path / "orders_reconciliation.yml",
    )
    report = sync_suites(tmp_path, db)
    assert report.synced == ["orders_reconciliation"]
    assert report.errors == []
    job = JobRepository(db).get("orders_reconciliation")
    assert job.params["rules"] == [
        {"type": "not_null", "column": "id", "severity": "error"}
    ]


def test_sync_reports_missing_job(db, tmp_path: Path) -> None:
    dump_suite(ExpectationSuite(job="ghost_job", rules=[]), tmp_path / "ghost_job.yml")
    report = sync_suites(tmp_path, db)
    assert report.missing_jobs == ["ghost_job"]
    assert report.synced == []


def test_sync_rejects_invalid_rule(db, tmp_path: Path) -> None:
    _seed_job(db)
    dump_suite(
        ExpectationSuite(job="orders_reconciliation",
                         rules=[{"type": "not_a_rule_type"}]),
        tmp_path / "orders_reconciliation.yml",
    )
    report = sync_suites(tmp_path, db)
    assert report.synced == []
    assert len(report.errors) == 1
    assert "orders_reconciliation" in report.errors[0]
    # job rules untouched
    job = JobRepository(db).get("orders_reconciliation")
    assert job.params["rules"] == [{"type": "unique", "column": "id"}]


def test_export_writes_suite_per_job_with_rules(db, tmp_path: Path) -> None:
    _seed_job(db)
    written = export_suites(tmp_path, db)
    assert written == ["orders_reconciliation"]
    suite = load_suite(tmp_path / "orders_reconciliation.yml")
    assert suite.rules == [{"type": "unique", "column": "id"}]
```

NOTE: `JobRepository.create(dict)` is confirmed — `tests/unit/test_run_executor.py:145` calls it with a plain dict. `update(name, data)` is confirmed from `api/routes/jobs.py:177`. If `create` complains about missing keys, copy the fuller dict shape from `test_run_executor.py:145-162` (adds `description/tags/exclude_columns/source_env/target_env`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_expectations_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'api.services.expectations_service'`

- [ ] **Step 3: Write minimal implementation**

```python
# api/services/expectations_service.py
"""Sync versioned expectation-suite YAML files with job DQ rules.

Direction of truth: YAML → DB on sync (declarative replace); DB → YAML on
export. Validation runs through ``api.schemas.DQRule`` so a suite can never
install a rule the engine doesn't understand.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from api.schemas import DQRule
from etl_framework.expectations.suite import ExpectationSuite, dump_suite, load_suites
from etl_framework.repository.repository import JobRepository


class SyncReport(BaseModel):
    synced: list[str] = Field(default_factory=list)
    missing_jobs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


def sync_suites(directory: str | Path, db: Session) -> SyncReport:
    report = SyncReport()
    repo = JobRepository(db)
    for suite in load_suites(directory):
        job = repo.get(suite.job)
        if job is None:
            report.missing_jobs.append(suite.job)
            continue
        try:
            validated = [DQRule.model_validate(rule) for rule in suite.rules]
        except ValidationError as exc:
            report.errors.append(f"{suite.job}: {exc.errors()[0]['msg']}")
            continue
        params = dict(job.params or {})
        params["rules"] = [
            rule.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
            | {"type": rule.type, "severity": rule.severity}
            for rule in validated
        ]
        repo.update(suite.job, {"params": params})
        report.synced.append(suite.job)
    return report


def export_suites(directory: str | Path, db: Session) -> list[str]:
    """Write one suite YAML per job that has rules. Returns job names written."""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for job in JobRepository(db).list():
        rules = (job.params or {}).get("rules") or []
        if not rules:
            continue
        dump_suite(ExpectationSuite(job=job.name, rules=rules), out_dir / f"{job.name}.yml")
        written.append(job.name)
    return written
```

If the first sync test fails on rule shape (extra defaulted keys), simplify the params line to `params["rules"] = suite.rules` after validation — validation is the gate; storage stays byte-identical to the YAML. Prefer that simpler form if it passes.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_expectations_sync.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add api/services/expectations_service.py tests/unit/test_expectations_sync.py
git commit -m "feat: sync/export expectation suites against job DQ rules"
```

### Task 3: Expectations API routes

**Files:**
- Create: `api/routes/expectations.py`
- Modify: `api/main.py` (router registration)
- Test: `tests/unit/test_expectations_routes.py`

- [ ] **Step 1: Write the failing test**

Copy the FastAPI TestClient + dependency-override fixture pattern from `tests/unit/test_coverage_routes.py` (or `tests/unit/test_selections_routes.py`) — reuse whichever fixture creates an app with an in-memory DB and auth bypass; do not invent a new pattern.

```python
# tests/unit/test_expectations_routes.py  (fixture imports per existing route tests)
def test_sync_endpoint_returns_report(client, db, tmp_path):
    # seed one job, one suite file
    from etl_framework.expectations.suite import ExpectationSuite, dump_suite
    from etl_framework.repository.repository import JobRepository
    JobRepository(db).create({
        "name": "orders_reconciliation", "job_type": "reconciliation",
        "query": "SELECT 1", "params": {}, "enabled": True,
    })
    dump_suite(ExpectationSuite(job="orders_reconciliation",
                                rules=[{"type": "not_null", "column": "id"}]),
               tmp_path / "orders_reconciliation.yml")
    resp = client.post("/api/expectations/sync", json={"directory": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["synced"] == ["orders_reconciliation"]


def test_sync_rejects_missing_directory(client):
    resp = client.post("/api/expectations/sync", json={"directory": "Z:/does/not/exist"})
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_expectations_routes.py -v`
Expected: FAIL (404 — route not registered)

- [ ] **Step 3: Write minimal implementation**

```python
# api/routes/expectations.py
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.services.audit_service import AuditService
from api.services.expectations_service import SyncReport, export_suites, sync_suites
from etl_framework.repository.database import get_db as get_session

router = APIRouter(prefix="/api/expectations", tags=["expectations"])


class SyncRequest(BaseModel):
    directory: str = "expectations"


@router.post("/sync", response_model=SyncReport)
def sync_expectations(body: SyncRequest, request: Request, db: Session = Depends(get_session)):
    directory = Path(body.directory)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {body.directory}")
    report = sync_suites(directory, db)
    AuditService(db).log(
        request, "expectations.synced", "expectations", body.directory,
        report.model_dump(),
    )
    return report


@router.post("/export")
def export_expectations(body: SyncRequest, db: Session = Depends(get_session)):
    written = export_suites(Path(body.directory), db)
    return {"written": written}
```

Before writing: check how existing routes import the session dependency (`api/routes/jobs.py` uses `db: Session = Depends(get_session)`) and how `AuditService` is imported there; mirror those imports exactly. Register in `api/main.py` next to the other `include_router` calls:

```python
from api.routes import expectations
app.include_router(expectations.router)
```

(Match the exact import/include style used by neighbours in `api/main.py` — some apps import routers as `from api.routes.jobs import router as jobs_router`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_expectations_routes.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Run the full unit suite (route registration can break app startup)**

Run: `python -m pytest tests/unit -q`
Expected: baseline count + 9 new, 0 failures

- [ ] **Step 6: Commit**

```bash
git add api/routes/expectations.py api/main.py tests/unit/test_expectations_routes.py
git commit -m "feat: expectations sync/export API routes"
```

### Task 4: Schema-drift compatibility classifier

**Files:**
- Create: `etl_framework/expectations/schema_compat.py`
- Test: `tests/unit/test_schema_compat.py`

Classification levels (worst wins for the overall verdict):
- `non_breaking`: column added; numeric widening (`int32→int64`, `int64→float64`, `float32→float64`)
- `risky`: dtype change to/from `object`, datetime unit/tz changes, anything not clearly widening or narrowing
- `breaking`: column removed; numeric narrowing (`int64→int32`, `float64→int64`)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_schema_compat.py
from etl_framework.expectations.schema_compat import classify_diff, classify_type_change


def test_widening_is_non_breaking():
    assert classify_type_change("int32", "int64") == "non_breaking"
    assert classify_type_change("float32", "float64") == "non_breaking"
    assert classify_type_change("int64", "float64") == "non_breaking"


def test_narrowing_is_breaking():
    assert classify_type_change("int64", "int32") == "breaking"
    assert classify_type_change("float64", "int64") == "breaking"


def test_object_transitions_are_risky():
    assert classify_type_change("object", "int64") == "risky"
    assert classify_type_change("int64", "object") == "risky"


def test_classify_diff_overall_is_worst_change():
    diff = {
        "added": ["new_col"],
        "removed": [],
        "changed": [{"column": "amount", "from": "int32", "to": "int64"}],
    }
    result = classify_diff(diff)
    assert result["compatibility"] == "non_breaking"
    assert result["changed"][0]["compatibility"] == "non_breaking"

    diff["removed"] = ["gone_col"]
    assert classify_diff(diff)["compatibility"] == "breaking"


def test_classify_diff_no_changes_is_full():
    assert classify_diff({"added": [], "removed": [], "changed": []})["compatibility"] == "full"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_schema_compat.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write minimal implementation**

```python
# etl_framework/expectations/schema_compat.py
"""Classify schema-snapshot diffs by consumer compatibility.

Levels, worst wins: full < non_breaking < risky < breaking.
Widening numeric changes keep every representable value, so consumers keep
working; narrowing can silently truncate — that's breaking.
"""
from __future__ import annotations

from typing import Any

_SEVERITY_ORDER = ["full", "non_breaking", "risky", "breaking"]

# Partial order of "safely widenable" numeric dtypes (pandas dtype strings).
_NUMERIC_WIDTH = {
    "int8": 0, "int16": 1, "int32": 2, "int64": 3,
    "uint8": 0, "uint16": 1, "uint32": 2, "uint64": 3,
    "float32": 4, "float64": 5,
}


def classify_type_change(from_dtype: str, to_dtype: str) -> str:
    old = _NUMERIC_WIDTH.get(from_dtype.lower())
    new = _NUMERIC_WIDTH.get(to_dtype.lower())
    if old is not None and new is not None:
        return "non_breaking" if new >= old else "breaking"
    return "risky"


def classify_diff(diff: dict[str, Any]) -> dict[str, Any]:
    """Return *diff* with per-change and overall ``compatibility`` keys added."""
    result = dict(diff)
    levels = ["full"]
    if diff.get("added"):
        levels.append("non_breaking")
    if diff.get("removed"):
        levels.append("breaking")
    changed = []
    for change in diff.get("changed") or []:
        level = classify_type_change(change["from"], change["to"])
        changed.append({**change, "compatibility": level})
        levels.append(level)
    result["changed"] = changed
    result["compatibility"] = max(levels, key=_SEVERITY_ORDER.index)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_schema_compat.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add etl_framework/expectations/schema_compat.py tests/unit/test_schema_compat.py
git commit -m "feat: schema-drift compatibility classifier"
```

### Task 5: Wire classifier into schema snapshot diffs

**Files:**
- Modify: `api/services/schema_snapshot_service.py`
- Test: extend `tests/unit/test_schema_snapshot_job.py`

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_schema_snapshot_job.py`)

```python
def test_diff_schemas_includes_compatibility():
    from api.services.schema_snapshot_service import diff_schemas
    current = [{"name": "id", "dtype": "int64"}]
    previous = [{"name": "id", "dtype": "int32"}, {"name": "old", "dtype": "object"}]
    diff = diff_schemas(current, previous)
    assert diff["compatibility"] == "breaking"          # column removed
    assert diff["changed"][0]["compatibility"] == "non_breaking"  # int32 -> int64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_schema_snapshot_job.py -v -k compatibility`
Expected: FAIL with KeyError `'compatibility'`

- [ ] **Step 3: Modify `diff_schemas`** — change the return line in `api/services/schema_snapshot_service.py`:

```python
from etl_framework.expectations.schema_compat import classify_diff

# ... existing capture_schema unchanged; in diff_schemas, replace:
#     return {"added": added, "removed": removed, "changed": changed}
# with:
    return classify_diff({"added": added, "removed": removed, "changed": changed})
```

- [ ] **Step 4: Run the snapshot tests and full suite** (additive key — existing asserts on added/removed/changed must keep passing)

Run: `python -m pytest tests/unit/test_schema_snapshot_job.py tests/unit -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/schema_snapshot_service.py tests/unit/test_schema_snapshot_job.py
git commit -m "feat: schema snapshot diffs carry compatibility classification"
```

### Task 6: Seed `expectations/` directory + docs

**Files:**
- Create: `expectations/README.md`
- Modify: `README.md` (Capabilities list)

- [ ] **Step 1: Create `expectations/README.md`**

```markdown
# Expectation Suites (Rules-as-Code)

One YAML file per job. The file's `rules` list **replaces** the job's DQ
rules on sync — this directory is the source of truth once you adopt it.

    job: orders_reconciliation
    rules:
      - type: not_null
        column: id
        severity: error

Sync:   `POST /api/expectations/sync   {"directory": "expectations"}`
Export: `POST /api/expectations/export {"directory": "expectations"}`

Rule types and fields: see `DQRule` in `api/schemas.py` or the Jobs UI.
```

- [ ] **Step 2: Add one bullet to README.md Capabilities section**

```markdown
- **Rules-as-code** — export job DQ rules to versioned YAML suites in `expectations/`, review them in PRs, and sync them back with `POST /api/expectations/sync`. Schema snapshot diffs now include a `compatibility` verdict (`full` / `non_breaking` / `risky` / `breaking`).
```

- [ ] **Step 3: Commit**

```bash
git add expectations/README.md README.md
git commit -m "docs: expectations directory and rules-as-code capability"
```

---

## Phase 2 — TransformCase DuckDB Harness

### Task 7: TransformCase harness

**Files:**
- Create: `etl_framework/transform_testing/__init__.py`
- Create: `etl_framework/transform_testing/harness.py`
- Test: `tests/unit/test_transform_case.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_transform_case.py
import pandas as pd
import pytest

from etl_framework.transform_testing.harness import TransformCase


def test_passing_transform_returns_no_mismatches():
    case = TransformCase(
        transform_sql="SELECT id, amount * 2 AS doubled FROM orders",
        inputs={"orders": pd.DataFrame({"id": [1, 2], "amount": [10.0, 20.0]})},
        expected=pd.DataFrame({"id": [1, 2], "doubled": [20.0, 40.0]}),
        key_columns=["id"],
    )
    assert case.run() == []


def test_wrong_output_reports_value_mismatch():
    case = TransformCase(
        transform_sql="SELECT id, amount FROM orders",
        inputs={"orders": pd.DataFrame({"id": [1], "amount": [10.0]})},
        expected=pd.DataFrame({"id": [1], "amount": [99.0]}),
        key_columns=["id"],
    )
    mismatches = case.run()
    assert len(mismatches) == 1
    assert mismatches[0].column_name == "amount"
    assert mismatches[0].mismatch_type == "value_diff"


def test_multiple_input_tables_joinable():
    case = TransformCase(
        transform_sql="""
            SELECT o.id, c.region
            FROM orders o JOIN customers c ON o.customer_id = c.id
        """,
        inputs={
            "orders": pd.DataFrame({"id": [1], "customer_id": [7]}),
            "customers": pd.DataFrame({"id": [7], "region": ["EU"]}),
        },
        expected=pd.DataFrame({"id": [1], "region": ["EU"]}),
        key_columns=["id"],
    )
    assert case.run() == []


def test_bad_sql_raises_with_context():
    case = TransformCase(
        transform_sql="SELECT nope FROM missing_table",
        inputs={},
        expected=pd.DataFrame(),
        key_columns=[],
    )
    with pytest.raises(Exception):
        case.run()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_transform_case.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# etl_framework/transform_testing/__init__.py
from etl_framework.transform_testing.harness import TransformCase

__all__ = ["TransformCase"]
```

```python
# etl_framework/transform_testing/harness.py
"""Isolated transform testing: run a transform SQL against in-memory DuckDB
fixture tables and reconcile output with the production comparison backend."""
from __future__ import annotations

from dataclasses import dataclass, field

import duckdb
import pandas as pd

from etl_framework.reconciliation.backends.duckdb_backend import DuckDBBackend
from etl_framework.reconciliation.models import MismatchRecord


@dataclass
class TransformCase:
    transform_sql: str
    inputs: dict[str, pd.DataFrame]
    expected: pd.DataFrame
    key_columns: list[str] = field(default_factory=list)
    float_tolerance: float = 1e-9

    def execute(self) -> pd.DataFrame:
        """Run the transform against fixture tables; return the output frame."""
        con = duckdb.connect(":memory:")
        try:
            for table_name, frame in self.inputs.items():
                con.register(f"_src_{table_name}", frame)
                con.execute(f'CREATE TABLE "{table_name}" AS SELECT * FROM _src_{table_name}')
            return con.execute(self.transform_sql).df()
        finally:
            con.close()

    def run(self) -> list[MismatchRecord]:
        """Execute the transform and compare its output against ``expected``."""
        actual = self.execute()
        backend = DuckDBBackend(
            key_columns=self.key_columns,
            float_tolerance=self.float_tolerance,
        )
        return backend.compare(actual, self.expected)
```

If `DuckDBBackend.compare` requires non-empty `key_columns` (check its `compare` body when the empty-key test errors unexpectedly), keep the harness as-is — the `test_bad_sql_raises_with_context` test only asserts an exception, and real cases always set keys.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_transform_case.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add etl_framework/transform_testing tests/unit/test_transform_case.py
git commit -m "feat: TransformCase harness for isolated transform testing"
```

### Task 8: Example transform test directory

**Files:**
- Create: `tests/transforms/__init__.py` (empty)
- Create: `tests/transforms/test_example_daily_revenue.py`

- [ ] **Step 1: Write the example (it's a passing test, TDD not applicable — it documents the pattern)**

```python
# tests/transforms/test_example_daily_revenue.py
"""Example transform test — copy this pattern for real business transforms."""
import pandas as pd

from etl_framework.transform_testing.harness import TransformCase

DAILY_REVENUE_SQL = """
SELECT
    order_date,
    SUM(amount) FILTER (WHERE status <> 'CANCELLED') AS revenue
FROM orders
GROUP BY order_date
ORDER BY order_date
"""


def test_cancelled_orders_excluded_from_revenue():
    mismatches = TransformCase(
        transform_sql=DAILY_REVENUE_SQL,
        inputs={"orders": pd.DataFrame({
            "order_date": ["2026-07-01", "2026-07-01", "2026-07-02"],
            "amount": [100.0, 50.0, 75.0],
            "status": ["COMPLETE", "CANCELLED", "COMPLETE"],
        })},
        expected=pd.DataFrame({
            "order_date": ["2026-07-01", "2026-07-02"],
            "revenue": [100.0, 75.0],
        }),
        key_columns=["order_date"],
    ).run()
    assert mismatches == [], f"Transform diverged: {mismatches}"
```

- [ ] **Step 2: Run it**

Run: `python -m pytest tests/transforms -v`
Expected: 1 PASSED

- [ ] **Step 3: Commit**

```bash
git add tests/transforms
git commit -m "test: example TransformCase transform test"
```

---

## Phase 3 — Write-Audit-Publish Gate Endpoint

Verdict semantics (MVP, composable with any orchestrator): the orchestrator loads to staging, triggers the job's run, then calls the gate. Gate answers **PROMOTE** only when (a) the latest `TestResult` for the job is `PASSED` and (b) no unresolved `ContractBreach` exists for any active contract whose `source_job` is this job. Anything else — `HOLD`, with machine-readable reasons.

### Task 9: Gate service

**Files:**
- Create: `api/services/gate_service.py`
- Test: `tests/unit/test_gate_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_gate_service.py
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.services.gate_service import evaluate_gate
from etl_framework.repository.contract_models import Contract, ContractBreach
from etl_framework.repository.database import Base
from etl_framework.repository.models import TestResult, TestRun


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_run(db, job: str, status: str, run_id: str = "run-1"):
    db.add(TestRun(run_id=run_id, status="COMPLETED"))
    db.add(TestResult(
        run_id=run_id, query_name=job, status=status,
        executed_at=datetime.now(timezone.utc),
    ))
    db.commit()


def test_promote_when_latest_result_passed(db):
    _seed_run(db, "orders_reconciliation", "PASSED")
    verdict = evaluate_gate("orders_reconciliation", db)
    assert verdict.verdict == "PROMOTE"
    assert verdict.run_id == "run-1"


def test_hold_when_latest_result_failed(db):
    _seed_run(db, "orders_reconciliation", "FAILED")
    verdict = evaluate_gate("orders_reconciliation", db)
    assert verdict.verdict == "HOLD"
    assert any("FAILED" in r for r in verdict.reasons)


def test_hold_when_no_run_exists(db):
    verdict = evaluate_gate("orders_reconciliation", db)
    assert verdict.verdict == "HOLD"
    assert any("no run" in r.lower() for r in verdict.reasons)


def test_hold_on_open_contract_breach(db):
    _seed_run(db, "orders_reconciliation", "PASSED")
    contract = Contract(name="orders_contract", source_job="orders_reconciliation",
                        owner="team-data", sla_hours=4.0)
    db.add(contract)
    db.flush()
    db.add(ContractBreach(contract_id=contract.id, run_id="run-0",
                          breach_type="dq_violation"))
    db.commit()
    verdict = evaluate_gate("orders_reconciliation", db)
    assert verdict.verdict == "HOLD"
    assert any("breach" in r.lower() for r in verdict.reasons)


def test_latest_result_wins(db):
    _seed_run(db, "orders_reconciliation", "FAILED", run_id="run-old")
    db.add(TestRun(run_id="run-new", status="COMPLETED"))
    db.add(TestResult(
        run_id="run-new", query_name="orders_reconciliation", status="PASSED",
        executed_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    ))
    db.commit()
    assert evaluate_gate("orders_reconciliation", db).verdict == "PROMOTE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_gate_service.py -v`
Expected: FAIL with ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# api/services/gate_service.py
"""Write-Audit-Publish gate: PROMOTE/HOLD verdict for a job's staged data.

Orchestration contract: load staging -> run job -> call gate -> swap/publish
only on PROMOTE. The gate never publishes anything itself.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from etl_framework.repository.contract_models import Contract, ContractBreach
from etl_framework.repository.models import TestResult


class GateVerdict(BaseModel):
    job: str
    verdict: str  # "PROMOTE" | "HOLD"
    run_id: str | None = None
    result_status: str | None = None
    reasons: list[str] = Field(default_factory=list)
    evaluated_at: datetime


def evaluate_gate(job_name: str, db: Session) -> GateVerdict:
    reasons: list[str] = []

    result = (
        db.query(TestResult)
        .filter(TestResult.query_name == job_name)
        .order_by(desc(TestResult.executed_at), desc(TestResult.id))
        .first()
    )
    if result is None:
        reasons.append(f"No run result found for job '{job_name}'")
    elif result.status != "PASSED":
        reasons.append(
            f"Latest result for '{job_name}' is {result.status}"
            + (f": {result.error_message}" if result.error_message else "")
        )

    open_breaches = (
        db.query(ContractBreach)
        .join(Contract, Contract.id == ContractBreach.contract_id)
        .filter(
            Contract.source_job == job_name,
            Contract.active.is_(True),
            ContractBreach.resolved_at.is_(None),
        )
        .count()
    )
    if open_breaches:
        reasons.append(f"{open_breaches} open contract breach(es) on '{job_name}'")

    return GateVerdict(
        job=job_name,
        verdict="HOLD" if reasons else "PROMOTE",
        run_id=result.run_id if result is not None else None,
        result_status=result.status if result is not None else None,
        reasons=reasons,
        evaluated_at=datetime.now(timezone.utc),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_gate_service.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add api/services/gate_service.py tests/unit/test_gate_service.py
git commit -m "feat: WAP gate service with PROMOTE/HOLD verdicts"
```

### Task 10: Gate API route

**Files:**
- Create: `api/routes/gates.py`
- Modify: `api/main.py` (router registration)
- Test: `tests/unit/test_gates_routes.py`

- [ ] **Step 1: Write the failing test** (same client fixture pattern as Task 3)

```python
# tests/unit/test_gates_routes.py
def test_gate_endpoint_returns_verdict(client, db):
    from datetime import datetime, timezone
    from etl_framework.repository.models import TestResult, TestRun
    db.add(TestRun(run_id="r1", status="COMPLETED"))
    db.add(TestResult(run_id="r1", query_name="orders_reconciliation",
                      status="PASSED", executed_at=datetime.now(timezone.utc)))
    db.commit()
    resp = client.post("/api/gates/orders_reconciliation/evaluate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "PROMOTE"
    assert body["job"] == "orders_reconciliation"


def test_gate_endpoint_holds_unknown_job(client):
    resp = client.post("/api/gates/ghost_job/evaluate")
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "HOLD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_gates_routes.py -v`
Expected: FAIL (404)

- [ ] **Step 3: Write minimal implementation**

```python
# api/routes/gates.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from api.services.audit_service import AuditService
from api.services.gate_service import GateVerdict, evaluate_gate
from etl_framework.repository.database import get_db as get_session

router = APIRouter(prefix="/api/gates", tags=["gates"])


@router.post("/{job_name}/evaluate", response_model=GateVerdict)
def evaluate(job_name: str, request: Request, db: Session = Depends(get_session)):
    verdict = evaluate_gate(job_name, db)
    AuditService(db).log(
        request, "gate.evaluated", "gate", job_name,
        {"verdict": verdict.verdict, "reasons": verdict.reasons},
    )
    return verdict
```

Register in `api/main.py` exactly like Task 3's router. (Same caveat: mirror neighbours' import style and the actual `AuditService.log` signature from `api/routes/jobs.py`.)

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/unit/test_gates_routes.py tests/unit -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/routes/gates.py api/main.py tests/unit/test_gates_routes.py
git commit -m "feat: POST /api/gates/{job}/evaluate WAP gate endpoint"
```

- [ ] **Step 6: README bullet** (Capabilities section) + commit

```markdown
- **Write-Audit-Publish gate** — `POST /api/gates/{job}/evaluate` returns a machine-readable `PROMOTE`/`HOLD` verdict (latest result status + open contract breaches) so orchestrators can gate a staging→production swap on data quality.
```

```bash
git add README.md
git commit -m "docs: WAP gate capability"
```

---

## Phase 4 — Secret Providers + Config Overlays

### Task 11: SecretProvider abstraction

**Files:**
- Create: `etl_framework/config/secrets.py`
- Test: `tests/unit/test_secret_providers.py`

URI scheme: `secret://<provider>/<name>` — e.g. `secret://env/DB_PASSWORD`. Providers registered by name; `env` ships by default. Unknown provider or missing secret raise `ValueError` (matching loader's existing missing-env-var behaviour).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_secret_providers.py
import pytest

from etl_framework.config.secrets import (
    EnvSecretProvider,
    is_secret_uri,
    register_provider,
    resolve_secret_uri,
)


def test_env_provider_reads_environment(monkeypatch):
    monkeypatch.setenv("MY_DB_PASS", "s3cret")
    assert resolve_secret_uri("secret://env/MY_DB_PASS") == "s3cret"


def test_env_provider_missing_raises(monkeypatch):
    monkeypatch.delenv("NOPE_MISSING", raising=False)
    with pytest.raises(ValueError, match="NOPE_MISSING"):
        resolve_secret_uri("secret://env/NOPE_MISSING")


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="vault"):
        resolve_secret_uri("secret://vault/whatever")


def test_custom_provider_registration():
    class Static:
        def get(self, name: str) -> str:
            return f"static-{name}"

    register_provider("static", Static())
    assert resolve_secret_uri("secret://static/abc") == "static-abc"


def test_is_secret_uri():
    assert is_secret_uri("secret://env/X")
    assert not is_secret_uri("${ENV_VAR}")
    assert not is_secret_uri("plainvalue")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_secret_providers.py -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Write minimal implementation**

```python
# etl_framework/config/secrets.py
"""Pluggable secret resolution for config values.

Config files reference secrets as ``secret://<provider>/<name>``. The ``env``
provider ships by default; deployments register others (Vault, Azure Key
Vault, ...) at startup via ``register_provider`` without touching config
parsing code.
"""
from __future__ import annotations

import os
from typing import Protocol

_PREFIX = "secret://"


class SecretProvider(Protocol):
    def get(self, name: str) -> str: ...


class EnvSecretProvider:
    def get(self, name: str) -> str:
        value = os.environ.get(name)
        if value is None:
            raise ValueError(f"Secret env var '{name}' is not set")
        return value


_PROVIDERS: dict[str, SecretProvider] = {"env": EnvSecretProvider()}


def register_provider(name: str, provider: SecretProvider) -> None:
    _PROVIDERS[name] = provider


def is_secret_uri(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def resolve_secret_uri(uri: str) -> str:
    rest = uri[len(_PREFIX):]
    provider_name, _, secret_name = rest.partition("/")
    if not provider_name or not secret_name:
        raise ValueError(f"Malformed secret URI: {uri!r} (want secret://<provider>/<name>)")
    provider = _PROVIDERS.get(provider_name)
    if provider is None:
        raise ValueError(
            f"Unknown secret provider '{provider_name}' in {uri!r}; "
            f"registered: {sorted(_PROVIDERS)}"
        )
    return provider.get(secret_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_secret_providers.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add etl_framework/config/secrets.py tests/unit/test_secret_providers.py
git commit -m "feat: pluggable SecretProvider abstraction with secret:// URIs"
```

### Task 12: Loader support for secret:// URIs and base overlays

**Files:**
- Modify: `etl_framework/config/loader.py`
- Test: extend `tests/unit/test_config.py`

Overlay rule: if `environments.base` exists, it is a template — merged under every other environment (shallow merge, env's own keys win) and **not** returned as an environment itself.

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/test_config.py`; match its existing style for writing temp YAML — reuse its helper/fixture if one exists)

```python
def test_loader_resolves_secret_uris(tmp_path, monkeypatch):
    monkeypatch.setenv("QA_DB_PASS", "resolved-pass")
    config = tmp_path / "envs.yml"
    config.write_text(
        "environments:\n"
        "  qa:\n"
        "    db_host: qa-host\n"
        "    db_password: secret://env/QA_DB_PASS\n",
        encoding="utf-8",
    )
    envs = ConfigLoader().load(str(config))
    assert envs["qa"].db_password == "resolved-pass"


def test_loader_merges_base_overlay(tmp_path):
    config = tmp_path / "envs.yml"
    config.write_text(
        "environments:\n"
        "  base:\n"
        "    db_host: shared-host\n"
        "    db_password: shared-pass\n"
        "    db_port: 1433\n"
        "  dev:\n"
        "    db_name: dev_db\n"
        "  qa:\n"
        "    db_name: qa_db\n"
        "    db_host: qa-override\n",
        encoding="utf-8",
    )
    envs = ConfigLoader().load(str(config))
    assert "base" not in envs
    assert envs["dev"].db_host == "shared-host"
    assert envs["dev"].db_name == "dev_db"
    assert envs["qa"].db_host == "qa-override"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config.py -v -k "secret_uris or base_overlay"`
Expected: FAIL (secret URI stored verbatim / ValidationError for base env or `base` in result)

- [ ] **Step 3: Modify the loader** — in `etl_framework/config/loader.py`:

Add import at top:

```python
from etl_framework.config.secrets import is_secret_uri, resolve_secret_uri
```

In `load`, before the `for env_name, env_raw in ...` loop, extract and strip the base overlay, then merge it under each env:

```python
        env_blocks = dict(raw.get("environments") or {})
        base_overlay = env_blocks.pop("base", None) or {}
        envs = {}
        for env_name, env_raw in env_blocks.items():
            merged = {**base_overlay, **env_raw}
            resolved = {k: self._resolve_env_vars(str(v)) if isinstance(v, str) else v
                        for k, v in merged.items()}
```

(the rest of the loop body is unchanged — keep the existing validation/error handling exactly as-is, operating on `resolved`).

In `_resolve_env_vars`, resolve secret URIs first:

```python
    def _resolve_env_vars(self, value: str) -> str:
        if is_secret_uri(value):
            return resolve_secret_uri(value)
        # ... existing ${VAR} logic unchanged
```

- [ ] **Step 4: Run config tests + full suite**

Run: `python -m pytest tests/unit/test_config.py tests/unit -q`
Expected: all PASS (pre-existing loader tests must not regress — the loop refactor keeps identical behaviour when no `base` key exists)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/config/loader.py tests/unit/test_config.py
git commit -m "feat: config loader secret:// resolution and base-overlay merge"
```

- [ ] **Step 6: README** — add to Configuration section:

```markdown
Environment config supports a `base` overlay (shared settings merged under every environment; per-env keys win) and `secret://<provider>/<name>` values (built-in `env` provider reads environment variables; register custom providers via `etl_framework.config.secrets.register_provider`).
```

```bash
git add README.md
git commit -m "docs: config overlays and secret providers"
```

---

## Phase 5 — CLI Gate Exit Codes + Shadow Run Profile

### Task 13: Shadow run profile in RunSettings + executor

**Files:**
- Modify: `api/schemas.py:67-88` (`RunSettings`)
- Modify: `api/services/run_executor.py:1101-1115` (`_build_backend`)
- Test: extend `tests/unit/test_run_executor.py`

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_run_executor.py` — it already imports `RunExecutor`, `RunSettings`, `RunRepository`, `JobRepository` and defines a `_session()` helper; `make_job_definition` comes from `tests.helpers.factories`)

```python
def test_shadow_profile_wraps_backend_in_sampling():
    from etl_framework.reconciliation.backends.sampling_backend import SamplingBackend
    from tests.helpers.factories import make_job_definition

    db = _session()
    RunRepository(db).create_run("run-shadow-001", "dev", "prod", {})
    executor = RunExecutor(
        db=db,
        run_id="run-shadow-001",
        source_env="dev",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(run_profile="shadow", metrics_enabled=False),
    )
    backend = executor._build_backend(make_job_definition())
    assert isinstance(backend, SamplingBackend)


def test_full_profile_backend_unwrapped():
    from etl_framework.reconciliation.backends.sampling_backend import SamplingBackend
    from tests.helpers.factories import make_job_definition

    db = _session()
    RunRepository(db).create_run("run-full-001", "dev", "prod", {})
    executor = RunExecutor(
        db=db,
        run_id="run-full-001",
        source_env="dev",
        target_env="prod",
        job_sequence=[],
        run_settings=RunSettings(metrics_enabled=False),
    )
    backend = executor._build_backend(make_job_definition())
    assert not isinstance(backend, SamplingBackend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_run_executor.py -v -k shadow`
Expected: FAIL — `RunSettings` rejects unknown field `run_profile` (`extra="forbid"`)

- [ ] **Step 3: Implement** — in `api/schemas.py`, add two fields to `RunSettings` (after `comparison_backend`):

```python
    run_profile: Literal["full", "shadow"] = "full"
    shadow_sample_frac: float = Field(default=0.02, gt=0, le=1.0)
```

In `api/services/run_executor.py` `_build_backend`, wrap the return value:

```python
    def _build_backend(self, job: JobDefinition):
        key_columns = job.key_columns or self._settings.key_columns
        if self._settings.comparison_backend == "polars":
            backend = PolarsBackend(
                key_columns=key_columns,
                float_tolerance=self._settings.float_tolerance,
                null_equals_null=self._settings.null_equals_null,
                mismatch_row_limit=self._settings.mismatch_row_limit,
            )
        else:
            backend = PandasBackend(
                key_columns=key_columns,
                float_tolerance=self._settings.float_tolerance,
                null_equals_null=self._settings.null_equals_null,
                mismatch_row_limit=self._settings.mismatch_row_limit,
            )
        if self._settings.run_profile == "shadow":
            from etl_framework.reconciliation.backends.sampling_backend import SamplingBackend
            backend = SamplingBackend(backend, sample_frac=self._settings.shadow_sample_frac)
        return backend
```

- [ ] **Step 4: Run executor tests + full suite**

Run: `python -m pytest tests/unit/test_run_executor.py tests/unit -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add api/schemas.py api/services/run_executor.py tests/unit/test_run_executor.py
git commit -m "feat: shadow run profile samples reconciliation via SamplingBackend"
```

### Task 14: CLI gate exit codes

**Files:**
- Modify: `etl_framework/runner/cli.py`
- Test: extend `tests/unit/test_runner_cli.py`

Exit-code contract (document in `--help` text): `0` run PASSED/COMPLETED with no failures; `1` run had failures (FAILED); `2` run CANCELLED; `3` run had errors or infra failure; `4` run not found. `--gate-run <run_id>` queries the repository DB and exits — CI calls this after triggering a run via the API.

- [ ] **Step 1: Write the failing test** (append to `tests/unit/test_runner_cli.py`; check how that file invokes `main` — it passes an argv list)

```python
def test_gate_run_exit_codes(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from etl_framework.repository.database import Base
    from etl_framework.repository.models import TestRun
    from etl_framework.runner import cli

    engine = create_engine(f"sqlite:///{tmp_path / 'gate.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(TestRun(run_id="run-pass", status="COMPLETED", failed=0, error=0))
    session.add(TestRun(run_id="run-fail", status="COMPLETED", failed=2, error=0))
    session.add(TestRun(run_id="run-err", status="COMPLETED", failed=0, error=1))
    session.add(TestRun(run_id="run-cancel", status="CANCELLED"))
    session.commit()
    session.close()

    monkeypatch.setattr(cli, "_gate_session_factory", sessionmaker(bind=engine))

    assert cli.main(["--gate-run", "run-pass"]) == 0
    assert cli.main(["--gate-run", "run-fail"]) == 1
    assert cli.main(["--gate-run", "run-cancel"]) == 2
    assert cli.main(["--gate-run", "run-err"]) == 3
    assert cli.main(["--gate-run", "run-ghost"]) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_runner_cli.py -v -k gate`
Expected: FAIL — argparse error: `--config` required / unknown `--gate-run`

- [ ] **Step 3: Implement** — in `etl_framework/runner/cli.py`:

Make `--config`, `--source-env`, `--target-env` non-required when gating; add the flag and a lazy session factory:

```python
# module level, after imports
def _default_gate_session_factory():
    from etl_framework.repository.database import SessionLocal, init_db
    init_db()
    return SessionLocal

_gate_session_factory = None  # test seam; resolved lazily in _gate_exit_code


def _gate_exit_code(run_id: str, output: str) -> int:
    from etl_framework.repository.models import TestRun
    factory = _gate_session_factory or _default_gate_session_factory()
    session = factory()
    try:
        run = session.query(TestRun).filter(TestRun.run_id == run_id).first()
        if run is None:
            verdict, code = "NOT_FOUND", 4
        elif run.status == "CANCELLED":
            verdict, code = "CANCELLED", 2
        elif (run.error or 0) > 0 or run.status == "ERROR":
            verdict, code = "ERROR", 3
        elif (run.failed or 0) > 0 or run.status == "FAILED":
            verdict, code = "FAILED", 1
        else:
            verdict, code = "PASSED", 0
        if output == "json":
            print(json.dumps({
                "run_id": run_id, "verdict": verdict, "exit_code": code,
                "passed": getattr(run, "passed", None),
                "failed": getattr(run, "failed", None),
                "error": getattr(run, "error", None),
            }))
        else:
            print(f"{verdict} run={run_id} exit={code}")
        return code
    finally:
        session.close()
```

In `build_parser`, change the three required args to `required=False` and add:

```python
    parser.add_argument(
        "--gate-run", default=None, metavar="RUN_ID",
        help="CI gate: exit 0=passed 1=failed 2=cancelled 3=error 4=not-found for the given run, then stop",
    )
```

At the top of `main`, right after `configure_logging(...)` would run — actually place it before config loading so gating needs no config file:

```python
    args = parser.parse_args(argv)
    configure_logging(level=args.log_level, log_format=args.log_format)
    if args.gate_run:
        return _gate_exit_code(args.gate_run, args.output)
    if not (args.config and args.source_env and args.target_env):
        parser.error("--config, --source-env and --target-env are required unless --gate-run is used")
    environments = ConfigLoader().load(args.config)
```

The test seam: `_gate_exit_code` reads module attribute `_gate_session_factory` at call time, so `monkeypatch.setattr(cli, "_gate_session_factory", ...)` works. Reference it via `globals()["_gate_session_factory"]` is unnecessary — plain module-global read is late-bound in Python.

- [ ] **Step 4: Run CLI tests + full suite**

Run: `python -m pytest tests/unit/test_runner_cli.py tests/unit -q`
Expected: all PASS (pre-existing CLI tests still pass — required-arg change is loosened only when `--gate-run` given, enforced manually otherwise)

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/cli.py tests/unit/test_runner_cli.py
git commit -m "feat: CLI --gate-run with documented CI exit codes"
```

### Task 15: pytest-xdist + final docs

**Files:**
- Modify: `pyproject.toml` (dev extras)
- Modify: `README.md` (Testing + Capabilities sections)

- [ ] **Step 1: Add `pytest-xdist` to `[project.optional-dependencies] dev`**

```toml
dev = [
    "pytest>=7.0",
    "pytest-xdist>=3.5",
    "hypothesis>=6.0",
    "pytest-cov",
    "python-json-logger>=2.0",
    "httpx>=0.28",
]
```

- [ ] **Step 2: Verify parallel run works**

Run: `pip install -e ".[dev]" && python -m pytest tests/unit -n auto -q`
Expected: same pass count as serial. If tests fail only under `-n auto` (shared SQLite/file state), do NOT chase fixes here — revert to documenting `-n auto` for `tests/unit` subsets that pass, note the failing files in the README Testing section, and move on. Parallel-unsafe tests are a follow-up.

- [ ] **Step 3: README** — Testing section addition:

```markdown
### CI quality gate

    # trigger a run via API, capture RUN_ID, then:
    python -m etl_framework.runner.cli --gate-run "$RUN_ID" --output json
    # exit codes: 0 passed, 1 failed, 2 cancelled, 3 error, 4 not found

For cheap per-PR shadow runs, launch with `run_settings: {"run_profile": "shadow", "shadow_sample_frac": 0.02}` — every reconciliation samples ~2% of rows (missing rows always kept). Nightly runs use the default `full` profile.

Run the suite in parallel: `python -m pytest tests/unit -n auto`.
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml README.md
git commit -m "feat: pytest-xdist dev dependency and CI gate docs"
```

---

## Final Verification

- [ ] Full suite: `python -m pytest tests -q` (unit + property + transforms; integration tests may require docker — skip if the existing markers do)
- [ ] App boots: `python -m uvicorn api.main:app --port 8001` then `GET http://127.0.0.1:8001/docs` shows `expectations` and `gates` tags
- [ ] Round-trip smoke: `POST /api/expectations/export` → edit a YAML → `POST /api/expectations/sync` → job rules updated (check via `GET /api/jobs`)

## Execution Notes

- Phases are independent — safe to stop after any phase; each leaves the suite green.
- Two deliberate verify-before-trust points (marked NOTE above): `JobRepository` dict signatures (Task 2) and the route-test client fixture (Tasks 3/10). Both are asserted from call-site evidence, not read directly — confirm before coding.
- Never modify existing test assertions to make new code pass; new keys/fields are additive by design.
