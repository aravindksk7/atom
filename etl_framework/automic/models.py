from dataclasses import dataclass
from datetime import datetime
from typing import Any

from etl_framework.runner.state import TestStatus

@dataclass
class JobStatus:
    identifier: str              # Run_ID or Job_Name
    identifier_type: str         # "run_id" | "job_name"
    status: TestStatus           # SUCCESS maps to PASSED, FAILED/NOT_FOUND -> FAILED, RUNNING -> RUNNING
    environment: str
    checked_at: datetime
    raw_response: dict[str, Any] # original API payload