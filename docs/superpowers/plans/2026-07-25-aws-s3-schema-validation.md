# AWS S3 Storage & Schema Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add native AWS S3 support to the ETL test framework — object metadata, row counts, Hive-style partition discovery, and CSV/Parquet/JSON/ORC format validation — on a reusable AWS session/config/exception foundation.

**Architecture:** A shared `etl_framework/aws/` package holds `AWSConfig` (pydantic) and `AWSSession` (boto3 wrapper with `endpoint_url` + dependency injection). An `etl_framework/aws_s3/` package builds on it: `S3Client` wraps the boto3 s3 client; feature modules (`metadata`, `row_count`, `partitions`, `formats`) return pydantic result models. Row counts use S3 Select for CSV/JSON and pyarrow footer reads for Parquet/ORC. All external clients are injectable so unit tests run offline via moto / botocore `Stubber` / pyarrow `LocalFileSystem`.

**Tech Stack:** Python 3.11+, boto3, pyarrow (`pyarrow.fs`, `pyarrow.parquet`, `pyarrow.orc`), pandas, pydantic v2, pytest, moto>=5.

**Spec:** `docs/superpowers/specs/2026-07-25-aws-s3-schema-validation-design.md`

---

## File Structure

**Create:**
- `etl_framework/aws/__init__.py` — package marker
- `etl_framework/aws/config.py` — `AWSConfig` pydantic model
- `etl_framework/aws/session.py` — `AWSSession` boto3 wrapper
- `etl_framework/aws_s3/__init__.py` — package marker
- `etl_framework/aws_s3/models.py` — result dataclasses/models
- `etl_framework/aws_s3/client.py` — `S3Client`
- `etl_framework/aws_s3/metadata.py` — `read_object_metadata`
- `etl_framework/aws_s3/row_count.py` — `footer_row_count`, `select_row_count`, `RowCounter`
- `etl_framework/aws_s3/partitions.py` — `discover_partitions`
- `etl_framework/aws_s3/formats.py` — `validate_format`
- `tests/helpers/s3_fixtures.py` — tiny CSV/Parquet/JSON/ORC file writers
- `tests/unit/test_aws_config.py`
- `tests/unit/test_aws_session.py`
- `tests/unit/test_s3_client.py`
- `tests/unit/test_s3_metadata.py`
- `tests/unit/test_s3_row_count.py`
- `tests/unit/test_s3_partitions.py`
- `tests/unit/test_s3_formats.py`
- `tests/integration/test_s3_live.py` — gated by `ATOM_AWS_LIVE=1`

**Modify:**
- `etl_framework/exceptions.py` — append AWS exception family
- `etl_framework/config/models.py` — extend `SECRET_FIELDS`
- `requirements.txt` — add `moto>=5.0` to dev block
- `pyproject.toml` — add `moto>=5.0` to `[dev]` extra

---

## Task 1: Dev dependency + AWSConfig model

**Files:**
- Modify: `requirements.txt`, `pyproject.toml`
- Create: `etl_framework/aws/__init__.py`, `etl_framework/aws/config.py`
- Modify: `etl_framework/config/models.py`
- Test: `tests/unit/test_aws_config.py`

- [ ] **Step 1: Add moto to dev deps**

In `requirements.txt`, under the `# Dev / test` block, add:

```
moto>=5.0
```

In `pyproject.toml`, in the `dev = [ ... ]` list, add:

```
    "moto>=5.0",
```

- [ ] **Step 2: Install it**

Run: `pip install "moto>=5.0"`
Expected: installs successfully.

- [ ] **Step 3: Write the failing test**

Create `tests/unit/test_aws_config.py`:

```python
from __future__ import annotations

import pytest

from etl_framework.aws.config import AWSConfig


def test_defaults_are_empty_and_ssl_on():
    cfg = AWSConfig()
    assert cfg.region == ""
    assert cfg.profile == ""
    assert cfg.access_key_id == ""
    assert cfg.secret_access_key == ""
    assert cfg.session_token == ""
    assert cfg.endpoint_url == ""
    assert cfg.verify_ssl is True


def test_strips_whitespace():
    cfg = AWSConfig(region="  us-east-1  ")
    assert cfg.region == "us-east-1"


def test_endpoint_url_requires_scheme():
    with pytest.raises(ValueError, match="scheme"):
        AWSConfig(endpoint_url="localhost:5000")


def test_endpoint_url_with_scheme_ok():
    cfg = AWSConfig(endpoint_url="http://localhost:5000")
    assert cfg.endpoint_url == "http://localhost:5000"


def test_aws_secret_fields_registered_for_masking():
    from etl_framework.config.models import SECRET_FIELDS
    assert "secret_access_key" in SECRET_FIELDS
    assert "session_token" in SECRET_FIELDS
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/unit/test_aws_config.py -v`
Expected: FAIL — `ModuleNotFoundError: etl_framework.aws.config`.

- [ ] **Step 5: Create the package marker**

Create `etl_framework/aws/__init__.py` (empty file).

- [ ] **Step 6: Implement AWSConfig**

Create `etl_framework/aws/config.py`:

```python
from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator


class AWSConfig(BaseModel):
    """Connection/credential config for AWS service clients.

    Mirrors EnvironmentConfig style. Leave keys empty to fall back to the
    default boto3 credential chain (env vars, shared config, instance role).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    region: str = ""
    profile: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""
    endpoint_url: str = ""
    verify_ssl: bool = True

    @field_validator("endpoint_url")
    @classmethod
    def validate_endpoint_url(cls, v: str) -> str:
        if v and not urlparse(v).scheme:
            raise ValueError("endpoint_url must include a scheme (http:// or https://)")
        return v
```

- [ ] **Step 7: Register AWS secret fields for masking**

In `etl_framework/config/models.py`, extend the `SECRET_FIELDS` frozenset to include the AWS secrets:

```python
SECRET_FIELDS = frozenset({
    "db_password", "automic_password", "bo_password", "ds_password",
    "api_key", "bearer_token", "basic_password", "sap_bo_logon_token",
    "secret_access_key", "session_token",
})
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/unit/test_aws_config.py -v`
Expected: PASS (5 tests).

- [ ] **Step 9: Commit**

```bash
git add requirements.txt pyproject.toml etl_framework/aws/__init__.py etl_framework/aws/config.py etl_framework/config/models.py tests/unit/test_aws_config.py
git commit -m "feat(aws): add AWSConfig model and register AWS secret fields"
```

---

## Task 2: AWS exception family

**Files:**
- Modify: `etl_framework/exceptions.py`
- Test: `tests/unit/test_aws_exceptions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_aws_exceptions.py`:

```python
from __future__ import annotations

from etl_framework.exceptions import (
    ETLFrameworkError,
    AWSError,
    S3ObjectNotFoundError,
    S3SelectError,
    UnsupportedFormatError,
    FileFormatValidationError,
)


def test_aws_error_is_framework_error():
    assert issubclass(AWSError, ETLFrameworkError)


def test_subtypes_inherit_aws_error():
    for exc in (
        S3ObjectNotFoundError,
        S3SelectError,
        UnsupportedFormatError,
        FileFormatValidationError,
    ):
        assert issubclass(exc, AWSError)


def test_object_not_found_carries_bucket_and_key():
    err = S3ObjectNotFoundError(bucket="b", key="k/x.csv")
    assert err.bucket == "b"
    assert err.key == "k/x.csv"
    assert "b" in str(err) and "k/x.csv" in str(err)


def test_unsupported_format_carries_fmt():
    err = UnsupportedFormatError(fmt="avro")
    assert err.fmt == "avro"
    assert "avro" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_aws_exceptions.py -v`
Expected: FAIL — `ImportError: cannot import name 'AWSError'`.

- [ ] **Step 3: Append the exceptions**

Append to `etl_framework/exceptions.py`:

```python
class AWSError(ETLFrameworkError):
    """Base for all AWS-related framework errors."""


class S3ObjectNotFoundError(AWSError):
    def __init__(self, bucket: str, key: str) -> None:
        self.bucket = bucket
        self.key = key
        super().__init__(f"S3 object not found: s3://{bucket}/{key}")


class S3SelectError(AWSError):
    def __init__(self, bucket: str, key: str, original_error: Exception) -> None:
        self.bucket = bucket
        self.key = key
        self.original_error = original_error
        super().__init__(
            f"S3 Select failed for s3://{bucket}/{key}: {original_error}"
        )


class UnsupportedFormatError(AWSError):
    def __init__(self, fmt: str) -> None:
        self.fmt = fmt
        super().__init__(f"Unsupported file format: {fmt!r}")


class FileFormatValidationError(AWSError):
    def __init__(self, bucket: str, key: str, fmt: str, original_error: Exception) -> None:
        self.bucket = bucket
        self.key = key
        self.fmt = fmt
        self.original_error = original_error
        super().__init__(
            f"File s3://{bucket}/{key} is not valid {fmt}: {original_error}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_aws_exceptions.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/exceptions.py tests/unit/test_aws_exceptions.py
git commit -m "feat(aws): add AWS exception family"
```

---

## Task 3: AWSSession

**Files:**
- Create: `etl_framework/aws/session.py`
- Test: `tests/unit/test_aws_session.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_aws_session.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession


def test_client_passes_endpoint_and_ssl_from_config():
    fake_session = MagicMock()
    cfg = AWSConfig(region="us-east-1", endpoint_url="http://localhost:5000", verify_ssl=False)
    sess = AWSSession(cfg, _session=fake_session)

    sess.client("s3")

    fake_session.client.assert_called_once_with(
        "s3", endpoint_url="http://localhost:5000", verify=False
    )


def test_client_omits_endpoint_when_unset():
    fake_session = MagicMock()
    cfg = AWSConfig(region="us-east-1")
    sess = AWSSession(cfg, _session=fake_session)

    sess.client("s3")

    fake_session.client.assert_called_once_with("s3", verify=True)


def test_client_is_cached_per_service():
    fake_session = MagicMock()
    sess = AWSSession(AWSConfig(), _session=fake_session)

    first = sess.client("s3")
    second = sess.client("s3")

    assert first is second
    assert fake_session.client.call_count == 1


def test_injected_session_is_used_directly():
    fake_session = MagicMock()
    sess = AWSSession(AWSConfig(), _session=fake_session)
    assert sess.session is fake_session
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_aws_session.py -v`
Expected: FAIL — `ModuleNotFoundError: etl_framework.aws.session`.

- [ ] **Step 3: Implement AWSSession**

Create `etl_framework/aws/session.py`:

```python
from __future__ import annotations

import boto3

from etl_framework.aws.config import AWSConfig


class AWSSession:
    """Thin wrapper over boto3.Session with endpoint_url + client caching.

    Pass ``_session`` to inject a session in tests (mirrors DBEngine(_engine=...)).
    """

    def __init__(self, cfg: AWSConfig, _session: "boto3.Session | None" = None) -> None:
        self._cfg = cfg
        if _session is not None:
            self.session = _session
        else:
            kwargs: dict = {}
            if cfg.profile:
                kwargs["profile_name"] = cfg.profile
            if cfg.region:
                kwargs["region_name"] = cfg.region
            if cfg.access_key_id:
                kwargs["aws_access_key_id"] = cfg.access_key_id
                kwargs["aws_secret_access_key"] = cfg.secret_access_key
                if cfg.session_token:
                    kwargs["aws_session_token"] = cfg.session_token
            self.session = boto3.Session(**kwargs)
        self._clients: dict[str, object] = {}

    def client(self, service: str):
        if service not in self._clients:
            kwargs: dict = {"verify": self._cfg.verify_ssl}
            if self._cfg.endpoint_url:
                kwargs["endpoint_url"] = self._cfg.endpoint_url
            self._clients[service] = self.session.client(service, **kwargs)
        return self._clients[service]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_aws_session.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/aws/session.py tests/unit/test_aws_session.py
git commit -m "feat(aws): add AWSSession boto3 wrapper with endpoint + client caching"
```

---

## Task 4: Result models

**Files:**
- Create: `etl_framework/aws_s3/__init__.py`, `etl_framework/aws_s3/models.py`
- Test: `tests/unit/test_s3_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_s3_models.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_s3_models.py -v`
Expected: FAIL — `ModuleNotFoundError: etl_framework.aws_s3.models`.

- [ ] **Step 3: Create package marker + models**

Create `etl_framework/aws_s3/__init__.py` (empty file).

Create `etl_framework/aws_s3/models.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_s3_models.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/aws_s3/__init__.py etl_framework/aws_s3/models.py tests/unit/test_s3_models.py
git commit -m "feat(aws-s3): add S3 result models"
```

---

## Task 5: Test fixture helpers

**Files:**
- Create: `tests/helpers/s3_fixtures.py`
- Test: `tests/unit/test_s3_fixtures.py` (self-check the helpers, then reused by later tasks)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_s3_fixtures.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_s3_fixtures.py -v`
Expected: FAIL — `ModuleNotFoundError: tests.helpers.s3_fixtures`.

- [ ] **Step 3: Implement the fixture helpers**

Create `tests/helpers/s3_fixtures.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_s3_fixtures.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/helpers/s3_fixtures.py tests/unit/test_s3_fixtures.py
git commit -m "test(aws-s3): add multi-format fixture writers"
```

---

## Task 6: S3Client

**Files:**
- Create: `etl_framework/aws_s3/client.py`
- Test: `tests/unit/test_s3_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_s3_client.py`:

```python
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.exceptions import S3ObjectNotFoundError


@pytest.fixture
def s3_client():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="data")
        raw.put_object(Bucket="data", Key="a/1.csv", Body=b"id\n1\n")
        raw.put_object(Bucket="data", Key="a/2.csv", Body=b"id\n2\n")
        session = AWSSession(AWSConfig(region="us-east-1"))
        session._clients["s3"] = raw  # inject the moto-backed client
        yield S3Client(session)


def test_list_objects_paginates(s3_client):
    keys = [o["Key"] for o in s3_client.list_objects("data", "a/")]
    assert keys == ["a/1.csv", "a/2.csv"]


def test_head_object_returns_dict(s3_client):
    head = s3_client.head_object("data", "a/1.csv")
    assert head["ContentLength"] == 5


def test_head_object_missing_raises_typed(s3_client):
    with pytest.raises(S3ObjectNotFoundError):
        s3_client.head_object("data", "a/missing.csv")


def test_get_object_body(s3_client):
    body = s3_client.get_object("data", "a/1.csv")
    assert body == b"id\n1\n"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_s3_client.py -v`
Expected: FAIL — `ModuleNotFoundError: etl_framework.aws_s3.client`.

- [ ] **Step 3: Implement S3Client**

Create `etl_framework/aws_s3/client.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_s3_client.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/aws_s3/client.py tests/unit/test_s3_client.py
git commit -m "feat(aws-s3): add S3Client wrapper"
```

---

## Task 7: Object metadata

**Files:**
- Create: `etl_framework/aws_s3/metadata.py`
- Test: `tests/unit/test_s3_metadata.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_s3_metadata.py`:

```python
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.metadata import read_object_metadata


@pytest.fixture
def s3_client():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="data")
        raw.put_object(
            Bucket="data", Key="a/1.csv", Body=b"id\n1\n", ContentType="text/csv"
        )
        session = AWSSession(AWSConfig(region="us-east-1"))
        session._clients["s3"] = raw
        yield S3Client(session)


def test_reads_core_metadata(s3_client):
    m = read_object_metadata(s3_client, "data", "a/1.csv")
    assert m.bucket == "data"
    assert m.key == "a/1.csv"
    assert m.size_bytes == 5
    assert m.content_type == "text/csv"
    assert m.etag  # non-empty
    assert m.storage_class == "STANDARD"
    assert m.last_modified is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_s3_metadata.py -v`
Expected: FAIL — `ModuleNotFoundError: etl_framework.aws_s3.metadata`.

- [ ] **Step 3: Implement read_object_metadata**

Create `etl_framework/aws_s3/metadata.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_s3_metadata.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/aws_s3/metadata.py tests/unit/test_s3_metadata.py
git commit -m "feat(aws-s3): add object metadata reader"
```

---

## Task 8: Row counts (footer + S3 Select + RowCounter)

**Files:**
- Create: `etl_framework/aws_s3/row_count.py`
- Test: `tests/unit/test_s3_row_count.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_s3_row_count.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow.fs as pafs
import pytest

from etl_framework.aws_s3.row_count import (
    footer_row_count,
    select_row_count,
    RowCounter,
)
from etl_framework.exceptions import UnsupportedFormatError
from tests.helpers.s3_fixtures import write_parquet, write_orc, SAMPLE_ROWS


def test_footer_counts_parquet(tmp_path):
    p = write_parquet(tmp_path / "d.parquet")
    count = footer_row_count(pafs.LocalFileSystem(), str(p), "parquet")
    assert count == len(SAMPLE_ROWS)


def test_footer_counts_orc(tmp_path):
    p = write_orc(tmp_path / "d.orc")
    count = footer_row_count(pafs.LocalFileSystem(), str(p), "orc")
    assert count == len(SAMPLE_ROWS)


def test_footer_rejects_non_footer_format(tmp_path):
    with pytest.raises(UnsupportedFormatError):
        footer_row_count(pafs.LocalFileSystem(), "x.csv", "csv")


def test_select_row_count_parses_count(monkeypatch):
    fake_client = MagicMock()
    fake_client.select_object_content.return_value = "3\n"
    n = select_row_count(fake_client, "b", "k.csv", "csv")
    assert n == 3
    # CSV uses FileHeaderInfo=USE so COUNT(*) excludes the header row.
    args, kwargs = fake_client.select_object_content.call_args
    assert "CSV" in kwargs["input_serialization"]


def test_select_row_count_json_serialization(monkeypatch):
    fake_client = MagicMock()
    fake_client.select_object_content.return_value = "3\n"
    select_row_count(fake_client, "b", "k.json", "json")
    _, kwargs = fake_client.select_object_content.call_args
    assert "JSON" in kwargs["input_serialization"]


def test_rowcounter_routes_csv_to_select():
    fake_client = MagicMock()
    fake_client.select_object_content.return_value = "3\n"
    rc = RowCounter(fake_client, fs=MagicMock())
    result = rc.count("b", "k.csv", "csv")
    assert result.row_count == 3
    assert result.engine == "s3_select"


def test_rowcounter_routes_parquet_to_footer(tmp_path):
    p = write_parquet(tmp_path / "d.parquet")
    fake_client = MagicMock()
    rc = RowCounter(fake_client, fs=pafs.LocalFileSystem())
    # For the footer path, key is the fs path; bucket is prefixed then stripped.
    result = rc.count("", str(p), "parquet")
    assert result.row_count == len(SAMPLE_ROWS)
    assert result.engine == "pyarrow_footer"
    fake_client.select_object_content.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_s3_row_count.py -v`
Expected: FAIL — `ModuleNotFoundError: etl_framework.aws_s3.row_count`.

- [ ] **Step 3: Implement row counting**

Create `etl_framework/aws_s3/row_count.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_s3_row_count.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/aws_s3/row_count.py tests/unit/test_s3_row_count.py
git commit -m "feat(aws-s3): add row counting (S3 Select + pyarrow footer)"
```

---

## Task 9: Partition discovery

**Files:**
- Create: `etl_framework/aws_s3/partitions.py`
- Test: `tests/unit/test_s3_partitions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_s3_partitions.py`:

```python
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.partitions import discover_partitions


@pytest.fixture
def s3_client():
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="lake")
        for key in [
            "t/dt=2026-01-01/region=us/part-0.parquet",
            "t/dt=2026-01-01/region=us/part-1.parquet",
            "t/dt=2026-01-01/region=eu/part-0.parquet",
            "t/dt=2026-01-02/region=us/part-0.parquet",
        ]:
            raw.put_object(Bucket="lake", Key=key, Body=b"x")
        session = AWSSession(AWSConfig(region="us-east-1"))
        session._clients["s3"] = raw
        yield S3Client(session)


def test_discovers_partition_columns_in_order(s3_client):
    scheme = discover_partitions(s3_client, "lake", "t/")
    assert scheme.columns == ["dt", "region"]


def test_counts_objects_per_leaf_partition(s3_client):
    scheme = discover_partitions(s3_client, "lake", "t/")
    by_values = {tuple(e.values.items()): e.object_count for e in scheme.entries}
    assert by_values[(("dt", "2026-01-01"), ("region", "us"))] == 2
    assert by_values[(("dt", "2026-01-01"), ("region", "eu"))] == 1
    assert by_values[(("dt", "2026-01-02"), ("region", "us"))] == 1


def test_ignores_non_hive_keys(s3_client):
    # a stray non-partitioned object under the prefix must not create a column
    s3_client._s3.put_object(Bucket="lake", Key="t/_SUCCESS", Body=b"")
    scheme = discover_partitions(s3_client, "lake", "t/")
    assert scheme.columns == ["dt", "region"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_s3_partitions.py -v`
Expected: FAIL — `ModuleNotFoundError: etl_framework.aws_s3.partitions`.

- [ ] **Step 3: Implement discover_partitions**

Create `etl_framework/aws_s3/partitions.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_s3_partitions.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/aws_s3/partitions.py tests/unit/test_s3_partitions.py
git commit -m "feat(aws-s3): add Hive-style partition discovery"
```

---

## Task 10: Format validation + schema assertion

**Files:**
- Create: `etl_framework/aws_s3/formats.py`
- Test: `tests/unit/test_s3_formats.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_s3_formats.py`:

```python
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.formats import validate_format
from etl_framework.exceptions import FileFormatValidationError, SchemaValidationError
from tests.helpers.s3_fixtures import write_parquet, SAMPLE_ROWS


@pytest.fixture
def s3(tmp_path):
    with mock_aws():
        raw = boto3.client("s3", region_name="us-east-1")
        raw.create_bucket(Bucket="data")
        raw.put_object(Bucket="data", Key="ok.csv", Body=b"id,name\n1,alice\n")
        raw.put_object(Bucket="data", Key="bad.parquet", Body=b"not a parquet file")
        p = write_parquet(tmp_path / "ok.parquet")
        raw.put_object(Bucket="data", Key="ok.parquet", Body=p.read_bytes())
        session = AWSSession(AWSConfig(region="us-east-1"))
        session._clients["s3"] = raw
        yield S3Client(session)


def test_valid_csv_parses(s3):
    r = validate_format(s3, "data", "ok.csv", "csv")
    assert r.parsed is True
    assert r.schema_ok is None


def test_corrupt_parquet_raises(s3):
    with pytest.raises(FileFormatValidationError):
        validate_format(s3, "data", "bad.parquet", "parquet")


def test_schema_assert_passes(s3):
    r = validate_format(s3, "data", "ok.parquet", "parquet",
                        expected_schema={"id": "int64", "name": "string"})
    assert r.parsed is True
    assert r.schema_ok is True


def test_schema_drift_raises_with_missing_and_extra(s3):
    with pytest.raises(SchemaValidationError) as exc:
        validate_format(s3, "data", "ok.parquet", "parquet",
                        expected_schema={"id": "int64", "email": "string"})
    assert "email" in exc.value.missing_in_target
    assert "name" in exc.value.extra_in_target
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_s3_formats.py -v`
Expected: FAIL — `ModuleNotFoundError: etl_framework.aws_s3.formats`.

- [ ] **Step 3: Implement validate_format**

Create `etl_framework/aws_s3/formats.py`:

```python
from __future__ import annotations

import io
import json

import pyarrow.orc as orc
import pyarrow.parquet as pq

from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.models import FormatValidationResult
from etl_framework.exceptions import (
    FileFormatValidationError,
    SchemaValidationError,
    UnsupportedFormatError,
)


def _actual_schema(fmt: str, data: bytes) -> dict[str, str]:
    """Return {column: type_string} for the object's inferred schema."""
    buf = io.BytesIO(data)
    if fmt == "parquet":
        schema = pq.ParquetFile(buf).schema_arrow
        return {name: str(schema.field(name).type) for name in schema.names}
    if fmt == "orc":
        schema = orc.ORCFile(buf).schema
        return {name: str(schema.field(name).type) for name in schema.names}
    if fmt == "csv":
        header = data.decode("utf-8").splitlines()[0]
        return {col.strip(): "string" for col in header.split(",")}
    if fmt == "json":
        first = data.decode("utf-8").splitlines()[0]
        return {k: "string" for k in json.loads(first).keys()}
    raise UnsupportedFormatError(fmt)


def _parse_check(fmt: str, data: bytes) -> None:
    """Raise if the bytes do not parse as ``fmt``."""
    buf = io.BytesIO(data)
    if fmt == "parquet":
        pq.ParquetFile(buf).metadata  # noqa: B018 — forces footer parse
    elif fmt == "orc":
        orc.ORCFile(buf).nrows
    elif fmt == "csv":
        text = data.decode("utf-8")
        if not text.strip():
            raise ValueError("empty CSV")
    elif fmt == "json":
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                json.loads(line)
    else:
        raise UnsupportedFormatError(fmt)


def validate_format(
    client: S3Client,
    bucket: str,
    key: str,
    fmt: str,
    expected_schema: dict[str, str] | None = None,
) -> FormatValidationResult:
    """Confirm an object parses as ``fmt``; optionally assert its schema.

    Parse failure -> FileFormatValidationError.
    Schema drift  -> SchemaValidationError (missing/extra columns).
    """
    if fmt not in {"csv", "json", "parquet", "orc"}:
        raise UnsupportedFormatError(fmt)

    data = client.get_object(bucket, key)
    try:
        _parse_check(fmt, data)
    except UnsupportedFormatError:
        raise
    except Exception as exc:
        raise FileFormatValidationError(bucket, key, fmt, exc) from exc

    if expected_schema is None:
        return FormatValidationResult(bucket=bucket, key=key, fmt=fmt, parsed=True)

    actual = _actual_schema(fmt, data)
    missing = sorted(set(expected_schema) - set(actual))
    extra = sorted(set(actual) - set(expected_schema))
    if missing or extra:
        raise SchemaValidationError(
            query_name=f"s3://{bucket}/{key}",
            missing_in_target=missing,
            extra_in_target=extra,
        )
    return FormatValidationResult(
        bucket=bucket, key=key, fmt=fmt, parsed=True, schema_ok=True,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_s3_formats.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add etl_framework/aws_s3/formats.py tests/unit/test_s3_formats.py
git commit -m "feat(aws-s3): add format validation with optional schema assertion"
```

---

## Task 11: Opt-in live integration test

**Files:**
- Create: `tests/integration/test_s3_live.py`

- [ ] **Step 1: Write the gated integration test**

Create `tests/integration/test_s3_live.py`:

```python
"""Live-AWS smoke test for the aws_s3 module.

Skipped unless ATOM_AWS_LIVE=1. Requires a real, readable bucket and a small
object set. Env vars:
  ATOM_AWS_LIVE=1
  ATOM_AWS_S3_BUCKET=<bucket>
  ATOM_AWS_S3_CSV_KEY=<key of a small csv object>
  AWS_REGION / standard boto3 credential env vars
"""
from __future__ import annotations

import os

import pyarrow.fs as pafs
import pytest

from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.metadata import read_object_metadata
from etl_framework.aws_s3.row_count import RowCounter

pytestmark = pytest.mark.skipif(
    os.environ.get("ATOM_AWS_LIVE") != "1",
    reason="live AWS tests disabled (set ATOM_AWS_LIVE=1 to enable)",
)


@pytest.fixture
def live_client():
    cfg = AWSConfig(region=os.environ.get("AWS_REGION", "us-east-1"))
    return S3Client(AWSSession(cfg))


def test_live_metadata_and_row_count(live_client):
    bucket = os.environ["ATOM_AWS_S3_BUCKET"]
    key = os.environ["ATOM_AWS_S3_CSV_KEY"]

    meta = read_object_metadata(live_client, bucket, key)
    assert meta.size_bytes > 0

    counter = RowCounter(live_client, fs=pafs.LocalFileSystem())
    result = counter.count(bucket, key, "csv")
    assert result.engine == "s3_select"
    assert result.row_count >= 0
```

- [ ] **Step 2: Verify it skips by default**

Run: `pytest tests/integration/test_s3_live.py -v`
Expected: SKIPPED (ATOM_AWS_LIVE not set).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_s3_live.py
git commit -m "test(aws-s3): add opt-in live-AWS smoke test"
```

---

## Task 12: Full suite green + README note

**Files:**
- Modify: `README.md` (add an `aws_s3` usage snippet near existing module docs)

- [ ] **Step 1: Run the full new suite**

Run: `pytest tests/unit/test_aws_config.py tests/unit/test_aws_exceptions.py tests/unit/test_aws_session.py tests/unit/test_s3_models.py tests/unit/test_s3_fixtures.py tests/unit/test_s3_client.py tests/unit/test_s3_metadata.py tests/unit/test_s3_row_count.py tests/unit/test_s3_partitions.py tests/unit/test_s3_formats.py tests/integration/test_s3_live.py -v`
Expected: all PASS except the live test SKIPPED.

- [ ] **Step 2: Run the whole project suite for regressions**

Run: `pytest -q`
Expected: no new failures introduced by this work.

- [ ] **Step 3: Add a README usage snippet**

In `README.md`, under the existing module/integration docs, add a short `aws_s3` section:

````markdown
### AWS S3 (`etl_framework/aws_s3`)

```python
from etl_framework.aws.config import AWSConfig
from etl_framework.aws.session import AWSSession
from etl_framework.aws_s3.client import S3Client
from etl_framework.aws_s3.metadata import read_object_metadata
from etl_framework.aws_s3.row_count import RowCounter
from etl_framework.aws_s3.partitions import discover_partitions
from etl_framework.aws_s3.formats import validate_format
import pyarrow.fs as pafs

session = AWSSession(AWSConfig(region="us-east-1"))
client = S3Client(session)

meta = read_object_metadata(client, "my-bucket", "data/part-0.parquet")

fs = pafs.S3FileSystem(region="us-east-1")
count = RowCounter(client, fs=fs).count("my-bucket", "data/part-0.parquet", "parquet")

scheme = discover_partitions(client, "my-bucket", "table/")

validate_format(client, "my-bucket", "data/part-0.parquet", "parquet",
                expected_schema={"id": "int64", "name": "string"})
```
````

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(aws-s3): add usage snippet to README"
```

---

## Self-Review Notes

- **Spec coverage:** metadata (Task 7), row counts CSV/JSON/Parquet/ORC (Task 8), Hive partitions (Task 9), format validation + schema assert for all 4 formats (Task 10), shared AWS foundation config/session/exceptions (Tasks 1–3), moto/Stubber-style offline tests + opt-in live (every task + Task 11), zero new runtime deps / moto in dev only (Task 1). All spec sections mapped.
- **Injection pattern:** `AWSSession._clients[service] = raw` injects the moto client; `RowCounter(fs=...)` injects the pyarrow filesystem — both mirror the existing `DBEngine(_engine=...)` convention, keeping unit tests fully offline.
- **Type consistency:** `RowCountResult.engine` values `"s3_select"` / `"pyarrow_footer"` match `RowCountEngine`; `SchemaValidationError(query_name, missing_in_target, extra_in_target)` matches the existing signature in `exceptions.py`.
