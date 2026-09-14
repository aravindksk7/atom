# File Watcher Job — Design

**Goal:** Add a `file_watcher` job type that can be dropped into a sequence like any other job: it polls a location (local folder, S3, SFTP/SCP) for a file matching a name pattern — optionally also containing given text — between an optional time-of-day/datetime window and/or up to a max number of tries, and passes/fails the step accordingly. Downstream steps in the sequence can depend on it exactly like any other step, and can consume the matched file path from its result.

**Tech stack:** Python (FastAPI, Pydantic), existing `RunExecutor`/`DagExecutor` DAG-step machinery, `etl_framework.reconciliation.file_mapping` discovery/readiness primitives, Alpine.js frontend.

---

## 1. Why a job_type, not a new concept

A sequence step already carries everything a watcher needs to participate in a DAG: `depends_on`, `trigger_rule`, `on_failure`, `max_retries`, `condition` (`api/schemas.py:235` `SequenceStepRef`, persisted per-step by `materialize_steps()` per the restart design, `docs/superpowers/specs/2026-09-14-restart-failed-sequence-job-design.md`). `RunExecutor._build_case` already dispatches on `job.job_type` to one of ~18 handlers (`api/services/run_executor.py:524-600`). Watcher becomes one more entry in that dispatch table and one more value in the `JobDefinition.job_type` Literal (`api/schemas.py:708`). No new execution model, no new step-level fields, no new terminal status — it fails FAILED like any other job and the step's existing `on_failure`/`trigger_rule` decide what happens next.

## 2. Reusing existing file-discovery machinery

`etl_framework/reconciliation/file_mapping.py` already has everything except the "watch until match, within a window/try-budget" loop for a *single* file target:

- `discover_s3_files(client, root, pattern)` (line 205) and `discover_sftp_files(client, root, pattern)` (line 241) — glob-pattern discovery against S3/SFTP, callers own client/credentials.
- Local discovery is the plain `Path(root).glob(pattern)` case already used elsewhere in the module for `kind == "local"`.
- `FileSourceSpec(kind, root, pattern, credentials_ref, readiness)` (line 342) — the exact shape a watcher's location config needs.
- `ReadinessSpec(expected_count, poll_interval_seconds, timeout_seconds)` + `wait_for_ready_files(discover, readiness, sleep)` (lines 775-826) — a poll loop that already does "keep calling `discover()` until N files show up or timeout," used today as a pre-check inside multi-file reconciliation jobs.

Watcher's poll loop is `wait_for_ready_files` generalized two ways: (a) it needs to accept a **max-tries** budget alongside/instead of a wall-clock timeout, and (b) it needs an optional **content filter** applied to each discovered candidate before counting it a match, and (c) it needs an optional **time-of-day window** gating when polling is allowed to happen at all. Rather than bolt all of that onto `ReadinessSpec` (which has a different, narrower job — "did N files show up" — used synchronously inline before a compare), a sibling primitive is added in the same module:

```python
@dataclass(frozen=True)
class WatchSpec:
    poll_interval_seconds: float = 30.0
    max_tries: int | None = None
    window_start: str | None = None   # "HH:MM" (recurring) or full ISO datetime (one-shot)
    window_end: str | None = None
    content_text: str | None = None
    content_is_regex: bool = False

def wait_for_watched_file(
    discover: Callable[[], list[DiscoveredFile]],
    spec: WatchSpec,
    read_text: Callable[[DiscoveredFile], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> DiscoveredFile:
    """Poll `discover()` until a candidate matches (optionally filtered by
    `spec.content_text`), or raise TimeoutError once max_tries is exhausted
    or `now()` passes window_end / hasn't reached window_start. Returns the
    first matching DiscoveredFile."""
```

`WatchSpec` requires at least one of `max_tries` / `window_end` at construction (mirrors the "no infinite watcher" rule from clarification) — enforced the same way `_parse_readiness` validates today (raise `ValueError` with a clear message, caught by the same Pydantic `model_validator` path other job types use).

Window semantics: `"HH:MM"` strings are resolved against the day `now()` falls on when the step starts (recurring nightly-window use case); a full ISO datetime is used as-is (one-shot override). If `now()` is before `window_start`, the poller sleeps until it (capped by `max_tries` if also set) rather than immediately trying and failing.

Content match: only evaluated after a filename-pattern match. `read_text` is capped to the first 64KB of the candidate (configurable constant) to avoid choking on huge files when the check is "does this flag file contain `STATUS=COMPLETE`" — binary/undecodable content is treated as "no match," not an error, since a watcher polling mid-write may see a partial/binary-looking snapshot.

SCP: there is no distinct SCP client path. `location.kind: "scp"` is accepted purely as a UI label and normalized to `kind: "sftp"` before reaching `FileSourceSpec` — same host/port/credentials_ref shape, same `discover_sftp_files`. This matches how the vast majority of "SCP server" targets are actually SSH hosts with the SFTP subsystem enabled; a host that truly only speaks raw SCP (no SFTP subsystem) is out of scope for v1 (see §8).

## 3. Schema

`api/schemas.py` `JobDefinition.job_type` Literal gains `"file_watcher"`. New params shape, validated in `validate_reconciliation_contract` alongside the other `elif self.job_type == ...` branches:

```python
elif self.job_type == "file_watcher":
    location = self.params.get("location")
    if not isinstance(location, dict):
        raise ValueError("file_watcher jobs require a 'location' object in params")
    kind = location.get("kind")
    if kind not in ("local", "s3", "sftp", "scp"):
        raise ValueError("file_watcher location.kind must be 'local', 's3', 'sftp', or 'scp'")
    if not location.get("root") or not location.get("pattern"):
        raise ValueError("file_watcher location requires 'root' and 'pattern'")
    if kind in ("s3", "sftp", "scp") and not location.get("credentials_ref"):
        raise ValueError(f"file_watcher location.kind '{kind}' requires 'credentials_ref'")
    max_tries = self.params.get("max_tries")
    window_end = self.params.get("window_end")
    if max_tries is None and not window_end:
        raise ValueError("file_watcher jobs require 'max_tries' and/or 'window_end' — an unbounded watch is not allowed")
    content_match = self.params.get("content_match")
    if content_match is not None and (not isinstance(content_match, dict) or not content_match.get("text")):
        raise ValueError("file_watcher content_match, if given, requires a 'text' field")
```

Full params shape:

```python
params = {
    "location": {
        "kind": "local" | "s3" | "sftp" | "scp",
        "root": str,             # folder path / "s3://bucket/prefix" / sftp dir
        "pattern": str,          # filename glob, e.g. "SALES_*.csv"
        "credentials_ref": str | None,   # required for s3/sftp/scp
    },
    "content_match": {"text": str, "is_regex": bool} | None,
    "poll_interval_seconds": int,        # default 30
    "max_tries": int | None,
    "window_start": str | None,          # "HH:MM" or ISO datetime
    "window_end": str | None,            # "HH:MM" or ISO datetime
}
```

`credentials_ref` resolution reuses whatever existing lookup `_build_case_multi_file_reconciliation`/`_build_case_bo_report` etc. already use for env-stored credentials (`api/services/run_executor.py` around line 727's "per (kind, credentials_ref) is reused throughout" comment) — no new credential storage mechanism.

## 4. Execution (`RunExecutor`)

New branch in `_build_case` (`api/services/run_executor.py:524`), same pattern as every other `if job.job_type == "...":`:

```python
if job.job_type == "file_watcher":
    return self._build_case_file_watcher(job)
```

`_build_case_file_watcher` builds a `discover()` closure appropriate to `location.kind` (local glob, or `discover_s3_files`/`discover_sftp_files` with a client built from `credentials_ref` the same way existing S3/SFTP-backed job types already do), builds a `WatchSpec` from `params`, and calls `wait_for_watched_file`. Result mapping onto `ReconciliationResult` (`etl_framework/reconciliation/models.py:21`) — no schema change needed, existing optional fields cover it:

- Match found → `status=PASSED`, `source_file_name=<matched file's basename>`, `data_artifact_path=<matched file's full path/URI>`, `mismatch_summary={"tries": n, "elapsed_seconds": t, "matched_text": <snippet if content_match used>}`.
- `TimeoutError` (tries/window exhausted, no match) → `status=FAILED`, same `mismatch_summary` shape reporting how many tries/how long it waited, for a clear run-detail message ("no file matching SALES_*.csv found after 40 tries / by 23:30").
- Any other exception (bad credentials, unreachable host) → `status=ERROR`, existing job-type error handling path (same as every other `_build_case_*`, no special casing).

Downstream steps read `data_artifact_path`/`source_file_name` off the watcher's `ParentOutcome.result` the same way any step today can reference a prior step's `ReconciliationResult` fields — this is already the shape `restart_run`'s `ParentOutcome(status=..., result=...)` reconstruction depends on (`docs/superpowers/specs/2026-09-14-restart-failed-sequence-job-design.md` §3.7), so no change to that machinery is needed either. Wiring a downstream reconciliation job to *automatically* consume that path (vs. a human reading it off the run detail page and configuring the next job by hand) is out of scope for v1 — see §8.

## 5. API

No new endpoints. `file_watcher` flows through the existing job-create/job-update routes (`api/routes/jobs.py`) exactly like any other `job_type`, and through sequence launch the same way `bo_job`/`ds_job`/etc. do today. `JobDefinition`/`JobOut` schemas already carry arbitrary `params`, so no field additions beyond the Literal change in §3.

## 6. Frontend

Job creation / sequence step editor (wherever `job_type` is a dropdown today, alongside `bo_job`, `ds_job`, `s3_row_count`, etc.) gains a `"File Watcher"` option. Its form panel:

- Location kind selector (Folder Path / S3 / SFTP / SCP) → shows root, pattern, and (for S3/SFTP/SCP) a credentials picker, matching the existing multi-file-reconciliation source-picker UI pattern already used for `file_mapping.source`/`target`.
- Optional "Also require file to contain" text field + regex checkbox.
- Poll interval, max tries, and a window start/end pair (accepting either `HH:MM` or a full datetime picker — same "both shapes" flexibility as the schema).
- Help text (`frontend/help-content.js`, matching how SFTP/S3 help entries already exist there) explaining the SCP-is-really-SFTP normalization and the "at least one of max tries / window end" requirement up front, so the form can validate client-side before submit.

Run detail page: a `file_watcher` step shows its poll count/elapsed time and (on success) the matched file path in the step's result panel — same panel other job types already render `data_artifact_path`/`mismatch_summary` into, no new UI component required.

## 7. Testing

- `tests/unit/test_file_mapping.py`: new `WatchSpec`/`wait_for_watched_file` cases — matches on first try, matches after N tries, times out on max_tries, times out on window_end, content_match filters out a filename-matching-but-content-wrong candidate, `scp` kind normalizes to `sftp` discovery, injected `sleep`/`now` keep tests instant (mirrors how `wait_for_ready_files` is already tested with an injectable `sleep`).
- `tests/unit/test_api.py` or a new `test_file_watcher_job.py`: `JobDefinition` validation — rejects missing location fields, rejects missing credentials_ref for remote kinds, rejects unbounded watch (no max_tries and no window_end), accepts valid local/s3/sftp/scp configs.
- `tests/unit/` for `RunExecutor`: `_build_case_file_watcher` produces PASSED with correct `data_artifact_path` on match, FAILED with tries/elapsed detail on exhaustion, ERROR on discovery exception — using a fake `discover()`/clock like the restart design's existing executor test patterns.
- `tests/e2e/`: a sequence with `file_watcher` step 1 → dependent job step 2, exercising: file appears mid-poll (step 1 passes, step 2 runs and can read the matched path from the run detail), and file never appears (step 1 fails, step 2 is BLOCKED per its `trigger_rule`, same as any other failed dependency today).

## 8. Out of scope (explicitly deferred)

- True raw-SCP-only hosts (no SFTP subsystem) — `scp` kind is an SFTP alias for v1.
- Automatic downstream parameter injection (a following job's `params` auto-filled from the watcher's matched path) — v1 exposes the path in the result for a human (or a future enhancement) to wire up; it does not rewrite a sibling step's params at runtime.
- Watching for multiple files / an `expected_count` (that's what `ReadinessSpec` already covers for the multi-file-reconciliation case) — `file_watcher` is single-match-then-done.
- Content match against binary/large files beyond the 64KB read cap.
- Re-arming a watcher mid-run (once matched or exhausted, the step is terminal, same as every other job type — re-running means relaunching the sequence).
