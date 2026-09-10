#!/usr/bin/env bash
# Launch an Atom Job Selection or Execution Sequence from GitLab CI via the
# `atom` CLI, gate the pipeline on its result, and update README.md with a
# markdown status summary.
#
# Required env vars: ATOM_API_URL, ATOM_API_TOKEN
# Usage: run-atom-target.sh <selection|sequence> <id_or_name> [environment] [target_env]
set -euo pipefail

TARGET_TYPE="${1:?Usage: run-atom-target.sh <selection|sequence> <id_or_name> [environment] [target_env]}"
TARGET="${2:?Usage: run-atom-target.sh <selection|sequence> <id_or_name> [environment] [target_env]}"
ENVIRONMENT="${3:-prod}"
# Optional: only Execution Sequences fall back to a stored default (SequenceDefaults.
# target_env) when this is omitted -- a Job Selection has no such stored default, so
# any job whose type isn't single-environment (api/services/job_env_validation.py's
# SINGLE_ENV_JOB_TYPES) needs this passed explicitly or its launch 422s.
TARGET_ENV="${4:-}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${ATOM_API_URL:?ATOM_API_URL must be set}"
: "${ATOM_API_TOKEN:?ATOM_API_TOKEN must be set}"

echo "Launching ${TARGET_TYPE} ${TARGET} against ${ENVIRONMENT} via atom CLI..."

# Tell GitLab a run is starting. Best effort: post-gitlab-status.sh handles all
# of its own errors and always exits 0, so this cannot affect the pipeline.
bash "${script_dir}/post-gitlab-status.sh" pending "${TARGET_TYPE}" "${TARGET}" \
  "Running via Atom..."

stdout_file=$(mktemp)
set +e
# --output is a top-level `atom` option (etl_framework/cli/app.py's @app.callback()),
# not an option of the `run` subcommand -- it must precede `run`, not trail it.
atom --output json run "${TARGET}" --target-type "${TARGET_TYPE}" --source-env "${ENVIRONMENT}" \
  --target-env "${TARGET_ENV}" \
  --ci-commit-sha "${CI_COMMIT_SHA:-unknown}" \
  --ci-pipeline-url "${CI_PIPELINE_URL:-}" \
  --ci-ref "${CI_COMMIT_REF_NAME:-unknown}" \
  --timeout "${ATOM_POLL_TIMEOUT_SECONDS:-1800}" \
  --poll-interval "${ATOM_POLL_INTERVAL_SECONDS:-10}" \
  > "${stdout_file}"
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
