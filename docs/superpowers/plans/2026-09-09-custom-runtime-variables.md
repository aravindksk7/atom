# Custom Runtime Variables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user define named variables (date/text/number/alphanumeric) on the Config page with a global default and optional per-Config override; jobs reference a variable as `{{name}}` in their `query`/`params`, and the value is resolved once per run and substituted before execution.

**Architecture:** A new `custom_variables` table holds global definitions; per-Config overrides ride inside the existing `SavedConfig.config_json["variables"]` blob (same pattern as `connections`/`api_endpoints`). A new `api/services/variable_resolution.py` module merges global → config-override → launch-override into one dict once per run (stored in the run's `config_snapshot["variables"]`), and substitutes `{{name}}` into each job's `query`/`params` inside `RunExecutor`.

**Tech Stack:** Python (FastAPI, Pydantic, SQLAlchemy), Alpine.js frontend, pytest.

**Reference:** `docs/superpowers/specs/2026-09-09-custom-runtime-variables-design.md`

---

### Task 1: `CustomVariable` model + repository

**Files:**
- Modify: `etl_framework/repository/models.py:18-27` (insert new class after `SavedConfig`)
- Modify: `etl_framework/repository/repository.py:1-15` (imports), `etl_framework/repository/repository.py:105-112` (insert new class after `ConfigRepository`)
- Test: `tests/unit/test_custom_variable_repository.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_custom_variable_repository.py`:

```python
import pytest
from etl_framework.repository.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from etl_framework.repository.repository import CustomVariableRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: F401 -- registers tables
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_create_and_get(db):
    repo = CustomVariableRepository(db)
    created = repo.create(name="run_date", var_type="date", default_value="today", description="Report date")
    assert created.id is not None
    fetched = repo.get(created.id)
    assert fetched.name == "run_date"
    assert fetched.var_type == "date"
    assert fetched.default_value == "today"


def test_get_by_name(db):
    repo = CustomVariableRepository(db)
    repo.create(name="batch_id", var_type="text", default_value=None, description="")
    found = repo.get_by_name("batch_id")
    assert found is not None
    assert repo.get_by_name("does_not_exist") is None


def test_list_orders_by_name(db):
    repo = CustomVariableRepository(db)
    repo.create(name="zeta", var_type="text", default_value=None, description="")
    repo.create(name="alpha", var_type="text", default_value=None, description="")
    names = [v.name for v in repo.list()]
    assert names == ["alpha", "zeta"]


def test_update(db):
    repo = CustomVariableRepository(db)
    created = repo.create(name="run_date", var_type="date", default_value="today", description="")
    updated = repo.update(created.id, default_value="today-1", description="Yesterday")
    assert updated.default_value == "today-1"
    assert updated.description == "Yesterday"
    assert updated.var_type == "date"  # untouched field stays as-is


def test_update_missing_returns_none(db):
    repo = CustomVariableRepository(db)
    assert repo.update(999, default_value="x") is None


def test_delete(db):
    repo = CustomVariableRepository(db)
    created = repo.create(name="run_date", var_type="date", default_value="today", description="")
    assert repo.delete(created.id) is True
    assert repo.get(created.id) is None
    assert repo.delete(created.id) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_custom_variable_repository.py -v`

Expected: failures with `ImportError: cannot import name 'CustomVariableRepository'`

- [ ] **Step 3: Write the implementation**

In `etl_framework/repository/models.py`, change:

```python
class SavedConfig(Base):
    __tablename__ = "saved_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    env_name = Column(String(100), nullable=False)
    config_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class SavedJob(Base):
```

to:

```python
class SavedConfig(Base):
    __tablename__ = "saved_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    env_name = Column(String(100), nullable=False)
    config_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class CustomVariable(Base):
    __tablename__ = "custom_variables"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    var_type = Column(String(20), nullable=False)  # 'text' | 'number' | 'date' | 'alphanumeric'
    default_value = Column(Text, nullable=True)
    description = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class SavedJob(Base):
```

This is a brand-new table, so `Base.metadata.create_all(bind=engine)` in `init_db()` (`etl_framework/repository/database.py:28`) creates it automatically on both fresh and pre-existing databases — no `ensure_table` shim needed.

In `etl_framework/repository/repository.py`, change the models import:

```python
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, NotificationDelivery, ScheduledRun, JobLineageEdge, AuditEvent,
    RunStep, JobSelection, JobSelectionVersion, AppSettings, TERMINAL_STATUSES,
    SchedulerTelemetryEvent,
)
```

to:

```python
from etl_framework.repository.models import (
    SavedConfig, SavedJob, TestRun, TestResult, MismatchDetail,
    ApiToken, NotificationHook, NotificationDelivery, ScheduledRun, JobLineageEdge, AuditEvent,
    RunStep, JobSelection, JobSelectionVersion, AppSettings, TERMINAL_STATUSES,
    SchedulerTelemetryEvent, CustomVariable,
)
```

Then, right after `ConfigRepository` ends (the `delete` method, ending with `return True`, immediately followed by `class JobRepository:`), insert:

```python

class CustomVariableRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, name: str, var_type: str, default_value: str | None, description: str) -> CustomVariable:
        var = CustomVariable(name=name, var_type=var_type, default_value=default_value, description=description)
        self._db.add(var)
        self._db.commit()
        self._db.refresh(var)
        return var

    def get(self, variable_id: int) -> CustomVariable | None:
        return self._db.get(CustomVariable, variable_id)

    def get_by_name(self, name: str) -> CustomVariable | None:
        return self._db.query(CustomVariable).filter(CustomVariable.name == name).first()

    def list(self) -> list[CustomVariable]:
        return self._db.query(CustomVariable).order_by(CustomVariable.name).all()

    def update(self, variable_id: int, **kwargs) -> CustomVariable | None:
        var = self._db.get(CustomVariable, variable_id)
        if var is None:
            return None
        for field in ("var_type", "default_value", "description"):
            if field in kwargs:
                setattr(var, field, kwargs[field])
        var.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(var)
        return var

    def delete(self, variable_id: int) -> bool:
        var = self.get(variable_id)
        if var is None:
            return False
        self._db.delete(var)
        self._db.commit()
        return True

```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_custom_variable_repository.py -v`

Expected: all 6 tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add etl_framework/repository/models.py etl_framework/repository/repository.py tests/unit/test_custom_variable_repository.py
git commit -m "feat(variables): add CustomVariable model and repository"
```

---

### Task 2: `variable_types` — validation and date-expression resolution

**Files:**
- Create: `api/services/variable_types.py`
- Test: `tests/unit/test_variable_types.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_variable_types.py`:

```python
from datetime import date, timedelta

import pytest

from api.services.variable_types import (
    resolve_date_expression,
    validate_variable_name,
    validate_variable_value,
)


def test_validate_variable_name_accepts_valid_identifiers():
    validate_variable_name("run_date")
    validate_variable_name("_leading_underscore")
    validate_variable_name("batch2")


@pytest.mark.parametrize("name", ["run-date", "run date", "2run", "", "run.date"])
def test_validate_variable_name_rejects_invalid(name):
    with pytest.raises(ValueError):
        validate_variable_name(name)


def test_validate_variable_value_text_accepts_anything():
    validate_variable_value("anything goes! 123", "text")


@pytest.mark.parametrize("value", ["abc123", "ABC", "123"])
def test_validate_variable_value_alphanumeric_accepts(value):
    validate_variable_value(value, "alphanumeric")


@pytest.mark.parametrize("value", ["abc-123", "abc 123", "abc_123", ""])
def test_validate_variable_value_alphanumeric_rejects(value):
    with pytest.raises(ValueError):
        validate_variable_value(value, "alphanumeric")


@pytest.mark.parametrize("value", ["123", "-123", "1.5", "-1.5"])
def test_validate_variable_value_number_accepts(value):
    validate_variable_value(value, "number")


@pytest.mark.parametrize("value", ["abc", "1.2.3", "", "1,5"])
def test_validate_variable_value_number_rejects(value):
    with pytest.raises(ValueError):
        validate_variable_value(value, "number")


@pytest.mark.parametrize("value", ["2026-09-08", "today", "TODAY", "today-1", "today+2"])
def test_validate_variable_value_date_accepts(value):
    validate_variable_value(value, "date")


@pytest.mark.parametrize("value", ["2026-9-8", "09/08/2026", "tomorrow", "today-", "today+x"])
def test_validate_variable_value_date_rejects(value):
    with pytest.raises(ValueError):
        validate_variable_value(value, "date")


def test_resolve_date_expression_literal_passes_through():
    assert resolve_date_expression("2026-09-08") == "2026-09-08"


def test_resolve_date_expression_today():
    assert resolve_date_expression("today") == date.today().isoformat()


def test_resolve_date_expression_today_minus_n():
    assert resolve_date_expression("today-1") == (date.today() - timedelta(days=1)).isoformat()


def test_resolve_date_expression_today_plus_n():
    assert resolve_date_expression("today+2") == (date.today() + timedelta(days=2)).isoformat()


def test_resolve_date_expression_is_case_insensitive():
    assert resolve_date_expression("TODAY-1") == (date.today() - timedelta(days=1)).isoformat()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_variable_types.py -v`

Expected: failures with `ModuleNotFoundError: No module named 'api.services.variable_types'`

- [ ] **Step 3: Write the implementation**

Create `api/services/variable_types.py`:

```python
from __future__ import annotations

import re
from datetime import date, timedelta

VAR_TYPES = ("text", "number", "date", "alphanumeric")

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALPHANUMERIC_RE = re.compile(r"^[A-Za-z0-9]+$")
_NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DATE_LITERAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_EXPR_RE = re.compile(r"^today([+-]\d+)?$", re.IGNORECASE)


def validate_variable_name(name: str) -> None:
    """Raise ValueError unless `name` is a valid {{placeholder}} identifier."""
    if not _NAME_RE.match(name):
        raise ValueError(
            "name must start with a letter or underscore and contain only "
            "letters, digits, and underscores"
        )


def validate_variable_value(value: str, var_type: str) -> None:
    """Raise ValueError if `value` doesn't satisfy `var_type`'s format."""
    if var_type == "text":
        return
    if var_type == "alphanumeric":
        if not _ALPHANUMERIC_RE.match(value):
            raise ValueError(f"'{value}' is not a valid alphanumeric value (letters and digits only)")
        return
    if var_type == "number":
        if not _NUMBER_RE.match(value):
            raise ValueError(f"'{value}' is not a valid number")
        return
    if var_type == "date":
        if _DATE_LITERAL_RE.match(value) or _DATE_EXPR_RE.match(value):
            return
        raise ValueError(
            f"'{value}' is not a valid date: use YYYY-MM-DD, 'today', 'today+N', or 'today-N'"
        )
    raise ValueError(f"unknown variable type '{var_type}'")


def resolve_date_expression(value: str) -> str:
    """Evaluate 'today'/'today+N'/'today-N' to a concrete ISO date. A literal
    YYYY-MM-DD (or anything else that isn't a today-expression) passes
    through unchanged."""
    match = _DATE_EXPR_RE.match(value.strip())
    if not match:
        return value
    offset = int(match.group(1)) if match.group(1) else 0
    return (date.today() + timedelta(days=offset)).isoformat()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_variable_types.py -v`

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/services/variable_types.py tests/unit/test_variable_types.py
git commit -m "feat(variables): add variable name/value validation and today-expression resolution"
```

---

### Task 3: Variable schemas

**Files:**
- Modify: `api/schemas.py` (add after `ConfigOut`, i.e. after line 25's block ends around line 30 — insert before the next class)
- Test: `tests/unit/test_variable_schemas.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_variable_schemas.py`:

```python
import pytest
from pydantic import ValidationError

from api.schemas import CustomVariableCreate, CustomVariableUpdate


def test_create_accepts_valid_variable():
    v = CustomVariableCreate(name="run_date", var_type="date", default_value="today-1", description="Report date")
    assert v.name == "run_date"


def test_create_rejects_invalid_name():
    with pytest.raises(ValidationError):
        CustomVariableCreate(name="run-date", var_type="text", default_value=None)


def test_create_rejects_default_value_not_matching_type():
    with pytest.raises(ValidationError):
        CustomVariableCreate(name="batch_id", var_type="alphanumeric", default_value="not valid!")


def test_create_allows_blank_default_value():
    v = CustomVariableCreate(name="batch_id", var_type="alphanumeric", default_value=None)
    assert v.default_value is None


def test_update_rejects_default_value_not_matching_declared_type():
    with pytest.raises(ValidationError):
        CustomVariableUpdate(var_type="number", default_value="abc")


def test_update_allows_partial_fields():
    u = CustomVariableUpdate(description="new description")
    assert u.var_type is None
    assert u.default_value is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_variable_schemas.py -v`

Expected: failures with `ImportError: cannot import name 'CustomVariableCreate'`

- [ ] **Step 3: Write the implementation**

In `api/schemas.py`, change:

```python
class ConfigOut(BaseModel):
    id: int
    name: str
    env_name: str
    config_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FrameworkErrorOut(BaseModel):
```

to:

```python
class ConfigOut(BaseModel):
    id: int
    name: str
    env_name: str
    config_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CustomVariableCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    var_type: Literal["text", "number", "date", "alphanumeric"]
    default_value: str | None = None
    description: str = ""

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        from api.services.variable_types import validate_variable_name
        validate_variable_name(v)
        return v

    @model_validator(mode="after")
    def _validate_default_value(self) -> "CustomVariableCreate":
        if self.default_value:
            from api.services.variable_types import validate_variable_value
            validate_variable_value(self.default_value, self.var_type)
        return self


class CustomVariableUpdate(BaseModel):
    var_type: Literal["text", "number", "date", "alphanumeric"] | None = None
    default_value: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def _validate_default_value(self) -> "CustomVariableUpdate":
        if self.default_value and self.var_type:
            from api.services.variable_types import validate_variable_value
            validate_variable_value(self.default_value, self.var_type)
        return self


class CustomVariableOut(BaseModel):
    id: int
    name: str
    var_type: str
    default_value: str | None
    description: str
    created_at: datetime
    updated_at: datetime


class FrameworkErrorOut(BaseModel):
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_variable_schemas.py -v`

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/schemas.py tests/unit/test_variable_schemas.py
git commit -m "feat(variables): add CustomVariable request/response schemas"
```

---

### Task 4: Variables API routes

**Files:**
- Create: `api/routes/variables.py`
- Modify: `api/main.py:11` (import), `api/main.py:55` (register router)
- Test: `tests/unit/test_variables_routes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_variables_routes.py`. This follows the same isolated-DB fixture pattern already used by `tests/unit/test_selections_routes.py` — a fresh in-memory SQLite engine per test (not the shared dev database), with `SessionLocal` monkeypatched to bind to it, plus a bearer token since `BearerTokenMiddleware` requires auth on every route:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_create_and_list(client):
    resp = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today-1"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "run_date"

    listed = client.get("/api/variables")
    assert listed.status_code == 200
    assert any(v["name"] == "run_date" for v in listed.json())


def test_create_duplicate_name_rejected(client):
    client.post("/api/variables", json={"name": "batch_id", "var_type": "text", "default_value": None})
    resp = client.post("/api/variables", json={"name": "batch_id", "var_type": "text", "default_value": None})
    assert resp.status_code == 409


def test_create_invalid_value_rejected(client):
    resp = client.post("/api/variables", json={"name": "batch_id", "var_type": "number", "default_value": "abc"})
    assert resp.status_code == 422


def test_update(client):
    created = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"}).json()
    resp = client.put(f"/api/variables/{created['id']}", json={"default_value": "today-1"})
    assert resp.status_code == 200
    assert resp.json()["default_value"] == "today-1"


def test_update_missing_404(client):
    resp = client.put("/api/variables/999999", json={"description": "x"})
    assert resp.status_code == 404


def test_delete(client):
    created = client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"}).json()
    resp = client.delete(f"/api/variables/{created['id']}")
    assert resp.status_code == 204
    assert client.get("/api/variables").json() == [] or all(v["id"] != created["id"] for v in client.get("/api/variables").json())


def test_delete_missing_404(client):
    resp = client.delete("/api/variables/999999")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_variables_routes.py -v`

Expected: failures with 404 (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

Create `api/routes/variables.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import CustomVariableCreate, CustomVariableOut, CustomVariableUpdate
from etl_framework.repository.repository import CustomVariableRepository

router = APIRouter(tags=["variables"])


def _to_out(var) -> CustomVariableOut:
    return CustomVariableOut(
        id=var.id, name=var.name, var_type=var.var_type,
        default_value=var.default_value, description=var.description,
        created_at=var.created_at, updated_at=var.updated_at,
    )


@router.get("", response_model=list[CustomVariableOut])
def list_variables(db: Session = Depends(get_session)):
    return [_to_out(v) for v in CustomVariableRepository(db).list()]


@router.post("", response_model=CustomVariableOut, status_code=201)
def create_variable(body: CustomVariableCreate, db: Session = Depends(get_session)):
    repo = CustomVariableRepository(db)
    if repo.get_by_name(body.name) is not None:
        raise HTTPException(status_code=409, detail=f"A variable named '{body.name}' already exists")
    var = repo.create(
        name=body.name, var_type=body.var_type,
        default_value=body.default_value, description=body.description,
    )
    return _to_out(var)


@router.put("/{variable_id}", response_model=CustomVariableOut)
def update_variable(variable_id: int, body: CustomVariableUpdate, db: Session = Depends(get_session)):
    repo = CustomVariableRepository(db)
    kwargs = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    var = repo.update(variable_id, **kwargs)
    if var is None:
        raise HTTPException(status_code=404, detail="Variable not found")
    return _to_out(var)


@router.delete("/{variable_id}", status_code=204)
def delete_variable(variable_id: int, db: Session = Depends(get_session)):
    if not CustomVariableRepository(db).delete(variable_id):
        raise HTTPException(status_code=404, detail="Variable not found")
```

In `api/main.py`, change:

```python
from api.routes import configs, runs, jobs, health as health_routes, adapters, compare as compare_routes
```

to:

```python
from api.routes import configs, runs, jobs, health as health_routes, adapters, compare as compare_routes
from api.routes import variables as variables_routes
```

Then change:

```python
app.include_router(configs.router, prefix="/api/configs")
```

to:

```python
app.include_router(configs.router, prefix="/api/configs")
app.include_router(variables_routes.router, prefix="/api/variables")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_variables_routes.py -v`

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/routes/variables.py api/main.py tests/unit/test_variables_routes.py
git commit -m "feat(variables): add /api/variables CRUD routes"
```

---

### Task 5: `variable_resolution` — merge precedence + substitution

**Files:**
- Create: `api/services/variable_resolution.py`
- Test: `tests/unit/test_variable_resolution.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_variable_resolution.py`:

```python
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.schemas import JobDefinition
from api.services.variable_resolution import resolve_variables, substitute_in_job
from etl_framework.repository.database import Base
from etl_framework.repository.repository import ConfigRepository, CustomVariableRepository


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: F401
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_resolve_uses_global_default_when_no_override(db):
    CustomVariableRepository(db).create(name="batch_id", var_type="text", default_value="B1", description="")
    assert resolve_variables(db, config_id=None) == {"batch_id": "B1"}


def test_resolve_config_override_wins_over_global_default(db):
    CustomVariableRepository(db).create(name="batch_id", var_type="text", default_value="B1", description="")
    cfg = ConfigRepository(db).create(name="dev", env_name="dev", config_data={"variables": {"batch_id": "B2"}})
    assert resolve_variables(db, config_id=cfg.id) == {"batch_id": "B2"}


def test_resolve_launch_override_wins_over_config_override(db):
    CustomVariableRepository(db).create(name="batch_id", var_type="text", default_value="B1", description="")
    cfg = ConfigRepository(db).create(name="dev", env_name="dev", config_data={"variables": {"batch_id": "B2"}})
    result = resolve_variables(db, config_id=cfg.id, overrides={"batch_id": "B3"})
    assert result == {"batch_id": "B3"}


def test_resolve_ignores_override_for_undefined_variable(db):
    cfg = ConfigRepository(db).create(name="dev", env_name="dev", config_data={"variables": {"ghost": "x"}})
    assert resolve_variables(db, config_id=cfg.id) == {}


def test_resolve_evaluates_today_expression(db):
    CustomVariableRepository(db).create(name="run_date", var_type="date", default_value="today-1", description="")
    result = resolve_variables(db, config_id=None)
    assert result["run_date"] == (date.today() - timedelta(days=1)).isoformat()


def test_resolve_leaves_literal_date_untouched(db):
    CustomVariableRepository(db).create(name="run_date", var_type="date", default_value="2026-01-01", description="")
    assert resolve_variables(db, config_id=None) == {"run_date": "2026-01-01"}


def test_substitute_in_job_replaces_query_placeholder():
    job = JobDefinition(name="j1", query="SELECT * FROM sales WHERE dt = '{{run_date}}'")
    result = substitute_in_job(job, {"run_date": "2026-09-08"})
    assert result.query == "SELECT * FROM sales WHERE dt = '2026-09-08'"


def test_substitute_in_job_replaces_nested_params():
    job = JobDefinition(
        name="j1", job_type="bo_report",
        params={
            "report_id": "R1",
            "bo_parameters": [{"name": "Date", "value": "{{run_date}}"}],
        },
    )
    result = substitute_in_job(job, {"run_date": "2026-09-08"})
    assert result.params["bo_parameters"][0]["value"] == "2026-09-08"


def test_substitute_in_job_leaves_unknown_placeholder_verbatim():
    job = JobDefinition(name="j1", query="SELECT '{{typo_var}}'")
    result = substitute_in_job(job, {"run_date": "2026-09-08"})
    assert result.query == "SELECT '{{typo_var}}'"


def test_substitute_in_job_no_variables_returns_same_job():
    job = JobDefinition(name="j1", query="SELECT 1")
    assert substitute_in_job(job, {}) is job
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_variable_resolution.py -v`

Expected: failures with `ModuleNotFoundError: No module named 'api.services.variable_resolution'`

- [ ] **Step 3: Write the implementation**

Create `api/services/variable_resolution.py`:

```python
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from api.schemas import JobDefinition
from api.services.variable_types import resolve_date_expression
from etl_framework.repository.repository import ConfigRepository, CustomVariableRepository

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def resolve_variables(
    db: Session,
    config_id: int | None,
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge global default -> per-Config override -> launch override, then
    evaluate any 'today'/'today+N'/'today-N' date value to a concrete ISO
    date. Call this once per run (not once per job) so every job in a
    sequence sees identical values, even across a midnight boundary."""
    global_vars = CustomVariableRepository(db).list()
    global_names = {v.name for v in global_vars}
    var_types = {v.name: v.var_type for v in global_vars}

    resolved: dict[str, str] = {
        v.name: v.default_value for v in global_vars if v.default_value is not None
    }

    if config_id is not None:
        cfg = ConfigRepository(db).get(config_id)
        cfg_overrides = (cfg.config_json or {}).get("variables", {}) if cfg is not None else {}
        for name, value in cfg_overrides.items():
            if name in global_names and value:
                resolved[name] = value

    for name, value in (overrides or {}).items():
        if name in global_names and value:
            resolved[name] = value

    for name in list(resolved):
        if var_types.get(name) == "date":
            resolved[name] = resolve_date_expression(str(resolved[name]))

    return resolved


def _substitute_text(text: str, variables: dict[str, str]) -> str:
    def _replace(match: re.Match) -> str:
        return variables.get(match.group(1), match.group(0))
    return _PLACEHOLDER_RE.sub(_replace, text)


def _substitute_value(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _substitute_text(value, variables)
    if isinstance(value, dict):
        return {k: _substitute_value(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_value(v, variables) for v in value]
    return value


def substitute_in_job(job: JobDefinition, variables: dict[str, str]) -> JobDefinition:
    """Return a copy of `job` with every {{name}} in `query` and every string
    leaf inside `params` (dicts/lists walked recursively) replaced by its
    resolved value. An unresolved placeholder (e.g. a typo'd name) is left
    verbatim rather than raising."""
    if not variables:
        return job
    return job.model_copy(update={
        "query": _substitute_text(job.query, variables),
        "params": _substitute_value(job.params, variables),
    })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_variable_resolution.py -v`

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/services/variable_resolution.py tests/unit/test_variable_resolution.py
git commit -m "feat(variables): add resolve_variables and substitute_in_job"
```

---

### Task 6: Launch requests gain `variable_overrides`; resolved once per run

**Files:**
- Modify: `api/schemas.py` (`RunTrigger` ~line 370-379, `SequenceLaunchRequest` ~line 254-262, `JobSelectionLaunchRequest` ~line 875-883)
- Modify: `api/routes/runs.py:230-286` (`_snapshot_from_trigger`)
- Modify: `api/routes/selections.py` (`RunTrigger(...)` construction inside `launch_selection`)
- Modify: `api/routes/sequences.py` (`RunTrigger(...)` construction inside `launch_sequence`)
- Test: `tests/unit/test_run_trigger_variables.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_trigger_variables.py`. This follows the same isolated-DB fixture as `tests/unit/test_selections_routes.py`, and reuses that file's technique of monkeypatching `api.routes.selections._execute_run` to a capturing stub so the test can inspect `config_snapshot` synchronously instead of racing a real background run:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_launch_selection_resolves_variables_into_snapshot(client, monkeypatch):
    client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "2026-09-08"})
    cfg = client.post("/api/configs", json={"name": "dev-vars", "env_name": "dev", "config_data": {}}).json()
    selection = client.post("/api/selections", json={
        "name": "sel-vars", "description": "", "tags": [], "job_sequence": [],
    }).json()

    captured = {}
    monkeypatch.setattr(
        "api.routes.selections._execute_run",
        lambda run_id, job_sequence, source_env, target_env, run_settings, config_snapshot: captured.update(
            config_snapshot=config_snapshot
        ),
    )

    resp = client.post(
        f"/api/selections/{selection['id']}/launch",
        json={"source_env": "dev", "target_env": "", "config_id": cfg["id"],
              "variable_overrides": {"run_date": "2026-09-09"}},
    )
    assert resp.status_code == 202, resp.text
    assert captured["config_snapshot"]["variables"] == {"run_date": "2026-09-09"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_run_trigger_variables.py -v`

Expected: failure — `KeyError: 'variables'`, since `config_snapshot` doesn't have that key until `_snapshot_from_trigger` is updated

- [ ] **Step 3: Write the implementation**

In `api/schemas.py`, change `RunTrigger`:

```python
class RunTrigger(BaseModel):
    source_env: str
    target_env: str
    source_connection: str | None = None
    target_connection: str | None = None
    job_names: list[str] = Field(default_factory=list)
    job_sequence: list[str | SequenceStep] = Field(default_factory=list)
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    run_settings: RunSettings = Field(default_factory=RunSettings)
```

to:

```python
class RunTrigger(BaseModel):
    source_env: str
    target_env: str
    source_connection: str | None = None
    target_connection: str | None = None
    job_names: list[str] = Field(default_factory=list)
    job_sequence: list[str | SequenceStep] = Field(default_factory=list)
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    run_settings: RunSettings = Field(default_factory=RunSettings)
    variable_overrides: dict[str, str] = Field(default_factory=dict)
```

Change `SequenceLaunchRequest`:

```python
class SequenceLaunchRequest(BaseModel):
    source_env: str | None = None       # None = fall back to SequenceDefaults.source_env
    target_env: str | None = None       # None = fall back to SequenceDefaults.target_env
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None        # None = fall back to SequenceDefaults.config_id
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None          # pin a sequence_version; None = latest
    ci_context: dict[str, Any] | None = None
```

to:

```python
class SequenceLaunchRequest(BaseModel):
    source_env: str | None = None       # None = fall back to SequenceDefaults.source_env
    target_env: str | None = None       # None = fall back to SequenceDefaults.target_env
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None        # None = fall back to SequenceDefaults.config_id
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None          # pin a sequence_version; None = latest
    ci_context: dict[str, Any] | None = None
    variable_overrides: dict[str, str] = Field(default_factory=dict)
```

Change `JobSelectionLaunchRequest`:

```python
class JobSelectionLaunchRequest(BaseModel):
    source_env: str
    target_env: str = ""
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
    ci_context: dict[str, Any] | None = None
```

to:

```python
class JobSelectionLaunchRequest(BaseModel):
    source_env: str
    target_env: str = ""
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
    ci_context: dict[str, Any] | None = None
    variable_overrides: dict[str, str] = Field(default_factory=dict)
```

In `api/routes/runs.py`, change the end of `_snapshot_from_trigger`:

```python
    if "ds_credentials" not in snapshot:
        snapshot["ds_credentials"] = {"name": "ds", **cfg_data}
    return snapshot
```

to:

```python
    if "ds_credentials" not in snapshot:
        snapshot["ds_credentials"] = {"name": "ds", **cfg_data}

    from api.services.variable_resolution import resolve_variables
    snapshot["variables"] = resolve_variables(db, body.config_id, body.variable_overrides)
    return snapshot
```

In `api/routes/selections.py`, inside `launch_selection`, change the `RunTrigger(...)` construction:

```python
    trigger = RunTrigger(
        source_env=body.source_env,
        target_env=body.target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=job_sequence,
        # The selection remembers its own config (saved on the selection so
        # launching doesn't require re-picking one every time); an explicit
        # config_id on the launch request overrides it for a one-off run.
        config_id=body.config_id if body.config_id is not None else version.config_id,
        config_data=body.config_data,
        run_settings=version.run_settings_json or {},
    )
```

to:

```python
    trigger = RunTrigger(
        source_env=body.source_env,
        target_env=body.target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=job_sequence,
        # The selection remembers its own config (saved on the selection so
        # launching doesn't require re-picking one every time); an explicit
        # config_id on the launch request overrides it for a one-off run.
        config_id=body.config_id if body.config_id is not None else version.config_id,
        config_data=body.config_data,
        run_settings=version.run_settings_json or {},
        variable_overrides=body.variable_overrides,
    )
```

In `api/routes/sequences.py`, inside `launch_sequence`, change:

```python
    trigger = RunTrigger(
        source_env=source_env,
        target_env=target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=job_sequence,
        config_id=config_id,
        config_data=body.config_data,
        run_settings=run_settings,
    )
```

to:

```python
    trigger = RunTrigger(
        source_env=source_env,
        target_env=target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=job_sequence,
        config_id=config_id,
        config_data=body.config_data,
        run_settings=run_settings,
        variable_overrides=body.variable_overrides,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_run_trigger_variables.py -v`

Expected: passes. Then run the broader launch test sweep to check nothing else broke:

Run: `cd c:\atom && python -m pytest tests/unit/test_selections_routes.py tests/unit/test_sequences_routes.py tests/unit/test_runs_routes.py -q`

Expected: all pass (these exercise `_snapshot_from_trigger` heavily — a broken merge here would show up as failures in existing snapshot-shape assertions)

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/schemas.py api/routes/runs.py api/routes/selections.py api/routes/sequences.py tests/unit/test_run_trigger_variables.py
git commit -m "feat(variables): resolve variables once per run into config_snapshot"
```

---

### Task 7: Substitute variables into jobs at execution time

**Files:**
- Modify: `api/services/run_executor.py:328-331` (`_build_jobs_index`)
- Test: `tests/unit/test_run_executor_variables.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_run_executor_variables.py`:

```python
from api.schemas import RunSettings
from api.services.run_executor import RunExecutor


class _FakeJobRepo:
    def list(self):
        return []


def test_build_jobs_index_substitutes_seed_job_query(db_session=None):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from etl_framework.repository.database import Base

    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: F401
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    executor = RunExecutor(
        db=db, run_id="r1", source_env="dev", target_env="prod",
        job_sequence=[], run_settings=RunSettings(),
        config_snapshot={"variables": {"table": "orders_snapshot"}},
    )
    executor._job_repo = _FakeJobRepo()
    index = executor._build_jobs_index()
    assert index["orders_reconciliation"].query == "SELECT * FROM orders"
```

Note this test only proves the seed job (which has no `{{...}}` in its query) still runs unchanged; the substitution behavior itself is already covered by `substitute_in_job`'s own unit tests in Task 5. Add one more assertion that actually exercises substitution using a fake job repo returning a job with a placeholder:

```python
class _FakeSavedJob:
    name = "custom_job"
    description = ""
    tags = []
    job_type = "reconciliation"
    query = "SELECT * FROM t WHERE dt = '{{run_date}}'"
    key_columns = []
    exclude_columns = []
    source_env = None
    target_env = None
    params = {}
    enabled = True


class _FakeJobRepoWithJob:
    def list(self):
        return [_FakeSavedJob()]


def test_build_jobs_index_substitutes_saved_job_query():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from etl_framework.repository.database import Base

    engine = create_engine("sqlite:///:memory:")
    from etl_framework.repository import models  # noqa: F401
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    executor = RunExecutor(
        db=db, run_id="r1", source_env="dev", target_env="prod",
        job_sequence=[], run_settings=RunSettings(),
        config_snapshot={"variables": {"run_date": "2026-09-08"}},
    )
    executor._job_repo = _FakeJobRepoWithJob()
    index = executor._build_jobs_index()
    assert index["custom_job"].query == "SELECT * FROM t WHERE dt = '2026-09-08'"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_run_executor_variables.py -v`

Expected: `test_build_jobs_index_substitutes_saved_job_query` fails — query still contains `{{run_date}}`

- [ ] **Step 3: Write the implementation**

In `api/services/run_executor.py`, change:

```python
    def _build_jobs_index(self) -> dict[str, JobDefinition]:
        index: dict[str, JobDefinition] = {job.name: job for job in _SEED_JOBS}
        index.update({job.name: self._job_to_definition(job) for job in self._job_repo.list()})
        return index
```

to:

```python
    def _build_jobs_index(self) -> dict[str, JobDefinition]:
        from api.services.variable_resolution import substitute_in_job

        variables = self._config_snapshot.get("variables") or {}
        index: dict[str, JobDefinition] = {
            job.name: substitute_in_job(job, variables) for job in _SEED_JOBS
        }
        index.update({
            job.name: substitute_in_job(self._job_to_definition(job), variables)
            for job in self._job_repo.list()
        })
        return index
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_run_executor_variables.py -v`

Expected: both tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/services/run_executor.py tests/unit/test_run_executor_variables.py
git commit -m "feat(variables): substitute {{name}} placeholders into jobs before execution"
```

---

### Task 8: Validate per-Config variable overrides on save

**Files:**
- Modify: `api/routes/configs.py:1-25` (imports), `api/routes/configs.py:115-126` (`create_config`), `api/routes/configs.py:263-298` (`update_config`)
- Test: `tests/unit/test_configs_variable_validation.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_configs_variable_validation.py`. Same isolated-DB fixture as the earlier route test files:

```python
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_create_config_rejects_invalid_variable_override(client):
    client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"})
    resp = client.post("/api/configs", json={
        "name": "dev-bad-var", "env_name": "dev",
        "config_data": {"variables": {"run_date": "not-a-date"}},
    })
    assert resp.status_code == 422


def test_create_config_accepts_valid_variable_override(client):
    client.post("/api/variables", json={"name": "run_date", "var_type": "date", "default_value": "today"})
    resp = client.post("/api/configs", json={
        "name": "dev-good-var", "env_name": "dev",
        "config_data": {"variables": {"run_date": "2026-09-08"}},
    })
    assert resp.status_code == 201


def test_create_config_ignores_override_for_unknown_variable(client):
    resp = client.post("/api/configs", json={
        "name": "dev-unknown-var", "env_name": "dev",
        "config_data": {"variables": {"ghost": "anything"}},
    })
    assert resp.status_code == 201


def test_update_config_rejects_invalid_variable_override(client):
    client.post("/api/variables", json={"name": "batch_id", "var_type": "alphanumeric", "default_value": None})
    created = client.post("/api/configs", json={"name": "dev-update-var", "env_name": "dev", "config_data": {}}).json()
    resp = client.put(f"/api/configs/{created['id']}", json={"config_data": {"variables": {"batch_id": "not valid!"}}})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_configs_variable_validation.py -v`

Expected: `test_create_config_rejects_invalid_variable_override` and `test_update_config_rejects_invalid_variable_override` fail (currently return 201/200 instead of 422)

- [ ] **Step 3: Write the implementation**

In `api/routes/configs.py`, change the imports block:

```python
from api.schemas import (
    ConfigCreate,
    ConfigImportYamlRequest,
    ConfigUpdate,
    ConfigOut,
    ConfigValidationRequest,
    ConfigValidationOut,
    FrameworkErrorOut,
)
```

to:

```python
from api.schemas import (
    ConfigCreate,
    ConfigImportYamlRequest,
    ConfigUpdate,
    ConfigOut,
    ConfigValidationRequest,
    ConfigValidationOut,
    FrameworkErrorOut,
)
from etl_framework.repository.repository import CustomVariableRepository
```

Then, right after `_preserve_masked_secrets` (which ends with `return result`) and before `@router.get("", response_model=list[ConfigOut])`, insert:

```python
def _validate_variable_overrides(db: Session, variables) -> list[FrameworkErrorOut]:
    """Check each entry in a config's `variables` override dict against the
    matching global CustomVariable's type. A key with no matching global
    variable (deleted or never created) is inert, not an error -- see the
    design spec's rename/delete-is-inert decision. A blank value means
    "inherit the global default", also not an error."""
    if not isinstance(variables, dict) or not variables:
        return []
    from api.services.variable_types import validate_variable_value

    var_types = {v.name: v.var_type for v in CustomVariableRepository(db).list()}
    errors: list[FrameworkErrorOut] = []
    for name, value in variables.items():
        var_type = var_types.get(name)
        if var_type is None or value in (None, ""):
            continue
        try:
            validate_variable_value(str(value), var_type)
        except ValueError as exc:
            errors.append(FrameworkErrorOut(
                error_type="validation_error", message=str(exc),
                field_name=f"variables.{name}", details={},
            ))
    return errors
```

Then change `create_config`:

```python
@router.post("", response_model=ConfigOut, status_code=201)
def create_config(body: ConfigCreate, request: Request, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    cfg = repo.create(name=body.name, env_name=body.env_name, config_data=body.config_data)
```

to:

```python
@router.post("", response_model=ConfigOut, status_code=201)
def create_config(body: ConfigCreate, request: Request, db: Session = Depends(get_session)):
    errors = _validate_variable_overrides(db, body.config_data.get("variables"))
    if errors:
        raise HTTPException(status_code=422, detail=[e.model_dump() for e in errors])
    repo = ConfigRepository(db)
    cfg = repo.create(name=body.name, env_name=body.env_name, config_data=body.config_data)
```

Then change `update_config`:

```python
@router.put("/{config_id}", response_model=ConfigOut)
def update_config(config_id: int, body: ConfigUpdate, request: Request, db: Session = Depends(get_session)):
    repo = ConfigRepository(db)
    before = repo.get(config_id)
```

to:

```python
@router.put("/{config_id}", response_model=ConfigOut)
def update_config(config_id: int, body: ConfigUpdate, request: Request, db: Session = Depends(get_session)):
    if body.config_data is not None:
        errors = _validate_variable_overrides(db, body.config_data.get("variables"))
        if errors:
            raise HTTPException(status_code=422, detail=[e.model_dump() for e in errors])
    repo = ConfigRepository(db)
    before = repo.get(config_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_configs_variable_validation.py -v`

Expected: all pass. Then run the full configs test file to confirm no regression:

Run: `cd c:\atom && python -m pytest tests/unit/test_configs_routes.py -q`

Expected: all pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add api/routes/configs.py tests/unit/test_configs_variable_validation.py
git commit -m "feat(variables): validate per-config variable overrides on save"
```

---

### Task 9: CLI `--var` override

**Files:**
- Modify: `etl_framework/cli/app.py:129-201` (`run` command)
- Test: `tests/unit/test_cli_app.py` (extend; if this file doesn't exist yet, create it following the pattern below)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_cli_app.py` (create the file if it doesn't already exist elsewhere in `tests/unit/`):

```python
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from etl_framework.cli.app import app

runner = CliRunner()


def test_run_forwards_var_overrides_in_launch_payload():
    with patch("etl_framework.cli.app._make_client") as make_client:
        client = MagicMock()
        client.get_json.return_value = [{"id": 1, "name": "my-selection"}]
        client.post_json.return_value = {"run_id": "abc123"}
        client.get_json.side_effect = [
            [{"id": 1, "name": "my-selection"}],  # _resolve_target lookup
            {"status": "PASSED", "passed": 1, "failed": 0, "error": 0},  # _wait_for_run
        ]
        make_client.return_value = client

        result = runner.invoke(app, [
            "--api-url", "http://atom.test", "run", "my-selection",
            "--source-env", "dev",
            "--var", "run_date=2026-09-08",
            "--var", "batch_id=B1",
            "--no-wait",
        ])

    assert result.exit_code == 0
    payload = client.post_json.call_args[0][1]
    assert payload["variable_overrides"] == {"run_date": "2026-09-08", "batch_id": "B1"}


def test_run_rejects_malformed_var():
    with patch("etl_framework.cli.app._make_client") as make_client:
        make_client.return_value = MagicMock()
        result = runner.invoke(app, [
            "--api-url", "http://atom.test", "run", "my-selection",
            "--source-env", "dev", "--var", "no-equals-sign", "--no-wait",
        ])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd c:\atom && python -m pytest tests/unit/test_cli_app.py -v`

Expected: failures — `--var` is not a recognized option yet (typer reports "no such option")

- [ ] **Step 3: Write the implementation**

In `etl_framework/cli/app.py`, change the `run` command signature:

```python
@app.command()
def run(
    ctx: typer.Context,
    selection: str = typer.Argument(
        ..., help="Job selection or sequence id/exact name (see --target-type)"
    ),
    target_type: str = typer.Option(
        "selection", "--target-type", help="Target type: 'selection' or 'sequence'"
    ),
    source_env: str = typer.Option(..., "--source-env",
                                   help="Source environment name"),
    target_env: str = typer.Option("", "--target-env",
                                   help="Target environment name"),
    ci_commit_sha: Optional[str] = typer.Option(None, "--ci-commit-sha"),
    ci_pipeline_url: Optional[str] = typer.Option(None, "--ci-pipeline-url"),
    ci_ref: Optional[str] = typer.Option(None, "--ci-ref"),
    junit_out: Optional[Path] = typer.Option(None, "--junit-out",
                                             help="Write JUnit XML here"),
    json_out: Optional[Path] = typer.Option(None, "--json-out",
                                            help="Write run detail JSON here"),
    html_out: Optional[Path] = typer.Option(None, "--html-out",
                                             help="Write HTML report here"),
    timeout: float = typer.Option(3600.0, "--timeout",
                                  help="Max seconds to wait for completion"),
    poll_interval: float = typer.Option(10.0, "--poll-interval",
                                        help="Seconds between status polls"),
    no_wait: bool = typer.Option(False, "--no-wait",
                                 help="Launch, print run id, exit 0"),
) -> None:
```

to:

```python
@app.command()
def run(
    ctx: typer.Context,
    selection: str = typer.Argument(
        ..., help="Job selection or sequence id/exact name (see --target-type)"
    ),
    target_type: str = typer.Option(
        "selection", "--target-type", help="Target type: 'selection' or 'sequence'"
    ),
    source_env: str = typer.Option(..., "--source-env",
                                   help="Source environment name"),
    target_env: str = typer.Option("", "--target-env",
                                   help="Target environment name"),
    ci_commit_sha: Optional[str] = typer.Option(None, "--ci-commit-sha"),
    ci_pipeline_url: Optional[str] = typer.Option(None, "--ci-pipeline-url"),
    ci_ref: Optional[str] = typer.Option(None, "--ci-ref"),
    var: list[str] = typer.Option(
        [], "--var", help="Override a variable for this run only, NAME=VALUE (repeatable)"
    ),
    junit_out: Optional[Path] = typer.Option(None, "--junit-out",
                                             help="Write JUnit XML here"),
    json_out: Optional[Path] = typer.Option(None, "--json-out",
                                            help="Write run detail JSON here"),
    html_out: Optional[Path] = typer.Option(None, "--html-out",
                                             help="Write HTML report here"),
    timeout: float = typer.Option(3600.0, "--timeout",
                                  help="Max seconds to wait for completion"),
    poll_interval: float = typer.Option(10.0, "--poll-interval",
                                        help="Seconds between status polls"),
    no_wait: bool = typer.Option(False, "--no-wait",
                                 help="Launch, print run id, exit 0"),
) -> None:
```

Then change the body — find:

```python
        target_id = _resolve_target(client, target_type, selection)
        payload: dict = {"source_env": source_env, "target_env": target_env}
        ci_context = {k: v for k, v in {
            "commit_sha": ci_commit_sha,
            "pipeline_url": ci_pipeline_url,
            "ref": ci_ref,
        }.items() if v}
        if ci_context:
            payload["ci_context"] = ci_context
```

to:

```python
        target_id = _resolve_target(client, target_type, selection)
        payload: dict = {"source_env": source_env, "target_env": target_env}
        ci_context = {k: v for k, v in {
            "commit_sha": ci_commit_sha,
            "pipeline_url": ci_pipeline_url,
            "ref": ci_ref,
        }.items() if v}
        if ci_context:
            payload["ci_context"] = ci_context
        if var:
            variable_overrides = {}
            for entry in var:
                if "=" not in entry:
                    raise typer.BadParameter(
                        f"--var must be NAME=VALUE, got {entry!r}"
                    )
                name, value = entry.split("=", 1)
                variable_overrides[name] = value
            payload["variable_overrides"] = variable_overrides
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd c:\atom && python -m pytest tests/unit/test_cli_app.py -v`

Expected: both tests pass

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add etl_framework/cli/app.py tests/unit/test_cli_app.py
git commit -m "feat(cli): add --var NAME=VALUE launch override to atom run"
```

---

### Task 10: Frontend — Custom Variables panel on the Config tab

**Files:**
- Modify: `frontend/features/config.js` (state block after line 17, methods after `loadConfigs`)
- Modify: `frontend/partials/tab-config.html:42-43` (insert new card after the Configs grid, before the Schema Explorer panel)

- [ ] **Step 1: Add state and load/save/delete methods**

In `frontend/features/config.js`, change:

```javascript
    configs: [],
    // NOTE: app-help.js's global Escape-key handler reads this flag directly to
    // close the modal — don't rename without updating app-help.js too.
    showConfigModal: false,
    configModal: {},
    configValidation: null,
```

to:

```javascript
    configs: [],
    // NOTE: app-help.js's global Escape-key handler reads this flag directly to
    // close the modal — don't rename without updating app-help.js too.
    showConfigModal: false,
    configModal: {},
    configValidation: null,

    // Custom Variables (global defaults, referenced as {{name}} in jobs)
    customVariables: [],
    variableModal: null,   // null = closed; {} = editor open
    variableModalEditing: false,
```

Then change `loadConfigs`:

```javascript
    async loadConfigs() {
      try { this.configs = await api('GET', '/api/configs'); } catch {}
    },
```

to:

```javascript
    async loadConfigs() {
      try { this.configs = await api('GET', '/api/configs'); } catch {}
    },

    async loadCustomVariables() {
      try { this.customVariables = await api('GET', '/api/variables'); } catch {}
    },

    openNewVariableModal() {
      this.variableModal = { name: '', var_type: 'text', default_value: '', description: '' };
      this.variableModalEditing = false;
    },

    editVariable(v) {
      this.variableModal = { id: v.id, name: v.name, var_type: v.var_type, default_value: v.default_value || '', description: v.description || '' };
      this.variableModalEditing = true;
    },

    async saveVariable() {
      const m = this.variableModal;
      const body = {
        var_type: m.var_type,
        default_value: m.default_value.trim() || null,
        description: m.description || '',
      };
      try {
        if (this.variableModalEditing) {
          await api('PUT', `/api/variables/${m.id}`, body);
        } else {
          await api('POST', '/api/variables', { name: m.name.trim(), ...body });
        }
        this.variableModal = null;
        await this.loadCustomVariables();
        this.toast('success', 'Variable saved', m.name);
      } catch (e) {
        this.toast('error', 'Save failed', e.message);
      }
    },

    async deleteVariable(id, name) {
      const referencedIn = this.jobs.filter(j =>
        (j.query || '').includes(`{{${name}}}`) || JSON.stringify(j.params || {}).includes(`{{${name}}}`)
      );
      const warning = referencedIn.length
        ? ` It's referenced by ${referencedIn.length} job(s): ${referencedIn.map(j => j.name).join(', ')}.`
        : '';
      if (!confirm(`Delete variable "${name}"?${warning}`)) return;
      try {
        await api('DELETE', `/api/variables/${id}`);
        await this.loadCustomVariables();
        this.toast('success', 'Variable deleted', name);
      } catch (e) {
        this.toast('error', 'Delete failed', e.message);
      }
    },
```

- [ ] **Step 2: Load variables when the Config tab mounts**

Find where `loadConfigs()` is called on initial app load (search `frontend/features/*.js` and `frontend/app.js` for `loadConfigs()` in an init/`loadAll` sequence) and add `this.loadCustomVariables()` alongside it in the same `Promise.all`/sequence, so `customVariables` is populated before the Config tab first renders. Follow whatever pattern that call site already uses (e.g. if it's `await Promise.all([this.loadConfigs(), this.loadJobs(), ...])`, add `this.loadCustomVariables()` to that same array).

- [ ] **Step 3: Add the Custom Variables panel to the Config tab**

In `frontend/partials/tab-config.html`, change:

```html
  </div>

  <!-- Schema Explorer Panel -->
```

(this is the closing `</div>` of the Configs list's `<div class="grid gap-3">` wrapper — the one right after the `<template x-for="cfg in configs">` block, immediately followed by the `<!-- Schema Explorer Panel -->` comment) to:

```html
  </div>

  <!-- Custom Variables -->
  <div class="card mt-4" data-testid="custom-variables-card">
    <div class="flex items-center justify-between mb-2">
      <div>
        <div class="font-semibold text-slate-700">Custom Variables</div>
        <div class="text-xs text-muted">Reference as <code>{{name}}</code> in a job's query or params. Edit the value here once instead of per job.</div>
      </div>
      <button @click="openNewVariableModal()" type="button" class="btn-primary btn-sm" data-testid="variable-new-btn">+ Add variable</button>
    </div>
    <template x-if="customVariables.length === 0">
      <div class="text-muted text-sm py-2">No custom variables defined yet.</div>
    </template>
    <div class="space-y-1">
      <template x-for="v in customVariables" :key="v.id">
        <div class="flex items-center gap-3 px-3 py-2 rounded border border-slate-100 bg-slate-50 text-sm" :data-testid="'variable-row-' + v.id">
          <span class="font-mono font-medium text-slate-700" x-text="v.name"></span>
          <span class="badge badge-gray text-xs" x-text="v.var_type"></span>
          <span class="flex-1 text-muted truncate" x-text="v.default_value ? ('default: ' + v.default_value) : 'default: (none)'"></span>
          <button @click="editVariable(v)" class="btn-secondary btn-sm text-xs">Edit</button>
          <button @click="deleteVariable(v.id, v.name)" class="btn-danger btn-sm text-xs">Delete</button>
        </div>
      </template>
    </div>
  </div>

  <!-- Custom Variable editor modal -->
  <div x-show="variableModal !== null" x-cloak class="modal-backdrop" @click.self="variableModal = null">
    <div class="modal-box w-full max-w-md" role="dialog" aria-modal="true" aria-labelledby="variableDialogTitle">
      <template x-if="variableModal">
        <div>
          <h2 id="variableDialogTitle" class="text-lg font-bold mb-4" x-text="variableModalEditing ? 'Edit Variable' : 'New Variable'"></h2>
          <div class="space-y-3">
            <div>
              <label class="field-label">Name *</label>
              <input x-model="variableModal.name" :disabled="variableModalEditing" class="field-input font-mono" placeholder="run_date" data-testid="variable-modal-name-input" />
              <p class="text-xs text-muted mt-1" x-show="!variableModalEditing">Letters, digits, underscore only. Referenced as <code x-text="'{{' + (variableModal.name || 'name') + '}}'"></code>.</p>
            </div>
            <div>
              <label class="field-label">Type</label>
              <select x-model="variableModal.var_type" class="field-input field-select" data-testid="variable-modal-type-select">
                <option value="text">Text</option>
                <option value="number">Number</option>
                <option value="date">Date</option>
                <option value="alphanumeric">Alphanumeric</option>
              </select>
            </div>
            <div>
              <label class="field-label">Default value</label>
              <input x-model="variableModal.default_value" class="field-input" :placeholder="variableModal.var_type === 'date' ? 'YYYY-MM-DD, today, today-1, today+1' : ''" data-testid="variable-modal-default-input" />
            </div>
            <div>
              <label class="field-label">Description</label>
              <input x-model="variableModal.description" class="field-input" placeholder="What this variable is for" />
            </div>
          </div>
          <div class="flex justify-end gap-3 mt-6">
            <button @click="variableModal = null" class="btn-secondary" data-testid="variable-modal-cancel-btn">Cancel</button>
            <button @click="saveVariable()" class="btn-primary" :disabled="!variableModal.name" data-testid="variable-modal-save-btn">Save</button>
          </div>
        </div>
      </template>
    </div>
  </div>

  <!-- Schema Explorer Panel -->
```

- [ ] **Step 4: Syntax-check the JS file**

Run: `cd c:\atom && node --check frontend/features/config.js`

Expected: no output (exit code 0)

- [ ] **Step 5: Manual verification**

Read `frontend/partials/tab-config.html` back and confirm the new "Custom Variables" card and its modal are siblings of the Configs `<div class="grid gap-3">` block and the `<!-- Schema Explorer Panel -->` comment, with matching indentation and closing tags.

- [ ] **Step 6: Commit**

```bash
cd c:\atom
git add frontend/features/config.js frontend/partials/tab-config.html
git commit -m "feat(config-ui): add Custom Variables panel to the Config tab"
```

---

### Task 11: Frontend — Variable Overrides section in the Config edit modal

**Files:**
- Modify: `frontend/features/config.js` (`openNewConfigModal`, `editConfig`, `_configDataFromModal`)
- Modify: `frontend/partials/tab-config.html:934-938` (Automic block, right before `configValidation`)

- [ ] **Step 1: Add `variables` to the modal shape**

In `frontend/features/config.js`, change `openNewConfigModal`'s object literal — find:

```javascript
        connections: [],
        apiEndpoints: [],
        apiBaseHost: '',
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },
```

to:

```javascript
        connections: [],
        apiEndpoints: [],
        apiBaseHost: '',
        variables: {},
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },
```

In `editConfig`, find the end of its object literal — the `apiEndpoints: Object.entries(...)` block closes with `})),` right before the final `};`. Change:

```javascript
          exchangeOpen: false,
        })),
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },
```

to:

```javascript
          exchangeOpen: false,
        })),
        variables: { ...(d.variables || {}) },
      };
      this.configValidation = null;
      this.showConfigModal = true;
    },
```

In `_configDataFromModal`, find:

```javascript
      if (m.apiBaseHost && m.apiBaseHost.trim()) {
        data.api_base_host = m.apiBaseHost.trim();
      }
```

and add, right before it:

```javascript
      const nonBlankVariables = Object.fromEntries(
        Object.entries(m.variables || {}).filter(([, v]) => (v || '').toString().trim())
      );
      if (Object.keys(nonBlankVariables).length > 0) {
        data.variables = nonBlankVariables;
      }
```

(This mirrors the existing `if (m.connections && ...)`/`if (m.apiEndpoints && ...)` "only include if non-empty" pattern already in this function.)

- [ ] **Step 2: Add the Variable Overrides section to the Config modal**

In `frontend/partials/tab-config.html`, change:

```html
        <div class="grid-2">
          <div><label  class="field-label" for="a11y-config-automic-url">Automic URL</label><input x-model="configModal.automic_url" class="field-input" id="a11y-config-automic-url" /></div>
          <div><label  class="field-label" for="a11y-config-automic-user">Automic User</label><input x-model="configModal.automic_user" class="field-input" id="a11y-config-automic-user" /></div>
          <div><label  class="field-label" for="a11y-config-automic-password">Automic Password</label><input x-model="configModal.automic_password" type="password" class="field-input" id="a11y-config-automic-password" /></div>
        </div>
        <template x-if="configValidation">
```

to:

```html
        <div class="grid-2">
          <div><label  class="field-label" for="a11y-config-automic-url">Automic URL</label><input x-model="configModal.automic_url" class="field-input" id="a11y-config-automic-url" /></div>
          <div><label  class="field-label" for="a11y-config-automic-user">Automic User</label><input x-model="configModal.automic_user" class="field-input" id="a11y-config-automic-user" /></div>
          <div><label  class="field-label" for="a11y-config-automic-password">Automic Password</label><input x-model="configModal.automic_password" type="password" class="field-input" id="a11y-config-automic-password" /></div>
        </div>
        <template x-if="customVariables.length > 0">
          <div>
            <div class="divider"></div>
            <span class="field-label">VARIABLE OVERRIDES</span>
            <span class="text-xs text-slate-400 ml-2">Blank = use the global default for this variable.</span>
            <div class="grid-2 mt-2">
              <template x-for="v in customVariables" :key="v.id">
                <div>
                  <label class="field-label font-mono" x-text="v.name"></label>
                  <input x-model="configModal.variables[v.name]" class="field-input" :placeholder="'default: ' + (v.default_value || '(none)')" :data-testid="'config-modal-variable-' + v.name + '-input'" />
                </div>
              </template>
            </div>
          </div>
        </template>
        <template x-if="configValidation">
```

- [ ] **Step 3: Syntax-check the JS file**

Run: `cd c:\atom && node --check frontend/features/config.js`

Expected: no output (exit code 0)

- [ ] **Step 4: Manual verification**

Read `frontend/partials/tab-config.html` back and confirm the new `<template x-if="customVariables.length > 0">` block sits between the Automic `<div class="grid-2">` and the existing `<template x-if="configValidation">`, both still direct children of the modal's `<div class="space-y-4">`.

- [ ] **Step 5: Commit**

```bash
cd c:\atom
git add frontend/features/config.js frontend/partials/tab-config.html
git commit -m "feat(config-ui): add per-config Variable Overrides section to the Config modal"
```

---

### Task 12: Frontend — launch-time variable override on the Launch Selection modal

**Files:**
- Modify: `frontend/features/launch.js` (`openLaunchSelectionModal`, `launchSelection`)
- Modify: `frontend/partials/tab-launch.html:1324-1348`

- [ ] **Step 1: Wire `variable_overrides` into the launch payload**

In `frontend/features/launch.js`, change:

```javascript
    openLaunchSelectionModal(sel) {
      this.launchSelectionModal = { selection_id: sel.id, source_env: 'dev', target_env: 'prod' };
      this.showLaunchSelectionModal = true;
    },

    async launchSelection() {
      const m = this.launchSelectionModal;
      const body = { source_env: m.source_env, target_env: m.target_env || '' };
      try {
        const run = await api('POST', `/api/selections/${m.selection_id}/launch`, body);
```

to:

```javascript
    openLaunchSelectionModal(sel) {
      this.launchSelectionModal = { selection_id: sel.id, source_env: 'dev', target_env: 'prod', variableOverridesRaw: '' };
      this.showLaunchSelectionModal = true;
    },

    async launchSelection() {
      const m = this.launchSelectionModal;
      const body = { source_env: m.source_env, target_env: m.target_env || '' };
      const variable_overrides = {};
      (m.variableOverridesRaw || '').split('\n').forEach(line => {
        const idx = line.indexOf('=');
        if (idx > 0) variable_overrides[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
      });
      if (Object.keys(variable_overrides).length > 0) body.variable_overrides = variable_overrides;
      try {
        const run = await api('POST', `/api/selections/${m.selection_id}/launch`, body);
```

- [ ] **Step 2: Add the override textarea to the modal**

In `frontend/partials/tab-launch.html`, change:

```html
        <div>
          <label  class="field-label" for="a11y-launch-target-env-leave-blank-for-single-environment-jobs">Target Env (leave blank for single-environment jobs)</label>
          <select x-model="launchSelectionModal.target_env" class="field-input field-select" id="a11y-launch-target-env-leave-blank-for-single-environment-jobs">
            <option value="">— None —</option>
            <option value="dev">Dev</option><option value="qa">QA</option>
            <option value="staging">Staging</option><option value="prod">Prod</option>
          </select>
        </div>
      </div>
      <div class="flex justify-end gap-3 mt-6">
        <button @click="showLaunchSelectionModal = false" class="btn-secondary">Cancel</button>
        <button @click="launchSelection()" class="btn-primary">Launch</button>
      </div>
```

to:

```html
        <div>
          <label  class="field-label" for="a11y-launch-target-env-leave-blank-for-single-environment-jobs">Target Env (leave blank for single-environment jobs)</label>
          <select x-model="launchSelectionModal.target_env" class="field-input field-select" id="a11y-launch-target-env-leave-blank-for-single-environment-jobs">
            <option value="">— None —</option>
            <option value="dev">Dev</option><option value="qa">QA</option>
            <option value="staging">Staging</option><option value="prod">Prod</option>
          </select>
        </div>
        <div>
          <label class="field-label">Variable overrides (optional, one per line "name=value")</label>
          <textarea x-model="launchSelectionModal.variableOverridesRaw" rows="2" class="field-input font-mono text-xs" placeholder="run_date=2026-09-08" data-testid="launch-selection-variable-overrides-textarea"></textarea>
          <p class="text-xs text-muted mt-1">Overrides the Config-stored value for this run only. Leave blank to use the Config's value.</p>
        </div>
      </div>
      <div class="flex justify-end gap-3 mt-6">
        <button @click="showLaunchSelectionModal = false" class="btn-secondary">Cancel</button>
        <button @click="launchSelection()" class="btn-primary">Launch</button>
      </div>
```

- [ ] **Step 3: Syntax-check the JS file**

Run: `cd c:\atom && node --check frontend/features/launch.js`

Expected: no output (exit code 0)

- [ ] **Step 4: Commit**

```bash
cd c:\atom
git add frontend/features/launch.js frontend/partials/tab-launch.html
git commit -m "feat(launch-ui): add optional variable overrides to the Launch Selection modal"
```

---

### Task 13: Full test sweep and push

- [ ] **Step 1: Run the full unit test suite**

Run: `cd c:\atom && python -m pytest tests/unit/ -q`

Expected: all pass (no regressions from the new `variables` key in `config_snapshot`, `RunTrigger`, `JobDefinition` copies, etc.)

- [ ] **Step 2: Syntax-check every touched frontend file**

Run:
```bash
cd c:\atom
node --check frontend/features/config.js
node --check frontend/features/launch.js
```

Expected: no output from either

- [ ] **Step 3: Push to origin master**

```bash
cd c:\atom
git push origin master
```
