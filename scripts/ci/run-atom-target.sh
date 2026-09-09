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
