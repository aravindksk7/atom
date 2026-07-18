# Web UI Help Center Refresh Design

**Date:** 2026-07-18
**Status:** Approved

## Goal

Refresh the web UI Help Center so users can discover and use the latest Atom capabilities, especially the HTTP-only `atom` CLI, CI/CD gating, JUnit export, scheduler/reporting automation, and related API workflows.

## Scope

In scope:
- Update existing searchable Help Center content in `frontend/help-content.js`.
- Add a focused `CLI & CI/CD` section covering install/config, `atom selections`, `atom runs`, `atom run`, `atom report`, artifacts, exit codes, and pipeline usage.
- Refresh existing automation guidance so CI/CD users prefer the new HTTP-only CLI over legacy direct-DB runner gates.
- Add JUnit/reporting details to History/Reports-oriented help content.
- Add E2E coverage that proves the new help topics are visible and searchable.

Out of scope:
- Redesigning the Help Center layout.
- Adding rich markdown/code-block rendering.
- Changing CLI behavior or API endpoints.

## Design

Use the existing Help Center data model: `window.ETL_HELP.sections[]`, where each section has `id`, `title`, `intro`, and `steps[]`, and each step has `title`, `text`, optional `where`, optional `tip`, and optional `warn`.

The update is content-only. The UI already renders searchable sections, sidebar navigation, where badges, tips, and cautions. Keeping this structure avoids layout risk and lets users search for `CLI`, `JUnit`, `atom run`, `exit code`, `GitLab`, `scheduler`, and `report`.

## Content Requirements

- Document `atom` as the preferred CI/CD path when CI cannot access Atom's database directly.
- Document global CLI options: `--api-url`, `--token`, `--output text|json`, `ATOM_API_URL`, and `ATOM_API_TOKEN`.
- Document `atom run SELECTION --source-env dev --target-env qa` with polling and gate exit codes.
- Document artifact flags: `--junit-out`, `--json-out`, and `--html-out`.
- Document `atom report RUN_ID --format junit|json|csv|html --out PATH`.
- Document exit codes `0` through `6`.
- Mention the JUnit endpoint `GET /api/runs/{run_id}/junit` for API users.
- Keep secret handling warnings visible: no hard-coded tokens or credentials.

## Testing

- Extend `tests/e2e/11-help.spec.ts` to verify the new CLI/JUnit help section is visible and searchable.
- Run the help E2E test if the Playwright setup is available.
- At minimum, validate `frontend/help-content.js` syntax with Node.
