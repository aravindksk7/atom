"""Drives scripts/ci/post-gitlab-status.sh against a stubbed `curl` on PATH.

The script talks to GitLab's REST API, so the only way to pin its request
shapes without a live GitLab is to intercept curl. The stub records every
call's full argv and replays canned (http_code, body) pairs in call order.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "post-gitlab-status.sh"

CURL_STUB = """#!/usr/bin/env bash
# Records argv, replays canned responses keyed by a call counter.
n=$(cat "${STUB_DIR}/count" 2>/dev/null || echo 0)
n=$((n + 1))
echo "${n}" > "${STUB_DIR}/count"
{
  printf 'CALL'
  for a in "$@"; do printf '\\t%s' "$a"; done
  printf '\\n'
} >> "${STUB_LOG}"
out_file=""
prev=""
for a in "$@"; do
  # The script sends the MR-comment body from a temp file it deletes on exit,
  # so snapshot it here while it still exists.
  case "$a" in
    body@*) cp "${a#body@}" "${STUB_DIR}/last_body" 2>/dev/null || true ;;
  esac
  if [ "${prev}" = "-o" ]; then out_file="$a"; fi
  prev="$a"
done
if [ -n "${out_file}" ]; then
  if [ -f "${STUB_DIR}/body${n}" ]; then cat "${STUB_DIR}/body${n}" > "${out_file}";
  else printf '{}' > "${out_file}"; fi
fi
if [ -f "${STUB_DIR}/code${n}" ]; then cat "${STUB_DIR}/code${n}"; else printf '200'; fi
exit 0
"""

GITLAB_ENV = {
    "CI_API_V4_URL": "https://gitlab.example.com/api/v4",
    "CI_PROJECT_ID": "42",
    "CI_JOB_TOKEN": "s3kr1t-job-token",
    "CI_COMMIT_SHA": "abc123def456",
    "ATOM_API_URL": "http://atom.local",
}


def _posix(path) -> str:
    """C:\\atom\\x -> /c/atom/x (Git-bash understands only the latter)."""
    text = str(path).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _find_bash() -> str | None:
    """A bash that can actually see this repo's files.

    On Windows, system32\\bash.exe is WSL and cannot read C:\\atom, so each
    candidate is probed with a real file test before being accepted.
    """
    candidates = []
    if os.name == "nt":
        candidates += [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ]
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    for candidate in candidates:
        if not os.path.exists(candidate):
            continue
        probe = subprocess.run(
            [candidate, "-c", 'test -f "$1" && echo ok', "bash", _posix(SCRIPT)],
            capture_output=True,
            text=True,
        )
        if probe.stdout.strip() == "ok":
            return candidate
    return None


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no bash that can read the repo")


class Result:
    def __init__(self, proc: subprocess.CompletedProcess, calls: list[list[str]], stub_dir: Path):
        self.returncode = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.calls = calls
        self.stub_dir = stub_dir

    def call(self, index: int) -> list[str]:
        return self.calls[index]

    def method(self, index: int) -> str:
        argv = self.calls[index]
        if "-G" in argv:
            return "GET"
        for i, arg in enumerate(argv):
            if arg == "-X":
                return argv[i + 1]
        return "GET"

    def url(self, index: int) -> str:
        return next(a for a in self.calls[index] if a.startswith("http"))

    def data(self, index: int) -> dict[str, str]:
        """--data-urlencode key=value pairs of call `index`."""
        argv = self.calls[index]
        out = {}
        for i, arg in enumerate(argv):
            if arg == "--data-urlencode" and "=" in argv[i + 1]:
                key, value = argv[i + 1].split("=", 1)
                out[key] = value
        return out


def run_script(tmp_path, args, env=None, responses=(), note_body=None) -> Result:
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    curl = stub_dir / "curl"
    curl.write_text(CURL_STUB, newline="\n")
    curl.chmod(0o755)
    for i, (code, body) in enumerate(responses, start=1):
        (stub_dir / f"code{i}").write_text(str(code), newline="\n")
        (stub_dir / f"body{i}").write_text(body, newline="\n")
    log = stub_dir / "log"
    log.write_text("")

    full_env = dict(os.environ)
    full_env.update(GITLAB_ENV if env is None else env)
    full_env["STUB_DIR"] = _posix(stub_dir)
    full_env["STUB_LOG"] = _posix(log)

    argv = list(args)
    if note_body is not None:
        note = tmp_path / "summary.md"
        note.write_text(note_body, newline="\n")
        argv.append(_posix(note))

    proc = subprocess.run(
        [BASH, "-c", 'export PATH="$STUB_DIR:$PATH"; bash "$1" "${@:2}"', "bash", _posix(SCRIPT)]
        + argv,
        capture_output=True,
        text=True,
        env=full_env,
    )
    calls = [line.split("\t")[1:] for line in log.read_text().splitlines() if line]
    return Result(proc, calls, stub_dir)


def test_posts_commit_status_with_namespaced_context(tmp_path):
    result = run_script(
        tmp_path,
        ["pending", "selection", "Nightly Regression", "Running via Atom..."],
        responses=[(201, "{}")],
    )
    assert result.returncode == 0
    assert len(result.calls) == 1
    assert result.method(0) == "POST"
    assert result.url(0) == (
        "https://gitlab.example.com/api/v4/projects/42/statuses/abc123def456"
    )
    assert result.data(0) == {
        "state": "pending",
        "context": "atom/selection/nightly-regression",
        "target_url": "http://atom.local",
        "description": "Running via Atom...",
    }
    assert "JOB-TOKEN: s3kr1t-job-token" in result.call(0)


def test_skips_entirely_when_not_a_gitlab_job(tmp_path):
    env = dict(GITLAB_ENV, CI_JOB_TOKEN="")
    result = run_script(tmp_path, ["success", "selection", "x", "ok"], env=env)
    assert result.returncode == 0
    assert result.calls == []
    assert "GitLab CI environment not detected" in result.stderr


def test_skips_when_commit_sha_missing(tmp_path):
    env = dict(GITLAB_ENV, CI_COMMIT_SHA="")
    result = run_script(tmp_path, ["success", "selection", "x", "ok"], env=env)
    assert result.returncode == 0
    assert result.calls == []


def test_commit_status_http_error_warns_but_exits_zero(tmp_path):
    result = run_script(
        tmp_path,
        ["success", "selection", "x", "ok"],
        responses=[(403, '{"message":"403 Forbidden"}')],
    )
    assert result.returncode == 0
    assert "HTTP 403" in result.stderr
    assert "403 Forbidden" in result.stderr


def test_job_token_never_appears_in_output(tmp_path):
    result = run_script(
        tmp_path,
        ["success", "selection", "x", "ok"],
        responses=[(500, '{"message":"boom"}')],
    )
    assert "s3kr1t-job-token" not in result.stdout
    assert "s3kr1t-job-token" not in result.stderr


def test_sequence_target_id_context(tmp_path):
    result = run_script(
        tmp_path, ["failed", "sequence", "17", "1 failed"], responses=[(201, "{}")]
    )
    assert result.data(0)["context"] == "atom/sequence/17"
    assert result.data(0)["state"] == "failed"
