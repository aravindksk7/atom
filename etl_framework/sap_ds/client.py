"""Client for the SAP Data Services SOAP web service.

Verified against the live WSDL at
``{ds_url}?ver=2.0`` — service ``DataServices_Server``, target namespace
``http://www.businessobjects.com``. The relevant operations, their SOAPAction
values, and request/response element shapes (confirmed from that WSDL):

======================  ==========================  ============================================
Operation               SOAPAction                  Request -> Response
======================  ==========================  ============================================
Logon                   ``function=Logon``          LogonRequest{username,password,cms_system?,
                                                     cms_authentication} -> session{SessionID}
Ping                    ``function=Ping``           Ping_Input{} -> pingVersion
Logout                  ``function=Logout``         Logout_Input{}
Run_Batch_Job           ``jobAdmin=Run_Batch_Job``  RunBatchJobRequest{jobName,repoName?,...}
                                                     -> BatchJobResponse{pid,cid,rid,repoName,
                                                        returnCode?,errorMessage?}  (rid = run id)
Get_BatchJob_Status     ``jobAdmin=                  batchJobStatusRequest{runID,repoName}
                        Get_BatchJob_Status``        -> batchJobStatusResponse{returnCode,status}
======================  ==========================  ============================================

The authenticated ``SessionID`` is carried in the SOAP header on every call
after Logon (SAP DS's documented session mechanism), not in an HTTP header.

The class name ``DSRestClient`` is retained for import compatibility even
though the transport is SOAP, not REST.
"""
import html
import logging
import re
import time
from urllib.parse import urlparse

import requests

from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import DSAPIError
from etl_framework.runner.state import TestStatus

logger = logging.getLogger("etl_framework.sap_ds.client")

DS_NS = "http://www.businessobjects.com"
# The WSDL declares body elements in the ServerX schema namespace (the
# ``localtypes`` prefix). Kept for reference, but request bodies are sent
# UNQUALIFIED (no namespace): the live server (verified against qetl111)
# dispatches on the ``function=``/``jobAdmin=`` SOAPAction and rejects a
# namespace-qualified LogonRequest with the misleading fault "Web Services
# logon request requires Username node in the Logon request". Response
# elements come back in this namespace but are parsed by local name.
DS_TYPES_NS = "http://www.businessobjects.com/DataServices/ServerX.xsd"
SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"


def _xml_escape(value: str) -> str:
    """Escape a value for inclusion as XML element text."""
    return html.escape(str(value), quote=False)


def _first_tag_text(xml: str, local_name: str) -> str | None:
    """Return the text of the first element whose *local* name matches.

    Namespace-prefix agnostic: matches ``<SessionID>``, ``<ns:SessionID>``,
    or ``<x:SessionID xmlns:x=...>`` alike. Returns None when absent.
    """
    m = re.search(
        rf"<(?:[\w.-]+:)?{re.escape(local_name)}\b[^>]*>(.*?)</(?:[\w.-]+:)?{re.escape(local_name)}>",
        xml,
        re.S,
    )
    if m is None:
        return None
    return html.unescape(m.group(1).strip())


class DSRestClient:
    """SAP Data Services SOAP web service client.

    Public surface (unchanged across the REST->SOAP rewrite):
    ``login`` / ``logout`` / ``ping`` / ``trigger_job`` / ``get_job_status`` /
    ``wait_for_completion``.
    """

    # Status strings map to TestStatus. Keys are compared upper-cased.
    # The live qetl111 server returns lowercase "succeeded" (verified), so the
    # mapping must include it; "WARNING" is a DS terminal state that completed
    # with warnings (treated as passed).
    STATUS_MAP: dict[str, TestStatus] = {
        "COMPLETED": TestStatus.PASSED,
        "SUCCESS": TestStatus.PASSED,
        "SUCCEEDED": TestStatus.PASSED,
        "WARNING": TestStatus.PASSED,
        "ERROR": TestStatus.FAILED,
        "FAILED": TestStatus.FAILED,
        "CANCELLED": TestStatus.FAILED,
        "STOPPED": TestStatus.FAILED,
        "RUNNING": TestStatus.RUNNING,
        "PENDING": TestStatus.RUNNING,
        "QUEUED": TestStatus.RUNNING,
        "STARTED": TestStatus.RUNNING,
    }

    def __init__(self, env_config: EnvironmentConfig):
        self._base_url = env_config.ds_url.rstrip("/")
        if self._base_url and not urlparse(self._base_url).scheme:
            raise ValueError("SAP DS URL must include http:// or https://")
        self._user = env_config.ds_user
        self._password = env_config.ds_password
        self._default_repository = env_config.ds_repository
        self._auth_type = env_config.ds_auth_type
        self._cms_system = getattr(env_config, "ds_cms_system", "") or ""
        self._timeout = env_config.ds_timeout
        self._token: str | None = None
        self._owns_token = False
        self._session = requests.Session()
        self._verify_ssl = env_config.ds_verify_ssl
        proxy_url = env_config.ds_proxy_url.strip()
        if proxy_url:
            self._session.proxies.update({"http": proxy_url, "https": proxy_url})
        else:
            # No explicit DS proxy configured: don't let requests inherit
            # ambient HTTP(S)_PROXY env vars. On corporate hosts those point at
            # an outbound internet proxy (e.g. Zscaler) that can't route to an
            # internal SAP DS server. trust_env=False makes the client connect
            # directly. Set ds_proxy_url if a proxy really is required.
            self._session.trust_env = False

    @property
    def logon_token(self) -> str | None:
        return self._token

    # ------------------------------------------------------------------
    # SOAP transport
    # ------------------------------------------------------------------

    def _endpoint(self) -> str:
        """The SOAP endpoint URL. SAP DS's soap:address in the WSDL carries a
        ``?ver=2.0`` query; append it when the configured base URL doesn't
        already include a query string."""
        if "?" in self._base_url:
            return self._base_url
        return f"{self._base_url}?ver=2.0"

    def _envelope(self, body_inner: str, *, include_session: bool) -> str:
        header = ""
        if include_session and self._token:
            header = (
                "<soapenv:Header><session>"
                f"<SessionID>{_xml_escape(self._token)}</SessionID></session></soapenv:Header>"
            )
        else:
            header = "<soapenv:Header/>"
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}">'
            f"{header}<soapenv:Body>{body_inner}</soapenv:Body></soapenv:Envelope>"
        )

    def _call(
        self,
        soap_action: str,
        body_inner: str,
        *,
        job_name: str,
        include_session: bool = True,
    ) -> str:
        """POST one SOAP request, return the response body text, raising
        DSAPIError on HTTP error or a SOAP Fault."""
        envelope = self._envelope(body_inner, include_session=include_session)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
            "Accept": "text/xml",
        }
        response = self._session.post(
            self._endpoint(),
            data=envelope.encode("utf-8"),
            headers=headers,
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        text = response.text or ""
        fault = _first_tag_text(text, "faultstring")
        if fault is not None:
            raise DSAPIError(job_name=job_name, http_status=response.status_code, response_body=fault)
        if response.status_code >= 400:
            raise DSAPIError(job_name=job_name, http_status=response.status_code, response_body=text)
        return text

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def login(self, username: str | None = None, password: str | None = None) -> str | None:
        """Logon operation. Returns the SessionID and stores it for subsequent
        calls' SOAP header.

        Requirement (verified against qetl111's DataServices_Server): the
        Logon body MUST carry a ``cms_system`` naming a reachable CMS host
        (e.g. ``qetl111`` or ``qetl111:6400``). Omitting it — or sending it
        empty — makes the server reject the request with the *misleading*
        generic SOAP fault "Web Services logon request requires Username node
        in the Logon request" (an empty cms yields "The syntax for the
        specified CMS name is invalid"). Set it via the config's
        ``ds_cms_system``. The element names are lowercase
        (``username``/``password``/``cms_system``/``cms_authentication``) per
        the WSDL XSD; the server dispatches on the ``function=Logon``
        SOAPAction and does not require a specific body namespace; in fact the
        body must be sent UNQUALIFIED — a namespace-qualified LogonRequest is
        rejected with the same misleading "requires Username node" fault.
        """
        if not self._cms_system:
            raise DSAPIError(
                job_name="<login>", http_status=0,
                response_body=(
                    "SAP DS Logon requires a CMS system: set 'ds_cms_system' in "
                    "the config (e.g. the CMS host 'qetl111' or 'qetl111:6400')"
                ),
            )
        user = self._user if username is None else username
        pwd = self._password if password is None else password
        cms = f"<cms_system>{_xml_escape(self._cms_system)}</cms_system>" if self._cms_system else ""
        body = (
            "<LogonRequest>"
            f"<username>{_xml_escape(user)}</username>"
            f"<password>{_xml_escape(pwd)}</password>"
            f"{cms}"
            f"<cms_authentication>{_xml_escape(self._auth_type)}</cms_authentication>"
            "</LogonRequest>"
        )
        logger.debug("Logon to SAP DS SOAP web service")
        text = self._call("function=Logon", body, job_name="<login>", include_session=False)
        self._token = _first_tag_text(text, "SessionID")
        if not self._token:
            raise DSAPIError(
                job_name="<login>", http_status=200,
                response_body="Logon response missing SessionID",
            )
        self._owns_token = True
        return self._token

    def use_logon_token(self, token: str, *, owns_token: bool = False) -> None:
        self._token = token
        self._owns_token = owns_token

    def ping(self) -> bool:
        """Ping operation — cheap liveness check for a valid session. Returns
        True when the server answers with a pingVersion."""
        if not self._token:
            self.login()
        text = self._call("function=Ping", f'<Ping_Input/>', job_name="<ping>")
        return _first_tag_text(text, "pingVersion") is not None

    def logout(self) -> None:
        if self._token and self._owns_token:
            try:
                self._call(
                    "function=Logout", f'<Logout_Input/>', job_name="<logout>",
                )
            except Exception:  # noqa: BLE001 - logout must never mask the real result
                logger.debug("SAP DS logout failed; clearing local session anyway", exc_info=True)
        self._token = None
        self._owns_token = False

    # ------------------------------------------------------------------
    # Batch jobs
    # ------------------------------------------------------------------

    def _resolve_repo(self, repository: str | None) -> str:
        repo = repository or self._default_repository
        if not repo:
            raise ValueError(
                "ds_job requires a repository: set 'ds_repository' in the environment config "
                "or 'repository' in the job's params",
            )
        return repo

    def trigger_job(
        self, job_name: str, repository: str | None = None, job_params: dict | None = None,
    ) -> str:
        """Run_Batch_Job operation. Triggers the named batch job in the
        resolved repository and returns the run id (the ``rid`` from
        BatchJobResponse). ``job_params`` are passed as global variables."""
        if not self._token:
            self.login()
        repo = self._resolve_repo(repository)
        variables = ""
        if job_params:
            var_elems = "".join(
                f'<variable name="{_xml_escape(k)}">{_xml_escape(v)}</variable>'
                for k, v in job_params.items()
            )
            variables = f"<globalVariables>{var_elems}</globalVariables>"
        body = (
            f'<RunBatchJobRequest>'
            f"<jobName>{_xml_escape(job_name)}</jobName>"
            f"<repoName>{_xml_escape(repo)}</repoName>"
            f"{variables}"
            f"</RunBatchJobRequest>"
        )
        text = self._call("jobAdmin=Run_Batch_Job", body, job_name=job_name)
        return_code = _first_tag_text(text, "returnCode")
        if return_code not in (None, "", "0"):
            err = _first_tag_text(text, "errorMessage") or f"returnCode={return_code}"
            raise DSAPIError(job_name=job_name, http_status=200, response_body=err)
        rid = _first_tag_text(text, "rid")
        if not rid:
            raise DSAPIError(
                job_name=job_name, http_status=200,
                response_body="Run_Batch_Job response missing 'rid'",
            )
        return rid

    def _normalise_job_status(self, raw_status: str) -> TestStatus:
        mapped = self.STATUS_MAP.get(raw_status.upper())
        if mapped is None:
            logger.warning(
                "Unrecognized SAP DS job status %r, treating as still running", raw_status,
            )
            return TestStatus.RUNNING
        return mapped

    def get_job_status(self, run_id: str, repository: str | None = None) -> TestStatus:
        """Get_BatchJob_Status operation. Fetches the status of a run and maps
        it to TestStatus. Non-terminal DS states and any unrecognized status
        both map to RUNNING so callers keep polling."""
        if not self._token:
            self.login()
        repo = self._resolve_repo(repository)
        body = (
            f'<batchJobStatusRequest>'
            f"<runID>{_xml_escape(run_id)}</runID>"
            f"<repoName>{_xml_escape(repo)}</repoName>"
            f"</batchJobStatusRequest>"
        )
        text = self._call("jobAdmin=Get_BatchJob_Status", body, job_name=str(run_id))
        status = _first_tag_text(text, "status") or ""
        return self._normalise_job_status(status)

    def wait_for_completion(
        self, run_id: str, repository: str | None = None,
        timeout_s: float = 600, poll_interval_s: float = 5,
    ) -> TestStatus:
        """Poll get_job_status until terminal (PASSED/FAILED) or timeout_s
        elapses. Raises TimeoutError if the run never reaches a terminal
        status in time."""
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.get_job_status(run_id, repository=repository)
            if status != TestStatus.RUNNING:
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"SAP DS job run '{run_id}' did not complete within {timeout_s}s",
                )
            time.sleep(poll_interval_s)
