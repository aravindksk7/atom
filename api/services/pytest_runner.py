from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

from sqlalchemy.orm import Session

from etl_framework.repository.models import TestResult
from etl_framework.repository.repository import RunRepository

_COLLECTED_RE = re.compile(r"collected (\d+) items?")
_RESULT_RE = re.compile(r"\s+(PASSED|FAILED|ERROR)\s+\[")

_BATCH_SIZE = 5

_EXIT_STATUS = {
    0: "PASSED",
    1: "COMPLETED",
}


@dataclass(frozen=True)
class _PytestCaseResult:
    name: str
    status: str
    duration_seconds: float
    error_message: str | None = None


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

        with tempfile.TemporaryDirectory(prefix=f"atom-pytest-{self._run_id}-") as tmpdir:
            junit_path = Path(tmpdir) / "junit.xml"
            cmd = [
                sys.executable, "-m", "pytest",
                "--tb=short", "-v", "--no-header",
            ] + self._pytest_args + [f"--junitxml={junit_path}"]

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
            case_results = self._parse_junit_results(junit_path)
            if case_results:
                self._persist_case_results(case_results)
                passed, failed, error = self._counts_from_cases(case_results)

            final_status = self._final_status(exit_code, failed=failed, error=error)
            update_fields = {
                "completed_at": datetime.now(timezone.utc),
                "passed": passed,
                "failed": failed,
                "error": error,
            }
            if case_results:
                update_fields["total_tests"] = len(case_results)
            self._run_repo.update_run_status(self._run_id, final_status, **update_fields)

    def _parse_junit_results(self, path: Path) -> list[_PytestCaseResult]:
        if not path.exists():
            return []
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError:
            return []

        results: list[_PytestCaseResult] = []
        for case in root.iter():
            if _local_name(case.tag) != "testcase":
                continue
            name = _case_name(case)
            duration = _float_attr(case, "time")
            status = "PASSED"
            error_message = None
            for child in list(case):
                child_name = _local_name(child.tag)
                if child_name == "failure":
                    status = "FAILED"
                    error_message = _message(child)
                    break
                if child_name == "error":
                    status = "ERROR"
                    error_message = _message(child)
                    break
                if child_name == "skipped":
                    status = "SKIPPED"
                    error_message = _message(child)
            results.append(_PytestCaseResult(
                name=name,
                status=status,
                duration_seconds=duration,
                error_message=error_message,
            ))
        return results

    def _persist_case_results(self, results: list[_PytestCaseResult]) -> None:
        executed_at = datetime.now(timezone.utc)
        for result in results:
            self._db.add(TestResult(
                run_id=self._run_id,
                query_name=result.name,
                status=result.status,
                duration_seconds=result.duration_seconds,
                source_row_count=0,
                target_row_count=0,
                value_mismatch_count=1 if result.status in {"FAILED", "ERROR"} else 0,
                missing_in_target_count=0,
                missing_in_source_count=0,
                error_message=result.error_message,
                executed_at=executed_at,
            ))
        self._db.commit()

    def _counts_from_cases(self, results: list[_PytestCaseResult]) -> tuple[int, int, int]:
        passed = sum(1 for result in results if result.status in {"PASSED", "SKIPPED"})
        failed = sum(1 for result in results if result.status == "FAILED")
        error = sum(1 for result in results if result.status == "ERROR")
        return passed, failed, error

    def _final_status(self, exit_code: int, failed: int, error: int) -> str:
        if error:
            return "ERROR"
        if failed:
            return "FAILED"
        return _EXIT_STATUS.get(exit_code, "ERROR")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _float_attr(element: ElementTree.Element, name: str) -> float:
    try:
        return float(element.attrib.get(name, 0) or 0)
    except ValueError:
        return 0.0


def _case_name(case: ElementTree.Element) -> str:
    classname = case.attrib.get("classname", "").strip()
    name = case.attrib.get("name", "").strip()
    full_name = f"{classname}::{name}" if classname else name
    if len(full_name) <= 255:
        return full_name
    digest = hashlib.sha1(full_name.encode("utf-8")).hexdigest()[:10]
    return f"{full_name[:244]}#{digest}"


def _message(element: ElementTree.Element) -> str | None:
    message = element.attrib.get("message")
    text = (element.text or "").strip()
    if message and text:
        return f"{message}\n{text}"
    return message or text or None
