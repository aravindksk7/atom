"""Unit tests for cross-process worker slot locking."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from etl_framework.bo_layout.model import PdfReadError
from etl_framework.bo_layout.slots import worker_slot


def test_slot_held_by_another_process_makes_caller_busy(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOM_PDF_SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("ATOM_PDF_MAX_WORKERS", "1")
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time\nfrom etl_framework.bo_layout.slots import worker_slot\n"
            "with worker_slot(time.monotonic()+5):\n print('held', flush=True); time.sleep(3)",
        ],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ},
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(PdfReadError) as e:
            with worker_slot(time.monotonic() + 0.5):
                pass
        assert (e.value.code, e.value.message) == ("busy", "PDF reader busy")
    finally:
        holder.wait(timeout=10)
    with worker_slot(time.monotonic() + 1):
        pass  # released when the holder exits


def test_two_slots_allow_two_holders(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOM_PDF_SLOT_DIR", str(tmp_path / "slots"))
    monkeypatch.setenv("ATOM_PDF_MAX_WORKERS", "2")
    with worker_slot(time.monotonic() + 1):
        with worker_slot(time.monotonic() + 1):
            pass


def test_slot_dir_is_created(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOM_PDF_SLOT_DIR", str(tmp_path / "a" / "b"))
    with worker_slot(time.monotonic() + 1):
        pass
    assert (tmp_path / "a" / "b").is_dir()
