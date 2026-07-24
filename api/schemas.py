from __future__ import annotations
from datetime import date, datetime
from enum import Enum
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
    run_profile: Literal["full", "shadow"] = "full"
    shadow_sample_frac: float = Field(default=0.02, gt=0, le=1.0)
    mismatch_row_limit: int = Field(default=1000, ge=1)
    max_compare_rows: int = Field(default=0, ge=0)
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
        # Statistical validation rule types
        "outlier_zscore", "outlier_iqr", "outlier_grubbs",
        "distribution_ks_test", "distribution_chi_square", "distribution_anderson_darling",
        "hypothesis_test_proportion", "anomaly_detection_sigma",
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
    threshold: float | None = None
    iqr_multiplier: float | None = None
    fence_type: Literal["inner", "outer"] = "inner"
    distribution: Literal["normal", "uniform", "exp"] | None = None
    distribution_params: dict[str, float] | None = None
    alpha: float | None = None
    bins: int | None = None
    expected_frequencies: list[float] = Field(default_factory=list)
    expected_proportion: float | None = None
    condition: str | None = None
    window: int | None = None


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


class TestSuiteTrigger(BaseModel):
    pytest_args: list[str] = []


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


class ColumnMismatchStatOut(BaseModel):
    column: str
    mismatch_count: int
    compared_rows: int
    match_pct: float | None = None


class FilePairSummaryOut(BaseModel):
    key: dict[str, Any] = Field(default_factory=dict)
    status: str
    error: str | None = None
    source_files: list[str] = Field(default_factory=list)
    target_files: list[str] = Field(default_factory=list)
    source_row_count: int = 0
    target_row_count: int = 0
    matched_count: int = 0
    missing_in_target_count: int = 0
    missing_in_source_count: int = 0
    value_mismatch_count: int = 0


class UnmatchedFileGroupOut(BaseModel):
    key: dict[str, Any] = Field(default_factory=dict)
    files: list[str] = Field(default_factory=list)


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
    source_file_name: str | None = None
    target_file_name: str | None = None
    override_reason: str | None = None
    overridden_by: str | None = None
    override_at: datetime | None = None
    sample_rows: list[dict] | None = None
    segment_summary: dict | None = None
    mismatch_summary: dict[str, Any] | None = None
    file_pairs: list[FilePairSummaryOut] = Field(default_factory=list)
    unmatched_sources: list[UnmatchedFileGroupOut] = Field(default_factory=list)
    unmatched_targets: list[UnmatchedFileGroupOut] = Field(default_factory=list)
    column_stats: list[ColumnMismatchStatOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class MismatchOut(BaseModel):
    id: int
    column_name: str | None = None
    key_values: dict | None = None
    source_value: str | None = None
    target_value: str | None = None
    mismatch_type: str | None = None
    delta: float | None = None
    relative_delta: float | None = None
    accepted: bool = False
    accepted_note: str | None = None
    accepted_at: datetime | None = None
    accepted_by: str | None = None
    rejected: bool = False
    rejected_note: str | None = None
    rejected_at: datetime | None = None
    rejected_by: str | None = None

    model_config = {"from_attributes": True}


class MismatchTypeFilter(str, Enum):
    value_diff = "value_diff"
    missing_in_target = "missing_in_target"
    missing_in_source = "missing_in_source"


class MismatchStatusFilter(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"


class MismatchSortField(str, Enum):
    id = "id"
    column = "column"
    mismatch_type = "mismatch_type"


class MismatchColumnInsight(BaseModel):
    column: str
    count: int


class MismatchTestInsight(BaseModel):
    result_id: int
    query_name: str
    total_issues: int
    stored_rows: int
    stored_complete: bool


class RunMismatchInsightsOut(BaseModel):
    run_id: str
    top_columns: list[MismatchColumnInsight] = Field(default_factory=list, description="Top 10 columns by mismatch count, descending")
    type_totals: dict[str, int] = Field(default_factory=dict)
    accepted_count: int = 0
    open_count: int = 0
    tests: list[MismatchTestInsight] = Field(default_factory=list)


class DrilldownRequest(BaseModel):
    segment_column: str = Field(min_length=1)


class DrilldownRow(BaseModel):
    value: str
    source_count: int
    target_count: int
    delta: int


class DrilldownOut(BaseModel):
    segment_column: str
    job_name: str
    rows: list[DrilldownRow]


class RunDetailOut(RunStatusOut):
    source_env: str | None = None
    target_env: str | None = None
    config_snapshot: dict | None = None
    file_name_a: str | None = None
    file_name_b: str | None = None
    results: list[TestResultOut] = []


class JobOut(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []


def _job_file_value(params: dict[str, Any], prefix: str, suffix: str) -> Any:
    side = "a" if prefix == "source" else "b"
    return params.get(f"{prefix}_file_{suffix}") or params.get(f"file_{side}_{suffix}")


def _has_job_file_source(params: dict[str, Any], prefix: str) -> bool:
    return bool(
        _job_file_value(params, prefix, "path")
        or _job_file_value(params, prefix, "content_b64")
    )


def _validate_job_file_source(params: dict[str, Any], prefix: str) -> None:
    if _job_file_value(params, prefix, "content_b64") and not _job_file_value(params, prefix, "name"):
        raise ValueError(f"{prefix} file uploads require a file name for format detection")


class JobDefinition(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile", "api_reconciliation",
        "bo_job",
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
        elif self.job_type == "api_reconciliation":
            if not self.params.get("source_api_endpoint"):
                raise ValueError("api_reconciliation jobs require 'source_api_endpoint' in params")
            if not self.key_columns:
                raise ValueError("api_reconciliation jobs require key_columns")
        elif self.job_type == "automic_job":
            if not self.params.get("job_name") and not self.params.get("run_id"):
                raise ValueError("automic_job jobs require 'job_name' or 'run_id' in params")
        elif self.job_type == "bo_job":
            if not self.params.get("object_id"):
                raise ValueError("bo_job jobs require 'object_id' in params")
        elif self.job_type == "dbt_artifact":
            if not self.params.get("run_results_path"):
                raise ValueError("dbt_artifact jobs require 'run_results_path' in params")
        elif self.job_type == "reconciliation":
            source_mode = self.params.get("source_mode")
            if source_mode == "bo_live":
                if not self.params.get("report_id"):
                    raise ValueError("bo_live reconciliation jobs require 'report_id' in params")
                if not self.params.get("bo_report_id"):
                    raise ValueError("bo_live reconciliation jobs require 'bo_report_id' in params")
                _validate_job_file_source(self.params, "target")
                if not _has_job_file_source(self.params, "target"):
                    raise ValueError("bo_live reconciliation jobs require a target file")
                # key_columns is optional: RunExecutor infers a shared ID column,
                # or falls back to positional row matching.
            elif source_mode == "multi_file":
                from etl_framework.reconciliation.file_mapping import FileMappingSpec
                FileMappingSpec.from_params(self.params)
            elif (
                source_mode == "files"
                or _has_job_file_source(self.params, "source")
                or _has_job_file_source(self.params, "target")
            ):
                _validate_job_file_source(self.params, "source")
                _validate_job_file_source(self.params, "target")
                if not _has_job_file_source(self.params, "source") or not _has_job_file_source(self.params, "target"):
                    raise ValueError("file-backed reconciliation jobs require source and target files")
                # key_columns is optional for file-backed jobs: RunExecutor infers a
                # shared ID column, or falls back to positional row matching.
            else:
                if not self.query.strip():
                    raise ValueError("reconciliation jobs require a query")
                if not self.key_columns:
                    raise ValueError("reconciliation jobs require key_columns")
        elif self.job_type == "freshness":
            if not self.params.get("timestamp_column"):
                raise ValueError("freshness jobs require 'timestamp_column' in params")
            _validate_job_file_source(self.params, "source")
            if not self.query.strip() and not _has_job_file_source(self.params, "source"):
                raise ValueError("freshness jobs require a query or source file")
        elif self.job_type == "cross_job_assertion":
            if not self.params.get("source_job") or not self.params.get("target_job"):
                raise ValueError("cross_job_assertion requires 'source_job' and 'target_job' in params")
        elif self.job_type in ("schema_snapshot", "profile"):
            _validate_job_file_source(self.params, "source")
            if not self.query.strip() and not _has_job_file_source(self.params, "source"):
                raise ValueError(f"{self.job_type} jobs require a query or source file")
        return self


# ---------------------------------------------------------------------------
# Job Selections
# ---------------------------------------------------------------------------

class JobSelectionVersionOut(BaseModel):
    version_number: int
    job_sequence: list[str | SequenceStep]
    run_settings: RunSettings
    created_at: datetime

    model_config = {"from_attributes": True}


class JobSelectionCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    job_sequence: list[str | SequenceStep] = Field(default_factory=list)
    run_settings: RunSettings = Field(default_factory=RunSettings)


class JobSelectionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    job_sequence: list[str | SequenceStep] | None = None
    run_settings: RunSettings | None = None


class JobSelectionOut(BaseModel):
    id: int
    name: str
    description: str
    tags: list[str]
    archived: bool
    latest_version: int
    job_count: int
    created_at: datetime
    updated_at: datetime


class JobSelectionDetailOut(JobSelectionOut):
    versions: list[JobSelectionVersionOut]


class JobSelectionLaunchRequest(BaseModel):
    source_env: str
    target_env: str = ""
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
    ci_context: dict[str, Any] | None = None


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


class BOLogonRequest(BaseModel):
    config_id: int
    auth_type: Literal["secEnterprise", "secWinAD", "secLDAP", "secSAPR3"] | None = None


class BOLogoffRequest(BaseModel):
    config_id: int


class BOAuthSessionOut(BaseModel):
    ok: bool
    message: str
    auth_scheme: Literal["x-sap-logontoken", "basic", "config"]
    token: str | None = None
    latency_ms: int = 0


class RestApiTestRequest(BaseModel):
    config_id: int
    endpoint_name: str


class RestApiPreviewRequest(BaseModel):
    config_id: int
    endpoint_name: str
    limit: int = 50


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
    source_type: Literal["live", "path", "upload", "api"]
    config_id: int | None = None
    doc_id: str | None = None
    report_id: str | None = None
    format: Literal["csv", "xlsx", "xls"] = "xlsx"
    file_path: str | None = None
    file_content_b64: str | None = None
    file_name: str | None = None
    api_endpoint_name: str | None = None

    @model_validator(mode="after")
    def validate_source(self) -> "SourceConfig":
        if self.source_type == "live" and self.config_id is None:
            raise ValueError("config_id required for live source")
        if self.source_type == "path" and not self.file_path:
            raise ValueError("file_path required for path source")
        if self.source_type == "upload" and not self.file_content_b64:
            raise ValueError("file_content_b64 required for upload source")
        if self.source_type == "api" and (self.config_id is None or not self.api_endpoint_name):
            raise ValueError("config_id and api_endpoint_name required for api source")
        return self


class AdvancedCompareOptions(BaseModel):
    """Shared advanced options for all tabular comparison endpoints."""
    float_tolerance: float = Field(default=1e-9, gt=0)
    column_tolerances: dict[str, float] = Field(default_factory=dict)
    datetime_tolerance_seconds: float = Field(default=0.0, ge=0)
    case_insensitive_columns: list[str] = Field(default_factory=list)
    whitespace_normalize_columns: list[str] = Field(default_factory=list)
    comparison_backend: Literal["pandas", "polars", "duckdb"] = "pandas"
    mismatch_row_limit: int = Field(default=5000, ge=1)
    sample_frac: float | None = Field(default=None, ge=0.01, le=1.0)
    parallel_columns: bool = False
    parallel_workers: int = Field(default=4, ge=1, le=32)


class BOCompareRequest(BaseModel):
    source_a: SourceConfig
    source_b: SourceConfig
    doc_id: str | None = None
    report_id: str | None = None
    key_columns: list[str] = Field(default_factory=list)
    exclude_columns: list[str] = Field(default_factory=list)
    label_a: str = "Source A"
    label_b: str = "Source B"
    advanced: AdvancedCompareOptions = Field(default_factory=AdvancedCompareOptions)


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
    advanced: AdvancedCompareOptions = Field(default_factory=AdvancedCompareOptions)

    @model_validator(mode="after")
    def validate_sources(self) -> "ReconFileCompareRequest":
        sources_a = [self.stored_run_id, self.file_a_path, self.file_a_content_b64]
        sources_b = [self.stored_run_id_b, self.file_b_path, self.file_b_content_b64]
        if sum(bool(value) for value in sources_a) != 1:
            raise ValueError("Source A requires exactly one stored run, file path, or upload")
        if sum(bool(value) for value in sources_b) != 1:
            raise ValueError("Source B requires exactly one stored run, file path, or upload")
        return self


class MultiFileCompareRequest(BaseModel):
    """Ad-hoc (no saved job) multi-file reconciliation, run once from the
    Compare tab. ``file_mapping`` is the same config shape a saved
    ``multi_file`` job's ``params.file_mapping`` uses (see
    ``etl_framework.reconciliation.file_mapping.FileMappingSpec.from_params``),
    but this phase only supports ``kind: "local"`` on both sides -- see the
    Phase 7 plan doc for why.
    """
    label_a: str = "Source A"
    label_b: str = "Source B"
    key_columns: list[str] | None = None
    exclude_columns: list[str] = Field(default_factory=list)
    file_mapping: dict[str, Any] = Field(...)
    advanced: AdvancedCompareOptions = Field(default_factory=AdvancedCompareOptions)


class PreviewFileMappingRequest(BaseModel):
    """Body for POST /api/jobs/preview-file-mapping. ``file_mapping`` is the
    same config shape used inside a saved multi_file job's
    ``params.file_mapping`` (see FileMappingSpec.from_params). Local sources
    need nothing else; s3/sftp sources need ``credentials_ref`` set on the
    relevant side AND a matching entry in ``file_source_credentials`` --
    there's no saved job yet at preview time to resolve a persisted
    credentials_ref against (see
    ``config_snapshot["file_source_credentials"]`` for the saved-job
    equivalent, ``api/services/multi_file_remote.py``'s
    ``resolve_file_source_credentials``), so the caller supplies raw
    credentials inline instead, keyed the same way. These credentials are
    used for this one preview call only -- never persisted anywhere.
    """
    file_mapping: dict[str, Any] = Field(...)
    file_source_credentials: dict[str, dict[str, Any]] = Field(default_factory=dict)


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
    chunk_size: int = Field(default=10_000, ge=0)
    advanced: AdvancedCompareOptions = Field(default_factory=AdvancedCompareOptions)


class MismatchAcceptRequest(BaseModel):
    note: str = Field(min_length=1)
    accepted_by: str | None = None


class MismatchRejectRequest(BaseModel):
    note: str = Field(min_length=1)
    rejected_by: str | None = None


class MismatchDecisionOut(BaseModel):
    id: int
    accepted: bool
    accepted_note: str | None = None
    accepted_at: datetime | None = None
    accepted_by: str | None = None
    rejected: bool = False
    rejected_note: str | None = None
    rejected_at: datetime | None = None
    rejected_by: str | None = None
    result_status_updated: bool = False


MismatchAcceptOut = MismatchDecisionOut


class DifferenceExportRequest(BaseModel):
    format: Literal["csv", "parquet", "json", "html"] = "csv"


class DifferenceExportStatusOut(BaseModel):
    export_id: str
    run_id: str
    format: Literal["csv", "parquet", "json", "html"]
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]
    row_count: int = 0
    error_message: str | None = None
    artifact_path: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    recomputed_at: datetime | None = None
    metadata: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Column Stats schemas
# ---------------------------------------------------------------------------

class ColumnStatsRequest(BaseModel):
    source_a: SourceConfig
    source_b: SourceConfig
    doc_id: str | None = None
    report_id: str | None = None
    label_a: str = "Source A"
    label_b: str = "Source B"
    query_name: str = "stats_compare"
    float_tolerance: float = Field(default=1e-9, gt=0)
    row_count_tolerance: int = Field(default=0, ge=0)


class ColumnStatsDiffOut(BaseModel):
    column: str
    metric: str
    source_value: Any
    target_value: Any
    delta: float | None = None


class ColumnStatsOut(BaseModel):
    query_name: str
    source_env: str
    target_env: str
    executed_at: datetime
    diffs: list[ColumnStatsDiffOut]
    has_diffs: bool
    diff_by_column: dict[str, list[ColumnStatsDiffOut]]


# ---------------------------------------------------------------------------
# Mismatch Diff schemas
# ---------------------------------------------------------------------------

class MismatchRecordOut(BaseModel):
    """A MismatchRecord not yet persisted to DB (no id field)."""
    column_name: str | None = None
    key_values: dict | None = None
    source_value: str | None = None
    target_value: str | None = None
    mismatch_type: str | None = None
    delta: float | None = None
    relative_delta: float | None = None


class MismatchDiffRequest(BaseModel):
    run_id_a: str
    run_id_b: str
    query_name: str | None = None
    run_a_label: str = "Run A"
    run_b_label: str = "Run B"


class MismatchDiffOut(BaseModel):
    query_name: str
    run_a_label: str
    run_b_label: str
    compared_at: datetime
    new: list[MismatchRecordOut]
    resolved: list[MismatchRecordOut]
    persistent: list[MismatchRecordOut]
    summary: dict[str, int]
    has_regressions: bool


class BulkMismatchAcceptRequest(BaseModel):
    result_ids: list[int] = Field(min_length=1)
    note: str = Field(min_length=1, max_length=1000)
    accepted_by: str | None = None


class BulkOverrideRequest(BaseModel):
    result_ids: list[int] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=4000)


class BulkDecisionOut(BaseModel):
    accepted_mismatch_count: int = 0
    result_status_updated: int = 0
    result_ids: list[int] = Field(default_factory=list)


class BulkMismatchDecisionRequest(BaseModel):
    decision: Literal["accept", "reject"]
    note: str = Field(min_length=1, max_length=1000)
    decided_by: str | None = None
    search: str | None = None
    column: str | None = None
    mismatch_type: MismatchTypeFilter | None = None
    status: MismatchStatusFilter | None = None


class BulkMismatchDecisionOut(BaseModel):
    decision: str
    matched_count: int = 0
    decided_count: int = 0
    result_status_updated: bool = False
