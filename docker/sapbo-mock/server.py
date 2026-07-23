from __future__ import annotations

import csv
import io
import json
import os
import re
import ssl
import zipfile
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


HOST = os.getenv("SAPBO_MOCK_HOST", "0.0.0.0")
PORT = int(os.getenv("SAPBO_MOCK_PORT", "8443"))
USER = os.getenv("SAPBO_MOCK_USER", "administrator")
PASSWORD = os.getenv("SAPBO_MOCK_PASSWORD", "Password1")
AUTH_TYPE = os.getenv("SAPBO_MOCK_AUTH_TYPE", "secEnterprise")
CERT_FILE = os.getenv("SAPBO_MOCK_CERT_FILE", "/certs/sapbo-mock.crt")
KEY_FILE = os.getenv("SAPBO_MOCK_KEY_FILE", "/certs/sapbo-mock.key")
TOKEN = "mock-sapbo-token"
# Admin-configured CMC page-size cap, enforced server-side regardless of the
# `pagesize` a client requests — mirrors on-premises biprws deployments.
PAGE_CAP = int(os.getenv("SAPBO_MOCK_PAGE_CAP", "10"))


DOCUMENTS = [
    {"id": "1001", "name": "Sales Orders", "folder": "/Public Folders/ATOM"},
    {"id": "1002", "name": "Inventory Snapshot", "folder": "/Public Folders/ATOM"},
]

REPORTS = {
    "1001": [
        {"id": "rpt-sales", "name": "Orders", "reportIndex": 0},
        {"id": "rpt-sales-summary", "name": "Summary", "reportIndex": 1},
    ],
    "1002": [
        {"id": "rpt-inventory", "name": "Inventory", "reportIndex": 0},
    ],
}

# Bulk fixtures for exercising the on-premises CMC page-size cap (PAGE_CAP
# below) end-to-end: more documents than one page, and one document with
# more report tabs than one page.
_BULK_DOC_COUNT = 25
_BULK_REPORT_COUNT = 25
for _i in range(_BULK_DOC_COUNT):
    _doc_id = f"2{_i:03d}"
    DOCUMENTS.append({"id": _doc_id, "name": f"Bulk Report {_i}", "folder": "/Public Folders/BULK"})
    REPORTS[_doc_id] = [
        {"id": f"rpt-{_doc_id}-{_j}", "name": f"Tab {_j}", "reportIndex": _j}
        for _j in range(_BULK_REPORT_COUNT)
    ]

DATASETS = {
    ("1001", "rpt-sales"): [
        {"id": 1, "sku": "A100", "amount": 25.50, "status": "SHIPPED"},
        {"id": 2, "sku": "B200", "amount": 50.00, "status": "OPEN"},
        {"id": 3, "sku": "C300", "amount": 75.00, "status": "SHIPPED"},
    ],
    ("1001", "rpt-sales-summary"): [
        {"metric": "orders", "value": 3},
        {"metric": "amount", "value": 150.50},
    ],
    ("1002", "rpt-inventory"): [
        {"id": 10, "sku": "A100", "on_hand": 42},
        {"id": 11, "sku": "B200", "on_hand": 7},
    ],
    ("2000", "rpt-2000-0"): [
        {"id": 1, "sku": "Z900", "amount": 12.00, "status": "OPEN"},
    ],
}


def _collapse_single(items: list) -> list | dict:
    """Mirror SAP BO biprws: a one-element collection serializes as a bare
    object, not a one-element array."""
    return items[0] if len(items) == 1 else items


def _rows_for_doc(doc_id: str) -> list[dict]:
    reports = REPORTS.get(doc_id, [])
    if not reports:
        return []
    return DATASETS.get((doc_id, reports[0]["id"]), [])


def _csv_bytes(rows: list[dict]) -> bytes:
    output = io.StringIO()
    fieldnames = list(rows[0].keys()) if rows else ["id"]
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def _xlsx_bytes(rows: list[dict]) -> bytes:
    headers = list(rows[0].keys()) if rows else ["id"]
    sheet_rows = [headers] + [[row.get(header, "") for header in headers] for row in rows]
    sheet_xml = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>',
    ]
    for row_idx, row in enumerate(sheet_rows, start=1):
        sheet_xml.append(f'<row r="{row_idx}">')
        for col_idx, value in enumerate(row, start=1):
            cell = f"{_col_name(col_idx)}{row_idx}"
            sheet_xml.append(
                f'<c r="{cell}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        sheet_xml.append("</row>")
    sheet_xml.append("</sheetData></worksheet>")

    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        ),
        "xl/worksheets/sheet1.xml": "".join(sheet_xml),
    }

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


class SAPBOMockHandler(BaseHTTPRequestHandler):
    server_version = "ATOMSAPBOMock/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("%s - - %s" % (self.address_string(), fmt % args), flush=True)

    def _send_json(self, status: HTTPStatus, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_token(self) -> bool:
        if self.headers.get("X-SAP-LogonToken") == TOKEN:
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "missing or invalid X-SAP-LogonToken"})
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if not self._require_token():
            return

        # The real biprws API paginates these collections (client.py's
        # _paginate_biprws_collection keeps requesting `page` until a page
        # comes back shorter than the previous one or empty), and some
        # on-premises deployments admin-cap the page size (CMC setting) below
        # whatever `pagesize` the client requests. Mirror that here: always
        # slice to PAGE_CAP regardless of the requested `pagesize`, so a
        # client that doesn't keep paging past a full-looking first page will
        # silently lose everything past PAGE_CAP items.
        page = int(parse_qs(parsed.query).get("page", ["1"])[0] or "1")
        start = (page - 1) * PAGE_CAP
        end = start + PAGE_CAP

        if path == "/biprws/raylight/v1/documents":
            # Real on-premises biprws wraps the collection under a "document"
            # child even when there's only one entry (observed live payload
            # kept it as a one-element array, unlike the "reports" sub-resource
            # below which does collapse a single element to a bare object).
            self._send_json(
                HTTPStatus.OK,
                {"documents": {"document": DOCUMENTS[start:end]}},
            )
            return

        reports_match = re.fullmatch(r"/biprws/raylight/v1/documents/([^/]+)/reports", path)
        if reports_match:
            doc_id = reports_match.group(1)
            if doc_id not in REPORTS:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"document {doc_id} not found"})
                return
            reports = REPORTS[doc_id][start:end]
            self._send_json(
                HTTPStatus.OK,
                {
                    "reports": _collapse_single(reports),
                    "dataset": _rows_for_doc(doc_id),
                },
            )
            return

        content_match = re.fullmatch(
            r"/biprws/raylight/v1/documents/([^/]+)/reports/([^/]+)",
            path,
        )
        if content_match:
            doc_id, report_id = content_match.groups()
            rows = DATASETS.get((doc_id, report_id))
            if rows is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"report {report_id} not found"})
                return
            accept = self.headers.get("Accept", "")
            if "spreadsheetml" in accept:
                self._send_bytes(
                    HTTPStatus.OK,
                    _xlsx_bytes(rows),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            elif "pdf" in accept:
                self._send_bytes(HTTPStatus.OK, b"%PDF-1.4\n% SAP BO mock report\n", "application/pdf")
            else:
                self._send_bytes(HTTPStatus.OK, _csv_bytes(rows), "text/csv")
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/biprws/logon/long":
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            if payload.get("auth") != AUTH_TYPE:
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": f"auth type '{payload.get('auth')}' not accepted, expected '{AUTH_TYPE}'"},
                )
                return
            if payload.get("userName") != USER or payload.get("password") != PASSWORD:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid credentials"})
                return
            self._send_json(
                HTTPStatus.OK,
                {"success": True},
                headers={"X-SAP-LogonToken": TOKEN},
            )
            return

        if path == "/biprws/logoff":
            self._send_json(HTTPStatus.OK, {"success": True})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), SAPBOMockHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"SAP BO mock listening on https://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
