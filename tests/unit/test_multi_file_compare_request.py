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


def test_multi_file_compare_request_accepts_run_reference():
    from api.schemas import MultiFileCompareRequest

    req = MultiFileCompareRequest(run_id="run-1", job_name="regional_sales_recon")

    assert req.run_id == "run-1"
    assert req.file_mapping is None


def test_multi_file_compare_request_rejects_run_id_without_job_name():
    from api.schemas import MultiFileCompareRequest
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError, match="run_id and job_name must both be set"):
        MultiFileCompareRequest(run_id="run-1")


def test_multi_file_compare_request_rejects_both_file_mapping_and_run_reference():
    from api.schemas import MultiFileCompareRequest
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError, match="mutually exclusive"):
        MultiFileCompareRequest(
            run_id="run-1", job_name="job",
            file_mapping={"strategy": "explicit", "source": {}, "target": {}},
        )


def test_multi_file_compare_request_rejects_neither_file_mapping_nor_run_reference():
    from api.schemas import MultiFileCompareRequest
    import pytest as _pytest
    from pydantic import ValidationError

    with _pytest.raises(ValidationError, match="requires either file_mapping or run_id"):
        MultiFileCompareRequest()
