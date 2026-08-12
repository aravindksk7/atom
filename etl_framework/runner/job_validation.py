from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import ValidationError

from api.schemas import BOCompareRequest, ReconFileCompareRequest


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR


S3_FORMATS = {"csv", "json", "parquet", "orc"}


def _params(job: Any) -> dict[str, Any]:
    if isinstance(job, dict):
        return dict(job.get("params") or {})
    return dict(getattr(job, "params", {}) or {})


def _job_type(job: Any) -> str:
    if isinstance(job, dict):
        return str(job.get("job_type") or "reconciliation")
    return str(getattr(job, "job_type", "reconciliation"))


def _has_config_ref(params: dict[str, Any]) -> bool:
    return bool(params.get("config_id") or params.get("config"))


def _require_non_empty(params: dict[str, Any], field: str, issues: list[ValidationIssue]) -> None:
    if not params.get(field):
        issues.append(ValidationIssue(f"params.{field}", f"S3 jobs require '{field}' in params"))


def _require_glue_non_empty(params: dict[str, Any], field: str, issues: list[ValidationIssue]) -> None:
    if not params.get(field):
        issues.append(ValidationIssue(f"params.{field}", f"aws_glue_catalog_compare jobs require '{field}' in params"))


def _non_negative_int(params: dict[str, Any], field: str, issues: list[ValidationIssue]) -> int | None:
    if field not in params or params.get(field) in (None, ""):
        return None
    try:
        value = int(params[field])
    except (TypeError, ValueError):
        issues.append(ValidationIssue(f"params.{field}", f"{field} must be a non-negative integer"))
        return None
    if value < 0:
        issues.append(ValidationIssue(f"params.{field}", f"{field} must be a non-negative integer"))
        return None
    return value


def _positive_int(params: dict[str, Any], field: str, issues: list[ValidationIssue]) -> int | None:
    if field not in params or params.get(field) in (None, ""):
        return None
    try:
        value = int(params[field])
    except (TypeError, ValueError):
        issues.append(ValidationIssue(f"params.{field}", f"{field} must be a positive integer"))
        return None
    if value <= 0:
        issues.append(ValidationIssue(f"params.{field}", f"{field} must be a positive integer"))
        return None
    return value


def _validate_s3_common(params: dict[str, Any], issues: list[ValidationIssue], fields: tuple[str, ...]) -> None:
    if not _has_config_ref(params):
        issues.append(ValidationIssue("params.config_id", "S3 jobs require 'config_id' or 'config' in params"))
    for field in fields:
        _require_non_empty(params, field, issues)


def _validate_s3_format(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    fmt = params.get("fmt")
    if fmt not in S3_FORMATS:
        issues.append(ValidationIssue("params.fmt", "fmt must be one of csv, json, parquet, or orc"))


def _validate_s3_row_count(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    _validate_s3_common(params, issues, ("bucket", "key"))
    _validate_s3_format(params, issues)
    min_rows = _non_negative_int(params, "min_rows", issues)
    max_rows = _non_negative_int(params, "max_rows", issues)
    if min_rows is not None and max_rows is not None and min_rows > max_rows:
        issues.append(ValidationIssue("params.min_rows", "min_rows must be less than or equal to max_rows"))


def _validate_s3_format_validation(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    _validate_s3_common(params, issues, ("bucket", "key"))
    _validate_s3_format(params, issues)
    expected_schema = params.get("expected_schema")
    if expected_schema is not None:
        if not isinstance(expected_schema, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in expected_schema.items()):
            issues.append(ValidationIssue("params.expected_schema", "expected_schema must map column names to type strings"))


def _validate_s3_partition_check(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    _validate_s3_common(params, issues, ("bucket", "prefix"))
    _non_negative_int(params, "min_partitions", issues)
    expected_columns = params.get("expected_columns")
    if expected_columns is not None:
        if not isinstance(expected_columns, list) or not expected_columns or not all(isinstance(v, str) and v for v in expected_columns):
            issues.append(ValidationIssue("params.expected_columns", "expected_columns must be a non-empty list of strings"))


def _validate_glue_catalog_compare(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    if not _has_config_ref(params):
        issues.append(ValidationIssue("params.config_id", "aws_glue_catalog_compare jobs require 'config_id' or 'config' in params"))
    for field in ("source_database", "source_table", "target_database", "target_table"):
        _require_glue_non_empty(params, field, issues)
    for field in ("compare_location", "compare_formats", "compare_partitions"):
        if field in params and not isinstance(params.get(field), bool):
            issues.append(ValidationIssue(f"params.{field}", f"{field} must be a boolean"))


def _validate_aws_athena_query(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    if not _has_config_ref(params):
        issues.append(ValidationIssue("params.config_id", "Athena jobs require 'config_id' or 'config' in params"))
    for field in ("query", "output_location"):
        if not params.get(field):
            issues.append(ValidationIssue(f"params.{field}", f"Athena jobs require '{field}' in params"))
    min_rows = _non_negative_int(params, "min_rows", issues)
    max_rows_assert = _non_negative_int(params, "max_rows_assert", issues)
    _non_negative_int(params, "max_rows", issues)
    _positive_int(params, "max_attempts", issues)
    if "poll_interval_seconds" in params:
        try:
            if float(params["poll_interval_seconds"]) < 0:
                issues.append(ValidationIssue("params.poll_interval_seconds", "poll_interval_seconds must be non-negative"))
        except (TypeError, ValueError):
            issues.append(ValidationIssue("params.poll_interval_seconds", "poll_interval_seconds must be non-negative"))
    if min_rows is not None and max_rows_assert is not None and min_rows > max_rows_assert:
        issues.append(ValidationIssue("params.min_rows", "min_rows must be less than or equal to max_rows_assert"))
    if params.get("expected_status") not in (None, "SUCCEEDED", "FAILED", "CANCELLED"):
        issues.append(ValidationIssue("params.expected_status", "expected_status must be SUCCEEDED, FAILED, or CANCELLED"))
    if "metric_assertions" in params and not isinstance(params.get("metric_assertions"), dict):
        issues.append(ValidationIssue("params.metric_assertions", "metric_assertions must be an object"))


def validate_job_definition(job: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    job_type = _job_type(job)
    params = _params(job)
    if job_type == "s3_row_count":
        _validate_s3_row_count(params, issues)
    elif job_type == "s3_format_validation":
        _validate_s3_format_validation(params, issues)
    elif job_type == "s3_partition_check":
        _validate_s3_partition_check(params, issues)
    elif job_type == "aws_glue_catalog_compare":
        _validate_glue_catalog_compare(params, issues)
    elif job_type == "aws_athena_query":
        _validate_aws_athena_query(params, issues)
    query = str(_get(job, "query", "") or "")
    key_columns = list(_get(job, "key_columns", []) or [])

    if not str(_get(job, "name", "") or "").strip():
        issues.append(ValidationIssue("name", "job name is required"))
    if job_type == "reconciliation":
        source_mode = params.get("source_mode")
        if source_mode == "bo_live":
            if not params.get("report_id"):
                issues.append(ValidationIssue("params.report_id", "bo_live reconciliation jobs require report_id"))
            if not params.get("bo_report_id"):
                issues.append(ValidationIssue("params.bo_report_id", "bo_live reconciliation jobs require bo_report_id"))
            _validate_file_source(params, "target", issues)
            if not _has_file_source(params, "target"):
                issues.append(ValidationIssue("params", "bo_live reconciliation jobs require a target file"))
            # key_columns is optional -- RunExecutor infers a shared ID column
            # or falls back to positional row matching.
        elif source_mode == "multi_file":
            from etl_framework.reconciliation.file_mapping import FileMappingSpec
            try:
                FileMappingSpec.from_params(params)
            except ValueError as exc:
                issues.append(ValidationIssue("params.file_mapping", str(exc)))
        elif source_mode == "files" or _has_file_source(params, "source") or _has_file_source(params, "target"):
            _validate_file_source(params, "source", issues)
            _validate_file_source(params, "target", issues)
            if not _has_file_source(params, "source") or not _has_file_source(params, "target"):
                issues.append(ValidationIssue("params", "file-backed reconciliation jobs require source and target files"))
            # key_columns is optional for file-backed jobs -- RunExecutor infers a
            # shared ID column or falls back to positional row matching.
        else:
            if not query.strip():
                issues.append(ValidationIssue("query", "reconciliation jobs require a query"))
            if not key_columns:
                issues.append(ValidationIssue("key_columns", "reconciliation jobs require key_columns"))
    elif job_type == "freshness":
        if not params.get("timestamp_column"):
            issues.append(ValidationIssue("params.timestamp_column", "freshness jobs require timestamp_column"))
        _validate_file_source(params, "source", issues)
        if not query.strip() and not _has_file_source(params, "source"):
            issues.append(ValidationIssue("query", "freshness jobs require a query or source file"))
    elif job_type in {"schema_snapshot", "profile"}:
        _validate_file_source(params, "source", issues)
        if not query.strip() and not _has_file_source(params, "source"):
            issues.append(ValidationIssue("query", f"{job_type} jobs require a query or source file"))
    elif job_type == "cross_job_assertion":
        if not params.get("source_job") or not params.get("target_job"):
            issues.append(ValidationIssue("params", "cross_job_assertion requires source_job and target_job"))
    elif job_type == "api_reconciliation":
        if not params.get("source_api_endpoint"):
            issues.append(ValidationIssue("params.source_api_endpoint", "api_reconciliation jobs require source_api_endpoint"))
        if not key_columns:
            issues.append(ValidationIssue("key_columns", "api_reconciliation jobs require key_columns"))
    elif job_type == "bo_report":
        if not params.get("report_id"):
            issues.append(ValidationIssue("params.report_id", "bo_report jobs require report_id"))
    elif job_type == "automic_job":
        if not params.get("job_name") and not params.get("run_id"):
            issues.append(ValidationIssue("params", "automic_job jobs require job_name or run_id"))
    elif job_type == "bo_job":
        if not params.get("object_id"):
            issues.append(ValidationIssue("params.object_id", "bo_job jobs require object_id"))
    elif job_type == "ds_job":
        if not params.get("job_name"):
            issues.append(ValidationIssue("params.job_name", "ds_job jobs require job_name"))
    elif job_type == "compare":
        compare_type = params.get("compare_type")
        request = params.get("request")
        if compare_type not in ("bo", "recon_file"):
            issues.append(ValidationIssue(
                "params.compare_type",
                "compare jobs require compare_type of 'bo' or 'recon_file'",
            ))
        if not isinstance(request, dict):
            issues.append(ValidationIssue(
                "params.request",
                "compare jobs require the compare request body in params.request",
            ))
        elif compare_type == "bo":
            try:
                parsed_bo = BOCompareRequest.model_validate(request)
            except ValidationError as exc:
                issues.append(ValidationIssue(
                    "params.request",
                    f"compare BO request is invalid: {_validation_error_message(exc)}",
                ))
            else:
                for field, source, label in (
                    ("params.request.source_a", parsed_bo.source_a, "Source A"),
                    ("params.request.source_b", parsed_bo.source_b, "Source B"),
                ):
                    if source.source_type in ("upload", "run"):
                        issues.append(ValidationIssue(
                            field,
                            f"compare job {label} uses a "
                            f"{'past run' if source.source_type == 'run' else 'file upload'}, "
                            "which cannot be re-run on a schedule - use a live, path, or api source",
                        ))
                    if source.source_type == "live" and not (source.doc_id or parsed_bo.doc_id):
                        issues.append(ValidationIssue(
                            field,
                            f"compare job {label} live source requires doc_id",
                        ))
        elif compare_type == "recon_file":
            try:
                parsed_file = ReconFileCompareRequest.model_validate(request)
            except ValidationError as exc:
                issues.append(ValidationIssue(
                    "params.request",
                    f"compare recon_file request is invalid: {_validation_error_message(exc)}",
                ))
            else:
                for field, stored, content, label in (
                    ("params.request.source_a", parsed_file.stored_run_id, parsed_file.file_a_content_b64, "Source A"),
                    ("params.request.source_b", parsed_file.stored_run_id_b, parsed_file.file_b_content_b64, "Source B"),
                ):
                    if stored or content:
                        issues.append(ValidationIssue(
                            field,
                            f"compare job {label} uses a "
                            f"{'stored run' if stored else 'file upload'}, "
                            "which cannot be re-run on a schedule - use a file path",
                        ))
        for field in ("rules", "pass_condition", "depends_on"):
            if params.get(field) or _get(job, field, None):
                issues.append(ValidationIssue(
                    f"params.{field}",
                    f"{field} is ignored for compare jobs - compare runs do not go "
                    "through the reconciliation job path",
                    ValidationSeverity.WARNING,
                ))
    elif job_type == "dbt_artifact":
        if not params.get("run_results_path"):
            issues.append(ValidationIssue("params.run_results_path", "dbt_artifact jobs require run_results_path"))
    return issues


def raise_for_validation_issues(issues: list[ValidationIssue]) -> None:
    errors = [issue for issue in issues if issue.severity == ValidationSeverity.ERROR]
    if errors:
        raise ValueError("; ".join(f"{issue.field}: {issue.message}" for issue in errors))


def _validation_error_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid request"))
    if location:
        return f"{location}: {message}"
    return message


def _get(job: Any, name: str, default: Any = None) -> Any:
    if isinstance(job, dict):
        return job.get(name, default)
    return getattr(job, name, default)


def _file_value(params: dict[str, Any], prefix: str, field: str) -> Any:
    # Canonical key convention, matching api/schemas.py's _job_file_value() and
    # api/services/run_executor.py's _job_file_value() -- e.g. "source_file_path",
    # with a "file_a_path"/"file_b_path" fallback. This function previously looked
    # up "source_path" (no "_file_" infix), which no producer of file-mode job
    # params (the frontend job modal, run_executor.py, or schemas.py's own pydantic
    # validator) ever writes -- so a well-formed file-backed job created through the
    # real UI was rejected here with a 422 despite passing every other check.
    side = "a" if prefix == "source" else "b"
    nested = params.get(prefix)
    if isinstance(nested, dict) and nested.get(field):
        return nested.get(field)
    return params.get(f"{prefix}_file_{field}") or params.get(f"file_{side}_{field}")


def _has_file_source(params: dict[str, Any], prefix: str) -> bool:
    return bool(_file_value(params, prefix, "path") or _file_value(params, prefix, "content_b64"))


def _validate_file_source(params: dict[str, Any], prefix: str, issues: list[ValidationIssue]) -> None:
    if _file_value(params, prefix, "content_b64") and not _file_value(params, prefix, "name"):
        issues.append(ValidationIssue(f"params.{prefix}", f"{prefix} file uploads require a file name for format detection"))
