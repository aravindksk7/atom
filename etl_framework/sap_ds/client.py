import logging
import time
import requests
from urllib.parse import urlparse
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import DSAPIError
from etl_framework.runner.state import TestStatus

logger = logging.getLogger("etl_framework.sap_ds.client")


class DSRestClient:
    """Client for SAP Data Services' Administrator/Management Console API.

    Endpoint paths, the session-token header name, and request/response
    payload shapes are best-effort, modeled after commonly documented SAP DS
    Administrator conventions -- not verified against a live SAP DS
    instance. Verify and adjust while integrating against a real server, the
    same way etl_framework/sap_bo/client.py's on-premises biprws quirks
    (_unwrap_collection, _paginate_biprws_collection) were discovered and
    documented over time rather than assumed correct up front.
    """

    LOGIN_ENDPOINT = "/Login"
    TRIGGER_ENDPOINT = "/BatchJob/{repository}/{job_name}/Execute"
    STATUS_ENDPOINT = "/BatchJob/{repository}/status/{run_id}"
    SESSION_TOKEN_HEADER = "X-DS-SessionToken"

    STATUS_MAP: dict[str, TestStatus] = {
        "COMPLETED": TestStatus.PASSED,
        "SUCCESS": TestStatus.PASSED,
        "ERROR": TestStatus.FAILED,
        "FAILED": TestStatus.FAILED,
        "CANCELLED": TestStatus.FAILED,
        "RUNNING": TestStatus.RUNNING,
        "PENDING": TestStatus.RUNNING,
        "QUEUED": TestStatus.RUNNING,
    }

    def __init__(self, env_config: EnvironmentConfig):
        self._base_url = env_config.ds_url.rstrip("/")
        if self._base_url and not urlparse(self._base_url).scheme:
            raise ValueError("SAP DS URL must include http:// or https://")
        self._user = env_config.ds_user
        self._password = env_config.ds_password
        self._default_repository = env_config.ds_repository
        self._timeout = env_config.ds_timeout
        self._token: str | None = None
        self._owns_token = False
        self._session = requests.Session()
        self._verify_ssl = env_config.ds_verify_ssl
        proxy_url = env_config.ds_proxy_url.strip()
        if proxy_url:
            self._session.proxies.update({"http": proxy_url, "https": proxy_url})

    def login(self, username: str | None = None, password: str | None = None) -> str | None:
        url = f"{self._base_url}{self.LOGIN_ENDPOINT}"
        payload = {
            "userName": self._user if username is None else username,
            "password": self._password if password is None else password,
        }
        logger.debug("Authenticating with SAP DS Administrator API")
        response = self._session.post(
            url,
            json=payload,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise DSAPIError(
                job_name="<login>", http_status=response.status_code, response_body=response.text,
            )
        self._token = response.headers.get(self.SESSION_TOKEN_HEADER)
        if self._token:
            self._owns_token = True
            self._session.headers.update({self.SESSION_TOKEN_HEADER: self._token})
        return self._token

    def logout(self) -> None:
        if self._token and self._owns_token:
            self._session.post(
                f"{self._base_url}/Logout",
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
        if self._token:
            self._session.headers.pop(self.SESSION_TOKEN_HEADER, None)
        self._token = None
        self._owns_token = False

    def trigger_job(
        self, job_name: str, repository: str | None = None, job_params: dict | None = None,
    ) -> str:
        """POST {repository}/{job_name}/Execute -- trigger a SAP DS batch job
        run in the given repository (falling back to the EnvironmentConfig's
        ds_repository if none is given). job_params is passed through as the
        JSON body for job substitution/global variables. Returns the new run
        id.

        Response shape is best-effort, assumes {"id": "<run_id>"}, matching
        the convention BORestClient.schedule_object already uses.
        """
        if not self._token:
            self.login()
        repo = repository or self._default_repository
        if not repo:
            raise ValueError(
                "ds_job requires a repository: set 'ds_repository' in the environment config "
                "or 'repository' in the job's params",
            )
        url = f"{self._base_url}{self.TRIGGER_ENDPOINT.format(repository=repo, job_name=job_name)}"
        response = self._session.post(
            url,
            json=job_params or {},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise DSAPIError(
                job_name=job_name, http_status=response.status_code, response_body=response.text,
            )
        run_id = str(response.json().get("id", ""))
        if not run_id:
            raise DSAPIError(
                job_name=job_name, http_status=response.status_code,
                response_body="trigger response missing 'id'",
            )
        return run_id
