"""Shared S3/SFTP client construction, credential resolution, and file
discovery/read dispatch for ``multi_file`` reconciliation jobs.

Both ``RunExecutor`` (live job execution, ``api/services/run_executor.py``)
and ``difference_export`` (recomputing a run's full diff set for export/HTML
reports, ``api/services/difference_export.py``) need to discover and read
files from the same four source kinds (``local``, ``s3``, ``sftp``, ``smb``).
This module is the single place that owns that logic, so the two call sites
don't each re-derive their own copy of client construction and credential
lookup.

Credentials come from a persisted, encrypted ``FileServerProfile`` (see
``etl_framework.repository.repository.FileServerProfileRepository``) looked
up by the source spec's ``credentials_ref`` -- there is no inline/raw
credentials path any more.

``RemoteFileSourceSession`` also caches one client per ``(kind,
credentials_ref)`` for the caller's lifetime -- a source with N files opens
one S3/SFTP/SMB connection total, not one per file read.
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import threading
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
    parse_unc_root,
)
from etl_framework.repository.repository import FileServerProfileRepository, ResolvedFileServerProfile


class SmbConnectError(RuntimeError):
    """Raised when connecting to a UNC share fails: bad credentials, an
    unreachable host, a missing share, or (on a non-Windows atom server) the
    platform itself. Intended to be recognised by
    ``file_transfer.is_transport_error`` once ``file_transfer.py`` gains smb
    support (not yet wired up)."""


def _scrub_smb_error(text: str, password: str | None) -> str:
    if password:
        text = text.replace(password, "***")
    return text


def _net_use(resource: str, username: str | None, password: str | None) -> None:
    """Authenticate to a UNC resource (``\\\\server\\share``) via Windows'
    built-in ``net use``. Raises ``SmbConnectError`` on any failure, its
    message scrubbed of ``password``."""
    if not password:
        raise SmbConnectError("SMB profile requires a password")
    args = ["net", "use", resource, password]
    if username:
        args.append("/user:" + username)
    try:
        # stdin=DEVNULL: net use prompts interactively on several failure
        # modes (bad password, "continue connection? Y/N") -- under a
        # console-launched uvicorn that inherits the server's real stdin,
        # that would stall the whole 30s timeout waiting on a prompt no one
        # can answer instead of failing fast with the real error. Matches
        # _net_use_delete below, which already does this.
        result = subprocess.run(
            args, capture_output=True, text=True, errors="replace", timeout=30, stdin=subprocess.DEVNULL,
        )
    except Exception as exc:
        # `from None` deliberately drops __cause__ -- the raw exception (e.g.
        # subprocess.TimeoutExpired) embeds the full argv, password included,
        # in its own __str__/repr, and keeping it chained would print that
        # unscrubbed the moment anything renders a traceback or logs with
        # exc_info=True, even though the message above has been scrubbed.
        raise SmbConnectError(_scrub_smb_error(str(exc), password)) from None
    if result.returncode != 0:
        message = _scrub_smb_error((result.stderr or result.stdout or "").strip(), password)
        raise SmbConnectError(f"net use {resource} failed: {message}")


def _net_use_delete(resource: str) -> None:
    """Best-effort ``net use ... /delete`` -- never raises."""
    try:
        subprocess.run(
            ["net", "use", resource, "/delete", "/y"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        pass


_smb_host_locks: dict[str, threading.Lock] = {}
_smb_host_locks_guard = threading.Lock()


def _smb_host_lock(host: str) -> threading.Lock:
    """Return the process-wide lock serializing ``net use`` connections to
    ``host`` (Windows allows only one credential per server at a time),
    creating it on first use. Keyed case-insensitively (``strip().casefold()``)
    since Windows server names are case-insensitive -- otherwise
    "FILESERVER01" and "fileserver01" would get different locks and
    serialization between them would be defeated."""
    key = host.strip().casefold()
    with _smb_host_locks_guard:
        if key not in _smb_host_locks:
            _smb_host_locks[key] = threading.Lock()
        return _smb_host_locks[key]


# Concurrent smb access to one host still serializes by design (spec §9) --
# RunExecutor runs multi_file pairs concurrently, and two pairs whose smb
# sources/destinations share a host will legitimately queue up on this lock,
# not race it. This timeout is a safety net against a genuinely stuck
# connection (a killed net.exe, a hung server), not a concurrency fix, so it
# defaults high enough to give real concurrent work room to finish; override
# with the SMB_LOCK_TIMEOUT_SECONDS env var if a deployment needs it tighter.
_SMB_LOCK_TIMEOUT_SECONDS_DEFAULT = 300


def _parse_smb_lock_timeout_seconds(raw: str | None) -> int:
    """Parse the ``SMB_LOCK_TIMEOUT_SECONDS`` env var, falling back to the
    default for anything that isn't a genuinely usable positive timeout: a
    non-integer value would otherwise raise at import time and take the
    whole module down; ``0`` would make ``Lock.acquire(timeout=0)`` fail
    instantly on any contention -- the opposite of "no timeout" someone
    setting ``0`` would likely intend; and a negative value would make
    ``acquire()`` itself raise a bare ``ValueError`` outside this module's
    own error handling, landing as an uncategorized error instead of a
    clean ``SmbConnectError``."""
    try:
        value = int(raw) if raw is not None else _SMB_LOCK_TIMEOUT_SECONDS_DEFAULT
    except ValueError:
        return _SMB_LOCK_TIMEOUT_SECONDS_DEFAULT
    return value if value > 0 else _SMB_LOCK_TIMEOUT_SECONDS_DEFAULT


_SMB_LOCK_TIMEOUT_SECONDS = _parse_smb_lock_timeout_seconds(os.environ.get("SMB_LOCK_TIMEOUT_SECONDS"))


class SmbSession:
    """A connected host, held for the lifetime of one
    ``RemoteFileSourceSession``. A single session can end up authenticated to
    more than one share on its host -- ``RemoteFileSourceSession`` reuses one
    ``SmbSession`` per (host, identity) rather than per share, since Windows
    allows only one identity per server, not per share -- so it tracks every
    ``\\\\server\\share`` resource it has ``net use``'d via ``ensure_resource``
    and ``close()`` tears all of them down, not just the first. ``close()``
    also releases the per-host lock -- picked up automatically by
    ``close_remote_client``'s generic ``getattr(client, "close", None)``."""

    def __init__(self, resource: str, lock: "threading.Lock", host: str) -> None:
        self.resource = resource
        self.resources = {resource}
        self.host = host
        self._lock = lock

    def ensure_resource(self, resource: str, username: str | None, password: str | None) -> None:
        """``net use`` an additional share on this session's already-locked
        host, if not already connected. Safe without acquiring the host lock
        again here -- this session already holds it for its whole life, and
        that's what makes it exclusive to connect an additional share on the
        same host while nothing else can race it."""
        if resource not in self.resources:
            _net_use(resource, username, password)
            self.resources.add(resource)

    def close(self) -> None:
        try:
            for resource in self.resources:
                _net_use_delete(resource)
        finally:
            self._lock.release()


def connect_smb_share(
    profile: ResolvedFileServerProfile | None, spec: FileSourceSpec, lock_timeout_seconds: float | None = None,
) -> SmbSession:
    """``lock_timeout_seconds`` overrides ``_SMB_LOCK_TIMEOUT_SECONDS`` for
    just this call when given -- used by callers (like a preview route) that
    need to fail fast on a contended host lock rather than wait for a real
    job's much longer default."""
    if profile is None:
        raise ValueError(f"'{spec.kind}' source requires credentials_ref, but none was set")
    if os.name != "nt":
        raise SmbConnectError("SMB transfers require the atom server to run on Windows")
    try:
        server, share = parse_unc_root(spec.root)
    except ValueError as exc:
        raise SmbConnectError(str(exc)) from None
    if profile.host and profile.host.lower() != server.lower():
        raise SmbConnectError(
            f"file_transfer source/destination root '\\\\{server}\\{share}' does not match "
            f"file server profile '{profile.name}''s host '{profile.host}'"
        )
    resource = f"\\\\{server}\\{share}"
    lock = _smb_host_lock(server)
    timeout = _SMB_LOCK_TIMEOUT_SECONDS if lock_timeout_seconds is None else lock_timeout_seconds
    if not lock.acquire(timeout=timeout):
        raise SmbConnectError(f"Timed out waiting for another SMB operation on host '{server}' to finish")
    try:
        _net_use(resource, profile.username, profile.password)
    except Exception:
        # A timed-out/killed net.exe can leave a half-established connection
        # that would block the next identity's connect to this host, so
        # clean it up (best-effort) before releasing the lock.
        _net_use_delete(resource)
        lock.release()
        raise
    return SmbSession(resource, lock, server)


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
    """Discovers and reads files for a ``local``/``s3``/``sftp``/``smb``
    source spec, reusing one client per ``(kind, credentials_ref)`` across
    every call for the lifetime of this session -- construct one per job
    execution, use it for both sides' discovery and every subsequent file
    read, then ``close()`` it (or use as a context manager) once the job is
    done with it.
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
        # Resolved profile per client key (None when the spec has no profile);
        # feeds location_key_for.
        self._profiles: dict[tuple[str, str | None], ResolvedFileServerProfile | None] = {}

    def _client_for(self, spec: FileSourceSpec, *, lock_timeout_seconds: float | None = None):
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
            elif spec.kind == "smb":
                if profile is None:
                    raise ValueError(f"'{spec.kind}' source requires credentials_ref, but none was set")
                try:
                    server, share = parse_unc_root(spec.root)
                except ValueError as exc:
                    raise SmbConnectError(str(exc)) from None
                # Same consistency check connect_smb_share does -- run here
                # too because the identity-match reuse branch below skips
                # connect_smb_share entirely, and without this a profile
                # whose host field doesn't actually match this spec's UNC
                # root would silently reuse another share's connection
                # instead of being rejected.
                if profile.host and profile.host.lower() != server.lower():
                    raise SmbConnectError(
                        f"file_transfer source/destination root '\\\\{server}\\{share}' does not match "
                        f"file server profile '{profile.name}''s host '{profile.host}'"
                    )
                resource = f"\\\\{server}\\{share}"
                reused_client = None
                for (other_kind, other_ref), other_client in self._clients.items():
                    if not (
                        other_kind == "smb"
                        and other_ref != spec.credentials_ref
                        and isinstance(other_client, SmbSession)
                        and other_client.host.casefold() == server.casefold()
                    ):
                        continue
                    # Two different profile names on the same host are only a
                    # real conflict if they're not actually the same identity
                    # -- a normal "one profile per share" setup can easily
                    # have two profile rows for the same host+username+
                    # password, and that must not be rejected. When they
                    # really are the same identity, reuse that side's
                    # already-open connection instead of opening a second
                    # net use to the same host: a second connect would
                    # otherwise contend forever on the host's lock, which the
                    # first, still-open connection holds for its whole life.
                    other_profile = self._profiles.get((other_kind, other_ref))
                    same_identity = other_profile is not None and (
                        # profile names are unique, so two different refs are
                        # always two different rows -- kept for clarity/
                        # future-proofing in case that constraint ever loosens.
                        other_profile.id == profile.id
                        or (other_profile.username, other_profile.password) == (profile.username, profile.password)
                    )
                    if not same_identity:
                        raise SmbConnectError(
                            f"This run already holds an SMB connection to host '{server}' via file server profile "
                            f"'{other_ref}' -- Windows allows only one active identity per server, so profile "
                            f"'{spec.credentials_ref}' cannot also connect to it in the same run. Use the same "
                            "file server profile for both sides, or point one side at a different host."
                        )
                    reused_client = other_client
                    break
                if reused_client is not None:
                    # The identity matches, but this spec's own share might
                    # still be a different one on that host (e.g. two specs
                    # sharing a profile but pointing at \\host\in and
                    # \\host\out) -- make sure it's net use'd too before
                    # handing the session back.
                    reused_client.ensure_resource(resource, profile.username, profile.password)
                    self._clients[key] = reused_client
                else:
                    self._clients[key] = connect_smb_share(profile, spec, lock_timeout_seconds)
            else:
                raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
            self._profiles[key] = profile
        elif spec.kind == "smb":
            # A cache hit on (kind, credentials_ref) alone isn't quite enough
            # for smb: a source spec and a destination spec can share the
            # same credentials_ref profile while pointing at two different
            # shares on that profile's host (the cache key doesn't include
            # the share). Make sure THIS spec's exact resource has been net
            # use'd too before handing back the already-cached session.
            client = self._clients[key]
            profile = self._profiles.get(key)
            if isinstance(client, SmbSession) and profile is not None:
                try:
                    server, share = parse_unc_root(spec.root)
                except ValueError as exc:
                    raise SmbConnectError(str(exc)) from None
                # Same consistency check the miss/reuse paths above already
                # do -- without it, a spec whose UNC root points at a totally
                # different server than the cached profile would silently
                # net use that other server here, never taking its host
                # lock (invisible to the same-host conflict check above) and
                # leaving client.host inaccurate relative to what it's
                # actually connected to.
                if profile.host and profile.host.lower() != server.lower():
                    raise SmbConnectError(
                        f"file_transfer source/destination root '\\\\{server}\\{share}' does not match "
                        f"file server profile '{profile.name}''s host '{profile.host}'"
                    )
                if client.host.casefold() != server.casefold():
                    raise SmbConnectError(
                        f"file_transfer source/destination root '\\\\{server}\\{share}' targets a different host "
                        f"than the existing SMB connection for this credentials_ref ('{client.host}')"
                    )
                client.ensure_resource(f"\\\\{server}\\{share}", profile.username, profile.password)
        return self._clients[key]

    def client_for(self, spec: FileSourceSpec):
        """Public accessor for the cached S3/SFTP/SMB client of ``spec`` (used
        by ``api.services.file_transfer``). Same one-client-per-(kind,
        credentials_ref) caching as every other call on this session."""
        return self._client_for(spec)

    def location_key_for(self, spec: FileSourceSpec) -> tuple | None:
        """Where ``spec`` really points, independent of the profile's name, so
        two differently named profiles for the same server compare equal:
        ``("s3", endpoint_url)`` (empty = AWS; bucket names are global there),
        ``("sftp", host, port, username)``, or ``("smb", server, share)``.
        None for local specs or a spec with no resolved profile."""
        if spec.kind not in ("s3", "sftp", "smb"):
            return None
        if spec.kind == "smb":
            # Computing the smb location key only needs the resolved profile
            # and the UNC root, not a live connection -- unlike s3/sftp,
            # calling self._client_for(spec) here would open a real net use
            # connection that holds a process-wide per-host lock for the
            # rest of this session's life (Windows allows only one net use
            # identity per server at a time), so comparing two different
            # profiles on the same host would self-deadlock on the second
            # spec's lock acquisition.
            from etl_framework.repository.database import SessionLocal

            with SessionLocal() as resolve_db:
                profile = resolve_file_server_profile(resolve_db, spec)
            if profile is None:
                return None
            # A UNC root is always fully qualified (never account-relative
            # the way an SFTP root can be), so the account isn't part of the
            # location -- two profiles for the same server+share are the
            # same object regardless of which account each uses.
            try:
                server, share = parse_unc_root(spec.root)
            except ValueError as exc:
                raise SmbConnectError(str(exc)) from None
            return ("smb", server.lower(), share.lower())
        self._client_for(spec)
        profile = self._profiles.get((spec.kind, spec.credentials_ref))
        if profile is None:
            return None
        if spec.kind == "s3":
            # Bucket names are treated as global per endpoint (true for AWS and
            # MinIO); tenant-scoped gateways such as Ceph RGW would need the
            # tenant in this key.
            return ("s3", (profile.endpoint_url or "").rstrip("/").lower())
        # The SSH username is part of the location: relative roots resolve
        # against each account's own home and chrooted accounts can both use
        # the same absolute path.
        return ("sftp", (profile.host or "").lower(), int(profile.port or 22), profile.username or "")

    def discover(
        self, spec: FileSourceSpec, *, recursive: bool = False, lock_timeout_seconds: float | None = None,
    ) -> list[DiscoveredFile]:
        """``lock_timeout_seconds`` (smb only) overrides the default smb
        host-lock wait for just this call -- None uses the module default
        (a real job's long, patient wait); pass a short value for a
        request-thread caller like a preview route that must fail fast
        instead of blocking behind a real running job on the same host."""
        if spec.kind == "local":
            root = resolve_allowed_path(spec.root)
            return discover_local_files(root, spec.pattern, recursive=recursive)
        if spec.kind == "s3":
            return discover_s3_files(self._client_for(spec), spec.root, spec.pattern, recursive=recursive)
        if spec.kind == "sftp":
            return discover_sftp_files(self._client_for(spec), spec.root, spec.pattern, recursive=recursive)
        if spec.kind == "smb":
            # connects (net use); the UNC path is then a normal filesystem path
            self._client_for(spec, lock_timeout_seconds=lock_timeout_seconds)
            return discover_local_files(Path(spec.root), spec.pattern, recursive=recursive)
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
        if spec.kind == "smb":
            self._client_for(spec)
            with open(file.path, "rb") as fh:
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
        if spec.kind == "smb":
            self._client_for(spec)
            with open(file.path, "rb") as fh:
                return fh.read(_CONTENT_MATCH_READ_LIMIT)
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")

    def close(self) -> None:
        try:
            closed_ids: set[int] = set()
            for client in self._clients.values():
                # An smb client can be cached under more than one
                # credentials_ref key when _client_for detects two profiles
                # for the same host+identity and reuses the same SmbSession
                # (see its smb branch) -- closing the same object twice would
                # double-release its per-host lock (a plain threading.Lock
                # raises RuntimeError on an unlocked release).
                if id(client) in closed_ids:
                    continue
                closed_ids.add(id(client))
                try:
                    close_remote_client(client)
                except Exception:
                    pass  # one bad client must not leave the others unclosed
        finally:
            self._clients.clear()
            self._profiles.clear()

    def __enter__(self) -> "RemoteFileSourceSession":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.close()
        return False
