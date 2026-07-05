# GitLab CI/CD Job Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a GitLab CI/CD pipeline launch an Atom Job Selection, gate the pipeline stage on the run's pass/fail outcome, and splice a markdown status summary into `README.md`.

**Architecture:** Extend the existing `POST /api/selections/{id}/launch` endpoint to accept optional `ci_context` (commit SHA, pipeline URL, ref), store it on the created `TestRun`, and add a new read-only `GET /api/runs/{run_id}/markdown-summary` endpoint that renders the run's per-job results as a markdown table. A checked-in shell script (`scripts/ci/run-atom-selection.sh`) orchestrates: launch → poll → fetch markdown → splice into `README.md` (via a small testable Python helper) → commit/push → exit with the run's pass/fail code. A new frontend modal on the Job Selection list generates the copy-paste `.gitlab-ci.yml` snippet and required GitLab CI/CD variables for a given selection.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Pydantic v2, pytest, Alpine.js, bash, Python (for the README splice helper).

**Spec:** [docs/superpowers/specs/2026-07-05-gitlab-cicd-job-integration-design.md](../specs/2026-07-05-gitlab-cicd-job-integration-design.md)

---

### Task 1: Add `ci_context` column to `TestRun`

**Files:**
- Modify: `etl_framework/repository/models.py:103-104`
- Modify: `etl_framework/repository/database.py:306-312`
- Test: `tests/unit/test_repository_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_repository_models.py`, using the file's existing `engine_and_db` fixture (defined at line 8-13, yields `(engine, session)`):

```python
def test_test_run_ci_context_defaults_to_none_and_round_trips(engine_and_db):
    engine, db = engine_and_db

    run = TestRun(run_id="ci-run-1", source_env="dev", target_env="prod")
    db.add(run)
    db.commit()
    db.refresh(run)
    assert run.ci_context is None

    run.ci_context = {
        "commit_sha": "a1b2c3d",
        "pipeline_url": "https://gitlab.example.com/team/proj/-/pipelines/4821",
        "ref": "main",
        "triggered_by": "gitlab-ci",
    }
    db.commit()
    db.refresh(run)
    assert run.ci_context["commit_sha"] == "a1b2c3d"
```

(`TestRun` is already imported at line 5 of this file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_repository_models.py::test_test_run_ci_context_defaults_to_none_and_round_trips -v`
Expected: FAIL with `AttributeError: 'TestRun' object has no attribute 'ci_context'` (or SQLAlchemy `TypeError` on unexpected keyword).

- [ ] **Step 3: Add the column to the model**

In `etl_framework/repository/models.py`, in the `TestRun` class, after line 104 (`selection_version = Column(Integer, nullable=True)`):

```python
    selection_version = Column(Integer, nullable=True)
    ci_context = Column(JSON, nullable=True)
```

- [ ] **Step 4: Add the bootstrap column to `database.py`**

In `etl_framework/repository/database.py`, immediately after the block at lines 306-309:

```python
        if "selection_id" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN selection_id INTEGER"))
        if "selection_version" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN selection_version INTEGER"))
```

add:

```python
        if "ci_context" not in test_run_cols:
            conn.execute(text("ALTER TABLE test_runs ADD COLUMN ci_context JSON"))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_repository_models.py::test_test_run_ci_context_defaults_to_none_and_round_trips -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py tests/unit/test_repository_models.py
git commit -m "feat: add nullable ci_context column to TestRun"
```

---

### Task 2: Extend `RunRepository.create_run()` to accept `ci_context`

**Files:**
- Modify: `etl_framework/repository/repository.py:235-260`
- Test: `tests/unit/test_repository.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_repository.py`, in the "RunRepository tests" section (after `test_run_create`, ~line 76):

```python
def test_run_create_with_ci_context(db):
    repo = RunRepository(db)
    ctx = {"commit_sha": "abc123", "pipeline_url": "https://gitlab.example.com/p/1", "ref": "main"}
    run = repo.create_run(run_id="run-ci-1", source_env="dev", target_env="prod", ci_context=ctx)
    assert run.ci_context == ctx


def test_run_create_without_ci_context_defaults_to_none(db):
    repo = RunRepository(db)
    run = repo.create_run(run_id="run-ci-2", source_env="dev", target_env="prod")
    assert run.ci_context is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_repository.py::test_run_create_with_ci_context -v`
Expected: FAIL with `TypeError: create_run() got an unexpected keyword argument 'ci_context'`

- [ ] **Step 3: Implement**

In `etl_framework/repository/repository.py`, replace the `create_run` method (lines 235-260):

```python
    def create_run(
        self,
        run_id: str,
        source_env: str,
        target_env: str,
        config_snapshot: dict | None = None,
        run_type: str = "reconciliation",
        pair_id: str | None = None,
        selection_id: int | None = None,
        selection_version: int | None = None,
        ci_context: dict | None = None,
    ) -> TestRun:
        run = TestRun(
            run_id=run_id,
            status="PENDING",
            source_env=source_env,
            target_env=target_env,
            config_snapshot=config_snapshot,
            run_type=run_type,
            pair_id=pair_id,
            selection_id=selection_id,
            selection_version=selection_version,
            ci_context=ci_context,
        )
        self._db.add(run)
        self._db.commit()
        self._db.refresh(run)
        return run
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_repository.py -k ci_context -v`
Expected: PASS (both new tests)

- [ ] **Step 5: Run the full repository test file to check for regressions**

Run: `pytest tests/unit/test_repository.py -v`
Expected: PASS (all tests, including pre-existing ones)

- [ ] **Step 6: Commit**

```bash
git add etl_framework/repository/repository.py tests/unit/test_repository.py
git commit -m "feat: accept optional ci_context in RunRepository.create_run"
```

---

### Task 3: Accept and store `ci_context` on the selection launch endpoint

**Files:**
- Modify: `api/schemas.py:408-415`
- Modify: `api/routes/selections.py:220-227`
- Test: `tests/unit/test_selections_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_selections_routes.py` (after `test_launch_creates_run_with_selection_fields`, ~line 102). This test needs run detail data that includes `ci_context`, so it queries the DB directly through a second session rather than the JSON API (the API's `RunDetailOut` intentionally doesn't expose internal CI bookkeeping):

```python
def test_launch_with_ci_context_stores_it_on_run(client):
    created = _create_selection(client)
    ctx = {"commit_sha": "deadbeef", "pipeline_url": "https://gitlab.example.com/p/9", "ref": "main"}
    resp = client.post(
        f"/api/selections/{created['id']}/launch",
        json={"source_env": "dev", "target_env": "qa", "ci_context": ctx},
    )
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]

    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    with _db_module.SessionLocal() as db:
        run = RunRepository(db).get_run(run_id)
        assert run.ci_context == ctx
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_selections_routes.py::test_launch_with_ci_context_stores_it_on_run -v`
Expected: FAIL — either a 422 (unknown field rejected under `extra="forbid"`, if configured) or the stored `run.ci_context` being `None` because the field is silently ignored. Confirm which by reading the failure output before proceeding.

- [ ] **Step 3: Add `ci_context` to the request schema**

In `api/schemas.py`, in `JobSelectionLaunchRequest` (lines 408-415):

```python
class JobSelectionLaunchRequest(BaseModel):
    source_env: str
    target_env: str = ""
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None
    ci_context: dict[str, Any] | None = None
```

- [ ] **Step 4: Pass it through in the launch endpoint**

In `api/routes/selections.py`, in `launch_selection` (lines 220-227), add `ci_context=body.ci_context` to the `create_run` call:

```python
    RunRepository(db).create_run(
        run_id=run_id,
        source_env=trigger.source_env,
        target_env=trigger.target_env,
        config_snapshot=config_snapshot or None,
        selection_id=selection_id,
        selection_version=version.version_number,
        ci_context=body.ci_context,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_selections_routes.py::test_launch_with_ci_context_stores_it_on_run -v`
Expected: PASS

- [ ] **Step 6: Run the full selections test file to check for regressions**

Run: `pytest tests/unit/test_selections_routes.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Commit**

```bash
git add api/schemas.py api/routes/selections.py tests/unit/test_selections_routes.py
git commit -m "feat: accept ci_context on selection launch and store it on the run"
```

---

### Task 4: `GET /api/runs/{run_id}/markdown-summary` endpoint

**Files:**
- Modify: `api/routes/runs.py` (add helper + route after `export_run_csv`, ~line 966)
- Test: `tests/unit/test_run_markdown_summary.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_run_markdown_summary.py`:

```python
"""Tests for GET /api/runs/{run_id}/markdown-summary."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))

    with Session(engine) as db:
        raw, _ = TokenRepository(db).create("test-runner")

    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def _make_run_with_results(client, run_id="run-md-1", ci_context=None):
    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    from etl_framework.repository.models import TestResult

    with _db_module.SessionLocal() as db:
        repo = RunRepository(db)
        run = repo.create_run(run_id=run_id, source_env="dev", target_env="qa", ci_context=ci_context)
        repo.update_run_status(run_id, "FAILED", total_tests=2, passed=1, failed=1)
        db.add(TestResult(
            run_id=run_id, query_name="orders_recon", status="PASSED",
            duration_seconds=12.4, source_row_count=10, target_row_count=10,
            value_mismatch_count=0, missing_in_target_count=0, missing_in_source_count=0,
        ))
        db.add(TestResult(
            run_id=run_id, query_name="customer_feed", status="FAILED",
            duration_seconds=3.2, source_row_count=10, target_row_count=9,
            value_mismatch_count=0, missing_in_target_count=1, missing_in_source_count=0,
        ))
        db.commit()


def test_markdown_summary_lists_each_job_with_status(client):
    _make_run_with_results(client)
    resp = client.get("/api/runs/run-md-1/markdown-summary")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    body = resp.text
    assert "orders_recon" in body
    assert "customer_feed" in body
    assert "✅" in body
    assert "❌" in body


def test_markdown_summary_shows_ci_context_when_present(client):
    _make_run_with_results(client, run_id="run-md-2", ci_context={
        "commit_sha": "a1b2c3d", "pipeline_url": "https://gitlab.example.com/p/4821", "ref": "main",
    })
    resp = client.get("/api/runs/run-md-2/markdown-summary")
    assert "a1b2c3d" in resp.text
    assert "https://gitlab.example.com/p/4821" in resp.text


def test_markdown_summary_shows_manual_when_no_ci_context(client):
    _make_run_with_results(client, run_id="run-md-3")
    resp = client.get("/api/runs/run-md-3/markdown-summary")
    assert "manual" in resp.text.lower()


def test_markdown_summary_missing_run_returns_404(client):
    resp = client.get("/api/runs/does-not-exist/markdown-summary")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_run_markdown_summary.py -v`
Expected: FAIL with 404 Not Found for all requests (route doesn't exist yet).

- [ ] **Step 3: Implement the endpoint**

In `api/routes/runs.py`, add this helper and route immediately after `export_run_csv` (after line 965, before the `@router.delete("/{run_id}"...)` at line 968):

```python
_STATUS_EMOJI = {"PASSED": "✅", "FAILED": "❌", "ERROR": "❌", "SLOW": "⚠️", "CANCELLED": "⚠️"}


def _render_markdown_summary(run) -> str:
    if run.ci_context:
        trigger_line = (
            f"_Last run: {run.completed_at or run.started_at} via GitLab CI "
            f"(commit {run.ci_context.get('commit_sha', '?')}, "
            f"[pipeline]({run.ci_context.get('pipeline_url', '')}), "
            f"ref `{run.ci_context.get('ref', '?')}`)_"
        )
    else:
        trigger_line = f"_Last run: {run.completed_at or run.started_at} (manual)_"

    lines = [
        "## Job Status (auto-updated)",
        "",
        trigger_line,
        "",
        "| Job | Status | Duration |",
        "|-----|--------|----------|",
    ]
    for result in run.results:
        emoji = _STATUS_EMOJI.get(result.effective_status, result.effective_status)
        lines.append(f"| {result.query_name} | {emoji} {result.effective_status} | {result.duration_seconds:.1f}s |")
    lines.append("")
    lines.append(f"[View full run in Atom](/#/runs/{run.run_id})")
    return "\n".join(lines)


@router.get("/{run_id}/markdown-summary", response_class=PlainTextResponse)
def get_run_markdown_summary(run_id: str, db: Session = Depends(get_session)):
    repo = RunRepository(db)
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return PlainTextResponse(_render_markdown_summary(run), media_type="text/markdown")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_run_markdown_summary.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Run the full runs test suite to check for regressions**

Run: `pytest tests/unit/test_runs_extensions.py tests/unit/test_run_steps.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add api/routes/runs.py tests/unit/test_run_markdown_summary.py
git commit -m "feat: add GET /api/runs/{run_id}/markdown-summary endpoint"
```

---

### Task 5: README marker section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a Contents entry**

In `README.md`, in the `## Contents` list (after the `- [Quick Start]` area — find the line `- [Capabilities](#capabilities)` around line 20), add a new entry right before it:

```markdown
- [CI/CD Job Status](#cicd-job-status)
- [Capabilities](#capabilities)
```

- [ ] **Step 2: Add the marked section**

In `README.md`, immediately after the "Quick Start" section's closing (after the line `Open \`http://127.0.0.1:8000\`. On first load the UI prompts for a token — follow the [bootstrap steps](#authentication) below.` and before `## Contents`), add:

```markdown
## CI/CD Job Status

<!-- ATOM:JOB-STATUS:START -->
_No CI-triggered run yet. See [CI/CD Integration](#cicd-integration) to wire up a GitLab pipeline._
<!-- ATOM:JOB-STATUS:END -->
```

- [ ] **Step 3: Verify markers are well-formed**

Run: `grep -c "ATOM:JOB-STATUS" README.md`
Expected: `2`

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: add CI/CD job status marker section to README"
```

---

### Task 6: `scripts/ci/splice_readme.py` — marker-splice helper

**Files:**
- Create: `scripts/ci/splice_readme.py`
- Test: `tests/unit/test_splice_readme.py` (new)

This is a standalone Python script (no Atom imports) so it can run in a bare GitLab CI image without installing Atom's dependencies, and so its core logic is directly unit-testable with pytest.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_splice_readme.py`:

```python
"""Tests for scripts/ci/splice_readme.py's marker-splice logic."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))

import pytest
from splice_readme import splice, MarkersNotFoundError

START = "<!-- ATOM:JOB-STATUS:START -->"
END = "<!-- ATOM:JOB-STATUS:END -->"


def test_splice_replaces_content_between_markers():
    original = f"# Title\n\n{START}\nold content\n{END}\n\nmore text\n"
    result = splice(original, "new content")
    assert result == f"# Title\n\n{START}\nnew content\n{END}\n\nmore text\n"


def test_splice_preserves_surrounding_content():
    original = f"before\n{START}\nold\n{END}\nafter\n"
    result = splice(original, "new")
    assert result.startswith("before\n")
    assert result.endswith("after\n")


def test_splice_raises_clear_error_when_markers_missing():
    with pytest.raises(MarkersNotFoundError, match="ATOM:JOB-STATUS"):
        splice("# Title\n\nno markers here\n", "new content")


def test_splice_raises_clear_error_when_only_start_marker_present():
    with pytest.raises(MarkersNotFoundError):
        splice(f"# Title\n\n{START}\nunterminated\n", "new content")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_splice_readme.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'splice_readme'`

- [ ] **Step 3: Implement the script**

Create `scripts/ci/splice_readme.py`:

```python
#!/usr/bin/env python3
"""Splice a markdown block into README.md between marker comments.

Usage:
    python splice_readme.py <readme_path> <markdown_content_path>

Exits non-zero with a clear message if the marker comments are not both
present in the target file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

START_MARKER = "<!-- ATOM:JOB-STATUS:START -->"
END_MARKER = "<!-- ATOM:JOB-STATUS:END -->"


class MarkersNotFoundError(Exception):
    pass


def splice(original: str, new_content: str) -> str:
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
        re.DOTALL,
    )
    if not pattern.search(original):
        raise MarkersNotFoundError(
            f"Could not find both {START_MARKER} and {END_MARKER} markers in the target file."
        )
    replacement = f"{START_MARKER}\n{new_content}\n{END_MARKER}"
    return pattern.sub(replacement, original)


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: splice_readme.py <readme_path> <markdown_content_path>", file=sys.stderr)
        return 2
    readme_path = Path(sys.argv[1])
    content_path = Path(sys.argv[2])

    original = readme_path.read_text(encoding="utf-8")
    new_content = content_path.read_text(encoding="utf-8").strip()

    try:
        updated = splice(original, new_content)
    except MarkersNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    readme_path.write_text(updated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_splice_readme.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Manual smoke test against the real README**

Run:
```bash
echo "test content $(date)" > /tmp/summary.md
python scripts/ci/splice_readme.py README.md /tmp/summary.md
grep -A2 "ATOM:JOB-STATUS:START" README.md
git checkout README.md
```
Expected: the grep shows the test content spliced in; `git checkout` reverts it since this was just a smoke test.

- [ ] **Step 6: Commit**

```bash
git add scripts/ci/splice_readme.py tests/unit/test_splice_readme.py
git commit -m "feat: add README marker-splice helper for CI job status updates"
```

---

### Task 7: `scripts/ci/run-atom-selection.sh` — orchestration script

**Files:**
- Create: `scripts/ci/run-atom-selection.sh`

This script has no automated test (it's a thin orchestration layer calling already-tested endpoints and the already-tested `splice_readme.py`); it's verified manually in Step 2.

- [ ] **Step 1: Create the script**

Create `scripts/ci/run-atom-selection.sh`:

```bash
#!/usr/bin/env bash
# Launch an Atom Job Selection from GitLab CI, gate the pipeline on its
# result, and update README.md with a markdown status summary.
#
# Required env vars: ATOM_API_URL, ATOM_API_TOKEN
# Usage: run-atom-selection.sh <selection_id> [environment]
set -euo pipefail

SELECTION_ID="${1:?Usage: run-atom-selection.sh <selection_id> [environment]}"
ENVIRONMENT="${2:-prod}"
POLL_INTERVAL_SECONDS="${ATOM_POLL_INTERVAL_SECONDS:-10}"
POLL_TIMEOUT_SECONDS="${ATOM_POLL_TIMEOUT_SECONDS:-1800}"
TERMINAL_STATUSES="PASSED FAILED ERROR CANCELLED SLOW"

: "${ATOM_API_URL:?ATOM_API_URL must be set}"
: "${ATOM_API_TOKEN:?ATOM_API_TOKEN must be set}"

auth_header="Authorization: Bearer ${ATOM_API_TOKEN}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Launching job selection ${SELECTION_ID} against ${ENVIRONMENT}..."
launch_body=$(cat <<JSON
{
  "source_env": "${ENVIRONMENT}",
  "ci_context": {
    "commit_sha": "${CI_COMMIT_SHA:-unknown}",
    "pipeline_url": "${CI_PIPELINE_URL:-}",
    "ref": "${CI_COMMIT_REF_NAME:-unknown}",
    "triggered_by": "gitlab-ci"
  }
}
JSON
)

launch_response=$(curl -sf -X POST "${ATOM_API_URL}/api/selections/${SELECTION_ID}/launch" \
  -H "${auth_header}" -H "Content-Type: application/json" \
  -d "${launch_body}")
run_id=$(echo "${launch_response}" | python3 -c "import sys,json; print(json.load(sys.stdin)['run_id'])")
echo "Launched run ${run_id}. Polling for completion..."

elapsed=0
status="PENDING"
while true; do
  detail=$(curl -sf "${ATOM_API_URL}/api/runs/${run_id}" -H "${auth_header}")
  status=$(echo "${detail}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  if echo " ${TERMINAL_STATUSES} " | grep -q " ${status} "; then
    break
  fi
  if [ "${elapsed}" -ge "${POLL_TIMEOUT_SECONDS}" ]; then
    echo "error: run ${run_id} did not complete within ${POLL_TIMEOUT_SECONDS}s (last status: ${status})" >&2
    exit 1
  fi
  sleep "${POLL_INTERVAL_SECONDS}"
  elapsed=$((elapsed + POLL_INTERVAL_SECONDS))
done
echo "Run ${run_id} finished with status ${status}."

readme_update_ok=true
if ! curl -sf "${ATOM_API_URL}/api/runs/${run_id}/markdown-summary" -o /tmp/atom-run-summary.md; then
  echo "warning: could not fetch markdown summary for run ${run_id}; skipping README update" >&2
  readme_update_ok=false
fi

if [ "${readme_update_ok}" = true ]; then
  if python3 "${script_dir}/splice_readme.py" README.md /tmp/atom-run-summary.md; then
    git config user.email "atom-ci-bot@localhost"
    git config user.name "atom-ci-bot"
    git add README.md
    if git diff --cached --quiet; then
      echo "README already up to date; nothing to commit."
    else
      git commit -m "chore: update job status for run ${run_id} [skip ci]"
      if ! git push origin "HEAD:${CI_COMMIT_REF_NAME}"; then
        echo "push rejected, retrying after rebase..."
        git pull --rebase origin "${CI_COMMIT_REF_NAME}"
        git push origin "HEAD:${CI_COMMIT_REF_NAME}"
      fi
    fi
  else
    echo "warning: README marker splice failed; continuing (pipeline result unaffected)" >&2
  fi
fi

if [ "${status}" = "PASSED" ]; then
  exit 0
else
  echo "error: job selection run ${run_id} finished with status ${status}" >&2
  exit 1
fi
```

- [ ] **Step 2: Manual verification**

Run the app locally, create a job selection with at least one job, create an API token, then:

```bash
export ATOM_API_URL="http://127.0.0.1:8000"
export ATOM_API_TOKEN="<token from /api/tokens>"
chmod +x scripts/ci/run-atom-selection.sh
./scripts/ci/run-atom-selection.sh <selection_id> dev
```

Expected: script prints the launch, polls, prints the final status, updates `README.md` locally (verify with `git diff README.md`), and exits 0 or 1 matching the run's outcome. Revert the local README change afterward with `git checkout README.md` since this is a local smoke test, not a real CI push.

- [ ] **Step 3: Commit**

```bash
chmod +x scripts/ci/run-atom-selection.sh
git add scripts/ci/run-atom-selection.sh
git commit -m "feat: add GitLab CI orchestration script for job selection runs"
```

---

### Task 8: Frontend — CI/CD Integration modal

**Files:**
- Modify: `frontend/app.js:543-546` (state) and after `openSelectionRuns` (~line 3329, methods)
- Modify: `frontend/index.html:1466-1471` (button) and after the Launch Selection Modal (~line 1503+, new modal markup)

- [ ] **Step 1: Add state fields**

In `frontend/app.js`, near line 545-546:

```javascript
    selectionModal: {},
    selectionModalEditing: false,
    showCiIntegrationModal: false,
    ciIntegrationModal: {},
```

- [ ] **Step 2: Add the method to build the snippet**

In `frontend/app.js`, after the `openSelectionRuns` method body (find its closing, ~line 3329-3330 based on current layout — insert as a new method in the "JOB SELECTIONS" section):

```javascript
    openCiIntegrationModal(sel) {
      const yaml = [
        `atom-job-selection:`,
        `  stage: test`,
        `  script:`,
        `    - ./scripts/ci/run-atom-selection.sh ${sel.id}`,
        `  rules:`,
        `    - if: '$CI_COMMIT_BRANCH == "main"'`,
      ].join('\n');
      this.ciIntegrationModal = {
        selectionId: sel.id,
        selectionName: sel.name,
        yamlSnippet: yaml,
      };
      this.showCiIntegrationModal = true;
    },

    async copyCiYamlSnippet() {
      try {
        await navigator.clipboard.writeText(this.ciIntegrationModal.yamlSnippet);
        this.toast('success', 'Copied', 'Pipeline snippet copied to clipboard');
      } catch {
        this.toast('warn', 'Copy failed', 'Select the text manually');
      }
    },
```

- [ ] **Step 3: Add the button**

In `frontend/index.html`, in the button row (line 1466-1471), add a new button after `History`:

```html
            <div class="flex gap-1 flex-shrink-0">
              <button @click="openLaunchSelectionModal(sel)" class="btn-primary btn-sm text-xs">Launch</button>
              <button @click="openSelectionRuns(sel)" class="btn-secondary btn-sm text-xs">History</button>
              <button @click="openCiIntegrationModal(sel)" class="btn-secondary btn-sm text-xs">CI/CD</button>
              <button @click="openEditSelectionModal(sel)" class="btn-secondary btn-sm text-xs">Edit</button>
              <button @click="deleteSelection(sel.id)" class="btn-danger btn-sm text-xs">Archive</button>
            </div>
```

- [ ] **Step 4: Add the modal markup**

In `frontend/index.html`, immediately after the closing `</div>` of the "Launch Selection Modal" block (the block starting at line 1506 in the pre-change file — insert the new modal right after its closing `</div>`):

```html
  <!-- CI/CD Integration Modal -->
  <div x-show="showCiIntegrationModal" x-cloak class="modal-backdrop" @click.self="showCiIntegrationModal = false">
    <div class="modal-box w-full max-w-2xl">
      <h2 class="text-lg font-bold mb-4">CI/CD Integration — <span x-text="ciIntegrationModal.selectionName"></span></h2>
      <div class="space-y-3 text-sm">
        <div>
          <div class="label">STEP 1 — Create an API token</div>
          <p class="text-muted">Use the Tokens section to create one, then store it as a masked/protected GitLab CI/CD variable.</p>
        </div>
        <div>
          <div class="label">STEP 2 — Add these variables in GitLab (Settings &rarr; CI/CD &rarr; Variables)</div>
          <pre class="bg-slate-50 border rounded p-2 text-xs font-mono">ATOM_API_URL = &lt;your Atom base URL&gt;
ATOM_API_TOKEN = &lt;paste token, mark Masked+Protected&gt;</pre>
        </div>
        <div>
          <div class="label">STEP 3 — Copy this into .gitlab-ci.yml</div>
          <pre class="bg-slate-50 border rounded p-2 text-xs font-mono" x-text="ciIntegrationModal.yamlSnippet"></pre>
          <button @click="copyCiYamlSnippet()" class="btn-primary btn-sm text-xs mt-2">Copy snippet</button>
        </div>
      </div>
      <div class="flex justify-end gap-3 mt-6">
        <button @click="showCiIntegrationModal = false" class="btn-secondary">Close</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 5: Manual verification**

Run the app, open the "Job Selections" sub-tab, click "CI/CD" on a selection row, confirm the modal shows the real selection ID in the YAML snippet, and that "Copy snippet" copies it (check by pasting into the URL bar or an editor).

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat: add CI/CD Integration modal to Job Selections UI"
```

---

## Self-Review Notes

- **Spec coverage:** Data model (Task 1), API changes (Tasks 2-4), frontend panel (Task 8), CI script + README mechanics (Tasks 5-7), error handling (poll timeout, markdown-summary fetch failure, push retry — all in Task 7's script) are all covered. GitLab Commit Status/MR comments and Atom-initiated triggering remain explicitly out of scope per the spec.
- **Type consistency:** `create_run(..., ci_context=...)` (Task 2) matches the call site added in Task 3 and the test setup in Task 4. `_render_markdown_summary` (Task 4) reads `run.ci_context`, `run.results`, `result.effective_status`, `result.duration_seconds`, `result.query_name` — all fields confirmed to exist on `TestRun`/`TestResult` today.
- **Script testability deviation from spec wording:** The spec says the CI script does the splicing; this plan factors that specific piece into `scripts/ci/splice_readme.py` (pure Python, no Atom dependency) so it has real unit tests, while `run-atom-selection.sh` still does the orchestration/git parts. This doesn't change any user-facing behavior described in the spec.
