from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from etl_framework.repository.repository import RunRepository

_COLLECTED_RE = re.compile(r"collected (\d+) items?")
_RESULT_RE = re.compile(r"\s+(PASSED|FAILED|ERROR)\s+\[")

_BATCH_SIZE = 5

_EXIT_STATUS = {
    0: "PASSED",
    1: "COMPLETED",
}


class PytestRunExecutor:
    def __init__(self, db: Session, run_id: str, pytest_args: list[str]) -> None:
        self._db = db
        self._run_id = run_id
        self._pytest_args = pytest_args
        self._run_repo = RunRepository(db)

    def execute(self) -> None:
        self._run_repo.update_run_status(
            self._run_id, "RUNNING", started_at=datetime.now(timezone.utc)
        )

        cmd = [
            sys.executable, "-m", "pytest",
            "--tb=short", "-v", "--no-header",
        ] + self._pytest_args

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        passed = failed = error = 0
        batch_count = 0

        for line in proc.stdout:
            collected = _COLLECTED_RE.search(line)
            if collected:
                self._run_repo.update_run_status(
                    self._run_id, "RUNNING",
                    total_tests=int(collected.group(1)),
                )
                continue

            match = _RESULT_RE.search(line)
            if match:
                outcome = match.group(1)
                if outcome == "PASSED":
                    passed += 1
                elif outcome == "FAILED":
                    failed += 1
                elif outcome == "ERROR":
                    error += 1

                batch_count += 1
                if batch_count >= _BATCH_SIZE:
                    self._run_repo.update_run_status(
                        self._run_id, "RUNNING",
                        passed=passed, failed=failed, error=error,
                    )
                    batch_count = 0

            if self._run_repo.is_cancel_requested(self._run_id):
                proc.terminate()
                self._run_repo.update_run_status(
                    self._run_id, "CANCELLED",
                    completed_at=datetime.now(timezone.utc),
                    passed=passed, failed=failed, error=error,
                )
                return

        exit_code = proc.wait()
        final_status = _EXIT_STATUS.get(exit_code, "ERROR")
        self._run_repo.update_run_status(
            self._run_id, final_status,
            completed_at=datetime.now(timezone.utc),
            passed=passed, failed=failed, error=error,
        )
