import io
import logging
import os
import re
import time
import zipfile
from datetime import date, datetime, timedelta
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


def _count_export_rows(data: bytes, format: str) -> int | None:
    """Rows in an exported report, or None when the format/bytes can't be counted.

    Only the tabular exports are countable: xlsx via the first worksheet's
    <row> elements (no openpyxl dependency — the export log must never be the
    thing that fails), csv via non-empty lines. PDF and anything unparseable
    return None, meaning "unknown", never "empty".
    """
    if not data:
        return 0
    if format == "csv":
        text = data.decode("utf-8", "replace")
        return len([line for line in text.splitlines() if line.strip()])
    if format != "xlsx" or data[:2] != b"PK":
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            sheets = sorted(
                n for n in archive.namelist() if n.startswith("xl/worksheets/sheet")
            )
            if not sheets:
                return None
            xml = archive.read(sheets[0]).decode("utf-8", "replace")
            return xml.count("<row ") + xml.count("<row>")
    except Exception:  # noqa: BLE001 - diagnostics must not break the download
        return None


_XLSX_TEXT = re.compile(r"<t[^>]*>(.*?)</t>", re.DOTALL)


def _preview_export_text(data: bytes, format: str, limit: int = 40) -> str | None:
    """First few cell strings in an export, or None when not previewable.

    A row count alone can't distinguish "17 rows of data" from "17 rows of
    report title, filter summary and column headers with an empty table" —
    which is exactly the shape this deployment returns. The cell text can, and
    it costs one already-downloaded byte string to read.
    """
    if not data:
        return None
    if format == "csv":
        lines = [ln for ln in data.decode("utf-8", "replace").splitlines() if ln.strip()]
        return " | ".join(lines[:5]) if lines else None
    if format != "xlsx" or data[:2] != b"PK":
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            texts: list[str] = []
            # Shared strings hold every distinct string in the workbook; inline
            # strings live in the sheet itself. Real exports use one or other.
            for member in ("xl/sharedStrings.xml", *sorted(
                n for n in names if n.startswith("xl/worksheets/sheet")
            )):
                if member not in names:
                    continue
                xml = archive.read(member).decode("utf-8", "replace")
                texts.extend(t.strip() for t in _XLSX_TEXT.findall(xml) if t.strip())
                if texts:
                    break
            return " | ".join(texts[:limit]) if texts else None
    except Exception:  # noqa: BLE001 - diagnostics must not break the download
        return None


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


def _dataproviders_refreshed_flag(response) -> str | None:
    """Pull `allDataprovidersRefreshed` out of the answer PUT's response.

    The live occurrence-0 PUT replies:
        {"success": {"message": …, "id": …, "details": {"property": [
            {"@key": "allDataprovidersRefreshed", "$": "true"}]}}}

    Returns the flag's value, or None when the response carries no such
    property (or isn't JSON at all). Never raises — this is an observation
    used for logging, not a gate on the download.
    """
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - any parse failure means "no flag"
        return None
    if not isinstance(payload, dict):
        return None
    details = ((payload.get("success") or {}).get("details") or {})
    for prop in _as_list(details.get("property")):
        if isinstance(prop, dict) and prop.get("@key") == "allDataprovidersRefreshed":
            return str(prop.get("$", ""))
    return None


# Sent by the Fiori BI viewer on every Raylight call (SAPBO_10_bold.har).
#
# These do NOT fix the blank export, and were briefly believed to. The
# 2026-08-07 matrix ran the answer PUT with them and without them and got
# allDataprovidersRefreshed:"true" either way — as it did for the c= buster,
# the pre-answer document open, an unchanged answer value, and the
# document-level parameters resource. Every difference in request shape is
# acquitted; the cause is still open.
#
# They are kept only because matching the one caller known to work costs
# nothing and this deployment sits behind a gateway with a history of treating
# requests differently than it should. Do not read them as a fix, and do not
# add more on the same reasoning.
_VIEWER_HEADERS = {
    "X-Client-Type": "wise",
    "X-SAP-PVL": "en_US",
    "Accept-Language": "en_US",
    "X-Requested-With": "XMLHttpRequest",
}


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
        # The occurrence path spelling this server was proved to use, or None
        # while both candidates are still open. See _occurrence_urls.
        self._occurrence_segment: str | None = None
        # (doc_id, allDataprovidersRefreshed) from the last answer PUT, so the
        # export can tell a healthy pull from one it already knows will carry
        # layout and no rows. Keyed by document because one client serves many
        # in a session. See download_report.
        self._last_refresh: tuple[str, str | None] | None = None
        self._session = requests.Session()
        self._session.headers.update(_VIEWER_HEADERS)
        self._verify_ssl = env_config.bo_verify_ssl
        self._server_utc_offset_hours = env_config.bo_server_utc_offset_hours
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
        previous_batch_size: int | None = None
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
            try:
                batch_max_id = max(int(d.get("SI_ID", 0)) for d in batch)
            except (TypeError, ValueError):
                logger.warning(
                    "SAP BO CMS query returned a non-numeric SI_ID -- stopping keyset "
                    "pagination with the %d document(s) collected so far", len(collected),
                )
                break
            # Guard against a gateway/server that ignores the `SI_ID > cursor`
            # clause and re-serves an already-seen page (the same class of
            # intermediary that defeated page/Range above): if the max id
            # didn't advance past the previous cursor, stop instead of looping
            # to _MAX_PAGES on duplicate data.
            if last_id is not None and batch_max_id <= last_id:
                logger.warning(
                    "SAP BO CMS query cursor did not advance past SI_ID %d (server re-served an "
                    "already-seen page) -- stopping with %d document(s) collected so far",
                    last_id, len(collected),
                )
                break
            collected.extend(batch)
            last_id = batch_max_id
            # A page shorter than the *previous* page means the collection is
            # exhausted; a page shorter than the requested TOP does NOT -- this
            # deployment caps every result set below our TOP (observed: 50 rows
            # vs TOP 200), so keying off TOP stops after the first page and
            # loses the rest. Same rule as the page-param path's docstring.
            if previous_batch_size is not None and len(batch) < previous_batch_size:
                break
            previous_batch_size = len(batch)
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
        # CeQL time literals are single-quoted 'yyyy.MM.dd.HH.mm.ss' strings.
        # The @-prefixed form is not valid CeQL: the live on-prem CMS parses it
        # far enough to reach execution, then fails with HTTP 500 (the listing
        # query works precisely because it carries no time literal).
        #
        # The CMS stores/compares SI_STARTTIME in UTC, so the requested local
        # `day` window [00:00 local, next 00:00 local) is shifted back by the
        # server's UTC offset to express it in UTC (offset 0 => unchanged).
        offset = timedelta(hours=self._server_utc_offset_hours)
        start_utc = datetime(day.year, day.month, day.day) - offset
        end_utc = start_utc + timedelta(days=1)
        day_start = f"'{start_utc:%Y.%m.%d.%H.%M.%S}'"
        day_end = f"'{end_utc:%Y.%m.%d.%H.%M.%S}'"
        date_clause = f"SI_INSTANCE=1 AND SI_STARTTIME >= {day_start} AND SI_STARTTIME < {day_end}"

        document_ids: list[str] = []
        seen: set[str] = set()
        last_id: int | None = None
        previous_batch_size: int | None = None
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
            try:
                batch_max_id = max(int(entry.get("SI_ID", 0)) for entry in batch)
            except (TypeError, ValueError):
                logger.warning(
                    "SAP BO CMS query for run-date filtering returned a non-numeric SI_ID -- "
                    "stopping keyset pagination with %d document id(s) collected",
                    len(document_ids),
                )
                break
            # Same re-serve guard as _list_documents_via_cms_query: stop if the
            # cursor didn't advance rather than looping on a re-served page.
            if last_id is not None and batch_max_id <= last_id:
                logger.warning(
                    "SAP BO CMS query for run-date filtering cursor did not advance past SI_ID %d "
                    "(server re-served an already-seen page) -- stopping with %d document id(s)",
                    last_id, len(document_ids),
                )
                break
            for entry in batch:
                parent_id = str(entry.get("SI_PARENTID", ""))
                if parent_id and parent_id not in seen:
                    seen.add(parent_id)
                    document_ids.append(parent_id)
            last_id = batch_max_id
            # Short-vs-previous means exhausted; short-vs-TOP does not (this
            # deployment caps results below our TOP) -- see
            # _list_documents_via_cms_query for the same rule and rationale.
            if previous_batch_size is not None and len(batch) < previous_batch_size:
                break
            previous_batch_size = len(batch)
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

    def get_document_parameters(self, doc_id: str) -> list[dict]:
        """GET …/documents/{doc_id}/parameters — list a report's prompts.

        Returns one dict per prompt: {"id", "name", "type", "mandatory"}.
        Unwraps both the flat {"parameters":[…]} and nested
        {"parameters":{"parameter":[…]}} BIP shapes (same convention as
        list_documents).
        """
        if not self._token:
            self.authenticate()
        url = f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}/parameters"
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
        raw = _unwrap_collection(response.json(), "parameters", "parameter")
        result = []
        for p in raw:
            # Real BIP raylight nests the value data type ("DateTime"/"String")
            # under `answer`; the top-level `type` is the parameter kind
            # ("prompt"). Prefer answer.type so date prompts are recognised and
            # converted — else they export as raw text and BO rejects (502).
            answer = p.get("answer") or {}
            dtype = (
                answer.get("type") or answer.get("@type")
                or p.get("type") or p.get("@type", "")
            )
            # Surface the prompt's current/default answer value when the
            # listing carries one, so the UI can pre-fill an editable default.
            vals = (answer.get("values") or {}).get("value") or []
            if isinstance(vals, dict):
                vals = [vals]
            default = str(vals[0].get("$", "")) if vals else ""
            # A DateTime default arrives as a full ISO instant; the <input
            # type="date"> picker only accepts YYYY-MM-DD, so trim to the date.
            if dtype == "DateTime" and re.match(r"^\d{4}-\d{2}-\d{2}", default):
                default = default[:10]
            result.append({
                "id": p.get("id"),
                "name": p.get("name", ""),
                "type": dtype,
                "mandatory": bool(p.get("mandatory", False)),
                "default": default,
            })
        return result

    # BO has shipped the occurrence path segment both ways: the 2026-08-04
    # trace and current docs say "occurrences", some releases say "occurences".
    # Ordered by what this deployment was observed to use.
    _OCCURRENCE_SEGMENTS = ("occurrences", "occurences")

    def _occurrence_urls(self, doc_id: str, suffix: str = ""):
        """Yield the occurrence-0 URLs to try, best candidate first.

        Once a spelling has answered anything other than a 404 it is the only
        one yielded: a deployment does not change spelling between requests,
        and probing again would double the write traffic of every prompted
        download on exactly the deployments that need the retry.
        """
        segments = (
            (self._occurrence_segment,) if self._occurrence_segment
            else self._OCCURRENCE_SEGMENTS
        )
        for segment in segments:
            yield (
                f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}"
                f"/{segment}/0{suffix}"
            )

    def _remember_occurrence_url(self, url: str) -> None:
        """Pin the spelling that a non-404 response proved this server uses."""
        for segment in self._OCCURRENCE_SEGMENTS:
            if f"/{segment}/0" in url:
                self._occurrence_segment = segment
                return

    def answer_document_parameters(self, doc_id: str, built_answers: list[dict]) -> None:
        """PUT …/documents/{doc_id}/occurrences/0/parameters — answer a
        document's prompts and refresh its data providers.

        This is step 1 of the two-step flow the on-premises web UI actually
        performs (2026-08-04 trace, document 124313); there is no snapshot or
        schedule step. Step 2 is `download_report`, which MUST read the same
        occurrence — see its docstring.

        `allDataprovidersRefreshed` is the one piece of positive evidence in
        this flow, and the thing that decides whether the export carries data —
        the export resource does not (see `download_report`). Its absence is
        logged as a warning here rather than discovered later as a workbook of
        column headers with no rows, and recorded on `_last_refresh` so the
        export can collect diagnostics instead of guessing from a row count.

        Whether the **document-level** parameters resource (the one
        `get_document_parameters` reads) would do just as well is untested. It
        was once claimed here that only the occurrence PUT refreshes; nothing
        on record establishes that, and the neighbouring claim about export
        resources turned out to be false, so treat it as open. The occurrence
        is used because it is the resource the browser writes.

        Occurrence **0** specifically: an earlier 404 (`the resource of type
        "Occurrence" with identifier "1" does not exist`) was for index 1, a
        viewing session's own instance copied out of a browser trace. Index 0
        is the document's persisted occurrence and needs no session.

        `built_answers` is a list of already-finalized
        {"id", "type", "value"} (date conversion and answer-vocabulary
        normalisation done by
        etl_framework.sap_bo.parameters.build_parameter_answers). Logs the
        full URL and the server's response body on failure so a path or
        vocabulary mismatch on a given deployment names itself instead of
        surfacing as an opaque 502.
        """
        if not self._token:
            self.authenticate()
        body = {"parameters": {"parameter": [
            {"id": a["id"], "answer": {"values": {"value": [
                {"$": a["value"], "@type": a["type"]}]}}}
            for a in built_answers
        ]}}
        # Values, not just ids and types. The 2026-08-07 matrix acquitted every
        # difference in request *shape* — viewer headers, the c= buster, the
        # pre-answer open, re-sending an unchanged value, and the
        # occurrence-vs-document resource all refreshed. What is left
        # unreplayed is the payload: `build_parameter_answers` converts a date
        # through the app timezone, so a non-UTC setting ships a shifted
        # instant ("2026-05-08" -> "2026-05-07T23:00:00.000Z") where the
        # browser and the probe both send plain UTC midnight. The 11:53 failure
        # could not be reproduced because this line recorded the shape and
        # dropped the payload. Prompt answers are report filters, not secrets.
        logger.info(
            "SAP BO answering %d parameter(s) on document %s: %s",
            len(built_answers), doc_id,
            [(a["id"], a["type"], a["value"]) for a in built_answers],
        )
        # The web UI posts every prompt and turns an untouched optional one into
        # "" (frontend/features/adapters.js). BO can apply that as a filter and
        # match nothing, so name the prompts before the export, not after.
        blank = [a["id"] for a in built_answers if not str(a["value"]).strip()]
        if blank:
            logger.warning(
                "SAP BO document %s: prompt(s) %s answered with an empty value. "
                "BO treats an empty answer as a filter, not as 'unanswered', so "
                "the export may come back with no rows.",
                doc_id, blank,
            )
        for url in self._occurrence_urls(doc_id, "/parameters"):
            response = self._session.put(
                url,
                params={
                    "dataproviderScope": "accessible",
                    "lovInfo": "false",
                    "prepare": "false",
                    # The viewer puts c=<ms> on every URL including this PUT.
                    # This deployment sits behind a gateway already caught
                    # keying purely on URL path — it defeats both `page` and
                    # `Range:` on the document listing.
                    "c": str(int(time.time() * 1000)),
                },
                json=body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
            if response.status_code != 404:
                self._remember_occurrence_url(url)
                break
        if response.status_code >= 400:
            logger.error(
                "SAP BO rejected the prompt answer PUT %s -> HTTP %s: %s",
                url, response.status_code, response.text,
            )
            raise BOAPIError(
                report_id=doc_id,
                http_status=response.status_code,
                response_body=response.text,
            )
        # A 200 here does not prove the answer took effect on the data the
        # export later reads — the blank export was a 200. Log what the server
        # echoed back, and single out the one flag that does carry evidence.
        #
        # The URL is logged on success too, not only on the error path above:
        # the 2026-08-05 11:53 log recorded a refreshed=false response without
        # it, which left "did that write reach the occurrence or the document?"
        # — the single most important fact about the request — unanswerable
        # after the fact.
        logger.info(
            "SAP BO answer PUT %s for document %s -> HTTP %s: %s",
            url, doc_id, response.status_code, str(response.text or "")[:1500],
        )
        refreshed = _dataproviders_refreshed_flag(response)
        self._last_refresh = (doc_id, refreshed)
        if refreshed == "true":
            logger.info(
                "SAP BO document %s reports allDataprovidersRefreshed=true", doc_id,
            )
        else:
            # A prediction, not a diagnosis. The 2026-08-07 19:40 log shows
            # "false" arriving alongside providers whose `updated` had moved to
            # this PUT's own moment, isPartial "false" and rowCount 0: the query
            # ran to completion and matched nothing. So the flag does not mean
            # the refresh was skipped — it reliably precedes a data-less export,
            # which is all that is claimed here. `download_report` reads it off
            # `_last_refresh` and gets the actual verdict from the providers.
            #
            # No escalation follows. SAP documents POST …/documents/{id}/parameters
            # as a separate refresh trigger and this client used to call it here;
            # the on-premises server answers that method with HTTP 405.
            logger.warning(
                "SAP BO document %s answered without allDataprovidersRefreshed=true "
                "(got %r) — the export is likely to come back with layout but no "
                "data rows.",
                doc_id, refreshed,
            )

    _MIME_MAP: dict[str, str] = {
        "pdf":  "application/pdf",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "csv":  "text/csv",
    }

    def download_report(self, doc_id: str, report_id: str = "", format: str = "pdf") -> bytes:
        """GET …/documents/{doc_id}/occurrences/0 — export as PDF/XLSX/CSV via
        the Accept header, with the report tab chosen by `reportIds`.

        Step 2 of the flow: `answer_document_parameters` refreshes occurrence
        0's data providers, then this reads that same occurrence.

        This URL and its parameters are confirmed against a real browser
        download (SAPBO_10_bold.har, 2026-08-07 05:41:36): the viewer's Export
        → Excel issues exactly `GET …/documents/{id}/occurrences/0?dpi=96&
        optimized=true&reportIds={n}&c=<ms>` with the xlsx Accept header, and
        gets 901280 bytes back. Earlier revisions of this docstring cited a
        trace that did not in fact contain the export.

        The export resource does **not** decide whether data comes back. This
        docstring used to claim `…/documents/{id}/reports/{id}` "returns HTTP
        200 and a well-formed workbook containing the report layout and zero
        data rows, because that resource does not see the refresh". The
        2026-08-07 probe run disproves it: against a refreshed document,
        occurrences/0?reportIds=N, occurrences/0/reports/N, documents/{id} and
        documents/{id}/reports/N all returned the same 901280 bytes and 18175
        rows. Occurrence 0 is kept because it is what the viewer reads and it
        costs nothing, not because the alternatives are blank.

        What decides it is upstream: whether the answer PUT reported
        `allDataprovidersRefreshed:"true"`. A blank workbook means the refresh
        did not run — see `answer_document_parameters` and the diagnostics at
        the end of this method.

        An empty `report_id` exports the **whole document** — every tab in one
        file, SAP's primary step 5 — by omitting `reportIds` entirely rather
        than sending it empty, which BO would read as the tab named "".

        `dpi`/`optimized` are the UI's own rendering options; `c` is its cache
        buster, kept because this deployment sits behind a proxy already caught
        re-serving cached GETs (it defeats `page` and `Range:` on the document
        listing).
        """
        if not self._token:
            self.authenticate()
        accept = self._MIME_MAP.get(format, "application/pdf")
        params = {
            "dpi": "96",
            "optimized": "true",
            "c": str(int(time.time() * 1000)),
        }
        if report_id:
            params["reportIds"] = report_id
        for url in self._occurrence_urls(doc_id):
            response = self._session.get(
                url,
                params=params,
                headers={"Accept": accept},
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
            if response.status_code != 404:
                self._remember_occurrence_url(url)
                break
        if response.status_code == 404:
            # A document with no prompts is downloaded with no preceding answer
            # PUT, so nothing guarantees it has an occurrence 0 — that resource
            # has only been observed on a prompted document.
            #
            # The fallback is not a degraded export. The 2026-08-07 probe run
            # pulled a refreshed document from all four resources and got
            # identical bytes, so this warning no longer predicts missing rows;
            # it records that the expected resource was absent, which is worth
            # knowing on its own.
            #
            # Which resource depends on what was asked for: SAP's whole-document
            # export is the document itself, its single-tab alternative is
            # …/reports/{id}. Falling back to a report resource for a request
            # that named no report would export the tab called "".
            logger.warning(
                "SAP BO occurrence 0 unavailable for document %s (HTTP 404: %s); "
                "falling back to the %s resource.",
                doc_id, str(response.text or "")[:300],
                "report" if report_id else "document",
            )
            url = f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}"
            if report_id:
                url = f"{url}/reports/{report_id}"
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
        rows = _count_export_rows(response.content, format)
        logger.info(
            "SAP BO export %s doc=%s report=%s fmt=%s -> HTTP %s, content-type=%s, "
            "bytes=%d, rows=%s",
            url, doc_id, report_id, format, response.status_code,
            (response.headers or {}).get("Content-Type"),
            len(response.content), "unknown" if rows is None else rows,
        )
        preview = _preview_export_text(response.content, format)
        if preview:
            logger.info(
                "SAP BO export doc=%s report=%s cell preview: %s",
                doc_id, report_id, preview[:1200],
            )
        # A row count cannot recognise this deployment's blank export: a WebI
        # sheet carries title, filter-summary and column-header rows even with
        # an empty table, so the observed data-less pull still reported
        # rows=17 — well clear of any threshold that wouldn't also fire on
        # healthy pulls.
        #
        # The answer PUT's `allDataprovidersRefreshed` can, and it is already
        # known by the time we get here: anything other than "true" is the exact
        # state that produced those 17 rows. Documents answered in this session
        # are matched by id so a failed refresh on one does not drag diagnostics
        # behind every later download. The row-count and env-var gates stay:
        # a genuinely empty file is still worth a look, and a document with no
        # prompts never sets the flag at all.
        forced = bool(os.environ.get("ATOM_BO_EXPORT_DIAGNOSTICS"))
        answered = self._last_refresh
        refresh_failed = bool(
            answered and answered[0] == doc_id and answered[1] != "true"
        )
        if forced or refresh_failed or (rows is not None and rows <= 1):
            logger.warning(
                "SAP BO export doc=%s report=%s collecting diagnostics "
                "(rows=%s, bytes=%d, forced=%s, refresh_failed=%s).",
                doc_id, report_id, rows, len(response.content), forced,
                refresh_failed,
            )
            self._log_blank_export_diagnostics(doc_id)
        return response.content

    def _diagnostic_get(self, label: str, url: str):
        """GET a diagnostic resource and log status + body. Never raises.

        Returns the response so the caller can draw a conclusion from it, or
        None if the request failed.
        """
        try:
            response = self._session.get(
                url,
                headers={"Accept": "application/json"},
                timeout=self._timeout,
                verify=self._verify_ssl,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SAP BO blank-export diagnostic [%s] %s failed: %s", label, url, exc
            )
            return None
        logger.warning(
            "SAP BO blank-export diagnostic [%s] %s -> HTTP %s: %s",
            label, url, response.status_code, str(response.text or "")[:1000],
        )
        return response

    def _log_dataprovider_verdict(self, doc_id: str, response) -> None:
        """Say what the occurrence's data providers mean. Never raises.

        The 2026-08-07 19:40 log is why this exists. It carried
        `allDataprovidersRefreshed:"false"` alongside providers reporting
        `updated` equal to the answer PUT's own moment, `isPartial:"false"` and
        `rowCount:0` — i.e. the query ran to completion and matched nothing.
        The flag does not mean the refresh was skipped, and reading it that way
        cost several rounds of investigation into the request instead of the
        result.

        Probe runs against that same document, with the same date and code but
        a different account, return 18159 rows. A completed zero-row query
        therefore points at what the account is allowed to see — row
        restrictions on the universe, or a connection resolving to different
        database credentials — rather than at anything this client sends.
        """
        if response is None:
            return
        try:
            providers = _unwrap_collection(
                response.json(), "dataproviders", "dataprovider")
        except Exception:  # noqa: BLE001 - a verdict we cannot form is not fatal
            return
        if not providers:
            return
        counts = [p.get("rowCount") for p in providers if isinstance(p, dict)]
        known = [c for c in counts if isinstance(c, int)]
        if not known:
            return
        stamps = ", ".join(
            f"{p.get('id')}: rowCount={p.get('rowCount')} updated={p.get('updated')}"
            for p in providers if isinstance(p, dict)
        )
        if any(c > 0 for c in known):
            logger.warning(
                "SAP BO document %s: the data providers hold rows (%s) but the "
                "export carried none — that is a problem with the export, not "
                "with the query or the account.",
                doc_id, stamps,
            )
            return
        logger.warning(
            "SAP BO document %s: the data providers ran and returned 0 rows "
            "(%s). `updated` moving to the answer PUT's own moment means the "
            "query executed and matched nothing — it was not skipped. Check "
            "what this account may see (row restrictions on the universe, or a "
            "connection mapping to different database credentials) and whether "
            "the prompt values select any data; the same document and prompts "
            "return rows for accounts with the rights.",
            doc_id, stamps,
        )

    def _log_blank_export_diagnostics(self, doc_id: str) -> None:
        """Dump the document's data-provider state after a suspiciously empty
        export: it distinguishes "nothing was refreshed" from "refreshed and
        the answers genuinely match no rows". Diagnostics only — the call
        swallows its errors, so this can never turn a successful export into a
        failure.

        Both scopes are probed. The 2026-08-05 browser trace's `rowCount:18159`
        came from the **occurrence's** data providers, while this dumped only
        `…/documents/{id}/dataproviders` — a different resource, and so unable
        to answer "did rows land on the thing we just exported?". The
        document-scoped dump is kept alongside it: it still distinguishes a
        document whose providers have never run from one whose occurrence
        simply isn't carrying the refresh.
        """
        doc_url = f"{self._base_url}/biprws/raylight/v1/documents/{doc_id}"
        occurrence_url = next(iter(self._occurrence_urls(doc_id, "/dataproviders")))
        occurrence = self._diagnostic_get("dataproviders (occurrence)", occurrence_url)
        self._diagnostic_get("dataproviders (document)", f"{doc_url}/dataproviders")
        self._log_dataprovider_verdict(doc_id, occurrence)

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
