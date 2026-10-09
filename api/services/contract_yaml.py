"""YAML export/import for data contracts (contract-as-code).

Import is two-phase: plan_import() validates the document and classifies each
item as create / update / unchanged / error without writing; apply_import()
writes every create/update in one transaction, and refuses when any row is an
error. Version is never changed by import on existing contracts — use bump.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator
from sqlalchemy.orm import Session

from etl_framework.repository.contract_models import Contract

_COMPARED_FIELDS = ("owner", "sla_hours", "consumers", "breach_severity")


class ContractImportError(ValueError):
    """The YAML document is unparseable or fails validation as a whole."""


_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class ContractImportItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: _Text
    source_job: _Text
    owner: _Text
    sla_hours: float = Field(gt=0, allow_inf_nan=False)
    consumers: list[str] = []
    breach_severity: Literal["error", "warn"] = "error"
    version: str = "1.0"

    @field_validator("version", mode="before")
    @classmethod
    def _version_to_str(cls, v: Any) -> Any:
        # YAML reads an unquoted 1.2 as a float.
        if isinstance(v, bool):
            raise ValueError('version must be a string, e.g. "1.2"')
        return str(v) if isinstance(v, (int, float)) else v


def _consumers(contract: Contract) -> list[str]:
    value = contract.consumers
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = []
    return list(value or [])


def export_contracts_yaml(contracts: list[Contract]) -> str:
    items = [
        {
            "name": c.name,
            "version": c.version,
            "source_job": c.source_job,
            "owner": c.owner,
            "sla_hours": c.sla_hours,
            "consumers": _consumers(c),
            "breach_severity": c.breach_severity,
        }
        for c in contracts
    ]
    return yaml.safe_dump({"contracts": items}, sort_keys=False, allow_unicode=True)


def _parse(text: str) -> list[ContractImportItem]:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractImportError(f"Invalid YAML: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("contracts"), list):
        raise ContractImportError("Document must have a top-level 'contracts' list")

    items: list[ContractImportItem] = []
    for idx, raw in enumerate(doc["contracts"]):
        label = raw.get("name") if isinstance(raw, dict) else None
        try:
            items.append(ContractImportItem.model_validate(raw))
        except ValidationError as exc:
            where = f"contracts[{idx}]" + (f" ({label})" if label else "")
            raise ContractImportError(f"{where}: {exc.errors()[0]['loc']} {exc.errors()[0]['msg']}") from exc

    seen: set[str] = set()
    for item in items:
        if item.name in seen:
            raise ContractImportError(f"duplicate contract name in file: {item.name}")
        seen.add(item.name)
    return items


def _plan_row(item: ContractImportItem, existing: Contract | None) -> dict[str, str]:
    if existing is None:
        return {"name": item.name, "action": "create"}
    if not existing.active:
        return {"name": item.name, "action": "error", "reason": "name belongs to a deleted contract"}
    if existing.source_job != item.source_job:
        return {"name": item.name, "action": "error", "reason": "source_job is immutable"}

    current = {
        "owner": existing.owner,
        "sla_hours": float(existing.sla_hours),
        "consumers": _consumers(existing),
        "breach_severity": existing.breach_severity,
    }
    incoming = {f: getattr(item, f) for f in _COMPARED_FIELDS}
    row: dict[str, str] = {"name": item.name, "action": "unchanged" if current == incoming else "update"}
    if row["action"] == "update" and existing.version != item.version:
        row["warning"] = "version differs; use bump to change version"
    return row


def plan_import(db: Session, text: str) -> tuple[list[dict[str, str]], list[ContractImportItem]]:
    items = _parse(text)
    rows = []
    for item in items:
        existing = db.query(Contract).filter(Contract.name == item.name).first()
        rows.append(_plan_row(item, existing))
    return rows, items


def apply_import(db: Session, rows: list[dict[str, str]], items: list[ContractImportItem]) -> bool:
    if any(r["action"] == "error" for r in rows):
        return False
    now = datetime.now(timezone.utc)
    try:
        for row, item in zip(rows, items):
            if row["action"] == "create":
                db.add(Contract(
                    name=item.name,
                    version=item.version,
                    source_job=item.source_job,
                    owner=item.owner,
                    sla_hours=item.sla_hours,
                    consumers=json.dumps(item.consumers),
                    breach_severity=item.breach_severity,
                    active=True,
                ))
            elif row["action"] == "update":
                contract = db.query(Contract).filter(Contract.name == item.name).one()
                contract.owner = item.owner
                contract.sla_hours = item.sla_hours
                contract.consumers = json.dumps(item.consumers)
                contract.breach_severity = item.breach_severity
                contract.updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        raise
    return True
