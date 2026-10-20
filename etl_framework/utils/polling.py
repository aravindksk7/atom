"""Status polling that tolerates transient network errors.

A remote job (SAP DS, SAP BO) keeps running whether or not one of our status
calls times out, so a single slow ``Get_BatchJob_Status`` response must not
fail the step: that left jobs ERROR in the app while they PASSED in the
source system. Only network-level errors are tolerated -- an API error or a
SOAP fault is a real answer and propagates immediately.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import requests

from etl_framework.runner.state import TestStatus

logger = logging.getLogger(__name__)

TRANSIENT_POLL_ERRORS = (requests.Timeout, requests.ConnectionError)


def poll_until_terminal(
    fetch_status: Callable[[], TestStatus],
    *,
    timeout_s: float,
    poll_interval_s: float,
    max_poll_errors: int,
    describe: str,
) -> TestStatus:
    """Call ``fetch_status`` until it returns a non-RUNNING status.

    Up to ``max_poll_errors - 1`` consecutive transient errors are logged and
    polled through; the next one is re-raised. A successful poll resets the
    count. Raises TimeoutError once ``timeout_s`` has elapsed.
    """
    max_poll_errors = max(1, int(max_poll_errors))
    deadline = time.monotonic() + timeout_s
    consecutive_errors = 0
    while True:
        try:
            status = fetch_status()
        except TRANSIENT_POLL_ERRORS as exc:
            consecutive_errors += 1
            if consecutive_errors >= max_poll_errors:
                raise
            logger.warning(
                "Status poll for %s failed (%s/%s consecutive): %s",
                describe, consecutive_errors, max_poll_errors, exc,
            )
        else:
            consecutive_errors = 0
            if status != TestStatus.RUNNING:
                return status
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{describe} did not complete within {timeout_s}s")
        time.sleep(poll_interval_s)
