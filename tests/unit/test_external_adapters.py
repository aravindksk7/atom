"""Smoke tests confirming external adapter modules are importable and properly structured."""
from unittest.mock import MagicMock

from etl_framework.config.models import EnvironmentConfig


def _env():
    return EnvironmentConfig(
        name="test", db_host="localhost", db_password="secret",
        bo_url="http://bo", bo_user="admin", bo_password="pass",
        automic_url="http://automic", automic_user="u", automic_password="p",
    )


def test_bo_rest_client_is_importable_and_instantiable():
    from etl_framework.sap_bo.client import BORestClient
    client = BORestClient(_env())
    assert hasattr(client, "authenticate")
    assert hasattr(client, "list_documents")
    assert hasattr(client, "download_report")


def test_automic_client_is_importable_and_instantiable():
    from etl_framework.automic.client import AutomicClient
    client = AutomicClient(_env())
    assert hasattr(client, "get_status_by_job_name")
    assert hasattr(client, "get_status_by_run_id")


def test_sap_bo_report_runner_is_importable():
    from etl_framework.sap_bo.reports import SAPBOReportRunner
    assert SAPBOReportRunner is not None


def test_automic_job_runner_is_importable():
    from etl_framework.automic.jobs import AutomicJobRunner
    assert AutomicJobRunner is not None
