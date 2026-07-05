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
if ! curl -sf "${ATOM_API_URL}/api/runs/${run_id}/markdown-summary" -H "${auth_header}" -o /tmp/atom-run-summary.md; then
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
        if ! { git pull --rebase origin "${CI_COMMIT_REF_NAME}" && git push origin "HEAD:${CI_COMMIT_REF_NAME}"; }; then
          echo "warning: README push failed after retry; continuing (pipeline result unaffected)" >&2
        fi
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
