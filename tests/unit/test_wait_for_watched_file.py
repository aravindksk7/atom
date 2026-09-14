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
