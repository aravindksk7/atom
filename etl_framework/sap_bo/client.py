import logging
import requests
import pandas as pd
from urllib.parse import urlparse
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import BOAPIError, ReportNotFoundError

logger = logging.getLogger("etl_framework.sap_bo.client")


def _as_list(value) -> list:
    """SAP BO's biprws collapses a single-element JSON collection into a bare
    object instead of a one-element array. Normalize both shapes to a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


class BORestClient:
    LOGON_ENDPOINT = "/biprws/logon/long"
    REPORT_ENDPOINT = "/biprws/raylight/v1/documents/{doc_id}/reports"

    def __init__(self, env_config: EnvironmentConfig):
        self._base_url = env_config.bo_url.rstrip("/")
        if self._base_url and not urlparse(self._base_url).scheme:
            raise ValueError("SAP BO URL must include http:// or https://")
        self._user = env_config.bo_user
        self._password = env_config.bo_password
        self._auth_type = env_config.bo_auth_type
        self._timeout = env_config.bo_timeout
        self._token = None
        self._session = requests.Session()
        self._verify_ssl = env_config.bo_verify_ssl
        proxy_url = env_config.bo_proxy_url.strip()
        if proxy_url:
            self._session.proxies.update({"http": proxy_url, "https": proxy_url})

    def authenticate(self) -> None:
        url = f"{self._base_url}{self.LOGON_ENDPOINT}"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        payload = {
            "password": self._password,
            "clientType": "",
            "auth": self._auth_type,
            "userName": self._user
        }
        logger.debug("Authenticating with SAP BO REST API")
        response = self._session.post(
            url,
            json=payload,
            headers=headers,
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=None,
                http_status=response.status_code,
                response_body=response.text,
            )
        
        self._token = response.headers.get("X-SAP-LogonToken")
        if self._token:
            self._session.headers.update({"X-SAP-LogonToken": self._token})

    def fetch_report_data(self, report_id: str) -> pd.DataFrame:
        if not self._token:
            self.authenticate()
            
        url = self.REPORT_ENDPOINT.format(doc_id=report_id)
        full_url = f"{self._base_url}{url}"
        
        logger.debug(f"Fetching report data for: {report_id}")
        response = self._session.get(
            full_url,
            headers={"Accept": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        
        if response.status_code == 404:
            raise ReportNotFoundError(report_id=report_id, env_name=self._base_url)
        elif response.status_code >= 400:
            raise BOAPIError(report_id=report_id, http_status=response.status_code, response_body=response.text)
        
        data = response.json()
        return pd.DataFrame(_as_list(data.get("dataset", data.get("reports", data))))

    def list_documents(self) -> list[dict]:
        """GET /biprws/raylight/v1/documents — list all WebI documents."""
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}/biprws/raylight/v1/documents"
        response = self._session.get(
            url,
            headers={"Accept": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=None,
                http_status=response.status_code,
                response_body=response.text,
            )
        data = response.json()
        raw = _as_list(data.get("documents", data.get("entries", [])))
        return [
            {
                "id": str(d.get("id", "")),
                "name": d.get("name", ""),
                "folder": d.get("folder", d.get("parentFolderCUID", "")),
            }
            for d in raw
        ]

    def list_reports(self, doc_id: str) -> list[dict]:
        """GET /biprws/raylight/v1/documents/{doc_id}/reports — list report tabs."""
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}/reports"
        response = self._session.get(
            url,
            headers={"Accept": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code == 404:
            raise ReportNotFoundError(report_id=doc_id, env_name=self._base_url)
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=doc_id,
                http_status=response.status_code,
                response_body=response.text,
            )
        data = response.json()
        return [
            {
                "id": str(r.get("id", "")),
                "name": r.get("name", ""),
                "reportIndex": r.get("reportIndex", 0),
            }
            for r in _as_list(data.get("reports", []))
        ]

    _MIME_MAP: dict[str, str] = {
        "pdf":  "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv":  "text/csv",
    }

    def download_report(self, doc_id: str, report_id: str, format: str = "pdf") -> bytes:
        """GET …/documents/{doc_id}/reports/{report_id}/content — download as PDF/XLSX/CSV."""
        if not self._token:
            self.authenticate()
        accept = self._MIME_MAP.get(format, "application/pdf")
        url = (
            f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}"
            f"/reports/{report_id}/content"
        )
        response = self._session.get(
            url,
            headers={"Accept": accept},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=report_id,
                http_status=response.status_code,
                response_body=response.text,
            )
        return response.content

    def logout(self) -> None:
        if self._token:
            self._session.post(
                f"{self._base_url}/biprws/logoff",
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
            self._session.headers.pop("X-SAP-LogonToken", None)
            self._token = None
