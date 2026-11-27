"""The rejected-path error tells the user how to allow the report's folder."""
from __future__ import annotations

import os

import pytest
from fastapi import HTTPException

import api.services.file_source as fs


@pytest.fixture
def only_allowed(tmp_path, monkeypatch):
    allowed = (tmp_path / "allowed").resolve()
    allowed.mkdir()
    monkeypatch.setattr(fs, "_UPLOAD_BASES", (allowed,))
    monkeypatch.setattr(fs, "_UPLOAD_BASE", allowed)
    monkeypatch.delenv("SERVER_FILE_ALLOWED_DIRS", raising=False)
    return allowed


def _reject(path: str) -> str:
    with pytest.raises(HTTPException) as exc:
        fs.resolve_allowed_path(path)
    assert exc.value.status_code == 400
    return str(exc.value.detail)


def test_message_names_report_folder_and_exact_setting(tmp_path, only_allowed):
    report = (tmp_path / "bo reports" / "sales.pdf").resolve()
    msg = _reject(str(report))
    lines = msg.split("\n")
    assert lines[0].startswith(f"Invalid file path: {report} is outside the folders")
    assert str(only_allowed) in lines[0]
    assert f"To allow the report folder {report.parent}, set SERVER_FILE_ALLOWED_DIRS" in msg
    assert "restart the API" in msg
    if os.name == "nt":
        assert f"$env:SERVER_FILE_ALLOWED_DIRS = '{report.parent}'" in lines
    else:
        assert f"export SERVER_FILE_ALLOWED_DIRS='{report.parent}'" in lines
    assert "upload the file" in msg


def test_suggestion_keeps_folders_already_configured(tmp_path, only_allowed, monkeypatch):
    existing = str((tmp_path / "existing").resolve())
    monkeypatch.setenv("SERVER_FILE_ALLOWED_DIRS", existing)
    report = (tmp_path / "new" / "r.pdf").resolve()
    msg = _reject(str(report))
    assert os.pathsep.join([str(report.parent), existing]) in msg


def test_relative_path_escaping_the_base_is_still_guided(only_allowed):
    msg = _reject(os.path.join("..", "outside", "r.pdf"))
    assert msg.startswith("Invalid file path: ")
    assert "SERVER_FILE_ALLOWED_DIRS" in msg


def test_single_quote_in_folder_is_escaped_in_the_command(tmp_path, only_allowed):
    report = (tmp_path / "O'Brien" / "r.pdf").resolve()
    msg = _reject(str(report))
    folder = str(report.parent)
    if os.name == "nt":
        assert "$env:SERVER_FILE_ALLOWED_DIRS = '" + folder.replace("'", "''") + "'" in msg
    else:
        assert "export SERVER_FILE_ALLOWED_DIRS='" + folder.replace("'", "'\\''") + "'" in msg
