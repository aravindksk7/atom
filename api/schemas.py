from __future__ import annotations
from datetime import date, datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigCreate(BaseModel):
    name: str
    env_name: str
    config_data: dict[str, Any] = {}


class ConfigUpdate(BaseModel):
    name: str | None = None
    env_name: str | None = None
    config_data: dict[str, Any] | None = None


class ConfigOut(BaseModel):
    id: int
    name: str
    env_name: str
    config_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FrameworkErrorOut(BaseModel):
    error_type: str
    message: str
    field_name: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ConfigValidationRequest(BaseModel):
    env_name: str
    config_data: dict[str, Any] = Field(default_factory=dict)


class ConfigValidationOut(BaseModel):
    ok: bool
    env_name: str
    config_data: dict[str, Any] | None = None
    errors: list[FrameworkErrorOut] = Field(default_factory=list)


class ConfigImportYamlRequest(BaseModel):
    yaml_content: str = Field(min_length=1)


class TestResultOverrideRequest(BaseModel):
    status: Literal["PASSED"] = "PASSED"
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def validate_reason(self) -> "TestResultOverrideRequest":
        self.reason = self.reason.strip()
        if not self.reason:
            raise ValueError("reason must contain agreed actions")
        return self



class RunSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    use_live_connections: bool = False
    execution_mode: Literal["parallel", "sequential"] = "parallel"
    max_workers: int = Field(default=4, ge=1)
    max_duration_seconds: float = Field(default=0, ge=0)
    float_tolerance: float = Field(default=1e-9, gt=0)
    schema_mismatch_policy: Literal["warn", "error"] = "warn"
    null_equals_null: bool = True
    chunk_size: int = Field(default=0, ge=0)
    use_hash_precheck: bool = True
    comparison_backend: Literal["pandas", "polars"] = "pandas"
    mismatch_row_limit: int = Field(default=1000, ge=1)
    exclude_columns: list[str] = Field(default_factory=list)
    key_columns: list[str] = Field(default_factory=list)
    health_check: bool = False
    metrics_enabled: bool = True
    notes: str = ""
    max_retries: int = Field(default=0, ge=0, le=10)
    retry_delay_seconds: float = Field(default=30.0, ge=0)
    retry_on: list[Literal["error", "timeout"]] = Field(default_factory=lambda: ["error"])


class AuditEventOut(BaseModel):
    id: int
    actor: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    diff: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DQRule(BaseModel):
    type: Literal[
        "not_null", "unique", "row_count_min", "row_count_max",
        "row_count_between", "column_mean_between", "match_regex", "custom_sql",
        "column_max_length", "column_min_length", "value_in_set", "value_not_in_set",
        "column_contains", "date_range", "positive_values", "negative_values",
        # New rule types
        "completeness_ratio", "distinct_count_between", "column_sum_between",
        "column_std_dev_between", "column_percentile", "column_type_check",
        "column_value_between", "cross_column_consistency", "pii_mask_check",
        "no_whitespace", "referential_check", "custom_sql_assert",
    ]
    column: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    pattern: str | None = None
    sql: str | None = None
    values: list[str | int | float | bool] = Field(default_factory=list)
    min_date: date | datetime | None = None
    max_date: date | datetime | None = None
    severity: Literal["error", "warn"] = "error"
    # New fields for extended rule types
    percentile: int | None = None
    operator: str | None = None
    lookup_query: str | None = None
    column_b: str | None = None
    expected_type: str | None = None


class PassCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_row_count: int | None = None
    max_row_count: int | None = None
    max_value_mismatches: int | None = None
    max_missing_in_target: int | None = None
    max_missing_in_source: int | None = None
    require_status: list[str] = Field(default_factory=list)
    pass_sql: str | None = None
    pass_sql_mode: Literal["rows_mean_pass", "rows_mean_fail"] = "rows_mean_pass"


class StepCondition(BaseModel):
    require_status: list[str] = Field(default_factory=lambda: ["PASSED"])
    max_mismatch_count: int | None = None
    min_row_count: int | None = None
    max_row_count: int | None = None
    max_value_mismatches: int | None = None
    max_missing_in_target: int | None = None
    max_missing_in_source: int | None = None


class SequenceStep(BaseModel):
    job_name: str
    hold_after: bool = False
    condition: StepCondition | None = None
    wait_seconds: int = Field(default=0, ge=0)


class RunStepOut(BaseModel):
    id: int
    run_id: str
    job_name: str
    step_index: int
    status: str
    hold_after: bool
    condition: dict[str, Any] | None = None
    wait_seconds: int
    held_at: datetime | None = None
    released_at: datetime | None = None
    released_by: str | None = None
    release_note: str | None = None
    release_action: str | None = None

    model_config = {"from_attributes": True}


class RunStepReleaseRequest(BaseModel):
    action: Literal["approve", "skip", "cancel"]
    note: str = Field(min_length=1)
    released_by: str = Field(min_length=1)


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

    @model_validator(mode="after")
    def normalize_job_sequence(self) -> "RunTrigger":
        if not self.job_sequence and self.job_names:
            self.job_sequence = list(self.job_names)
        coerced: list[SequenceStep] = []
        for item in self.job_sequence:
            if isinstance(item, str):
                coerced.append(SequenceStep(job_name=item))
            elif isinstance(item, dict):
                coerced.append(SequenceStep(**item))
            else:
                coerced.append(item)
        self.job_sequence = coerced
        return self


class RunStatusOut(BaseModel):
    run_id: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    slow: int = 0
    error: int = 0
    run_type: str = "reconciliation"
    pair_id: str | None = None

    model_config = {"from_attributes": True}


class ExecutionProgressOut(BaseModel):
    run_id: str
    status: str
    total_tests: int = 0
    completed_tests: int = 0
    current_job: str | None = None
    percent_complete: int = Field(default=0, ge=0, le=100)


class GeneratedArtifactOut(BaseModel):
    name: str
    artifact_type: Literal["metrics", "log", "report", "other"] = "other"
    path: str
    created_at: datetime | None = None


class HealthCheckOut(BaseModel):
    component: str
    healthy: bool
    message: str


class HealthCheckRequest(BaseModel):
    environments: dict[str, dict[str, Any]] = Field(default_factory=dict)


class TestResultOut(BaseModel):
    id: int
    query_name: str
    status: str
    effective_status: str
    duration_seconds: float
    source_row_count: int
    target_row_count: int
    value_mismatch_count: int
    missing_in_target_count: int
    missing_in_source_count: int
    error_message: str | None = None
    executed_at: datetime | None = None
    override_reason: str | None = None
    overridden_by: str | None = None
    override_at: datetime | None = None

    model_config = {"from_attributes": True}


class MismatchOut(BaseModel):
    id: int
    column_name: str | None = None
    key_values: dict | None = None
    source_value: str | None = None
    target_value: str | None = None
    mismatch_type: str | None = None
    accepted: bool = False
    accepted_note: str | None = None
    accepted_at: datetime | None = None
    accepted_by: str | None = None

    model_config = {"from_attributes": True}


class RunDetailOut(RunStatusOut):
    source_env: str | None = None
    target_env: str | None = None
    config_snapshot: dict | None = None
    results: list[TestResultOut] = []


class JobOut(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []


class JobDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile",
    ] = "reconciliation"
    query: str = ""
    key_columns: list[str] = Field(default_factory=list)
    exclude_columns: list[str] = Field(default_factory=list)
    source_env: str | None = None
    target_env: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    rules: list[DQRule] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    pass_condition: PassCondition | None = None

    @model_validator(mode="after")
    def validate_reconciliation_contract(self) -> "JobDefinition":
        if self.job_type == "bo_report":
            if not self.params.get("report_id"):
                raise ValueError("bo_report jobs require 'report_id' in params")
        elif self.job_type == "automic_job":
            if not self.params.get("job_name") and not self.params.get("run_id"):
                raise ValueError("automic_job jobs require 'job_name' or 'run_id' in params")
        elif self.job_type == "dbt_artifact":
            if not self.params.get("run_results_path"):
                raise ValueError("dbt_artifact jobs require 'run_results_path' in params")
        elif self.job_type == "reconciliation":
            if not self.query.strip():
                raise ValueError("reconciliation jobs require a query")
            if not self.key_columns:
                raise ValueError("reconciliation jobs require key_columns")
        elif self.job_type == "freshness":
            if not self.params.get("timestamp_column"):
                raise ValueError("freshness jobs require 'timestamp_column' in params")
        elif self.job_type == "cross_job_assertion":
            if not self.params.get("source_job") or not self.params.get("target_job"):
                raise ValueError("cross_job_assertion requires 'source_job' and 'target_job' in params")
        elif self.job_type in ("schema_snapshot", "profile"):
            if not self.query.strip():
                raise ValueError(f"{self.job_type} jobs require a query")
        return self


# ---------------------------------------------------------------------------
# Adapter / SAP BO / Automic schemas
# ---------------------------------------------------------------------------

class RunProgressOut(BaseModel):
    run_id: str
    status: str
    total_tests: int = 0
    completed_tests: int = 0
    current_job: str | None = None
    percent_complete: int = Field(default=0, ge=0, le=100)
    current_step: int | None = None
    held_step: int | None = None


class BODocOut(BaseModel):
    id: str
    name: str
    folder: str = ""


class BOReportOut(BaseModel):
    id: str
    name: str
    report_index: int = 0


class AdapterTestOut(BaseModel):
    ok: bool
    message: str
    latency_ms: int = 0


class AutomicJobStatusOut(BaseModel):
    identifier: str
    identifier_type: str
    status: str
    environment: str
    checked_at: datetime


class BOTestRequest(BaseModel):
    config_id: int


class AutomicLookupRequest(BaseModel):
    config_id: int
    identifier: str
    id_type: Literal["run_id", "job_name"] = "job_name"


class BOJobCreateRequest(BaseModel):
    name: str
    title: str
    doc_id: str
    report_id: str
    key_columns: list[str]
    format: str = "xlsx"


class AutomicJobCreateRequest(BaseModel):
    name: str
    job_name: str | None = None
    run_id: str | None = None

    @model_validator(mode="after")
    def validate_identifier(self) -> "AutomicJobCreateRequest":
        if not self.job_name and not self.run_id:
            raise ValueError("job_name or run_id is required")
        return self


class AutomicJobSummary(BaseModel):
    name: str
    status: str


class AutomicBulkImportRequest(BaseModel):
    config_id: int
    job_names: list[str] = Field(min_length=1)


class AutomicBulkImportResponse(BaseModel):
    imported: list[JobDefinition]
    errors: dict[str, str] = Field(default_factory=dict)


class TestCompareOut(BaseModel):
    test_name: str
    status_a: str | None = None
    status_b: str | None = None
    duration_a: float | None = None
    duration_b: float | None = None
    mismatches_a: int | None = None
    mismatches_b: int | None = None
    result_id_a: int | None = None
    result_id_b: int | None = None


class RunCompareOut(BaseModel):
    run_a: RunStatusOut
    run_b: RunStatusOut
    tests: list[TestCompareOut]
    summary: dict[str, int]


# ---------------------------------------------------------------------------
# Compare tab schemas
# ---------------------------------------------------------------------------

class SourceConfig(BaseModel):
    source_type: Literal["live", "path", "upload"]
    config_id: int | None = None
    doc_id: str | None = None
    report_id: str | None = None
    format: Literal["csv", "xlsx", "xls"] = "xlsx"
    file_path: str | None = None
    file_content_b64: str | None = None
    file_name: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "SourceConfig":
        if self.source_type == "live" and self.config_id is None:
            raise ValueError("config_id required for live source")
        if self.source_type == "path" and not self.file_path:
            raise ValueError("file_path required for path source")
        if self.source_type == "upload" and not self.file_content_b64:
            raise ValueError("file_content_b64 required for upload source")
        return self


class BOCompareRequest(BaseModel):
    source_a: SourceConfig
    source_b: SourceConfig
    doc_id: str | None = None
    report_id: str | None = None
    key_columns: list[str] = Field(default_factory=list)
    exclude_columns: list[str] = Field(default_factory=list)
    label_a: str = "Source A"
    label_b: str = "Source B"


class DualEnvLaunchRequest(BaseModel):
    config_id_a: int
    config_id_b: int
    source_env_a: str
    target_env_a: str
    source_env_b: str
    target_env_b: str
    job_names: list[str] = Field(default_factory=list)
    run_settings: RunSettings = Field(default_factory=RunSettings)


class DualEnvLaunchOut(BaseModel):
    pair_id: str
    run_id_a: str
    run_id_b: str


class PairSummaryOut(BaseModel):
    pair_id: str
    run_a: RunStatusOut
    run_b: RunStatusOut


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
        sources_a = [self.stored_run_id, self.file_a_path, self.file_a_content_b64]
        sources_b = [self.stored_run_id_b, self.file_b_path, self.file_b_content_b64]
        if sum(bool(value) for value in sources_a) != 1:
            raise ValueError("Source A requires exactly one stored run, file path, or upload")
        if sum(bool(value) for value in sources_b) != 1:
            raise ValueError("Source B requires exactly one stored run, file path, or upload")
        return self


class SQLCompareRequest(BaseModel):
    config_id_a: int
    config_id_b: int
    query_a: str
    query_b: str
    label_a: str = "Source A"
    label_b: str = "Source B"
    connection_a: str | None = None
    connection_b: str | None = None
    key_columns: list[str] = Field(default_factory=list)
    exclude_columns: list[str] = Field(default_factory=list)


class MismatchAcceptRequest(BaseModel):
    note: str = Field(min_length=1)
    accepted_by: str | None = None


class MismatchAcceptOut(BaseModel):
    id: int
    accepted: bool
    accepted_note: str | None = None
    accepted_at: datetime | None = None
    accepted_by: str | None = None
    result_status_updated: bool = False
