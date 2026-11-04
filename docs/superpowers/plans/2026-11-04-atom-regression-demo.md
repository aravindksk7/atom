# ATOM Regression Demo Runner Implementation Plan

> **For agentic workers:** executed inline with superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** One Docker command records a narrated, highlighted 1920x1080 video of ATOM running a
business-date regression across 100 ETLs, reconciliation, BO reports and a summary email.

**Architecture:** `docker-compose.demo.yml` runs real ATOM with simulated external systems. A
Python generator seeds files, tables and baselines. A Playwright global setup creates ATOM objects
through the API. A Playwright test drives the UI through presenter helpers and asserts each
validation point.

**Tech stack:** Docker Compose, Playwright (TypeScript), Python 3.12, MinIO, Mailpit, Postgres 16.

**Spec:** `docs/superpowers/specs/2026-11-04-atom-regression-demo-design.md`

## Facts from reading the code

These replace the assumptions the spec listed for verification.

- `compare_type: matrix` SQL sources ignore `config_id` and need `connection_string`. Oracle vs
  Postgres therefore uses `compare_type: sql` (`config_id_a`, `config_id_b`), which honours saved
  configs. Postgres vs flat file stays `matrix`, with `connection_string` on the SQL side.
- `substitute_in_job` walks all string leaves in `params`, so `{{run_date}}` works in DS job params,
  SQL, S3 and CrushFTP paths, and BO prompt answers.
- A launch override only applies when the variable exists globally: create `run_date` through
  `POST /api/variables` first.
- The launch modal sends no `config_id`, so the sequence's `defaults` must carry `config_id` and
  `run_settings.use_live_connections: true`.
- Plain HTTP CrushFTP needs `CRUSHFTP_ALLOW_INSECURE_HTTP=1` on `atom`.
- `FakeCrushFtp.start()` binds `127.0.0.1:0`, so the simulator runs its own server on `_Handler`.
- BO doc `1003` serves date-dependent rows, for `2026-06-02` and `2026-06-03`. The default business
  date is `2026-06-03`, so the BO prompt answer changes the data for real.
- Use the `run.completed` hook event, which fires once whatever the final status.

## File map

| File | Responsibility |
|---|---|
| `docker/sapds-mock/server.py` | Optional job list and date-keyed output publishing |
| `demo/seed/generate_data.py` | Pure dataset builder and writers for files, tables and S3 |
| `demo/mocks/crushftp_sim.py` | Serves `FakeCrushFtp` on the Docker network |
| `demo/seed/seed_atom.ts` | Playwright global setup, creates ATOM objects |
| `demo/helpers/presenter.ts` | Spotlight, pace, typing, cursor, captions |
| `demo/atom-regression.demo.ts` | Narrated script and assertions |
| `demo/playwright.demo.config.ts` | Video, viewport, timeouts |
| `docker-compose.demo.yml` | The stack |
| `demo/README.md` | Recording instructions |
| `tests/unit/test_demo_generate_data.py` | Dataset tests |
| `tests/unit/test_sapds_mock_outputs.py` | Mock extension tests |

## Tasks

### Task 1: SAP DS mock extension (TDD)

- [ ] Write failing tests: job file merges over defaults; publishing copies template files to the
  date folder; no env vars means no behaviour change.
- [ ] Implement `SAPDS_MOCK_JOBS_FILE`, `SAPDS_MOCK_TEMPLATE_DIR`, `SAPDS_MOCK_OUTPUT_DIR`,
  `SAPDS_MOCK_OUTPUT_DATE_VAR`, and a `/debug/runs` listing.
- [ ] Run `C:\Python314\python.exe -m pytest tests/unit/test_sapds_mock_outputs.py -v`. Commit.

### Task 2: Data generator (TDD)

- [ ] Write failing tests: determinism; 100 ETLs; 64 CSV and 40 XML feeds; each seeded exception
  appears exactly where the spec says; non-impacted fields equal.
- [ ] Implement `build_dataset(business_date)` and the writers. Run tests. Commit.

### Task 3: Stack

- [ ] Write `demo/mocks/crushftp_sim.py` and `docker-compose.demo.yml`.
- [ ] Run `docker compose -f docker-compose.demo.yml config --quiet`. Bring the stack up and confirm
  `seed` exits 0 and `atom` is healthy. Commit.

### Task 4: ATOM seed and a headless proof run

- [ ] Write `demo/seed/seed_atom.ts` and `demo/playwright.demo.config.ts`.
- [ ] Launch the sequence through the API and assert step counts and result counts against the seed
  table. Fix mismatches. Commit.

### Task 5: Presenter and script

- [ ] Write `demo/helpers/presenter.ts` and `demo/atom-regression.demo.ts`.
- [ ] Smoke run with `DEMO_PACE=0` until green. Commit.

### Task 6: Record and document

- [ ] Record at full pace, extract frames from the video, and check them by eye: highlight, cursor,
  captions, legibility.
- [ ] Write `demo/README.md`, ignore `demo/output/`, update the spec for the `sql` compare type and
  measured duration. Commit.
