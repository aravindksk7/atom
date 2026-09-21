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
    if profile is None:
        raise ValueError(f"'{spec.kind}' source requires credentials_ref, but none was set")
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


def _load_sftp_private_key(private_key_text: str, password: str | None):
    """Parse PEM/OpenSSH private key text into the matching paramiko key
    subclass. ``paramiko.PKey.from_private_key`` only works when called on a
    concrete subclass (``RSAKey``, ``Ed25519Key``, ``ECDSAKey``) -- calling it
    on the abstract ``PKey`` base raises ``TypeError`` in the paramiko version
    this project pins, so the key type must be sniffed first. Mirrors the
    detection ``paramiko.PKey.from_path`` uses for file-based keys, but stays
    in-memory (no disk write of the decrypted key material)."""
    import paramiko
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

    data = private_key_text.encode()
    pwd = password.encode() if password else None
    try:
        loaded = serialization.load_ssh_private_key(data=data, password=pwd)
    except ValueError:
        loaded = serialization.load_pem_private_key(data=data, password=pwd)

    if isinstance(loaded, rsa.RSAPrivateKey):
        key_class = paramiko.RSAKey
    elif isinstance(loaded, ed25519.Ed25519PrivateKey):
        key_class = paramiko.Ed25519Key
    elif isinstance(loaded, ec.EllipticCurvePrivateKey):
        key_class = paramiko.ECDSAKey
    else:
        raise ValueError(f"Unsupported SSH private key type: {type(loaded).__name__}")
    return key_class.from_private_key(io.StringIO(private_key_text), password=password)


def build_sftp_client(profile: ResolvedFileServerProfile, spec: FileSourceSpec):
    if profile is None:
        raise ValueError(f"'{spec.kind}' source requires credentials_ref, but none was set")
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko is required for multi_file SFTP sources") from exc
    transport = paramiko.Transport((profile.host, int(profile.port or 22)))
    try:
        # start_client() only negotiates the SSH transport and host key -- it
        # does NOT authenticate. That lets us verify the host key fingerprint
        # BEFORE any credential material (password or key signature) goes
        # over the wire, so a MITM'd/unpinned host is rejected before it ever
        # sees the password or gets a chance to request a key signature.
        transport.start_client()
        presented = transport.get_remote_server_key()
        fingerprint = hashlib.sha256(presented.asbytes()).hexdigest()
        if not profile.host_key_fingerprint or fingerprint != profile.host_key_fingerprint:
            raise RuntimeError(
                f"Host key verification failed for file server profile '{profile.name}' -- "
                "run Test Connection in File Servers to review and pin the presented fingerprint"
            )
        if profile.auth_method == "private_key":
            key = _load_sftp_private_key(profile.private_key, profile.key_passphrase or None)
            transport.auth_publickey(profile.username, key)
        else:
            transport.auth_password(profile.username, profile.password)
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
        # Not used for credential resolution (see _client_for) -- that opens
        # its own short-lived Session instead, since resolve_file_server_profile
        # is a self-contained read and RunExecutor's multi_file pairs run this
        # session's methods concurrently across worker threads while sharing
        # one caller-supplied Session, which SQLAlchemy documents as unsafe.
        # Kept for API stability / potential future use by other methods.
        self._db = db
        self._clients: dict[tuple[str, str | None], Any] = {}

    def _client_for(self, spec: FileSourceSpec):
        key = (spec.kind, spec.credentials_ref)
        if key not in self._clients:
            # Resolve credentials on a fresh, thread-local Session rather than
            # self._db: RunExecutor runs each multi_file pair concurrently in
            # a worker thread, and every pair's RemoteFileSourceSession shares
            # the SAME caller Session object. SQLAlchemy's Session is not safe
            # for concurrent use across threads, so reusing self._db here was
            # producing intermittent spurious "No file server profile" /
            # internal SQLAlchemy errors under concurrency. This lookup is a
            # self-contained read that doesn't need to join the caller's
            # transaction, so a short-lived session scoped to just this call
            # sidesteps the race entirely.
            from etl_framework.repository.database import SessionLocal

            with SessionLocal() as resolve_db:
                profile = resolve_file_server_profile(resolve_db, spec)
            if spec.kind == "s3":
                self._clients[key] = build_s3_client(profile, spec)
            elif spec.kind == "sftp":
                self._clients[key] = build_sftp_client(profile, spec)
            else:
                raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
        return self._clients[key]

    def client_for(self, spec: FileSourceSpec):
        """Public accessor for the cached S3/SFTP client of ``spec`` (used by
        ``api.services.file_transfer``). Same one-client-per-(kind,
        credentials_ref) caching as every other call on this session."""
        return self._client_for(spec)

    def discover(self, spec: FileSourceSpec, *, recursive: bool = False) -> list[DiscoveredFile]:
        if spec.kind == "local":
            root = resolve_allowed_path(spec.root)
            return discover_local_files(root, spec.pattern, recursive=recursive)
        if spec.kind == "s3":
            return discover_s3_files(self._client_for(spec), spec.root, spec.pattern, recursive=recursive)
        if spec.kind == "sftp":
            return discover_sftp_files(self._client_for(spec), spec.root, spec.pattern, recursive=recursive)
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
