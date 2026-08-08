"""Tests for the server-side copy of a SAP BO download.

Everything here runs against bytes and a tmp directory — no HTTP, no BO
client. That is the whole reason the write policy lives in its own module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from api.services.bo_archive import save_bo_download

STAMP = datetime(2026, 8, 7, 20, 30, 15, tzinfo=timezone.utc)


def test_empty_directory_means_disabled(tmp_path):
    """An unset setting must be a no-op, not an error and not a write. This is
    the default on every upgraded install."""
    path, error = save_bo_download(
        b"PK\x03\x04", doc_id="124313", report_id="1", fmt="xlsx",
        directory="", now=STAMP,
    )
    assert path is None
    assert error is None
    assert list(tmp_path.iterdir()) == []


def test_writes_a_timestamped_file(tmp_path):
    path, error = save_bo_download(
        b"PK\x03\x04", doc_id="124313", report_id="1", fmt="xlsx",
        directory=str(tmp_path), now=STAMP,
    )
    assert error is None
    assert path == tmp_path / "report_124313_1_20260807T203015Z.xlsx"
    assert path.read_bytes() == b"PK\x03\x04"


def test_whole_document_export_omits_the_report_segment(tmp_path):
    """An empty report_id is SAP's whole-document export; the routes already
    treat it that way in Content-Disposition, so the name must match."""
    path, error = save_bo_download(
        b"data", doc_id="124313", report_id="", fmt="csv",
        directory=str(tmp_path), now=STAMP,
    )
    assert error is None
    assert path == tmp_path / "report_124313_20260807T203015Z.csv"


def test_unknown_format_falls_back_to_bin(tmp_path):
    path, _ = save_bo_download(
        b"data", doc_id="1", report_id="", fmt="docx",
        directory=str(tmp_path), now=STAMP,
    )
    assert path.name.endswith(".bin")


def test_never_overwrites_within_the_same_second(tmp_path):
    """Two downloads inside one second would collide. Timestamped names are
    the whole point of the feature, so the first file must survive."""
    first, _ = save_bo_download(
        b"first", doc_id="124313", report_id="1", fmt="xlsx",
        directory=str(tmp_path), now=STAMP,
    )
    second, _ = save_bo_download(
        b"second", doc_id="124313", report_id="1", fmt="xlsx",
        directory=str(tmp_path), now=STAMP,
    )
    assert first != second
    assert second.name == "report_124313_1_20260807T203015Z-1.xlsx"
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_ids_cannot_escape_the_directory(tmp_path):
    """doc_id and report_id arrive straight off the URL path. Unsanitized,
    an id like ../../x writes outside the directory the operator nominated."""
    nested = tmp_path / "out"
    nested.mkdir()
    path, error = save_bo_download(
        b"data", doc_id="../../evil", report_id="a/b", fmt="xlsx",
        directory=str(nested), now=STAMP,
    )
    assert error is None
    assert path.parent == nested
    assert path.name == "report_______evil_a_b_20260807T203015Z.xlsx"


def test_missing_directory_returns_an_error_and_does_not_raise(tmp_path):
    """A path that vanished between save-time validation and download time —
    a network share, typically. The download must not die with it."""
    path, error = save_bo_download(
        b"data", doc_id="1", report_id="", fmt="xlsx",
        directory=str(tmp_path / "does-not-exist"), now=STAMP,
    )
    assert path is None
    assert error


def test_uses_the_current_time_when_now_is_omitted(tmp_path):
    path, error = save_bo_download(
        b"data", doc_id="1", report_id="", fmt="xlsx",
        directory=str(tmp_path),
    )
    assert error is None
    assert path.exists()
