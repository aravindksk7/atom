# Sequence CI/CD Launch Parity — Design Spec

**Date:** 2026-09-09
**Status:** Approved (brainstorming session)
**Sub-project:** 1 of 4 (remaining GitLab CI/CD uplift). Later sub-projects: GitLab-native
signals (Commit Status API, MR comments), CI-facing API hardening (scoped trigger tokens,
artifacts store, webhooks), CI runs dashboard. Each gets its own spec.

## Problem

Job Selections have a full CI/CD path: `.gitlab-ci.yml` snippet, the `atom` CLI, JUnit
export, `ci_context` provenance on the run (`2026-07-05-gitlab-cicd-job-integration-design.md`,
`2026-07-18-atom-cli-cicd-design.md`). Execution Sequences — the saved DAG entity
(`2026-08-12-saved-execution-sequences-design.md`) — have none of this. A sequence can
only be launched by wrapping it in a Job Selection (`sequence_ref`) or attaching it to a
`Schedule`; there is no ad-hoc launch endpoint, and the `atom` CLI's `run` command resolves
selections only. CI/CD pipelines that want to gate on a sequence directly have no path in.

## Solution

Give sequences the same direct CI launch path selections already have: a new ad-hoc launch
endpoint, `atom run --target-type sequence` support in the CLI, and an updated CI script.
While updating the CI script, also fold `scripts/ci/run-atom-selection.sh`'s hand-rolled
curl/bash polling logic into a thin wrapper around the `atom` CLI, which already does that
polling more correctly (retries, real JSON parsing, the CLI's exit-code table) — the script
predates the CLI and has been carrying duplicate logic since the CLI shipped.

No new tables, no new columns on `TestRun`. Provenance already flows through
`config_snapshot["sequence"]`, exactly as it does today for a selection whose version
carries a `sequence_ref` — a direct sequence launch just skips the selection wrapper.

---

## 1. API

### `POST /api/sequences/{id}/launch` (new)

```python
class SequenceLaunchRequest(BaseModel):
    source_env: str | None = None       # None = fall back to resolved.defaults.source_env
    target_env: str | None = None       # None = fall back to resolved.defaults.target_env
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None        # None = fall back to resolved.defaults.config_id
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None          # pin a sequence_version; None = latest
    ci_context: dict[str, Any] | None = None
```

Unlike a selection launch, there is no version row to fall back to for environment and
config — so this is the first real consumer of `SequenceDefaults.source_env` /
`target_env` (defined in the sequence spec, never actually read by either the selection
launch path or the scheduler, which get their env from the selection version / schedule
row instead). Handler mirrors the sequence branch already in `api/services/scheduler.py`
(`_run_schedule`, the `sched.sequence_id is not None` branch) plus `launch_selection`'s
`resolved`/`dag_steps` split:

1. `resolve_sequence(db, SequenceRef(sequence_id=id, sequence_version=body.version))` → 404
   if the sequence or pinned version doesn't exist.
2. `check_preconditions(db, resolved.preconditions)` → **422, no run row created** if the
   gate refuses (matches the sequence spec's "no run is created" rule).
3. Resolve effective values, caller wins: `source_env = body.source_env or resolved.defaults.source_env`
   (**422** if still `None` — a sequence with no default and no caller value has nowhere to
   run), `target_env = body.target_env or resolved.defaults.target_env or ""`,
   `config_id = body.config_id if body.config_id is not None else resolved.defaults.config_id`,
   `run_settings = resolved.defaults.run_settings or {}`.
4. `_validate_env_requirements(resolved.as_linear_steps(), jobs_by_name, target_env)`.
5. Build `RunTrigger(source_env=..., target_env=..., job_sequence=resolved.as_linear_steps(), config_id=..., config_data=body.config_data, run_settings=run_settings)`
   — the flat shape, used only for env validation and the config snapshot, exactly as
   `launch_selection` does. Execution itself is handed `resolved.steps` (the real DAG)
   directly, not `trigger.job_sequence`, matching the `dag_steps` split already used by
   both `launch_selection` and the scheduler's sequence branch.
6. `RunRepository(db).create_run(selection_id=None, ci_context=body.ci_context, ...)`;
   `config_snapshot["sequence"] = resolved.snapshot_meta()`.
7. `background_tasks.add_task(_execute_run, run_id, resolved.steps, ...)`.
8. Audit-logged as `sequence.launched` (mirrors `selection.launched`), same fields.

Returns `202` + `RunStatusOut`, identical shape to the selection-launch response.

---

## 2. CLI

`etl_framework/cli/app.py::run` gains one option:

```python
target_type: str = typer.Option(
    "selection", "--target-type",
    help="Target type: 'selection' or 'sequence'",
)
```

Default `"selection"` — every existing call site and pipeline keeps working unchanged.

```python
def _resolve_target(client: AtomClient, target_type: str, target: str) -> int:
    if target_type not in ("selection", "sequence"):
        raise typer.BadParameter("--target-type must be 'selection' or 'sequence'")
    path = "/api/selections" if target_type == "selection" else "/api/sequences"
    if target.isdigit():
        return int(target)
    matches = [s for s in client.get_json(path) if s.get("name") == target]
    if not matches:
        raise AtomNotFoundError(f"no {target_type} named {target!r}")
    if len(matches) > 1:
        raise AtomAPIError(f"multiple {target_type}s named {target!r}; use the numeric id")
    return int(matches[0]["id"])
```

`_resolve_selection` is replaced by this (kept as a thin wrapper is not needed — no other
caller uses it). The launch call branches on `target_type`:

```python
launch_path = (
    f"/api/selections/{target_id}/launch" if target_type == "selection"
    else f"/api/sequences/{target_id}/launch"
)
```

Everything downstream — poll, gate exit code, `--junit-out`/`--json-out`/`--html-out`,
`--no-wait` — is untouched; one code path serves both target types since both launch
endpoints return the same `RunStatusOut`/run-status shape.

`atom selections` stays as-is. No new `atom sequences` listing command in this
sub-project — `--target-type sequence` plus a numeric id, or an exact sequence name,
covers the CI use case. (A dedicated listing command can be added later if needed; YAGNI
for now, matching how `atom selections` was scoped originally.)

---

## 3. CI script

`scripts/ci/run-atom-target.sh` replaces `scripts/ci/run-atom-selection.sh`:

```
run-atom-target.sh <selection|sequence> <id_or_name> [environment]
```

```bash
#!/usr/bin/env bash
set -euo pipefail
TARGET_TYPE="${1:?Usage: run-atom-target.sh <selection|sequence> <id_or_name> [environment]}"
TARGET="${2:?Usage: run-atom-target.sh <selection|sequence> <id_or_name> [environment]}"
ENVIRONMENT="${3:-prod}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_id=""
set +e
atom run "${TARGET}" --target-type "${TARGET_TYPE}" --source-env "${ENVIRONMENT}" \
  --ci-commit-sha "${CI_COMMIT_SHA:-unknown}" \
  --ci-pipeline-url "${CI_PIPELINE_URL:-}" \
  --ci-ref "${CI_COMMIT_REF_NAME:-unknown}" \
  --output json > /tmp/atom-run-stdout.json
gate_code=$?
set -e

run_id=$(python3 -c "import json; print(json.load(open('/tmp/atom-run-stdout.json'))['run_id'])" 2>/dev/null || true)

if [ -n "${run_id}" ]; then
  if curl -sf "${ATOM_API_URL}/api/runs/${run_id}/markdown-summary" \
      -H "Authorization: Bearer ${ATOM_API_TOKEN}" -o /tmp/atom-run-summary.md; then
    if python3 "${script_dir}/splice_readme.py" README.md /tmp/atom-run-summary.md; then
      git config user.email "atom-ci-bot@localhost"
      git config user.name "atom-ci-bot"
      git add README.md
      if ! git diff --cached --quiet; then
        git commit -m "chore: update ${TARGET_TYPE} status for run ${run_id} [skip ci]"
        if ! git push origin "HEAD:${CI_COMMIT_REF_NAME}"; then
          echo "push rejected, retrying after rebase..." >&2
          git pull --rebase origin "${CI_COMMIT_REF_NAME}" \
            && git push origin "HEAD:${CI_COMMIT_REF_NAME}" \
            || echo "warning: README push failed after retry; continuing" >&2
        fi
      fi
    else
      echo "warning: README marker splice failed; continuing" >&2
    fi
  else
    echo "warning: could not fetch markdown summary for run ${run_id}; continuing" >&2
  fi
fi

exit "${gate_code}"
```

All curl-based launch/poll/JSON-parsing logic is deleted — the `atom` CLI owns launch,
poll, retry, and gate-code mapping now (it already has bounded retries via `tenacity`,
which the old script never had). README-splice remains the one thing the CLI doesn't do,
so the script's only remaining job is that plus preserving the pipeline's exit code from
`atom run`.

`scripts/ci/run-atom-selection.sh` is deleted. Existing pipelines change one call-site
line: `run-atom-selection.sh 42 prod` → `run-atom-target.sh selection 42 prod`.

---

## 4. Frontend

Reality check against the actual UI (the 2026-07-05 spec described a detail-view tab;
what was actually built is a shared modal): `openCiIntegrationModal(sel)` in
`frontend/features/launch.js`, rendered by the always-mounted CI/CD Integration modal in
`frontend/partials/tab-launch.html`, triggered today only from a button on the Job
Selections view. It already covers steps 1–2 (token shortcut, GitLab variables block)
generically; only the YAML snippet and title are selection-specific.

Generalize in place rather than duplicating the modal:

- `openCiIntegrationModal(target, targetType = 'selection')` — `targetType` selects the
  script invocation (`run-atom-target.sh selection <id>` vs `run-atom-target.sh sequence
  <id>`) and a label ("Job Selection" / "Execution Sequence") used in the modal title.
  `ciIntegrationModal.selectionName` is renamed `targetName`; the title in
  `tab-launch.html` becomes `CI/CD Integration — <span x-text="ciIntegrationModal.targetTypeLabel"></span> — <span x-text="ciIntegrationModal.targetName"></span>`.
- The existing selection-list button's call site changes from `openCiIntegrationModal(sel)`
  to `openCiIntegrationModal(sel, 'selection')` (functionally identical, explicit).
- `frontend/partials/tab-sequences.html` gains a "CI/CD" button next to "Edit as new
  version" in the sequence detail card header, calling
  `openCiIntegrationModal(selectedSequence, 'sequence')`.

Purely generative, same as before — no server-side persistence, no new endpoint backing
the modal.

---

## 5. Error Handling

| Situation | Behaviour |
|---|---|
| Unknown sequence id/name | `404` from the launch endpoint; CLI exits `4` (`EXIT_NOT_FOUND`). |
| Pinned `version` doesn't exist | `404`, same as an unknown sequence. |
| No `source_env` in the request and no `SequenceDefaults.source_env` on the sequence | `422` with a clear "source_env required" message; CLI exits `3`. |
| Precondition gate refuses | `422`, no run row created; CLI exits `3` (`EXIT_ERROR`) with the gate's reason in the error body. Matches "no run is created" from the sequence spec — the CI script sees a clean non-zero exit with no run_id to report on. |
| `job_name` in the resolved sequence missing/disabled | `422` from `_validate_env_requirements`, same as today's selection path. |
| Launch succeeds, run fails/errors/gets cancelled | CLI's existing gate-code mapping (unchanged) — `1`/`3`/`2` respectively. |
| Markdown-summary fetch fails after a terminal run | Script logs a warning, does not touch the pipeline's exit code (`gate_code` from `atom run` is preserved verbatim). |
| README push rejected (race, branch protection) | One retry after `git pull --rebase`; second failure is a warning only. |

---

## 6. Testing

- **Server unit:** new route tests mirroring `launch_selection`'s existing suite — happy
  path, precondition failure (422, no run row), missing/pinned version, unknown sequence,
  `ci_context` stored verbatim, `config_snapshot["sequence"]` provenance present,
  `source_env` falling back to `SequenceDefaults.source_env` when omitted, and 422 when
  neither is set.
- **CLI unit:** `--target-type` branching (`selection` vs `sequence` vs invalid value) with
  a mocked `AtomClient`; `_resolve_target` numeric-id and name-lookup paths, including the
  "multiple matches" and "no match" errors, parameterized over both target types.
- **Script test:** update the existing marker-splice test fixtures for the new script name
  and argument order; add a case asserting the script's exit code equals `atom run`'s exit
  code regardless of README-splice outcome.
- **Integration:** launch a two-branch saved sequence end to end via
  `run-atom-target.sh sequence <id>` against the docker-compose integration stack, assert
  the pipeline gates correctly on a failing branch.

---

## Out of Scope (this sub-project)

- GitLab Commit Status API / MR comment posting (sub-project 2).
- Scoped/limited-permission CI trigger tokens (sub-project 3) — reuses existing global
  bearer tokens, same as the selection path.
- A dedicated `atom sequences` listing command.
- Any change to how `Schedule` or `JobSelectionVersion.sequence_ref` reference sequences —
  this is purely an additional, direct, ad-hoc entry point.
