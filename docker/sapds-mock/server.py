from __future__ import annotations

import json
import os
import re
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


HOST = os.getenv("SAPDS_MOCK_HOST", "0.0.0.0")
PORT = int(os.getenv("SAPDS_MOCK_PORT", "8444"))
USER = os.getenv("SAPDS_MOCK_USER", "administrator")
PASSWORD = os.getenv("SAPDS_MOCK_PASSWORD", "Password1")
CERT_FILE = os.getenv("SAPDS_MOCK_CERT_FILE", "/certs/sapds-mock.crt")
KEY_FILE = os.getenv("SAPDS_MOCK_KEY_FILE", "/certs/sapds-mock.key")
TOKEN = "mock-sapds-token"

# Batch jobs that can be triggered via POST /BatchJob/{repository}/{job_name}/Execute.
# Each entry's outcome is reached after JOB_POLLS_TO_TERMINAL polls of
# GET /BatchJob/{repository}/status/{run_id} -- first poll(s) return "Running"
# to exercise the client's poll loop, not just its terminal-status parsing.
SCHEDULABLE_JOBS = {
    "DS_NIGHTLY_LOAD": "Completed",
    "DS_BAD_LOAD": "Error",
}
JOB_POLLS_TO_TERMINAL = 2

# run_id -> {"job_name": str, "polls_seen": int}
_JOB_RUNS: dict[str, dict] = {}
_next_run_id = [0]


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

        status_match = re.fullmatch(r"/BatchJob/([^/]+)/status/([^/]+)", path)
        if status_match:
            _repository, run_id = status_match.groups()
            run = _JOB_RUNS.get(run_id)
            if run is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"run {run_id} not found"})
                return
            run["polls_seen"] += 1
            if run["polls_seen"] < JOB_POLLS_TO_TERMINAL:
                self._send_json(HTTPStatus.OK, {"id": run_id, "status": "Running"})
            else:
                terminal_status = SCHEDULABLE_JOBS[run["job_name"]]
                self._send_json(HTTPStatus.OK, {"id": run_id, "status": terminal_status})
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

        trigger_match = re.fullmatch(r"/BatchJob/([^/]+)/([^/]+)/Execute", path)
        if trigger_match:
            if not self._require_token():
                return
            _repository, job_name = trigger_match.groups()
            if job_name not in SCHEDULABLE_JOBS:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": f"job {job_name} not found"})
                return
            _next_run_id[0] += 1
            run_id = f"run-{_next_run_id[0]}"
            _JOB_RUNS[run_id] = {"job_name": job_name, "polls_seen": 0}
            self._send_json(HTTPStatus.OK, {"id": run_id})
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
