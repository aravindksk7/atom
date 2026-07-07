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


def test_read_json_nested_records():
    data = {"payload": {"items": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]}}
    df = read_tabular(content_b64=b64(json.dumps(data).encode()), file_name="data.json")
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2


def test_read_xml_repeated_records():
    raw = b"""
    <orders>
      <order><id>1</id><name>Alice</name><amount>10.5</amount></order>
      <order><id>2</id><name>Bob</name><amount>20</amount></order>
    </orders>
    """
    df = read_tabular(content_b64=b64(raw), file_name="data.xml")
    assert list(df.columns) == ["id", "name", "amount"]
    assert len(df) == 2
    assert df.iloc[0]["name"] == "Alice"


def test_read_xml_nested_record_container():
    raw = b"""
    <response>
      <orders>
        <order><id>1</id><name>Alice</name></order>
        <order><id>2</id><name>Bob</name></order>
      </orders>
    </response>
    """
    df = read_tabular(content_b64=b64(raw), file_name="data.xml")
    assert list(df.columns) == ["id", "name"]
    assert len(df) == 2


def test_read_xml_attributes_as_columns():
    raw = b"""
    <rows>
      <row id="1"><name>Alice</name></row>
      <row id="2"><name>Bob</name></row>
    </rows>
    """
    df = read_tabular(content_b64=b64(raw), file_name="data.xml")
    assert list(df.columns) == ["id", "name"]
    assert df.iloc[1]["id"] == "2"


def test_json_and_xml_records_normalize_to_comparable_columns():
    json_data = [{"id": "1", "name": "Alice"}, {"id": "2", "name": "Bob"}]
    xml_raw = b"""
    <rows>
      <row><id>1</id><name>Alice</name></row>
      <row><id>2</id><name>Bob</name></row>
    </rows>
    """
    json_df = read_tabular(content_b64=b64(json.dumps(json_data).encode()), file_name="data.json")
    xml_df = read_tabular(content_b64=b64(xml_raw), file_name="data.xml")
    assert list(json_df.columns) == list(xml_df.columns)
    assert len(json_df) == len(xml_df) == 2


def test_xml_with_dtd_raises_400():
    raw = b'<!DOCTYPE rows [<!ENTITY x "y">]><rows><row><id>&x;</id></row></rows>'
    with pytest.raises(HTTPException) as exc_info:
        read_tabular(content_b64=b64(raw), file_name="data.xml")
    assert exc_info.value.status_code == 400


def test_bad_xml_raises_400():
    with pytest.raises(HTTPException) as exc_info:
        read_tabular(content_b64=b64(b"<rows><row></rows>"), file_name="data.xml")
    assert exc_info.value.status_code == 400


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
