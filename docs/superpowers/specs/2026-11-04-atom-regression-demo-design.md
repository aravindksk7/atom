# ATOM Regression Demo Runner: Design

**Date:** 2026-11-04
**Status:** Approved in brainstorming, pending written-spec review

## Goal

Record a narrated video of ATOM running one end-to-end regression for a chosen
business date:

Business Date → 4 ETL sets → ~100 ETLs → output files → reconciliation → impact
validation → BO reports → consolidated result → summary email.

A Playwright script drives the real ATOM UI at presentation pace, highlights each
element before acting on it, and uses its step comments as the narration script.

## Decisions

| Topic | Decision |
|---|---|
| Realism | Real ATOM API, UI, database and comparison engine. Only external systems are simulated. |
| Run style | A live run on seeded data, not a replay and not browser-stubbed API calls. |
| Expected vs unexpected | Reviewer decision on screen: filter Differences by column and bulk-accept with a note. No rules engine is added. |
| Email | The existing email notification hook sends a text summary to a recipient list. The report is not attached or linked. |
| Outcome | The run finds real exceptions and ends FAILED, with evidence. |
| ATOM product code | No changes. |

The opening caption states: "Demo environment: Oracle, CrushFTP, AWS S3 and SAP
are simulated."

## Architecture

`docker-compose.demo.yml` is separate from the test and integration compose files.
It publishes no host ports except an optional one for watching the app, so it cannot
hit Windows excluded port ranges.

| Service | Image or source | Role |
|---|---|---|
| `atom` | `Dockerfile.test`, uvicorn, throwaway SQLite | Real ATOM API and UI |
| `demo-runner` | `Dockerfile.test` | Seeding and Playwright recording |
| `sapds` | `docker/sapds-mock` | SAP Data Services, runs the ETLs |
| `sapbo` | `docker/sapbo-mock` | SAP BusinessObjects reports |
| `postgres` | `postgres:16` | Real Postgres data source |
| `oracle-sim` | `postgres:16` | Simulated Oracle: Oracle-style names, connection `ORACLE_PROD`, `db_type: postgresql` |
| `crushftp-sim` | Wrapper around `tests/helpers/fake_crushftp.py` | Simulated CrushFTP holding the Production flat file |
| `s3-sim` | `minio/minio` | Simulated AWS S3 holding Production CSV/XML baselines |
| `mailpit` | `axllent/mailpit` | Catches the summary email and shows it in an inbox |

A shared volume holds ETL output files, readable by `atom` through
`SERVER_FILE_ALLOWED_DIRS`.

### Files

```
docker-compose.demo.yml
demo/
  README.md                     how to record, change pace, find the video
  playwright.demo.config.ts     1920x1080, video on, no webServer
  atom-regression.demo.ts       the narrated script
  helpers/presenter.ts          spotlight, pace, typing, cursor, captions
  seed/generate_data.py         deterministic files, tables and report baselines
  seed/seed_atom.ts             creates ATOM objects through the API
  mocks/crushftp_sim.py         runs FakeCrushFtp as a seeded network service
  output/                       video and evidence (gitignored)
docker/sapds-mock/server.py     additive change, see below
```

### Change to the SAP DS mock

`SCHEDULABLE_JOBS` is hardcoded to two jobs. Add two optional environment variables:

- `SAPDS_MOCK_JOBS_FILE`: JSON map of job name to terminal status, merged over the
  defaults.
- `SAPDS_MOCK_OUTPUT_DIR`: on success, copy the ETL's generated files into a folder
  named from the business date the mock received.

A wrong date therefore produces unmatched file pairs, so the date check is real. With
neither variable set, the mock behaves as it does today.

## Seeded data

`generate_data.py` is deterministic for a given business date.

- 100 ETLs, 25 per set, named like `SET1_ETL_001`.
- 104 file pairs (64 CSV, 40 XML), because 4 ETLs produce two files.
- Non-impacted fields are identical on both sides unless listed below.

| Seed | Location | Expected result |
|---|---|---|
| `fee_amount` changed on purpose | ~40 files, plus Oracle vs Postgres | Expected difference, accepted on screen |
| Missing record | 1 CSV | Exception |
| Additional record | 1 CSV | Exception |
| Wrong `notional` | 1 XML | Exception |
| Wrong `notional` | 1 Oracle vs Postgres row | Exception |
| Row missing from Postgres | Postgres vs flat file | Exception |
| ETL failure | `SET3_ETL_075`, status `Error` | Failed step identified; its baseline shows as an unmatched pair |
| Wrong business-critical total | 1 of 3 BO reports | Exception |

## ATOM setup

`seed_atom.ts` runs before recording, using the API. It creates:

- The DS, BO, Postgres and `ORACLE_PROD` connections.
- CrushFTP and S3 file-server profiles.
- 100 `ds_job` jobs with fast polling, passing the business date as a DS global variable.
- The jobs listed below, and the sequence that orders them.
- SMTP settings pointing at `mailpit`.
- An email hook on `run.failed` with a template showing date and counts, sent to the
  demo recipient list.

### Sequence stages

| # | Stage | Mode | Start rule | Steps |
|---|---|---|---|---|
| 1 | Set 1 | Serial | n/a | 25 `ds_job` |
| 2 | Set 2 | Serial | All passed | 25 `ds_job` |
| 3 | Set 3 | Serial | All passed | 25 `ds_job`; the failing ETL is last |
| 4 | Set 4 | Serial | `all_done` | 25 `ds_job` |
| 5 | Fetch | Serial | `all_done` | `file_transfer` from CrushFTP to local |
| 6 | Reconcile | Parallel | `all_done` | See jobs below |
| 7 | BO | Serial | `all_done` | 3 `bo_live` reconciliation jobs |

The failing ETL is last in Set 3 because a step that never ran stops later stages under
every start rule. Failing mid-set would block the rest of the run.

### Reconcile jobs

| Job | Type | Test side | Production side |
|---|---|---|---|
| CSV outputs | `reconciliation`, `source_mode: multi_file`, `unmatched_policy: warn` | Local ETL output | S3 baseline |
| XML outputs | Same | Local ETL output | S3 baseline |
| Oracle vs Postgres | `compare`, `compare_type: matrix` | Postgres SQL | `ORACLE_PROD` SQL |
| Postgres vs flat file | `compare`, `compare_type: matrix` | Postgres SQL | File fetched from CrushFTP |

Each multi-file job produces one aggregate result with per-pair detail, so one column
filter can accept across all of its pairs.

## Video flow

Every step has a narration comment above it, and every element is highlighted before use.

1. **Configure:** show the caption, open the sequence, show its stages, open Run sequence,
   and type `run_date=<business date>`.
2. **Execute:** launch and watch the Monitor move through Sets 1 to 4, then call out the
   failed ETL and its error.
3. **Reconcile:** show the CrushFTP fetch step, then the CSV, XML and database results,
   the per-pair breakdown, and one mismatch opened to show Test vs Production.
4. **Validate:** in Differences, filter to `fee_amount` and bulk-accept with a note on each
   affected result. Show the exceptions that stay open: missing record, additional record
   and wrong value. Then show the BO total difference.
5. **Report:** show the consolidated run and HTML report, then open the summary email in
   Mailpit with its recipient list.

## Presenter helpers

- `spotlight(locator)`: applies `outline: 3px solid #FF3366; outline-offset: 2px;
  background: rgba(255, 51, 102, 0.1);`, holds it, and removes it after the action.
- `pace(kind)`: waits 1.5 to 2.5 seconds, scaled by `DEMO_PACE`.
- `typeLikeHuman(locator, text)`: uses `pressSequentially` with a delay.
- `clickDemo`, `selectDemo`, `typeDemo`: wrap move cursor, spotlight, act and clear.
- Fake cursor: an injected element that glides to each target, because Playwright video
  doesn't show the real pointer.
- `chapter(title)`: shows a caption banner for each of the five chapters.

## Validation and error handling

The script asserts each validation point as it runs:

- **Correct ETLs:** 100 steps ran, with the expected names.
- **Correct sequence:** step start times follow the stage order.
- **Correct date:** the mock's record of received variables shows the chosen date for every ETL.
- **Completion:** 99 ETLs succeeded and `SET3_ETL_075` failed.
- **Reconciliation:** exception counts match the seed table.
- **Outcome:** the run ends FAILED, and the email reached Mailpit with the expected recipients.

A failed assertion fails the take and keeps its video and trace, so a bad take can't pass
silently. Seeding fails fast with a clear message if a service isn't ready. `video: 'on'`
is used, so successful takes are always kept.

## Testing

- Unit tests for `generate_data.py`: deterministic output, and the seed table is honoured.
- Tests for the DS mock's new variables; existing mock integration tests keep passing.
- `DEMO_PACE=0` runs the full script quickly as a smoke test before recording.
- A final full-speed take is watched and its duration recorded.

## Limits

- ATOM's Oracle driver and SQL dialect are not exercised, because `ORACLE_PROD` is Postgres.
  The config screen stays out of the video, and the opening caption discloses the simulation.
- Accepting differences is a human decision, not automatic categorisation. Narration should
  say so.
- The email is a summary and doesn't include the accepted-difference breakdown.
- Estimated length is 12 to 15 minutes, to be measured on the first take.

## To verify during implementation

Each item has a fallback or needs a design update if it proves false.

1. `run_date` substitutes into `ds_job` variables, matrix SQL, and S3 and CrushFTP paths.
2. Accepted differences leave a run FAILED while exceptions remain, including on `compare` jobs.
3. The Differences bulk accept honours the column filter.
4. The CrushFTP profile accepts an HTTP endpoint, and `FakeCrushFtp` supports the
   `file_transfer` download.
5. `multi_file` jobs can read one side locally and the other from S3.
6. The hook fires and renders correctly for a run ending FAILED.
7. Monitor stays readable with 100+ steps at 1920x1080.

## Out of scope

- Expected-change rules or automatic categorisation.
- Attaching or linking the report in email.
- Audio, editing or voiceover.
- Any change to the uncommitted difference-export work in the working tree.
