# etl_framework/reconciliation/file_transfer_spec.py
"""Param validation and parsing for ``file_transfer`` jobs.

``api/schemas.py`` and ``etl_framework/runner/job_validation.py`` both validate
job params, and ``RunExecutor`` needs them parsed into typed specs. This module
is the single implementation all three share (same idea as ``file_mapping``'s
``FileMappingSpec.from_params``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from etl_framework.reconciliation.file_mapping import FileSourceSpec

LOCATION_KINDS = ("local", "s3", "sftp", "scp")
REMOTE_KINDS = ("s3", "sftp", "scp")
ON_EXISTS_VALUES = ("fail", "overwrite", "skip")


@dataclass(frozen=True)
class FileTransferSpec:
    source: FileSourceSpec
    destination: FileSourceSpec
    on_exists: str = "fail"
    recursive: bool = False
    preserve_structure: bool = False


def file_transfer_param_errors(params: dict[str, Any]) -> list[tuple[str, str]]:
    """Return ``(field, message)`` for every problem in ``params``. An empty
    list means the structural checks pass. A referenced file server profile is
    only checked at run time, not here."""
    errors: list[tuple[str, str]] = []

    def check_location(key: str, needs_pattern: bool) -> None:
        location = params.get(key)
        if not isinstance(location, dict):
            errors.append((f"params.{key}", f"file_transfer jobs require a '{key}' object in params"))
            return
        kind = location.get("kind")
        if kind not in LOCATION_KINDS:
            errors.append((f"params.{key}.kind", f"file_transfer {key}.kind must be 'local', 's3', 'sftp', or 'scp'"))
        if not location.get("root"):
            errors.append((f"params.{key}.root", f"file_transfer {key} requires 'root'"))
        if needs_pattern and not location.get("pattern"):
            errors.append((f"params.{key}.pattern", f"file_transfer {key} requires 'pattern'"))
        if kind in REMOTE_KINDS and not location.get("credentials_ref"):
            errors.append((
                f"params.{key}.credentials_ref",
                f"file_transfer {key}.kind '{kind}' requires 'credentials_ref'",
            ))

    check_location("source", needs_pattern=True)
    check_location("destination", needs_pattern=False)

    on_exists = params.get("on_exists")
    if on_exists is not None and on_exists not in ON_EXISTS_VALUES:
        errors.append(("params.on_exists", "file_transfer on_exists must be 'fail', 'overwrite', or 'skip'"))
    for flag in ("recursive", "preserve_structure"):
        value = params.get(flag)
        if value is not None and not isinstance(value, bool):
            errors.append((f"params.{flag}", f"file_transfer {flag} must be true or false"))
    if params.get("preserve_structure") is True and params.get("recursive") is not True:
        errors.append((
            "params.preserve_structure",
            "file_transfer preserve_structure requires recursive to be true",
        ))
    return errors


def _location_spec(location: dict[str, Any]) -> FileSourceSpec:
    kind = location["kind"]
    return FileSourceSpec(
        kind="sftp" if kind == "scp" else kind,
        root=location["root"],
        pattern=location.get("pattern") or "*",
        credentials_ref=location.get("credentials_ref"),
    )


def parse_file_transfer_params(params: dict[str, Any]) -> FileTransferSpec:
    errors = file_transfer_param_errors(params)
    if errors:
        raise ValueError(errors[0][1])
    return FileTransferSpec(
        source=_location_spec(params["source"]),
        destination=_location_spec(params["destination"]),
        on_exists=params.get("on_exists") or "fail",
        recursive=bool(params.get("recursive", False)),
        preserve_structure=bool(params.get("preserve_structure", False)),
    )
