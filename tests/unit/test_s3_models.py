from __future__ import annotations

from datetime import datetime, timezone

from etl_framework.aws_s3.models import (
    ObjectMetadata,
    RowCountResult,
    PartitionEntry,
    PartitionScheme,
    FormatValidationResult,
)


def test_object_metadata_fields():
    m = ObjectMetadata(
        bucket="b", key="k", size_bytes=10,
        last_modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
        etag="abc", storage_class="STANDARD", content_type="text/csv",
    )
    assert m.size_bytes == 10
    assert m.storage_class == "STANDARD"


def test_row_count_result_records_engine():
    r = RowCountResult(bucket="b", key="k", fmt="parquet", row_count=5, engine="pyarrow_footer")
    assert r.row_count == 5
    assert r.engine == "pyarrow_footer"


def test_partition_scheme_holds_entries():
    scheme = PartitionScheme(
        columns=["dt", "region"],
        entries=[PartitionEntry(values={"dt": "2026-01-01", "region": "us"}, object_count=2, row_count=None)],
    )
    assert scheme.columns == ["dt", "region"]
    assert scheme.entries[0].object_count == 2


def test_format_validation_result():
    r = FormatValidationResult(bucket="b", key="k", fmt="csv", parsed=True, schema_ok=None)
    assert r.parsed is True
    assert r.schema_ok is None
