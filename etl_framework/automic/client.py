import logging
import requests
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from etl_framework.config.models import EnvironmentConfig
from etl_framework.automic.models import JobStatus
from etl_framework.runner.state import TestStatus
from etl_framework.exceptions import AutomicAPIError, AutomicTimeoutError

logger = logging.getLogger("etl_framework.automic.client")

class AutomicClient:
    STATUS_MAP = {
        "ENDED_OK": TestStatus.PASSED,
        "ENDED_NOT_OK": TestStatus.FAILED,
        "ACTIVE": TestStatus.RUNNING,
        "WAITING": TestStatus.RUNNING,
    }

    def __init__(self, env_config: EnvironmentConfig):
        self._base_url = env_config.automic_url.rstrip("/")
        self._env_name = env_config.name
        self._session = requests.Session()
        self._session.headers.update(self._build_auth_header(env_config))
        self._timeout = env_config.automic_timeout
        self._max_retries = env_config.automic_max_retries

        # Dynamically set up the retry policy
        self._request_with_retry = retry(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            retry=retry_if_exception_type(requests.RequestException),
            reraise=True
        )(self._request_raw)

    def _build_auth_header(self, env_config: EnvironmentConfig) -> dict[str, str]:
        import base64
        credentials = base64.b64encode(f"{env_config.automic_user}:{env_config.automic_password}".encode()).decode()
        return {"Authorization": f"Basic {credentials}"}

    def _request_raw(self, method: str, url: str) -> dict:
        logger.debug(f"Automic API Request: {method} {url}")
        response = self._session.request(method, url, timeout=self._timeout)
        if response.status_code >= 400:
            raise AutomicAPIError(
                http_status=response.status_code,
                response_body=response.text,
                url=url
            )
        return response.json()

    def _request(self, method: str, url: str) -> dict:
        try:
            return self._request_with_retry(method, url)
        except requests.RequestException as e:
            raise AutomicTimeoutError(
                url=url,
                attempts=self._max_retries,
                timeout_seconds=self._timeout
            ) from e

    def _normalise_status(self, raw_status: str) -> TestStatus:
        return self.STATUS_MAP.get(raw_status.upper(), TestStatus.FAILED)

    def get_status_by_run_id(self, run_id: str) -> JobStatus:
        url = f"{self._base_url}/api/v1/executions/{run_id}"
        data = self._request("GET", url)
        status = self._normalise_status(data.get("status", "NOT_FOUND"))
        return JobStatus(
            identifier=run_id, identifier_type="run_id",
            status=status, environment=self._env_name,
            checked_at=datetime.now(), raw_response=data
        )

    def get_status_by_job_name(self, job_name: str) -> JobStatus:
        url = f"{self._base_url}/api/v1/jobs/{job_name}/executions?limit=1&sort=start_time:desc"
        data = self._request("GET", url)
        executions = data.get("data", [])
        
        if not executions:
            return JobStatus(identifier=job_name, identifier_type="job_name", status=TestStatus.FAILED, environment=self._env_name, checked_at=datetime.now(), raw_response=data)

        latest = executions[0]
        status = self._normalise_status(latest.get("status", "NOT_FOUND"))
        return JobStatus(identifier=job_name, identifier_type="job_name", status=status, environment=self._env_name, checked_at=datetime.now(), raw_response=latest)

    def get_statuses(self, identifiers: list[str], id_type: str = "run_id") -> dict[str, JobStatus]:
        return {ident: (self.get_status_by_run_id(ident) if id_type == "run_id" else self.get_status_by_job_name(ident)) for ident in identifiers}

    def search_jobs(self, filter: str) -> list[dict]:
        url = f"{self._base_url}/api/v1/jobs?filter={filter}&limit=100"
        data = self._request("GET", url)
        return data.get("data", [])