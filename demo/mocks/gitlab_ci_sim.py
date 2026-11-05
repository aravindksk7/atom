"""A simulated GitLab CI pipeline for the demo, clearly labelled as simulated.

It stands in for a GitLab SaaS runner: pressing "Run pipeline" runs ATOM's real
scripts/ci/run-atom-target.sh, with the CI_* variables GitLab would set, inside a
throwaway git repository. The pipeline passes or fails on the script's real exit code.
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(os.environ.get("GITLAB_SIM_SCRIPT", "/app/scripts/ci/run-atom-target.sh"))
ATOM_API_URL = os.environ.get("ATOM_API_URL", "http://atom:8055")
PROJECT = "demo-group/atom-regression"
PIPELINE_ID = "4821"
COMMIT_SHA = "9f3c1e7ab24d6c0815e2f7d3a96b4c1d0e8f5a72"
BRANCH = "release/fee-model-cr-2041"
PORT = int(os.environ.get("GITLAB_SIM_PORT", "8090"))

README = (
    "# ATOM regression\n\n## CI/CD Job Status\n\n"
    "<!-- ATOM:JOB-STATUS:START -->\n_No CI-triggered run yet._\n<!-- ATOM:JOB-STATUS:END -->\n"
)
ATOM_SHIM = '#!/bin/sh\nexec python -c "from etl_framework.cli.app import app; app()" "$@"\n'


def pipeline_url(base_url: str) -> str:
    return f"{base_url}/{PROJECT}/-/pipelines/{PIPELINE_ID}"


class Pipeline:
    def __init__(self) -> None:
        self.status = "created"
        self.exit_code: int | None = None
        self.log: list[str] = []
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        with self._lock:
            return {"status": self.status, "exit_code": self.exit_code, "log": list(self.log)}

    def append(self, line: str) -> None:
        with self._lock:
            self.log.append(line)

    def finish(self, status: str, exit_code: int | None) -> None:
        with self._lock:
            self.status, self.exit_code = status, exit_code

    def start(self, token: str, target_id: str, base_url: str, run_date: str) -> bool:
        with self._lock:
            if self.status == "running":
                return False
            self.status, self.exit_code, self.log = "running", None, []
        threading.Thread(
            target=self._run, args=(token, target_id, base_url, run_date), daemon=True,
        ).start()
        return True

    def _run(self, token: str, target_id: str, base_url: str, run_date: str) -> None:
        try:
            with tempfile.TemporaryDirectory() as workspace:
                self._prepare_repository(Path(workspace))
                self._stream(token, target_id, base_url, run_date, Path(workspace))
        except Exception as exc:  # noqa: BLE001 - the page must show why a job died
            self.append(f"ERROR: {exc}")
            self.finish("failed", None)

    def _prepare_repository(self, workspace: Path) -> None:
        origin, clone = workspace / "origin.git", workspace / "repo"
        for command in (
            ["git", "init", "--quiet", "--bare", "-b", BRANCH, str(origin)],
            ["git", "init", "--quiet", "-b", BRANCH, str(clone)],
            ["git", "-C", str(clone), "remote", "add", "origin", str(origin)],
        ):
            subprocess.run(command, check=True)
        (clone / "README.md").write_text(README, encoding="utf-8")
        for command in (
            ["git", "-C", str(clone), "add", "README.md"],
            ["git", "-C", str(clone), "-c", "user.name=demo", "-c", "user.email=demo@atom-demo.example",
             "commit", "--quiet", "-m", "demo"],
            ["git", "-C", str(clone), "push", "--quiet", "origin", f"HEAD:{BRANCH}"],
        ):
            subprocess.run(command, check=True)
        bin_dir = workspace / "bin"
        bin_dir.mkdir()
        (bin_dir / "atom").write_text(ATOM_SHIM, encoding="utf-8")
        (bin_dir / "atom").chmod(0o755)
        # A Windows checkout has CRLF line endings, which bash cannot run. A real Linux
        # runner checks out LF, so run LF copies and leave the repository untouched.
        scripts = workspace / "ci-scripts"
        scripts.mkdir()
        for source in SCRIPT.parent.iterdir():
            if source.is_file():
                (scripts / source.name).write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))

    def _stream(self, token: str, target_id: str, base_url: str, run_date: str, workspace: Path) -> None:
        env = {
            **os.environ,
            "PATH": f"{workspace / 'bin'}:{os.environ['PATH']}",
            "ATOM_API_URL": ATOM_API_URL,
            "ATOM_API_TOKEN": token,
            "ATOM_POLL_INTERVAL_SECONDS": "3",
            "CI_COMMIT_SHA": COMMIT_SHA,
            "CI_COMMIT_REF_NAME": BRANCH,
            "CI_PIPELINE_URL": pipeline_url(base_url),
            "CI_PIPELINE_SOURCE": "push",
            "RUN_DATE": run_date,
        }
        self.append(f"$ bash scripts/ci/run-atom-target.sh sequence {target_id} qa prod")
        process = subprocess.Popen(
            ["bash", str(workspace / "ci-scripts" / SCRIPT.name), "sequence", target_id, "qa", "prod"],
            cwd=workspace / "repo", env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        assert process.stdout is not None
        for line in process.stdout:
            self.append(line.rstrip())
        code = process.wait()
        self.append(f"Job finished with exit code {code}")
        self.finish("passed" if code == 0 else "failed", code)


PIPELINE = Pipeline()

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Simulated GitLab CI</title>
<style>
  body { margin: 0; background: #101114; color: #ececef; font: 16px/1.5 system-ui, sans-serif; }
  header { padding: 18px 40px; background: #1f1e24; border-bottom: 1px solid #3a383f; font-size: 20px; font-weight: 600; }
  header small { color: #f5c26b; font-weight: 500; margin-left: 12px; font-size: 15px; }
  main { padding: 30px 40px; max-width: 1500px; margin: auto; }
  h1 { font-size: 28px; margin: 0 0 6px; }
  .meta { color: #a4a3a8; margin-bottom: 22px; }
  code { color: #c9d1d9; }
  button { background: #1f75cb; color: #fff; border: 0; border-radius: 6px; padding: 11px 20px; font-size: 16px; cursor: pointer; }
  button:disabled { background: #3a383f; color: #89888d; cursor: default; }
  .job { display: inline-flex; align-items: center; gap: 12px; margin: 22px 0; padding: 12px 18px; border: 1px solid #3a383f; border-radius: 22px; }
  .status { padding: 3px 11px; border-radius: 12px; font-size: 14px; font-weight: 700; background: #3a383f; }
  .status.running { background: #1f75cb; } .status.passed { background: #108548; } .status.failed { background: #dd2b0e; }
  pre { background: #000; color: #d7d7d7; padding: 18px; border-radius: 6px; height: 500px; overflow: auto; font-size: 14px; white-space: pre-wrap; word-break: break-all; }
</style></head><body>
<header>GitLab CI <small>Simulated for this demo: not a real GitLab server</small></header>
<main>
  <h1 data-testid="pipeline-title">Pipeline #__PIPELINE__</h1>
  <div class="meta"><code>__PROJECT__</code> &middot; branch <code>__BRANCH__</code> &middot; commit <code>__SHORT__</code></div>
  <button data-testid="run-pipeline" onclick="run()">Run pipeline</button>
  <div><div class="job" data-testid="pipeline-job"><span>regression &middot; atom-regression</span>
    <span class="status" data-testid="pipeline-status">created</span></div></div>
  <pre data-testid="pipeline-log"></pre>
</main>
<script>
  async function run() {
    await fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: location.origin }) });
    poll();
  }
  async function poll() {
    const state = await (await fetch('/api/state')).json();
    const status = document.querySelector('[data-testid=pipeline-status]');
    status.textContent = state.status;
    status.className = 'status ' + state.status;
    const log = document.querySelector('[data-testid=pipeline-log]');
    log.textContent = state.log.join('\\n');
    log.scrollTop = log.scrollHeight;
    document.querySelector('[data-testid=run-pipeline]').disabled = state.status === 'running';
  }
  poll();
  setInterval(poll, 1500);
</script></body></html>
"""


def render_page() -> bytes:
    page = PAGE
    for name, value in (
        ("__PIPELINE__", PIPELINE_ID), ("__PROJECT__", PROJECT),
        ("__BRANCH__", BRANCH), ("__SHORT__", COMMIT_SHA[:8]),
    ):
        page = page.replace(name, html.escape(value))
    return page.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    # Set by the demo's global setup through POST /api/configure.
    token = ""
    target_id = ""
    run_date = ""

    def log_message(self, *args: object) -> None:
        pass

    def _json(self, status: HTTPStatus, body: object) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._json(HTTPStatus.OK, PIPELINE.snapshot())
            return
        data = render_page()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/configure":
            Handler.token = body["token"]
            Handler.target_id = str(body["target_id"])
            Handler.run_date = body.get("run_date", "")
            self._json(HTTPStatus.OK, {"configured": True})
        elif self.path == "/api/run":
            if not Handler.token:
                self._json(HTTPStatus.CONFLICT, {"error": "not configured"})
                return
            started = PIPELINE.start(
                Handler.token, Handler.target_id, body.get("base_url", ""), Handler.run_date,
            )
            self._json(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT, {"started": started})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})


def main() -> None:
    print(f"Simulated GitLab CI on port {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
