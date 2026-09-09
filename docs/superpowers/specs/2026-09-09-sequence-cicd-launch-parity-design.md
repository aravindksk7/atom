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
    source_env: str
    target_env: str = ""
    source_connection: str | None = None
    target_connection: str | None = None
    config_id: int | None = None
    config_data: dict[str, Any] = Field(default_factory=dict)
    version: int | None = None          # pin a sequence_version; None = latest
    ci_context: dict[str, Any] | None = None
```

Handler reuses every helper `launch_selection` (`api/routes/selections.py`) already calls —
no new business logic, just no selection row wrapping the run:

1. `resolve_sequence(db, SequenceRef(sequence_id=id, sequence_version=body.version))` → 404
   if the sequence or pinned version doesn't exist.
2. `check_preconditions(db, resolved.preconditions)` → **422, no run row created** if the
   gate refuses (matches the sequence spec's "no run is created" rule).
3. `_validate_env_requirements(resolved.as_linear_steps(), jobs_by_name, body.target_env)`.
4. Build `RunTrigger` from `resolved.steps` (the real DAG) and `resolved.defaults` merged
   under caller-supplied values (sequence defaults lose to explicit request fields, per the
   sequence spec's environment-agnostic rule).
5. `RunRepository(db).create_run(selection_id=None, ci_context=body.ci_context, ...)`;
   `config_snapshot["sequence"] = resolved.snapshot_meta()`.
6. Audit-logged as `sequence.launched` (mirrors `selection.launched`), same fields.

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

Sequence detail view (`frontend/partials/tab-sequences.html`, `frontend/features/sequences.js`)
gains a "CI/CD Integration" tab, matching the existing Job Selection one:

1. Create an API token — same shortcut to `/api/tokens`, reused as-is.
2. GitLab CI/CD variables block (`ATOM_API_URL`, `ATOM_API_TOKEN`) — identical to the
   selection panel.
3. `.gitlab-ci.yml` snippet, pre-filled with the sequence's real id:
   ```yaml
   atom-sequence:
     stage: test
     script:
       - pip install etl-framework
       - scripts/ci/run-atom-target.sh sequence 17 prod
   ```

Purely generative, same as the selection panel — no server-side persistence.

The existing Job Selection CI/CD panel's snippet updates to use `run-atom-target.sh
selection <id> prod` in place of the old script name.

---

## 5. Error Handling

| Situation | Behaviour |
|---|---|
| Unknown sequence id/name | `404` from the launch endpoint; CLI exits `4` (`EXIT_NOT_FOUND`). |
| Pinned `version` doesn't exist | `404`, same as an unknown sequence. |
| Precondition gate refuses | `422`, no run row created; CLI exits `3` (`EXIT_ERROR`) with the gate's reason in the error body. Matches "no run is created" from the sequence spec — the CI script sees a clean non-zero exit with no run_id to report on. |
| `job_name` in the resolved sequence missing/disabled | `422` from `_validate_env_requirements`, same as today's selection path. |
| Launch succeeds, run fails/errors/gets cancelled | CLI's existing gate-code mapping (unchanged) — `1`/`3`/`2` respectively. |
| Markdown-summary fetch fails after a terminal run | Script logs a warning, does not touch the pipeline's exit code (`gate_code` from `atom run` is preserved verbatim). |
| README push rejected (race, branch protection) | One retry after `git pull --rebase`; second failure is a warning only. |

---

## 6. Testing

- **Server unit:** new route tests mirroring `launch_selection`'s existing suite — happy
  path, precondition failure (422, no run row), missing/pinned version, unknown sequence,
  `ci_context` stored verbatim, `config_snapshot["sequence"]` provenance present.
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
