# Compare Tab Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 7th "⇄ Compare" tab to the web GUI that makes BO report comparison and reconciliation comparison first-class test runs — stored in the DB, tracked in History, reportable, and with a mismatch-acceptance workflow.

**Architecture:** FastAPI backend with two new service classes and one new route module; SQLite schema extended with `run_type`/`pair_id` on `TestRun` and acceptance columns on `MismatchDetail`; Alpine.js frontend with a new Compare tab containing BO Report and Reconciliation sub-panels. Ruflo `swarm_init`/`agent_spawn` parallelise dual-env launches.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy, Alpine.js, pandas/openpyxl (file reading), BeautifulSoup4 (HTML report parsing), Ruflo MCP tools (parallel agents).

---

## 1. Core Concept

Every comparison is a **real `TestRun`** stored in the database. A test passes when it has zero unaccepted mismatches. Users may accept individual mismatches with a mandatory note; once all mismatches on a result are accepted the test status flips to PASSED. This applies to both BO report comparisons and reconciliation comparisons.

**Pass/fail rule:**
```
TestResult.status = PASSED  iff  (value_mismatch_count + missing_in_target_count + missing_in_source_count) - accepted_count == 0
```

---

## 2. Data Model Changes

File: `etl_framework/repository/models.py`

### `TestRun` — two new columns

```python
run_type = Column(String(50), nullable=False, default="reconciliation")
# values: "reconciliation" | "bo_comparison" | "dual_env"

pair_id  = Column(String(36), nullable=True, index=True)
# set on both legs of a dual-env launch to the same UUID
```

### `MismatchDetail` — four new columns

```python
accepted    = Column(Boolean, nullable=False, default=False)
accepted_note = Column(Text, nullable=True)
accepted_at   = Column(DateTime(timezone=True), nullable=True)
accepted_by   = Column(String(255), nullable=True)
```

Schema migration is additive (SQLite `ALTER TABLE ADD COLUMN`) — no existing data is affected. Existing `run_type` defaults to `"reconciliation"` for all prior rows.

---

## 3. API Surface

### 3a. New router: `api/routes/compare.py`

Mounted at `/api/compare` in `api/main.py`.

```
POST /api/compare/bo-report
    Body: BOCompareRequest
    Returns: RunStatusOut (202 Accepted — run executes in background)
    Creates a TestRun with run_type="bo_comparison"

POST /api/compare/dual-env
    Body: DualEnvLaunchRequest
    Returns: DualEnvLaunchOut  {pair_id, run_id_a, run_id_b}
    Creates two TestRuns with run_type="dual_env", same pair_id
    Uses Ruflo swarm_init + agent_spawn to parallelise both legs

GET /api/compare/pairs
    Returns: list[PairSummaryOut]  — all dual-env pairs, most recent first

GET /api/compare/pairs/{pair_id}
    Returns: PairSummaryOut  — both run statuses; used for frontend polling
```

### 3b. Extended: `api/routes/runs.py`

```
PATCH /api/runs/{run_id}/results/{result_id}/mismatches/{mismatch_id}/accept
    Body: MismatchAcceptRequest  {note: str, accepted_by: str | None}
    Returns: MismatchAcceptOut
    Side-effect: recalculates TestResult.status after acceptance
```

---

## 4. New Schemas (`api/schemas.py`)

```python
class SourceConfig(BaseModel):
    """Describes one side of a comparison — live API, file path, or upload."""
    source_type: Literal["live", "path", "upload"]
    config_id: int | None = None          # required when source_type == "live"
    file_path: str | None = None          # required when source_type == "path"
    file_content_b64: str | None = None   # required when source_type == "upload"
    file_name: str | None = None          # for display; inferred from path if omitted

class BOCompareRequest(BaseModel):
    source_a: SourceConfig
    source_b: SourceConfig
    doc_id: str | None = None             # required for live source — BO document ID
    report_id: str | None = None          # required for live source — BO report tab ID
    key_columns: list[str] = []
    exclude_columns: list[str] = []
    label_a: str = "Source A"
    label_b: str = "Source B"

class DualEnvLaunchRequest(BaseModel):
    config_id_a: int
    config_id_b: int
    source_env_a: str
    target_env_a: str
    source_env_b: str
    target_env_b: str
    job_names: list[str] = []
    run_settings: RunSettings = Field(default_factory=RunSettings)

class DualEnvLaunchOut(BaseModel):
    pair_id: str
    run_id_a: str
    run_id_b: str

class PairSummaryOut(BaseModel):
    pair_id: str
    run_a: RunStatusOut
    run_b: RunStatusOut

class ReconFileCompareRequest(BaseModel):
    """Compare a production HTML reconciliation report against a stored run."""
    stored_run_id: str | None = None      # source A: run from DB
    file_a_path: str | None = None        # source A: file path (if not using stored run)
    file_a_content_b64: str | None = None # source A: uploaded bytes
    file_b_path: str | None = None        # source B: file path
    file_b_content_b64: str | None = None # source B: uploaded bytes
    label_a: str = "Run / File A"
    label_b: str = "Production Report"

    @model_validator(mode="after")
    def validate_sources(self) -> "ReconFileCompareRequest":
        has_a = bool(self.stored_run_id or self.file_a_path or self.file_a_content_b64)
        has_b = bool(self.file_b_path or self.file_b_content_b64)
        if not has_a:
            raise ValueError("Source A must be a stored_run_id, file_a_path, or file_a_content_b64")
        if not has_b:
            raise ValueError("Source B must be a file_b_path or file_b_content_b64")
        return self

class MismatchAcceptRequest(BaseModel):
    note: str = Field(min_length=1)
    accepted_by: str | None = None

class MismatchAcceptOut(BaseModel):
    id: int
    accepted: bool
    accepted_note: str | None
    accepted_at: datetime | None
    accepted_by: str | None
    result_status_updated: bool           # True if TestResult.status changed to PASSED
```

`MismatchOut` (existing) gains `accepted`, `accepted_note`, `accepted_at`, `accepted_by` fields.

---

## 5. New Services

### 5a. `api/services/file_source.py`

Responsibility: Read tabular data from a file (CSV or XLSX) into a pandas DataFrame, regardless of whether the input is a filesystem path, a UNC path, or raw bytes from a browser upload.

```python
def read_tabular(
    path: str | None = None,
    content_b64: str | None = None,
    file_name: str | None = None,
) -> pd.DataFrame:
    """
    Returns a DataFrame from path (CSV/XLSX) or base64-encoded bytes.
    Raises HTTPException(400) for unsupported formats.
    """
```

### 5b. `api/services/compare_service.py`

Responsibility: Orchestrate BO comparisons and file-based reconciliation comparisons, then persist results as `TestRun` / `TestResult` / `MismatchDetail` rows.

```python
class CompareService:
    def __init__(self, db: Session, config_repo: ConfigRepository) -> None: ...

    def run_bo_comparison(self, req: BOCompareRequest, run_id: str) -> None:
        """
        Builds DataFrames from each source (live or file), runs ReconciliationEngine,
        writes TestRun + TestResult + MismatchDetail rows.
        Called in a BackgroundTask.
        """

    def run_recon_file_compare(self, req: ReconFileCompareRequest, run_id: str) -> None:
        """
        Reads production HTML report(s) with _parse_html_report(), diffs test stats
        against a stored run or another parsed report, writes TestRun + TestResult rows.
        MismatchDetail rows are not written (HTML reports carry summary counts only).
        """

    @staticmethod
    def _parse_html_report(html: str) -> dict[str, dict]:
        """
        Parse a framework-generated HTML reconciliation report.
        Returns {test_name: {status, passed, failed, duration_seconds, ...}}.
        Uses BeautifulSoup4 to locate the results table in the standard template.
        """
```

### 5c. `api/services/adapter_service.py` — extension

Add `fetch_bo_dataframe(config_id, doc_id, report_id) -> pd.DataFrame` that calls `BORestClient.fetch_report_data()` and returns the raw DataFrame. Used by `CompareService` for live-source BO comparisons.

---

## 6. Mismatch Acceptance Logic (`api/routes/runs.py`)

```
PATCH /api/runs/{run_id}/results/{result_id}/mismatches/{mismatch_id}/accept
```

Steps:
1. Load `MismatchDetail` by `mismatch_id`; 404 if not found or belongs to different `result_id`.
2. Set `accepted=True`, `accepted_note=body.note`, `accepted_at=utcnow()`, `accepted_by=body.accepted_by`.
3. Count remaining unaccepted mismatches on `result_id`.
4. If count == 0: set `TestResult.status = "PASSED"` and update parent `TestRun.passed += 1`, `failed -= 1`.
5. Return `MismatchAcceptOut` with `result_status_updated=True/False`.

The `MismatchOut` schema is extended so `GET /api/runs/{run_id}/results/{result_id}/mismatches` returns acceptance fields, allowing the UI to restore accepted state on page reload.

---

## 7. Ruflo Dual-Env Launch

`POST /api/compare/dual-env` handler:

1. Generate `pair_id = str(uuid4())`.
2. Create two `TestRun` rows: `run_id_a` and `run_id_b`, both with `run_type="dual_env"`, `pair_id=pair_id`.
3. Call `swarm_init(swarm_id=pair_id, agents=["env_a", "env_b"])` via Ruflo MCP.
4. Call `agent_spawn(swarm_id=pair_id, agent_id="env_a", task=_build_task(req, run_id_a, "a"))`.
5. Call `agent_spawn(swarm_id=pair_id, agent_id="env_b", task=_build_task(req, run_id_b, "b"))`.
6. Return `DualEnvLaunchOut(pair_id=pair_id, run_id_a=run_id_a, run_id_b=run_id_b)`.

Each spawned agent calls the existing `_execute_run()` function with the appropriate config. The frontend polls `GET /api/compare/pairs/{pair_id}` every 3 s until both runs reach a terminal status, then calls `GET /api/runs/compare?run_a=…&run_b=…` to render the delta table.

**Fallback:** If Ruflo MCP tools are unavailable (connection error), fall back to `BackgroundTasks.add_task` for each leg sequentially. Log a warning. The pair_id mechanism still works; only the parallelism is lost.

---

## 8. Frontend: Compare Tab

### 8a. Navigation (`frontend/index.html`)

Add `⇄ Compare` as the 7th nav button alongside existing tabs. The Alpine.js `tab` state gains a new value `'compare'`.

### 8b. Compare tab structure

```
Compare tab
├── Sub-tab: BO Report
│   ├── Source A picker  (type toggle: Live API | File Path | Upload)
│   │   ├── Live: config dropdown → document dropdown → report-tab dropdown (cascading)
│   │   ├── Path: UNC/local path text input
│   │   └── Upload: drag-drop zone (stores file as base64 in Alpine state)
│   ├── Source B picker  (same structure, independent type toggle)
│   ├── Key Columns + Exclude Columns inputs
│   ├── "⇄ Run Comparison" button  → POST /api/compare/bo-report
│   └── Results panel
│       ├── Summary chips: matched / mismatches / missing-in-A / missing-in-B
│       ├── Row-level diff table (same columns as History mismatch expand)
│       └── Per-row "✓ Accept" button → inline note form → PATCH accept endpoint
│
└── Sub-tab: Reconciliation
    ├── Mode cards: Stored Run Diff | Dual-Env Launch | File vs Run
    │
    ├── [Stored Run Diff mode]
    │   ├── Run A dropdown (from /api/runs list)
    │   ├── Run B dropdown
    │   └── "Compare →" button  → GET /api/runs/compare (existing endpoint)
    │
    ├── [Dual-Env Launch mode]
    │   ├── Env A: config + source_env + target_env
    │   ├── Env B: config + source_env + target_env
    │   ├── Job set multiselect
    │   ├── "⚡ Launch Dual-Env Run" button  → POST /api/compare/dual-env
    │   ├── Progress: polls /api/compare/pairs/{pair_id} every 3 s
    │   └── Results: delta table with mismatch inline expand + accept buttons
    │
    └── [File vs Run mode]
        ├── Source A: stored run dropdown OR file path/upload
        ├── Source B: file path/upload (production HTML report)
        ├── "Compare →" button  → POST /api/compare/recon-file
        └── Results: side-by-side summary stats + per-test delta table
```

### 8c. Mismatch acceptance (shared, used in Compare + History)

- `expandedMismatches[result_id]` already holds loaded mismatch rows.
- New state: `acceptForms: {}` keyed by `mismatch_id` → `{open: bool, note: str}`.
- `toggleAcceptForm(mismatchId)` opens/closes the inline note form.
- `submitAccept(runId, resultId, mismatchId)` calls `PATCH …/accept`, updates the mismatch row in `expandedMismatches` in-place, shows toast.
- If `result_status_updated` is true in the response: update `selectedRun.results` status and counters locally.
- "✓ Accept" button hidden once `mismatch.accepted == true`; shows ✓ check + note instead.
- Informational banner "All mismatches accepted — this test is now PASSED" appears when `expandedMismatches[result_id].every(m => m.accepted)`. No manual confirmation required — status flips automatically on the last acceptance (see §6).

### 8d. New Alpine.js state variables (`frontend/app.js`)

```javascript
// Compare tab
compareSubTab: 'bo',          // 'bo' | 'recon'
reconMode: 'stored',          // 'stored' | 'dual' | 'file'
boSourceAType: 'live',        // 'live' | 'path' | 'upload'
boSourceBType: 'upload',
boSourceA: {},                // {configId, docId, reportId, filePath, fileB64, fileName}
boSourceB: {},
boKeyColumns: '',
boExcludeColumns: '',
boCompareRunId: null,         // run_id of last BO compare run (for polling)
boCompareResult: null,        // RunDetailOut after completion

dualEnvConfigA: '', dualEnvConfigB: '',
dualEnvSourceEnvA: '', dualEnvTargetEnvA: '',
dualEnvSourceEnvB: '', dualEnvTargetEnvB: '',
dualEnvJobs: [],
dualEnvPairId: null,
dualEnvPollInterval: null,    // setInterval handle
dualEnvResult: null,          // RunCompareOut after both legs complete

fileSourceAType: 'run',       // 'run' | 'file'
fileRunId: '',
filePathA: '', fileB64A: '', filePathB: '', fileB64B: '',
fileCompareResult: null,

// Mismatch acceptance (shared)
acceptForms: {},              // mismatch_id → {open, note}
```

---

## 9. Report Integration

Accepted mismatches and their notes are included in the existing HTML reconciliation report template (`etl_framework/reporting/templates/report.html.j2`). The `ArtifactService` passes `mismatch.accepted` and `mismatch.accepted_note` to the template context; accepted mismatches are rendered with a ✓ badge and the note in green, making the sign-off trail visible in the exported report.

---

## 10. File List

| Status | Path | Change |
|--------|------|--------|
| Modify | `etl_framework/repository/models.py` | Add `run_type`, `pair_id` to `TestRun`; add acceptance columns to `MismatchDetail` |
| Modify | `etl_framework/repository/repository.py` | Add `accept_mismatch`, `get_pair_runs`, `list_pairs`, `count_accepted_mismatches` |
| Modify | `etl_framework/reporting/templates/report.html.j2` | Render accepted mismatches with note |
| Modify | `api/schemas.py` | Add 8 new schemas; extend `MismatchOut` |
| Create | `api/routes/compare.py` | 4 new endpoints |
| Modify | `api/routes/runs.py` | Add PATCH accept endpoint |
| Modify | `api/main.py` | Register compare router |
| Create | `api/services/compare_service.py` | `CompareService` + `_parse_html_report` |
| Create | `api/services/file_source.py` | `read_tabular()` |
| Modify | `api/services/adapter_service.py` | Add `fetch_bo_dataframe()` |
| Modify | `frontend/index.html` | 7th Compare tab; mismatch accept UI in History |
| Modify | `frontend/app.js` | New state + methods |
| Modify | `frontend/styles.css` | Acceptance UI styles |
| Create | `tests/unit/test_compare_api.py` | Unit tests for compare routes |
| Create | `tests/unit/test_file_source.py` | Unit tests for file reading |
| Create | `tests/unit/test_mismatch_accept.py` | Unit tests for accept endpoint |

---

## 11. Error Handling

- **File format error**: `read_tabular()` raises `HTTPException(400)` for non-CSV/XLSX files; frontend shows toast.
- **HTML parse failure**: `_parse_html_report()` raises `HTTPException(422, "Cannot parse reconciliation report — not a framework-generated report")`.
- **Ruflo unavailable**: dual-env launch falls back to sequential `BackgroundTasks`; `DualEnvLaunchOut` still returned with both run IDs.
- **Partial dual-env**: if one leg errors, `pair_id` polling surfaces both run statuses; the Compare table shows one column as `ERROR` with the error message.
- **Accept note empty**: schema enforces `min_length=1` on `MismatchAcceptRequest.note`; frontend disables Confirm until non-empty.
- **Re-accepting**: PATCH is idempotent — accepting an already-accepted mismatch updates the note, returns `result_status_updated=False`.
