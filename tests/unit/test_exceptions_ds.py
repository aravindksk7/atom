from __future__ import annotations

from etl_framework.exceptions import DSAPIError, ETLFrameworkError


def test_ds_api_error_message_and_attrs():
    exc = DSAPIError(job_name="nightly_load", http_status=404, response_body="not found")
    assert exc.job_name == "nightly_load"
    assert exc.http_status == 404
    assert exc.response_body == "not found"
    assert "404" in str(exc)
    assert "nightly_load" in str(exc)
    assert isinstance(exc, ETLFrameworkError)
