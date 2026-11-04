# ATOM Regression Demo Runner: Design

**Date:** 2026-11-04, revised 2026-11-05
**Status:** Implemented. `demo/README.md` is the operating guide.

The first version ran about 100 ETLs and ended with a summary email. The user's revised brief replaced
that with the seven parts below: about 25 execution jobs of mixed types, about 10 reconciliation jobs,
series, parallel and hybrid flows, restart from failure, CI/CD, and a timeline synced to the video. The
email is not in the revised brief, so it was removed.

## Goal

Record a narrated video of ATOM running one end-to-end regression for a chosen business date.

A Playwright script drives the real ATOM UI at presentation pace. It highlights each element before
acting on it, shows a banner naming the current step, and uses its step comments as the narration.

## Decisions

| Topic | Decision |
|---|---|
| Realism | Real ATOM API, UI, orchestration, restart and comparison engine. Only external systems are simulated. |
| Run style | Live runs on seeded data, not a replay and not browser-stubbed API calls. |
| Expected vs unexpected | Reviewer decision on screen: search Differences for the field and bulk-accept with a note. No rules engine is added. |
| Outcome | A run stops on an unintended failure, is restarted, and ends FAILED on real data exceptions. |
| ATOM product code | No changes. |
| Timeline | Generated from each take, so its timecodes always match that take's video. |

The opening title card states that Oracle, CrushFTP, AWS S3, SAP and GitLab are simulated.

## Architecture

`docker-compose.demo.yml` is separate from the test and integration compose files. It publishes no host
ports, so it cannot hit Windows excluded port ranges.

| Service | Image or source | Role |
|---|---|---|
| `atom` | `Dockerfile.test`, uvicorn | Real ATOM API and UI |
| `atom-db` | `postgres:16` | ATOM's own repository (SQLite locked under load and returned 500s) |
| `seed` | `Dockerfile.test` | Generates files, tables and baselines |
| `demo-runner` | `Dockerfile.test` | Seeds ATOM through its API and records with Playwright |
| `sapds` | `docker/sapds-mock` | SAP Data Services, runs the ETLs |
| `sapbo` | `docker/sapbo-mock` | SAP BusinessObjects reports |
| `postgres` | `postgres:16` | Real Postgres data source |
| `oracle-sim` | `postgres:16` | Simulated Oracle: Oracle-style names, config `ORACLE_PROD`, `db_type: postgresql` |
| `crushftp-sim` | `demo/mocks/crushftp_sim.py` around `tests/helpers/fake_crushftp.py` | Simulated CrushFTP |
| `s3-sim` | `floci/floci` | Simulated AWS S3 (`minio/minio` could not be pulled) |
| `gitlab-sim` | `demo/mocks/gitlab_ci_sim.py` | Simulated GitLab runner, labelled as simulated on its page. Runs the real `scripts/ci/run-atom-target.sh` |

### Change to the SAP DS mock

Optional environment variables add a configurable job list and copy each job's template files into a
folder named from the business date it received. A wrong date therefore produces unmatched file pairs, so
the date check is real. With none of them set, the mock behaves as it did before.

## The sequence

35 jobs in nine stages. `demo/README.md` lists each job.

| Stage | Mode | Jobs |
|---|---|---|
| Set 1 | Series | CrushFTP file watcher, CrushFTP fetch, 3 ETLs, validation |
| Set 2 | Parallel | 6 ETLs |
| Set 2 validation | Series | Validation |
| Set 3 start | Series | 1 ETL |
| Set 3 middle | Parallel | 3 ETLs |
| Set 3 end | Series | 1 ETL, validation |
| Set 4 | Series | 4 ETLs, publish to AWS S3, validate the upload |
| Reconcile | Parallel | 7 file and database comparisons |
| BO | Series, starts on `all_done` | 3 BO comparisons |

BO starts on `all_done` because a data difference is a real result and must not stop later comparisons.
Within BO, each report waits for the one before it, so the late file blocks the report after it.

### Pacing the run to the narration

- The Set 1 file watcher waits for an arrival signal, and the script delivers it once the presenter is on
  the Monitor. This holds the run until the narration reaches it.
- Parallel ETLs run longer than serial ones, so several show RUNNING at the same moment. The Monitor
  refreshes a few seconds behind the API, so the highlight waits until the screen shows them running.

### Unintended failure and restart

The Production copy of the Daily Sales report is withheld in `bo-late/`, so its job errors and the next
report is blocked. The script delivers the file, then presses Restart from failure. ATOM creates a new
linked run, carries over the 29 steps that passed, and re-runs the other 6.

Comparisons that found differences count as failed, so they run again and find the same differences. This
is why the review happens after the restart, on the run that will be kept.

## Video flow

| Part | Content |
|---|---|
| 1. Configure | Sequence, stage modes, execution preview, business date |
| 2. Execute | 25 jobs live: series, parallel, hybrid, S3 publish |
| 3. Reconcile | 10 comparisons, and their results in History |
| 4. Restart | The failure, the delivered file, continuing from the failure |
| 5. Validate | Accept `fee_amount` on each affected result, and 6 exceptions stay open |
| 6. Report | Consolidated report |
| 7. CI/CD | Generated GitLab job, simulated pipeline, CI Runs tab |

## Validation and error handling

The script asserts each validation point, and a failed assertion fails the take:

- **Correct ETLs:** the SAP DS mock's own log holds exactly the 18 ETLs.
- **Correct sequence:** in that log, every set finished before the next began.
- **Correct date:** every ETL received the business date.
- **Flow:** one job at a time in Set 1, all 6 together in Set 2, and all 3 together in the middle of Set 3.
- **Failure:** the late-file job errored, and the next report was blocked.
- **Restart:** no ETL ran twice, 29 steps were carried over, and the blocked report ran.
- **Numbers narrated aloud:** 35 jobs, 30 passed, 5 failed, 6 open exceptions.
- **Pipeline:** a real ATOM run finished with verdict FAILED and exit code 1, so the pipeline failed for the
  reason the narration gives.

## Timeline

`demo/helpers/timeline.ts` writes `demo/output/timeline.md` after each take. It takes timecodes from the
presenter's step calls and narration from the script's `STEP` comments, so there is one source of truth.

## Limits

See "Limits" in `demo/README.md`.

## Findings in ATOM

Seen while building this. None were changed, as they are out of scope.

1. The Differences column dropdown is empty for multi-file results, because they carry no `column_stats`.
2. The Differences "Open" and "Accepted" metrics count the whole run, not the selected test.
3. The execution order preview stays empty when the sequence editor opens, until the first edit.
4. The Monitor shows `? → ?` for environments, and a total that grows as steps finish rather than the
   sequence's full size.
5. History shows `? → ?` for environments on sequence runs in the same way.
6. The multi-file Files column repeats itself: "63 file(s) across 63 file(s) across 63 pair(s)".
7. SQLite returned `database is locked` 500s to status polling during a long run.
8. Matrix compare SQL sources ignore `config_id` and use ATOM's own database unless given a connection
   string. This can silently query the wrong database.
9. Some UI actions took tens of seconds while recording, such as opening the launch modal and working in
   Differences. The cause wasn't identified, and it may be specific to headless recording in Docker.
