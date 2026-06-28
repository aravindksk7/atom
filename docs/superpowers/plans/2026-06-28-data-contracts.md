# Data Contracts + Ownership Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/api/contracts` resource that derives its expectations from existing jobs, tracks SLA-timed breaches that auto-resolve when the source job passes, and routes breach/resolve/escalate webhooks to the contract owner.

**Architecture:** Contracts are a separate entity (`contracts` table) that reference an existing `source_job`. The run executor calls a `ContractBreachChecker` after every job completes. A 15-minute APScheduler task escalates breaches that have exceeded `sla_hours`. Three new webhook event types (`contract.breached`, `contract.resolved`, `contract.escalated`) are fired through the existing `notify()` pipeline.

**Tech Stack:** Python, SQLAlchemy ORM, FastAPI, APScheduler, Alpine.js, SQLite (dev) / SQL Server (prod), pytest, Hypothesis

**Spec:** `docs/superpowers/specs/2026-06-28-data-contracts-design.md`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `etl_framework/repository/contract_models.py` | ORM models: `Contract`, `ContractVersion`, `ContractBreach` |
| Create | `etl_framework/repository/contract_repository.py` | DB operations: CRUD + breach lifecycle |
| Create | `api/services/contract_breach_checker.py` | Post-run hook: opens/resolves breaches + fires webhooks |
| Create | `api/routes/contracts.py` | FastAPI router: all `/api/contracts` endpoints |
| Modify | `etl_framework/repository/database.py` | Import contract_models; add 3 `CREATE TABLE IF NOT EXISTS` DDL blocks |
| Modify | `api/services/notifier.py` | Add 3 new event types to `EVENTS` set |
| Modify | `api/services/run_executor.py` | Call `ContractBreachChecker` in `_complete_run` |
| Modify | `api/services/scheduler.py` | Add 15-min escalation job on startup |
| Modify | `api/main.py` | Register `contracts.router` |
| Modify | `frontend/app.js` | Add Contracts tab: list panel + breach detail panel |
| Create | `tests/unit/test_contracts.py` | Unit tests: CRUD, breach open/resolve/escalate, derived views, version bump |
| Create | `tests/integration/test_contracts_integration.py` | Full lifecycle: create → fail → breach open → pass → breach resolved |
| Create | `tests/property/test_contracts_property.py` | Hypothesis invariants on breach math |
| Modify | `tests/unit/test_run_executor.py` | Add contract hook coverage |
| Modify | `tests/unit/test_notifier.py` | Add contract event type assertions |

---

## Task 1: ORM Models

**Files:**
- Create: `etl_framework/repository/contract_models.py`

- [ ] **Step 1: Create the models file**

```python
# etl_framework/repository/contract_models.py
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, Text, DateTime
from etl_framework.repository.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Contract(Base):
    __tablename__ = "contracts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    version = Column(String(50), nullable=False, default="1.0")
    source_job = Column(String(255), nullable=False, index=True)
    owner = Column(String(255), nullable=False)
    sla_hours = Column(Float, nullable=False)
    consumers = Column(Text, nullable=False, default="[]")  # JSON list stored as text
    breach_severity = Column(String(10), nullable=False, default="error")
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class ContractVersion(Base):
    __tablename__ = "contract_versions"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    version = Column(String(50), nullable=False)
    bump_type = Column(String(10), nullable=False)  # "minor" or "major"
    note = Column(Text, nullable=True)
    bumped_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ContractBreach(Base):
    __tablename__ = "contract_breaches"

    id = Column(Integer, primary_key=True, index=True)
    contract_id = Column(Integer, ForeignKey("contracts.id"), nullable=False, index=True)
    run_id = Column(String(36), nullable=False, index=True)
    breach_type = Column(String(30), nullable=False)  # dq_violation | sla_breach | schema_change
    opened_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolution_run_id = Column(String(36), nullable=True)
    escalated = Column(Boolean, nullable=False, default=False)
    escalated_at = Column(DateTime(timezone=True), nullable=True)
    duration_hours = Column(Float, nullable=True)  # computed on resolve
```

- [ ] **Step 2: Wire models into `database.py` init**

In `etl_framework/repository/database.py`, change the `init_db` function from:
```python
def init_db() -> None:
    from etl_framework.repository import models  # noqa: F401 — registers all ORM models
    Base.metadata.create_all(bind=engine)
```
To:
```python
def init_db() -> None:
    from etl_framework.repository import models  # noqa: F401 — registers all ORM models
    from etl_framework.repository import contract_models  # noqa: F401 — registers contract ORM models
    Base.metadata.create_all(bind=engine)
```

- [ ] **Step 3: Add DDL to `_ensure_compare_columns`**

At the end of the `with bind.begin() as conn:` block in `_ensure_compare_columns` (after the `schema_snapshots` block, before the closing brace), add:

```python
        # --- Data Contracts tables ---
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS contracts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name VARCHAR(255) NOT NULL UNIQUE, "
            "version VARCHAR(50) NOT NULL DEFAULT '1.0', "
            "source_job VARCHAR(255) NOT NULL, "
            "owner VARCHAR(255) NOT NULL, "
            "sla_hours REAL NOT NULL, "
            "consumers TEXT NOT NULL DEFAULT '[]', "
            "breach_severity VARCHAR(10) NOT NULL DEFAULT 'error', "
            "active BOOLEAN NOT NULL DEFAULT 1, "
            "created_at DATETIME, "
            "updated_at DATETIME)"
        ))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contracts_name ON contracts (name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_contracts_source_job ON contracts (source_job)"))

        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS contract_versions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "contract_id INTEGER NOT NULL REFERENCES contracts(id), "
            "version VARCHAR(50) NOT NULL, "
            "bump_type VARCHAR(10) NOT NULL, "
            "note TEXT, "
            "bumped_at DATETIME NOT NULL)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_contract_versions_contract_id "
            "ON contract_versions (contract_id)"
        ))

        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS contract_breaches ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "contract_id INTEGER NOT NULL REFERENCES contracts(id), "
            "run_id VARCHAR(36) NOT NULL, "
            "breach_type VARCHAR(30) NOT NULL, "
            "opened_at DATETIME NOT NULL, "
            "resolved_at DATETIME, "
            "resolution_run_id VARCHAR(36), "
            "escalated BOOLEAN NOT NULL DEFAULT 0, "
            "escalated_at DATETIME, "
            "duration_hours REAL)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_contract_breaches_contract_id "
            "ON contract_breaches (contract_id)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_contract_breaches_run_id "
            "ON contract_breaches (run_id)"
        ))
```

- [ ] **Step 4: Verify the models import cleanly**

```powershell
python -c "from etl_framework.repository.contract_models import Contract, ContractVersion, ContractBreach; print('OK')"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/contract_models.py etl_framework/repository/database.py
git commit -m "feat(contracts): add Contract, ContractVersion, ContractBreach ORM models and DDL"
```

---

## Task 2: ContractRepository — CRUD

**Files:**
- Create: `etl_framework/repository/contract_repository.py`
- Create: `tests/unit/test_contracts.py` (CRUD section)

- [ ] **Step 1: Write the failing CRUD tests**

```python
# tests/unit/test_contracts.py
from __future__ import annotations
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
import etl_framework.repository.contract_models  # noqa: F401
from etl_framework.repository.contract_repository import ContractRepository


def _db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _contract_data(**overrides) -> dict:
    base = {
        "name": "payments_v1",
        "source_job": "payments_reconciliation",
        "owner": "data-platform@co.com",
        "sla_hours": 4.0,
        "consumers": ["finance-team"],
        "breach_severity": "error",
        "version": "1.0",
    }
    base.update(overrides)
    return base


# --- CRUD ---

def test_create_and_get_contract():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    assert contract.id is not None
    fetched = repo.get("payments_v1")
    assert fetched is not None
    assert fetched.owner == "data-platform@co.com"
    assert fetched.sla_hours == 4.0


def test_list_contracts():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data(name="c1", source_job="job1"))
    repo.create(_contract_data(name="c2", source_job="job2"))
    result = repo.list()
    assert len(result) == 2


def test_update_contract():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    updated = repo.update("payments_v1", owner="new-owner@co.com", sla_hours=8.0)
    assert updated is not None
    assert updated.owner == "new-owner@co.com"
    assert updated.sla_hours == 8.0


def test_delete_contract_soft_deletes():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    deleted = repo.delete("payments_v1")
    assert deleted is True
    fetched = repo.get("payments_v1")
    assert fetched is None  # soft-deleted: active=False, get() only returns active


def test_create_duplicate_name_raises():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    with pytest.raises(Exception):
        repo.create(_contract_data())


def test_get_nonexistent_returns_none():
    db = _db()
    repo = ContractRepository(db)
    assert repo.get("does_not_exist") is None


def test_list_by_source_job():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data(name="c1", source_job="job_a"))
    repo.create(_contract_data(name="c2", source_job="job_a"))
    repo.create(_contract_data(name="c3", source_job="job_b"))
    result = repo.list_by_source_job("job_a")
    assert len(result) == 2
    assert all(c.source_job == "job_a" for c in result)
```

- [ ] **Step 2: Run to verify tests fail**

```powershell
python -m pytest tests/unit/test_contracts.py -q
```
Expected: `ImportError` or `ModuleNotFoundError` for `contract_repository`

- [ ] **Step 3: Implement ContractRepository CRUD**

```python
# etl_framework/repository/contract_repository.py
from __future__ import annotations
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from etl_framework.repository.contract_models import Contract, ContractVersion, ContractBreach


class ContractRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, data: dict) -> Contract:
        consumers = data.get("consumers", [])
        contract = Contract(
            name=data["name"],
            version=data.get("version", "1.0"),
            source_job=data["source_job"],
            owner=data["owner"],
            sla_hours=float(data["sla_hours"]),
            consumers=json.dumps(consumers) if isinstance(consumers, list) else consumers,
            breach_severity=data.get("breach_severity", "error"),
            active=True,
        )
        self._db.add(contract)
        self._db.commit()
        self._db.refresh(contract)
        return contract

    def get(self, name: str) -> Contract | None:
        return (
            self._db.query(Contract)
            .filter(Contract.name == name, Contract.active.is_(True))
            .first()
        )

    def list(self) -> list[Contract]:
        return (
            self._db.query(Contract)
            .filter(Contract.active.is_(True))
            .order_by(Contract.name)
            .all()
        )

    def list_by_source_job(self, job_name: str) -> list[Contract]:
        return (
            self._db.query(Contract)
            .filter(Contract.source_job == job_name, Contract.active.is_(True))
            .all()
        )

    def update(self, name: str, **kwargs) -> Contract | None:
        contract = self.get(name)
        if contract is None:
            return None
        for key, value in kwargs.items():
            if key == "consumers" and isinstance(value, list):
                value = json.dumps(value)
            if hasattr(contract, key):
                setattr(contract, key, value)
        contract.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        self._db.refresh(contract)
        return contract

    def delete(self, name: str) -> bool:
        contract = self.get(name)
        if contract is None:
            return False
        contract.active = False
        contract.updated_at = datetime.now(timezone.utc)
        self._db.commit()
        return True
```

- [ ] **Step 4: Run CRUD tests to verify they pass**

```powershell
python -m pytest tests/unit/test_contracts.py -q
```
Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/contract_repository.py tests/unit/test_contracts.py
git commit -m "feat(contracts): add ContractRepository CRUD + unit tests"
```

---

## Task 3: ContractRepository — Breach Lifecycle

**Files:**
- Modify: `etl_framework/repository/contract_repository.py`
- Modify: `tests/unit/test_contracts.py`

- [ ] **Step 1: Write failing breach lifecycle tests** (append to `tests/unit/test_contracts.py`)

```python
# --- Breach lifecycle ---

def test_open_breach_creates_record():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    breach = repo.open_breach(contract.id, "run-001", "dq_violation")
    assert breach is not None
    assert breach.contract_id == contract.id
    assert breach.run_id == "run-001"
    assert breach.breach_type == "dq_violation"
    assert breach.opened_at is not None
    assert breach.resolved_at is None


def test_open_breach_idempotent_when_already_open():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    breach1 = repo.open_breach(contract.id, "run-001", "dq_violation")
    breach2 = repo.open_breach(contract.id, "run-002", "dq_violation")
    assert breach2 is None  # second call returns None: breach already open


def test_resolve_breaches_for_job_sets_resolved_at_and_duration():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    resolved = repo.resolve_breaches_for_job("payments_reconciliation", "run-002")
    assert len(resolved) == 1
    breach, resolved_contract = resolved[0]
    assert breach.resolved_at is not None
    assert breach.resolution_run_id == "run-002"
    assert breach.duration_hours is not None
    assert breach.duration_hours >= 0
    assert resolved_contract.id == contract.id


def test_resolve_does_nothing_when_no_open_breach():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    resolved = repo.resolve_breaches_for_job("payments_reconciliation", "run-002")
    assert resolved == []


def test_escalate_overdue_marks_breach_escalated():
    from datetime import timedelta
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data(sla_hours=0.001))  # ~3.6 seconds SLA
    breach = repo.open_breach(contract.id, "run-001", "dq_violation")
    # backdate opened_at to simulate overdue
    from etl_framework.repository.contract_models import ContractBreach
    raw = db.query(ContractBreach).filter(ContractBreach.id == breach.id).first()
    raw.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    escalated = repo.escalate_overdue()
    assert len(escalated) == 1
    assert escalated[0][0].escalated is True
    assert escalated[0][0].escalated_at is not None


def test_escalate_does_not_re_escalate():
    from datetime import timedelta
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data(sla_hours=0.001))
    breach = repo.open_breach(contract.id, "run-001", "dq_violation")
    from etl_framework.repository.contract_models import ContractBreach
    raw = db.query(ContractBreach).filter(ContractBreach.id == breach.id).first()
    raw.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    repo.escalate_overdue()
    escalated_again = repo.escalate_overdue()
    assert escalated_again == []


def test_list_breaches_returns_history():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    repo.resolve_breaches_for_job("payments_reconciliation", "run-002")
    repo.open_breach(contract.id, "run-003", "schema_change")
    breaches = repo.list_breaches(contract.id)
    assert len(breaches) == 2


def test_list_open_breaches():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    open_breaches = repo.list_open_breaches(contract.id)
    assert len(open_breaches) == 1
    assert open_breaches[0].resolved_at is None


def test_get_status_ok_when_no_breach():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    status = repo.get_status("payments_v1")
    assert status["status"] == "OK"
    assert status["open_breach"] is None


def test_get_status_breached():
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data())
    repo.open_breach(contract.id, "run-001", "dq_violation")
    status = repo.get_status("payments_v1")
    assert status["status"] == "BREACHED"
    assert status["open_breach"]["breach_type"] == "dq_violation"


def test_get_status_overdue():
    from datetime import timedelta
    db = _db()
    repo = ContractRepository(db)
    contract = repo.create(_contract_data(sla_hours=0.001))
    breach = repo.open_breach(contract.id, "run-001", "dq_violation")
    from etl_framework.repository.contract_models import ContractBreach
    raw = db.query(ContractBreach).filter(ContractBreach.id == breach.id).first()
    raw.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
    raw.escalated = True
    db.commit()
    status = repo.get_status("payments_v1")
    assert status["status"] == "OVERDUE"


# --- Version bump ---

def test_bump_version_records_history():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    v = repo.bump_version("payments_v1", "minor", note="added freshness check")
    assert v.version == "1.1"
    assert v.bump_type == "minor"
    versions = repo.list_versions("payments_v1")
    assert len(versions) == 1
    assert versions[0].version == "1.1"


def test_bump_major_version():
    db = _db()
    repo = ContractRepository(db)
    repo.create(_contract_data())
    v = repo.bump_version("payments_v1", "major")
    assert v.version == "2.0"
```

- [ ] **Step 2: Run to verify tests fail**

```powershell
python -m pytest tests/unit/test_contracts.py -q -k "breach or resolve or escalate or status or version"
```
Expected: `AttributeError` — methods not yet defined

- [ ] **Step 3: Implement breach lifecycle methods** (append to `ContractRepository` in `contract_repository.py`)

```python
    # --- Breach lifecycle ---

    def open_breach(
        self, contract_id: int, run_id: str, breach_type: str
    ) -> ContractBreach | None:
        existing = (
            self._db.query(ContractBreach)
            .filter(
                ContractBreach.contract_id == contract_id,
                ContractBreach.resolved_at.is_(None),
            )
            .first()
        )
        if existing:
            return None  # idempotent: already open
        breach = ContractBreach(
            contract_id=contract_id,
            run_id=run_id,
            breach_type=breach_type,
        )
        self._db.add(breach)
        self._db.commit()
        self._db.refresh(breach)
        return breach

    def resolve_breaches_for_job(
        self, job_name: str, run_id: str
    ) -> list[tuple[ContractBreach, Contract]]:
        contracts = self.list_by_source_job(job_name)
        resolved: list[tuple[ContractBreach, Contract]] = []
        now = datetime.now(timezone.utc)
        for contract in contracts:
            open_breaches = (
                self._db.query(ContractBreach)
                .filter(
                    ContractBreach.contract_id == contract.id,
                    ContractBreach.resolved_at.is_(None),
                )
                .all()
            )
            for breach in open_breaches:
                opened = breach.opened_at
                if opened.tzinfo is None:
                    from datetime import timezone as _tz
                    opened = opened.replace(tzinfo=_tz.utc)
                duration = (now - opened).total_seconds() / 3600
                breach.resolved_at = now
                breach.resolution_run_id = run_id
                breach.duration_hours = round(duration, 4)
                self._db.commit()
                self._db.refresh(breach)
                resolved.append((breach, contract))
        return resolved

    def escalate_overdue(self) -> list[tuple[ContractBreach, Contract]]:
        from sqlalchemy import and_
        now = datetime.now(timezone.utc)
        open_breaches = (
            self._db.query(ContractBreach)
            .filter(
                ContractBreach.resolved_at.is_(None),
                ContractBreach.escalated.is_(False),
            )
            .all()
        )
        escalated: list[tuple[ContractBreach, Contract]] = []
        for breach in open_breaches:
            contract = self._db.get(Contract, breach.contract_id)
            if contract is None or not contract.active:
                continue
            opened = breach.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            elapsed_hours = (now - opened).total_seconds() / 3600
            if elapsed_hours >= contract.sla_hours:
                breach.escalated = True
                breach.escalated_at = now
                self._db.commit()
                self._db.refresh(breach)
                escalated.append((breach, contract))
        return escalated

    def list_breaches(self, contract_id: int) -> list[ContractBreach]:
        return (
            self._db.query(ContractBreach)
            .filter(ContractBreach.contract_id == contract_id)
            .order_by(ContractBreach.opened_at.desc())
            .all()
        )

    def list_open_breaches(self, contract_id: int) -> list[ContractBreach]:
        return (
            self._db.query(ContractBreach)
            .filter(
                ContractBreach.contract_id == contract_id,
                ContractBreach.resolved_at.is_(None),
            )
            .all()
        )

    def get_status(self, name: str) -> dict:
        contract = self.get(name)
        if contract is None:
            return {"status": "NOT_FOUND", "open_breach": None}
        open_breach = (
            self._db.query(ContractBreach)
            .filter(
                ContractBreach.contract_id == contract.id,
                ContractBreach.resolved_at.is_(None),
            )
            .order_by(ContractBreach.opened_at.desc())
            .first()
        )
        if open_breach is None:
            return {"status": "OK", "open_breach": None}
        status = "OVERDUE" if open_breach.escalated else "BREACHED"
        return {
            "status": status,
            "open_breach": {
                "id": open_breach.id,
                "breach_type": open_breach.breach_type,
                "run_id": open_breach.run_id,
                "opened_at": open_breach.opened_at.isoformat(),
                "escalated": open_breach.escalated,
            },
        }

    # --- Version management ---

    def bump_version(
        self, name: str, bump_type: str, note: str | None = None
    ) -> ContractVersion:
        contract = self.get(name)
        if contract is None:
            raise ValueError(f"Contract '{name}' not found")
        major, minor = (contract.version or "1.0").split(".")
        if bump_type == "major":
            new_version = f"{int(major) + 1}.0"
        else:
            new_version = f"{major}.{int(minor) + 1}"
        contract.version = new_version
        contract.updated_at = datetime.now(timezone.utc)
        cv = ContractVersion(
            contract_id=contract.id,
            version=new_version,
            bump_type=bump_type,
            note=note,
        )
        self._db.add(cv)
        self._db.commit()
        self._db.refresh(cv)
        return cv

    def list_versions(self, name: str) -> list[ContractVersion]:
        contract = self.get(name)
        if contract is None:
            return []
        return (
            self._db.query(ContractVersion)
            .filter(ContractVersion.contract_id == contract.id)
            .order_by(ContractVersion.bumped_at.desc())
            .all()
        )
```

- [ ] **Step 4: Run all contract tests**

```powershell
python -m pytest tests/unit/test_contracts.py -q
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/repository/contract_repository.py tests/unit/test_contracts.py
git commit -m "feat(contracts): add breach lifecycle and version bump to ContractRepository"
```

---

## Task 4: New Webhook Event Types

**Files:**
- Modify: `api/services/notifier.py`
- Modify: `tests/unit/test_notifier.py`

- [ ] **Step 1: Write failing tests** (append to `tests/unit/test_notifier.py`)

```python
# --- Contract events ---

def test_contract_breached_is_valid_event():
    from api.services.notifier import EVENTS
    assert "contract.breached" in EVENTS


def test_contract_resolved_is_valid_event():
    from api.services.notifier import EVENTS
    assert "contract.resolved" in EVENTS


def test_contract_escalated_is_valid_event():
    from api.services.notifier import EVENTS
    assert "contract.escalated" in EVENTS


def test_contract_event_passes_through_status_to_event():
    from api.services.notifier import _status_to_event
    assert _status_to_event("contract.breached") == ["contract.breached"]
    assert _status_to_event("contract.resolved") == ["contract.resolved"]
    assert _status_to_event("contract.escalated") == ["contract.escalated"]
```

- [ ] **Step 2: Run to verify they fail**

```powershell
python -m pytest tests/unit/test_notifier.py -q -k "contract"
```
Expected: `AssertionError` — events not in EVENTS

- [ ] **Step 3: Add events to notifier**

In `api/services/notifier.py`, change the `EVENTS` set from:
```python
EVENTS = {
    "run.passed",
    "run.failed",
    "run.slow",
    "run.error",
    "run.completed",
    "run.held",
    "run.cancelled",
}
```
To:
```python
EVENTS = {
    "run.passed",
    "run.failed",
    "run.slow",
    "run.error",
    "run.completed",
    "run.held",
    "run.cancelled",
    "contract.breached",
    "contract.resolved",
    "contract.escalated",
}
```

- [ ] **Step 4: Run notifier tests**

```powershell
python -m pytest tests/unit/test_notifier.py -q
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/notifier.py tests/unit/test_notifier.py
git commit -m "feat(contracts): add contract.breached/resolved/escalated webhook event types"
```

---

## Task 5: ContractBreachChecker Service

**Files:**
- Create: `api/services/contract_breach_checker.py`
- Modify: `tests/unit/test_run_executor.py`

- [ ] **Step 1: Write failing test for breach checker** (append to `tests/unit/test_run_executor.py`)

```python
# --- Contract breach checker ---

def test_run_executor_opens_contract_breach_on_failure():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    import etl_framework.repository.contract_models  # noqa: F401
    from etl_framework.repository.contract_repository import ContractRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine)

    RunRepository(db).create_run("run-breach-001", "dev", "prod", {})
    JobRepository(db).create({
        "name": "orders",
        "description": "Orders",
        "tags": [],
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": {
            "source_rows": [{"id": 1, "amount": 10.0}],
            "target_rows": [{"id": 1, "amount": 99.0}],  # mismatch → FAILED
        },
        "enabled": True,
    })
    # Create a contract pointing at the job
    ContractRepository(db).create({
        "name": "orders_contract",
        "source_job": "orders",
        "owner": "owner@co.com",
        "sla_hours": 4.0,
    })

    RunExecutor(
        db=db,
        run_id="run-breach-001",
        source_env="dev",
        target_env="prod",
        job_sequence=["orders"],
        run_settings=RunSettings(),
        config_snapshot={},
    ).execute()

    breaches = ContractRepository(db).list_open_breaches(
        ContractRepository(db).get("orders_contract").id
    )
    assert len(breaches) == 1
    assert breaches[0].breach_type == "dq_violation"
```

- [ ] **Step 2: Run to verify it fails**

```powershell
python -m pytest tests/unit/test_run_executor.py::test_run_executor_opens_contract_breach_on_failure -v
```
Expected: FAIL — breach not created (breach checker not wired yet)

- [ ] **Step 3: Create `api/services/contract_breach_checker.py`**

```python
# api/services/contract_breach_checker.py
"""Post-run hook that opens or resolves contract breaches based on job outcomes."""
from __future__ import annotations
import logging
from sqlalchemy.orm import Session

logger = logging.getLogger("api.contract_breach_checker")


def _breach_type(job_status: str, test_type: str) -> str:
    if test_type == "freshness":
        return "sla_breach"
    if test_type == "schema_snapshot":
        return "schema_change"
    return "dq_violation"


class ContractBreachChecker:
    def __init__(self, db: Session) -> None:
        self._db = db

    def check(self, job_name: str, job_status: str, run_id: str, test_type: str = "reconciliation") -> None:
        """Call after each job result is persisted. Opens or resolves contract breaches."""
        try:
            from etl_framework.repository.contract_repository import ContractRepository
            repo = ContractRepository(self._db)
            if job_status in ("FAILED", "ERROR"):
                self._open(repo, job_name, run_id, test_type)
            elif job_status == "PASSED":
                self._resolve(repo, job_name, run_id)
        except Exception:
            logger.exception("ContractBreachChecker.check failed for job=%s", job_name)

    def _open(self, repo, job_name: str, run_id: str, test_type: str) -> None:
        contracts = repo.list_by_source_job(job_name)
        breach_type = _breach_type("FAILED", test_type)
        for contract in contracts:
            breach = repo.open_breach(contract.id, run_id, breach_type)
            if breach:
                self._notify("contract.breached", breach.run_id, {
                    "contract_name": contract.name,
                    "source_job": contract.source_job,
                    "breach_type": breach.breach_type,
                    "owner": contract.owner,
                })

    def _resolve(self, repo, job_name: str, run_id: str) -> None:
        resolved = repo.resolve_breaches_for_job(job_name, run_id)
        for breach, contract in resolved:
            self._notify("contract.resolved", run_id, {
                "contract_name": contract.name,
                "source_job": contract.source_job,
                "duration_hours": breach.duration_hours,
                "met_sla": (breach.duration_hours or 0) <= contract.sla_hours,
                "owner": contract.owner,
            })

    def _notify(self, event: str, run_id: str, extra: dict) -> None:
        try:
            from etl_framework.repository.repository import NotificationRepository
            from api.services.notifier import notify
            hooks = NotificationRepository(self._db).list_enabled_for_event(event)
            notify(run_id, event, extra=extra, hooks=hooks, db_session=self._db)
        except Exception:
            pass  # never let notification failure affect the run
```

- [ ] **Step 4: Wire ContractBreachChecker into `_complete_run`**

In `api/services/run_executor.py`, after line `self._fire_webhooks(final_status, passed=passed, failed=failed, error=error)` (end of `_complete_run`), add:

```python
        self._check_contracts(states)
```

Then add the new method immediately after `_fire_webhooks`:

```python
    def _check_contracts(self, states: list[TestCaseState]) -> None:
        try:
            from api.services.contract_breach_checker import ContractBreachChecker
            checker = ContractBreachChecker(self._db)
            for state in states:
                job_status = state.status.value if hasattr(state.status, "value") else str(state.status)
                checker.check(state.name, job_status, self._run_id, state.test_type)
        except Exception:
            pass  # never let contract check affect the run
```

- [ ] **Step 5: Run the breach test**

```powershell
python -m pytest tests/unit/test_run_executor.py -q
```
Expected: all tests PASS (new test included)

- [ ] **Step 6: Commit**

```bash
git add api/services/contract_breach_checker.py api/services/run_executor.py tests/unit/test_run_executor.py
git commit -m "feat(contracts): add ContractBreachChecker and wire into RunExecutor._complete_run"
```

---

## Task 6: Escalation Scheduler Job

**Files:**
- Modify: `api/services/scheduler.py`

- [ ] **Step 1: Add escalation job to `start()` in `api/services/scheduler.py`**

At the end of the `start()` function, before the closing `finally: db.close()`, add:

```python
        if _APSCHEDULER_AVAILABLE and _scheduler is not None:
            _scheduler.add_job(
                _escalate_contracts,
                "interval",
                minutes=15,
                id="contract_escalation",
                replace_existing=True,
                misfire_grace_time=120,
            )
            logger.info("Contract escalation job scheduled every 15 minutes.")
```

Then add the `_escalate_contracts` function near the top of the module (after `_run_schedule`):

```python
def _escalate_contracts() -> None:
    """Called by APScheduler every 15 minutes to escalate overdue contract breaches."""
    from etl_framework.repository.database import SessionLocal
    from etl_framework.repository.contract_repository import ContractRepository

    db = SessionLocal()
    try:
        repo = ContractRepository(db)
        escalated = repo.escalate_overdue()
        for breach, contract in escalated:
            _notify_escalation(db, breach, contract)
        if escalated:
            logger.info("Escalated %d overdue contract breach(es).", len(escalated))
    except Exception as exc:
        logger.exception("Contract escalation job failed: %s", exc)
    finally:
        db.close()


def _notify_escalation(db, breach, contract) -> None:
    try:
        from etl_framework.repository.repository import NotificationRepository
        from api.services.notifier import notify
        from datetime import datetime, timezone
        opened = breach.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        hours_overdue = (datetime.now(timezone.utc) - opened).total_seconds() / 3600 - contract.sla_hours
        hooks = NotificationRepository(db).list_enabled_for_event("contract.escalated")
        notify(
            breach.run_id,
            "contract.escalated",
            extra={
                "contract_name": contract.name,
                "source_job": contract.source_job,
                "owner": contract.owner,
                "hours_overdue": round(max(hours_overdue, 0), 2),
            },
            hooks=hooks,
            db_session=db,
        )
    except Exception:
        pass
```

- [ ] **Step 2: Verify the scheduler module still imports cleanly**

```powershell
python -c "from api.services import scheduler; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add api/services/scheduler.py
git commit -m "feat(contracts): add 15-min escalation APScheduler job for overdue breaches"
```

---

## Task 7: API Routes

**Files:**
- Create: `api/routes/contracts.py`
- Modify: `api/main.py`

- [ ] **Step 1: Create `api/routes/contracts.py`**

```python
# api/routes/contracts.py
from __future__ import annotations
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.audit_service import AuditService
from etl_framework.repository.contract_repository import ContractRepository

router = APIRouter(tags=["contracts"])


class ContractCreate(BaseModel):
    name: str
    source_job: str
    owner: str
    sla_hours: float
    consumers: list[str] = []
    breach_severity: str = "error"
    version: str = "1.0"


class ContractUpdate(BaseModel):
    owner: str | None = None
    sla_hours: float | None = None
    consumers: list[str] | None = None
    breach_severity: str | None = None


class ContractOut(BaseModel):
    id: int
    name: str
    version: str
    source_job: str
    owner: str
    sla_hours: float
    consumers: str
    breach_severity: str
    active: bool
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class VersionBump(BaseModel):
    bump: str = "minor"  # "minor" or "major"
    note: str | None = None


@router.get("", response_model=list[ContractOut])
def list_contracts(db: Session = Depends(get_session)):
    return ContractRepository(db).list()


@router.post("", response_model=ContractOut, status_code=201)
def create_contract(body: ContractCreate, request: Request, db: Session = Depends(get_session)):
    repo = ContractRepository(db)
    if repo.get(body.name):
        raise HTTPException(status_code=409, detail=f"Contract '{body.name}' already exists")
    # Validate source_job exists
    from etl_framework.repository.repository import JobRepository
    jobs = {j.name for j in JobRepository(db).list()}
    if body.source_job not in jobs:
        raise HTTPException(status_code=422, detail=f"source_job '{body.source_job}' not found")
    contract = repo.create(body.model_dump())
    AuditService(db).log(request, "contract.created", "contract", contract.id, {"name": contract.name})
    return contract


@router.get("/{name}", response_model=ContractOut)
def get_contract(name: str, db: Session = Depends(get_session)):
    contract = ContractRepository(db).get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    return contract


@router.put("/{name}", response_model=ContractOut)
def update_contract(name: str, body: ContractUpdate, request: Request, db: Session = Depends(get_session)):
    repo = ContractRepository(db)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    contract = repo.update(name, **updates)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    AuditService(db).log(request, "contract.updated", "contract", contract.id, updates)
    return contract


@router.delete("/{name}", status_code=204)
def delete_contract(name: str, request: Request, db: Session = Depends(get_session)):
    repo = ContractRepository(db)
    contract = repo.get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    repo.delete(name)
    AuditService(db).log(request, "contract.deleted", "contract", contract.id, {"name": name})


@router.get("/{name}/status")
def get_status(name: str, db: Session = Depends(get_session)):
    status = ContractRepository(db).get_status(name)
    if status["status"] == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="Contract not found")
    return status


@router.get("/{name}/breaches")
def list_breaches(name: str, db: Session = Depends(get_session)):
    repo = ContractRepository(db)
    contract = repo.get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    breaches = repo.list_breaches(contract.id)
    from etl_framework.repository.contract_models import ContractBreach
    return [
        {
            "id": b.id,
            "breach_type": b.breach_type,
            "run_id": b.run_id,
            "opened_at": b.opened_at.isoformat(),
            "resolved_at": b.resolved_at.isoformat() if b.resolved_at else None,
            "resolution_run_id": b.resolution_run_id,
            "escalated": b.escalated,
            "escalated_at": b.escalated_at.isoformat() if b.escalated_at else None,
            "duration_hours": b.duration_hours,
            "met_sla": (b.duration_hours <= contract.sla_hours) if b.duration_hours is not None else None,
        }
        for b in breaches
    ]


@router.get("/{name}/breaches/open")
def list_open_breaches(name: str, db: Session = Depends(get_session)):
    repo = ContractRepository(db)
    contract = repo.get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    breaches = repo.list_open_breaches(contract.id)
    return [
        {
            "id": b.id,
            "breach_type": b.breach_type,
            "run_id": b.run_id,
            "opened_at": b.opened_at.isoformat(),
            "escalated": b.escalated,
        }
        for b in breaches
    ]


@router.get("/{name}/rules")
def get_rules(name: str, db: Session = Depends(get_session)):
    """Derive DQ rules live from the source_job — nothing stored on the contract."""
    repo = ContractRepository(db)
    contract = repo.get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    from etl_framework.repository.repository import JobRepository
    job = JobRepository(db).get(contract.source_job)
    if job is None:
        return {"source_job": contract.source_job, "rules": [], "note": "source_job not found"}
    rules = (job.params or {}).get("rules", [])
    return {"source_job": contract.source_job, "rules": rules}


@router.get("/{name}/schema")
def get_schema(name: str, db: Session = Depends(get_session)):
    """Return the latest schema snapshot from the source_job."""
    repo = ContractRepository(db)
    contract = repo.get(name)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    from etl_framework.repository.repository import SchemaSnapshotRepository
    latest = SchemaSnapshotRepository(db).get_latest(contract.source_job, "both")
    if latest is None:
        return {"source_job": contract.source_job, "columns": [], "note": "no snapshot found"}
    import json
    columns = json.loads(latest.columns) if isinstance(latest.columns, str) else latest.columns
    return {"source_job": contract.source_job, "captured_at": latest.captured_at.isoformat(), "columns": columns}


@router.post("/{name}/version")
def bump_version(name: str, body: VersionBump, request: Request, db: Session = Depends(get_session)):
    if body.bump not in ("minor", "major"):
        raise HTTPException(status_code=422, detail="bump must be 'minor' or 'major'")
    repo = ContractRepository(db)
    if repo.get(name) is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    version = repo.bump_version(name, body.bump, body.note)
    AuditService(db).log(request, "contract.version_bumped", "contract", name, {"version": version.version})
    return {"version": version.version, "bump_type": version.bump_type, "bumped_at": version.bumped_at.isoformat()}


@router.get("/{name}/versions")
def list_versions(name: str, db: Session = Depends(get_session)):
    repo = ContractRepository(db)
    if repo.get(name) is None:
        raise HTTPException(status_code=404, detail="Contract not found")
    versions = repo.list_versions(name)
    return [
        {"version": v.version, "bump_type": v.bump_type, "note": v.note, "bumped_at": v.bumped_at.isoformat()}
        for v in versions
    ]
```

- [ ] **Step 2: Register the router in `api/main.py`**

Add the import line alongside the other route imports:
```python
from api.routes import contracts as contracts_routes
```

Add the router registration after `schema_snapshot_routes`:
```python
app.include_router(contracts_routes.router, prefix="/api/contracts")
```

- [ ] **Step 3: Verify the app starts cleanly**

```powershell
python -c "from api.main import app; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Spot-check the OpenAPI schema includes /api/contracts**

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8001 &
Start-Sleep 2
Invoke-RestMethod http://127.0.0.1:8001/api/health
```
Then open `http://127.0.0.1:8001/docs` and confirm the `contracts` tag appears.

- [ ] **Step 5: Commit**

```bash
git add api/routes/contracts.py api/main.py
git commit -m "feat(contracts): add /api/contracts router and register in main.py"
```

---

## Task 8: Integration Tests

**Files:**
- Create: `tests/integration/test_contracts_integration.py`

- [ ] **Step 1: Write and run integration tests**

```python
# tests/integration/test_contracts_integration.py
"""Full lifecycle contract tests against an in-memory SQLite database."""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
import etl_framework.repository.contract_models  # noqa: F401
from etl_framework.repository.repository import JobRepository, RunRepository
from etl_framework.repository.contract_repository import ContractRepository
from api.services.run_executor import RunExecutor
from api.schemas import RunSettings


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session, job_params: dict) -> tuple[ContractRepository, str]:
    """Seed: one job + one contract pointing at it. Returns (repo, job_name)."""
    JobRepository(db).create({
        "name": "orders",
        "description": "Orders",
        "tags": [],
        "job_type": "reconciliation",
        "query": "SELECT * FROM orders",
        "key_columns": ["id"],
        "exclude_columns": [],
        "source_env": None,
        "target_env": None,
        "params": job_params,
        "enabled": True,
    })
    repo = ContractRepository(db)
    repo.create({
        "name": "orders_contract",
        "source_job": "orders",
        "owner": "owner@co.com",
        "sla_hours": 4.0,
    })
    return repo, "orders"


def _run(db: Session, run_id: str) -> None:
    RunRepository(db).create_run(run_id, "dev", "prod", {})
    RunExecutor(
        db=db,
        run_id=run_id,
        source_env="dev",
        target_env="prod",
        job_sequence=["orders"],
        run_settings=RunSettings(),
        config_snapshot={},
    ).execute()


def test_full_lifecycle_breach_then_resolve():
    db = _db()
    repo, _ = _seed(db, {
        "source_rows": [{"id": 1, "amount": 10.0}],
        "target_rows": [{"id": 1, "amount": 99.0}],  # mismatch → FAILED
    })
    contract = repo.get("orders_contract")

    # Run 1: job fails → breach opens
    _run(db, "run-001")
    open_b = repo.list_open_breaches(contract.id)
    assert len(open_b) == 1
    assert open_b[0].breach_type == "dq_violation"

    # Run 2: job passes → breach auto-resolves
    # Update the job so it passes this time
    from etl_framework.repository.models import SavedJob
    job = db.query(SavedJob).filter(SavedJob.name == "orders").first()
    job.params = {
        "source_rows": [{"id": 1, "amount": 10.0}],
        "target_rows": [{"id": 1, "amount": 10.0}],
    }
    db.commit()

    _run(db, "run-002")
    open_b_after = repo.list_open_breaches(contract.id)
    assert open_b_after == []

    all_b = repo.list_breaches(contract.id)
    assert len(all_b) == 1
    assert all_b[0].resolved_at is not None
    assert all_b[0].resolution_run_id == "run-002"
    assert all_b[0].duration_hours is not None
    assert all_b[0].duration_hours >= 0


def test_breach_does_not_open_twice_for_same_contract():
    db = _db()
    repo, _ = _seed(db, {
        "source_rows": [{"id": 1, "amount": 10.0}],
        "target_rows": [{"id": 1, "amount": 99.0}],
    })
    contract = repo.get("orders_contract")

    _run(db, "run-001")
    _run(db, "run-002")  # second fail: should NOT open a second breach

    open_b = repo.list_open_breaches(contract.id)
    assert len(open_b) == 1  # still only one open breach


def test_escalation_marks_breach_overdue():
    from datetime import timedelta, timezone
    from etl_framework.repository.contract_models import ContractBreach

    db = _db()
    repo, _ = _seed(db, {
        "source_rows": [{"id": 1, "amount": 10.0}],
        "target_rows": [{"id": 1, "amount": 99.0}],
    })
    contract = repo.get("orders_contract")
    _run(db, "run-001")

    # Backdate the breach so it's past SLA
    from datetime import datetime
    breach = db.query(ContractBreach).first()
    breach.opened_at = datetime.now(timezone.utc) - timedelta(hours=5)
    db.commit()

    escalated = repo.escalate_overdue()
    assert len(escalated) == 1
    status = repo.get_status("orders_contract")
    assert status["status"] == "OVERDUE"
```

- [ ] **Step 2: Run integration tests**

```powershell
python -m pytest tests/integration/test_contracts_integration.py -v
```
Expected: all 3 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_contracts_integration.py
git commit -m "test(contracts): add full lifecycle integration tests"
```

---

## Task 9: Property-Based Tests

**Files:**
- Create: `tests/property/test_contracts_property.py`

- [ ] **Step 1: Write and run property tests**

```python
# tests/property/test_contracts_property.py
"""Hypothesis invariants for contract breach mathematics."""
from __future__ import annotations
import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
import etl_framework.repository.contract_models  # noqa: F401
from etl_framework.repository.contract_repository import ContractRepository


def _db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _repo_with_contract(db: Session, sla_hours: float) -> tuple[ContractRepository, int]:
    repo = ContractRepository(db)
    contract = repo.create({
        "name": "prop_contract",
        "source_job": "prop_job",
        "owner": "owner@co.com",
        "sla_hours": sla_hours,
    })
    return repo, contract.id


@given(sla_hours=st.floats(min_value=0.01, max_value=720.0, allow_nan=False))
@settings(max_examples=50)
def test_duration_hours_always_nonnegative(sla_hours):
    """Resolved breach duration must never be negative."""
    db = _db()
    repo, contract_id = _repo_with_contract(db, sla_hours)
    repo.open_breach(contract_id, "run-001", "dq_violation")
    resolved = repo.resolve_breaches_for_job("prop_job", "run-002")
    assert len(resolved) == 1
    breach, _ = resolved[0]
    assert breach.duration_hours is not None
    assert breach.duration_hours >= 0


@given(
    sla_hours=st.floats(min_value=0.01, max_value=720.0, allow_nan=False),
)
@settings(max_examples=50)
def test_met_sla_consistent_with_duration(sla_hours):
    """met_sla == (duration_hours <= sla_hours) always."""
    db = _db()
    repo, contract_id = _repo_with_contract(db, sla_hours)
    contract = db.get(Contract, contract_id)
    repo.open_breach(contract_id, "run-001", "dq_violation")
    resolved = repo.resolve_breaches_for_job("prop_job", "run-002")
    breach, _ = resolved[0]
    met_sla = breach.duration_hours <= contract.sla_hours
    assert met_sla == (breach.duration_hours <= sla_hours)


@given(name=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"))))
@settings(max_examples=30)
def test_nonexistent_source_job_can_still_create_contract(name):
    """ContractRepository.create does not validate source_job existence (that's the route's job)."""
    db = _db()
    repo = ContractRepository(db)
    assume(len(name.strip()) > 0)
    contract = repo.create({
        "name": name,
        "source_job": "nonexistent_job_" + name,
        "owner": "owner@co.com",
        "sla_hours": 4.0,
    })
    assert contract.id is not None
    assert repo.get(name) is not None


def test_no_open_breach_has_resolved_at_set():
    """An open breach must always have resolved_at=None."""
    db = _db()
    repo, contract_id = _repo_with_contract(db, 4.0)
    repo.open_breach(contract_id, "run-001", "dq_violation")
    open_breaches = repo.list_open_breaches(contract_id)
    for b in open_breaches:
        assert b.resolved_at is None


def test_resolved_breach_always_has_duration():
    """A resolved breach must always have duration_hours set."""
    db = _db()
    repo, contract_id = _repo_with_contract(db, 4.0)
    repo.open_breach(contract_id, "run-001", "dq_violation")
    resolved = repo.resolve_breaches_for_job("prop_job", "run-002")
    for breach, _ in resolved:
        assert breach.duration_hours is not None
        assert breach.resolved_at is not None
```

- [ ] **Step 2: Run property tests**

```powershell
python -m pytest tests/property/test_contracts_property.py -v
```
Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/property/test_contracts_property.py
git commit -m "test(contracts): add Hypothesis property-based tests for breach math invariants"
```

---

## Task 10: Frontend — Contracts Tab

**Files:**
- Modify: `frontend/app.js`

- [ ] **Step 1: Add Alpine.js data properties for contracts**

Find the `data()` function in `frontend/app.js` (the top-level Alpine component). Add these properties to the returned object:

```javascript
// Contracts tab
contracts: [],
selectedContract: null,
contractBreaches: [],
contractsLoading: false,
newContract: { name: '', source_job: '', owner: '', sla_hours: 4, consumers: '', breach_severity: 'error', version: '1.0' },
contractError: '',
```

- [ ] **Step 2: Add contract methods to the Alpine component**

In the `methods` section (or inline in the component), add:

```javascript
async loadContracts() {
    this.contractsLoading = true;
    try {
        const resp = await fetch('/api/contracts', { headers: this._authHeaders() });
        if (!resp.ok) throw new Error(await resp.text());
        this.contracts = await resp.json();
    } catch (e) {
        this.contractError = e.message;
    } finally {
        this.contractsLoading = false;
    }
},

async selectContract(contract) {
    this.selectedContract = contract;
    const resp = await fetch(`/api/contracts/${contract.name}/breaches`, { headers: this._authHeaders() });
    if (resp.ok) this.contractBreaches = await resp.json();
},

contractStatusClass(name) {
    const s = this.contracts.find(c => c.name === name);
    if (!s) return '';
    const status = s._status || 'OK';
    return status === 'OK' ? 'badge-ok' : status === 'OVERDUE' ? 'badge-overdue' : 'badge-breached';
},

async createContract() {
    const body = { ...this.newContract };
    body.consumers = body.consumers.split(',').map(s => s.trim()).filter(Boolean);
    body.sla_hours = parseFloat(body.sla_hours);
    const resp = await fetch('/api/contracts', {
        method: 'POST',
        headers: { ...this._authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (resp.ok) {
        this.newContract = { name: '', source_job: '', owner: '', sla_hours: 4, consumers: '', breach_severity: 'error', version: '1.0' };
        await this.loadContracts();
    } else {
        this.contractError = (await resp.json()).detail || 'Failed to create contract';
    }
},

async deleteContract(name) {
    if (!confirm(`Delete contract "${name}"?`)) return;
    await fetch(`/api/contracts/${name}`, { method: 'DELETE', headers: this._authHeaders() });
    this.selectedContract = null;
    this.contractBreaches = [];
    await this.loadContracts();
},
```

- [ ] **Step 3: Add the Contracts tab to the nav**

Find the tab navigation in `frontend/app.js` (the `x-show` or tab switcher section). Add a Contracts tab button alongside existing tabs:

```html
<button
  :class="activeTab === 'contracts' ? 'tab-active' : ''"
  @click="activeTab = 'contracts'; loadContracts()">
  Contracts
</button>
```

- [ ] **Step 4: Add the Contracts tab panel**

Find where tab panels are rendered (the `x-show="activeTab === '...'"` divs). Add:

```html
<div x-show="activeTab === 'contracts'" x-cloak>
  <div style="display:flex;gap:16px;height:70vh">

    <!-- Left: contract list -->
    <div style="width:280px;overflow-y:auto;background:#1e293b;border-radius:8px;padding:12px">
      <div style="font-size:11px;color:#94a3b8;margin-bottom:8px;text-transform:uppercase">
        Contracts (<span x-text="contracts.length"></span>)
      </div>

      <!-- Create form -->
      <div style="margin-bottom:12px;background:#0f172a;padding:8px;border-radius:6px">
        <input x-model="newContract.name" placeholder="Contract name" class="inp" style="width:100%;margin-bottom:4px">
        <select x-model="newContract.source_job" class="inp" style="width:100%;margin-bottom:4px">
          <option value="">Source job…</option>
          <template x-for="j in jobs" :key="j.name">
            <option :value="j.name" x-text="j.name"></option>
          </template>
        </select>
        <input x-model="newContract.owner" placeholder="Owner email" class="inp" style="width:100%;margin-bottom:4px">
        <input x-model.number="newContract.sla_hours" type="number" placeholder="SLA hours" class="inp" style="width:100%;margin-bottom:4px">
        <button @click="createContract()" class="btn-primary" style="width:100%;font-size:12px">+ Add Contract</button>
        <p x-show="contractError" x-text="contractError" style="color:#f87171;font-size:11px;margin-top:4px"></p>
      </div>

      <template x-if="contractsLoading">
        <p style="color:#64748b;font-size:12px">Loading…</p>
      </template>
      <template x-for="c in contracts" :key="c.name">
        <div
          @click="selectContract(c)"
          :style="selectedContract && selectedContract.name === c.name ? 'background:#1e40af' : ''"
          style="display:flex;justify-content:space-between;align-items:center;padding:6px 8px;border-radius:6px;cursor:pointer;margin-bottom:4px;border:1px solid #334155">
          <span x-text="c.name" style="color:#e2e8f0;font-size:13px"></span>
          <span
            x-text="c._status || 'OK'"
            :style="(c._status || 'OK') === 'OK' ? 'background:#16a34a' : (c._status === 'OVERDUE' ? 'background:#d97706' : 'background:#dc2626')"
            style="color:#fff;padding:1px 8px;border-radius:10px;font-size:11px">
          </span>
        </div>
      </template>
    </div>

    <!-- Right: breach detail -->
    <div style="flex:1;overflow-y:auto;background:#1e293b;border-radius:8px;padding:16px">
      <template x-if="!selectedContract">
        <p style="color:#64748b">Select a contract to view breach details.</p>
      </template>
      <template x-if="selectedContract">
        <div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <h3 x-text="selectedContract.name" style="color:#e2e8f0;margin:0"></h3>
            <button @click="deleteContract(selectedContract.name)" style="background:#dc2626;color:#fff;border:none;border-radius:4px;padding:4px 10px;cursor:pointer;font-size:12px">Delete</button>
          </div>
          <div style="font-size:12px;color:#94a3b8;line-height:2.2;margin-bottom:16px">
            <div>Owner: <span x-text="selectedContract.owner" style="color:#e2e8f0"></span></div>
            <div>Source job: <span x-text="selectedContract.source_job" style="color:#7dd3fc"></span></div>
            <div>SLA: <span x-text="selectedContract.sla_hours + 'h'" style="color:#e2e8f0"></span></div>
            <div>Version: <span x-text="selectedContract.version" style="color:#e2e8f0"></span></div>
          </div>

          <div style="font-size:11px;color:#94a3b8;text-transform:uppercase;margin-bottom:8px">Breach History</div>
          <template x-if="contractBreaches.length === 0">
            <p style="color:#64748b;font-size:13px">No breaches recorded.</p>
          </template>
          <template x-for="b in contractBreaches" :key="b.id">
            <div style="background:#0f172a;border-radius:6px;padding:10px;margin-bottom:8px;font-size:12px">
              <div style="display:flex;justify-content:space-between;margin-bottom:4px">
                <span x-text="b.breach_type" style="color:#fbbf24;font-weight:600"></span>
                <span
                  x-text="b.resolved_at ? (b.met_sla ? '✓ Within SLA' : '✗ SLA missed') : (b.escalated ? '⚠ OVERDUE' : 'OPEN')"
                  :style="b.resolved_at ? (b.met_sla ? 'color:#86efac' : 'color:#f87171') : 'color:#fbbf24'">
                </span>
              </div>
              <div style="color:#64748b">
                Opened: <span x-text="new Date(b.opened_at).toLocaleString()" style="color:#94a3b8"></span>
              </div>
              <template x-if="b.resolved_at">
                <div style="color:#64748b">
                  Resolved in <span x-text="b.duration_hours?.toFixed(2) + 'h'" style="color:#94a3b8"></span>
                </div>
              </template>
            </div>
          </template>
        </div>
      </template>
    </div>
  </div>
</div>
```

- [ ] **Step 5: Check JS syntax**

```powershell
node --check frontend/app.js
```
Expected: no output (clean)

- [ ] **Step 6: Smoke-test the UI**

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
```
Open `http://127.0.0.1:8000`. Navigate to the Contracts tab. Confirm: list renders, create form submits, breach detail panel responds to selection.

- [ ] **Step 7: Commit**

```bash
git add frontend/app.js
git commit -m "feat(contracts): add Contracts tab to Alpine.js UI with list + breach detail"
```

---

## Task 11: Full Test Suite Pass

- [ ] **Step 1: Run all tests**

```powershell
python -m pytest tests/ -q --tb=short
```
Expected: all tests PASS, no regressions

- [ ] **Step 2: Run coverage check**

```powershell
python -m pytest tests/unit/test_contracts.py tests/integration/test_contracts_integration.py tests/property/test_contracts_property.py --cov=etl_framework.repository.contract_repository --cov=api.services.contract_breach_checker --cov=api.routes.contracts --cov-report=term-missing -q
```
Expected: >85% coverage on contract modules

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat(contracts): data contracts feature complete — repository, API, breach checker, scheduler, UI, tests"
```
