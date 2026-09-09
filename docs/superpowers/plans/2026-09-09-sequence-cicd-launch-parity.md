# Sequence CI/CD Launch Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Execution Sequences the same direct CI/CD launch path Job Selections already
have — an ad-hoc `POST /api/sequences/{id}/launch` endpoint, `atom run --target-type
sequence` CLI support, and a CI script — while consolidating the old curl-based CI script
into a thin wrapper around the `atom` CLI it now duplicates.

**Architecture:** New launch route in `api/routes/sequences.py` reuses the existing
`resolve_sequence` / `check_preconditions` / `_validate_env_requirements` / `_execute_run`
machinery already proven by `launch_selection` and the scheduler's sequence branch — no new
persistence, no new tables. CLI gets one new option that branches which REST path `atom run`
calls. The CI script drops its own curl/poll loop in favor of shelling out to the CLI.

**Tech Stack:** FastAPI + Pydantic (server), Typer + `requests`/`tenacity` (CLI), bash
(CI script), Alpine.js (frontend modal).

**Spec:** `docs/superpowers/specs/2026-09-09-sequence-cicd-launch-parity-design.md`

---

## File Structure

| File | Change |
|---|---|
| `api/schemas.py` | Add `SequenceLaunchRequest`. |
| `api/routes/sequences.py` | Add `POST /{sequence_id}/launch`; new imports. |
| `tests/unit/test_sequences_routes.py` | Add launch tests; monkeypatch `_execute_run` in the fixture. |
| `etl_framework/cli/app.py` | Add `--target-type` to `run`; replace `_resolve_selection` with `_resolve_target`. |
| `tests/unit/test_cli_app.py` | Add `--target-type` tests. |
| `scripts/ci/run-atom-target.sh` | New — replaces `run-atom-selection.sh`. |
| `scripts/ci/run-atom-selection.sh` | Deleted. |
| `frontend/features/launch.js` | Generalize `openCiIntegrationModal` to accept `targetType`. |
| `frontend/partials/tab-launch.html` | Modal title becomes type-aware; selection call site passes `'selection'`. |
| `frontend/partials/tab-sequences.html` | New "CI/CD" button in the sequence detail header. |
| `tests/e2e/40-live-docker-gitlab-retry.spec.ts` | Update assertion for the renamed script; add a sequence-modal case. |

No database migration — no new columns, matching the spec's "no schema change" decision
(provenance already flows through `config_snapshot["sequence"]`).

---

## Task 1: `SequenceLaunchRequest` schema

**Files:**
- Modify: `api/schemas.py` (add after `SequenceRef`, around line 252, so it sits with the
  other sequence schemas rather than down by `JobSelectionLaunchRequest`)

- [ ] **Step 1: Add the schema**

Open `api/schemas.py`. Immediately after the `SequenceRef` class (ends at line 251 —
`sequence_version: int | None = None   # None resolves to the latest version`), insert:

```python
class SequenceLaunchRequest(BaseModel):
    source_env: str | None = None       # None = fall back to SequenceDefaults.source_env
    target_env: str | None = None       # None = fall back to SequenceDefaults.target_env
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None        # None = fall back to SequenceDefaults.config_id
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None          # pin a sequence_version; None = latest
    ci_context: dict[str, Any] | None = None
```

This has no test of its own — it's exercised end to end by Task 2's route tests. Pydantic
model definitions with no validators don't need a dedicated unit test; a bare model with no
behavior would just test that Pydantic works.

- [ ] **Step 2: Commit**

```bash
git add api/schemas.py
git commit -m "feat(sequences): add SequenceLaunchRequest schema"
```

---

## Task 2: `POST /api/sequences/{id}/launch`

**Files:**
- Modify: `api/routes/sequences.py`
- Test: `tests/unit/test_sequences_routes.py`

- [ ] **Step 1: Update the test fixture to allow launch tests**

Open `tests/unit/test_sequences_routes.py`. In the `client` fixture (after line 25's
`monkeypatch.setattr(_db_module, "SessionLocal", ...)`), add:

```python
    monkeypatch.setattr("api.routes.sequences._execute_run", lambda *a, **k: None)
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_sequences_routes.py` (the file already defines `CHAIN` and
`_create` at module level — reuse them):

```python
def test_launch_creates_run_and_returns_202(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch",
                        json={"source_env": "dev", "target_env": "qa"})
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "PENDING"
    assert resp.json()["run_id"]


def test_launch_stores_sequence_provenance_on_run(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch",
                        json={"source_env": "dev", "target_env": "qa"})
    run_id = resp.json()["run_id"]

    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    with _db_module.SessionLocal() as db:
        run = RunRepository(db).get_run(run_id)
        assert run.selection_id is None
        assert run.config_snapshot["sequence"] == {
            "id": created["id"], "name": "nightly", "version": 1,
        }


def test_launch_stores_ci_context_on_run(client):
    created = _create(client).json()
    ctx = {"commit_sha": "deadbeef", "pipeline_url": "https://gitlab.example.com/p/9", "ref": "main"}
    resp = client.post(
        f"/api/sequences/{created['id']}/launch",
        json={"source_env": "dev", "target_env": "qa", "ci_context": ctx},
    )
    assert resp.status_code == 202

    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    with _db_module.SessionLocal() as db:
        run = RunRepository(db).get_run(resp.json()["run_id"])
        assert run.ci_context == ctx


def test_launch_unknown_sequence_returns_404(client):
    resp = client.post("/api/sequences/999/launch", json={"source_env": "dev"})
    assert resp.status_code == 404


def test_launch_unknown_pinned_version_returns_404(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch",
                        json={"source_env": "dev", "version": 99})
    assert resp.status_code == 404


def test_launch_falls_back_to_sequence_default_source_env(client):
    resp = client.post("/api/sequences", json={
        "name": "with-defaults", "description": "", "tags": [], "steps": CHAIN,
        "defaults": {"source_env": "dev", "target_env": "qa"},
    })
    seq_id = resp.json()["id"]
    launch_resp = client.post(f"/api/sequences/{seq_id}/launch", json={})
    assert launch_resp.status_code == 202, launch_resp.text


def test_launch_without_source_env_or_default_returns_422(client):
    created = _create(client).json()
    resp = client.post(f"/api/sequences/{created['id']}/launch", json={})
    assert resp.status_code == 422
    assert "source_env" in resp.json()["detail"]


def test_launch_precondition_failure_creates_no_run(client):
    resp = client.post("/api/sequences", json={
        "name": "gated", "description": "", "tags": [], "steps": CHAIN,
        "preconditions": {"weekdays": []},  # no day is ever allowed
    })
    seq_id = resp.json()["id"]
    launch_resp = client.post(f"/api/sequences/{seq_id}/launch",
                               json={"source_env": "dev", "target_env": "qa"})
    assert launch_resp.status_code == 422

    from etl_framework.repository import database as _db_module
    from etl_framework.repository.repository import RunRepository
    with _db_module.SessionLocal() as db:
        assert RunRepository(db).list_runs(limit=50) == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_sequences_routes.py -v -k launch`
Expected: FAIL — `404 Not Found` (no such route yet) on every new test.

- [ ] **Step 4: Implement the route**

In `api/routes/sequences.py`, update the imports block (lines 1–25) to:

```python
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.schemas import (
    ExecutionSequenceCreate,
    ExecutionSequenceDetailOut,
    ExecutionSequenceOut,
    ExecutionSequenceUpdate,
    ExecutionSequenceVersionCreate,
    ExecutionSequenceVersionOut,
    RunStatusOut,
    RunTrigger,
    SequenceLaunchRequest,
    SequenceRef,
    SequenceUsageOut,
    SequenceValidateRequest,
    SequenceValidateResponse,
)
from api.routes.selections import _dump_job_sequence
from api.routes.runs import _execute_run, _snapshot_from_trigger
from api.services.audit_service import AuditService
from api.services.job_env_validation import validate_env_requirements
from api.services.sequence_preconditions import check_for_session as check_preconditions
from api.services.sequence_resolver import SequenceResolutionError, resolve as resolve_sequence
from api.services.sequence_validation import (
    SequenceCycleError,
    topological_order,
    validate_steps,
)
from etl_framework.repository.repository import JobRepository, RunRepository
from etl_framework.repository.sequence_repository import ExecutionSequenceRepository

router = APIRouter(tags=["sequences"])
```

(`Query` was already imported; the rest are additions.) Note: `api/routes/selections.py`
already imports from `api/routes/runs.py` in this same way, so this is not introducing a
new cross-module pattern.

Then append the route at the end of the file, after `get_sequence_usage`:

```python
@router.post("/{sequence_id}/launch", response_model=RunStatusOut, status_code=202)
def launch_sequence(
    sequence_id: int,
    body: SequenceLaunchRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_session),
):
    try:
        resolved = resolve_sequence(
            db, SequenceRef(sequence_id=sequence_id, sequence_version=body.version)
        )
    except SequenceResolutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    gate = check_preconditions(db, resolved.preconditions)
    if not gate.ok:
        raise HTTPException(status_code=422, detail=gate.reason)

    source_env = body.source_env or resolved.defaults.source_env
    if not source_env:
        raise HTTPException(
            status_code=422,
            detail="source_env is required: no value was given and the sequence has no default.",
        )
    target_env = body.target_env or resolved.defaults.target_env or ""
    config_id = body.config_id if body.config_id is not None else resolved.defaults.config_id
    run_settings = resolved.defaults.run_settings or {}

    job_sequence = resolved.as_linear_steps()
    jobs_by_name = {j.name: j for j in JobRepository(db).list()}
    validate_env_requirements(job_sequence, jobs_by_name, target_env)

    trigger = RunTrigger(
        source_env=source_env,
        target_env=target_env,
        source_connection=body.source_connection,
        target_connection=body.target_connection,
        job_sequence=job_sequence,
        config_id=config_id,
        config_data=body.config_data,
        run_settings=run_settings,
    )

    run_id = str(uuid.uuid4())
    config_snapshot = _snapshot_from_trigger(trigger, db)
    config_snapshot["job_sequence"] = _dump_job_sequence(trigger.job_sequence)
    config_snapshot["run_settings"] = trigger.run_settings.model_dump()
    config_snapshot["sequence"] = resolved.snapshot_meta()

    RunRepository(db).create_run(
        run_id=run_id,
        source_env=trigger.source_env,
        target_env=trigger.target_env,
        config_snapshot=config_snapshot or None,
        ci_context=body.ci_context,
    )
    AuditService(db).log(
        request, "sequence.launched", "execution_sequence", sequence_id,
        {
            "run_id": run_id, "source_env": trigger.source_env,
            "target_env": trigger.target_env, "version": resolved.version_number,
        },
    )
    background_tasks.add_task(
        _execute_run, run_id, resolved.steps,
        trigger.source_env, trigger.target_env, trigger.run_settings, config_snapshot,
    )
    return RunStatusOut(run_id=run_id, status="PENDING")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_sequences_routes.py -v`
Expected: PASS — every test in the file, including the pre-existing CRUD/validation ones
(confirms the import changes didn't break anything).

- [ ] **Step 6: Commit**

```bash
git add api/routes/sequences.py tests/unit/test_sequences_routes.py
git commit -m "feat(sequences): add ad-hoc POST /api/sequences/{id}/launch"
```

---

## Task 3: `atom run --target-type`

**Files:**
- Modify: `etl_framework/cli/app.py`
- Test: `tests/unit/test_cli_app.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli_app.py`:

```python
SEQUENCE_RUN_ARGS = ["run", "7", "--target-type", "sequence",
                     "--source-env", "dev", "--target-env", "qa",
                     "--poll-interval", "0"]


def _sequence_launch_responses(final_status):
    return {
        ("POST", "/api/sequences/7/launch"): {"run_id": "r-9", "status": "PENDING"},
        ("GET", "/api/runs/r-9/status"): [
            {"run_id": "r-9", "status": "RUNNING", "passed": 0, "failed": 0, "error": 0},
            final_status,
        ],
    }


def test_run_target_type_sequence_calls_sequence_endpoint(fake_client):
    from etl_framework.cli.app import app

    # _sequence_launch_responses only registers a fake response for
    # POST /api/sequences/7/launch. If the CLI called the selections endpoint
    # instead, FakeClient would raise AtomNotFoundError and exit_code would be
    # 4, not 0 -- so reaching 0 already proves the sequence path was hit.
    fake_client(_sequence_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + SEQUENCE_RUN_ARGS)
    assert result.exit_code == 0


def test_run_default_target_type_is_selection(fake_client):
    from etl_framework.cli.app import app

    fake_client(_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0}))
    result = runner.invoke(app, BASE_ARGS + RUN_ARGS)
    assert result.exit_code == 0


def test_run_resolves_sequence_by_name(fake_client):
    from etl_framework.cli.app import app

    responses = _sequence_launch_responses(
        {"run_id": "r-9", "status": "PASSED", "passed": 1, "failed": 0, "error": 0})
    responses[("GET", "/api/sequences")] = [
        [{"id": 7, "name": "Nightly DAG", "step_count": 2,
          "archived": False, "latest_version": 1}],
    ]
    fake_client(responses)
    result = runner.invoke(app, BASE_ARGS + ["run", "Nightly DAG", "--target-type", "sequence",
                                             "--source-env", "dev", "--poll-interval", "0"])
    assert result.exit_code == 0


def test_run_unknown_sequence_name_exits_4(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/sequences"): [[]]})
    result = runner.invoke(app, BASE_ARGS + ["run", "Ghost", "--target-type", "sequence",
                                             "--source-env", "dev"])
    assert result.exit_code == 4


def test_run_multiple_sequence_matches_exits_3(fake_client):
    from etl_framework.cli.app import app

    fake_client({("GET", "/api/sequences"): [
        [{"id": 7, "name": "dup", "step_count": 1, "archived": False, "latest_version": 1},
         {"id": 8, "name": "dup", "step_count": 1, "archived": False, "latest_version": 1}],
    ]})
    result = runner.invoke(app, BASE_ARGS + ["run", "dup", "--target-type", "sequence",
                                             "--source-env", "dev"])
    assert result.exit_code == 3


def test_run_invalid_target_type_fails(fake_client):
    from etl_framework.cli.app import app

    fake_client({})
    result = runner.invoke(app, BASE_ARGS + ["run", "7", "--target-type", "bogus",
                                             "--source-env", "dev"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_cli_app.py -v -k target_type`
Expected: FAIL — `typer` reports `--target-type` as an unknown option (usage error), so
every new test fails.

- [ ] **Step 3: Implement `--target-type`**

In `etl_framework/cli/app.py`, replace `_resolve_selection` (lines 76–87) with:

```python
def _resolve_target(client: AtomClient, target_type: str, target: str) -> int:
    path = "/api/selections" if target_type == "selection" else "/api/sequences"
    if target.isdigit():
        return int(target)
    matches = [s for s in client.get_json(path) if s.get("name") == target]
    if not matches:
        raise AtomNotFoundError(f"no {target_type} named {target!r}")
    if len(matches) > 1:
        raise AtomAPIError(
            f"multiple {target_type}s named {target!r}; use the numeric id"
        )
    return int(matches[0]["id"])
```

Then update the `run` command (lines 129–192). Add the new option right after
`selection: str = typer.Argument(...)`:

```python
    target_type: str = typer.Option(
        "selection", "--target-type", help="Target type: 'selection' or 'sequence'"
    ),
```

And inside the function body, replace:

```python
        selection_id = _resolve_selection(client, selection)
```

with:

```python
        if target_type not in ("selection", "sequence"):
            raise typer.BadParameter("--target-type must be 'selection' or 'sequence'")
        target_id = _resolve_target(client, target_type, selection)
```

And replace:

```python
        launched = client.post_json(f"/api/selections/{selection_id}/launch",
                                    payload)
```

with:

```python
        launch_path = (
            f"/api/selections/{target_id}/launch" if target_type == "selection"
            else f"/api/sequences/{target_id}/launch"
        )
        launched = client.post_json(launch_path, payload)
```

`typer.BadParameter` raised inside the `try` block is not one of the `except` clauses
already there — check that it propagates as a usage error (exit code 2, not one of the
gate codes). This matches how `report`'s `--format` validation works elsewhere in the
same file, which raises `typer.BadParameter` outside any try/except.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_app.py -v`
Expected: PASS — the full CLI test file, old and new tests together.

- [ ] **Step 5: Commit**

```bash
git add etl_framework/cli/app.py tests/unit/test_cli_app.py
git commit -m "feat(cli): add --target-type sequence support to atom run"
```

---

## Task 4: CI script consolidation

**Files:**
- Create: `scripts/ci/run-atom-target.sh`
- Delete: `scripts/ci/run-atom-selection.sh`

No Python/pytest test covers this script (the existing script had none either — only
`splice_readme.py`, which is unchanged, has unit tests). Verification is a manual dry run
in Step 3.

- [ ] **Step 1: Create the new script**

Create `scripts/ci/run-atom-target.sh`:

```bash
#!/usr/bin/env bash
# Launch an Atom Job Selection or Execution Sequence from GitLab CI via the
# `atom` CLI, gate the pipeline on its result, and update README.md with a
# markdown status summary.
#
# Required env vars: ATOM_API_URL, ATOM_API_TOKEN
# Usage: run-atom-target.sh <selection|sequence> <id_or_name> [environment]
set -euo pipefail

TARGET_TYPE="${1:?Usage: run-atom-target.sh <selection|sequence> <id_or_name> [environment]}"
TARGET="${2:?Usage: run-atom-target.sh <selection|sequence> <id_or_name> [environment]}"
ENVIRONMENT="${3:-prod}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${ATOM_API_URL:?ATOM_API_URL must be set}"
: "${ATOM_API_TOKEN:?ATOM_API_TOKEN must be set}"

echo "Launching ${TARGET_TYPE} ${TARGET} against ${ENVIRONMENT} via atom CLI..."

stdout_file=$(mktemp)
set +e
atom run "${TARGET}" --target-type "${TARGET_TYPE}" --source-env "${ENVIRONMENT}" \
  --ci-commit-sha "${CI_COMMIT_SHA:-unknown}" \
  --ci-pipeline-url "${CI_PIPELINE_URL:-}" \
  --ci-ref "${CI_COMMIT_REF_NAME:-unknown}" \
  --output json > "${stdout_file}"
gate_code=$?
set -e

cat "${stdout_file}"
# --output json prints a JSON object on the happy/failed/error/cancelled paths,
# but a bare run_id line (no JSON) on timeout -- WaitTimeoutError in
# etl_framework/cli/app.py prints it unconditionally, ignoring --output. Handle
# both: try JSON first, fall back to the raw first line.
run_id=$(python3 -c "
import json
with open('${stdout_file}') as f:
    content = f.read().strip()
try:
    print(json.loads(content).get('run_id', ''))
except Exception:
    print(content.splitlines()[0] if content else '')
" 2>/dev/null || true)

if [ -n "${run_id}" ]; then
  echo "Run ${run_id} finished. Fetching markdown summary..."
  if curl -sf "${ATOM_API_URL}/api/runs/${run_id}/markdown-summary" \
      -H "Authorization: Bearer ${ATOM_API_TOKEN}" -o /tmp/atom-run-summary.md; then
    if python3 "${script_dir}/splice_readme.py" README.md /tmp/atom-run-summary.md; then
      git config user.email "atom-ci-bot@localhost"
      git config user.name "atom-ci-bot"
      git add README.md
      if git diff --cached --quiet; then
        echo "README already up to date; nothing to commit."
      else
        git commit -m "chore: update ${TARGET_TYPE} status for run ${run_id} [skip ci]"
        if ! git push origin "HEAD:${CI_COMMIT_REF_NAME}"; then
          echo "push rejected, retrying after rebase..." >&2
          if ! { git pull --rebase origin "${CI_COMMIT_REF_NAME}" && git push origin "HEAD:${CI_COMMIT_REF_NAME}"; }; then
            echo "warning: README push failed after retry; continuing (pipeline result unaffected)" >&2
          fi
        fi
      fi
    else
      echo "warning: README marker splice failed; continuing (pipeline result unaffected)" >&2
    fi
  else
    echo "warning: could not fetch markdown summary for run ${run_id}; skipping README update" >&2
  fi
else
  echo "warning: no run_id captured (launch likely failed before a run was created); skipping README update" >&2
fi

exit "${gate_code}"
```

Make it executable:

Run: `chmod +x scripts/ci/run-atom-target.sh`

- [ ] **Step 2: Delete the old script**

Run: `git rm scripts/ci/run-atom-selection.sh`

- [ ] **Step 3: Manual dry run against a local API**

This step requires a running Atom API (`uvicorn api.main:app` or the docker-compose
integration stack) with at least one job selection saved, and the `atom` console script
installed (`pip install -e .` from the repo root, since `pyproject.toml` already declares
the `atom` entry point).

Run:
```bash
export ATOM_API_URL=http://localhost:8000
export ATOM_API_TOKEN=<a real token from POST /api/tokens>
bash scripts/ci/run-atom-target.sh selection 1 dev
```
Expected: the script launches the selection via `atom run`, prints run status, and (if
`README.md` has the `ATOM:JOB-STATUS` markers) updates and commits it. Exit code matches
the run's pass/fail state. Repeat with a saved sequence id and `sequence` as the first
argument to confirm both paths work.

- [ ] **Step 4: Commit**

```bash
git add scripts/ci/run-atom-target.sh scripts/ci/run-atom-selection.sh
git commit -m "feat(ci): consolidate CI script into a thin atom-CLI wrapper, support sequences"
```

---

## Task 5: Frontend — generalize the CI/CD Integration modal

**Files:**
- Modify: `frontend/features/launch.js`
- Modify: `frontend/partials/tab-launch.html`
- Modify: `frontend/partials/tab-sequences.html`

No Python test covers frontend JS in this repo; verification is the Playwright e2e suite
in Task 6, plus a manual check in Step 4 below.

- [ ] **Step 1: Generalize `openCiIntegrationModal`**

In `frontend/features/launch.js`, replace the `openCiIntegrationModal` method (lines
1236–1252):

```javascript
    openCiIntegrationModal(target, targetType) {
      targetType = targetType || 'selection';
      const fallback = targetType === 'sequence'
        ? { id: 1, name: 'default-sequence' }
        : { id: 1, name: 'default-selection' };
      const resolved = target || (targetType === 'selection'
        ? (this.jobSelections && this.jobSelections[0])
        : (this.sequences && this.sequences[0])) || fallback;
      const label = targetType === 'sequence' ? 'Execution Sequence' : 'Job Selection';
      const yaml = [
        `atom-${targetType}:`,
        `  stage: test`,
        `  script:`,
        `    - ./scripts/ci/run-atom-target.sh ${targetType} ${resolved.id}`,
        `  rules:`,
        `    - if: '$CI_COMMIT_BRANCH == "main"'`,
      ].join('\n');
      this.ciIntegrationModal = {
        targetId: resolved.id,
        targetName: resolved.name,
        targetTypeLabel: label,
        yamlSnippet: yaml,
      };
      this.showCiIntegrationModal = true;
    },
```

- [ ] **Step 2: Update the modal markup and the selection call site**

In `frontend/partials/tab-launch.html`:

- Line 1354 (modal title) changes from:
  ```html
  <h2 id="ciIntegrationModalTitle" class="text-lg font-bold mb-4">CI/CD Integration — <span x-text="ciIntegrationModal.selectionName"></span></h2>
  ```
  to:
  ```html
  <h2 id="ciIntegrationModalTitle" class="text-lg font-bold mb-4">CI/CD Integration — <span x-text="ciIntegrationModal.targetTypeLabel"></span> — <span x-text="ciIntegrationModal.targetName"></span></h2>
  ```
- Line 9's button call site changes from `openCiIntegrationModal()` to
  `openCiIntegrationModal(null, 'selection')` — explicit, matching the new second
  parameter (passing nothing still defaults to `'selection'` inside the function, so this
  is a clarity change, not a behavior change).

- [ ] **Step 3: Add the CI/CD button to the Sequences tab**

In `frontend/partials/tab-sequences.html`, in the detail card header (around line 39–43),
change:

```html
      <div class="flex items-center justify-between">
        <div class="card-title" x-text="selectedSequence && selectedSequence.name"></div>
        <button class="btn btn-secondary btn-sm" data-testid="sequence-edit-btn"
                @click="openSequenceVersionEditor()">Edit as new version</button>
      </div>
```

to:

```html
      <div class="flex items-center justify-between">
        <div class="card-title" x-text="selectedSequence && selectedSequence.name"></div>
        <div class="flex gap-2">
          <button class="btn btn-secondary btn-sm" data-testid="sequence-ci-btn"
                  @click="openCiIntegrationModal(selectedSequence, 'sequence')">CI/CD</button>
          <button class="btn btn-secondary btn-sm" data-testid="sequence-edit-btn"
                  @click="openSequenceVersionEditor()">Edit as new version</button>
        </div>
      </div>
```

- [ ] **Step 4: Manual verification**

Run the app locally (see the `run` skill / project's normal dev-server startup) and:
1. Go to the Launch tab, click the existing GitLab CI button, confirm the modal still
   shows "CI/CD Integration — Job Selection — <name>" and a snippet calling
   `run-atom-target.sh selection <id>`.
2. Go to the Sequences tab, select a saved sequence, click the new "CI/CD" button, confirm
   the modal shows "CI/CD Integration — Execution Sequence — <name>" and a snippet calling
   `run-atom-target.sh sequence <id>`.

- [ ] **Step 5: Commit**

```bash
git add frontend/features/launch.js frontend/partials/tab-launch.html frontend/partials/tab-sequences.html
git commit -m "feat(ui): generalize CI/CD Integration modal to cover sequences"
```

---

## Task 6: Update the e2e test for the renamed script

**Files:**
- Modify: `tests/e2e/40-live-docker-gitlab-retry.spec.ts`

- [ ] **Step 1: Update the assertion and add a sequence case**

Replace the file's content:

```typescript
import { test, expect } from './fixtures';

test.describe('GitLab CI Integration and Retry', () => {
  test('renders GitLab CI modal with snippet for a job selection', async ({ authedPage }) => {
    await authedPage.goto('/#launch');
    await expect(authedPage.locator('#btn-gitlab-ci-snippet')).toBeVisible();
    await authedPage.click('#btn-gitlab-ci-snippet');
    await expect(authedPage.locator('.gitlab-ci-modal')).toBeVisible();
    await expect(authedPage.locator('.gitlab-ci-snippet-code')).toContainText('run-atom-target.sh selection');
  });

  test('renders GitLab CI modal with snippet for a sequence', async ({ authedPage }) => {
    await authedPage.goto('/#sequences');
    await authedPage.click('[data-testid^="sequence-row-"]');
    await authedPage.click('[data-testid="sequence-ci-btn"]');
    await expect(authedPage.locator('.gitlab-ci-modal')).toBeVisible();
    await expect(authedPage.locator('.gitlab-ci-snippet-code')).toContainText('run-atom-target.sh sequence');
  });
});
```

This assumes at least one saved sequence exists in the e2e fixture data so
`[data-testid^="sequence-row-"]` resolves to something clickable — check
`tests/e2e/17-sequences.spec.ts` for how that suite seeds a sequence, and reuse the same
seeding approach (either an existing fixture sequence or a `beforeEach` that creates one)
if the second test can't find a row to click.

- [ ] **Step 2: Run the e2e test**

Run: `npx playwright test tests/e2e/40-live-docker-gitlab-retry.spec.ts`
Expected: PASS. (Per this project's known quirk, run the raw `npx playwright test`
command directly rather than through any wrapping tool that might mangle reporter output.)

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/40-live-docker-gitlab-retry.spec.ts
git commit -m "test(e2e): cover sequence CI/CD modal, update renamed-script assertion"
```

---

## Task 7: Full verification pass

- [ ] **Step 1: Run the full unit suite**

Run: `python -m pytest tests/unit -v`
Expected: PASS, zero failures, zero errors.

- [ ] **Step 2: Run the full e2e suite** (if a live/docker environment is available)

Run: `npx playwright test`
Expected: PASS. If no live backend is available in the current environment, note this
explicitly rather than claiming the suite passed.

- [ ] **Step 3: Confirm no leftover references to the deleted script**

Run: `grep -rn "run-atom-selection.sh" --include="*.py" --include="*.js" --include="*.html" --include="*.ts" .`
Expected: no output (only historical spec/plan docs under `docs/superpowers/` should
still mention the old name, which is correct — they're a record of what was true when
written, not live documentation).
