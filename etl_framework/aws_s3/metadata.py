from __future__ import annotations

from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.models import ObjectMetadata


def read_object_metadata(client: S3Client, bucket: str, key: str) -> ObjectMetadata:
    head = client.head_object(bucket, key)
    return ObjectMetadata(
        bucket=bucket,
        key=key,
        size_bytes=head["ContentLength"],
        last_modified=head["LastModified"],
        etag=head.get("ETag", "").strip('"'),
        # S3 omits StorageClass on the head of a STANDARD object.
        storage_class=head.get("StorageClass", "STANDARD"),
        content_type=head.get("ContentType", ""),
    )
