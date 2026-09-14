# File Watcher Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `file_watcher` job type (backend + tests only; frontend UI wiring is a separate follow-up plan) that polls a local folder, S3, or SFTP/SCP location for a file matching a name pattern — optionally also containing given text — within an optional time window and/or up to a max number of tries, then passes/fails as a normal DAG step so it can gate downstream sequence steps.

**Architecture:** A new poll primitive (`WatchSpec`/`wait_for_watched_file`) in `etl_framework/reconciliation/file_mapping.py` generalizes the existing `ReadinessSpec`/`wait_for_ready_files` pattern. `RemoteFileSourceSession` (`api/services/multi_file_remote.py`) gains a raw-bytes/text read method for content matching, reusing its existing local/S3/SFTP client machinery. `file_watcher` becomes one more `job_type` in `JobDefinition` (`api/schemas.py`), `validate_job_definition` (`etl_framework/runner/job_validation.py`), and `RunExecutor._build_case` (`api/services/run_executor.py`) — the same three places every other job type is wired into, no new concepts.

**Tech Stack:** Python (FastAPI, Pydantic, dataclasses), pytest, existing `RunExecutor`/`ReconciliationResult` machinery.

**Spec:** `docs/superpowers/specs/2026-09-14-file-watcher-job-design.md`

---

### Task 1: `WatchSpec` / `wait_for_watched_file` poll primitive

**Files:**
- Modify: `etl_framework/reconciliation/file_mapping.py` (append after `wait_for_ready_files`, currently ending at line 827)
- Test: `tests/unit/test_wait_for_watched_file.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_wait_for_watched_file.py`:

```python
# tests/unit/test_wait_for_watched_file.py
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from etl_framework.reconciliation.file_mapping import (
    DiscoveredFile,
    FileWatchTimeout,
    WatchSpec,
    wait_for_watched_file,
)


def _df(name: str) -> DiscoveredFile:
    return DiscoveredFile(path=f"/watch/{name}", file_name=name, tokens={})


def _clock(*values: datetime):
    """Returns a `now` callable that yields `values` in order, then repeats the last one."""
    remaining = list(values)

    def _now() -> datetime:
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    return _now


def test_wait_for_watched_file_returns_immediately_when_already_present() -> None:
    calls = []

    def discover():
        calls.append(1)
        return [_df("DONE.flag")]

    sleeps = []
    result = wait_for_watched_file(
        discover,
        WatchSpec(max_tries=3, poll_interval_seconds=1),
        sleep=sleeps.append,
    )

    assert result.file.file_name == "DONE.flag"
    assert result.tries == 1
    assert len(calls) == 1
    assert sleeps == []


def test_wait_for_watched_file_polls_until_match_found() -> None:
    responses = [[], [], [_df("DONE.flag")]]

    def discover():
        return responses.pop(0)

    sleeps = []
    result = wait_for_watched_file(
        discover,
        WatchSpec(max_tries=5, poll_interval_seconds=2),
        sleep=sleeps.append,
    )

    assert result.file.file_name == "DONE.flag"
    assert result.tries == 3
    assert sleeps == [2, 2]


def test_wait_for_watched_file_raises_after_max_tries_exhausted() -> None:
    def discover():
        return []

    sleeps = []
    with pytest.raises(FileWatchTimeout, match="no matching file found after 3 attempt"):
        wait_for_watched_file(
            discover,
            WatchSpec(max_tries=3, poll_interval_seconds=1),
            sleep=sleeps.append,
        )
    assert sleeps == [1, 1]


def test_wait_for_watched_file_timeout_carries_tries_and_elapsed() -> None:
    def discover():
        return []

    now = _clock(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
    )

    with pytest.raises(FileWatchTimeout) as excinfo:
        wait_for_watched_file(
            discover,
            WatchSpec(max_tries=2, poll_interval_seconds=1),
            sleep=lambda s: None,
            now=now,
        )
    assert excinfo.value.tries == 2
    assert excinfo.value.elapsed_seconds == 10.0


def test_wait_for_watched_file_respects_window_end() -> None:
    def discover():
        return []

    now = _clock(
        datetime(2026, 1, 1, 22, 0, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 23, 31, tzinfo=timezone.utc),
    )

    with pytest.raises(FileWatchTimeout, match="window_end"):
        wait_for_watched_file(
            discover,
            WatchSpec(window_end="2026-01-01T23:30:00+00:00", poll_interval_seconds=60),
            sleep=lambda s: None,
            now=now,
        )


def test_wait_for_watched_file_waits_for_window_start_before_first_poll() -> None:
    calls = []

    def discover():
        calls.append(1)
        return [_df("DONE.flag")]

    now = _clock(datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc))
    sleeps = []
    result = wait_for_watched_file(
        discover,
        WatchSpec(window_start="22:00", max_tries=1, poll_interval_seconds=1),
        sleep=sleeps.append,
        now=now,
    )

    assert result.file.file_name == "DONE.flag"
    assert sleeps == [3600.0]  # slept from 21:00 to 22:00 before ever calling discover


def test_wait_for_watched_file_content_match_filters_by_text() -> None:
    files = [_df("a.flag"), _df("b.flag")]
    contents = {"a.flag": "STATUS=PENDING", "b.flag": "STATUS=COMPLETE"}

    result = wait_for_watched_file(
        lambda: files,
        WatchSpec(max_tries=1, content_text="STATUS=COMPLETE"),
        read_text=lambda f: contents[f.file_name],
        sleep=lambda s: None,
    )

    assert result.file.file_name == "b.flag"
    assert result.matched_snippet == "STATUS=COMPLETE"


def test_wait_for_watched_file_content_match_regex() -> None:
    files = [_df("a.flag")]
    result = wait_for_watched_file(
        lambda: files,
        WatchSpec(max_tries=1, content_text=r"STATUS=\w+", content_is_regex=True),
        read_text=lambda f: "prefix STATUS=COMPLETE suffix",
        sleep=lambda s: None,
    )
    assert result.file.file_name == "a.flag"


def test_wait_for_watched_file_content_match_without_read_text_raises() -> None:
    with pytest.raises(ValueError, match="content_text match requires a read_text callable"):
        wait_for_watched_file(
            lambda: [_df("a.flag")],
            WatchSpec(max_tries=1, content_text="X"),
            sleep=lambda s: None,
        )


def test_watch_spec_requires_max_tries_or_window_end() -> None:
    with pytest.raises(ValueError, match="max_tries and/or window_end"):
        WatchSpec()


def test_watch_spec_rejects_non_positive_poll_interval() -> None:
    with pytest.raises(ValueError, match="poll_interval_seconds must be a positive number"):
        WatchSpec(max_tries=1, poll_interval_seconds=0)


def test_watch_spec_rejects_non_positive_max_tries() -> None:
    with pytest.raises(ValueError, match="max_tries must be a positive integer"):
        WatchSpec(max_tries=0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_wait_for_watched_file.py -v`
Expected: FAIL with `ImportError: cannot import name 'WatchSpec'` (nothing implemented yet)

- [ ] **Step 3: Implement `WatchSpec`, `WatchResult`, `FileWatchTimeout`, `wait_for_watched_file`**

Append to `etl_framework/reconciliation/file_mapping.py` (after `wait_for_ready_files`, which currently ends the file at line 827):

```python


class FileWatchTimeout(TimeoutError):
    """Raised by wait_for_watched_file when no match was found before
    max_tries was exhausted or the current time passed window_end."""

    def __init__(self, message: str, tries: int, elapsed_seconds: float) -> None:
        super().__init__(message)
        self.tries = tries
        self.elapsed_seconds = elapsed_seconds


@dataclass(frozen=True)
class WatchSpec:
    poll_interval_seconds: float = 30.0
    max_tries: int | None = None
    window_start: str | None = None   # "HH:MM" (recurring) or ISO datetime (one-shot)
    window_end: str | None = None     # "HH:MM" (recurring) or ISO datetime (one-shot)
    content_text: str | None = None
    content_is_regex: bool = False

    def __post_init__(self) -> None:
        if self.max_tries is None and not self.window_end:
            raise ValueError(
                "WatchSpec requires max_tries and/or window_end -- an unbounded watch is not allowed"
            )
        if self.poll_interval_seconds <= 0:
            raise ValueError("WatchSpec.poll_interval_seconds must be a positive number")
        if self.max_tries is not None and (
            not isinstance(self.max_tries, int) or isinstance(self.max_tries, bool) or self.max_tries < 1
        ):
            raise ValueError("WatchSpec.max_tries must be a positive integer")


@dataclass(frozen=True)
class WatchResult:
    file: DiscoveredFile
    tries: int
    elapsed_seconds: float
    matched_snippet: str | None = None


_CONTENT_SNIPPET_LENGTH = 200


def _resolve_window_bound(value: str | None, reference: datetime) -> datetime | None:
    """"HH:MM" resolves against `reference`'s own date (recurring window);
    anything else is parsed as a full ISO datetime (one-shot window)."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        pass
    hour_str, sep, minute_str = value.partition(":")
    if not sep:
        raise ValueError(f"invalid time window value: {value!r}; expected 'HH:MM' or an ISO datetime")
    try:
        hour, minute = int(hour_str), int(minute_str)
    except ValueError as exc:
        raise ValueError(f"invalid time window value: {value!r}; expected 'HH:MM' or an ISO datetime") from exc
    return reference.replace(hour=hour, minute=minute, second=0, microsecond=0)


def wait_for_watched_file(
    discover: Callable[[], list[DiscoveredFile]],
    spec: WatchSpec,
    read_text: Callable[["DiscoveredFile"], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> WatchResult:
    """Poll `discover()` until a candidate matches (filtered by `spec.content_text`
    via `read_text`, when given), or raise FileWatchTimeout once `spec.max_tries`
    is exhausted or `now()` passes `spec.window_end`. If `spec.window_start` is in
    the future, sleeps until it before the first `discover()` call. `sleep`/`now`
    are injectable for tests; production callers use the real clock.
    """
    start_now = now()
    window_start = _resolve_window_bound(spec.window_start, start_now)
    window_end = _resolve_window_bound(spec.window_end, start_now)

    if window_start is not None and start_now < window_start:
        sleep((window_start - start_now).total_seconds())

    content_regex = re.compile(spec.content_text) if (spec.content_text and spec.content_is_regex) else None
    tries = 0

    while True:
        current = now()
        if window_end is not None and current > window_end:
            raise FileWatchTimeout(
                f"no matching file found before window_end ({spec.window_end}) after {tries} attempt(s)",
                tries=tries,
                elapsed_seconds=(current - start_now).total_seconds(),
            )
        tries += 1
        for candidate in discover():
            if spec.content_text is None:
                return WatchResult(candidate, tries, (now() - start_now).total_seconds())
            if read_text is None:
                raise ValueError("content_text match requires a read_text callable")
            text = read_text(candidate)
            matched = content_regex.search(text) if content_regex is not None else spec.content_text in text
            if matched:
                snippet = text[:_CONTENT_SNIPPET_LENGTH]
                return WatchResult(candidate, tries, (now() - start_now).total_seconds(), matched_snippet=snippet)
        if spec.max_tries is not None and tries >= spec.max_tries:
            raise FileWatchTimeout(
                f"no matching file found after {tries} attempt(s) (max_tries={spec.max_tries})",
                tries=tries,
                elapsed_seconds=(now() - start_now).total_seconds(),
            )
        sleep(spec.poll_interval_seconds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_wait_for_watched_file.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/reconciliation/file_mapping.py tests/unit/test_wait_for_watched_file.py
git commit -m "feat(file-watcher): add WatchSpec/wait_for_watched_file poll primitive"
```

---

### Task 2: Raw content read on `RemoteFileSourceSession`

**Files:**
- Modify: `api/services/multi_file_remote.py:119-131` (`read_file` method and the lines right after it)
- Test: `tests/unit/test_multi_file_remote.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_multi_file_remote.py` (near the existing `read_file`/local-kind tests):

```python
def test_remote_file_source_session_read_text_local(tmp_path, monkeypatch) -> None:
    from api.services import file_source

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "DONE.flag").write_text("STATUS=COMPLETE\n", encoding="utf-8")

    session = RemoteFileSourceSession({})
    spec = FileSourceSpec(kind="local", root=str(tmp_path), pattern="DONE.flag")
    discovered = session.discover(spec)

    assert session.read_text(discovered[0], spec) == "STATUS=COMPLETE\n"


def test_remote_file_source_session_read_text_s3(monkeypatch) -> None:
    built_clients: list[_FakeS3Client] = []

    def _fake_build_s3_client(config_snapshot, spec):
        client = _FakeS3Client()
        built_clients.append(client)
        return client

    monkeypatch.setattr("api.services.multi_file_remote.build_s3_client", _fake_build_s3_client)

    spec = FileSourceSpec(kind="s3", root="s3://bucket/prefix", pattern="sales_{region}.csv")
    session = RemoteFileSourceSession({})
    discovered = session.discover(spec)

    text = session.read_text(discovered[0], spec)
    assert text == "id,value\n1,alpha\n"


def test_remote_file_source_session_read_text_caps_length(tmp_path, monkeypatch) -> None:
    from api.services import file_source
    from api.services.multi_file_remote import _CONTENT_MATCH_READ_LIMIT

    monkeypatch.setattr(file_source, "_UPLOAD_BASE", tmp_path.resolve())
    monkeypatch.setattr(file_source, "_UPLOAD_BASES", (tmp_path.resolve(),))
    (tmp_path / "BIG.flag").write_text("x" * (_CONTENT_MATCH_READ_LIMIT * 2), encoding="utf-8")

    session = RemoteFileSourceSession({})
    spec = FileSourceSpec(kind="local", root=str(tmp_path), pattern="BIG.flag")
    discovered = session.discover(spec)

    assert len(session.read_text(discovered[0], spec)) == _CONTENT_MATCH_READ_LIMIT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_multi_file_remote.py -v -k read_text`
Expected: FAIL with `AttributeError: 'RemoteFileSourceSession' object has no attribute 'read_text'`

- [ ] **Step 3: Implement `read_text`/`_read_bytes`**

In `api/services/multi_file_remote.py`, add a module-level constant near the top (after the imports, before `resolve_file_source_credentials`):

```python
_CONTENT_MATCH_READ_LIMIT = 65536
```

Then insert these two methods into `RemoteFileSourceSession`, right after `read_file` ends and before `def close(self)`:

```python
    def read_text(self, file: DiscoveredFile, spec: FileSourceSpec) -> str:
        """Read up to _CONTENT_MATCH_READ_LIMIT bytes of `file` as text, for
        file_watcher content matching. Undecodable bytes are dropped rather
        than raising -- a watcher polling mid-write may see a partial or
        binary-looking snapshot, which should read as "no match" not error.
        """
        raw = self._read_bytes(file, spec)
        return raw.decode("utf-8", errors="ignore")

    def _read_bytes(self, file: DiscoveredFile, spec: FileSourceSpec) -> bytes:
        if spec.kind == "local":
            with open(file.path, "rb") as fh:
                return fh.read(_CONTENT_MATCH_READ_LIMIT)
        if spec.kind == "s3":
            parsed = urlparse(file.path)
            obj = self._client_for(spec).get_object(
                Bucket=parsed.netloc,
                Key=unquote(parsed.path.lstrip("/")),
                Range=f"bytes=0-{_CONTENT_MATCH_READ_LIMIT - 1}",
            )
            return obj["Body"].read()
        if spec.kind == "sftp":
            client = self._client_for(spec)
            with client.open(file.path, "rb") as fh:
                return fh.read(_CONTENT_MATCH_READ_LIMIT)
        raise ValueError(f"Unsupported multi_file source kind: {spec.kind}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_multi_file_remote.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add api/services/multi_file_remote.py tests/unit/test_multi_file_remote.py
git commit -m "feat(file-watcher): add raw content read to RemoteFileSourceSession"
```

---

### Task 3: `file_watcher` job_type on `JobDefinition`

**Files:**
- Modify: `api/schemas.py:708-713` (Literal), `api/schemas.py:863-870` (validator chain)
- Test: `tests/unit/test_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_api.py` (near `test_create_dbt_artifact_job`):

```python
def test_create_file_watcher_job(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "watch_sales_drop",
            "job_type": "file_watcher",
            "query": "",
            "key_columns": [],
            "params": {
                "location": {"kind": "local", "root": "/data/inbound", "pattern": "SALES_*.csv"},
                "max_tries": 10,
                "poll_interval_seconds": 30,
            },
        },
    )
    assert resp.status_code == 201
    assert resp.json()["job_type"] == "file_watcher"


def test_create_file_watcher_job_rejects_missing_location(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "bad_watch",
            "job_type": "file_watcher",
            "query": "",
            "key_columns": [],
            "params": {"max_tries": 10},
        },
    )
    assert resp.status_code == 422


def test_create_file_watcher_job_rejects_unbounded_watch(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "bad_watch",
            "job_type": "file_watcher",
            "query": "",
            "key_columns": [],
            "params": {"location": {"kind": "local", "root": "/data/inbound", "pattern": "*.csv"}},
        },
    )
    assert resp.status_code == 422


def test_create_file_watcher_job_rejects_remote_kind_without_credentials(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "bad_watch",
            "job_type": "file_watcher",
            "query": "",
            "key_columns": [],
            "params": {
                "location": {"kind": "sftp", "root": "/inbound", "pattern": "*.csv"},
                "max_tries": 5,
            },
        },
    )
    assert resp.status_code == 422


def test_create_file_watcher_job_accepts_scp_kind_and_content_match(client):
    resp = client.post(
        "/api/jobs",
        json={
            "name": "watch_flag",
            "job_type": "file_watcher",
            "query": "",
            "key_columns": [],
            "params": {
                "location": {
                    "kind": "scp", "root": "/inbound", "pattern": "*.flag",
                    "credentials_ref": "scp_host",
                },
                "content_match": {"text": "STATUS=COMPLETE"},
                "window_end": "23:30",
            },
        },
    )
    assert resp.status_code == 201
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_api.py -v -k file_watcher`
Expected: FAIL — `file_watcher` is not a permitted `job_type` value (Pydantic literal error), so the first test gets a 422 instead of 201.

- [ ] **Step 3: Add `"file_watcher"` to the `job_type` Literal**

In `api/schemas.py`, modify the `JobDefinition.job_type` Literal (currently lines 708-713):

```python
    job_type: Literal[
        "reconciliation", "health_check", "bo_report", "automic_job", "dbt_artifact",
        "freshness", "cross_job_assertion", "schema_snapshot", "profile", "api_reconciliation",
        "bo_job", "ds_job", "s3_row_count", "s3_format_validation", "s3_partition_check",
        "aws_glue_catalog_compare", "aws_glue_job_run", "aws_athena_query", "airflow_dag_run", "compare",
        "file_watcher",
    ] = "reconciliation"
```

- [ ] **Step 4: Add the `file_watcher` validation branch**

In `api/schemas.py`, insert a new `elif` branch into `validate_reconciliation_contract`, right after the `elif self.job_type in ("schema_snapshot", "profile"):` block and before the final `if self.job_type == "compare":` statement (currently around line 866-870):

```python
        elif self.job_type == "file_watcher":
            location = self.params.get("location")
            if not isinstance(location, dict):
                raise ValueError("file_watcher jobs require a 'location' object in params")
            kind = location.get("kind")
            if kind not in ("local", "s3", "sftp", "scp"):
                raise ValueError("file_watcher location.kind must be 'local', 's3', 'sftp', or 'scp'")
            if not location.get("root") or not location.get("pattern"):
                raise ValueError("file_watcher location requires 'root' and 'pattern'")
            if kind in ("s3", "sftp", "scp") and not location.get("credentials_ref"):
                raise ValueError(f"file_watcher location.kind '{kind}' requires 'credentials_ref'")
            max_tries = self.params.get("max_tries")
            window_end = self.params.get("window_end")
            if max_tries is None and not window_end:
                raise ValueError(
                    "file_watcher jobs require 'max_tries' and/or 'window_end' -- "
                    "an unbounded watch is not allowed"
                )
            if max_tries is not None and (
                not isinstance(max_tries, int) or isinstance(max_tries, bool) or max_tries < 1
            ):
                raise ValueError("file_watcher max_tries must be a positive integer")
            poll_interval = self.params.get("poll_interval_seconds")
            if poll_interval is not None:
                try:
                    positive_poll_interval = float(poll_interval) > 0
                except (TypeError, ValueError):
                    positive_poll_interval = False
                if not positive_poll_interval:
                    raise ValueError("file_watcher poll_interval_seconds must be a positive number")
            content_match = self.params.get("content_match")
            if content_match is not None and (
                not isinstance(content_match, dict) or not content_match.get("text")
            ):
                raise ValueError("file_watcher content_match, if given, requires a 'text' field")
```

This must stay part of the `if self.job_type == "bo_report": ... elif ...` chain (i.e. `elif`, not `if`) so it doesn't run alongside unrelated branches.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_api.py -v -k file_watcher`
Expected: ALL PASS

- [ ] **Step 6: Run the full API test file to check for regressions**

Run: `python -m pytest tests/unit/test_api.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add api/schemas.py tests/unit/test_api.py
git commit -m "feat(file-watcher): add file_watcher job_type to JobDefinition"
```

---

### Task 4: `file_watcher` validation in `validate_job_definition`

**Files:**
- Modify: `etl_framework/runner/job_validation.py` (new `_validate_file_watcher` function + dispatch line in `validate_job_definition`)
- Test: `tests/unit/test_job_validation.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_job_validation.py` (near the `airflow_dag_run` tests):

```python
def test_file_watcher_valid_job_has_no_issues():
    issues = validate_job_definition({
        "name": "watch_sales_drop",
        "job_type": "file_watcher",
        "params": {
            "location": {"kind": "local", "root": "/data/inbound", "pattern": "SALES_*.csv"},
            "max_tries": 10,
            "poll_interval_seconds": 30,
        },
    })
    assert issues == []


def test_file_watcher_requires_location():
    issues = validate_job_definition({
        "name": "bad_watch",
        "job_type": "file_watcher",
        "params": {"max_tries": 5},
    })
    assert any(issue.field == "params.location" for issue in issues)


def test_file_watcher_requires_root_and_pattern():
    issues = validate_job_definition({
        "name": "bad_watch",
        "job_type": "file_watcher",
        "params": {"location": {"kind": "local"}, "max_tries": 5},
    })
    fields = {issue.field for issue in issues}
    assert "params.location.root" in fields
    assert "params.location.pattern" in fields


def test_file_watcher_requires_credentials_ref_for_remote_kinds():
    issues = validate_job_definition({
        "name": "bad_watch",
        "job_type": "file_watcher",
        "params": {
            "location": {"kind": "sftp", "root": "/inbound", "pattern": "*.csv"},
            "max_tries": 5,
        },
    })
    assert any(issue.field == "params.location.credentials_ref" for issue in issues)


def test_file_watcher_accepts_scp_kind():
    issues = validate_job_definition({
        "name": "watch_flag",
        "job_type": "file_watcher",
        "params": {
            "location": {
                "kind": "scp", "root": "/inbound", "pattern": "*.flag",
                "credentials_ref": "scp_host",
            },
            "max_tries": 5,
        },
    })
    assert issues == []


def test_file_watcher_requires_max_tries_or_window_end():
    issues = validate_job_definition({
        "name": "bad_watch",
        "job_type": "file_watcher",
        "params": {"location": {"kind": "local", "root": "/data/inbound", "pattern": "*.csv"}},
    })
    assert any(issue.field == "params" for issue in issues)


def test_file_watcher_window_end_alone_satisfies_bound_requirement():
    issues = validate_job_definition({
        "name": "watch_nightly",
        "job_type": "file_watcher",
        "params": {
            "location": {"kind": "local", "root": "/data/inbound", "pattern": "*.csv"},
            "window_end": "23:30",
        },
    })
    assert issues == []


def test_file_watcher_rejects_non_positive_max_tries_and_poll_interval():
    issues = validate_job_definition({
        "name": "bad_watch",
        "job_type": "file_watcher",
        "params": {
            "location": {"kind": "local", "root": "/data/inbound", "pattern": "*.csv"},
            "max_tries": 0,
            "poll_interval_seconds": -1,
        },
    })
    fields = {issue.field for issue in issues}
    assert "params.max_tries" in fields
    assert "params.poll_interval_seconds" in fields


def test_file_watcher_content_match_requires_text():
    issues = validate_job_definition({
        "name": "bad_watch",
        "job_type": "file_watcher",
        "params": {
            "location": {"kind": "local", "root": "/data/inbound", "pattern": "*.flag"},
            "max_tries": 5,
            "content_match": {"is_regex": True},
        },
    })
    assert any(issue.field == "params.content_match" for issue in issues)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_job_validation.py -v -k file_watcher`
Expected: FAIL — `file_watcher` isn't dispatched to any validator yet, so `test_file_watcher_requires_location` etc. find no issues where issues are expected.

- [ ] **Step 3: Implement `_validate_file_watcher` and wire it into the dispatch chain**

In `etl_framework/runner/job_validation.py`, add this function near `_validate_airflow_dag_run` (before `def validate_job_definition`):

```python
def _validate_file_watcher(params: dict[str, Any], issues: list[ValidationIssue]) -> None:
    location = params.get("location")
    if not isinstance(location, dict):
        issues.append(ValidationIssue("params.location", "file_watcher jobs require a 'location' object in params"))
        location = {}
    kind = location.get("kind")
    if kind not in ("local", "s3", "sftp", "scp"):
        issues.append(ValidationIssue(
            "params.location.kind", "file_watcher location.kind must be 'local', 's3', 'sftp', or 'scp'",
        ))
    if not location.get("root"):
        issues.append(ValidationIssue("params.location.root", "file_watcher location requires 'root'"))
    if not location.get("pattern"):
        issues.append(ValidationIssue("params.location.pattern", "file_watcher location requires 'pattern'"))
    if kind in ("s3", "sftp", "scp") and not location.get("credentials_ref"):
        issues.append(ValidationIssue(
            "params.location.credentials_ref",
            f"file_watcher location.kind '{kind}' requires 'credentials_ref'",
        ))
    if params.get("max_tries") is None and not params.get("window_end"):
        issues.append(ValidationIssue(
            "params", "file_watcher jobs require 'max_tries' and/or 'window_end' -- an unbounded watch is not allowed",
        ))
    _positive_int(params, "max_tries", issues)
    if "poll_interval_seconds" in params:
        try:
            if float(params["poll_interval_seconds"]) <= 0:
                issues.append(ValidationIssue("params.poll_interval_seconds", "poll_interval_seconds must be a positive number"))
        except (TypeError, ValueError):
            issues.append(ValidationIssue("params.poll_interval_seconds", "poll_interval_seconds must be a positive number"))
    content_match = params.get("content_match")
    if content_match is not None and (not isinstance(content_match, dict) or not content_match.get("text")):
        issues.append(ValidationIssue("params.content_match", "file_watcher content_match, if given, requires a 'text' field"))
```

Then add one line to the initial job-type-specific block inside `validate_job_definition` (currently lines 219-232), right after the `airflow_dag_run` branch:

```python
    elif job_type == "airflow_dag_run":
        _validate_airflow_dag_run(params, issues)
    elif job_type == "file_watcher":
        _validate_file_watcher(params, issues)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_job_validation.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add etl_framework/runner/job_validation.py tests/unit/test_job_validation.py
git commit -m "feat(file-watcher): validate file_watcher jobs in validate_job_definition"
```

---

### Task 5: `RunExecutor` dispatch and execution

**Files:**
- Modify: `api/services/run_executor.py` (`_build_case` dispatch around line 549-551; new methods placed after `_execute_airflow_dag_run`, currently ending at line 1551)
- Test: `tests/unit/test_run_executor_file_watcher.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_executor_file_watcher.py`, mirroring `tests/unit/test_run_executor_airflow.py`'s fixtures:

```python
# tests/unit/test_run_executor_file_watcher.py
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.schemas import JobDefinition, RunSettings
from api.services.run_executor import RunExecutor
from etl_framework.reconciliation.file_mapping import DiscoveredFile, FileWatchTimeout
from etl_framework.repository.database import Base
from etl_framework.runner.state import TestStatus


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def executor(db_session, config_snapshot=None):
    return RunExecutor(
        db=db_session, run_id="run-1", source_env="qa", target_env="prod",
        job_sequence=[], run_settings=RunSettings(use_live_connections=True),
        config_snapshot=config_snapshot or {},
    )


def job(params=None):
    p = {"location": {"kind": "local", "root": "/data/inbound", "pattern": "SALES_*.csv"}, "max_tries": 5}
    if params:
        p.update(params)
    return JobDefinition(name="watch_sales_drop", job_type="file_watcher", params=p)


class _FakeWatchResult:
    def __init__(self, file, tries=2, elapsed_seconds=4.0, matched_snippet=None):
        self.file = file
        self.tries = tries
        self.elapsed_seconds = elapsed_seconds
        self.matched_snippet = matched_snippet


def test_build_case_dispatches_file_watcher_job_type(db_session, monkeypatch):
    matched = DiscoveredFile(path="/data/inbound/SALES_20260914.csv", file_name="SALES_20260914.csv", tokens={})
    monkeypatch.setattr(
        "etl_framework.reconciliation.file_mapping.wait_for_watched_file",
        lambda *a, **k: _FakeWatchResult(matched, tries=1, elapsed_seconds=0.0),
    )
    run_job = executor(db_session)._build_case(job())
    result = run_job()
    assert result.status == TestStatus.PASSED


def test_execute_file_watcher_passes_on_match(db_session, monkeypatch):
    matched = DiscoveredFile(path="/data/inbound/SALES_20260914.csv", file_name="SALES_20260914.csv", tokens={})
    monkeypatch.setattr(
        "etl_framework.reconciliation.file_mapping.wait_for_watched_file",
        lambda *a, **k: _FakeWatchResult(matched, tries=3, elapsed_seconds=6.0),
    )
    result = executor(db_session)._execute_file_watcher(job())
    assert result.status == TestStatus.PASSED
    assert result.source_file_name == "SALES_20260914.csv"
    assert result.data_artifact_path == "/data/inbound/SALES_20260914.csv"
    assert result.mismatch_summary["tries"] == 3
    assert result.mismatch_summary["elapsed_seconds"] == 6.0
    assert result.mismatches == []


def test_execute_file_watcher_includes_matched_snippet_when_content_matched(db_session, monkeypatch):
    matched = DiscoveredFile(path="/inbound/DONE.flag", file_name="DONE.flag", tokens={})
    monkeypatch.setattr(
        "etl_framework.reconciliation.file_mapping.wait_for_watched_file",
        lambda *a, **k: _FakeWatchResult(matched, tries=1, elapsed_seconds=0.0, matched_snippet="STATUS=COMPLETE"),
    )
    result = executor(db_session)._execute_file_watcher(job({"content_match": {"text": "STATUS=COMPLETE"}}))
    assert result.status == TestStatus.PASSED
    assert result.mismatch_summary["matched_text"] == "STATUS=COMPLETE"


def test_execute_file_watcher_fails_on_timeout(db_session, monkeypatch):
    def _raise(*a, **k):
        raise FileWatchTimeout("no matching file found after 5 attempt(s) (max_tries=5)", tries=5, elapsed_seconds=150.0)

    monkeypatch.setattr("etl_framework.reconciliation.file_mapping.wait_for_watched_file", _raise)
    result = executor(db_session)._execute_file_watcher(job())
    assert result.status == TestStatus.FAILED
    assert result.mismatch_summary["tries"] == 5
    assert result.mismatch_summary["elapsed_seconds"] == 150.0
    assert result.source_file_name is None
    assert result.data_artifact_path is None


def test_execute_file_watcher_errors_on_unexpected_exception(db_session, monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("sftp host unreachable")

    monkeypatch.setattr("etl_framework.reconciliation.file_mapping.wait_for_watched_file", _raise)
    result = executor(db_session)._execute_file_watcher(job())
    assert result.status == TestStatus.ERROR
    assert result.mismatches[0].mismatch_type == "file_watcher_error"
    assert "sftp host unreachable" in result.mismatches[0].target_value


def test_execute_file_watcher_normalizes_scp_kind_to_sftp(db_session, monkeypatch):
    captured_specs = []

    def _fake_discover(self, spec):
        captured_specs.append(spec)
        return []

    monkeypatch.setattr("api.services.multi_file_remote.RemoteFileSourceSession.discover", _fake_discover)
    matched = DiscoveredFile(path="/inbound/a.csv", file_name="a.csv", tokens={})
    monkeypatch.setattr(
        "etl_framework.reconciliation.file_mapping.wait_for_watched_file",
        lambda discover, spec, **k: (discover(), _FakeWatchResult(matched))[1],
    )
    executor(
        db_session,
        config_snapshot={"file_source_credentials": {"scp_host": {"host": "h", "username": "u", "password": "p"}}},
    )._execute_file_watcher(job({
        "location": {"kind": "scp", "root": "/inbound", "pattern": "*.csv", "credentials_ref": "scp_host"},
    }))
    assert captured_specs[0].kind == "sftp"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_run_executor_file_watcher.py -v`
Expected: FAIL with `AttributeError: 'RunExecutor' object has no attribute '_execute_file_watcher'`

- [ ] **Step 3: Add the `_build_case` dispatch line**

In `api/services/run_executor.py`, insert a new branch into `_build_case` right after the `compare` branch (currently lines 549-550) and before the `bo_report` branch:

```python
        if job.job_type == "compare":
            return self._build_case_compare(job)
        if job.job_type == "file_watcher":
            return self._build_case_file_watcher(job)
        if job.job_type == "bo_report":
```

- [ ] **Step 4: Implement `_build_case_file_watcher`, `_file_watcher_result`, `_execute_file_watcher`**

In `api/services/run_executor.py`, insert these three methods right after `_execute_airflow_dag_run` ends (currently line 1551) and before the `# -- Freshness --` comment (currently line 1553):

```python
    def _build_case_file_watcher(self, job: JobDefinition):
        def run_file_watcher() -> ReconciliationResult:
            return self._execute_file_watcher(job)
        return run_file_watcher

    def _file_watcher_result(
        self,
        job: JobDefinition,
        status: TestStatus,
        tries: int,
        elapsed_seconds: float,
        executed_at: datetime,
        duration_seconds: float,
        matched_file=None,
        matched_text: str | None = None,
        error: str | None = None,
    ) -> ReconciliationResult:
        mismatch_summary: dict[str, Any] = {"tries": tries, "elapsed_seconds": elapsed_seconds}
        mismatches: list[MismatchRecord] = []
        if matched_text is not None:
            mismatch_summary["matched_text"] = matched_text
        if error is not None:
            mismatch_summary["error"] = error
            mismatches.append(MismatchRecord({"job": job.name}, "file_watcher", "matched", error, "file_watcher_error"))
        return ReconciliationResult(
            query_name=job.name,
            source_env=self._source_env,
            target_env=self._target_env,
            source_row_count=1 if matched_file else 0,
            target_row_count=1 if matched_file else 0,
            matched_count=1 if matched_file else 0,
            missing_in_target_count=0,
            missing_in_source_count=0,
            value_mismatch_count=len(mismatches),
            mismatches=mismatches,
            status=status,
            executed_at=executed_at,
            duration_seconds=duration_seconds,
            source_file_name=matched_file.file_name if matched_file else None,
            data_artifact_path=matched_file.path if matched_file else None,
            mismatch_summary=mismatch_summary,
        )

    def _execute_file_watcher(self, job: JobDefinition) -> ReconciliationResult:
        t0 = time.monotonic()
        executed_at = datetime.now(timezone.utc)
        from etl_framework.reconciliation.file_mapping import (
            FileSourceSpec, FileWatchTimeout, WatchSpec, wait_for_watched_file,
        )
        from api.services.multi_file_remote import RemoteFileSourceSession

        location = job.params.get("location") or {}
        kind = location.get("kind")
        file_spec = FileSourceSpec(
            kind="sftp" if kind == "scp" else kind,
            root=location.get("root", ""),
            pattern=location.get("pattern", ""),
            credentials_ref=location.get("credentials_ref"),
        )
        content_match = job.params.get("content_match") or {}
        watch_spec = WatchSpec(
            poll_interval_seconds=float(job.params.get("poll_interval_seconds", 30.0)),
            max_tries=job.params.get("max_tries"),
            window_start=job.params.get("window_start"),
            window_end=job.params.get("window_end"),
            content_text=content_match.get("text"),
            content_is_regex=bool(content_match.get("is_regex", False)),
        )
        try:
            with RemoteFileSourceSession(self._config_snapshot) as session:
                read_text = (lambda f: session.read_text(f, file_spec)) if watch_spec.content_text else None
                watch_result = wait_for_watched_file(
                    lambda: session.discover(file_spec), watch_spec, read_text=read_text,
                )
        except FileWatchTimeout as exc:
            return self._file_watcher_result(
                job, TestStatus.FAILED, tries=exc.tries, elapsed_seconds=exc.elapsed_seconds,
                executed_at=executed_at, duration_seconds=time.monotonic() - t0, error=str(exc),
            )
        except Exception as exc:
            return self._file_watcher_result(
                job, TestStatus.ERROR, tries=0, elapsed_seconds=0.0,
                executed_at=executed_at, duration_seconds=time.monotonic() - t0, error=str(exc),
            )
        return self._file_watcher_result(
            job, TestStatus.PASSED, tries=watch_result.tries, elapsed_seconds=watch_result.elapsed_seconds,
            executed_at=executed_at, duration_seconds=time.monotonic() - t0,
            matched_file=watch_result.file, matched_text=watch_result.matched_snippet,
        )
```

Note: `wait_for_watched_file` is called via the module attribute (`from etl_framework.reconciliation.file_mapping import ... wait_for_watched_file` inside the method body) specifically so tests can `monkeypatch.setattr("etl_framework.reconciliation.file_mapping.wait_for_watched_file", ...)` — this mirrors the existing local-import pattern already used for `RemoteFileSourceSession` in `_build_case_multi_file_reconciliation` (line 712-720).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_executor_file_watcher.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add api/services/run_executor.py tests/unit/test_run_executor_file_watcher.py
git commit -m "feat(file-watcher): dispatch and execute file_watcher jobs in RunExecutor"
```

---

### Task 6: Full-suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the full unit test suite**

Run: `python -m pytest tests/unit -v`
Expected: ALL PASS (no regressions in existing job-type dispatch, schema validation, or file-mapping tests)

- [ ] **Step 2: Confirm no stray debug code**

Run: `git diff master --stat`
Expected: only the five files touched across Tasks 1-5 plus their test files; no leftover `print`/`breakpoint` statements (spot-check with `git diff master -- api/services/run_executor.py etl_framework/reconciliation/file_mapping.py api/services/multi_file_remote.py api/schemas.py etl_framework/runner/job_validation.py`)

If step 1 or 2 turns up anything, fix it and re-run before considering the plan done. Nothing to commit here if both pass — Task 5's commit is the final one.
