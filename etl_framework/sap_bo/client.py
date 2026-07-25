import logging
import time
from datetime import date, timedelta
import requests
import pandas as pd
from urllib.parse import urlparse
from etl_framework.config.models import EnvironmentConfig
from etl_framework.exceptions import BOAPIError, ReportNotFoundError
from etl_framework.runner.state import TestStatus

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
    SCHEDULE_ENDPOINT = "/biprws/infostore/{object_id}/schedules"
    INSTANCE_ENDPOINT = "/biprws/infostore/{instance_id}"

    STATUS_MAP: dict[str, TestStatus] = {
        "SUCCESS": TestStatus.PASSED,
        "FAILED": TestStatus.FAILED,
        "RUNNING": TestStatus.RUNNING,
        "PENDING": TestStatus.RUNNING,
        "RECURRING": TestStatus.RUNNING,
        "PAUSED": TestStatus.RUNNING,
    }

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
    ) -> tuple[list[dict], bool]:
        """Page through a biprws collection endpoint.

        Returns `(items, stuck)`. `stuck` is True only when the page-param
        repeat quirk fired *and* the Range-header retry also came back empty/
        repeated -- i.e. both known pagination mechanisms are confirmed
        defeated (by an intermediary cache/gateway, or a server that just
        doesn't support either past its admin-configured page cap). Callers
        that have a non-paginated last resort (list_documents' CMS query) use
        this to decide whether it's worth trying; callers without one
        (list_reports) can ignore it.

        biprws paginates these collections and the page size is
        admin-configured in CMC (observed defaulting to as few as 10). Some
        on-prem deployments silently clamp the response to that cap
        regardless of the `pagesize` we request, so a page shorter than
        what we *asked for* is not proof there's no more data — only an
        empty page, or a page shorter than the *previous* page, means the
        collection is exhausted.

        Some on-prem deployments go further and ignore the `page` param
        entirely, re-serving page 1's content forever (often a reverse
        proxy/gateway in front of the BOE server caching or mishandling the
        query string). A batch whose ids exactly match the previous batch's
        ids is that case, not "more of the same page size" — instead of
        looping to `_MAX_PAGES`, switch to `Range`-header pagination (see
        `_paginate_biprws_range_continuation`), SAP's other documented
        pagination mechanism for these endpoints, which a query-string-blind
        gateway is less likely to also break. As a second line of defense
        (e.g. overlapping-but-not-identical pages), entries are de-duplicated
        by id after paging finishes; entries without an id can't be told
        apart this way and are left as-is.
        """
        raw: list[dict] = []
        page = 1
        previous_batch_size: int | None = None
        previous_batch_ids: list[str] | None = None
        stuck = False
        while page <= self._MAX_PAGES:
            response = self._session.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-cache, no-store",
                    "Pragma": "no-cache",
                },
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
                logger.warning(
                    "SAP BO pagination stopped at page %d for %s: server re-served an identical "
                    "%d-item batch (likely ignores the `page` param on this deployment) -- "
                    "retrying via Range-header pagination",
                    page, url, len(batch),
                )
                extra = self._paginate_biprws_range_continuation(
                    url, plural_key, singular_key, *fallback_keys,
                    start_offset=len(raw), seed_ids=batch_ids,
                )
                if extra:
                    logger.warning(
                        "SAP BO Range-header pagination recovered %d additional item(s) for %s "
                        "past the page-param repeat", len(extra), url,
                    )
                    raw.extend(extra)
                else:
                    stuck = True
                break
            raw.extend(batch)
            if not batch or (previous_batch_size is not None and len(batch) < previous_batch_size):
                logger.debug(
                    "SAP BO pagination stopped at page %d for %s: batch size %d (previous %s), total so far %d",
                    page, url, len(batch), previous_batch_size, len(raw),
                )
                break
            previous_batch_size = len(batch)
            previous_batch_ids = batch_ids
            page += 1
        result = _dedupe_by_id(raw)
        logger.debug("SAP BO pagination for %s: %d page(s), %d item(s) after dedupe", url, page, len(result))
        return result, stuck

    def _paginate_biprws_range_continuation(
        self,
        url: str,
        plural_key: str,
        singular_key: str,
        *fallback_keys: str,
        start_offset: int,
        seed_ids: list[str],
    ) -> list[dict]:
        """Continue past a page a biprws deployment re-serves for every
        `page` value by switching to the `Range: elements=N-M` header --
        SAP's documented alternative pagination mechanism for these
        collection endpoints. Some on-prem gateways/proxies key their
        (possibly query-string-blind) caching, or a broken `page` handler,
        off the query string specifically; a header-based request can
        succeed where `page`/`pagesize` silently keeps returning the same
        content.

        Seeding `previous_batch_ids` with the caller's already-known-repeated
        `seed_ids` (rather than starting from None) means a deployment that
        ignores Range too is detected after a single extra probe request,
        instead of needing two Range calls to notice the repeat itself.

        Soft-fails (returns whatever was collected, possibly empty) rather
        than raising: if Range isn't supported either, the caller already has
        real data from the page-param pass and should keep it rather than
        erroring out the whole browse.
        """
        collected: list[dict] = []
        start = start_offset
        previous_batch_ids = seed_ids
        page_count = 0
        while page_count < self._MAX_PAGES:
            end = start + self._PAGE_REQUEST_SIZE - 1
            response = self._session.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Range": f"elements={start}-{end}",
                    "Cache-Control": "no-cache, no-store",
                    "Pragma": "no-cache",
                },
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
            if response.status_code >= 400:
                logger.warning(
                    "SAP BO Range-header pagination unsupported for %s (HTTP %d) -- "
                    "keeping only the page-param data collected so far",
                    url, response.status_code,
                )
                break
            batch = _unwrap_collection(response.json(), plural_key, singular_key, *fallback_keys)
            batch_ids = [str(item.get("id", "")) for item in batch]
            if not batch or batch_ids == previous_batch_ids:
                logger.warning(
                    "SAP BO Range-header pagination for %s did not return new data past offset "
                    "%d (got %s item(s), %s) -- the deployment likely ignores Range too (e.g. an "
                    "intermediary cache/gateway keying purely on URL path), or genuinely has no "
                    "more data; keeping only the page-param data collected so far",
                    url, start, len(batch), "identical to the previous batch" if batch else "empty",
                )
                break
            collected.extend(batch)
            previous_batch_ids = batch_ids
            start += len(batch)
            page_count += 1
        return collected

    CMS_QUERY_ENDPOINT = "/biprws/v1/cmsquery"

    def _list_documents_via_cms_query(self) -> list[dict] | None:
        """Non-paginated-transport last resort for listing WebI documents:
        `POST /biprws/v1/cmsquery` CeQL queries, keyset-paginated via
        `TOP N ... WHERE SI_ID > :last_seen_id` rather than a page number or
        Range header -- nothing for an intermediary cache/gateway to defeat,
        since proxies don't cache POST bodies and each request's body is
        different by construction (see `_paginate_biprws_collection`'s
        docstring for the live failure this whole CMS-query path works
        around: page param AND Range header AND no-cache headers all
        re-served the same 10 items).

        Live evidence this keyset approach itself was necessary: the CMS
        query endpoint pages too -- an initial version issuing one
        unbounded query got exactly one batch back (matching CeQL's
        server-side default result cap when no TOP/keyset bound is given),
        not the real (much larger) total.

        Returns None (not an empty list) when the *first* query fails --
        the endpoint itself isn't available/supported (older BOE version,
        disabled at the CMC level, etc.) -- so the caller can tell
        "unsupported" apart from "zero documents" and fall back to whatever
        the paginated attempt collected. A failure on a *later* page keeps
        whatever was already collected instead: the endpoint clearly works,
        so that's real (if incomplete) data worth keeping.

        No equivalent exists for `list_reports`: WebI report tabs are part
        of a document's internal structure, not CI_INFOOBJECTS rows
        queryable via CeQL.
        """
        url = f"{self._base_url}{self.CMS_QUERY_ENDPOINT}"
        collected: list[dict] = []
        last_id: int | None = None
        page_count = 0
        while page_count < self._MAX_PAGES:
            where = "SI_KIND='Webi'" if last_id is None else f"SI_KIND='Webi' AND SI_ID > {last_id}"
            query = (
                f"SELECT TOP {self._PAGE_REQUEST_SIZE} SI_ID, SI_NAME, SI_ANCESTOR "
                f"FROM CI_INFOOBJECTS WHERE {where} ORDER BY SI_ID"
            )
            response = self._session.post(
                url,
                json={"query": query},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache, no-store",
                    "Pragma": "no-cache",
                },
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
            if response.status_code >= 400:
                if page_count == 0:
                    logger.warning(
                        "SAP BO CMS query endpoint unavailable for document listing (HTTP %d) -- "
                        "keeping only the paginated /raylight/v1/documents result",
                        response.status_code,
                    )
                    return None
                logger.warning(
                    "SAP BO CMS query failed past SI_ID %d (HTTP %d) -- keeping the %d "
                    "document(s) already collected via CMS query",
                    last_id, response.status_code, len(collected),
                )
                break
            batch = _unwrap_collection(response.json(), "documents", "document", "entries")
            if not batch:
                break
            collected.extend(batch)
            try:
                last_id = max(int(d.get("SI_ID", 0)) for d in batch)
            except (TypeError, ValueError):
                logger.warning(
                    "SAP BO CMS query returned a non-numeric SI_ID -- stopping keyset "
                    "pagination with the %d document(s) collected so far", len(collected),
                )
                break
            if len(batch) < self._PAGE_REQUEST_SIZE:
                break
            page_count += 1
        results = []
        for d in collected:
            doc_id = str(d.get("SI_ID", d.get("id", "")))
            if not doc_id:
                logger.warning("SAP BO CMS query document entry missing SI_ID/id, raw entry: %r", d)
            results.append({
                "id": doc_id,
                "name": d.get("SI_NAME", d.get("name", "")),
                "folder": str(d.get("SI_ANCESTOR", d.get("folder", ""))),
            })
        return results

    def list_document_ids_with_runs_on(self, day: date) -> list[str] | None:
        """Keyset-paginated CeQL query for the distinct set of WebI document
        ids that had at least one run ("instance") on `day`. Powers the
        Adapters tab's run-date filter (see the 2026-07-25 design spec).

        An instance's `SI_PARENTID` is the CMS id of the document it belongs
        to (an instance's parent in the CMS hierarchy is the report that
        owns it). Collects distinct `SI_PARENTID` values across all pages,
        keeping first-seen order.

        Returns None (not an empty list) when the *first* query fails --
        same "unsupported vs. zero results" distinction as
        `_list_documents_via_cms_query` -- so the caller can tell the two
        apart. A failure on a later page keeps whatever distinct ids were
        already collected instead of discarding them.
        """
        url = f"{self._base_url}{self.CMS_QUERY_ENDPOINT}"
        day_start = f"@{day.year}.{day.month:02d}.{day.day:02d}.00.00.00"
        next_day = day + timedelta(days=1)
        day_end = f"@{next_day.year}.{next_day.month:02d}.{next_day.day:02d}.00.00.00"
        date_clause = f"SI_INSTANCE=1 AND SI_STARTTIME >= {day_start} AND SI_STARTTIME < {day_end}"

        document_ids: list[str] = []
        seen: set[str] = set()
        last_id: int | None = None
        page_count = 0
        while page_count < self._MAX_PAGES:
            where = date_clause if last_id is None else f"{date_clause} AND SI_ID > {last_id}"
            query = (
                f"SELECT TOP {self._PAGE_REQUEST_SIZE} SI_ID, SI_PARENTID "
                f"FROM CI_INFOOBJECTS WHERE {where} ORDER BY SI_ID"
            )
            response = self._session.post(
                url,
                json={"query": query},
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Cache-Control": "no-cache, no-store",
                    "Pragma": "no-cache",
                },
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
            if response.status_code >= 400:
                if page_count == 0:
                    logger.warning(
                        "SAP BO CMS query endpoint unavailable for run-date filtering (HTTP %d)",
                        response.status_code,
                    )
                    return None
                logger.warning(
                    "SAP BO CMS query for run-date filtering failed past SI_ID %d (HTTP %d) -- "
                    "keeping the %d document id(s) already collected",
                    last_id, response.status_code, len(document_ids),
                )
                break
            batch = _unwrap_collection(response.json(), "documents", "document", "entries")
            if not batch:
                break
            for entry in batch:
                parent_id = str(entry.get("SI_PARENTID", ""))
                if parent_id and parent_id not in seen:
                    seen.add(parent_id)
                    document_ids.append(parent_id)
            try:
                last_id = max(int(entry.get("SI_ID", 0)) for entry in batch)
            except (TypeError, ValueError):
                logger.warning(
                    "SAP BO CMS query for run-date filtering returned a non-numeric SI_ID -- "
                    "stopping keyset pagination with %d document id(s) collected",
                    len(document_ids),
                )
                break
            if len(batch) < self._PAGE_REQUEST_SIZE:
                break
            page_count += 1
        return document_ids

    def list_documents(self) -> list[dict]:
        """GET /biprws/raylight/v1/documents — list all WebI documents."""
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}/biprws/raylight/v1/documents"
        raw, stuck = self._paginate_biprws_collection(url, "documents", "document", "entries")
        if stuck:
            cms_results = self._list_documents_via_cms_query()
            if cms_results is not None:
                return cms_results
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
        raw, _stuck = self._paginate_biprws_collection(
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

    def schedule_object(self, object_id: str, schedule_params: dict | None = None) -> str:
        """POST /biprws/infostore/{object_id}/schedules — schedule any BOE
        InfoStore object (WebI document, Crystal Report, or Publication) to
        run now. `schedule_params` is passed through as the JSON body for
        object-specific run parameters (e.g. prompt values). Returns the new
        schedule instance id.

        Response shape is best-effort pending verification against a live
        biprws server: assumes {"id": "<instance_id>"}, matching every other
        biprws entity this client parses (list_documents/list_reports).
        """
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}{self.SCHEDULE_ENDPOINT.format(object_id=object_id)}"
        response = self._session.post(
            url,
            json=schedule_params or {},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=object_id, http_status=response.status_code, response_body=response.text,
            )
        instance_id = str(response.json().get("id", ""))
        if not instance_id:
            raise BOAPIError(
                report_id=object_id, http_status=response.status_code,
                response_body="schedule response missing 'id'",
            )
        return instance_id

    def _normalise_schedule_status(self, raw_status: str) -> TestStatus:
        mapped = self.STATUS_MAP.get(raw_status.upper())
        if mapped is None:
            logger.warning(
                "Unrecognized SAP BO schedule status %r, treating as still running", raw_status,
            )
            return TestStatus.RUNNING
        return mapped

    def get_schedule_status(self, instance_id: str) -> TestStatus:
        """GET /biprws/infostore/{instance_id} — fetch the current status of
        a scheduled instance and map it to TestStatus. Non-terminal BOE
        states (Running/Pending/Recurring/Paused) and any unrecognized
        status string both map to TestStatus.RUNNING, so callers keep
        polling instead of mis-reading an unknown state as done."""
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}{self.INSTANCE_ENDPOINT.format(instance_id=instance_id)}"
        response = self._session.get(
            url,
            headers={"Accept": "application/json"},
            timeout=self._timeout,
            verify=self._verify_ssl,
        )
        if response.status_code >= 400:
            raise BOAPIError(
                report_id=instance_id, http_status=response.status_code, response_body=response.text,
            )
        return self._normalise_schedule_status(str(response.json().get("status", "")))

    def wait_for_completion(
        self, instance_id: str, timeout_s: float = 600, poll_interval_s: float = 5,
    ) -> TestStatus:
        """Poll get_schedule_status until it returns a terminal status
        (PASSED/FAILED) or timeout_s elapses. Raises TimeoutError if the
        instance never reaches a terminal status in time -- callers treat
        that as a run error, not a job failure."""
        deadline = time.monotonic() + timeout_s
        while True:
            status = self.get_schedule_status(instance_id)
            if status != TestStatus.RUNNING:
                return status
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"SAP BO schedule instance '{instance_id}' did not complete within {timeout_s}s",
                )
            time.sleep(poll_interval_s)

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
