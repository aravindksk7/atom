from __future__ import annotations
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

from etl_framework.runner.state import TestCaseState, TestStatus
from etl_framework.utils.logging import get_logger
from etl_framework.utils.tracing import span as _span

logger = get_logger("runner.test_runner")


class TestRunner:
    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers if max_workers is not None else min(4, os.cpu_count() or 1)

    def run(self, cases: list[tuple[str, Callable]]) -> list[TestCaseState]:
        if not cases:
            return []
        results: list[TestCaseState] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._run_single, name, fn): name
                for name, fn in cases
            }
            for future in as_completed(futures):
                case_name = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    results.append(TestCaseState(
                        name=case_name,
                        test_type="reconciliation",
                        status=TestStatus.ERROR,
                        started_at=datetime.now(timezone.utc),
                        completed_at=datetime.now(timezone.utc),
                        error_message=str(exc),
                    ))
        return results

    def _run_single(self, name: str, fn: Callable) -> TestCaseState:
        with _span("test_runner.run_single", attributes={"test_name": name}):
            started_at = datetime.now(timezone.utc)
            try:
                result = fn()
                status = getattr(result, "status", TestStatus.PASSED)
                return TestCaseState(
                    name=name,
                    test_type="reconciliation",
                    status=status,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    result=result,
                )
            except Exception as exc:
                logger.exception("Test case %r raised an exception", name)
                return TestCaseState(
                    name=name,
                    test_type="reconciliation",
                    status=TestStatus.ERROR,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    error_message=str(exc),
                )
