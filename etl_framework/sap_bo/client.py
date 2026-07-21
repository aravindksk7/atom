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


def _unwrap_collection(data: dict, plural_key: str, singular_key: str, *fallback_keys: str) -> list:
    """Unwrap a biprws collection response.

    On-premises biprws wraps collections one level deeper than a flat
    {plural_key: [...]}: {plural_key: {singular_key: [...]}} (the collection
    is a plural container element whose only child is the singular element
    name, itself subject to the same single-item-collapses-to-bare-object
    quirk _as_list handles). Fall back to a flat list/bare object directly
    under plural_key (or fallback_keys) for shapes that don't nest this way.
    """
    container = data.get(plural_key)
    if container is None:
        for key in fallback_keys:
            container = data.get(key)
            if container is not None:
                break
    if isinstance(container, dict) and singular_key in container:
        container = container[singular_key]
    return _as_list(container)


def _dedupe_by_id(items: list[dict]) -> list[dict]:
    """Drop later entries sharing an already-seen non-empty id, keeping the
    first occurrence's order. Entries with no id are kept as-is — they can't
    be told apart this way, and the UI already tolerates missing ids."""
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        item_id = str(item.get("id", ""))
        if item_id:
            if item_id in seen:
                continue
            seen.add(item_id)
        deduped.append(item)
    return deduped


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
        self._owns_token = False
        self._session = requests.Session()
        self._verify_ssl = env_config.bo_verify_ssl
        proxy_url = env_config.bo_proxy_url.strip()
        if proxy_url:
            self._session.proxies.update({"http": proxy_url, "https": proxy_url})

    @property
    def logon_token(self) -> str | None:
        return self._token

    def use_logon_token(self, token: str, *, owns_token: bool = False) -> None:
        self._token = token
        self._owns_token = owns_token
        self._session.headers.update({"X-SAP-LogonToken": token})

    def authenticate(
        self,
        username: str | None = None,
        password: str | None = None,
        auth_type: str | None = None,
    ) -> str | None:
        url = f"{self._base_url}{self.LOGON_ENDPOINT}"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        payload = {
            "password": self._password if password is None else password,
            "clientType": "",
            "auth": self._auth_type if auth_type is None else auth_type,
            "userName": self._user if username is None else username
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
            self._owns_token = True
            self._session.headers.update({"X-SAP-LogonToken": self._token})
        return self._token

    def validate_session(self) -> None:
        if not self._token:
            self.authenticate()
        response = self._session.get(
            f"{self._base_url}/biprws/raylight/v1/documents",
            headers={"Accept": "application/json"},
            params={"page": 1, "pagesize": 1},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=None,
                http_status=response.status_code,
                response_body=response.text,
            )

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

    _PAGE_REQUEST_SIZE = 200
    _MAX_PAGES = 500

    def _paginate_biprws_collection(
        self,
        url: str,
        plural_key: str,
        singular_key: str,
        *fallback_keys: str,
        error_report_id: str | None = None,
        not_found_report_id: str | None = None,
    ) -> list[dict]:
        """Page through a biprws collection endpoint.

        biprws paginates these collections and the page size is
        admin-configured in CMC (observed defaulting to as few as 10). Some
        on-prem deployments silently clamp the response to that cap
        regardless of the `pagesize` we request, so a page shorter than
        what we *asked for* is not proof there's no more data — only an
        empty page, or a page shorter than the *previous* page, means the
        collection is exhausted.

        Some on-prem deployments go further and ignore the `page` param
        entirely, re-serving page 1's content forever. A batch whose ids
        exactly match the previous batch's ids is that case, not "more of
        the same page size" — stop instead of looping to `_MAX_PAGES`. As a
        second line of defense (e.g. overlapping-but-not-identical pages),
        entries are de-duplicated by id after paging finishes; entries
        without an id can't be told apart this way and are left as-is.
        """
        raw: list[dict] = []
        page = 1
        previous_batch_size: int | None = None
        previous_batch_ids: list[str] | None = None
        while page <= self._MAX_PAGES:
            response = self._session.get(
                url,
                headers={"Accept": "application/json"},
                params={"page": page, "pagesize": self._PAGE_REQUEST_SIZE},
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
            if not_found_report_id is not None and response.status_code == 404:
                raise ReportNotFoundError(report_id=not_found_report_id, env_name=self._base_url)
            if response.status_code >= 400:
                raise BOAPIError(
                    report_id=error_report_id,
                    http_status=response.status_code,
                    response_body=response.text,
                )
            batch = _unwrap_collection(response.json(), plural_key, singular_key, *fallback_keys)
            batch_ids = [str(item.get("id", "")) for item in batch]
            if batch and batch_ids == previous_batch_ids:
                break
            raw.extend(batch)
            if not batch or (previous_batch_size is not None and len(batch) < previous_batch_size):
                break
            previous_batch_size = len(batch)
            previous_batch_ids = batch_ids
            page += 1
        return _dedupe_by_id(raw)

    def list_documents(self) -> list[dict]:
        """GET /biprws/raylight/v1/documents — list all WebI documents."""
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}/biprws/raylight/v1/documents"
        raw = self._paginate_biprws_collection(url, "documents", "document", "entries")
        results = []
        for d in raw:
            doc_id = str(d.get("id", ""))
            if not doc_id:
                logger.warning("SAP BO document entry missing 'id' field, raw entry: %r", d)
            results.append({
                "id": doc_id,
                "name": d.get("name", ""),
                "folder": d.get("folder", d.get("parentFolderCUID", "")),
            })
        return results

    def list_reports(self, doc_id: str) -> list[dict]:
        """GET /biprws/raylight/v1/documents/{doc_id}/reports — list report tabs."""
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}/reports"
        raw = self._paginate_biprws_collection(
            url, "reports", "report",
            error_report_id=doc_id, not_found_report_id=doc_id,
        )
        results = []
        for r in raw:
            report_id = str(r.get("id", ""))
            if not report_id:
                logger.warning("SAP BO report entry missing 'id' field, raw entry: %r", r)
            results.append({
                "id": report_id,
                "name": r.get("name", ""),
                "reportIndex": r.get("reportIndex", 0),
            })
        return results

    _MIME_MAP: dict[str, str] = {
        "pdf":  "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv":  "text/csv",
    }

    def download_report(self, doc_id: str, report_id: str, format: str = "pdf") -> bytes:
        """GET …/documents/{doc_id}/reports/{report_id} — export as PDF/XLSX/CSV
        via the Accept header. There is no '/content' sub-resource; requesting
        one 404s on a real biprws server."""
        if not self._token:
            self.authenticate()
        accept = self._MIME_MAP.get(format, "application/pdf")
        url = f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}/reports/{report_id}"
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
        if self._token and self._owns_token:
            self._session.post(
                f"{self._base_url}/biprws/logoff",
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
        if self._token:
            self._session.headers.pop("X-SAP-LogonToken", None)
        self._token = None
        self._owns_token = False
