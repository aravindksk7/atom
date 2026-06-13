from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TestStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


@dataclass
class TestCaseState:
    name: str
    test_type: str
    status: TestStatus = TestStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    result: Any = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
