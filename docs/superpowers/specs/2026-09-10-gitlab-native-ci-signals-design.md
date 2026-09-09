# GitLab-Native CI Signals — Design Spec

**Date:** 2026-09-10
**Status:** Approved (brainstorming session)
**Sub-project:** 2 of 4 (remaining GitLab CI/CD uplift), following
`2026-09-09-sequence-cicd-launch-parity-design.md` (sub-project 1). Later sub-projects:
CI-facing API hardening (scoped trigger tokens, artifacts store, webhooks), CI runs
dashboard. Each gets its own spec.

## Problem

Today a GitLab pipeline running an Atom job selection or sequence learns pass/fail only
through the CI script's own exit code (`docs/superpowers/specs/2026-07-05-gitlab-cicd-job-integration-design.md`
explicitly deferred GitLab-side signals: "GitLab Commit Status API / MR comment
integration — exit code is sufficient for now"). That means a reviewer looking at a merge
request sees nothing from Atom directly — no separate check in the MR widget, no summary
comment. They have to go find the pipeline job log.

## Solution

`scripts/ci/run-atom-target.sh` (added in sub-project 1) gains two call sites into a new
sibling script, `scripts/ci/post-gitlab-status.sh`, which posts a GitLab Commit Status
(`pending` before the run, `success`/`failed`/`canceled` after) and, on merge-request
pipelines only, upserts one sticky MR comment per target with the run's markdown summary.

The call to GitLab happens entirely in CI, using GitLab's own per-job `CI_JOB_TOKEN` —
**no new credential storage in Atom, no Atom server changes at all.** This mirrors how
the README-splice step already works today (CI does its own git push with GitLab-provided
credentials; Atom just serves the data).

---

## 1. `scripts/ci/post-gitlab-status.sh` (new)

```
post-gitlab-status.sh <state> <target_type> <target> <description> [note_body_file]
```

- `state`: one of `pending|success|failed|canceled` — GitLab's own Commit Status
  vocabulary, computed by the caller (`run-atom-target.sh`), not by this script.
- `target_type`, `target`: the same values `run-atom-target.sh` already receives
  (`selection|sequence`, and the id/name passed to `atom run --target-type`).
- `description`: short human text shown in GitLab's UI (e.g. `"3 passed, 1 failed"`).
- `note_body_file`: optional. When given, also upserts a sticky MR comment (see §3).

### Commit Status

Always attempted (both branch and MR pipelines — a commit status is keyed by SHA, not by
MR):

```
POST $CI_API_V4_URL/projects/$CI_PROJECT_ID/statuses/$CI_COMMIT_SHA
Header: JOB-TOKEN: $CI_JOB_TOKEN
Params: state, context=atom/<target_type>/<target-slug>, target_url=$ATOM_API_URL, description
```

`context` is namespaced per target (`atom/selection/nightly-regression`,
`atom/sequence/17`) so two different selections/sequences launched against the same commit
don't overwrite each other's status, and so it's recognizable in GitLab's UI as coming
from Atom.

### Required environment

`CI_API_V4_URL`, `CI_PROJECT_ID`, `CI_COMMIT_SHA`, `CI_JOB_TOKEN`, `ATOM_API_URL` — all
either GitLab-predefined or already required by `run-atom-target.sh`. If any of the
GitLab-provided ones are missing (non-GitLab CI, or a misconfigured job), the script skips
entirely with a warning to stderr and exits `0` — this is additive behavior, never a
pipeline-breaking one.

`CI_JOB_TOKEN` is never logged: no `set -x`, no `-v` on curl, errors print only the HTTP
status and GitLab's own JSON error body (not the request).

---

## 2. Sticky MR comment

Only attempted when `note_body_file` is given **and** `CI_MERGE_REQUEST_IID` is set
(GitLab only sets this on merge-request pipelines; absent on branch/tag pipelines — skip
silently, not a warning, since this is the expected common case for a branch push).

1. `GET $CI_API_V4_URL/projects/$CI_PROJECT_ID/merge_requests/$CI_MERGE_REQUEST_IID/notes`
2. Search response bodies for the marker `<!-- ATOM:CI-COMMENT:<target_type>:<target> -->`
   (same HTML-comment convention as the README's `ATOM:JOB-STATUS` markers — invisible in
   GitLab's rendered markdown).
3. Found → `PUT .../notes/<id>` with the new body. Not found → `POST .../notes`.
4. Body = the marker line, then the contents of `note_body_file` verbatim (the same
   markdown-summary content already fetched for the README splice — no new formatting
   code).

One sticky comment per `(target_type, target)` pair, so a selection and a sequence
triggered on the same MR each get their own comment, updated in place on every push
rather than accumulating.

Any failure in this section (list/create/update) is a warning, never affects the script's
exit code.

---

## 3. `scripts/ci/run-atom-target.sh` — two new call sites

Right after the existing usage/env guards, **before** launching:

```bash
bash "${script_dir}/post-gitlab-status.sh" pending "${TARGET_TYPE}" "${TARGET}" "Running via Atom..."
```

After the run completes (after the existing `gate_code`/`run_id` extraction, before the
final `exit "${gate_code}"`):

```bash
gitlab_state=failed
case "${gate_code}" in
  0) gitlab_state=success ;;
  2) gitlab_state=canceled ;;
  *) gitlab_state=failed ;;   # 1 (failed), 3 (error), 4/5/6 (not found/connection/timeout) all read as failed
esac
description="$(python3 -c "
import json
try:
    d = json.load(open('${stdout_file}'))
    print(f\"{d.get('passed', 0)} passed, {d.get('failed', 0)} failed, {d.get('error', 0)} error\")
except Exception:
    print('${gitlab_state}')
" 2>/dev/null || echo "${gitlab_state}")"
bash "${script_dir}/post-gitlab-status.sh" "${gitlab_state}" "${TARGET_TYPE}" "${TARGET}" \
  "${description}" /tmp/atom-run-summary.md
```

Both calls are best-effort (failure inside `post-gitlab-status.sh` already can't affect
the exit code; the calls themselves aren't wrapped in anything that could either — no
`set -e` interaction, since the script already runs with `set +e` around the CLI call and
`set -e` resumes only for the git/README section, and `post-gitlab-status.sh` handles its
own errors internally and always exits 0).

---

## 4. No Atom server or database changes

No new table, no new endpoint, no new stored credential. The markdown-summary and JSON
run-status endpoints this reuses already exist from prior work. This keeps the "GitLab
Commit Status / MR comment integration" item that the original spec deferred fully
additive, matching how the rest of the CI/CD uplift has been layered.

---

## 5. Frontend

No required change — the generated `.gitlab-ci.yml` snippet in the CI/CD Integration
modal (`frontend/features/launch.js::openCiIntegrationModal`) doesn't need new variables;
`CI_JOB_TOKEN` is automatically available to every GitLab CI job. Optionally, the modal's
static help text gains one line noting that MR comments only appear on merge-request
pipelines (branch pipelines still get the commit status).

---

## 6. Known limitation (not fixed here)

`target_url` on the commit status points at Atom's root UI (`$ATOM_API_URL`), not the
specific run — there is no per-run deep-link route in the frontend today (checked:
`frontend/app.js`'s hash router only handles top-level tab ids; the markdown-summary's own
`[View full run in Atom](/#/runs/{run_id})` link is already dead for the same reason).
Fixing that is a separate, unrelated frontend gap, out of scope for this sub-project.

---

## 7. Error Handling

| Situation | Behaviour |
|---|---|
| GitLab env vars missing (`CI_API_V4_URL`/`CI_PROJECT_ID`/`CI_JOB_TOKEN`) | `post-gitlab-status.sh` skips entirely, warns to stderr, exits 0. |
| Commit status POST fails (network, 403, etc.) | Warning to stderr; script continues, still exits 0. |
| `CI_MERGE_REQUEST_IID` unset (branch pipeline) | MR-comment step silently skipped — not a warning, this is the expected common case. |
| MR notes list/create/update fails | Warning to stderr; commit status (already posted) is unaffected; script exits 0. |
| `note_body_file` missing/empty (e.g. markdown-summary fetch already failed upstream in `run-atom-target.sh`) | `post-gitlab-status.sh` still posts the commit status; skips the MR-comment step since there's nothing to post. |
| Any of the above | Never changes `run-atom-target.sh`'s own exit code, which is driven solely by `atom run`'s gate code. |

---

## 8. Testing

- **Static:** `bash -n` on the new script; guard-clause behavior for missing GitLab env
  vars (skip, exit 0); a mocked/stubbed `curl` (or a local HTTP echo server) to verify the
  request shapes (method, URL, headers, params) for commit-status create, MR-note list,
  MR-note create (no marker found), and MR-note update (marker found) without needing a
  real GitLab instance.
- **Manual:** run `run-atom-target.sh` end to end against a real (or sandbox) GitLab
  project and a local Atom instance, on both a branch pipeline (expect: commit status
  only) and a merge-request pipeline (expect: commit status + one sticky comment, updated
  on a second push rather than duplicated).

---

## Out of Scope (this sub-project)

- Fixing the pre-existing dead per-run deep link (`#/runs/<id>`) — noted as a known
  limitation, not addressed here.
- Non-GitLab CI systems posting equivalent signals (Jenkins build status, GitHub Actions
  checks) — GitLab-specific by design; the script degrades to a no-op warning elsewhere.
- Any Atom-side credential storage for GitLab (ruled out by the architecture decision in
  §"Solution" — `CI_JOB_TOKEN` is sufficient and requires none).
- Scoped/limited-permission Atom API tokens (sub-project 3).
- A CI runs dashboard in the frontend (sub-project 4).
