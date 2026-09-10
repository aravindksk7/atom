"""Pins run-atom-target.sh's two calls into post-gitlab-status.sh.

Runs the real script with stubs for `atom` (canned JSON + chosen exit code),
`curl` (fails the summary fetch so the README/git section is a no-op), and a
fake sibling post-gitlab-status.sh that just records its argv.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "run-atom-target.sh"

RECORDER = """#!/usr/bin/env bash
{ printf 'CALL'; for a in "$@"; do printf '\\t%s' "$a"; done; printf '\\n'; } >> "${STUB_LOG}"
exit 0
"""

ATOM_STUB = """#!/usr/bin/env bash
cat "${STUB_DIR}/atom_output"
exit "${STUB_GATE_CODE:-0}"
"""

# The summary fetch must fail so the git/commit/push section never runs in a test.
CURL_STUB = """#!/usr/bin/env bash
exit 22
"""


def _posix(path) -> str:
    text = str(path).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _find_bash() -> str | None:
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


def run_target(tmp_path, args, gate_code=0, atom_output=None):
    """Copy run-atom-target.sh next to a fake post-gitlab-status.sh and run it."""
    work = tmp_path / "ci"
    work.mkdir()
    shutil.copy(SCRIPT, work / "run-atom-target.sh")
    (work / "post-gitlab-status.sh").write_text(RECORDER, newline="\n")
    (work / "atom").write_text(ATOM_STUB, newline="\n")
    (work / "curl").write_text(CURL_STUB, newline="\n")
    for name in ("post-gitlab-status.sh", "atom", "curl"):
        (work / name).chmod(0o755)
    if atom_output is None:
        atom_output = json.dumps({
            "run_id": "run-1", "verdict": "PASSED", "exit_code": 0,
            "passed": 3, "failed": 0, "error": 0,
        })
    (work / "atom_output").write_text(atom_output, newline="\n")
    log = work / "log"
    log.write_text("")

    env = dict(os.environ)
    env.update({
        "STUB_DIR": _posix(work),
        "STUB_LOG": _posix(log),
        "STUB_GATE_CODE": str(gate_code),
        "ATOM_API_URL": "http://atom.local",
        "ATOM_API_TOKEN": "token",
    })
    proc = subprocess.run(
        [BASH, "-c", 'export PATH="$STUB_DIR:$PATH"; bash "$STUB_DIR/run-atom-target.sh" "$@"',
         "bash"] + list(args),
        capture_output=True,
        text=True,
        env=env,
    )
    calls = [line.split("\t")[1:] for line in log.read_text().splitlines() if line]
    return proc, calls


def test_posts_pending_before_launching(tmp_path):
    proc, calls = run_target(tmp_path, ["selection", "Nightly Regression", "dev"])
    assert calls[0][:3] == ["pending", "selection", "Nightly Regression"]
    assert calls[0][3] == "Running via Atom..."


def test_posts_success_with_counts_after_a_passing_run(tmp_path):
    proc, calls = run_target(tmp_path, ["selection", "Nightly Regression", "dev"], gate_code=0)
    assert proc.returncode == 0
    assert calls[1][:3] == ["success", "selection", "Nightly Regression"]
    assert calls[1][3] == "3 passed, 0 failed, 0 error"


@pytest.mark.parametrize(
    "gate_code,expected_state",
    [(0, "success"), (1, "failed"), (2, "canceled"), (3, "failed"), (4, "failed"),
     (5, "failed"), (6, "failed")],
)
def test_gate_code_maps_to_gitlab_state(tmp_path, gate_code, expected_state):
    proc, calls = run_target(tmp_path, ["sequence", "17"], gate_code=gate_code)
    assert proc.returncode == gate_code, "gate code must pass through untouched"
    assert calls[1][0] == expected_state


def test_non_json_atom_output_falls_back_to_state_as_description(tmp_path):
    """On timeout the CLI prints a bare run id, not JSON (see the comment in the script)."""
    proc, calls = run_target(tmp_path, ["selection", "s1"], gate_code=6, atom_output="run-9\n")
    assert proc.returncode == 6
    assert calls[1][0] == "failed"
    assert calls[1][3] == "failed"
