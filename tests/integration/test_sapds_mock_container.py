from __future__ import annotations

import os
import time

import pytest
import requests
import urllib3

from etl_framework.config.models import EnvironmentConfig
from etl_framework.runner.state import TestStatus
from etl_framework.sap_ds.client import DSRestClient


pytestmark = [
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_SAPDS_TESTS") != "1",
        reason="set RUN_LIVE_SAPDS_TESTS=1 and start docker-compose.integration.yml sapds",
    ),
    pytest.mark.filterwarnings("ignore:Unverified HTTPS request"),
]


HOST = os.getenv("LIVE_SAPDS_HOST", "127.0.0.1")
PORT = int(os.getenv("LIVE_SAPDS_PORT", "18444"))
USER = os.getenv("LIVE_SAPDS_USER", "administrator")
PASSWORD = os.getenv("LIVE_SAPDS_PASSWORD", "Password1")
BASE_URL = f"https://{HOST}:{PORT}"

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _wait_for_sapds() -> None:
    last_error: Exception | None = None
    for _ in range(30):
        try:
            response = requests.get(f"{BASE_URL}/health", timeout=2, verify=False)
            response.raise_for_status()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise AssertionError(f"SAP DS mock did not become ready: {last_error}")


def _env() -> EnvironmentConfig:
    return EnvironmentConfig(
        name="sapds-mock",
        db_host="unused",
        db_password="unused",
        ds_url=BASE_URL,
        ds_user=USER,
        ds_password=PASSWORD,
        ds_repository="DS_REPO",
        ds_timeout=5,
        ds_verify_ssl=False,
    )


def test_trigger_and_wait_for_completion_success():
    _wait_for_sapds()
    client = DSRestClient(_env())
    client.login()

    run_id = client.trigger_job("DS_NIGHTLY_LOAD")
    status = client.wait_for_completion(run_id, timeout_s=5, poll_interval_s=0.1)
    assert status == TestStatus.PASSED


def test_trigger_and_wait_for_completion_failure():
    _wait_for_sapds()
    client = DSRestClient(_env())
    client.login()

    run_id = client.trigger_job("DS_BAD_LOAD")
    status = client.wait_for_completion(run_id, timeout_s=5, poll_interval_s=0.1)
    assert status == TestStatus.FAILED


def test_trigger_unknown_job_raises_ds_api_error():
    from etl_framework.exceptions import DSAPIError

    _wait_for_sapds()
    client = DSRestClient(_env())
    client.login()

    with pytest.raises(DSAPIError) as exc_info:
        client.trigger_job("does-not-exist")
    assert exc_info.value.http_status == 404


def test_login_rejects_wrong_credentials():
    from etl_framework.exceptions import DSAPIError

    _wait_for_sapds()
    cfg = _env().model_copy(update={"ds_password": "wrong"})
    client = DSRestClient(cfg)

    with pytest.raises(DSAPIError) as exc_info:
        client.login()
    assert exc_info.value.http_status == 401
