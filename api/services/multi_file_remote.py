"""Shared S3/SFTP client construction, credential resolution, and file
discovery/read dispatch for ``multi_file`` reconciliation jobs.

Both ``RunExecutor`` (live job execution, ``api/services/run_executor.py``)
and ``difference_export`` (recomputing a run's full diff set for export/HTML
reports, ``api/services/difference_export.py``) need to discover and read
files from the same three source kinds (``local``, ``s3``, ``sftp``). This
module is the single place that owns that logic, so the two call sites don't
each re-derive their own copy of client construction and credential lookup.

``RemoteFileSourceSession`` also caches one client per ``(kind,
credentials_ref)`` for the caller's lifetime -- a source with N files opens
one S3/SFTP connection total, not one per file read.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pandas as pd

from api.services.file_source import _read_tabular_bytes, read_tabular, resolve_allowed_path
from etl_framework.reconciliation.file_mapping import (
    DiscoveredFile,
    FileSourceSpec,
    discover_local_files,
    discover_s3_files,
    discover_sftp_files,
)


def resolve_file_source_credentials(config_snapshot: dict[str, Any], spec: FileSourceSpec) -> dict[str, Any]:
    if not spec.credentials_ref:
        return {}
    sources = config_snapshot.get("file_source_credentials") or {}
    creds = sources.get(spec.credentials_ref, {}) if isinstance(sources, dict) else {}
    return dict(creds) if isinstance(creds, dict) else {}


def build_s3_client(config_snapshot: dict[str, Any], spec: FileSourceSpec):
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise RuntimeError("boto3 is required for multi_file S3 sources") from exc
    creds = resolve_file_source_credentials(config_snapshot, spec)
    client_kwargs: dict[str, Any] = {
        "aws_access_key_id": creds.get("aws_access_key_id"),
        "aws_secret_access_key": creds.get("aws_secret_access_key"),
        "aws_session_token": creds.get("aws_session_token"),
        "region_name": creds.get("region_name"),
        "endpoint_url": creds.get("endpoint_url"),
    }
    if creds.get("endpoint_url"):
        # A custom endpoint_url means a non-AWS, S3-compatible target (MinIO,
        # on-prem object storage) -- these commonly reject the virtual-hosted-
        # style bucket addressing boto3 otherwise defaults to whenever a
        # custom endpoint is set. Real AWS never sets endpoint_url, so this
        # never affects the existing real-AWS path.
        client_kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
    return boto3.client("s3", **client_kwargs)


def build_sftp_client(config_snapshot: dict[str, Any], spec: FileSourceSpec):
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is required for multi_file SFTP sources") from exc
    creds = resolve_file_source_credentials(config_snapshot, spec)
    transport = paramiko.Transport((creds.get("host"), int(creds.get("port", 22))))
    transport.connect(username=creds.get("username"), password=creds.get("password"))
    return paramiko.SFTPClient.from_transport(transport)


def close_remote_client(client) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()
    transport = getattr(client, "get_transport", lambda: None)()
    transport_close = getattr(transport, "close", None)
    if callable(transport_close):
        transport_close()


class RemoteFileSourceSession:
    """Discovers and reads files for a ``local``/``s3``/``sftp`` source spec,
    reusing one client per ``(kind, credentials_ref)`` across every call for
    the lifetime of this session -- construct one per job execution, use it
    for both sides' discovery and every subsequent file read, then ``close()``
    it (or use as a context manager) once the job is done with it.
    """

    def __init__(self, config_snapshot: dict[str, Any] | None = None) -> None:
        self._config_snapshot = config_snapshot or {}
        self._clients: dict[tuple[str, str | None], Any] = {}

    def _client_for(self, spec: FileSourceSpec):
        key = (spec.kind, spec.credentials_ref)
        if key not in self._clients:
            if spec.kind == "s3":
                self._clients[key] = build_s3_client(self._config_snapshot, spec)
            elif spec.kind == "sftp":
                self._clients[key] = build_sftp_client(self._config_snapshot, spec)
            else:
                raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
        return self._clients[key]

    def discover(self, spec: FileSourceSpec) -> list[DiscoveredFile]:
        if spec.kind == "local":
            root = resolve_allowed_path(spec.root)
            return discover_local_files(root, spec.pattern)
        if spec.kind == "s3":
            return discover_s3_files(self._client_for(spec), spec.root, spec.pattern)
        if spec.kind == "sftp":
            return discover_sftp_files(self._client_for(spec), spec.root, spec.pattern)
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")

    def read_file(self, file: DiscoveredFile, spec: FileSourceSpec) -> pd.DataFrame:
        if spec.kind == "local":
            return read_tabular(path=file.path, file_name=file.file_name)
        if spec.kind == "s3":
            parsed = urlparse(file.path)
            obj = self._client_for(spec).get_object(Bucket=parsed.netloc, Key=unquote(parsed.path.lstrip("/")))
            return _read_tabular_bytes(obj["Body"].read(), Path(file.file_name).suffix.lower())
        if spec.kind == "sftp":
            client = self._client_for(spec)
            with client.open(file.path, "rb") as fh:
                raw = fh.read()
            return _read_tabular_bytes(raw, Path(file.file_name).suffix.lower())
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")

    def close(self) -> None:
        for client in self._clients.values():
            close_remote_client(client)
        self._clients.clear()

    def __enter__(self) -> "RemoteFileSourceSession":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False
