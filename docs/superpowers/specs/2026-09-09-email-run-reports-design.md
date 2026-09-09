# Email Run Reports — Design

**Date:** 2026-09-09
**Status:** Approved (design), pending implementation plan
**Author:** Via brainstorming
**Depends on:** `docs/superpowers/specs` (none formal) — builds on the email notification channel added in PRs #19/#20 (`api/services/notifier.py`, `NotificationHook.channel == "email"`).

## Context
Email notification hooks (added in #19/#20) send a JSON or `{{var}}`-templated body when a subscribed run event fires, but never touch the run's actual HTML report (`GET /api/runs/{run_id}/report`, generated on demand by `ArtifactService.generate_html_report`). Users want the report itself in their inbox — both automatically on hook events, and ad hoc for a specific run.

## Goals
- Email hooks can optionally include a link to (and, size permitting, an attachment of) the run's HTML report.
- Any authenticated user can email a specific run's report to arbitrary addresses on demand, independent of hooks.
- No change to existing hook behavior unless explicitly opted in.
- Reuse the existing SMTP delivery path (`_send_email`, `_resolve_smtp_config`) — no new delivery infrastructure.

## Non-Goals
- Scheduled/periodic digest emails (rollups across multiple runs) — out of scope for this design.
- Per-user notification preferences / accounts — this app has no user-login concept, only API tokens; recipients are addresses typed at hook-creation or send time.
- Configurable size cap — hardcoded, not exposed as a setting.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Report link base URL | New `AppSettings.app_url`, env fallback `ETL_APP_URL` | Mirrors the existing SMTP settings pattern (DB row wins, env var is the fallback); a background delivery thread has no request context to infer its own reachable host. |
| Hook opt-in | New `NotificationHook.include_report` (bool, default `False`) | Additive-only — existing hooks keep sending exactly what they send today. Same convention as `subject_template`/`body_template`. |
| Attachment size cap | Hardcoded 5MB constant in `notifier.py` | Balances SMTP relay limits (many cap 10–25MB; base64 adds ~33% overhead) against not silently dropping most reports. One less setting; easy to bump later. |
| Report generation timing | Lazy, generated at send time (hook path: on the delivery thread; ad-hoc path: inline in the request) | Matches the existing on-demand semantics of `GET /api/runs/{run_id}/report` — no extra work on every run completion for reports nobody asked for. |
| Ad-hoc endpoint access | Any authenticated user (no admin gate) | Matches today's report-download access level. |
| Ad-hoc send mode | Synchronous, returns real success/failure | User is waiting on a deliberate, low-frequency action and wants to know immediately if it failed — same reasoning as the existing SMTP test-send endpoint. |
| Shared logic | One `_email_run_report(db, run_id, recipients, subject=None, body=None) -> DeliveryResult` helper in `notifier.py` | Both the hook path and the ad-hoc endpoint generate/link/attach identically; avoids duplicating that logic at the two call sites. |

## Data Model Changes

`etl_framework/repository/models.py`:
- `AppSettings.app_url: Column(String(500), nullable=False, default="")`
- `NotificationHook.include_report: Column(Boolean, nullable=False, default=False)`

`etl_framework/repository/database.py`: idempotent `ensure_column` migrations for both, following the existing pattern in `_ensure_compare_columns`.

`SettingsRepository`: `get_app_url() -> str` (DB value or `ETL_APP_URL` env fallback), `set_app_url(url: str) -> AppSettings`.

## Flow 1 — Auto, via email hooks

In `notify()`'s email branch, when `hook.include_report` is true and the payload carries a `run_id`:

1. Call the shared helper's report-generation step: `ArtifactService(repository=RunRepository(db)).generate_html_report(run_id)` inside the delivery thread's existing `SessionLocal()` session. Wrapped in try/except — generation failure logs a warning and the send proceeds without a report (link and attachment both skipped), never blocking or failing the notification.
2. If `app_url` resolves (DB or env), append `\n\nFull report: {app_url}/api/runs/{run_id}/report` to the body. If unset, skip the link line silently — a broken/relative link is worse than none.
3. If the generated file is under 5MB, attach it (`EmailMessage.add_attachment`, `text/html`, filename via the existing `export_filename()` helper). Over the cap, the attachment is skipped; the link line still goes out.

This extends the existing `_send_email`/`_send_email_and_track` thread — no new background infrastructure.

## Flow 2 — Ad hoc, "email this report" button

**New endpoint** `POST /api/runs/{run_id}/email-report`
```json
{ "to": "alice@corp.com, bob@corp.com" }
```
- 404 if the run doesn't exist.
- Validates each address with the same regex already used for email-hook recipients.
- Generates the report via the shared helper, subject `"ETL report: {run_id} ({status})"`, body is the link line, attaches under the 5MB cap — identical logic to Flow 1's attach/link step.
- Runs synchronously; returns 202 with the delivery result on success, 502 with the SMTP error on failure.

**UI**: an "✉ Email" button placed next to wherever the existing report-download action lives in the run detail/history view (the implementation plan locates the exact markup), opening a small dialog: a to-address field and a Send button, toasts for success/failure — mirrors the SMTP test-send UX already in Settings.

## Error Handling
- Report-generation failure never blocks hook-path delivery (degrade to text-only); it's a hard 502 on the ad-hoc path since the user is waiting and explicitly asked for a report.
- Oversized reports never fail the send — attachment is dropped, link remains.
- Missing `app_url` degrades to no link line, never a broken URL.
- SMTP failures behave exactly as today: `DeliveryResult`, tracked in `notification_deliveries` for the hook path; propagated as the 502 detail for the ad-hoc path.

## Testing
- `SettingsRepository.get_app_url()`/`set_app_url()` — mirrors existing SMTP config tests.
- `_email_run_report()`: attached under cap, skipped over cap, link omitted when `app_url` unset, generation failure degrades gracefully instead of raising.
- Route tests for `POST /api/runs/{run_id}/email-report`: 404 missing run, 400 bad address, 502 SMTP failure, 202 success.
- End-to-end via `TestClient`: create an email hook with `include_report=True`, fire `notify()`, confirm the mocked `_send_email` received a link line and (for a small report) an attachment.

## Architecture Components Touched
1. `etl_framework/repository/models.py` — `AppSettings.app_url`, `NotificationHook.include_report`.
2. `etl_framework/repository/database.py` — migrations for both columns.
3. `etl_framework/repository/repository.py` — `SettingsRepository.get_app_url/set_app_url`; `NotificationRepository.create/update` gain `include_report`.
4. `api/services/notifier.py` — `_email_run_report()` shared helper; `notify()`'s email branch calls it when opted in.
5. `api/routes/runs.py` — new `POST /{run_id}/email-report`.
6. `api/routes/settings.py` — `app_url` in `SettingsOut`/`SettingsUpdate`.
7. `api/routes/notifications.py` — `include_report` in `HookCreate`/`HookUpdate`/`HookOut`.
8. `frontend/partials/tab-config.html` + `frontend/app.js`/`frontend/features/config.js` — App URL field in Settings, `include_report` checkbox on the hook modal, "Email report" button + dialog on the run view.
