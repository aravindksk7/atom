from __future__ import annotations

import json
import os
import re
import ssl
import uuid
from html import escape, unescape
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
# run id -> {"job_name": str, "polls_seen": int, "variables": dict[str, str]}
_JOB_RUNS: dict[str, dict] = {}
# Most recently triggered run id, so a test that doesn't otherwise learn the DS-side
# rid (atom's own run API doesn't surface it -- see RunExecutor._build_case_ds_job)
# can still inspect the last Run_Batch_Job call via GET /debug/last-run.
_LAST_RUN_ID: str | None = None


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


_NUMERIC_LITERAL_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
# A BODS single-quoted string literal: opening/closing ', with any embedded
# ' doubled ('') -- the same escaping DSRestClient._bods_literal applies.
_QUOTED_LITERAL_RE = re.compile(r"^'(?:[^']|'')*'$")


class InvalidGlobalVariableLiteral(ValueError):
    """Raised when a <variable> value isn't a valid BODS expression.

    Run_Batch_Job on the live server evaluates each global variable's
    substitution value as a BODS expression: a varchar var needs a quoted
    string literal (verified via HAR capture against qetl111, which always
    sends e.g. $G_BUSINESS_DATE='31-Jul-2026'), an int/float var needs a
    bare numeric literal. The real server doesn't error on a bad expression
    it silently drops the substitution and falls back to the job's compiled
    default, which is exactly how the "custom variable didn't get passed"
    bug went unnoticed. This mock fails loudly instead, so a client
    regression here is caught by tests rather than only in production.
    """


def _unwrap_bods_literal(literal: str) -> str:
    """Inverse of DSRestClient._bods_literal: bare numeric text passes
    through, a quoted string literal is unquoted and '' un-escaped to '."""
    if _NUMERIC_LITERAL_RE.match(literal):
        return literal
    if _QUOTED_LITERAL_RE.match(literal):
        return literal[1:-1].replace("''", "'")
    raise InvalidGlobalVariableLiteral(
        f"global variable value {literal!r} is not a valid BODS expression "
        "(expected a bare number or a single-quoted string literal)",
    )


def _parse_global_variables(body: str) -> dict[str, str]:
    """Extract <globalVariables><variable name="...">value</variable>...</globalVariables>
    from a RunBatchJobRequest body -- mirrors how DSRestClient.trigger_job serializes
    `job_params`. Captured per-run so a test can assert the client sent exactly the
    (already-substituted) variables it expected via GET /debug/runs/{rid} below.

    Values are BODS expressions on the wire (see InvalidGlobalVariableLiteral);
    this unwraps them back to the plain value tests compare against, and
    raises if a value isn't validly quoted/numeric -- catching a client that
    regresses to sending an unquoted string, per-variable, rather than
    silently accepting it the way the real DataServices_Server would."""
    block = re.search(r"<globalVariables>(.*?)</globalVariables>", body, re.S)
    if not block:
        return {}
    return {
        name: _unwrap_bods_literal(unescape(value))
        for name, value in re.findall(r'<variable name="([^"]*)">(.*?)</variable>', block.group(1), re.S)
    }


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
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        # Test-only inspection endpoint (not part of the real DataServices_Server
        # WSDL): lets a live e2e/integration test confirm the exact globalVariables
        # DSRestClient.trigger_job sent for a given run -- e.g. that a
        # `{{custom_variable}}` placeholder in a job's job_params was actually
        # resolved before reaching the SOAP call, not just persisted as a literal.
        if path == "/debug/last-run":
            if _LAST_RUN_ID is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "no run yet"})
                return
            run = _JOB_RUNS[_LAST_RUN_ID]
            self._send_json(
                HTTPStatus.OK,
                {"run_id": _LAST_RUN_ID, "job_name": run["job_name"], "variables": run.get("variables", {})},
            )
            return
        if path.startswith("/debug/runs/"):
            run_id = path[len("/debug/runs/"):]
            run = _JOB_RUNS.get(run_id)
            if run is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._send_json(
                HTTPStatus.OK,
                {"job_name": run["job_name"], "variables": run.get("variables", {})},
            )
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
        try:
            variables = _parse_global_variables(body)
        except InvalidGlobalVariableLiteral as exc:
            self._send_fault(HTTPStatus.BAD_REQUEST, str(exc))
            return
        global _LAST_RUN_ID
        run_id = str(uuid.uuid4().int % 100000)
        _JOB_RUNS[run_id] = {
            "job_name": job_name, "polls_seen": 0,
            "variables": variables,
        }
        _LAST_RUN_ID = run_id
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
