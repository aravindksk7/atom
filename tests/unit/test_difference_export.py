"""Tests for the DifferenceWriter json (NDJSON) export format and related helpers."""
from __future__ import annotations

import json
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def test_writer_json_format_writes_one_object_per_line(tmp_path):
    from api.services.difference_export import DifferenceWriter, DIFFERENCE_FIELDS

    path = tmp_path / "diffs.jsonl"
    with DifferenceWriter(path, "json") as writer:
        writer.write({
            "test_name": "orders", "key_values": {"id": 1}, "column_name": "amount",
            "source_value": "10", "target_value": "12", "mismatch_type": "value_diff",
            "delta": 2.0, "relative_delta": 0.2,
        })
        writer.write({
            "test_name": "orders", "key_values": {"id": 2}, "column_name": "amount",
            "source_value": "20", "target_value": "21", "mismatch_type": "value_diff",
            "delta": 1.0, "relative_delta": 0.05,
        })

    assert writer.row_count == 2
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert set(row.keys()) == set(DIFFERENCE_FIELDS)
    first = json.loads(lines[0])
    assert first["test_name"] == "orders"
    assert first["key_values"] == '{"id": 1}'
    assert first["source_value"] == "10"


def test_validate_difference_format_accepts_json():
    from api.services.difference_export import validate_difference_format

    assert validate_difference_format("json") == "json"
    assert validate_difference_format("JSON") == "json"


def test_validate_difference_format_still_rejects_unknown():
    import pytest
    from fastapi import HTTPException
    from api.services.difference_export import validate_difference_format

    with pytest.raises(HTTPException):
        validate_difference_format("xlsx")


def test_media_type_for_json():
    from api.services.difference_export import media_type_for

    assert media_type_for("json") == "application/x-ndjson"


def test_export_filename_json_uses_jsonl_suffix():
    from api.services.difference_export import export_filename

    name = export_filename("run-1", "json", "exp-1")
    assert name.endswith(".jsonl")
    assert "run-1" in name and "exp-1" in name


def test_export_filename_csv_and_parquet_unaffected():
    from api.services.difference_export import export_filename

    assert export_filename("run-1", "csv").endswith(".csv")
    assert export_filename("run-1", "parquet").endswith(".parquet")
