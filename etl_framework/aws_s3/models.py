from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RowCountEngine = Literal["s3_select", "pyarrow_footer"]
FileFormat = Literal["csv", "json", "parquet", "orc"]


class ObjectMetadata(BaseModel):
    bucket: str
    key: str
    size_bytes: int
    last_modified: datetime
    etag: str
    storage_class: str
    content_type: str


class RowCountResult(BaseModel):
    bucket: str
    key: str
    fmt: FileFormat
    row_count: int
    engine: RowCountEngine


class PartitionEntry(BaseModel):
    values: dict[str, str]
    object_count: int
    row_count: int | None = None


class PartitionScheme(BaseModel):
    columns: list[str]
    entries: list[PartitionEntry] = Field(default_factory=list)


class FormatValidationResult(BaseModel):
    bucket: str
    key: str
    fmt: FileFormat
    parsed: bool
    schema_ok: bool | None = None
    missing_columns: list[str] = Field(default_factory=list)
    extra_columns: list[str] = Field(default_factory=list)
