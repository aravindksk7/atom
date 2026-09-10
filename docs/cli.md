# atom - CI/CD command line client

Thin HTTP client for the Atom API. Install with `pip install -e .` (or the
published package); the `atom` entry point is registered via `[project.scripts]`.

## Configuration

| Option | Env var | Purpose |
|---|---|---|
| `--api-url` | `ATOM_API_URL` | Base URL of the Atom API (required) |
| `--token` | `ATOM_API_TOKEN` | Bearer token |
| `--output text|json` | - | Human vs machine output |

## Commands

### atom run TARGET

Launch a Job Selection or Execution Sequence (by numeric id or exact name), poll
until it finishes, write artifacts, and exit with the gate code.

```bash
atom run "Nightly Regression" --target-type selection --source-env dev --target-env qa \
    --junit-out atom-junit.xml --json-out atom-run.json \
    --ci-commit-sha "$CI_COMMIT_SHA" --ci-pipeline-url "$CI_PIPELINE_URL" \
    --ci-ref "$CI_COMMIT_REF_NAME"
```

Options: `--target-type selection|sequence` (default `selection`, so existing
calls with no flag are unchanged), `--source-env` (required), `--target-env`
(default empty), `--timeout` (default 3600s), `--poll-interval` (default 10s),
`--no-wait` (launch, print run id, exit 0), `--junit-out`, `--json-out`, and
`--html-out`.

### atom report RUN_ID

```bash
atom report run-abc123 --format junit --out junit.xml
```

`--format junit|json|csv|html` (default json). `--out` writes to a file
(required for html).

### atom selections / atom runs

Discovery listings. `atom runs --limit N` caps the list (default 20).

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Run passed / command succeeded |
| 1 | Run failed |
| 2 | Run cancelled |
| 3 | Run error |
| 4 | Selection or run not found |
| 5 | Auth or connection failure (after retries) |
| 6 | Timed out waiting for completion (run id printed to stdout) |

## GitLab CI example

```yaml
atom-tests:
  stage: test
  script:
    - pip install etl-framework
    - atom run "Nightly Regression" --target-type selection --source-env dev --target-env qa \
        --ci-commit-sha "$CI_COMMIT_SHA" \
        --ci-pipeline-url "$CI_PIPELINE_URL" \
        --ci-ref "$CI_COMMIT_REF_NAME" \
        --junit-out atom-junit.xml
  artifacts:
    when: always
    reports:
      junit: atom-junit.xml
```

This calls `atom` directly for JUnit reporting into GitLab's own test-report UI. To also
get the README status splice and the GitLab-native signals below, call
`scripts/ci/run-atom-target.sh selection "Nightly Regression" dev` instead — it wraps this
same CLI and adds both. The Launch tab's / Sequences tab's **CI/CD** button generates the
`run-atom-target.sh` form of this snippet for either target type.

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
