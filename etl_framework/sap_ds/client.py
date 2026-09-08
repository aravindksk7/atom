import logging
import re
import time
import uuid
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

    Live-server quirks found so far:
    - login endpoint is lowercase "/logon" (not "/Login"), and it 400s on a
      plain "Accept: application/json" -- needs a browser-style Accept
      header instead.
    - job triggering does NOT go through this REST-style login/token API at
      all. DevTools capture (2026-09-03) against the real Data Services
      Management Console shows it's a legacy servlet form POST:
      "POST /DataServices/servlet/AwBatchJobExecute" with
      "Content-Type: application/x-www-form-urlencoded", not JSON. See
      trigger_job's docstring for the still-unverified parts (CSRF/session
      handling, response shape) carried over from that capture.
    - TRIGGER_ENDPOINT is resolved against the server origin
      (scheme+host+port from ds_url), NOT ds_url's configured path.
      Confirmed live 2026-09-08: this on-prem instance's ds_url is
      "https://qetl111/DataServices/launch/" -- login is genuinely nested
      under that "/launch" path, but AwBatchJobExecute is not, it's a
      sibling servlet directly under "/DataServices/" at the origin.
      Concatenating ds_url + TRIGGER_ENDPOINT the way login does doubled
      "/DataServices" and pulled in the extra "/launch" segment, producing
      a 404 (SAP's generic "Missing Page" template, not an app-level
      "job not found" -- that distinction is what pointed at a routing
      bug rather than an auth/job-name one).

    Status checking (get_job_status/wait_for_completion) went through a
    complete rework on 2026-09-08 once real captures became available for
    it, replacing an original REST-style guess ({repository}/status/
    {run_id}) that turned out to have no basis in how this app actually
    works. See get_job_status's docstring for what's confirmed vs. still
    inferred in the new design.
    """

    LOGIN_ENDPOINT = "/logon"
    TRIGGER_ENDPOINT = "/DataServices/servlet/AwBatchJobExecute"
    HISTORY_ENDPOINT = "/DataServices/servlet/AwBatchJobHistory"
    SESSION_TOKEN_HEADER = "X-DS-SessionToken"

    # AwBatchJobHistory's status column is a colored icon
    # (../images/circ{color}.gif), not a text field. circgreen.gif ->
    # PASSED is directly confirmed by a live capture (2026-09-08).
    # circred.gif -> FAILED is a traffic-light-convention inference, not
    # yet directly observed. Any other/unknown icon (e.g. a presumed
    # in-progress one we haven't seen) falls back to RUNNING via
    # _parse_history_row_status's default, same "unrecognized -> keep
    # polling" philosophy as the rest of this client.
    ICON_STATUS_MAP: dict[str, TestStatus] = {
        "green": TestStatus.PASSED,
        "red": TestStatus.FAILED,
    }

    def __init__(self, env_config: EnvironmentConfig):
        self._base_url = env_config.ds_url.rstrip("/")
        if self._base_url and not urlparse(self._base_url).scheme:
            raise ValueError("SAP DS URL must include http:// or https://")
        self._user = env_config.ds_user
        self._password = env_config.ds_password
        self._default_repository = env_config.ds_repository
        self._auth_type = env_config.ds_auth_type
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
            "authType": self._auth_type,
        }
        logger.debug("Authenticating with SAP DS Administrator API")
        response = self._session.post(
            url,
            json=payload,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise DSAPIError(
                job_name="<login>", http_status=response.status_code, response_body=response.text,
                url=response.url,
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
        """POST form-encoded to the Management Console's AwBatchJobExecute
        servlet -- trigger a SAP DS batch job run in the given repository
        (falling back to the EnvironmentConfig's ds_repository if none is
        given). job_params is flattened directly into the form body as
        global-variable fields (e.g. {"$G_RUN_DATE": "2026-07-24"}),
        matching what the browser sends.

        Modeled directly on a live DevTools capture (2026-09-03) of a real
        "Execute Batch Job" submission, but still has unverified pieces:
        - X-CSRF-TOKEN: the captured request carried one, scraped by the
          browser from a page it had loaded first. We don't yet know how to
          obtain it headlessly, so this first cut omits it and expects a
          403/redirect-to-login response to confirm it's actually required.
        - JOB_SERVER: derived as "{host}:3500" from ds_url's host (3500 is
          SAP DS's default Job Server port, and matches the captured
          "QETL111:3500" for host "qetl111") -- not read from config.
        - Response is HTML, not JSON, and its success/run-id shape is
          unknown. The GUID this method generates and submits (the real
          request submits one as a correlation id) is returned as a
          working-hypothesis run id for get_job_status/wait_for_completion
          to poll with -- also still unverified, since STATUS_ENDPOINT
          hasn't been checked against a live response yet.

        Confirmed live 2026-09-08: unlike login (which is genuinely nested
        under ds_url's configured path, e.g. ".../DataServices/launch/"),
        this servlet lives at the server origin + TRIGGER_ENDPOINT
        regardless of whatever path ds_url carries -- concatenating onto
        self._base_url the way login does doubled "/DataServices" and
        pulled in an extra "/launch" segment that doesn't belong here,
        producing a 404. Built from urlparse(self._base_url)'s scheme+host
        instead, matching the captured browser request exactly.
        """
        if not self._token:
            self.login()
        repo = repository or self._default_repository
        if not repo:
            raise ValueError(
                "ds_job requires a repository: set 'ds_repository' in the environment config "
                "or 'repository' in the job's params",
            )
        guid = str(uuid.uuid4())
        parsed_base = urlparse(self._base_url)
        host = parsed_base.hostname or ""
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        form = {
            "SAMPLE_RATE": "5",
            "AUDIT_CONTROL": "",
            "USE_STAT": "",
            "JOB_SERVER": f"{host.upper()}:3500",
            "TRACE": "TRACE_SELECTED",
            "job_trace_session": "yes",
            "job_trace_workflow": "yes",
            "job_trace_dataflow": "yes",
            "default_ACTION_REQUEST": "none",
            "ACTION_REQUEST": "Execute",
            "REPOSITORY_NAME": repo,
            "JobName": job_name,
            "GUID": guid,
            "__MOVE_DIRECTION": "FORWARD",
            "ACTIVE_VIEW": "Execute Batch Job",
        }
        for key, value in (job_params or {}).items():
            form[key] = "" if value is None else str(value)
        url = f"{origin}{self.TRIGGER_ENDPOINT}"
        response = self._session.post(
            url,
            data=form,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise DSAPIError(
                job_name=job_name, http_status=response.status_code, response_body=response.text,
                url=response.url,
            )
        logger.info(
            "AwBatchJobExecute response for job %r (guid=%s), status=%s, url=%s: %s",
            job_name, guid, response.status_code, response.url, response.text[:2000],
        )
        return guid

    _HISTORY_ROW_RE = re.compile(r"<TR\s+class=tablerow\s*>(.*?)</TR>", re.IGNORECASE | re.DOTALL)
    _HISTORY_ROW_JOB_NAME_RE = re.compile(r'<TD\s+class="cell"\s+nowrap>([^<]+)</TD>')
    _HISTORY_ROW_ICON_RE = re.compile(r"circ(\w+?)\.gif", re.IGNORECASE)

    def _parse_history_row_status(self, row_html: str) -> TestStatus:
        icon_match = self._HISTORY_ROW_ICON_RE.search(row_html)
        icon = icon_match.group(1).lower() if icon_match else ""
        mapped = self.ICON_STATUS_MAP.get(icon)
        if mapped is None:
            logger.warning(
                "Unrecognized SAP DS history status icon %r, treating as still running", icon,
            )
            return TestStatus.RUNNING
        return mapped

    def get_job_status(
        self, job_name: str, run_id: str | None = None, repository: str | None = None,
    ) -> TestStatus:
        """Fetch the status of job_name's most recent execution and map it
        to TestStatus.

        Complete rework (2026-09-08) of an original REST-style guess that
        had no basis in how this app actually works. Modeled on live
        captures of the Management Console's own "Batch Job Status" /
        "Job Trace Log" pages:

        - GET /DataServices/servlet/AwBatchJobHistory (resolved against the
          server origin, like trigger_job) with GROUP_TIME_RADIO=
          LAST_EXECUTION_RADIO returns a listing filtered to just the LAST
          execution of the matching job(s) -- exactly what we want right
          after triggering a run. Confirmed live: the app's own "tab" links
          use plain GET with these exact query params (no CSRF, no session
          form fields needed), the same pattern already confirmed working
          for AwBatchJobLogs.
        - There is NO run-id-keyed status lookup in this app at all. Status
          is read off a colored icon (../images/circ{color}.gif) in the
          matching row of that listing -- see ICON_STATUS_MAP. run_id (the
          GUID trigger_job returns) is accepted here only for logging/
          correlation -- confirmed live that this GUID does get used
          server-side (it appears, dashes->underscores, in the triggered
          run's trace log filename), but the history listing itself is
          looked up by job_name, not run_id, since there's no endpoint that
          takes a run id directly.
        - Known race condition, unverified severity: if called immediately
          after trigger_job, before DS has registered the new run in
          history, this could return the PREVIOUS execution's status
          instead of the new one. Live evidence (a trace log timestamped
          the same second as the trigger) suggests DS registers the run at
          start, not completion, so the window should be small, but this
          hasn't been stress-tested.
        - Icon color mapping: circgreen.gif -> PASSED is directly confirmed
          live. circred.gif -> FAILED is inferred from traffic-light
          convention, not yet observed directly. No live example of an
          in-progress icon exists yet either -- any icon other than
          green/red, and the case of no matching history row yet, both
          fall back to RUNNING (keep polling) rather than guessing.

        OPEN QUESTION as of 2026-09-08: a live run of a job known to finish
        in ~4s (per its own trace log) instead timed out after the full
        600s here, meaning get_job_status never found a matching row the
        entire time. Suspect this GET-with-query-params approach may not
        actually apply the JobName/GROUP_TIME_RADIO filter the way the
        app's own "tab" link navigation does -- classic legacy-JSP-app
        behavior is for a bare GET to render a default/unfiltered view and
        only a real form POST to execute the search. Every call now logs a
        compact INFO line (row counts, matched-or-not, resolved status) and
        the full response body at DEBUG when nothing matches, specifically
        to get real evidence on this before guessing further (e.g. before
        switching this to a POST).
        """
        if not self._token:
            self.login()
        repo = repository or self._default_repository
        if not repo:
            raise ValueError(
                "ds_job requires a repository: set 'ds_repository' in the environment config "
                "or 'repository' in the job's params",
            )
        parsed_base = urlparse(self._base_url)
        origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
        params = {
            "REPOSITORY_NAME": repo,
            "JobName": job_name,
            "JOB": "All batch jobs",
            "GROUP_TIME_RADIO": "LAST_EXECUTION_RADIO",
            "DAYS_INTERVAL": "1",
            "FROM_DATE": "",
            "TO_DATE": "",
            "ACTIVE_VIEW": "Batch Job Status",
        }
        response = self._session.get(
            f"{origin}{self.HISTORY_ENDPOINT}",
            params=params,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise DSAPIError(
                job_name=job_name, http_status=response.status_code, response_body=response.text,
                url=response.url,
            )
        all_rows = self._HISTORY_ROW_RE.findall(response.text)
        matched_row = None
        for row_html in all_rows:
            name_match = self._HISTORY_ROW_JOB_NAME_RE.search(row_html)
            if name_match and name_match.group(1).strip() == job_name:
                matched_row = row_html
                break
        result = self._parse_history_row_status(matched_row) if matched_row is not None else TestStatus.RUNNING
        logger.info(
            "AwBatchJobHistory check for job %r (run_id=%s): url=%s status_code=%s "
            "total_rows=%d matched_row=%s -> %s",
            job_name, run_id, response.url, response.status_code,
            len(all_rows), matched_row is not None, result,
        )
        if matched_row is None:
            logger.debug("AwBatchJobHistory full response body for job %r: %s", job_name, response.text[:5000])
        return result

    def wait_for_completion(
        self, job_name: str, run_id: str | None = None, repository: str | None = None,
        timeout_s: float = 600, poll_interval_s: float = 5,
    ) -> TestStatus:
        """Poll get_job_status until it returns a terminal status
        (PASSED/FAILED) or timeout_s elapses. Raises TimeoutError if the run
        never reaches a terminal status in time -- callers treat that as a
        run error, not a job failure."""
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.get_job_status(job_name, run_id=run_id, repository=repository)
            if status != TestStatus.RUNNING:
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"SAP DS job '{job_name}' (run_id={run_id}) did not complete within {timeout_s}s",
                )
            time.sleep(poll_interval_s)
