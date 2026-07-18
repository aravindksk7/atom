# Atom CI/CD CLI + JUnit Interop — Design

**Date:** 2026-07-18
**Status:** Approved (brainstorming session)
**Sub-project:** 1 of 3 (CI/CD uplift). Later sub-projects: CI-facing API (scoped trigger tokens, artifacts store, webhooks), CI runs dashboard. Each gets its own spec.

## Goal

Give any CI system (GitLab, Jenkins, GitHub Actions) a first-class way to drive Atom: launch a Job Selection, wait for the outcome, gate the pipeline on it, and collect standard test artifacts (JUnit XML, JSON, HTML) — without shell scripts or direct DB access.

## Scope

**In scope**
- New `atom` console-script CLI (Typer-based) in a new `etl_framework/cli/` package. HTTP-only client of the existing FastAPI API.
- New server endpoint `GET /api/runs/{run_id}/junit` rendering a run's per-job results as JUnit XML.
- Commands: `atom run`, `atom report`, `atom selections`, `atom runs`.

**Out of scope (later sub-projects)**
- Scoped CI trigger tokens / API keys, artifacts store API, webhooks/notifications.
- CI runs dashboard in the frontend.
- pytest plugin (JUnit XML + JSON judged sufficient for interop).
- Changes to the legacy runner CLI (`etl_framework/runner/cli.py`) — it stays as-is; its direct-DB `--gate-run`, `--scheduler-stats`, `--scheduler-report` modes remain supported.

## Architecture

New package `etl_framework/cli/`:

| Module | Responsibility |
|---|---|
| `app.py` | Typer application, subcommand definitions, exit-code mapping. Entry point `main()`. |
| `client.py` | `AtomClient` — thin `requests` wrapper: base URL, token header, per-request timeout, bounded retries (`tenacity`, already a dependency). No business logic. |
| `render.py` | Output formatting: human text tables and `--output json`. |

- Console script: `[project.scripts] atom = "etl_framework.cli.app:main"` in `pyproject.toml`.
- New dependency: `typer>=0.12`.
- The CLI imports nothing from `etl_framework.repository` or `api.*` — it talks only HTTP. This keeps it installable/usable on thin CI runners and keeps one code path.

Server side, following the existing `markdown-summary` pattern:
- `api/services/junit_export.py` — pure function building JUnit XML from a run + its results.
- Route `GET /api/runs/{run_id}/junit` in `api/routes/runs.py`, `Content-Type: application/xml`. Mapping: testsuite = run (name, timestamp, duration, counts), testcase = per-job test result (classname = job name), `<failure>`/`<error>` nodes carry status, mismatch counts, and message. Same auth as other run endpoints.

## Commands

Global options on every command:
- `--api-url` (env `ATOM_API_URL`, required)
- `--token` (env `ATOM_API_TOKEN`)
- `--output text|json` (default text)

### `atom run SELECTION`
1. Resolve `SELECTION` by numeric id, else by exact name via `GET /api/selections`.
2. `POST /api/selections/{id}/launch`, passing `ci_context` built from `--ci-commit-sha`, `--ci-pipeline-url`, `--ci-ref` (existing launch-endpoint feature).
3. Poll `GET /api/runs/{run_id}/status` until terminal status. `--poll-interval` (default 10s), `--timeout` (default 3600s).
4. Optionally write artifacts: `--junit-out PATH` (from the new junit endpoint), `--json-out PATH` (run detail JSON), `--html-out PATH` (`GET /api/runs/{run_id}/report`).
5. Exit with gate code (below). `--no-wait`: print run_id (or JSON) after launch and exit 0, skipping steps 3–5.

### `atom report RUN_ID`
`--format junit|json|csv|html` (default json), `--out PATH` (default stdout; html requires `--out`). Named `--out` to avoid colliding with the global `--output text|json` flag. Fetches from the corresponding existing endpoints (`/junit`, `/{run_id}`, `/{run_id}/export`, `/{run_id}/report`). Exit 0 on success.

### `atom selections`
List job selections (`GET /api/selections`): id, name, job count, last run. Text table or JSON.

### `atom runs`
List recent runs (`GET /api/runs`), `--limit N` (default 20): run_id, status, passed/failed/error counts, started time.

## Exit codes

Aligned with the legacy `--gate-run` semantics:

| Code | Meaning |
|---|---|
| 0 | Run passed (or non-gating command succeeded) |
| 1 | Run failed (failed > 0 or status FAILED) |
| 2 | Run cancelled |
| 3 | Run error (error > 0 or status ERROR) |
| 4 | Selection or run not found |
| 5 | Auth or connection failure (after retries) |
| 6 | Timed out waiting for run completion |

## Error handling

- Transient network errors during polling: retried with `tenacity` (bounded attempts, backoff); only after exhaustion → exit 5.
- Human-readable errors go to stderr; machine output (JSON, JUnit) to stdout or `--output` file. With `--output json`, errors are emitted as a JSON object `{"error": ..., "exit_code": ...}` on stderr.
- Timeout on `atom run` prints the run_id before exiting 6 so a later `atom report` can pick it up.
- HTTP 401/403 → exit 5 with a hint about `ATOM_API_TOKEN`.

## Testing

TDD throughout.

- **CLI unit tests:** Typer `CliRunner` with a mocked `AtomClient` — command parsing, exit-code mapping, artifact writing, `--no-wait`, timeout path.
- **Client unit tests:** `AtomClient` against monkeypatched `requests` — headers, retries, error translation.
- **Server tests:** FastAPI `TestClient` for the junit endpoint — XML parsed with `xml.etree` and asserted on structure for: passing run, run with failures/errors, empty run, unknown run (404).
- **Integration lane (docker):** extend `docker-compose.integration.yml` usage — run the installed `atom` CLI against the containerized API for a happy-path `atom run` and `atom report junit`.

## CI usage example (GitLab)

```yaml
atom-tests:
  stage: test
  script:
    - pip install etl-framework
    - atom run "Nightly Regression"
        --ci-commit-sha "$CI_COMMIT_SHA"
        --ci-pipeline-url "$CI_PIPELINE_URL"
        --ci-ref "$CI_COMMIT_REF_NAME"
        --junit-out atom-junit.xml
  artifacts:
    when: always
    reports:
      junit: atom-junit.xml
```
