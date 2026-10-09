from __future__ import annotations

import pytest
import yaml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from etl_framework.repository.database import Base
import etl_framework.repository.models  # noqa: F401
import etl_framework.repository.contract_models  # noqa: F401
from etl_framework.repository.contract_repository import ContractRepository
from api.services.contract_yaml import (
    ContractImportError,
    apply_import,
    export_contracts_yaml,
    plan_import,
)


def _db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db, **overrides):
    data = {
        "name": "orders_c", "source_job": "orders_etl", "owner": "o@co.com",
        "sla_hours": 4.0, "consumers": ["finance", "ops"], "breach_severity": "error", "version": "1.2",
    }
    data.update(overrides)
    return ContractRepository(db).create(data)


def _doc(**overrides) -> str:
    item = {
        "name": "orders_c", "version": "1.2", "source_job": "orders_etl", "owner": "o@co.com",
        "sla_hours": 4.0, "consumers": ["finance", "ops"], "breach_severity": "error",
    }
    item.update(overrides)
    return yaml.safe_dump({"contracts": [item]})


def test_export_round_trips_to_unchanged():
    db = _db()
    _seed(db)
    text = export_contracts_yaml(ContractRepository(db).list())
    parsed = yaml.safe_load(text)
    assert parsed["contracts"][0]["consumers"] == ["finance", "ops"]
    assert parsed["contracts"][0]["version"] == "1.2"
    assert "active" not in parsed["contracts"][0]

    rows, _ = plan_import(db, text)
    assert [r["action"] for r in rows] == ["unchanged"]


def test_plan_create_for_new_name():
    db = _db()
    rows, _ = plan_import(db, _doc(name="new_c"))
    assert rows == [{"name": "new_c", "action": "create"}]


def test_plan_update_with_version_warning():
    db = _db()
    _seed(db)
    rows, _ = plan_import(db, _doc(owner="new@co.com", version="2.0"))
    assert rows[0]["action"] == "update"
    assert "version differs" in rows[0]["warning"]


def test_plan_error_on_source_job_change():
    db = _db()
    _seed(db)
    rows, _ = plan_import(db, _doc(source_job="other_job"))
    assert rows[0]["action"] == "error"
    assert rows[0]["reason"] == "source_job is immutable"


def test_plan_error_on_soft_deleted_name():
    db = _db()
    _seed(db)
    ContractRepository(db).delete("orders_c")
    rows, _ = plan_import(db, _doc())
    assert rows[0]["action"] == "error"
    assert "deleted" in rows[0]["reason"]


def test_float_version_coerced_to_string():
    db = _db()
    rows, items = plan_import(db, "contracts:\n  - {name: a, version: 1.5, source_job: j, owner: o, sla_hours: 1}\n")
    assert items[0].version == "1.5"
    assert rows[0]["action"] == "create"


@pytest.mark.parametrize("text", [
    "contracts: [",                                   # bad yaml
    "foo: []",                                        # no contracts key
    "contracts: {}",                                  # not a list
    "contracts:\n  - {name: a}\n",                    # missing fields
    "contracts:\n  - {name: a, source_job: j, owner: o, sla_hours: 1, breach_severity: fatal}\n",
])
def test_plan_rejects_invalid(text):
    with pytest.raises(ContractImportError):
        plan_import(_db(), text)


def test_plan_rejects_duplicate_names():
    item = "{name: a, source_job: j, owner: o, sla_hours: 1}"
    with pytest.raises(ContractImportError, match="duplicate"):
        plan_import(_db(), f"contracts:\n  - {item}\n  - {item}\n")


def test_apply_creates_and_updates():
    db = _db()
    _seed(db)
    text = yaml.safe_dump({"contracts": [
        {"name": "orders_c", "source_job": "orders_etl", "owner": "x@co.com", "sla_hours": 8, "consumers": ["bi"]},
        {"name": "new_c", "source_job": "new_job", "owner": "n@co.com", "sla_hours": 2, "version": "3.0"},
    ]})
    rows, items = plan_import(db, text)
    assert apply_import(db, rows, items) is True

    repo = ContractRepository(db)
    updated = repo.get("orders_c")
    assert updated.owner == "x@co.com" and updated.sla_hours == 8 and updated.version == "1.2"
    created = repo.get("new_c")
    assert created is not None and created.version == "3.0"


def test_apply_blocked_by_error_row():
    db = _db()
    _seed(db)
    text = yaml.safe_dump({"contracts": [
        {"name": "orders_c", "source_job": "other_job", "owner": "o", "sla_hours": 1},
        {"name": "new_c", "source_job": "new_job", "owner": "n", "sla_hours": 2},
    ]})
    rows, items = plan_import(db, text)
    assert apply_import(db, rows, items) is False
    assert ContractRepository(db).get("new_c") is None


def test_apply_rolls_back_on_commit_failure(monkeypatch):
    db = _db()
    _seed(db)
    text = yaml.safe_dump({"contracts": [
        {"name": "orders_c", "source_job": "orders_etl", "owner": "x@co.com", "sla_hours": 8},
        {"name": "new_c", "source_job": "new_job", "owner": "n@co.com", "sla_hours": 2},
    ]})
    rows, items = plan_import(db, text)
    real_commit = db.commit

    def boom():
        raise RuntimeError("commit failed")

    monkeypatch.setattr(db, "commit", boom)
    with pytest.raises(RuntimeError):
        apply_import(db, rows, items)
    monkeypatch.setattr(db, "commit", real_commit)

    repo = ContractRepository(db)
    assert repo.get("new_c") is None
    assert repo.get("orders_c").owner == "o@co.com"
