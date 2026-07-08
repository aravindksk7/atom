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


def test_read_xml_repeated_records():
    raw = b"""<?xml version="1.0"?>
<dataset>
  <record><id>1</id><name>Alice</name></record>
  <record><id>2</id><name>Bob</name></record>
</dataset>
"""
    df = read_tabular(content_b64=b64(raw), file_name="data.xml")
    assert list(df.columns) == ["id", "name"]
    assert df.to_dict("records") == [
        {"id": "1", "name": "Alice"},
        {"id": "2", "name": "Bob"},
    ]


def test_read_json_absolute_path_under_upload_base(tmp_path, monkeypatch):
    import api.services.file_source as fs

    monkeypatch.setattr(fs, "_UPLOAD_BASE", tmp_path.resolve())
    path = tmp_path / "data.json"
    path.write_text(json.dumps([{"id": 1, "name": "Alice"}]), encoding="utf-8")

    df = read_tabular(path=str(path))

    assert df.to_dict("records") == [{"id": 1, "name": "Alice"}]


def test_read_xml_absolute_path_under_upload_base(tmp_path, monkeypatch):
    import api.services.file_source as fs

    monkeypatch.setattr(fs, "_UPLOAD_BASE", tmp_path.resolve())
    path = tmp_path / "data.xml"
    path.write_text(
        "<dataset><record><id>1</id><name>Alice</name></record></dataset>",
        encoding="utf-8",
    )

    df = read_tabular(path=str(path))

    assert df.to_dict("records") == [{"id": "1", "name": "Alice"}]


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
