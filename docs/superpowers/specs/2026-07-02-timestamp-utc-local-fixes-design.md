# Timestamp UTC/local consistency fixes

## Problem

The app's overall timestamp convention is correct: store everything in UTC (`DateTime(timezone=True)` columns, `datetime.now(timezone.utc)`), serialize as ISO8601 with an explicit offset, and convert to the viewer's local time zone only at the final display layer (`frontend/app.js:fmtDate()` already does this correctly for the browser UI).

Three places break that convention:

1. **HTML report template** (`etl_framework/reporting/templates/report.html.j2:348`) renders a stored UTC `accepted_at` datetime with raw `strftime`, no conversion. This is a server-rendered static file with no client-side JS, so whoever opens it sees a UTC clock time with no indication it isn't their local time.
2. **JSON log formatter** (`etl_framework/utils/logging.py:44-48`) sets `datefmt="%Y-%m-%dT%H:%M:%SZ"`. `asctime` is produced from `time.localtime` (the stdlib default), so the value is already local time — the hardcoded `Z` falsely labels it as UTC.
3. **Two naive `datetime.now()` call sites** (`etl_framework/reconciliation/engine.py:77` for `executed_at`, `etl_framework/automic/client.py:73,82,86` for `checked_at`) produce timezone-naive local-time values, while every other producer of these same fields uses `datetime.now(timezone.utc)` and the DB columns/Pydantic schemas expect timezone-aware UTC. A naive local value flowing into that path is silently treated as UTC downstream, skewing the value by the server's UTC offset.

## Fix

**Storage/API layer stays UTC.** This is deliberate — it's the only convention that survives multiple viewers in different time zones and round-trips correctly through `frontend/app.js`'s existing UTC→browser-local conversion. We are not inverting it.

1. **Report template**: add a Jinja filter `to_local` registered on `ReportGenerator._jinja_env` (`etl_framework/reporting/generator.py`) that converts an aware UTC datetime to the server's local time zone via `.astimezone()` and formats it with a zone abbreviation (`%Y-%m-%d %H:%M %Z`). Update `report.html.j2:348` to use `{{ mm.accepted_at | to_local }}` instead of `mm.accepted_at.strftime('%Y-%m-%d %H:%M')`.
2. **JSON log formatter**: change `datefmt` from `"%Y-%m-%dT%H:%M:%SZ"` to `"%Y-%m-%dT%H:%M:%S%z"` so the emitted offset reflects the actual local UTC offset instead of a fake `Z`. No change to the value itself (it was already local) — only the label becomes truthful.
3. **Naive `datetime.now()` sites**: change to `datetime.now(timezone.utc)` in both `engine.py:77` and `client.py:73,82,86` (add `timezone` to the `datetime` import in `client.py`), matching every other UTC producer in the codebase. This is a correctness fix, not a "make it local" change — these values feed timezone-aware DB columns/schemas and must match that convention.

## Testing

- Existing tests under `tests/` covering `ReportGenerator`, `ReconciliationEngine`, and the Automic client should continue passing; add/extend assertions where these fields are checked.
- New unit test for the `to_local` Jinja filter: given a fixed aware UTC datetime, assert the rendered string reflects the conversion (using a monkeypatched/fixed local timezone if the test environment's TZ isn't controlled, or asserting structurally that the offset conversion occurred).
- New unit test for `configure_logging(log_format="json")` asserting the JSON log record's `timestamp` field carries a real `%z` offset, not a literal `Z` unless the server's local zone actually is UTC.
- New unit test/assertion that `engine.py`'s `executed_at` and `client.py`'s `checked_at` are timezone-aware (`tzinfo is not None`) after the fix.

## Out of scope

- No changes to `frontend/app.js` — its UTC→browser-local conversion is already correct.
- No changes to any other API route, DB column, or `datetime.now(timezone.utc)` call site — they already follow the correct convention.
- No DB migration — no column types change.
