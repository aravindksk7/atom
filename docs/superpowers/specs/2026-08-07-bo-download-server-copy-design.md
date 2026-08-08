# SAP BO Download — Server-Side Copy

**Date:** 2026-08-07
**Status:** Approved, not yet implemented

## Problem

Downloading a SAP BO report from Adapters → SAP BO opens the browser's **Save As**
dialog and waits for the user to pick a location. The report should also land in a
known directory without anyone clicking anything.

The dialog itself is not ours to remove. `triggerDownload`
([frontend/app.js:110](../../../frontend/app.js#L110)) builds an `<a download>` and
clicks it — an ordinary browser download. Whether the browser prompts is the
per-browser setting *"Ask where to save each file before downloading"*; no web page
can suppress it. So "saves automatically" has to mean the server writes a copy.

## Decisions

| Question | Decision |
|---|---|
| Where does the file land? | **Both** — server writes a copy, browser still receives one |
| Where is the directory configured? | **Global app setting**, on the Config tab |
| Filenames | **Timestamped, never overwrite** |
| Server write fails | **Download still succeeds**, failure shown loudly |
| Scope | **Ad-hoc UI downloads only**; job runs keep their run-scoped artifacts |
| Cleanup | **None.** The app writes and never deletes |

Two of these deserve their reasoning recorded.

**Timestamped, not stable.** The report is prompt-driven. A stable
`report_124313_1.xlsx` would let a colleague's 9-May pull silently overwrite your
8-May pull — the same class of silent-wrong-data bug that the 2026-08-07
investigation was about. Cost: the directory grows, which is why cleanup is a
separate decision below.

**No cleanup.** The operator nominates this directory, and it may be a shared drive
holding unrelated files. An application that sweeps files out of a path it does not
own is a bad surprise. Growth is visible and manageable with whatever already
governs that share.

## Architecture

A dedicated module owns writing; the service threads it; the routes supply the
setting and surface the outcome.

```
Config tab ──► settings route ──► SettingsRepository.set_bo_download_dir
                                          │  (validates at save time)
                                          ▼
                                  app_settings.bo_download_dir
                                          │
downloadBOReport (JS) ──► adapters route ─┤ reads the setting
                                          ▼
                            AdapterService.download_bo_report(download_dir=…)
                                          │
                            ┌─────────────┴─────────────┐
                            ▼                           ▼
                   BORestClient.download_report   bo_archive.save_bo_download
                            │                           │
                            └──────► BOReportDownload ◄─┘
                                          │
                            Response(body) + X-Saved-Path / X-Save-Error
                                          │
                                          ▼
                                  toasts in the browser
```

The alternatives considered were writing in each of the four route handlers (copies
the naming and failure policy four times) and writing inline inside the service
(mixes fetching with archiving, and testing the policies then needs a mocked BO
client). A dedicated module keeps the three policies behind one interface that can
be tested with nothing but bytes and a tmp directory.

`api/services/api_artifact.py` is deliberately **not** extended. It manages the
app's own artifact root, keyed by run and config. This writes to a directory the
operator nominated. Different ownership, different trust, different lifecycle.

## Component 1 — the setting

New column, added with `ensure_column` exactly as `upload_retention_days` is at
[etl_framework/repository/database.py:327](../../../etl_framework/repository/database.py#L327):

```sql
ALTER TABLE app_settings ADD COLUMN bo_download_dir TEXT NOT NULL DEFAULT ''
```

**Empty means disabled**, and it is the default — an upgraded install behaves
exactly as it does today, browser-only, until someone sets a path.

`SettingsRepository` gains `get_bo_download_dir()` and `set_bo_download_dir(path)`.
The setter validates the way `set_timezone` already does, raising `ValueError` (which
`update_settings` turns into an **HTTP 422**, matching how it already handles a bad
timezone):

- empty string → accepted, disables the feature
- non-empty → must be an **absolute** path (`Path(p).is_absolute()`) to an existing
  directory (`Path(p).is_dir()`) that passes `os.access(p, os.W_OK)`

`os.access` is advisory on Windows and will not catch every permission case. That is
accepted: it exists to catch typos at the point of entry, and download-time failure
handles the rest. A network share can also be reachable at save and gone later, so
save-time validation can never be the only check.

The Config tab gets a card beside Timezone: a text input, a Save button, and one line
saying an empty value keeps downloads browser-only.

## Component 2 — `api/services/bo_archive.py`

```python
def save_bo_download(content: bytes, *, doc_id: str, report_id: str, fmt: str,
                     directory: str, now: datetime | None = None
                     ) -> tuple[Path | None, str | None]:
    """Write a BO export to the configured directory. Never raises.

    (path, None)  wrote it
    (None, error) tried and failed
    (None, None)  disabled
    """
```

`now` is injectable so tests can pin the timestamp.

Four policies, all owned here:

**Disabled.** `directory` empty → `(None, None)` with no filesystem contact.

**Naming.** `report_<doc>[_<report>]_<YYYYMMDDTHHMMSSZ>.<ext>`, timestamp in UTC.
This extends the convention the routes already use for `Content-Disposition`
(`report_<docId>_<reportId>.<fmt>`) rather than inventing a second one. A
whole-document export omits the report segment, matching how the routes already treat
an empty `report_id`.

`<ext>` comes from `fmt` by the same rule the routes apply at
[api/routes/adapters.py:45](../../../api/routes/adapters.py#L45) — `pdf`/`xlsx`/`csv`
map to themselves, anything else to `bin`. The module carries its own copy of that
one-line mapping rather than importing it from the routes, which would make the
routes → service → archive dependency circular.

**Never overwrite.** Two downloads inside the same second would collide, so if the
target exists the module appends `-1`, `-2`, … until a free name is found. This
guarantees the property rather than approximating it with a finer timestamp.

**Never raise.** Any `OSError` — missing directory, permission denied, disk full —
returns `(None, str(exc))`.

### Path traversal

`doc_id` and `report_id` arrive **straight off the URL path**. An id shaped like
`../../etc/cron.d/x` would otherwise write outside the nominated directory. Both are
sanitized before they reach a filename: every character outside `[A-Za-z0-9_-]` is
replaced with `_`. This has a direct test asserting the written file stays inside the
nominated directory.

## Component 3 — service and routes

`AdapterService.download_bo_report` takes a new `download_dir: str = ""` and returns
a dataclass instead of `bytes`:

```python
@dataclass(frozen=True)
class BOReportDownload:
    content: bytes
    saved_path: Path | None
    save_error: str | None
```

This is a breaking change to a method mocked in `tests/unit/test_adapter_service.py`;
those mocks are updated. It is called only from the four download routes, so nothing
else moves.

The **routes** read the setting and pass it in, mirroring how `timezone` already
travels ([api/routes/adapters.py:203](../../../api/routes/adapters.py#L203)). This
keeps the service free of settings concerns. The two GET routes gain
`db: Session = Depends(get_session)`, which they do not currently take.

All four routes build their `Response` through one small module-local helper, so the
header logic is written once:

- `X-Saved-Path` — the absolute path, on success
- `X-Save-Error` — the OS error, on failure

Both are **percent-encoded** with `urllib.parse.quote`. HTTP header values are
latin-1, and both a Windows share path and an OS error string can carry characters
outside it.

## Component 4 — the browser

`apiBlob` currently returns only `{blob, disposition}`. It gains `savedPath` and
`saveError`, `decodeURIComponent`-ed. The precedent for lifting a custom header into
the returned object is `apiPaged`, which does exactly this with `x-stored-complete`
([frontend/app.js:105](../../../frontend/app.js#L105)) — a sibling helper, not
`apiBlob` itself. The POST branch of `downloadBOReport` uses a raw `fetch` and reads
the headers directly.

Today the two branches duplicate their toast code. They will share one local helper:

| Outcome | Toasts |
|---|---|
| Saved | `success` "Download started" — message `Also saved to <path>` |
| Failed | `success` "Download started" **and** `error` "Server copy failed" with the path and OS error |
| Disabled | `success` "Download started", unchanged from today |

Only `toast-success` and `toast-error` are styled, and `error` also carries
`role="alert"`. The download genuinely succeeded and the archive genuinely failed, so
both toasts fire — reporting only the failure would be wrong, and reporting only the
success is the silent-skip pattern this codebase has been bitten by.

## Testing

**`tests/unit/test_bo_archive.py`** (new) exercises the four policies directly, with
no HTTP and no BO client:

- disabled returns `(None, None)` and touches no filesystem
- filename shape, with and without a report segment
- two writes in the same pinned second → second becomes `-1`, first file intact
- a traversal-shaped `doc_id` cannot escape the tmp directory
- `OSError` returns `(None, error)` and does not raise

**Settings** — `set_bo_download_dir` accepts empty, rejects a relative path, rejects a
non-existent path, accepts a tmp directory; the settings route returns 400 on a bad
value.

**Routes** — `X-Saved-Path` present and percent-encoded on success; `X-Save-Error`
present on failure **and the body still carries the file**.

**End to end** — `tests/integration/test_sapbo_ui_download_flow.py` already drives the
whole UI download path with only the BO server mocked. Point it at a tmp directory and
assert the file appears on disk with the expected name, alongside the bytes the
browser receives. This is the test that proves the feature.

## Out of scope

- Scheduled `bo_report` job runs. They already write into the run's artifact
  directory, which is tied to run history and covered by retention.
- Any attempt to suppress the browser's Save As dialog. Not possible from a web page;
  it is a per-browser setting.
- Retention or cleanup of the nominated directory.
