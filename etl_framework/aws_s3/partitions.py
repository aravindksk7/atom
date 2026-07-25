from __future__ import annotations

from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.models import PartitionEntry, PartitionScheme
from etl_framework.aws_s3.row_count import RowCounter


def _parse_hive_segments(key: str) -> list[tuple[str, str]]:
    """Extract ordered (col, value) pairs from Hive-style key=value path segments."""
    pairs: list[tuple[str, str]] = []
    for segment in key.split("/"):
        if "=" in segment:
            col, _, value = segment.partition("=")
            if col:
                pairs.append((col, value))
    return pairs


def discover_partitions(
    client: S3Client,
    bucket: str,
    prefix: str,
    fmt: str | None = None,
    row_counter: RowCounter | None = None,
) -> PartitionScheme:
    """Discover a Hive-style partition scheme under ``prefix``.

    Objects with no ``key=value`` segments are ignored. When ``fmt`` and
    ``row_counter`` are supplied, per-partition row counts are attached.
    """
    columns: list[str] = []
    # leaf partition (tuple of pairs) -> object keys under it
    leaves: dict[tuple[tuple[str, str], ...], list[str]] = {}

    for obj in client.list_objects(bucket, prefix):
        key = obj["Key"]
        pairs = _parse_hive_segments(key)
        if not pairs:
            continue
        for col, _ in pairs:
            if col not in columns:
                columns.append(col)
        leaves.setdefault(tuple(pairs), []).append(key)

    entries: list[PartitionEntry] = []
    for pairs, keys in leaves.items():
        row_count = None
        if fmt is not None and row_counter is not None:
            row_count = sum(row_counter.count(bucket, k, fmt).row_count for k in keys)
        entries.append(
            PartitionEntry(
                values=dict(pairs),
                object_count=len(keys),
                row_count=row_count,
            )
        )
    return PartitionScheme(columns=columns, entries=entries)
