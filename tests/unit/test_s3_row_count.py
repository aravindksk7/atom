from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow.fs as pafs
import pytest

from etl_framework.aws_s3.row_count import (
    footer_row_count,
    select_row_count,
    RowCounter,
)
from etl_framework.exceptions import UnsupportedFormatError
from tests.helpers.s3_fixtures import write_parquet, write_orc, SAMPLE_ROWS


def test_footer_counts_parquet(tmp_path):
    p = write_parquet(tmp_path / "d.parquet")
    count = footer_row_count(pafs.LocalFileSystem(), str(p), "parquet")
    assert count == len(SAMPLE_ROWS)


def test_footer_counts_orc(tmp_path):
    p = write_orc(tmp_path / "d.orc")
    count = footer_row_count(pafs.LocalFileSystem(), str(p), "orc")
    assert count == len(SAMPLE_ROWS)


def test_footer_rejects_non_footer_format(tmp_path):
    with pytest.raises(UnsupportedFormatError):
        footer_row_count(pafs.LocalFileSystem(), "x.csv", "csv")


def test_select_row_count_parses_count(monkeypatch):
    fake_client = MagicMock()
    fake_client.select_object_content.return_value = "3\n"
    n = select_row_count(fake_client, "b", "k.csv", "csv")
    assert n == 3
    args, kwargs = fake_client.select_object_content.call_args
    assert "CSV" in kwargs["input_serialization"]


def test_select_row_count_json_serialization(monkeypatch):
    fake_client = MagicMock()
    fake_client.select_object_content.return_value = "3\n"
    select_row_count(fake_client, "b", "k.json", "json")
    _, kwargs = fake_client.select_object_content.call_args
    assert "JSON" in kwargs["input_serialization"]


def test_rowcounter_routes_csv_to_select():
    fake_client = MagicMock()
    fake_client.select_object_content.return_value = "3\n"
    rc = RowCounter(fake_client, fs=MagicMock())
    result = rc.count("b", "k.csv", "csv")
    assert result.row_count == 3
    assert result.engine == "s3_select"


def test_rowcounter_routes_parquet_to_footer(tmp_path):
    p = write_parquet(tmp_path / "d.parquet")
    fake_client = MagicMock()
    rc = RowCounter(fake_client, fs=pafs.LocalFileSystem())
    result = rc.count("", str(p), "parquet")
    assert result.row_count == len(SAMPLE_ROWS)
    assert result.engine == "pyarrow_footer"
    fake_client.select_object_content.assert_not_called()
