# tests/unit/test_multi_file_compare_request.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schemas import MultiFileCompareRequest


def test_multi_file_compare_request_requires_file_mapping() -> None:
    with pytest.raises(ValidationError):
        MultiFileCompareRequest()


def test_multi_file_compare_request_accepts_minimal_config() -> None:
    req = MultiFileCompareRequest(file_mapping={
        "match_on": ["region"],
        "source": {"kind": "local", "root": "/spool", "pattern": "sales_{region}.csv"},
        "target": {"kind": "local", "root": "/baseline", "pattern": "fin_{region}.csv"},
    })
    assert req.label_a == "Source A"
    assert req.label_b == "Source B"
    assert req.key_columns is None
    assert req.exclude_columns == []
    assert req.file_mapping["match_on"] == ["region"]
    assert req.advanced.float_tolerance == 1e-9
