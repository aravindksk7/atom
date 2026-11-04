"""The SAP DS mock's optional demo behaviour: a configurable job list, and ETL
output published under the business date the job actually received."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[2] / "docker" / "sapds-mock" / "server.py"


@pytest.fixture
def mock_server():
    spec = importlib.util.spec_from_file_location("sapds_mock_server_under_test", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_defaults_are_unchanged_without_a_jobs_file(mock_server):
    assert mock_server.load_schedulable_jobs(None) == {
        "DS_NIGHTLY_LOAD": "succeeded",
        "DS_BAD_LOAD": "Error",
    }


def test_jobs_file_is_merged_over_the_defaults(mock_server, tmp_path):
    jobs_file = tmp_path / "jobs.json"
    jobs_file.write_text(json.dumps({"SET1_ETL_001": "succeeded", "SET3_ETL_075": "Error"}))

    jobs = mock_server.load_schedulable_jobs(str(jobs_file))

    assert jobs["SET1_ETL_001"] == "succeeded"
    assert jobs["SET3_ETL_075"] == "Error"
    assert jobs["DS_NIGHTLY_LOAD"] == "succeeded"


def test_publish_copies_template_files_to_the_received_date(mock_server, tmp_path):
    (tmp_path / "templates" / "SET1_ETL_001").mkdir(parents=True)
    (tmp_path / "templates" / "SET1_ETL_001" / "gl_001.csv").write_text("a\n1\n")

    published = mock_server.publish_job_outputs(
        "SET1_ETL_001",
        {"G_BUSINESS_DATE": "2026-06-03"},
        template_dir=str(tmp_path / "templates"),
        output_dir=str(tmp_path / "out"),
        date_var="G_BUSINESS_DATE",
    )

    assert published == [str(tmp_path / "out" / "2026-06-03" / "gl_001.csv")]
    assert (tmp_path / "out" / "2026-06-03" / "gl_001.csv").read_text() == "a\n1\n"


def test_publish_does_nothing_when_not_configured(mock_server, tmp_path):
    assert mock_server.publish_job_outputs(
        "SET1_ETL_001", {"G_BUSINESS_DATE": "2026-06-03"},
        template_dir="", output_dir="", date_var="G_BUSINESS_DATE",
    ) == []


def test_publish_does_nothing_without_the_date_variable(mock_server, tmp_path):
    (tmp_path / "templates" / "SET1_ETL_001").mkdir(parents=True)
    (tmp_path / "templates" / "SET1_ETL_001" / "gl_001.csv").write_text("a\n1\n")

    assert mock_server.publish_job_outputs(
        "SET1_ETL_001", {},
        template_dir=str(tmp_path / "templates"),
        output_dir=str(tmp_path / "out"),
        date_var="G_BUSINESS_DATE",
    ) == []
    assert not (tmp_path / "out").exists()


def test_publish_rejects_a_date_that_escapes_the_output_folder(mock_server, tmp_path):
    (tmp_path / "templates" / "SET1_ETL_001").mkdir(parents=True)
    (tmp_path / "templates" / "SET1_ETL_001" / "gl_001.csv").write_text("a\n1\n")

    assert mock_server.publish_job_outputs(
        "SET1_ETL_001", {"G_BUSINESS_DATE": "../../escaped"},
        template_dir=str(tmp_path / "templates"),
        output_dir=str(tmp_path / "out"),
        date_var="G_BUSINESS_DATE",
    ) == []
    assert not (tmp_path / "escaped").exists()
