from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, JSON, ForeignKey, Text
from sqlalchemy.orm import relationship
from etl_framework.repository.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SavedConfig(Base):
    __tablename__ = "saved_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    env_name = Column(String(100), nullable=False)
    config_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class SavedJob(Base):
    __tablename__ = "saved_jobs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    tags = Column(JSON, nullable=False, default=list)
    job_type = Column(String(50), nullable=False, default="reconciliation")
    query = Column(Text, nullable=False, default="")
    key_columns = Column(JSON, nullable=False, default=list)
    exclude_columns = Column(JSON, nullable=False, default=list)
    source_env = Column(String(100), nullable=True)
    target_env = Column(String(100), nullable=True)
    params = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class TestRun(Base):
    __tablename__ = "test_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(36), nullable=False, unique=True, index=True)
    status = Column(String(20), nullable=False, default="PENDING")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    source_env = Column(String(100), nullable=True)
    target_env = Column(String(100), nullable=True)
    config_snapshot = Column(JSON, nullable=True)
    total_tests = Column(Integer, default=0, nullable=False)
    passed = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)
    slow = Column(Integer, default=0, nullable=False)
    error = Column(Integer, default=0, nullable=False)
    run_type = Column(String(50), nullable=False, default="reconciliation")
    pair_id  = Column(String(36), nullable=True, index=True)
    is_baseline = Column(Boolean, nullable=False, default=False, index=True)

    results = relationship("TestResult", back_populates="run",
                           cascade="all, delete-orphan", lazy="select")

    @property
    def test_cases(self):
        return self.results

    @property
    def reconciliation_results(self):
        return self.results

    @property
    def total_passed(self) -> int:
        return self.passed or 0

    @property
    def total_failed(self) -> int:
        return (self.failed or 0) + (self.error or 0)

    @property
    def total_skipped(self) -> int:
        return 0


class TestResult(Base):
    __tablename__ = "test_results"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(36), ForeignKey("test_runs.run_id", ondelete="CASCADE"),
                    nullable=False, index=True)
    query_name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False)
    duration_seconds = Column(Float, default=0.0, nullable=False)
    source_row_count = Column(Integer, default=0, nullable=False)
    target_row_count = Column(Integer, default=0, nullable=False)
    value_mismatch_count = Column(Integer, default=0, nullable=False)
    missing_in_target_count = Column(Integer, default=0, nullable=False)
    missing_in_source_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)

    # Override fields for marking test outcomes as passing even when they fail
    override_status = Column(String(20), nullable=True)
    override_reason = Column(Text, nullable=True)
    override_by = Column(String(255), nullable=True)
    override_at = Column(DateTime(timezone=True), nullable=True)

    run = relationship("TestRun", back_populates="results")
    mismatches = relationship("MismatchDetail", back_populates="test_result",
                              cascade="all, delete-orphan", lazy="select")

    @property
    def total_issues(self) -> int:
        return (
            (self.value_mismatch_count or 0)
            + (self.missing_in_target_count or 0)
            + (self.missing_in_source_count or 0)
        )

    @property
    def effective_status(self) -> str:
        """Return the override status if set, otherwise the computed status."""
        return self.override_status if self.override_status is not None else self.status

    @property
    def schema_diff(self):
        return None


class MismatchDetail(Base):
    __tablename__ = "mismatch_details"

    id = Column(Integer, primary_key=True, index=True)
    test_result_id = Column(Integer, ForeignKey("test_results.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    key_values = Column(JSON, nullable=True)
    column_name = Column(String(255), nullable=True)
    source_value = Column(Text, nullable=True)
    target_value = Column(Text, nullable=True)
    mismatch_type = Column(String(50), nullable=True)
    accepted      = Column(Boolean, nullable=False, default=False)
    accepted_note = Column(Text, nullable=True)
    accepted_at   = Column(DateTime(timezone=True), nullable=True)
    accepted_by   = Column(String(255), nullable=True)

    test_result = relationship("TestResult", back_populates="mismatches")


# ---------------------------------------------------------------------------
# P3 — Job Lineage
# ---------------------------------------------------------------------------

class JobLineageEdge(Base):
    __tablename__ = "job_lineage_edges"

    id = Column(Integer, primary_key=True, index=True)
    upstream_job = Column(String(255), nullable=False, index=True)
    downstream_job = Column(String(255), nullable=False, index=True)
    edge_type = Column(String(50), nullable=False, default="depends_on")
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


# ---------------------------------------------------------------------------
# P0 — Auth
# ---------------------------------------------------------------------------

class ApiToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    token_hint = Column(String(8), nullable=False, default="")


# ---------------------------------------------------------------------------
# P0 — Alerting
# ---------------------------------------------------------------------------

class NotificationHook(Base):
    __tablename__ = "notification_hooks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    url = Column(Text, nullable=False)
    events = Column(JSON, nullable=False, default=list)  # e.g. ["run.failed","run.error"]
    enabled = Column(Boolean, nullable=False, default=True)
    secret = Column(Text, nullable=True)                 # HMAC-SHA256 signing key
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationship
    deliveries = relationship("NotificationDelivery", back_populates="hook", cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# P0 — Scheduling
# ---------------------------------------------------------------------------

class ScheduledRun(Base):
    __tablename__ = "scheduled_runs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    cron_expr = Column(String(100), nullable=False)
    job_sequence = Column(JSON, nullable=False, default=list)
    source_env = Column(String(100), nullable=False, default="")
    target_env = Column(String(100), nullable=False, default="")
    run_settings_json = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditEvent(Base):
    __tablename__ = "audit_events"

    id            = Column(Integer, primary_key=True, index=True)
    actor         = Column(String(255), nullable=True)
    action        = Column(String(100), nullable=False)
    resource_type = Column(String(50),  nullable=False, index=True)
    resource_id   = Column(String(255), nullable=True,  index=True)
    diff          = Column(JSON,        nullable=True)
    created_at    = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)


# ---------------------------------------------------------------------------
# P0 — Alerting: notification delivery tracking
# ---------------------------------------------------------------------------

class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    hook_id = Column(Integer, ForeignKey("notification_hooks.id", ondelete="CASCADE"), nullable=False, index=True)
    run_id = Column(String(36), nullable=False, index=True)
    event = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False)  # pending, success, failed
    attempt_count = Column(Integer, default=0)
    last_attempt_at = Column(DateTime(timezone=True))
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    response_status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationship
    hook = relationship("NotificationHook", back_populates="deliveries")
