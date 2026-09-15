"""Shared S3/SFTP client construction, credential resolution, and file
discovery/read dispatch for ``multi_file`` reconciliation jobs.

Both ``RunExecutor`` (live job execution, ``api/services/run_executor.py``)
and ``difference_export`` (recomputing a run's full diff set for export/HTML
reports, ``api/services/difference_export.py``) need to discover and read
files from the same three source kinds (``local``, ``s3``, ``sftp``). This
module is the single place that owns that logic, so the two call sites don't
each re-derive their own copy of client construction and credential lookup.

Credentials come from a persisted, encrypted ``FileServerProfile`` (see
``etl_framework.repository.repository.FileServerProfileRepository``) looked
up by the source spec's ``credentials_ref`` -- there is no inline/raw
credentials path any more.

``RemoteFileSourceSession`` also caches one client per ``(kind,
credentials_ref)`` for the caller's lifetime -- a source with N files opens
one S3/SFTP connection total, not one per file read.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import pandas as pd
from sqlalchemy.orm import Session

from api.services.file_source import _read_tabular_bytes, read_tabular, resolve_allowed_path
from etl_framework.reconciliation.file_mapping import (
    DiscoveredFile,
    FileSourceSpec,
    discover_local_files,
    discover_s3_files,
    discover_sftp_files,
)
from etl_framework.repository.repository import FileServerProfileRepository, ResolvedFileServerProfile

_CONTENT_MATCH_READ_LIMIT = 65536


def resolve_file_server_profile(db: Session, spec: FileSourceSpec) -> ResolvedFileServerProfile | None:
    if not spec.credentials_ref:
        return None
    profile = FileServerProfileRepository(db).get_decrypted_by_name(spec.credentials_ref)
    if profile is None:
        raise ValueError(f"No file server profile named '{spec.credentials_ref}' -- create one in File Servers before running this job")
    # scp locations reuse the sftp client/fields, so either kind is valid for an sftp-kind spec;
    # an s3 location must be backed by an s3-kind profile and vice versa.
    compatible = {profile.kind} | ({"sftp", "scp"} if profile.kind in ("sftp", "scp") else set())
    if spec.kind not in compatible:
        raise ValueError(f"File server profile '{spec.credentials_ref}' is kind '{profile.kind}', not '{spec.kind}'")
    return profile


def build_s3_client(profile: ResolvedFileServerProfile, spec: FileSourceSpec):
    try:
        import boto3
        from botocore.config import Config as BotoConfig
    except ImportError as exc:
        raise RuntimeError("boto3 is required for multi_file S3 sources") from exc
    client_kwargs: dict[str, Any] = {
        "aws_access_key_id": profile.aws_access_key_id,
        "aws_secret_access_key": profile.aws_secret_access_key,
        "aws_session_token": profile.aws_session_token,
        "region_name": profile.region_name,
        "endpoint_url": profile.endpoint_url,
    }
    if profile.endpoint_url:
        # A custom endpoint_url means a non-AWS, S3-compatible target (MinIO,
        # on-prem object storage) -- these commonly reject the virtual-hosted-
        # style bucket addressing boto3 otherwise defaults to whenever a
        # custom endpoint is set. Real AWS never sets endpoint_url, so this
        # never affects the existing real-AWS path.
        client_kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
    return boto3.client("s3", **client_kwargs)


def build_sftp_client(profile: ResolvedFileServerProfile, spec: FileSourceSpec):
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is required for multi_file SFTP sources") from exc
    transport = paramiko.Transport((profile.host, int(profile.port or 22)))
    try:
        if profile.auth_method == "private_key":
            key = paramiko.PKey.from_private_key(io.StringIO(profile.private_key), password=profile.key_passphrase or None)
            transport.connect(username=profile.username, pkey=key)
        else:
            transport.connect(username=profile.username, password=profile.password)
        presented = transport.get_remote_server_key()
        fingerprint = hashlib.sha256(presented.asbytes()).hexdigest()
        if not profile.host_key_fingerprint or fingerprint != profile.host_key_fingerprint:
            raise RuntimeError(
                f"Host key verification failed for file server profile '{profile.name}' -- "
                "run Test Connection in File Servers to review and pin the presented fingerprint"
            )
    except Exception:
        transport.close()
        raise
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

    def __init__(self, db: Session) -> None:
        self._db = db
        self._clients: dict[tuple[str, str | None], Any] = {}

    def _client_for(self, spec: FileSourceSpec):
        key = (spec.kind, spec.credentials_ref)
        if key not in self._clients:
            profile = resolve_file_server_profile(self._db, spec)
            if spec.kind == "s3":
                self._clients[key] = build_s3_client(profile, spec)
            elif spec.kind == "sftp":
                self._clients[key] = build_sftp_client(profile, spec)
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

    def read_text(self, file: DiscoveredFile, spec: FileSourceSpec) -> str:
        """Read up to _CONTENT_MATCH_READ_LIMIT bytes of `file` as text, for
        file_watcher content matching. Undecodable bytes are dropped rather
        than raising -- a watcher polling mid-write may see a partial or
        binary-looking snapshot, which should read as "no match" not error.
        """
        raw = self._read_bytes(file, spec)
        return raw.decode("utf-8", errors="ignore")

    def _read_bytes(self, file: DiscoveredFile, spec: FileSourceSpec) -> bytes:
        if spec.kind == "local":
            with open(file.path, "rb") as fh:
                return fh.read(_CONTENT_MATCH_READ_LIMIT)
        if spec.kind == "s3":
            import botocore.exceptions

            parsed = urlparse(file.path)
            try:
                obj = self._client_for(spec).get_object(
                    Bucket=parsed.netloc,
                    Key=unquote(parsed.path.lstrip("/")),
                    Range=f"bytes=0-{_CONTENT_MATCH_READ_LIMIT - 1}",
                )
            except botocore.exceptions.ClientError as exc:
                # A 0-byte object has no valid byte at offset 0, so S3 (and
                # S3-compatible stores) reject the Range header above with
                # InvalidRange -- treat that the same as an empty read rather
                # than raising, matching the local/sftp branches below which
                # already return b"" for an empty file.
                if exc.response.get("Error", {}).get("Code") == "InvalidRange":
                    return b""
                raise
            return obj["Body"].read()
        if spec.kind == "sftp":
            client = self._client_for(spec)
            with client.open(file.path, "rb") as fh:
                return fh.read(_CONTENT_MATCH_READ_LIMIT)
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
