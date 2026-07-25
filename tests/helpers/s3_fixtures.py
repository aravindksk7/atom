"""Tiny CSV/JSON/Parquet/ORC file writers for S3 tests.

SAMPLE_ROWS is the canonical dataset; every writer emits the same 3 rows so
tests can assert a stable row count of 3 across all formats.
"""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.orc as orc
import pyarrow.parquet as pq

SAMPLE_ROWS = [
    {"id": 1, "name": "alice"},
    {"id": 2, "name": "bob"},
    {"id": 3, "name": "carol"},
]


def _table() -> pa.Table:
    return pa.Table.from_pylist(SAMPLE_ROWS)


def write_csv(path: Path) -> Path:
    lines = ["id,name"]
    lines += [f"{r['id']},{r['name']}" for r in SAMPLE_ROWS]
    Path(path).write_text("\n".join(lines) + "\n")
    return Path(path)


def write_json(path: Path) -> Path:
    # newline-delimited JSON (one object per line) — the S3 Select JSON default.
    body = "\n".join(json.dumps(r) for r in SAMPLE_ROWS)
    Path(path).write_text(body + "\n")
    return Path(path)


def write_parquet(path: Path) -> Path:
    pq.write_table(_table(), str(path))
    return Path(path)


def write_orc(path: Path) -> Path:
    orc.write_table(_table(), str(path))
    return Path(path)
