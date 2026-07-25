from __future__ import annotations

import pyarrow.fs as pafs
import pyarrow.orc as orc
import pyarrow.parquet as pq

from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.models import RowCountResult
from etl_framework.exceptions import S3SelectError, UnsupportedFormatError

_FOOTER_FORMATS = {"parquet", "orc"}
_SELECT_FORMATS = {"csv", "json"}


def footer_row_count(fs: "pafs.FileSystem", path: str, fmt: str) -> int:
    """Row count from a Parquet/ORC footer without a full scan."""
    if fmt not in _FOOTER_FORMATS:
        raise UnsupportedFormatError(fmt)
    with fs.open_input_file(path) as f:
        if fmt == "parquet":
            return pq.ParquetFile(f).metadata.num_rows
        return orc.ORCFile(f).nrows


def _input_serialization(fmt: str) -> dict:
    if fmt == "csv":
        return {"CSV": {"FileHeaderInfo": "USE"}}
    if fmt == "json":
        return {"JSON": {"Type": "LINES"}}
    raise UnsupportedFormatError(fmt)


def select_row_count(client: S3Client, bucket: str, key: str, fmt: str) -> int:
    """Row count via S3 Select COUNT(*) for CSV/JSON."""
    payload = client.select_object_content(
        bucket=bucket,
        key=key,
        expression="SELECT COUNT(*) FROM s3object",
        input_serialization=_input_serialization(fmt),
    )
    text = payload.strip()
    if not text:
        raise S3SelectError(bucket, key, ValueError("empty COUNT(*) result"))
    return int(text.splitlines()[-1].strip())


class RowCounter:
    """Route row counts to S3 Select (csv/json) or pyarrow footer (parquet/orc).

    ``fs`` is a pyarrow FileSystem for the footer path. In production build it
    from AWSConfig via ``pyarrow.fs.S3FileSystem``; tests inject LocalFileSystem.
    """

    def __init__(self, client: S3Client, fs: "pafs.FileSystem") -> None:
        self._client = client
        self._fs = fs

    def count(self, bucket: str, key: str, fmt: str) -> RowCountResult:
        if fmt in _SELECT_FORMATS:
            n = select_row_count(self._client, bucket, key, fmt)
            engine = "s3_select"
        elif fmt in _FOOTER_FORMATS:
            path = f"{bucket}/{key}" if bucket else key
            n = footer_row_count(self._fs, path, fmt)
            engine = "pyarrow_footer"
        else:
            raise UnsupportedFormatError(fmt)
        return RowCountResult(bucket=bucket, key=key, fmt=fmt, row_count=n, engine=engine)
