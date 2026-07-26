from __future__ import annotations

from api.schemas import (
    S3MetadataRequest,
    S3RowCountRequest,
    S3PartitionsRequest,
    S3ValidateFormatRequest,
    ObjectMetadataOut,
    RowCountOut,
    PartitionSchemeOut,
    PartitionEntryOut,
    FormatValidationOut,
)


def test_requests_carry_config_id():
    assert S3MetadataRequest(config_id=1, bucket="b", key="k").config_id == 1
    assert S3RowCountRequest(config_id=1, bucket="b", key="k", fmt="csv").fmt == "csv"
    assert S3PartitionsRequest(config_id=1, bucket="b", prefix="t/").prefix == "t/"
    r = S3ValidateFormatRequest(config_id=1, bucket="b", key="k", fmt="parquet",
                                expected_schema={"id": "int64"})
    assert r.expected_schema == {"id": "int64"}


def test_validate_format_expected_schema_optional():
    r = S3ValidateFormatRequest(config_id=1, bucket="b", key="k", fmt="csv")
    assert r.expected_schema is None


def test_response_models():
    m = ObjectMetadataOut(bucket="b", key="k", size_bytes=5,
                          last_modified="2026-01-01T00:00:00Z", etag="e",
                          storage_class="STANDARD", content_type="text/csv")
    assert m.size_bytes == 5
    rc = RowCountOut(bucket="b", key="k", fmt="parquet", row_count=3, engine="pyarrow_footer")
    assert rc.engine == "pyarrow_footer"
    scheme = PartitionSchemeOut(columns=["dt"], entries=[
        PartitionEntryOut(values={"dt": "2026-01-01"}, object_count=2, row_count=None)])
    assert scheme.entries[0].object_count == 2
    fv = FormatValidationOut(bucket="b", key="k", fmt="csv", parsed=True, schema_ok=None)
    assert fv.parsed is True
