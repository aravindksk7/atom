# App-wide timezone setting

## Problem

The backend correctly stores every timestamp in UTC (`DateTime(timezone=True)` columns, `datetime.now(timezone.utc)`). Two places implicitly derive "local" time instead of letting an admin pick one explicitly:

1. **Display**: `frontend/app.js:fmtDate()` (line ~3520, 18 call sites in `index.html`) converts UTC to whatever the *viewer's browser* reports as its locale/timezone via `toLocaleDateString()`/`toLocaleTimeString()`. Different viewers of the same dashboard see different clock times, and there is no way to pin the app to a specific zone (e.g. the team's office timezone) regardless of who's looking.
2. **Schedule execution**: `api/services/scheduler.py` hardcodes `timezone="UTC"` in both `BackgroundScheduler(timezone="UTC")` (line 95) and `CronTrigger.from_crontab(sched.cron_expr, timezone="UTC")` (line 128). A schedule's cron expression `0 9 * * *` always fires at 9am UTC, never 9am in the timezone the schedule was actually intended for.

This spec adds one admin-configurable IANA timezone, stored server-side, used consistently for both display and cron interpretation.

Related prior work: `2026-07-02-timestamp-utc-local-fixes-design.md` fixed correctness bugs (naive datetimes, mislabeled `Z` in JSON logs) and added a `to_local` Jinja filter to the HTML report template that converts UTC to the **server's OS-local timezone** via bare `.astimezone()`. That filter is updated here (see "Integration with existing report filter" below) to use the same app-configured timezone instead, so the static HTML report and the live dashboard agree.

## Design

### Storage

New single-row table `app_settings`:

```
id INTEGER PRIMARY KEY        -- always 1
timezone VARCHAR(64) NOT NULL DEFAULT 'UTC'
updated_at DATETIME
```

Added as a SQLAlchemy model in `etl_framework/repository/models.py`, plus the usual `CREATE TABLE IF NOT EXISTS` + seed-row shim in `etl_framework/repository/database.py::_ensure_compare_columns` (matching how every other table in this codebase has been backward-compat-migrated for existing SQLite files).

### Repository

`SettingsRepository` in `etl_framework/repository/repository.py`:
- `get_timezone() -> str` — returns the stored value, `"UTC"` if the row is somehow missing.
- `set_timezone(tz: str) -> AppSettings` — validates via `zoneinfo.ZoneInfo(tz)` (raises `ValueError` on an unknown zone name), persists, updates `updated_at`.

### API

New `api/routes/settings.py`, registered in `main.py` under `/api/settings`:
- `GET /api/settings` — any authenticated caller — returns `{"timezone": "UTC"}`.
- `PUT /api/settings` — gated with `dependencies=[Depends(require_admin)]` (same pattern as `tokens.py`) — body `{"timezone": "America/New_York"}`. Validates via the repository (422 on invalid zone name), saves, then calls `scheduler.refresh_all_timezones()` so already-scheduled cron jobs immediately pick up the new zone without an app restart. Logs an audit event (`settings.timezone_changed`) via `AuditService`, matching how schedule/token changes are audited elsewhere.

### Scheduler integration

`api/services/scheduler.py`:
- `_add_job(sched)` looks up the current app timezone (via `SettingsRepository`, one query, executed once per add) instead of the hardcoded `"UTC"` string when building `CronTrigger.from_crontab(sched.cron_expr, timezone=<app_tz>)`.
- New `refresh_all_timezones()`: iterates `ScheduleRepository(db).list_enabled()` and calls `_add_job` again for each (APScheduler's `replace_existing=True` already handles the swap) — the mechanism the `PUT /api/settings` route uses to make a timezone change take effect immediately.
- `BackgroundScheduler(timezone="UTC")` at `start()` stays as the scheduler's own internal bookkeeping default; the per-job `CronTrigger` timezone is what actually determines fire time, so this is unaffected by the setting.

### Frontend

- On startup (after the auth check resolves), fetch `GET /api/settings` once and store the value as `appTimezone` in the root Alpine data.
- `fmtDate()` is rewritten to format via `Intl.DateTimeFormat(undefined, { timeZone: this.appTimezone, dateStyle: 'short', timeStyle: 'short' })` instead of `toLocaleDateString()`/`toLocaleTimeString()` (which used the browser's implicit timezone). All 18 existing call sites in `index.html` are unaffected — same function signature, same return shape (a display string).
- New collapsible card in the Config tab, alongside the existing Security/Notifications cards (`index.html` ~line 224): **"🌐 Regional — Timezone"**. A `<select>` populated from a curated list of ~30 common IANA zones (UTC plus major American, European, Asian, Australian zones) shows/sets the current value. Editing is gated on `activeTokenIsAdmin`, matching the token-management card's convention; non-admins see the current value read-only.
- The schedule modal's cron-expression helper text (`index.html` ~line 1639) appends the active timezone, e.g. `"...= 6am daily (times in America/New_York)"`, so schedule authors aren't surprised by what "6am" means.

### Integration with existing report filter

`etl_framework/reporting/generator.py`'s `to_local` Jinja filter (added by the prior timestamp-fixes spec) currently does a bare `.astimezone()`, which uses the server process's OS timezone. It's changed to accept and use the configured app timezone (`ZoneInfo(SettingsRepository(db).get_timezone())`) instead, so a generated HTML report and the live dashboard show the same wall-clock time for the same event.

## Testing

- New repository test: `SettingsRepository.set_timezone("not-a-zone")` raises; a valid zone round-trips through `get_timezone()`.
- New route tests: `GET /api/settings` returns the default `UTC` on a fresh DB; `PUT /api/settings` as non-admin returns 403; as admin, persists and returns the new value; invalid zone name returns 422.
- New scheduler test: after `PUT /api/settings` changes the zone, an existing schedule's APScheduler job reflects the new `CronTrigger` timezone (assert via the job's trigger object, not by waiting for a real fire).
- Frontend: manual check — set timezone to a non-UTC zone, confirm run history/badges/timestamps across History, Monitor, and Reports tabs all shift consistently; confirm the schedule modal's helper text reflects the chosen zone.

## Out of scope

- No per-user/per-browser override — this is a single app-wide value by explicit choice.
- No full IANA timezone list in the UI dropdown — a curated common list keeps it usable; can be extended later without any schema change.
- No change to the JSON log formatter's timezone (still server OS-local) — operational logs are a distinct, non-user-facing concern from the prior spec, untouched here.
- No migration/backfill of historical data — this only changes how existing UTC values are *interpreted for display* and *how new cron triggers are scheduled*; no stored value changes meaning.

## Known limitation

Changing the app timezone changes the *display* and *future schedule fire times* immediately, but does not retroactively relabel `last_run_at` history or already-fired runs — those remain correct UTC instants, simply rendered in whatever zone is active at view time.
