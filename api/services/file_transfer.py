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
