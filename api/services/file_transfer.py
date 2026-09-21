"""Copy files between ``local``, ``s3`` and ``sftp`` locations for
``file_transfer`` jobs (see docs/superpowers/specs/2026-09-21-file-transfer-job-design.md).

Structure:

* One small *endpoint* class per location kind (``LocalEndpoint``,
  ``S3Endpoint``, ``SftpEndpoint``) with the same duck-typed interface, so a
  copy is kind-agnostic: ``relative_of``, ``destination_for``, ``identity``,
  ``exists``, ``size``, ``open_read``, ``write``, ``delete``.
* ``plan_transfer`` -- pure planning: destination keys, safety checks,
  ``on_exists`` handling. Writes nothing.
* ``run_transfer`` -- streams each planned copy and verifies its size.

Writes are atomic from the point of view of anything watching the destination
(local/sftp write ``<name>.part`` then rename; S3 uploads only become visible
on completion), so a ``file_watcher`` or reconciliation job never sees a
half-written file. Credentials and clients come from
``RemoteFileSourceSession`` (see ``multi_file_remote.py``).
"""
from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlparse
from uuid import uuid4

from api.services.file_source import resolve_allowed_path
from etl_framework.reconciliation.file_mapping import DiscoveredFile

CHUNK_SIZE = 1024 * 1024
PART_SUFFIX = ".part"


class TransferError(Exception):
    """A failure that should surface as a FAILED step: a plan-time rejection
    (collision, unsafe path, no files) or a per-file copy failure. Connection
    and credential problems are left as their original exception type so the
    executor reports them as ERROR."""


def _copy_stream(stream: Any, sink: Any) -> None:
    while True:
        chunk = stream.read(CHUNK_SIZE)
        if not chunk:
            return
        sink.write(chunk)


# -- Local -------------------------------------------------------------------

_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _windows_illegal_name(component: str) -> str | None:
    """Reason a single path component cannot be created on Windows, or None."""
    if any(ord(ch) < 32 or ch in '<>:"|?*' for ch in component):
        return "contains a character Windows does not allow"
    if component != component.rstrip(" ."):
        return "ends with a space or dot"
    if component.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        return "is a reserved Windows device name"
    return None


class LocalEndpoint:
    kind = "local"

    def __init__(self, root: str) -> None:
        self.credentials_ref = None
        # Raises HTTPException(400) when root is outside the server allowlist.
        self.root = resolve_allowed_path(root)

    def relative_of(self, file: DiscoveredFile) -> str:
        try:
            return Path(file.path).resolve().relative_to(self.root).as_posix()
        except ValueError:
            raise TransferError(f"'{file.path}' is not under source root '{self.root}'") from None

    def destination_for(self, relative: str) -> str:
        target = (self.root / relative).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            raise TransferError(
                f"destination path for '{relative}' escapes destination root '{self.root}'"
            ) from None
        if os.name == "nt":
            # After the escape check so traversal/absolute inputs keep their
            # more specific "escapes destination root" error.
            for component in relative.split("/"):
                reason = _windows_illegal_name(component)
                if reason:
                    raise TransferError(f"destination name '{component}' in '{relative}' {reason}")
        return str(target)

    def identity(self, path: str) -> tuple:
        return ("local", None, os.path.normcase(str(Path(path).resolve())))

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def size(self, path: str) -> int:
        return Path(path).stat().st_size

    def open_read(self, path: str):
        return open(path, "rb")

    def write(self, path: str, stream: Any) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f"{dest.name}.{uuid4().hex}{PART_SUFFIX}")
        try:
            with open(part, "wb") as fh:
                _copy_stream(stream, fh)
            os.replace(part, dest)
        except BaseException:
            part.unlink(missing_ok=True)
            raise

    def delete(self, path: str) -> None:
        Path(path).unlink(missing_ok=True)


# -- S3 ----------------------------------------------------------------------

class S3Endpoint:
    """Paths are ``s3://bucket/key`` URIs with the key percent-quoted, the
    same shape ``discover_s3_files`` puts in ``DiscoveredFile.path``."""

    kind = "s3"

    def __init__(self, root: str, client: Any, credentials_ref: str | None) -> None:
        parsed = urlparse(root)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError("S3 root must be s3://bucket/prefix")
        self.client = client
        self.credentials_ref = credentials_ref
        self.bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")
        self.prefix = prefix if not prefix or prefix.endswith("/") else prefix + "/"

    @staticmethod
    def _split(uri: str) -> tuple[str, str]:
        parsed = urlparse(uri)
        return parsed.netloc, unquote(parsed.path.lstrip("/"))

    def relative_of(self, file: DiscoveredFile) -> str:
        _, key = self._split(file.path)
        if not self.prefix:
            return key
        if not key.startswith(self.prefix):
            raise TransferError(
                f"'{file.path}' is not under source root 's3://{self.bucket}/{self.prefix}'"
            )
        return key[len(self.prefix):]

    def destination_for(self, relative: str) -> str:
        return f"s3://{self.bucket}/{quote(self.prefix + relative, safe='/')}"

    def identity(self, path: str) -> tuple:
        bucket, key = self._split(path)
        return ("s3", self.credentials_ref, f"{bucket}/{key}")

    def exists(self, path: str) -> bool:
        import botocore.exceptions

        bucket, key = self._split(path)
        try:
            self.client.head_object(Bucket=bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def size(self, path: str) -> int:
        bucket, key = self._split(path)
        return int(self.client.head_object(Bucket=bucket, Key=key)["ContentLength"])

    def open_read(self, path: str):
        bucket, key = self._split(path)
        return self.client.get_object(Bucket=bucket, Key=key)["Body"]

    def write(self, path: str, stream: Any) -> None:
        bucket, key = self._split(path)
        self.client.upload_fileobj(stream, bucket, key)

    def delete(self, path: str) -> None:
        bucket, key = self._split(path)
        self.client.delete_object(Bucket=bucket, Key=key)


# -- SFTP / SCP --------------------------------------------------------------

class SftpEndpoint:
    """``scp`` locations are normalized to ``sftp`` before reaching here (see
    ``file_transfer_spec.parse_file_transfer_params``)."""

    kind = "sftp"

    def __init__(self, root: str, client: Any, credentials_ref: str | None) -> None:
        self.client = client
        self.credentials_ref = credentials_ref
        self.root = root.rstrip("/") or "/"

    def relative_of(self, file: DiscoveredFile) -> str:
        prefix = self.root.rstrip("/") + "/"
        if not file.path.startswith(prefix):
            raise TransferError(f"'{file.path}' is not under source root '{self.root}'")
        return file.path[len(prefix):]

    def destination_for(self, relative: str) -> str:
        target = posixpath.normpath(posixpath.join(self.root, relative))
        if target != self.root and not target.startswith(self.root.rstrip("/") + "/"):
            raise TransferError(
                f"destination path for '{relative}' escapes destination root '{self.root}'"
            )
        return target

    def identity(self, path: str) -> tuple:
        return ("sftp", self.credentials_ref, posixpath.normpath(path))

    def exists(self, path: str) -> bool:
        try:
            self.client.stat(path)
        except FileNotFoundError:
            return False
        return True

    def size(self, path: str) -> int:
        return int(self.client.stat(path).st_size)

    def open_read(self, path: str):
        return self.client.open(path, "rb")

    def _make_dirs(self, directory: str) -> None:
        current = ""
        for part in PurePosixPath(directory).parts:
            current = posixpath.join(current, part) if current else part
            try:
                self.client.stat(current)
            except FileNotFoundError:
                try:
                    self.client.mkdir(current)
                except OSError:
                    self.client.stat(current)  # lost a race: fine if it exists now, re-raises otherwise

    def _discard(self, path: str) -> None:
        try:
            self.client.remove(path)
        except Exception:
            pass

    def write(self, path: str, stream: Any) -> None:
        self._make_dirs(posixpath.dirname(path))
        part = f"{path}.{uuid4().hex}{PART_SUFFIX}"
        try:
            self.client.putfo(stream, part)
            try:
                self.client.posix_rename(part, path)
            except OSError as exc:
                if exc.errno is not None:
                    raise
                # Server has no posix-rename extension (paramiko raises a plain
                # IOError with no errno). Plain rename refuses to replace an
                # existing file, so move the old one aside and restore it if the
                # swap fails: the destination is never left missing.
                try:
                    self.client.rename(part, path)  # works when the destination is absent
                except OSError:
                    backup = f"{path}.{uuid4().hex}.bak"
                    self.client.rename(path, backup)
                    try:
                        self.client.rename(part, path)
                    except BaseException:
                        self.client.rename(backup, path)
                        raise
                    self._discard(backup)
        except BaseException:
            self._discard(part)
            raise

    def delete(self, path: str) -> None:
        try:
            self.client.remove(path)
        except FileNotFoundError:
            pass


# -- Planning ----------------------------------------------------------------

@dataclass(frozen=True)
class PlannedCopy:
    source: DiscoveredFile
    destination: str
    relative: str


@dataclass
class TransferPlan:
    to_copy: list[PlannedCopy] = field(default_factory=list)
    skipped: list[PlannedCopy] = field(default_factory=list)


def _validate_relative(relative: str) -> str:
    unsafe = (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(part in ("", ".", "..") for part in relative.split("/"))
    )
    if unsafe:
        raise TransferError(f"unsafe destination path '{relative}' -- refusing to write outside the destination root")
    return relative


def plan_transfer(
    files: list[DiscoveredFile],
    source: Any,
    destination: Any,
    *,
    on_exists: str,
    preserve_structure: bool,
) -> TransferPlan:
    """Decide what to copy without writing anything. Raises ``TransferError``
    when the plan is unsafe or (``on_exists="fail"``) would collide with
    existing destination files."""
    entries: list[PlannedCopy] = []
    seen: dict[tuple, str] = {}
    for file in files:
        relative = source.relative_of(file) if preserve_structure else file.file_name
        _validate_relative(relative)
        target = destination.destination_for(relative)
        target_identity = destination.identity(target)
        if source.identity(file.path) == target_identity:
            raise TransferError(f"source and destination are the same object: {file.path}")
        if target_identity in seen:
            raise TransferError(
                f"duplicate destination '{target}' for '{seen[target_identity]}' and '{file.path}' -- "
                "enable preserve_structure or narrow the pattern"
            )
        seen[target_identity] = file.path
        entries.append(PlannedCopy(source=file, destination=target, relative=relative))

    plan = TransferPlan()
    existing: list[PlannedCopy] = []
    for entry in entries:
        if on_exists == "overwrite" or not destination.exists(entry.destination):
            plan.to_copy.append(entry)
        elif on_exists == "skip":
            plan.skipped.append(entry)
        else:
            existing.append(entry)
    if existing:
        shown = ", ".join(entry.destination for entry in existing[:5])
        more = f" and {len(existing) - 5} more" if len(existing) > 5 else ""
        raise TransferError(
            f"destination already contains {len(existing)} file(s) (on_exists=fail): {shown}{more}"
        )
    return plan


# -- Execution ---------------------------------------------------------------

class _CountingReader:
    """Wraps a readable stream and counts the bytes read through it."""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        data = self._raw.read(size)
        self.count += len(data)
        return data


@dataclass
class TransferOutcome:
    copied: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    bytes_copied: int = 0
    failed_file: str | None = None
    error: str | None = None


def _copy_one(entry: PlannedCopy, source: Any, destination: Any) -> int:
    expected = source.size(entry.source.path)
    reader = source.open_read(entry.source.path)
    try:
        counting = _CountingReader(reader)
        destination.write(entry.destination, counting)
    finally:
        close = getattr(reader, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    if counting.count < expected:
        try:
            destination.delete(entry.destination)
        except Exception:
            pass
        raise TransferError(
            f"source truncated: read {counting.count} of {expected} bytes from {entry.source.path}"
        )
    actual = destination.size(entry.destination)
    if actual != counting.count:
        try:
            destination.delete(entry.destination)
        except Exception:
            pass
        raise TransferError(
            f"size mismatch after copy: streamed {counting.count} bytes "
            f"but destination '{entry.destination}' holds {actual}"
        )
    return counting.count


def run_transfer(plan: TransferPlan, source: Any, destination: Any) -> TransferOutcome:
    """Copy every planned file, one at a time. Stops at the first failure;
    files copied before it stay in place (no rollback)."""
    outcome = TransferOutcome(skipped=[
        {"source": entry.source.path, "destination": entry.destination, "bytes": 0, "action": "skipped"}
        for entry in plan.skipped
    ])
    for entry in plan.to_copy:
        try:
            copied_bytes = _copy_one(entry, source, destination)
        except Exception as exc:
            outcome.failed_file = entry.source.path
            outcome.error = f"{entry.source.path}: {type(exc).__name__}: {exc}"
            return outcome
        outcome.copied.append({
            "source": entry.source.path,
            "destination": entry.destination,
            "bytes": copied_bytes,
            "action": "copied",
        })
        outcome.bytes_copied += copied_bytes
    return outcome


# -- Wiring ------------------------------------------------------------------

def build_endpoint(session: Any, spec: Any):
    """Endpoint for a ``FileSourceSpec``. ``session`` is a
    ``RemoteFileSourceSession`` (supplies cached, credential-resolved clients)."""
    if spec.kind == "local":
        return LocalEndpoint(spec.root)
    client = session.client_for(spec)
    if spec.kind == "s3":
        return S3Endpoint(spec.root, client, spec.credentials_ref)
    if spec.kind == "sftp":
        return SftpEndpoint(spec.root, client, spec.credentials_ref)
    raise ValueError(f"Unsupported file_transfer location kind: {spec.kind}")
