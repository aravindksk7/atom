# tests/unit/test_preview_file_mapping_request.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import PreviewFileMappingRequest


def test_preview_file_mapping_request_requires_file_mapping() -> None:
    with pytest.raises(ValidationError):
        PreviewFileMappingRequest()


def test_preview_file_mapping_request_defaults_credentials_to_empty_dict() -> None:
    req = PreviewFileMappingRequest(file_mapping={
        "match_on": ["region"],
        "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
        "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
    })
    assert req.file_source_credentials == {}


def test_preview_file_mapping_request_accepts_inline_credentials() -> None:
    req = PreviewFileMappingRequest(
        file_mapping={
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv", "credentials_ref": "aws_source"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        },
        file_source_credentials={"aws_source": {"aws_access_key_id": "AKIA...", "aws_secret_access_key": "s3cr3t"}},
    )
    assert req.file_source_credentials["aws_source"]["aws_access_key_id"] == "AKIA..."
