# tests/unit/test_wait_for_ready_files.py
from __future__ import annotations

import pytest

from etl_framework.reconciliation.file_mapping import (
    DiscoveredFile,
    ReadinessSpec,
    wait_for_ready_files,
)


def _df(name: str) -> DiscoveredFile:
    return DiscoveredFile(path=f"/x/{name}", file_name=name, tokens={})


def test_wait_for_ready_files_returns_immediately_when_already_satisfied() -> None:
    calls = []

    def discover():
        calls.append(1)
        return [_df("a.csv"), _df("b.csv")]

    sleeps = []
    result = wait_for_ready_files(
        discover, ReadinessSpec(expected_count=2, poll_interval_seconds=1, timeout_seconds=10),
        sleep=sleeps.append,
    )

    assert [f.file_name for f in result] == ["a.csv", "b.csv"]
    assert len(calls) == 1  # no polling needed
    assert sleeps == []  # never slept


def test_wait_for_ready_files_polls_until_expected_count_reached() -> None:
    responses = [
        [_df("a.csv")],
        [_df("a.csv"), _df("b.csv")],
        [_df("a.csv"), _df("b.csv"), _df("c.csv")],
    ]

    def discover():
        return responses.pop(0)

    sleeps = []
    result = wait_for_ready_files(
        discover, ReadinessSpec(expected_count=3, poll_interval_seconds=1, timeout_seconds=10),
        sleep=sleeps.append,
    )

    assert len(result) == 3
    assert sleeps == [1, 1]  # slept twice (after the 1st and 2nd insufficient discoveries)


def test_wait_for_ready_files_raises_timeout_error_when_never_satisfied() -> None:
    def discover():
        return [_df("a.csv")]

    elapsed = {"total": 0.0}

    def fake_sleep(seconds: float) -> None:
        elapsed["total"] += seconds

    with pytest.raises(TimeoutError, match="only 1 of 5 expected file"):
        wait_for_ready_files(
            discover, ReadinessSpec(expected_count=5, poll_interval_seconds=2, timeout_seconds=5),
            sleep=fake_sleep,
        )

    assert elapsed["total"] >= 5
