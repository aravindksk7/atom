import logging
from etl_framework.automic.client import AutomicClient
from etl_framework.automic.models import JobStatus
from etl_framework.config.models import EnvironmentConfig

logger = logging.getLogger("etl_framework.automic.jobs")

class AutomicJobRunner:
    def __init__(self, env_config: EnvironmentConfig) -> None:
        self._client = AutomicClient(env_config)

    def run_by_id(self, run_id: str) -> JobStatus:
        logger.info(f"Checking Automic job run_id: {run_id}")
        return self._client.get_status_by_run_id(run_id)

    def run_by_name(self, job_name: str) -> JobStatus:
        logger.info(f"Checking Automic job_name: {job_name}")
        return self._client.get_status_by_job_name(job_name)
