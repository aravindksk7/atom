from __future__ import annotations

import json
import os
import re
import ssl
import uuid
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


HOST = os.getenv("SAPDS_MOCK_HOST", "0.0.0.0")
PORT = int(os.getenv("SAPDS_MOCK_PORT", "8444"))
USER = os.getenv("SAPDS_MOCK_USER", "administrator")
PASSWORD = os.getenv("SAPDS_MOCK_PASSWORD", "Password1")
CMS_SYSTEM = os.getenv("SAPDS_MOCK_CMS_SYSTEM", "mock-cms")
CERT_FILE = os.getenv("SAPDS_MOCK_CERT_FILE", "/certs/sapds-mock.crt")
KEY_FILE = os.getenv("SAPDS_MOCK_KEY_FILE", "/certs/sapds-mock.key")

DS_NS = "http://www.businessobjects.com"
SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"

# Batch jobs triggerable via Run_Batch_Job. Value is the terminal status text
# Get_BatchJob_Status returns once JOB_POLLS_TO_TERMINAL polls have been seen
# for that run -- "succeeded" (lowercase) on the happy path deliberately
# matches the live qetl111 quirk documented in DSRestClient.STATUS_MAP (the
# real server does not consistently capitalize status strings); "Error" on
# the failure path exercises the client's case-insensitive matching from the
# other direction.
SCHEDULABLE_JOBS = {
    "DS_NIGHTLY_LOAD": "succeeded",
    "DS_BAD_LOAD": "Error",
}
JOB_POLLS_TO_TERMINAL = 2

# SessionID -> True, for currently "logged on" sessions.
_SESSIONS: set[str] = set()
# run id -> {"job_name": str, "polls_seen": int}
_JOB_RUNS: dict[str, dict] = {}


def _tag(xml: str, local_name: str) -> str | None:
    """Namespace-prefix-agnostic first-match text extraction, mirroring
    DSRestClient's own `_first_tag_text` so the mock parses requests the same
    way the client parses responses."""
    m = re.search(
        rf"<(?:[\w.-]+:)?{re.escape(local_name)}\b[^>]*>(.*?)</(?:[\w.-]+:)?{re.escape(local_name)}>",
        xml,
        re.S,
    )
    return m.group(1).strip() if m else None


def _soap_action(headers) -> str:
    raw = headers.get("SOAPAction", "")
    return raw.strip().strip('"')


class SAPDSMockHandler(BaseHTTPRequestHandler):
    server_version = "ATOMSAPDSMock/2.0"

    def log_message(self, fmt: str, *args) -> None:
        print("%s - - %s" % (self.address_string(), fmt % args), flush=True)

    def _send_xml(self, status: HTTPStatus, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _envelope(self, body_inner: str) -> str:
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}">'
            f"<soapenv:Body>{body_inner}</soapenv:Body></soapenv:Envelope>"
        )

    def _send_result(self, status: HTTPStatus, body_inner: str) -> None:
        self._send_xml(status, self._envelope(body_inner))

    def _send_fault(self, status: HTTPStatus, message: str) -> None:
        fault = (
            f'<soapenv:Fault xmlns:soapenv="{SOAP_ENV_NS}">'
            "<faultcode>soapenv:Server</faultcode>"
            f"<faultstring>{escape(message)}</faultstring>"
            "</soapenv:Fault>"
        )
        self._send_xml(status, self._envelope(fault))

    def _session_from_header(self, body: str) -> str | None:
        header_match = re.search(r"<soapenv:Header>(.*?)</soapenv:Header>", body, re.S)
        if not header_match:
            return None
        return _tag(header_match.group(1), "SessionID")

    def _require_session(self, body: str) -> bool:
        session_id = self._session_from_header(body)
        if session_id is None or session_id not in _SESSIONS:
            self._send_fault(HTTPStatus.INTERNAL_SERVER_ERROR, "Session expired or invalid")
            return False
        return True

    def do_GET(self) -> None:
        if urlparse(self.path).path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length) if length else b""
        body = raw_body.decode("utf-8", "replace")
        action = _soap_action(self.headers)

        handlers = {
            "function=Logon": self._handle_logon,
            "function=Ping": self._handle_ping,
            "function=Logout": self._handle_logout,
            "jobAdmin=Run_Batch_Job": self._handle_run_batch_job,
            "jobAdmin=Get_BatchJob_Status": self._handle_get_batch_job_status,
        }
        handler = handlers.get(action)
        if handler is None:
            self._send_fault(HTTPStatus.NOT_FOUND, f"Unknown SOAPAction {action!r}")
            return
        handler(body)

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def _handle_logon(self, body: str) -> None:
        # LogonRequest is sent UNQUALIFIED (see DSRestClient.login) -- element
        # names are lowercase per the WSDL XSD.
        username = _tag(body, "username") or ""
        password = _tag(body, "password") or ""
        cms_system = _tag(body, "cms_system") or ""
        if not cms_system:
            self._send_fault(
                HTTPStatus.BAD_REQUEST, "The syntax for the specified CMS name is invalid",
            )
            return
        if cms_system != CMS_SYSTEM or username != USER or password != PASSWORD:
            self._send_fault(HTTPStatus.UNAUTHORIZED, "Logon failed: invalid credentials")
            return
        session_id = f"MOCK-SESS-{uuid.uuid4().hex}"
        _SESSIONS.add(session_id)
        self._send_result(
            HTTPStatus.OK,
            f'<session xmlns="{DS_NS}"><SessionID>{session_id}</SessionID></session>',
        )

    def _handle_ping(self, body: str) -> None:
        if not self._require_session(body):
            return
        self._send_result(HTTPStatus.OK, f'<pingVersion xmlns="{DS_NS}">MOCK-1.0</pingVersion>')

    def _handle_logout(self, body: str) -> None:
        session_id = self._session_from_header(body)
        _SESSIONS.discard(session_id)
        self._send_result(HTTPStatus.OK, "<LogoutResponse/>")

    def _handle_run_batch_job(self, body: str) -> None:
        if not self._require_session(body):
            return
        job_name = _tag(body, "jobName") or ""
        repo_name = _tag(body, "repoName") or ""
        if job_name not in SCHEDULABLE_JOBS:
            self._send_fault(HTTPStatus.NOT_FOUND, f"Job '{job_name}' not found in repository '{repo_name}'")
            return
        run_id = str(uuid.uuid4().int % 100000)
        _JOB_RUNS[run_id] = {"job_name": job_name, "polls_seen": 0}
        self._send_result(
            HTTPStatus.OK,
            f'<BatchJobResponse xmlns="{DS_NS}"><pid>1</pid><cid>1</cid>'
            f"<rid>{run_id}</rid><repoName>{escape(repo_name)}</repoName>"
            f"<returnCode>0</returnCode></BatchJobResponse>",
        )

    def _handle_get_batch_job_status(self, body: str) -> None:
        if not self._require_session(body):
            return
        run_id = _tag(body, "runID") or ""
        run = _JOB_RUNS.get(run_id)
        if run is None:
            # No run-id-keyed "not found" signal in this operation's real
            # contract -- an unrecognized run id just gets an empty status,
            # same as DSRestClient._normalise_job_status's "keep polling"
            # fallback for anything it doesn't recognize.
            self._send_result(
                HTTPStatus.OK,
                f'<batchJobStatusResponse xmlns="{DS_NS}"><returnCode>0</returnCode>'
                f"<status></status></batchJobStatusResponse>",
            )
            return
        run["polls_seen"] += 1
        if run["polls_seen"] < JOB_POLLS_TO_TERMINAL:
            status = "Running"
        else:
            status = SCHEDULABLE_JOBS[run["job_name"]]
        self._send_result(
            HTTPStatus.OK,
            f'<batchJobStatusResponse xmlns="{DS_NS}"><returnCode>0</returnCode>'
            f"<status>{status}</status></batchJobStatusResponse>",
        )


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), SAPDSMockHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"SAP DS SOAP mock listening on https://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
