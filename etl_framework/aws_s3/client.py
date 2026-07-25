from __future__ import annotations

from typing import Iterator

from botocore.exceptions import ClientError

from etl_framework.aws.session import AWSSession
from etl_framework.exceptions import S3ObjectNotFoundError, S3SelectError

_NOT_FOUND_CODES = {"NoSuchKey", "404", "NotFound"}


class S3Client:
    """Thin S3 wrapper: paginated listing, head, get, and S3 Select."""

    def __init__(self, session: AWSSession) -> None:
        self._s3 = session.client("s3")

    def list_objects(self, bucket: str, prefix: str) -> Iterator[dict]:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj

    def head_object(self, bucket: str, key: str) -> dict:
        try:
            return self._s3.head_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise S3ObjectNotFoundError(bucket, key) from exc
            raise

    def get_object(self, bucket: str, key: str) -> bytes:
        try:
            resp = self._s3.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
                raise S3ObjectNotFoundError(bucket, key) from exc
            raise
        return resp["Body"].read()

    def select_object_content(
        self, bucket: str, key: str, expression: str, input_serialization: dict
    ) -> str:
        """Run an S3 Select query, returning the concatenated record payload."""
        try:
            resp = self._s3.select_object_content(
                Bucket=bucket,
                Key=key,
                Expression=expression,
                ExpressionType="SQL",
                InputSerialization=input_serialization,
                OutputSerialization={"CSV": {}},
            )
            payload = []
            for event in resp["Payload"]:
                if "Records" in event:
                    payload.append(event["Records"]["Payload"].decode("utf-8"))
            return "".join(payload)
        except ClientError as exc:
            raise S3SelectError(bucket, key, exc) from exc
