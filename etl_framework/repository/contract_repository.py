from __future__ import annotations
import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from etl_framework.repository.contract_models import Contract, ContractBreach, ContractVersion


class ContractRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # --- CRUD ---

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
                    opened = opened.replace(tzinfo=timezone.utc)
                duration = (now - opened).total_seconds() / 3600
                breach.resolved_at = now
                breach.resolution_run_id = run_id
                breach.duration_hours = round(duration, 4)
                self._db.commit()
                self._db.refresh(breach)
                resolved.append((breach, contract))
        return resolved

    def escalate_overdue(self) -> list[tuple[ContractBreach, Contract]]:
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
