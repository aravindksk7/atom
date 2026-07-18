from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import Boolean, Column, Integer, String, Float, DateTime, JSON, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from etl_framework.repository.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"PASSED", "FAILED", "SLOW", "ERROR", "COMPLETED", "CANCELLED"}
)


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


# ---------------------------------------------------------------------------
# Job Selections
# ---------------------------------------------------------------------------

class JobSelection(Base):
    __tablename__ = "job_selections"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    tags = Column(JSON, nullable=False, default=list)
    archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    versions = relationship(
        "JobSelectionVersion", back_populates="selection",
        cascade="all, delete-orphan", lazy="select",
        order_by="JobSelectionVersion.version_number",
    )


class JobSelectionVersion(Base):
    __tablename__ = "job_selection_versions"

    id = Column(Integer, primary_key=True, index=True)
    selection_id = Column(Integer, ForeignKey("job_selections.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    job_sequence = Column(JSON, nullable=False, default=list)
    run_settings_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    selection = relationship("JobSelection", back_populates="versions")


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
    cancel_requested = Column(Boolean, default=False, nullable=False)
    selection_id = Column(Integer, nullable=True, index=True)
    selection_version = Column(Integer, nullable=True)
    ci_context = Column(JSON, nullable=True)

    results = relationship("TestResult", back_populates="run",
                           cascade="all, delete-orphan", lazy="select")
    steps = relationship("RunStep", back_populates="run",
                         cascade="all, delete-orphan", lazy="select",
                         order_by="RunStep.step_index")

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
    source_file_name = Column(String(1024), nullable=True)
    target_file_name = Column(String(1024), nullable=True)
    sample_rows = Column(JSON, nullable=True)
    segment_summary = Column(JSON, nullable=True)
    mismatch_summary = Column(JSON, nullable=True)

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
    delta = Column(Float, nullable=True)
    relative_delta = Column(Float, nullable=True)
    accepted      = Column(Boolean, nullable=False, default=False)
    accepted_note = Column(Text, nullable=True)
    accepted_at   = Column(DateTime(timezone=True), nullable=True)
    accepted_by   = Column(String(255), nullable=True)
    rejected      = Column(Boolean, nullable=False, default=False)
    rejected_note = Column(Text, nullable=True)
    rejected_at   = Column(DateTime(timezone=True), nullable=True)
    rejected_by   = Column(String(255), nullable=True)

    test_result = relationship("TestResult", back_populates="mismatches")


class DifferenceExportJob(Base):
    __tablename__ = "difference_export_jobs"

    id = Column(Integer, primary_key=True, index=True)
    export_id = Column(String(36), nullable=False, unique=True, index=True)
    run_id = Column(String(36), ForeignKey("test_runs.run_id", ondelete="CASCADE"),
                    nullable=False, index=True)
    format = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="PENDING")
    artifact_path = Column(Text, nullable=True)
    row_count = Column(Integer, default=0, nullable=False)
    error_message = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    recomputed_at = Column(DateTime(timezone=True), nullable=True)


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
    selection_id = Column(Integer, nullable=True, index=True)
    selection_version = Column(Integer, nullable=True)


class SchedulerTelemetryEvent(Base):
    __tablename__ = "scheduler_telemetry_events"

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("scheduled_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    schedule_name = Column(String(255), nullable=False, index=True)
    job_name = Column(String(255), nullable=True, index=True)
    selection_id = Column(Integer, nullable=True, index=True)
    selection_version = Column(Integer, nullable=True)
    run_id = Column(String(36), nullable=True, index=True)
    event_state = Column(String(32), nullable=False, index=True)
    status = Column(String(32), nullable=False, index=True)
    exit_code = Column(Integer, nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_summary = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)

    schedule = relationship("ScheduledRun")


Index("ix_scheduler_telemetry_schedule_created", SchedulerTelemetryEvent.schedule_id, SchedulerTelemetryEvent.created_at)
Index("ix_scheduler_telemetry_status_created", SchedulerTelemetryEvent.status, SchedulerTelemetryEvent.created_at)
Index("ix_scheduler_telemetry_state_created", SchedulerTelemetryEvent.event_state, SchedulerTelemetryEvent.created_at)


# ---------------------------------------------------------------------------
# App-wide settings
# ---------------------------------------------------------------------------

class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    timezone = Column(String(64), nullable=False, default="UTC")
    upload_retention_days = Column(Integer, nullable=False, default=30)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


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
# Execution Sequence Scheduler
# ---------------------------------------------------------------------------

class RunStep(Base):
    __tablename__ = "run_steps"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(
        String(36),
        ForeignKey("test_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_name = Column(String(255), nullable=False)
    step_index = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="PENDING")
    hold_after = Column(Boolean, nullable=False, default=False)
    condition = Column(JSON, nullable=True)
    wait_seconds = Column(Integer, nullable=False, default=0)
    held_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    released_by = Column(String(255), nullable=True)
    release_note = Column(Text, nullable=True)
    release_action = Column(String(20), nullable=True)

    run = relationship("TestRun", back_populates="steps")


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


# ---------------------------------------------------------------------------
# ETL Capabilities Expansion — Profile + Schema Snapshot
# ---------------------------------------------------------------------------

class ColumnProfile(Base):
    __tablename__ = "column_profiles"

    id             = Column(Integer, primary_key=True, index=True)
    job_name       = Column(String(255), nullable=False, index=True)
    run_id         = Column(String(36), nullable=True, index=True)
    column_name    = Column(String(255), nullable=False)
    null_rate      = Column(Float, nullable=True)
    distinct_count = Column(Integer, nullable=True)
    min_val        = Column(Text, nullable=True)
    max_val        = Column(Text, nullable=True)
    mean_val       = Column(Float, nullable=True)
    std_val        = Column(Float, nullable=True)
    p25            = Column(Float, nullable=True)
    p50            = Column(Float, nullable=True)
    p75            = Column(Float, nullable=True)
    p95            = Column(Float, nullable=True)
    captured_at    = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshots"

    id          = Column(Integer, primary_key=True, index=True)
    job_name    = Column(String(255), nullable=False, index=True)
    environment = Column(String(50), nullable=False, default="both")
    run_id      = Column(String(36), nullable=True, index=True)
    captured_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    columns     = Column(JSON, nullable=False, default=list)
