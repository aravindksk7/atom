# ATOM regression demo

Records a narrated video of ATOM running one business date end to end:

Business Date → 4 ETL Sets → ~100 ETLs → Output Files → Reconciliation → Impact Validation →
BO Reports → Consolidated Result → summary email.

The script drives the real UI at presentation pace, highlights each element before using it, and
shows a visible cursor and chapter captions. It also asserts each validation point, so a broken take
fails instead of recording something wrong.

## Record

You need Docker, and nothing else on the host. No host ports are published.

```bash
docker compose -f docker-compose.demo.yml down -v
docker compose -f docker-compose.demo.yml up -d --build --wait atom
docker compose -f docker-compose.demo.yml run --rm demo-runner
docker compose -f docker-compose.demo.yml down -v
```

The video is written to `demo/output/atom-regression-demo.webm` (1920x1080).

Always start with `down -v`. A run needs an empty ATOM database and empty ETL output folders, and
seeding stops with a clear message if they aren't empty.

## Change the pace

`DEMO_PACE` multiplies every pause. The default is `1`.

```bash
docker compose -f docker-compose.demo.yml run --rm -e DEMO_PACE=1.5 demo-runner   # slower
docker compose -f docker-compose.demo.yml run --rm -e DEMO_PACE=0 demo-runner     # smoke test
```

A take at the default pace is about 14 minutes. The ETL stage runs in real time whatever the pace,
about 3 minutes for 100 ETLs. Talk over it, or cut it down in your video editor.

## Narration

The `STEP` comments in `atom-regression.demo.ts` are written to be read aloud, in order.

## What is real and what is simulated

| Part | In this demo |
|---|---|
| ATOM API, UI, comparison engine, reports, email hook | Real |
| Postgres | Real container with seeded data |
| Oracle | Simulated: a Postgres container with Oracle-style names, connection `ORACLE_PROD` |
| CrushFTP | Simulated: `tests/helpers/fake_crushftp.py`, reached through ATOM's real CrushFTP client |
| AWS S3 | Simulated: Floci, reached through ATOM's real S3 file-server profile |
| SAP Data Services, SAP BusinessObjects | Simulated: `docker/sapds-mock`, `docker/sapbo-mock` |
| Email delivery | Caught by Mailpit |

The opening title card says this on screen. Please keep it.

## Limits

- ATOM's Oracle driver and SQL dialect are not exercised. Don't use this video as evidence of Oracle
  support, and keep the Config screen out of any re-recording, since it shows `ORACLE_PROD` as Postgres.
- Expected and unexpected differences are separated by a reviewer accepting them with a note. ATOM has
  no rules that categorise them automatically.
- The email is a text summary with pass/fail counts. The report isn't attached.
- The business date is fixed at `2026-06-03`, the date the SAP BO mock serves dated rows for.

## Seeded results

`demo/seed/generate_data.py` documents these, and `tests/unit/test_demo_generate_data.py` checks them.

| Seed | Where | Result |
|---|---|---|
| `fee_amount` raised 10% on purpose | About a third of the feeds, plus trades | Expected, accepted on screen |
| Record missing from Test | `gl_005.csv` | Exception |
| Additional record in Test | `gl_020.csv` | Exception |
| Wrong `notional` | `gl_003.xml` | Exception |
| Wrong `notional` | QA Postgres vs Production Oracle | Exception |
| Row missing from Postgres | Postgres vs CrushFTP flat file | Exception |
| Wrong amount total | BO Sales Summary | Exception |
| ETL failure | `SET3_ETL_075` | Failed step; `gl_075.csv` is reported as an unmatched file |

The run ends FAILED with 102 steps passed and 6 failed. Six data exceptions stay open after review.
