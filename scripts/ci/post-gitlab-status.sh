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
