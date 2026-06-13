from __future__ import annotations
import json
import os
from datetime import datetime, timezone

from etl_framework.reconciliation.models import ReconciliationResult
from etl_framework.runner.state import TestStatus
from etl_framework.utils.logging import get_logger

logger = get_logger("reporting.metrics")


class MetricsWriter:
    def __init__(self, output_path: str) -> None:
        self._output_path = output_path

    def write(self, run_id: str, results: list[ReconciliationResult]) -> None:
        parent = os.path.dirname(self._output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        passed = sum(1 for r in results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in results if r.status == TestStatus.FAILED)
        slow = sum(1 for r in results if r.status == TestStatus.SLOW)
        total_duration = sum(r.duration_seconds for r in results)

        payload = {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_tests": len(results),
            "passed": passed,
            "failed": failed,
            "slow": slow,
            "total_duration_seconds": round(total_duration, 6),
            "tests": [
                {
                    "name": r.query_name,
                    "status": r.status if isinstance(r.status, str) else r.status.value,
                    "duration_seconds": r.duration_seconds,
                    "source_row_count": r.source_row_count,
                    "target_row_count": r.target_row_count,
                    "total_issues": r.total_issues,
                }
                for r in results
            ],
        }

        with open(self._output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        logger.info("Metrics written to %s (%d tests)", self._output_path, len(results))
