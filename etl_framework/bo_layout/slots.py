"""Cross-process file-locked worker slots for PDF parsing concurrency limits."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from etl_framework.bo_layout.model import PdfReadError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_POLL_INTERVAL_S = 0.2


def _slot_dir() -> Path:
    raw = os.environ.get("ATOM_PDF_SLOT_DIR")
    if raw:
        return Path(raw)
    return Path(tempfile.gettempdir()) / "atom_pdf_slots"


def _max_workers() -> int:
    raw = os.environ.get("ATOM_PDF_MAX_WORKERS", "2")
    try:
        val = int(raw)
    except ValueError:
        return 2
    return max(1, val)


def _try_acquire(lock_path: Path) -> int | None:
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return None


def _release(fd: int) -> None:
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


@contextmanager
def worker_slot(deadline: float) -> Iterator[None]:
    """Acquire one cross-process worker slot before `deadline` or raise `busy`."""
    slot_dir = _slot_dir()
    slot_dir.mkdir(parents=True, exist_ok=True)
    max_workers = _max_workers()

    acquired_fd: int | None = None
    while True:
        for idx in range(max_workers):
            fd = _try_acquire(slot_dir / f"slot-{idx}.lock")
            if fd is not None:
                acquired_fd = fd
                break
        if acquired_fd is not None:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PdfReadError("busy", "PDF reader busy")
        time.sleep(min(_POLL_INTERVAL_S, remaining))

    try:
        yield
    finally:
        _release(acquired_fd)
