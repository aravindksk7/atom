from __future__ import annotations

from unittest.mock import MagicMock

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession


def test_client_passes_endpoint_and_ssl_from_config():
    fake_session = MagicMock()
    cfg = AWSConfig(region="us-east-1", endpoint_url="http://localhost:5000", verify_ssl=False)
    sess = AWSSession(cfg, _session=fake_session)

    sess.client("s3")

    fake_session.client.assert_called_once_with(
        "s3", endpoint_url="http://localhost:5000", verify=False
    )


def test_client_omits_endpoint_when_unset():
    fake_session = MagicMock()
    cfg = AWSConfig(region="us-east-1")
    sess = AWSSession(cfg, _session=fake_session)

    sess.client("s3")

    fake_session.client.assert_called_once_with("s3", verify=True)


def test_client_is_cached_per_service():
    fake_session = MagicMock()
    sess = AWSSession(AWSConfig(), _session=fake_session)

    first = sess.client("s3")
    second = sess.client("s3")

    assert first is second
    assert fake_session.client.call_count == 1


def test_injected_session_is_used_directly():
    fake_session = MagicMock()
    sess = AWSSession(AWSConfig(), _session=fake_session)
    assert sess.session is fake_session
