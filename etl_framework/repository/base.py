from __future__ import annotations
from abc import ABC, abstractmethod
from etl_framework.repository.models import TestRun


class AbstractTestRunRepository(ABC):
    @abstractmethod
    def get_run(self, run_id: str) -> TestRun | None: ...
