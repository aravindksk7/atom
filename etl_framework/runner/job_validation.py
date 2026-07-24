from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR


def validate_job_definition(job: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    job_type = _get(job, "job_type", "reconciliation")
    params = _get(job, "params", {}) or {}
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
    elif job_type == "dbt_artifact":
        if not params.get("run_results_path"):
            issues.append(ValidationIssue("params.run_results_path", "dbt_artifact jobs require run_results_path"))
    return issues


def raise_for_validation_issues(issues: list[ValidationIssue]) -> None:
    errors = [issue for issue in issues if issue.severity == ValidationSeverity.ERROR]
    if errors:
        raise ValueError("; ".join(f"{issue.field}: {issue.message}" for issue in errors))


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
