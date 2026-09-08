from __future__ import annotations

import json
import os
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


HOST = os.getenv("SAPDS_MOCK_HOST", "0.0.0.0")
PORT = int(os.getenv("SAPDS_MOCK_PORT", "8444"))
USER = os.getenv("SAPDS_MOCK_USER", "administrator")
PASSWORD = os.getenv("SAPDS_MOCK_PASSWORD", "Password1")
CERT_FILE = os.getenv("SAPDS_MOCK_CERT_FILE", "/certs/sapds-mock.crt")
KEY_FILE = os.getenv("SAPDS_MOCK_KEY_FILE", "/certs/sapds-mock.key")
TOKEN = "mock-sapds-token"

# Batch jobs that can be triggered via the legacy servlet form POST
# /DataServices/servlet/AwBatchJobExecute (application/x-www-form-urlencoded,
# matching the live 2026-09-03 DevTools capture -- see
# DSRestClient.trigger_job's docstring). The client's submitted GUID form
# field is used as the run id key, since the client no longer reads a
# server-generated id out of the (HTML, not JSON) response body.
#
# Status is checked via GET /DataServices/servlet/AwBatchJobHistory
# (matching the live 2026-09-08 capture -- see
# DSRestClient.get_job_status's docstring), keyed by JobName since there's
# no run-id-keyed status endpoint on the real app. Each entry's outcome is
# reached after JOB_POLLS_TO_TERMINAL polls -- earlier polls return an
# empty listing (simulating the run not yet appearing in history) to
# exercise the client's "no matching row -> keep polling" path, not just
# its terminal-icon parsing.
SCHEDULABLE_JOBS = {
    "DS_NIGHTLY_LOAD": "green",
    "DS_BAD_LOAD": "red",
}
JOB_POLLS_TO_TERMINAL = 2

# run_id (client-submitted GUID) -> {"job_name": str, "polls_seen": int}
_JOB_RUNS: dict[str, dict] = {}


def _history_html(rows: list[tuple[str, str]]) -> str:
    """rows: list of (job_name, icon_color). Matches the real
    AwBatchJobHistory row structure closely enough for DSRestClient's
    regex-based scraping (class=tablerow lowercase, a bare
    class="cell" nowrap job-name cell, a circ{color}.gif status icon)."""
    row_html = "".join(
        f"""<TR  class=tablerow ><TD ><input type="checkbox" Name="CBG1" Value="1"></TD>
        <TD  class="cell" nowrap align=CENTER><IMG alt='' src='../images/circ{icon}.gif'></TD>
        <TD  class="cell" nowrap>{job_name}</TD></TR>"""
        for job_name, icon in rows
    )
    return f"<html><body><TABLE><TBODY>{row_html}</TBODY></TABLE></body></html>"


class SAPDSMockHandler(BaseHTTPRequestHandler):
    server_version = "ATOMSAPDSMock/1.0"

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

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _require_token(self) -> bool:
        if self.headers.get("X-DS-SessionToken") == TOKEN:
            return True
        self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "missing or invalid X-DS-SessionToken"})
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        if not self._require_token():
            return

        if path == "/DataServices/servlet/AwBatchJobHistory":
            query = parse_qs(parsed.query)
            job_name = query.get("JobName", [""])[0]
            run = None
            for candidate in reversed(list(_JOB_RUNS.values())):
                if candidate["job_name"] == job_name:
                    run = candidate
                    break
            if run is None:
                self._send_html(HTTPStatus.OK, _history_html([]))
                return
            run["polls_seen"] += 1
            if run["polls_seen"] < JOB_POLLS_TO_TERMINAL:
                self._send_html(HTTPStatus.OK, _history_html([]))
            else:
                icon = SCHEDULABLE_JOBS[job_name]
                self._send_html(HTTPStatus.OK, _history_html([(job_name, icon)]))
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/logon":
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
                return
            if payload.get("userName") != USER or payload.get("password") != PASSWORD:
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid credentials"})
                return
            self._send_json(HTTPStatus.OK, {"success": True}, headers={"X-DS-SessionToken": TOKEN})
            return

        if path == "/Logout":
            self._send_json(HTTPStatus.OK, {"success": True})
            return

        if path == "/DataServices/servlet/AwBatchJobExecute":
            if not self._require_token():
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length else b""
            form = {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
            job_name = form.get("JobName", "")
            run_id = form.get("GUID", "")
            if job_name not in SCHEDULABLE_JOBS or not run_id:
                self._send_html(HTTPStatus.NOT_FOUND, f"<html>job {job_name} not found</html>")
                return
            _JOB_RUNS[run_id] = {"job_name": job_name, "polls_seen": 0}
            self._send_html(HTTPStatus.OK, "<html>submitted</html>")
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), SAPDSMockHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    print(f"SAP DS mock listening on https://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
