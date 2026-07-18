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

### atom run SELECTION

Launch a Job Selection (by numeric id or exact name), poll until it finishes,
write artifacts, and exit with the gate code.

```bash
atom run "Nightly Regression" --source-env dev --target-env qa \
    --junit-out atom-junit.xml --json-out atom-run.json \
    --ci-commit-sha "$CI_COMMIT_SHA" --ci-pipeline-url "$CI_PIPELINE_URL" \
    --ci-ref "$CI_COMMIT_REF_NAME"
```

Options: `--source-env` (required), `--target-env` (default empty),
`--timeout` (default 3600s), `--poll-interval` (default 10s), `--no-wait`
(launch, print run id, exit 0), `--junit-out`, `--json-out`, and `--html-out`.

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
    - atom run "Nightly Regression" --source-env dev --target-env qa \
        --ci-commit-sha "$CI_COMMIT_SHA" \
        --ci-pipeline-url "$CI_PIPELINE_URL" \
        --ci-ref "$CI_COMMIT_REF_NAME" \
        --junit-out atom-junit.xml
  artifacts:
    when: always
    reports:
      junit: atom-junit.xml
```
