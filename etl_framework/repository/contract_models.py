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
