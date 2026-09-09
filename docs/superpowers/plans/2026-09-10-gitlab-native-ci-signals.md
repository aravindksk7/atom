# GitLab-Native CI Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an Atom run launched from GitLab CI report itself back into GitLab — a per-target Commit Status on the pipeline SHA (`pending` before the run, `success`/`failed`/`canceled` after) and, on merge-request pipelines, one sticky MR comment per target carrying the run's markdown summary.

**Architecture:** One new bash script, `scripts/ci/post-gitlab-status.sh`, talks to GitLab's REST API with the job's own `CI_JOB_TOKEN`. `scripts/ci/run-atom-target.sh` calls it twice (before launch, after the run). Everything is best-effort: the new script handles all its own errors and always exits 0, so it can never change the pipeline verdict, which stays driven solely by `atom run`'s gate code. **No Atom server, API, database, or CLI changes.**

**Tech Stack:** bash 4+ (`curl`, `python3` for JSON parsing — both already assumed by `run-atom-target.sh`), pytest for the stubbed-curl unit suite, Playwright for the frontend modal assertion.

**Spec:** `docs/superpowers/specs/2026-09-10-gitlab-native-ci-signals-design.md`

## Global Constraints

- **The new script must never change the pipeline's exit code.** `post-gitlab-status.sh` handles every failure internally, warns to stderr, and always `exit 0`. `run-atom-target.sh` still ends with `exit "${gate_code}"` and that value is unchanged by this work.
- **`CI_JOB_TOKEN` must never be logged.** No `set -x`, no `curl -v`. Error messages print only the HTTP status code and GitLab's own JSON response body — never the request line, headers, or full curl argv.
- **No Atom-side credential storage.** The only credential used is GitLab's per-job `CI_JOB_TOKEN`, which GitLab injects automatically into every CI job. No new CI/CD variable is required from the user.
- **No server, schema, API, or CLI changes.** This sub-project touches only `scripts/ci/`, its tests, the frontend CI modal's help copy, and `docs/cli.md`.
- **Commit-status states are exactly GitLab's vocabulary:** `pending`, `success`, `failed`, `canceled` (American spelling, one `l` — GitLab rejects `cancelled`).
- **Commit-status context is namespaced per target:** `atom/<target_type>/<slug>`, e.g. `atom/selection/nightly-regression`, `atom/sequence/17`.
- **MR-comment marker format is exactly:** `<!-- ATOM:CI-COMMENT:<target_type>:<target> -->` (HTML comment, invisible in GitLab's rendered markdown, same convention as README's `ATOM:JOB-STATUS` markers).
- Python floor is 3.11 (`pyproject.toml: requires-python = ">=3.11"`); tests live under `tests/` (`[tool.pytest.ini_options] testpaths = ["tests"]`).

---

## File Structure

| File | Change |
|---|---|
| `scripts/ci/post-gitlab-status.sh` | **New.** The whole GitLab integration: commit status + sticky MR comment. Self-contained, always exits 0. |
| `scripts/ci/run-atom-target.sh` | **Modify.** Two new call sites (pending before launch, final state after) + gate-code→state mapping + description extraction. |
| `tests/unit/test_post_gitlab_status.py` | **New.** Drives the real script under a stubbed `curl` on `PATH`, asserting request shapes and skip/warn behavior. |
| `tests/unit/test_run_atom_target_signals.py` | **New.** Drives `run-atom-target.sh` under stubbed `atom`/`curl` plus a recording fake sibling, pinning both call sites and the gate-code mapping. |
| `frontend/features/launch.js` | **Modify.** One extra static help line in the CI modal's data (`ciIntegrationModal.gitlabSignalsNote`). |
| `frontend/partials/tab-launch.html` | **Modify.** Render that note in the modal. |
| `frontend/index.html` | **Regenerated** by `npm run build:html` — never hand-edited. |
| `tests/e2e/40-live-docker-gitlab-retry.spec.ts` | **Modify.** Assert the new help line renders. |
| `docs/cli.md` | **Modify.** Document the GitLab signals behavior under the existing "GitLab CI example" section. |

**No database migration, no `api/` change, no `etl_framework/` change.**

### Why one script, not two

Commit status and MR comment share the same guard clauses, the same `curl`/`JOB-TOKEN` plumbing, the same "warn and carry on" error policy, and the same two identity arguments (`target_type`, `target`). Splitting them would duplicate all of that for no reviewer benefit. The file stays well under 100 lines.

---

## Task 1: `post-gitlab-status.sh` — commit status

**Files:**
- Create: `scripts/ci/post-gitlab-status.sh`
- Create: `tests/unit/test_post_gitlab_status.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: the CLI contract `post-gitlab-status.sh <state> <target_type> <target> <description> [note_body_file]`, used verbatim by Task 3. `<state>` ∈ `pending|success|failed|canceled`. Exit code is always `0`.

### Background you need

This repo is developed on Windows but the script is bash. The test suite therefore locates a **real** bash (Git for Windows' `C:\Program Files\Git\bin\bash.exe`, or plain `bash` on POSIX) and skips if none is usable. Note that Windows' `C:\WINDOWS\system32\bash.exe` (WSL) **cannot see** `C:\atom` paths, so the helper probes each candidate with a real file test and rejects the ones that fail — do not simplify this to `shutil.which("bash")`.

The tests work by putting a **stub `curl`** first on `PATH`. The stub appends its full argv (tab-separated) to `$STUB_LOG`, writes canned response bodies to whatever path follows `-o`, and prints a canned HTTP code (because the script calls curl with `-w '%{http_code}'`). Response N is served from `$STUB_DIR/codeN` / `$STUB_DIR/bodyN` where N is the 1-based call counter. This gives full request-shape assertions with no GitLab and no network.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_post_gitlab_status.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

`_find_bash` probes for the script's existence, so with no script at all the module would *skip* rather than fail — and a skip proves nothing. Create an empty placeholder first so the test really runs:

Run:
```bash
mkdir -p scripts/ci
printf '#!/usr/bin/env bash\nexit 0\n' > scripts/ci/post-gitlab-status.sh
python -m pytest tests/unit/test_post_gitlab_status.py -v
```

Expected: **FAIL**, with `IndexError: list index out of range` from `result.call(0)` — the placeholder made no curl calls.

If instead you see `SKIPPED [1] ... no bash that can read the repo`, no usable bash was found. Install Git for Windows (which provides `C:\Program Files\Git\bin\bash.exe`) or run on POSIX before continuing; note that Windows' bundled `C:\WINDOWS\system32\bash.exe` is WSL and deliberately rejected by the probe.

- [ ] **Step 3: Write the script**

Create `scripts/ci/post-gitlab-status.sh`:

```bash
#!/usr/bin/env bash
# Post GitLab-native CI signals for an Atom run: a Commit Status on the
# pipeline's SHA and, on merge-request pipelines, one sticky MR comment.
#
# Usage: post-gitlab-status.sh <state> <target_type> <target> <description> [note_body_file]
#   state       pending|success|failed|canceled  (GitLab's vocabulary, computed by the caller)
#   target_type selection|sequence
#   target      the selection/sequence id or name passed to `atom run`
#   description short text for GitLab's UI, e.g. "3 passed, 1 failed, 0 error"
#
# Best effort by design: every failure warns to stderr and this script always
# exits 0, so it can never change the pipeline's pass/fail verdict.
#
# CI_JOB_TOKEN is never echoed: no `set -x`, no `curl -v`; errors print only the
# HTTP status and GitLab's own response body.
set -uo pipefail

STATE="${1:?Usage: post-gitlab-status.sh <state> <target_type> <target> <description> [note_body_file]}"
TARGET_TYPE="${2:?Usage: post-gitlab-status.sh <state> <target_type> <target> <description> [note_body_file]}"
TARGET="${3:?Usage: post-gitlab-status.sh <state> <target_type> <target> <description> [note_body_file]}"
DESCRIPTION="${4:-}"
NOTE_BODY_FILE="${5:-}"

# Not a GitLab job (or a misconfigured one): skip entirely. Additive feature --
# never a reason to break a pipeline.
if [ -z "${CI_API_V4_URL:-}" ] || [ -z "${CI_PROJECT_ID:-}" ] \
   || [ -z "${CI_JOB_TOKEN:-}" ] || [ -z "${CI_COMMIT_SHA:-}" ]; then
  echo "warning: GitLab CI environment not detected; skipping GitLab status/comment" >&2
  exit 0
fi

api="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}"
# Namespaced per target so a selection and a sequence on the same commit don't
# overwrite each other's status.
slug=$(printf '%s' "${TARGET}" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9._-' '-' \
       | sed 's/--*/-/g; s/^-//; s/-$//')
response=$(mktemp)
payload=$(mktemp)
trap 'rm -f "${response}" "${payload}"' EXIT

http=$(curl -sS -o "${response}" -w '%{http_code}' -X POST \
  "${api}/statuses/${CI_COMMIT_SHA}" \
  -H "JOB-TOKEN: ${CI_JOB_TOKEN}" \
  --data-urlencode "state=${STATE}" \
  --data-urlencode "context=atom/${TARGET_TYPE}/${slug}" \
  --data-urlencode "target_url=${ATOM_API_URL:-}" \
  --data-urlencode "description=${DESCRIPTION}" 2>/dev/null) || http=000
case "${http}" in
  2*) ;;
  *) echo "warning: commit status POST failed (HTTP ${http}): $(head -c 500 "${response}")" >&2 ;;
esac

exit 0
```

This overwrites the Step-2 placeholder. Make it executable (and record the bit in git, which matters on POSIX CI runners):

Run:
```bash
chmod +x scripts/ci/post-gitlab-status.sh
git update-index --add --chmod=+x scripts/ci/post-gitlab-status.sh
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_post_gitlab_status.py -v`

Expected: PASS.

Also run: `bash -n scripts/ci/post-gitlab-status.sh` (on Windows: `& "C:\Program Files\Git\bin\bash.exe" -n scripts/ci/post-gitlab-status.sh`)

Expected: no output, exit 0.

- [ ] **Step 5: Add the guard-clause and failure tests**

Append to `tests/unit/test_post_gitlab_status.py`:

```python
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
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/unit/test_post_gitlab_status.py -v`

Expected: 6 passed. (`test_job_token_never_appears_in_output` passes because errors print only `${http}` and the response body.)

- [ ] **Step 7: Commit**

```bash
git add scripts/ci/post-gitlab-status.sh tests/unit/test_post_gitlab_status.py
git commit -m "feat(ci): post a per-target GitLab commit status for Atom runs"
```

---

## Task 2: `post-gitlab-status.sh` — sticky MR comment

**Files:**
- Modify: `scripts/ci/post-gitlab-status.sh` (append after the commit-status block)
- Modify: `tests/unit/test_post_gitlab_status.py` (append tests)

**Interfaces:**
- Consumes: the `run_script`/`Result` helpers and `GITLAB_ENV` from Task 1's test module; the commit-status block from Task 1's script.
- Produces: the optional 5th argument `note_body_file` now does something. Marker format `<!-- ATOM:CI-COMMENT:<target_type>:<target> -->` — Task 3 relies on passing `/tmp/atom-run-summary.md` here.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_post_gitlab_status.py`:

```python
MR_ENV = dict(GITLAB_ENV, CI_MERGE_REQUEST_IID="7")
MARKER = "<!-- ATOM:CI-COMMENT:selection:Nightly Regression -->"
NOTES_MATCH = json.dumps([
    {"id": 3, "body": "some other reviewer comment"},
    {"id": 9, "body": MARKER + "\nstale summary"},
])
NOTES_NO_MATCH = json.dumps([{"id": 5, "body": "unrelated"}])


def test_updates_existing_sticky_comment_when_marker_found(tmp_path):
    result = run_script(
        tmp_path,
        ["success", "selection", "Nightly Regression", "3 passed, 0 failed, 0 error"],
        env=MR_ENV,
        responses=[(201, "{}"), (200, NOTES_MATCH), (200, "{}")],
        note_body="## Atom run\n\n3 passed\n",
    )
    assert result.returncode == 0
    assert len(result.calls) == 3
    assert result.method(1) == "GET"
    assert result.url(1) == (
        "https://gitlab.example.com/api/v4/projects/42/merge_requests/7/notes"
    )
    assert result.method(2) == "PUT"
    assert result.url(2) == (
        "https://gitlab.example.com/api/v4/projects/42/merge_requests/7/notes/9"
    )


def test_creates_sticky_comment_when_marker_absent(tmp_path):
    result = run_script(
        tmp_path,
        ["failed", "selection", "Nightly Regression", "2 passed, 1 failed, 0 error"],
        env=MR_ENV,
        responses=[(201, "{}"), (200, NOTES_NO_MATCH), (201, "{}")],
        note_body="## Atom run\n",
    )
    assert len(result.calls) == 3
    assert result.method(2) == "POST"
    assert result.url(2) == (
        "https://gitlab.example.com/api/v4/projects/42/merge_requests/7/notes"
    )


def test_comment_body_is_marker_then_summary(tmp_path):
    """The body goes out as --data-urlencode body@<tempfile>, which the script
    deletes on exit -- the curl stub snapshots it to `last_body` for us."""
    result = run_script(
        tmp_path,
        ["success", "selection", "Nightly Regression", "ok"],
        env=MR_ENV,
        responses=[(201, "{}"), (200, NOTES_NO_MATCH), (201, "{}")],
        note_body="## Atom run\n\nall good\n",
    )
    written = (result.stub_dir / "last_body").read_text()
    assert written.startswith(MARKER)
    assert "all good" in written


def test_branch_pipeline_skips_comment_silently(tmp_path):
    """No CI_MERGE_REQUEST_IID: commit status only, and no warning about it."""
    result = run_script(
        tmp_path,
        ["success", "selection", "x", "ok"],
        responses=[(201, "{}")],
        note_body="## Atom run\n",
    )
    assert len(result.calls) == 1
    assert result.stderr.strip() == ""


def test_missing_note_body_file_still_posts_commit_status(tmp_path):
    result = run_script(
        tmp_path, ["success", "selection", "x", "ok"], env=MR_ENV, responses=[(201, "{}")]
    )
    assert len(result.calls) == 1


def test_empty_note_body_file_skips_comment(tmp_path):
    result = run_script(
        tmp_path,
        ["success", "selection", "x", "ok"],
        env=MR_ENV,
        responses=[(201, "{}")],
        note_body="",
    )
    assert len(result.calls) == 1


def test_notes_list_failure_warns_and_exits_zero(tmp_path):
    result = run_script(
        tmp_path,
        ["success", "selection", "x", "ok"],
        env=MR_ENV,
        responses=[(201, "{}"), (500, "boom")],
        note_body="## Atom run\n",
    )
    assert result.returncode == 0
    assert len(result.calls) == 2
    assert "MR notes list failed" in result.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_post_gitlab_status.py -v`

Expected: the 6 Task-1 tests PASS; the new MR-comment tests FAIL (`assert len(result.calls) == 3` sees 1 — the script currently stops after the commit status).

- [ ] **Step 3: Implement the MR-comment block**

In `scripts/ci/post-gitlab-status.sh`, replace the trailing `exit 0` (the last line, immediately after the commit-status `case` block) with:

```bash
# --- Sticky MR comment ------------------------------------------------------
# Nothing to post, or not a merge-request pipeline (GitLab only sets
# CI_MERGE_REQUEST_IID on MR pipelines) -- skip silently, this is the common
# case for a branch push and is not a problem.
[ -n "${NOTE_BODY_FILE}" ] && [ -s "${NOTE_BODY_FILE}" ] || exit 0
[ -n "${CI_MERGE_REQUEST_IID:-}" ] || exit 0

# Invisible in rendered markdown, same convention as README's ATOM:JOB-STATUS.
marker="<!-- ATOM:CI-COMMENT:${TARGET_TYPE}:${TARGET} -->"
notes_url="${api}/merge_requests/${CI_MERGE_REQUEST_IID}/notes"

http=$(curl -sS -o "${response}" -w '%{http_code}' -G "${notes_url}" \
  -H "JOB-TOKEN: ${CI_JOB_TOKEN}" \
  --data-urlencode "per_page=100" 2>/dev/null) || http=000
case "${http}" in
  2*) ;;
  *) echo "warning: MR notes list failed (HTTP ${http}); skipping MR comment" >&2; exit 0 ;;
esac

note_id=$(python3 -c '
import json, sys
marker = sys.argv[1]
try:
    notes = json.load(sys.stdin)
except Exception:
    notes = []
for note in notes if isinstance(notes, list) else []:
    if marker in (note.get("body") or ""):
        print(note.get("id"))
        break
' "${marker}" < "${response}" 2>/dev/null) || note_id=""

{ printf '%s\n\n' "${marker}"; cat "${NOTE_BODY_FILE}"; } > "${payload}"

if [ -n "${note_id}" ]; then
  http=$(curl -sS -o "${response}" -w '%{http_code}' -X PUT "${notes_url}/${note_id}" \
    -H "JOB-TOKEN: ${CI_JOB_TOKEN}" \
    --data-urlencode "body@${payload}" 2>/dev/null) || http=000
else
  http=$(curl -sS -o "${response}" -w '%{http_code}' -X POST "${notes_url}" \
    -H "JOB-TOKEN: ${CI_JOB_TOKEN}" \
    --data-urlencode "body@${payload}" 2>/dev/null) || http=000
fi
case "${http}" in
  2*) ;;
  *) echo "warning: MR comment upsert failed (HTTP ${http}): $(head -c 500 "${response}")" >&2 ;;
esac

exit 0
```

Two details that matter:
- `--data-urlencode "body@${payload}"` reads the body from a file, so a multi-KB markdown summary with newlines, quotes, and backticks needs no shell escaping and never hits `ARG_MAX`.
- The marker match is done in `python3` reading the response from **stdin**, not from a path. On Git-for-Windows, bash's `mktemp` returns a POSIX path (`/tmp/tmp.xyz`) that native Windows `python3` cannot open; piping via stdin sidesteps the path translation entirely.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_post_gitlab_status.py -v`

Expected: 13 passed.

Also run: `bash -n scripts/ci/post-gitlab-status.sh`

Expected: no output, exit 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/ci/post-gitlab-status.sh tests/unit/test_post_gitlab_status.py
git commit -m "feat(ci): upsert one sticky MR comment per target with the run summary"
```

---

## Task 3: Wire the two call sites into `run-atom-target.sh`

**Files:**
- Modify: `scripts/ci/run-atom-target.sh` (currently 76 lines; insert after line 18 and before line 76)
- Create: `tests/unit/test_run_atom_target_signals.py`

**Interfaces:**
- Consumes: `post-gitlab-status.sh <state> <target_type> <target> <description> [note_body_file]` from Tasks 1–2.
- Produces: nothing consumed later. `run-atom-target.sh`'s own contract (`run-atom-target.sh <selection|sequence> <id_or_name> [environment]`, exit = `atom run`'s gate code) is unchanged.

### Gate code → GitLab state

`docs/cli.md` defines the `atom` CLI's exit codes: 0 passed, 1 failed, 2 cancelled, 3 error, 4 not found, 5 auth/connection, 6 timeout. GitLab's Commit Status vocabulary has no "error"/"not found"/"timeout", so everything that isn't a clean pass or a cancel maps to `failed`.

| gate_code | GitLab state |
|---|---|
| 0 | `success` |
| 2 | `canceled` |
| 1, 3, 4, 5, 6 | `failed` |

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_run_atom_target_signals.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_run_atom_target_signals.py -v`

Expected: FAIL — every test errors on `calls[0]` with `IndexError: list index out of range`, because `run-atom-target.sh` doesn't call the sibling script yet.

- [ ] **Step 3: Add the "pending" call site**

In `scripts/ci/run-atom-target.sh`, after line 18 (`echo "Launching ${TARGET_TYPE} ${TARGET} against ${ENVIRONMENT} via atom CLI..."`) and before line 20 (`stdout_file=$(mktemp)`), insert:

```bash

# Tell GitLab a run is starting. Best effort: post-gitlab-status.sh handles all
# of its own errors and always exits 0, so this cannot affect the pipeline.
bash "${script_dir}/post-gitlab-status.sh" pending "${TARGET_TYPE}" "${TARGET}" \
  "Running via Atom..."
```

- [ ] **Step 4: Add the final-state call site**

In the same file, replace the final line `exit "${gate_code}"` with:

```bash
# Map the CLI's gate code (docs/cli.md) onto GitLab's Commit Status vocabulary.
# GitLab has no error/not-found/timeout state, so everything that isn't a clean
# pass or a cancel reads as failed.
case "${gate_code}" in
  0) gitlab_state=success ;;
  2) gitlab_state=canceled ;;
  *) gitlab_state=failed ;;
esac
# --output json prints counts on the normal paths but a bare run id on timeout,
# so fall back to the state itself when the payload isn't JSON.
description=$(python3 -c '
import json, sys
data = json.load(sys.stdin)
print("{} passed, {} failed, {} error".format(
    data.get("passed") or 0, data.get("failed") or 0, data.get("error") or 0))
' < "${stdout_file}" 2>/dev/null) || description="${gitlab_state}"
bash "${script_dir}/post-gitlab-status.sh" "${gitlab_state}" "${TARGET_TYPE}" "${TARGET}" \
  "${description}" /tmp/atom-run-summary.md

exit "${gate_code}"
```

Note `/tmp/atom-run-summary.md` is exactly the path the existing summary fetch on line 50 writes to. When that fetch failed the file is absent, and `post-gitlab-status.sh`'s `[ -s ... ]` guard skips the comment step while still posting the commit status.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_atom_target_signals.py -v`

Expected: 10 passed (3 standalone tests + 7 parametrized cases of `test_gate_code_maps_to_gitlab_state`).

Also run: `bash -n scripts/ci/run-atom-target.sh`

Expected: no output, exit 0.

- [ ] **Step 6: Run the whole unit suite to check nothing regressed**

Run: `python -m pytest tests/unit -q`

Expected: no new failures relative to the pre-change baseline. (If the baseline already has failures unrelated to `scripts/ci/`, note them and move on — do not "fix" unrelated tests in this plan.)

- [ ] **Step 7: Commit**

```bash
git add scripts/ci/run-atom-target.sh tests/unit/test_run_atom_target_signals.py
git commit -m "feat(ci): report Atom run state back to GitLab from run-atom-target.sh"
```

---

## Task 4: CI/CD modal help line + docs

**Files:**
- Modify: `frontend/features/launch.js:1242-1266` (`openCiIntegrationModal`)
- Modify: `frontend/partials/tab-launch.html:1580-1603` (the CI modal)
- Modify: `tests/e2e/40-live-docker-gitlab-retry.spec.ts:24-30`
- Modify: `docs/cli.md` (after the "GitLab CI example" block, line 73)
- Regenerate: `frontend/index.html` via `npm run build:html`

**Interfaces:**
- Consumes: nothing from Tasks 1–3 at runtime — this is static help copy describing the behavior those tasks implemented.
- Produces: `ciIntegrationModal.gitlabSignalsNote` (string), rendered in the modal.

**Important:** `frontend/index.html` is **generated** from `frontend/index.template.html` + `frontend/partials/*.html` by `scripts/build-html.js`. Edit the partial, then run the build. Never hand-edit `frontend/index.html`.

- [ ] **Step 1: Add the note to the modal's data**

In `frontend/features/launch.js`, inside `openCiIntegrationModal`, extend the `this.ciIntegrationModal = { ... }` assignment (currently lines 1259–1264) to:

```javascript
      this.ciIntegrationModal = {
        targetId: resolved.id,
        targetName: resolved.name,
        targetTypeLabel: label,
        yamlSnippet: yaml,
        gitlabSignalsNote: 'The job posts a GitLab commit status (context '
          + `atom/${targetType}/<name>) on every pipeline. On merge-request `
          + 'pipelines it also keeps one summary comment up to date; branch '
          + 'pipelines get the commit status only. No extra CI/CD variable is '
          + 'needed — the job\'s built-in CI_JOB_TOKEN is used.',
      };
```

- [ ] **Step 2: Render it in the modal**

In `frontend/partials/tab-launch.html`, inside the `<div class="space-y-3 text-sm">` block, after the STEP 3 `<div>` (which ends with the "Copy snippet" button's `</div>` on line 1597), insert:

```html
      <div>
        <div class="label">WHAT YOU'LL SEE IN GITLAB</div>
        <p class="text-muted gitlab-signals-note" x-text="ciIntegrationModal.gitlabSignalsNote"></p>
      </div>
```

- [ ] **Step 3: Rebuild the generated HTML**

Run: `npm run build:html`

Expected output: `Built .../frontend/index.html from .../frontend/index.template.html + N partials`

Verify the note reached the generated file:

Run: `rg -n "gitlab-signals-note" frontend/index.html`

Expected: one match.

- [ ] **Step 4: Update the e2e assertion**

In `tests/e2e/40-live-docker-gitlab-retry.spec.ts`, in the first test (`renders GitLab CI modal with snippet for a job selection`), after the existing snippet assertion on line 29, add:

```typescript
    await expect(authedPage.locator('.gitlab-signals-note')).toContainText('commit status');
    await expect(authedPage.locator('.gitlab-signals-note')).toContainText('CI_JOB_TOKEN');
```

- [ ] **Step 5: Document the behavior**

In `docs/cli.md`, append after the closing ``` of the "GitLab CI example" block (end of file, line 73):

```markdown

## GitLab-native signals

When a run is launched through `scripts/ci/run-atom-target.sh`, the script also
reports the result back into GitLab using the job's own `CI_JOB_TOKEN` — no
extra CI/CD variable and no credential stored in Atom:

- **Commit status** on `$CI_COMMIT_SHA`, posted as `pending` before the run and
  `success` / `failed` / `canceled` after it, with a description like
  `3 passed, 1 failed, 0 error`. The status context is namespaced per target
  (`atom/selection/nightly-regression`, `atom/sequence/17`), so several
  selections or sequences on the same commit don't overwrite each other.
- **Merge-request comment**, on merge-request pipelines only: one sticky comment
  per target carrying the run's markdown summary, updated in place on each push
  instead of accumulating. Branch pipelines get the commit status only.

Both are best effort. If the GitLab API is unreachable, the token lacks
permission, or the job isn't running in GitLab CI at all, the script warns on
stderr and continues — the pipeline's pass/fail verdict always comes from
`atom run`'s exit code alone.

The commit status links back to `$ATOM_API_URL` (Atom's UI root) rather than the
individual run, because there is no per-run deep-link route in the frontend yet.
```

- [ ] **Step 6: Verify the frontend change manually**

Start the API (`uvicorn api.main:app --reload`) or the docker-compose integration stack, open the Launch tab, click **GitLab CI**, and confirm the modal now shows a "WHAT YOU'LL SEE IN GITLAB" paragraph mentioning the commit status and `CI_JOB_TOKEN`. Repeat via a sequence's **CI/CD** button to confirm the note interpolates `atom/sequence/<name>`.

If the e2e stack is available, run: `npx playwright test tests/e2e/40-live-docker-gitlab-retry.spec.ts`

Expected: both tests pass. (This spec needs the live docker stack; if it isn't running, the manual check above is the verification.)

- [ ] **Step 7: Commit**

```bash
git add frontend/features/launch.js frontend/partials/tab-launch.html frontend/index.html tests/e2e/40-live-docker-gitlab-retry.spec.ts docs/cli.md
git commit -m "docs(ci): explain GitLab commit status and MR comment signals"
```

---

## Task 5: End-to-end verification against a real GitLab

**Files:** none changed — this is the manual acceptance pass the spec's §8 calls for.

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: nothing.

This task needs a real (or sandbox) GitLab project whose CI can reach a running Atom instance. If that isn't available, mark this task blocked rather than complete, and record it as the outstanding risk — the stubbed-curl suite pins request *shapes*, not GitLab's acceptance of them.

- [ ] **Step 1: Set up the pipeline**

In the GitLab project, add to `.gitlab-ci.yml` (use the snippet the CI/CD Integration modal generates, which already targets `run-atom-target.sh`):

```yaml
atom-selection:
  stage: test
  script:
    - pip install -e .
    - ./scripts/ci/run-atom-target.sh selection <your-selection-id>
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH'
```

Set `ATOM_API_URL` and `ATOM_API_TOKEN` as masked+protected CI/CD variables. Do **not** add anything for `CI_JOB_TOKEN` — GitLab injects it.

- [ ] **Step 2: Verify a branch pipeline**

Push a commit to a branch (not an MR). Then check:
- The commit in GitLab shows a status check named `atom/selection/<slug>` that goes `pending` → `success`/`failed`.
- Its description reads like `3 passed, 1 failed, 0 error`.
- Clicking it opens `$ATOM_API_URL`.
- **No** MR comment is created, and the job log shows no warning about it.

- [ ] **Step 3: Verify a merge-request pipeline**

Open an MR from that branch and let the pipeline run. Then check:
- The MR widget shows the same `atom/selection/<slug>` check.
- Exactly one comment from the CI user carries the run's markdown summary.
- Its rendered body shows **no** visible marker text.

- [ ] **Step 4: Verify stickiness**

Push a second commit to the MR branch. Then check:
- The comment count is still **one** for that target — the existing comment was updated, not duplicated.
- Its content reflects the newer run.

- [ ] **Step 5: Verify two targets don't collide**

Add a second job to the pipeline for a sequence:

```yaml
atom-sequence:
  stage: test
  script:
    - pip install -e .
    - ./scripts/ci/run-atom-target.sh sequence <your-sequence-id>
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

Push. Then check the MR shows **two** distinct status checks (`atom/selection/...` and `atom/sequence/...`) and **two** distinct sticky comments.

- [ ] **Step 6: Verify the non-GitLab degradation**

On any machine, with no GitLab env vars set:

```bash
bash scripts/ci/post-gitlab-status.sh success selection demo "ok"; echo "exit=$?"
```

Expected: `warning: GitLab CI environment not detected; skipping GitLab status/comment` on stderr, and `exit=0`.

- [ ] **Step 7: Verify no token leaks into the job log**

Open the raw job log of any of the above pipelines and search it for the job token value. Expected: no match. Confirm the script's warnings (if any appeared) contain only an HTTP code and a GitLab JSON message.

---

## Out of Scope (restated from the spec)

- The dead per-run deep link (`#/runs/<id>`) — a pre-existing frontend gap, documented in `docs/cli.md` by Task 4 as a known limitation, not fixed here.
- Equivalent signals for non-GitLab CI (Jenkins, GitHub Actions) — the script degrades to a warning + no-op there by design.
- Any Atom-side GitLab credential storage.
- Scoped/limited-permission Atom API tokens (sub-project 3).
- A CI runs dashboard in the frontend (sub-project 4).
