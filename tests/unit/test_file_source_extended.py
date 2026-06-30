from __future__ import annotations
import base64, io, json
import pandas as pd
import pytest
from fastapi import HTTPException
from api.services.file_source import read_tabular


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_read_json_records():
    data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    df = read_tabular(content_b64=b64(json.dumps(data).encode()), file_name="data.json")
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2


def test_read_tsv():
    raw = b"id\tname\n1\tAlice\n2\tBob\n"
    df = read_tabular(content_b64=b64(raw), file_name="data.tsv")
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2


def test_read_txt_treated_as_tsv():
    raw = b"id\tname\n1\tAlice\n"
    df = read_tabular(content_b64=b64(raw), file_name="data.txt")
    assert "id" in df.columns
    assert len(df) == 1


def test_unsupported_extension_raises_400():
    raw = b"fake binary"
    with pytest.raises(HTTPException) as exc_info:
        read_tabular(content_b64=b64(raw), file_name="data.parquet")
    assert exc_info.value.status_code == 400
    assert ".parquet" in exc_info.value.detail


def test_existing_csv_still_works():
    raw = b"id,val\n1,10\n2,20\n"
    df = read_tabular(content_b64=b64(raw), file_name="data.csv")
    assert list(df.columns) == ["id", "val"]
    assert len(df) == 2
