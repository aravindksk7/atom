"""Assemble and apply a portable JSON bundle of file servers, configs, jobs,
execution sequences, and job selections, for moving records between
installs. See docs/superpowers/specs/2026-09-17-config-export-import-design.md.

Cross-entity references (a sequence's default config, a selection's config or
sequence) are carried by NAME, not numeric ID, so a bundle built on one
install resolves correctly on another where the same names may have
different row IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from etl_framework.config.models import SECRET_FIELDS
from etl_framework.repository.repository import (
    ConfigRepository,
    FileServerProfileRepository,
    JobRepository,
    JobSelectionRepository,
    _FILE_SERVER_SECRET_FIELDS,
)
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

BUNDLE_VERSION = 1
ENTITY_TYPES = ("file_servers", "configs", "jobs", "sequences", "selections")


@dataclass
class BundleItemResult:
    type: str
    name: str
    status: str  # "created" | "skipped" | "error"
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"type": self.type, "name": self.name, "status": self.status, "reason": self.reason}


def _strip_config_secrets(data: dict) -> dict:
    """Blank every SECRET_FIELDS value (top-level, plus nested
    connections/api_endpoints entries) to None. Mirrors the walk in
    api/routes/configs.py::_mask, but blanks instead of masking -- an
    exported bundle must not carry a value a reader could mistake for real,
    even a fixed placeholder string."""
    def _strip_entry(entry: dict) -> dict:
        return {k: (None if k in SECRET_FIELDS else v) for k, v in entry.items()}

    result = _strip_entry(data)
    for group_key in ("connections", "api_endpoints"):
        group = data.get(group_key)
        if isinstance(group, dict):
            result[group_key] = {
                name: (_strip_entry(entry) if isinstance(entry, dict) else entry)
                for name, entry in group.items()
            }
    return result


def _strip_file_server_secrets(data: dict) -> dict:
    return {k: (None if k in _FILE_SERVER_SECRET_FIELDS else v) for k, v in data.items()}


def _export_file_server(profile) -> dict:
    data = {
        "name": profile.name, "kind": profile.kind, "description": profile.description,
        "host": profile.host, "port": profile.port, "username": profile.username,
        "auth_method": profile.auth_method, "password": profile.password,
        "private_key": profile.private_key, "key_passphrase": profile.key_passphrase,
        "host_key_fingerprint": profile.host_key_fingerprint,
        "aws_access_key_id": profile.aws_access_key_id,
        "aws_secret_access_key": profile.aws_secret_access_key,
        "aws_session_token": profile.aws_session_token,
        "region_name": profile.region_name, "endpoint_url": profile.endpoint_url,
    }
    return _strip_file_server_secrets(data)


def _export_config(cfg) -> dict:
    return {
        "name": cfg.name, "env_name": cfg.env_name,
        "config_data": _strip_config_secrets(cfg.config_json or {}),
    }


def _export_sequence(sequence_repo, config_repo, name: str) -> dict | None:
    sequence = sequence_repo.get_by_name(name)
    if sequence is None:
        return None
    version = sequence_repo.latest_version(sequence.id)
    defaults = dict((version.defaults_json or {}) if version else {})
    config_id = defaults.pop("config_id", None)
    if config_id is not None:
        cfg = config_repo.get(config_id)
        defaults["config_name"] = cfg.name if cfg else None
    return {
        "name": sequence.name, "description": sequence.description, "tags": sequence.tags or [],
        "steps": (version.steps_json or []) if version else [],
        "preconditions": version.preconditions_json if version else None,
        "defaults": defaults,
    }


def _export_selection(selection_repo, config_repo, sequence_repo, name: str) -> dict | None:
    sel = selection_repo.get_by_name(name)
    if sel is None:
        return None
    version = selection_repo.latest_version(sel.id)
    config_name = None
    if version and version.config_id is not None:
        cfg = config_repo.get(version.config_id)
        config_name = cfg.name if cfg else None
    sequence_ref = None
    if version and version.sequence_ref:
        seq = sequence_repo.get(version.sequence_ref.get("sequence_id"))
        if seq is not None:
            sequence_ref = {"sequence_name": seq.name}
    return {
        "name": sel.name, "description": sel.description, "tags": sel.tags or [],
        "job_sequence": (version.job_sequence or []) if version else [],
        "run_settings": (version.run_settings_json or {}) if version else {},
        "config_name": config_name,
        "sequence_ref": sequence_ref,
    }


def build_bundle(db: Session, selection: dict[str, list[str]]) -> dict:
    """`selection` maps each entity type in ENTITY_TYPES to the list of names
    to include. A missing key, or a name not found, contributes nothing."""
    from api.routes.jobs import _job_to_schema  # local import: avoids a route<->service import cycle

    bundle: dict[str, Any] = {"bundle_version": BUNDLE_VERSION}

    file_server_repo = FileServerProfileRepository(db)
    bundle["file_servers"] = [
        _export_file_server(profile)
        for name in selection.get("file_servers", [])
        if (profile := file_server_repo.get_by_name(name)) is not None
    ]

    config_repo = ConfigRepository(db)
    bundle["configs"] = [
        _export_config(cfg)
        for name in selection.get("configs", [])
        if (cfg := config_repo.get_by_name(name)) is not None
    ]

    job_repo = JobRepository(db)
    bundle["jobs"] = [
        _job_to_schema(job).model_dump(mode="json")
        for name in selection.get("jobs", [])
        if (job := job_repo.get(name)) is not None
    ]

    sequence_repo = ExecutionSequenceRepository(db)
    bundle["sequences"] = [
        entry
        for name in selection.get("sequences", [])
        if (entry := _export_sequence(sequence_repo, config_repo, name)) is not None
    ]

    selection_repo = JobSelectionRepository(db)
    bundle["selections"] = [
        entry
        for name in selection.get("selections", [])
        if (entry := _export_selection(selection_repo, config_repo, sequence_repo, name)) is not None
    ]

    return bundle


def _apply_file_servers(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    repo = FileServerProfileRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if repo.get_by_name(name) is not None:
            out.append(BundleItemResult("file_servers", name, "skipped", "already exists"))
            continue
        repo.create(dict(entry))
        out.append(BundleItemResult("file_servers", name, "created"))
    return out


def _apply_configs(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    repo = ConfigRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if repo.get_by_name(name) is not None:
            out.append(BundleItemResult("configs", name, "skipped", "already exists"))
            continue
        repo.create(name=name, env_name=entry["env_name"], config_data=entry.get("config_data") or {})
        out.append(BundleItemResult("configs", name, "created"))
    return out


def _apply_jobs(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    from api.routes.jobs import _job_to_data
    from api.schemas import JobDefinition

    repo = JobRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if repo.get(name) is not None:
            out.append(BundleItemResult("jobs", name, "skipped", "already exists"))
            continue
        try:
            definition = JobDefinition(**entry)
        except Exception as exc:
            out.append(BundleItemResult("jobs", name, "error", str(exc)))
            continue
        repo.create(_job_to_data(definition))
        out.append(BundleItemResult("jobs", name, "created"))
    return out


def _apply_sequences(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    sequence_repo = ExecutionSequenceRepository(db)
    config_repo = ConfigRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if sequence_repo.get_by_name(name) is not None:
            out.append(BundleItemResult("sequences", name, "skipped", "already exists"))
            continue
        defaults = dict(entry.get("defaults") or {})
        config_name = defaults.pop("config_name", None)
        if config_name is not None:
            cfg = config_repo.get_by_name(config_name)
            if cfg is None:
                out.append(BundleItemResult(
                    "sequences", name, "error", f"referenced config '{config_name}' was not found",
                ))
                continue
            defaults["config_id"] = cfg.id
        sequence_repo.create(
            name=name, description=entry.get("description", ""), tags=entry.get("tags") or [],
            steps=entry.get("steps") or [], preconditions=entry.get("preconditions"), defaults=defaults,
        )
        out.append(BundleItemResult("sequences", name, "created"))
    return out


def _apply_selections(db: Session, entries: list[dict]) -> list[BundleItemResult]:
    selection_repo = JobSelectionRepository(db)
    config_repo = ConfigRepository(db)
    sequence_repo = ExecutionSequenceRepository(db)
    out: list[BundleItemResult] = []
    for entry in entries:
        name = entry["name"]
        if selection_repo.get_by_name(name) is not None:
            out.append(BundleItemResult("selections", name, "skipped", "already exists"))
            continue
        config_id = None
        config_name = entry.get("config_name")
        if config_name is not None:
            cfg = config_repo.get_by_name(config_name)
            if cfg is None:
                out.append(BundleItemResult(
                    "selections", name, "error", f"referenced config '{config_name}' was not found",
                ))
                continue
            config_id = cfg.id
        sequence_ref = None
        ref = entry.get("sequence_ref")
        if ref is not None:
            seq = sequence_repo.get_by_name(ref["sequence_name"])
            if seq is None:
                out.append(BundleItemResult(
                    "selections", name, "error",
                    f"referenced sequence '{ref['sequence_name']}' was not found",
                ))
                continue
            sequence_ref = {"sequence_id": seq.id, "sequence_version": None}
        selection_repo.create(
            name=name, description=entry.get("description", ""), tags=entry.get("tags") or [],
            job_sequence=entry.get("job_sequence") or [], run_settings=entry.get("run_settings") or {},
            config_id=config_id, sequence_ref=sequence_ref,
        )
        out.append(BundleItemResult("selections", name, "created"))
    return out


def apply_bundle(db: Session, bundle: dict) -> list[BundleItemResult]:
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError(f"Unsupported bundle_version: {bundle.get('bundle_version')!r}")
    results: list[BundleItemResult] = []
    results += _apply_file_servers(db, bundle.get("file_servers") or [])
    results += _apply_configs(db, bundle.get("configs") or [])
    results += _apply_jobs(db, bundle.get("jobs") or [])
    results += _apply_sequences(db, bundle.get("sequences") or [])
    results += _apply_selections(db, bundle.get("selections") or [])
    return results
