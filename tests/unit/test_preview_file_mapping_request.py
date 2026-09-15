# tests/unit/test_preview_file_mapping_request.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import PreviewFileMappingRequest


def test_preview_file_mapping_request_requires_file_mapping() -> None:
    with pytest.raises(ValidationError):
        PreviewFileMappingRequest()


def test_preview_file_mapping_request_has_no_inline_credentials_field() -> None:
    """Credential resolution moved to persisted FileServerProfile records (see
    api.services.multi_file_remote.resolve_file_server_profile) -- there is no
    inline-credentials path on this request any more. This supersedes the old
    test_preview_file_mapping_request_defaults_credentials_to_empty_dict /
    test_preview_file_mapping_request_accepts_inline_credentials tests, which
    asserted on a `file_source_credentials` field that no longer exists.
    A stray `file_source_credentials` key in the request body is silently
    ignored (the model doesn't forbid extra fields), not rejected."""
    req = PreviewFileMappingRequest(
        file_mapping={
            "match_on": ["region"],
            "source": {"kind": "s3", "root": "s3://bucket/prefix", "pattern": "sales_{region}.csv", "credentials_ref": "aws_source"},
            "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
        },
        file_source_credentials={"aws_source": {"aws_access_key_id": "AKIA...", "aws_secret_access_key": "s3cr3t"}},
    )
    assert not hasattr(req, "file_source_credentials")
