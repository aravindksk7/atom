from __future__ import annotations

import pyarrow.parquet as pq
import pyarrow.orc as orc

from tests.helpers.s3_fixtures import (
    write_csv,
    write_json,
    write_parquet,
    write_orc,
    SAMPLE_ROWS,
)


def test_write_csv(tmp_path):
    p = tmp_path / "d.csv"
    write_csv(p)
    text = p.read_text()
    assert "id,name" in text.splitlines()[0]
    assert len(text.strip().splitlines()) == len(SAMPLE_ROWS) + 1


def test_write_json(tmp_path):
    p = tmp_path / "d.json"
    write_json(p)
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    assert len(lines) == len(SAMPLE_ROWS)


def test_write_parquet(tmp_path):
    p = tmp_path / "d.parquet"
    write_parquet(p)
    assert pq.ParquetFile(str(p)).metadata.num_rows == len(SAMPLE_ROWS)


def test_write_orc(tmp_path):
    p = tmp_path / "d.orc"
    write_orc(p)
    assert orc.ORCFile(str(p)).nrows == len(SAMPLE_ROWS)
