# ATOM regression demo

Records a narrated video of ATOM running one business date end to end:

Business Date → 4 ETL Sets with live progress → Output Files → Reconciliation → Gantt → Restart from
failure → Impact Validation → BO Reports → Consolidated Result → CI/CD.

The script drives the real UI at presentation pace, highlights each element before using it, and
shows a visible cursor and a banner naming the current step. It also asserts each validation point,
so a broken take fails instead of recording something wrong.

## Record

You need Docker, and nothing else on the host. No host ports are published.

```bash
docker compose -f docker-compose.demo.yml down -v
docker compose -f docker-compose.demo.yml up -d --build --wait atom gitlab-sim
docker compose -f docker-compose.demo.yml run --rm demo-runner
docker compose -f docker-compose.demo.yml down -v
```

Each take writes to `demo/output/`:

| File | Contents |
|---|---|
| `atom-regression-demo.webm` | The video, 1920x1080 |
| `timeline.md` | Every part and step with its start, end and length in that video, plus narration |
| `timeline.srt` | The same narration as cues. Open it with the video in a player or editor to read along |

`demo/TIMELINE.md` is the timeline from the take recorded when this was built. Timecodes change with
each take, so use `demo/output/timeline.md` alongside any new recording.

Always start with `down -v`. A run needs an empty ATOM database and empty ETL output folders, and
seeding stops with a clear message if they aren't empty.

## Record from the screen, for smoother video

Playwright's own recording can look choppy. This records the same take from the screen instead, with
the browser full screen on Windows and the video encoded on the GPU. It needs Node with this repo's
`npm ci`, and `ffmpeg` on `PATH` with `h264_qsv`, which means an Intel GPU, and a 1920x1080 display.

```bash
node demo/record-screen.cjs
```

It resets the demo stack before and after, and writes `demo/output/atom-regression-demo.mp4`. The screen
belongs to the take until it ends, about 27 minutes:

- Don't touch the mouse or keyboard, because they reach the browser being recorded.
- Turn on Do Not Disturb, because anything that appears on screen is recorded.
- Keep the machine from sleeping or locking, and close what you can.

## Change the pace

`DEMO_PACE` multiplies every pause. The default is `1`.

```bash
docker compose -f docker-compose.demo.yml run --rm -e DEMO_PACE=1.5 demo-runner   # slower
docker compose -f docker-compose.demo.yml run --rm -e DEMO_PACE=0 demo-runner     # smoke test
```

A take at the default pace is about 27 minutes, of which about 15 can be cut. The jobs run in real time
whatever the pace, and some screens respond slowly while recording, most of all for 2 minutes or so
after each "Accept all". `timeline.md` marks each of these stretches with `Spare`, to trim in your
editor.

## Narration

The `STEP` comments in `atom-regression.demo.ts` are written to be read aloud, and the step banner on
screen shows the same number, and stays up at least long enough to say them. `timeline.md` lists them
with their timecodes and about how long each takes to say, and points out steps with spare time to
trim.

## What the video shows

| Part | Content |
|---|---|
| 1. Configure | The sequence, its stages and execution preview, entering the business date |
| 2. Execute | 25 jobs with a live progress bar each: Set 1 in series, Set 2 in parallel, Set 3 hybrid, Set 4 publishing to S3 |
| 3. Reconcile | 10 comparisons: 7 file and database jobs in parallel, then 3 BO reports in series. The Monitor then shows the run's Gantt |
| 4. Restart | A job fails because a file is late. Restart continues from there, carrying over 29 passed jobs, with its own progress, Gantt and a place in the sequence's Recent runs |
| 5. Validate | Expected `fee_amount` changes are accepted, and 6 exceptions stay open |
| 6. Report | The consolidated report |
| 7. CI/CD | The generated GitLab job, a simulated pipeline run, and ATOM's CI Runs tab |

### Execution jobs (25)

| Set | Flow | Jobs |
|---|---|---|
| 1 | Series | File watcher on CrushFTP, CrushFTP fetch, 3 ETLs, validation |
| 2 | Parallel | 6 ETLs together, then validation |
| 3 | Hybrid | 1 ETL, then 3 together, then 1 ETL, then validation |
| 4 | Series | 4 ETLs, publish to AWS S3, validate the upload |

### Reconciliation jobs (10)

| Job | Compares | Seeded result |
|---|---|---|
| `RECON_CSV_OUTPUTS_VS_S3_BASELINE` | 14 generated CSVs vs S3 baselines | `fee_amount` plus a missing and an additional record |
| `RECON_XML_OUTPUTS_VS_S3_BASELINE` | 6 generated XMLs vs S3 baselines | `fee_amount` plus one wrong `notional` |
| `RECON_S3_PUBLISHED_VS_LOCAL_OUTPUTS` | Published S3 files vs local outputs | Match |
| `COMPARE_TRADES_POSTGRES_VS_ORACLE` | QA Postgres vs Production Oracle | `fee_amount` plus one wrong `notional` |
| `COMPARE_BALANCES_POSTGRES_VS_ORACLE` | QA Postgres vs Production Oracle | Match |
| `COMPARE_POSITIONS_POSTGRES_VS_FLAT_FILE` | QA Postgres vs CrushFTP flat file | One row missing from Postgres |
| `COMPARE_POSITIONS_ORACLE_VS_FLAT_FILE` | Production Oracle vs CrushFTP flat file | Match |
| `BO_SALES_ORDERS_VS_PROD` | Live BO report vs Production copy | Match |
| `BO_DAILY_SALES_VS_PROD` | Live BO report vs Production copy | Errors until the late file arrives, then matches |
| `BO_SALES_SUMMARY_VS_PROD` | Live BO report vs Production copy | Blocked, then a wrong amount total |

`demo/seed/generate_data.py` documents the seeded differences, and
`tests/unit/test_demo_generate_data.py` checks them.

## What is real and what is simulated

| Part | In this demo |
|---|---|
| ATOM API, UI, orchestration, progress bars, Gantt, restart, comparison engine, reports, CI script | Real |
| The 5 earlier nightly runs | Seeded: `demo/seed/seed_history.py` writes their runs and results directly, because the bars need at least 3 results per job to estimate a percent |
| Postgres | Real container with seeded data |
| Oracle | Simulated: a Postgres container with Oracle-style names, connection `ORACLE_PROD` |
| CrushFTP | Simulated: `tests/helpers/fake_crushftp.py`, reached through ATOM's real CrushFTP client |
| AWS S3 | Simulated: Floci, reached through ATOM's real S3 file-server profile |
| SAP Data Services, SAP BusinessObjects | Simulated: `docker/sapds-mock`, `docker/sapbo-mock` |
| GitLab | Simulated: `demo/mocks/gitlab_ci_sim.py` runs ATOM's real `scripts/ci/run-atom-target.sh` |

The opening title card says this on screen, including that the earlier nights are seeded, and the
GitLab page labels itself as simulated. Please keep both.

## Limits

- ATOM's Oracle driver and SQL dialect are not exercised. Don't use this video as evidence of Oracle
  support, and keep the Config screen out of any re-recording, since it shows `ORACLE_PROD` as Postgres.
- Expected and unexpected differences are separated by a reviewer accepting them with a note. ATOM has
  no rules that categorise them automatically, and acceptances don't carry into a later run, so the
  CI run reports `fee_amount` again.
- The "unintended failure" is scripted: a Production file is withheld, and the script delivers it
  just before pressing Restart.
- Restart re-runs every step that didn't pass, so failed comparisons run again and find the same
  differences. Passed steps, including all the ETLs, are not repeated.
- The CI run uses `run_date`'s configured value, since the `atom` CLI has no variable override. The
  script sets that value off screen before the pipeline runs.
- The simulated pipeline can't post commit statuses, so its log says GitLab wasn't detected.
- The GitLab job shown in the CI/CD modal uses the modal's default `dev` environment, while the
  simulated pipeline runs `qa` to `prod`.
- The business date is fixed at `2026-06-03`, the date the SAP BO mock serves dated rows for.
- The 7 parallel comparisons have `wait_seconds: 12`, ATOM's real per-step wait, because they otherwise
  finish in a few seconds and the audience wouldn't see them run. Elapsed times include that wait, so
  they aren't performance figures.
- A progress percent is an estimate from each job's history, not a measure from inside the job. The
  seeded durations in `demo/seed/seed_atom.ts` were measured from this stack, so a slower machine can
  show some bars as over estimate, in amber.
