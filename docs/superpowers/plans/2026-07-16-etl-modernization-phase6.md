# Phase 6 Modernization — RBAC, DQ Scorecard, Alert Channels, Command Palette

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four highest-severity gaps found when benchmarking this ETL test framework against industry practice (Great Expectations, Soda, Datafold, Monte Carlo, elementary): role-based access control, a data-quality dimension scorecard, native Slack/Teams/email alert channels, and a command palette for the UI.

**Architecture:** All four features are additive. RBAC extends the existing `ApiToken` model + `BearerTokenMiddleware` with a `role` column and a pure permission function. The scorecard is a new read-only service over existing `test_results`/`mismatch_details` rows (no new tables). Alert channels extend `NotificationHook` with a `channel` column and per-channel payload formatters, reusing the existing SSRF-guarded delivery path. The command palette is a self-contained Alpine.js overlay.

**Tech Stack:** FastAPI, SQLAlchemy 2, Pydantic 2, pytest, Alpine.js 3, Tailwind (vendored).

---

## Benchmark Summary (why these four)

Benchmarked against Great Expectations, Soda Core/Cloud, dbt tests, Datafold, Monte Carlo, elementary, and data-diff. The repo already covers an unusually large surface: reconciliation (SQL/file/REST), 20 DQ rule types, DAG dependencies, retries, cron schedules, SSE monitoring, cancellation, baselines, σ-drift, badges, HMAC webhooks, audit log, profiling, schema snapshots + compatibility classifier, coverage matrix, flaky detection, segment drilldown, data contracts with SLA/breach/escalation, WAP gate, CI gate exit codes, shadow sampling, rules-as-code YAML. That is ahead of most open-source DQ tools.

Critical gaps (this plan):

| Gap | Industry reference | Severity |
|---|---|---|
| Auth is binary admin/non-admin; any valid token can mutate jobs, trigger runs, accept mismatches | Every enterprise DQ platform ships viewer/operator/admin at minimum | Critical |
| No quality-dimension rollup (completeness/uniqueness/validity/consistency/accuracy/timeliness) — rich raw data exists but no scorecard | Soda Cloud scorecards, Monte Carlo table health, DAMA dimensions | High |
| Alerting is generic webhook only; Slack/Teams/email require the user to run middleware | Soda, elementary, Monte Carlo all ship native Slack/Teams/email | High |
| 8-tab UI with no global navigation/search; power users click through tabs constantly | Linear/Datadog-style Ctrl+K palette is table stakes for dense ops UIs | Medium |

Deferred to follow-up plans (independent subsystems; per scope check these should NOT be in this plan):

- **Alembic migrations** — replace the `ensure_column` shim before any server-DB deployment.
- **OpenLineage emission** — emit RunEvents so Marquez/DataHub/OpenMetadata can ingest run lineage.
- **Seasonality-aware anomaly detection** — upgrade σ-drift to EWMA/STL adaptive thresholds (needs `stats` extra).
- **Partition/watermark-aware incremental reconciliation** — reconcile only changed partitions (Datafold-style checksum bisection).
- **Frontend modularization** — split the 4,276-line `app.js` and 5,438-line `index.html` into per-tab modules; add light theme via design tokens; virtualize large mismatch tables.
- **Great Expectations suite import** — map GE JSON suites onto the existing `expectations/` YAML format.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `etl_framework/repository/models.py` | Modify | `ApiToken.role`, `NotificationHook.channel` columns |
| `etl_framework/repository/database.py` | Modify | `ensure_column` shims for the two new columns |
| `etl_framework/repository/repository.py` | Modify | `TokenRepository.create(role=...)` |
| `api/middleware/auth.py` | Modify | `role_permits()` pure function + enforcement in dispatch |
| `api/routes/tokens.py` | Modify | `role` on create/read schemas |
| `api/services/scorecard_service.py` | Create | Dimension mapping + score computation |
| `api/routes/quality.py` | Create | `GET /api/quality/scorecard` |
| `api/main.py` | Modify | register quality router |
| `api/services/notifier.py` | Modify | `format_payload()` per channel + email delivery |
| `api/routes/notifications.py` | Modify | accept/return `channel` |
| `frontend/index.html` | Modify | role picker, Scorecard sub-tab, channel picker, palette overlay |
| `frontend/app.js` | Modify | role state, `loadScorecard()`, palette state/actions |
| `tests/unit/test_rbac.py` | Create | role model + permission matrix tests |
| `tests/unit/test_scorecard_service.py` | Create | scorecard math tests |
| `tests/unit/test_notifier_channels.py` | Create | payload formatter + email tests |

Run all commands from repo root `c:\atom` with the venv active. Full suite check between tasks: `python -m pytest tests/unit -x -q`.

---

### Task 1: RBAC — `role` column, migration shim, repository support

**Files:**
- Modify: `etl_framework/repository/models.py:254` (inside `class ApiToken`)
- Modify: `etl_framework/repository/database.py` (inside `_ensure_compare_columns`)
- Modify: `etl_framework/repository/repository.py:758` (`TokenRepository.create`)
- Test: `tests/unit/test_rbac.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rbac.py`:

```python
"""RBAC: role column, effective-role derivation, repository support."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from etl_framework.repository.models import ApiToken, Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_api_token_role_defaults_to_empty(db):
    token = ApiToken(token_hash="h1", name="t")
    db.add(token)
    db.commit()
    assert token.role == ""


def test_effective_role_derives_from_is_admin_for_legacy_tokens():
    from etl_framework.repository.repository import effective_role

    legacy_admin = ApiToken(token_hash="h2", name="a", is_admin=True, role="")
    legacy_user = ApiToken(token_hash="h3", name="u", is_admin=False, role="")
    assert effective_role(legacy_admin) == "admin"
    assert effective_role(legacy_user) == "operator"


def test_effective_role_prefers_explicit_role():
    from etl_framework.repository.repository import effective_role

    viewer = ApiToken(token_hash="h4", name="v", is_admin=False, role="viewer")
    assert effective_role(viewer) == "viewer"


def test_effective_role_ignores_unknown_role_values():
    from etl_framework.repository.repository import effective_role

    weird = ApiToken(token_hash="h5", name="w", is_admin=False, role="superuser")
    assert effective_role(weird) == "operator"


def test_token_repository_create_persists_role_and_syncs_is_admin(db):
    from etl_framework.repository.repository import TokenRepository

    repo = TokenRepository(db)
    raw, token = repo.create("alice", role="viewer")
    assert token.role == "viewer"
    assert token.is_admin is False

    raw2, token2 = repo.create("root", role="admin")
    assert token2.role == "admin"
    assert token2.is_admin is True  # keeps existing require_admin checks working
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_rbac.py -v`
Expected: FAIL — `ApiToken` has no attribute `role`, `ImportError: cannot import name 'effective_role'`.

- [ ] **Step 3: Add the column to the model**

In `etl_framework/repository/models.py`, inside `class ApiToken`, after the `token_hint` line:

```python
    token_hint = Column(String(8), nullable=False, default="")
    # RBAC role: "viewer" | "operator" | "admin". Empty string = legacy token,
    # effective role derived from is_admin (see repository.effective_role).
    role = Column(String(16), nullable=False, default="")
```

- [ ] **Step 4: Add the SQLite migration shim**

In `etl_framework/repository/database.py`, locate the block of `ensure_column(...)` calls inside `_ensure_compare_columns` (around line 50) and add, following the exact existing call style:

```python
    if "api_tokens" in tables:
        ensure_column(conn, "api_tokens", "role",
                      "ALTER TABLE api_tokens ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT ''")
```

Note: match how the surrounding calls acquire `conn` and guard on table presence — copy the pattern of the adjacent `scheduled_runs`/`mismatch_details` blocks exactly.

- [ ] **Step 5: Add `effective_role` and extend `TokenRepository.create`**

In `etl_framework/repository/repository.py`, directly above `class TokenRepository` (line 747):

```python
ROLES = ("viewer", "operator", "admin")


def effective_role(token) -> str:
    """Resolve a token's role, deriving from is_admin for legacy rows."""
    role = getattr(token, "role", "") or ""
    if role in ROLES:
        return role
    return "admin" if getattr(token, "is_admin", False) else "operator"
```

Change `TokenRepository.create` (line 758) signature and body:

```python
    def create(self, name: str, expires_at: datetime | None = None,
               is_admin: bool = False, role: str = "") -> tuple[str, ApiToken]:
        if role and role not in ROLES:
            raise ValueError(f"Unknown role: {role!r}")
        if role == "admin":
            is_admin = True
        elif role:
            is_admin = False
        if expires_at is not None:
            cap = datetime.now(timezone.utc) + timedelta(days=_TOKEN_MAX_TTL_DAYS)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at > cap:
                expires_at = cap
        raw = "etl_" + _secrets.token_hex(32)
        token = ApiToken(
            token_hash=self._hash(raw),
            name=name,
            expires_at=expires_at,
            is_admin=is_admin,
            role=role or ("admin" if is_admin else "operator"),
            token_hint=raw[-8:],
        )
        self._db.add(token)
        self._db.commit()
        self._db.refresh(token)
        return raw, token
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_rbac.py -v`
Expected: 5 PASSED.

- [ ] **Step 7: Run existing token tests for regressions**

Run: `python -m pytest tests/test_token_model.py tests/test_token_repository.py tests/test_token_routes.py -q`
Expected: all PASS (create() keeps backward-compatible defaults).

- [ ] **Step 8: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py etl_framework/repository/repository.py tests/unit/test_rbac.py
git commit -m "feat(rbac): add role column with legacy is_admin derivation"
```

---

### Task 2: RBAC — permission matrix + middleware enforcement

**Files:**
- Modify: `api/middleware/auth.py`
- Test: `tests/unit/test_rbac_permissions.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_rbac_permissions.py`:

```python
"""Pure permission-matrix tests for role_permits()."""
from api.middleware.auth import role_permits


def test_viewer_can_read():
    assert role_permits("viewer", "GET", "/api/runs") is True
    assert role_permits("viewer", "HEAD", "/api/jobs") is True
    assert role_permits("viewer", "OPTIONS", "/api/jobs") is True


def test_viewer_cannot_mutate():
    assert role_permits("viewer", "POST", "/api/runs") is False
    assert role_permits("viewer", "PUT", "/api/jobs/x") is False
    assert role_permits("viewer", "DELETE", "/api/jobs/x") is False


def test_viewer_allowed_read_only_posts():
    # POST endpoints that are semantically read-only
    assert role_permits("viewer", "POST", "/api/compare/sql") is True
    assert role_permits("viewer", "POST", "/api/gates/my_job/evaluate") is True
    assert role_permits("viewer", "POST", "/api/auth/verify") is True


def test_operator_can_mutate_but_not_admin_paths():
    assert role_permits("operator", "POST", "/api/runs") is True
    assert role_permits("operator", "DELETE", "/api/jobs/x") is True
    # admin-only management surfaces
    assert role_permits("operator", "POST", "/api/tokens") is False
    assert role_permits("operator", "DELETE", "/api/tokens/3") is False
    assert role_permits("operator", "POST", "/api/settings") is False


def test_admin_can_do_everything():
    assert role_permits("admin", "POST", "/api/tokens") is True
    assert role_permits("admin", "DELETE", "/api/jobs/x") is True
    assert role_permits("admin", "GET", "/api/runs") is True


def test_unknown_role_treated_as_viewer():
    assert role_permits("banana", "GET", "/api/runs") is True
    assert role_permits("banana", "POST", "/api/runs") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_rbac_permissions.py -v`
Expected: FAIL — `ImportError: cannot import name 'role_permits'`.

- [ ] **Step 3: Implement `role_permits` in `api/middleware/auth.py`**

Add below the `_is_exempt` function:

```python
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}

# POST endpoints that are semantically read-only (no state mutation).
_VIEWER_POST_PREFIXES = (
    "/api/compare/",
    "/api/gates/",
    "/api/auth/verify",
)

# Path prefixes that only admin tokens may mutate. GETs on these paths are
# still governed by the read rules (and by route-level require_admin deps).
_ADMIN_WRITE_PREFIXES = (
    "/api/tokens",
    "/api/settings",
)


def role_permits(role: str, method: str, path: str) -> bool:
    """Pure permission matrix: may `role` perform `method` on `path`?

    Unknown roles degrade to viewer (least privilege).
    """
    method = method.upper()
    if role == "admin":
        return True
    if method in _READ_METHODS:
        return True
    if role == "operator":
        return not any(path.startswith(p) for p in _ADMIN_WRITE_PREFIXES)
    # viewer / unknown
    return any(path.startswith(p) for p in _VIEWER_POST_PREFIXES) and method == "POST"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_rbac_permissions.py -v`
Expected: 6 PASSED.

- [ ] **Step 5: Enforce in the middleware dispatch**

In `BearerTokenMiddleware.dispatch` in `api/middleware/auth.py`, find the point where the token has been successfully verified and attached (`request.state.token = token` or equivalent — read the method first). Immediately after the token is validated and before `call_next`, add:

```python
        from etl_framework.repository.repository import effective_role
        if not role_permits(effective_role(token), request.method, request.url.path):
            return JSONResponse(
                status_code=403,
                content={"detail": f"Role '{effective_role(token)}' may not "
                                   f"{request.method} {request.url.path}"},
            )
```

(Import `effective_role` at module top instead if there is no circular-import problem — try the top-level import first; the middleware already imports from `etl_framework.repository.repository`.)

- [ ] **Step 6: Add one integration test to the existing middleware test file**

Open `tests/test_auth_middleware.py`, copy its existing client/fixture pattern, and add a test that creates a `role="viewer"` token, calls `POST /api/jobs` with it, and asserts status 403; then `GET /api/jobs` asserts 200. Follow the file's existing setup verbatim — do not invent a new fixture style.

- [ ] **Step 7: Run the full auth test set**

Run: `python -m pytest tests/unit/test_rbac_permissions.py tests/test_auth_middleware.py tests/test_token_routes.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add api/middleware/auth.py tests/unit/test_rbac_permissions.py tests/test_auth_middleware.py
git commit -m "feat(rbac): enforce viewer/operator/admin permission matrix in middleware"
```

---

### Task 3: RBAC — API schema + UI role picker

**Files:**
- Modify: `api/routes/tokens.py`
- Modify: `frontend/index.html` (Security section token-create form)
- Modify: `frontend/app.js` (token create state/payload)

- [ ] **Step 1: Extend the Pydantic schemas**

In `api/routes/tokens.py`:

```python
class TokenCreate(BaseModel):
    name: str
    expires_at: datetime | None = None
    is_admin: bool = False           # legacy — still honoured when role omitted
    role: str = ""                   # "viewer" | "operator" | "admin" | "" (legacy)
```

```python
class TokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    enabled: bool
    is_admin: bool
    role: str
    token_hint: str
    model_config = {"from_attributes": True}
```

Find the create handler in the same file (the function that calls `TokenRepository(db).create(...)`) and pass the role through, with validation:

```python
    if body.role and body.role not in ("viewer", "operator", "admin"):
        raise HTTPException(status_code=422, detail=f"Unknown role: {body.role}")
    raw, token = TokenRepository(db).create(
        body.name, expires_at=body.expires_at,
        is_admin=body.is_admin, role=body.role,
    )
```

Also enforce: on the **bootstrap path** (first-ever token, unauthenticated), force `role="admin"` regardless of the request body — locate the bootstrap branch guarded by `_bootstrap_lock` and set `body.role = "admin"` there before create.

- [ ] **Step 2: Run token route tests**

Run: `python -m pytest tests/test_token_routes.py -v`
Expected: PASS. If any test asserts an exact response shape, add `role` to the expected keys.

- [ ] **Step 3: Add the UI role picker**

Locate the token-create form: `grep -n "Create Token" frontend/index.html`. In that form, next to the existing admin checkbox (search `is_admin` nearby), add:

```html
<label class="block text-xs text-slate-400 mt-2">Role</label>
<select x-model="newTokenRole"
        class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm">
  <option value="operator">Operator — run &amp; manage jobs</option>
  <option value="viewer">Viewer — read-only</option>
  <option value="admin">Admin — full access incl. tokens</option>
</select>
```

Match the surrounding form's exact utility classes — copy the classes used by the adjacent `<select>`/`<input>` elements rather than the ones above if they differ.

- [ ] **Step 4: Wire the state in `frontend/app.js`**

Find the token-create state (`grep -n "newToken" frontend/app.js`). Add `newTokenRole: 'operator',` beside the existing `newTokenName`-style fields, and include `role: this.newTokenRole` in the JSON body of the create-token fetch call. Also render the role in the token list table: find where `is_admin` is displayed (`grep -n "is_admin" frontend/index.html`) and show `x-text="t.role || (t.is_admin ? 'admin' : 'operator')"` as a badge column.

- [ ] **Step 5: Manual verify**

Run: `python -m uvicorn api.main:app --port 8000`
Open the Security section, create a viewer token, activate it, confirm: History loads (GET ok), creating a job returns a 403 toast.

- [ ] **Step 6: Commit**

```bash
git add api/routes/tokens.py frontend/index.html frontend/app.js
git commit -m "feat(rbac): role selection on token create + role badge in token list"
```

---

### Task 4: DQ Scorecard service

**Files:**
- Create: `api/services/scorecard_service.py`
- Test: `tests/unit/test_scorecard_service.py`

Dimension model (DAMA-aligned) — a DQ violation's `mismatch_type` (the rule type string) maps to a dimension; reconciliation row/value mismatches map to consistency/accuracy:

| Dimension | Signals |
|---|---|
| completeness | `not_null`, `completeness_ratio` violations |
| uniqueness | `unique`, `distinct_count_between` violations |
| validity | `match_regex`, `column_type_check`, `column_value_between`, `no_whitespace`, `pii_mask_check`, `column_percentile` violations |
| consistency | `cross_column_consistency`, `referential_check`, `custom_sql`, `custom_sql_assert` violations + `missing_in_target`/`missing_in_source` counts |
| accuracy | `column_mean_between`, `column_sum_between`, `column_std_dev_between` violations + `value_mismatch_count` |
| timeliness | `freshness` violations |

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_scorecard_service.py`:

```python
"""Scorecard math: dimension mapping and score computation."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from etl_framework.repository.models import (
    Base, MismatchDetail, TestResult, TestRun,
)
from api.services.scorecard_service import (
    DIMENSION_BY_RULE, compute_scorecard, dimension_for,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _mk_run(db, run_id: str, status: str = "COMPLETED"):
    run = TestRun(run_id=run_id, status=status,
                  source_env="dev", target_env="prod")
    db.add(run)
    db.flush()
    return run


def _mk_result(db, run_id: str, job: str, status: str, *,
               value_mismatches: int = 0, missing_target: int = 0,
               src_rows: int = 100) -> TestResult:
    r = TestResult(run_id=run_id, query_name=job, status=status,
                   source_row_count=src_rows, target_row_count=src_rows,
                   value_mismatch_count=value_mismatches,
                   missing_in_target_count=missing_target)
    db.add(r)
    db.flush()
    return r


def test_dimension_for_maps_rule_types():
    assert dimension_for("not_null") == "completeness"
    assert dimension_for("unique") == "uniqueness"
    assert dimension_for("match_regex") == "validity"
    assert dimension_for("referential_check") == "consistency"
    assert dimension_for("column_mean_between") == "accuracy"
    assert dimension_for("freshness") == "timeliness"
    assert dimension_for("weird_unknown") is None


def test_perfect_job_scores_100(db):
    _mk_run(db, "r1")
    _mk_result(db, "r1", "orders", "PASSED")
    db.commit()

    card = compute_scorecard(db, window=10)
    entry = next(e for e in card["jobs"] if e["job"] == "orders")
    assert entry["score"] == 100.0
    assert entry["runs_considered"] == 1


def test_value_mismatches_reduce_accuracy(db):
    _mk_run(db, "r1")
    _mk_result(db, "r1", "orders", "FAILED",
               value_mismatches=10, src_rows=100)
    db.commit()

    card = compute_scorecard(db, window=10)
    entry = next(e for e in card["jobs"] if e["job"] == "orders")
    # 10 mismatched of 100 rows -> accuracy 90.0
    assert entry["dimensions"]["accuracy"] == 90.0
    assert entry["score"] < 100.0


def test_dq_violations_land_in_their_dimension(db):
    _mk_run(db, "r1")
    res = _mk_result(db, "r1", "orders", "FAILED", src_rows=100)
    db.add(MismatchDetail(test_result_id=res.id, mismatch_type="not_null",
                          column_name="email"))
    db.commit()

    card = compute_scorecard(db, window=10)
    entry = next(e for e in card["jobs"] if e["job"] == "orders")
    assert entry["dimensions"]["completeness"] < 100.0
    # untouched dimensions stay perfect
    assert entry["dimensions"]["uniqueness"] == 100.0


def test_window_limits_runs_considered(db):
    for i in range(15):
        _mk_run(db, f"r{i}")
        _mk_result(db, f"r{i}", "orders", "PASSED")
    db.commit()

    card = compute_scorecard(db, window=10)
    entry = next(e for e in card["jobs"] if e["job"] == "orders")
    assert entry["runs_considered"] == 10


def test_overall_summary_present(db):
    _mk_run(db, "r1")
    _mk_result(db, "r1", "a", "PASSED")
    _mk_result(db, "r1", "b", "FAILED", value_mismatches=50, src_rows=100)
    db.commit()

    card = compute_scorecard(db, window=10)
    assert 0.0 <= card["overall_score"] <= 100.0
    assert card["job_count"] == 2
```

Note: check `TestRun`'s required constructor fields first (`grep -n "class TestRun" -A 30 etl_framework/repository/models.py`) and adjust `_mk_run` to satisfy any non-nullable columns without defaults.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_scorecard_service.py -v`
Expected: FAIL — `ModuleNotFoundError: api.services.scorecard_service`.

- [ ] **Step 3: Implement the service**

Create `api/services/scorecard_service.py`:

```python
"""Data-quality dimension scorecard.

Rolls existing test_results / mismatch_details rows up into DAMA-style
quality dimensions per job, over a rolling window of recent runs.
Read-only: no new tables, no writes.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from etl_framework.repository.models import MismatchDetail, TestResult

DIMENSIONS = ("completeness", "uniqueness", "validity",
              "consistency", "accuracy", "timeliness")

DIMENSION_BY_RULE: dict[str, str] = {
    "not_null": "completeness",
    "completeness_ratio": "completeness",
    "unique": "uniqueness",
    "distinct_count_between": "uniqueness",
    "match_regex": "validity",
    "column_type_check": "validity",
    "column_value_between": "validity",
    "no_whitespace": "validity",
    "pii_mask_check": "validity",
    "column_percentile": "validity",
    "cross_column_consistency": "consistency",
    "referential_check": "consistency",
    "custom_sql": "consistency",
    "custom_sql_assert": "consistency",
    "column_mean_between": "accuracy",
    "column_sum_between": "accuracy",
    "column_std_dev_between": "accuracy",
    "freshness": "timeliness",
}

# Per-violation penalty (percentage points) for rule-type violations.
_RULE_PENALTY = 5.0
_MAX_PENALTY = 100.0


def dimension_for(rule_type: str) -> Optional[str]:
    return DIMENSION_BY_RULE.get(rule_type)


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, round(v, 1)))


def compute_scorecard(db: Session, window: int = 10) -> dict:
    """Compute per-job dimension scores over each job's last `window` results."""
    jobs: dict[str, list[TestResult]] = defaultdict(list)
    # newest first; collect up to `window` per job
    for res in db.query(TestResult).order_by(desc(TestResult.id)).limit(window * 200):
        if len(jobs[res.query_name]) < window:
            jobs[res.query_name].append(res)

    entries = []
    for job, results in sorted(jobs.items()):
        dim_scores = {d: 100.0 for d in DIMENSIONS}
        total_rows = 0
        total_value_mismatches = 0
        total_missing = 0
        rule_hits: dict[str, int] = defaultdict(int)

        result_ids = [r.id for r in results]
        if result_ids:
            q = (db.query(MismatchDetail.mismatch_type)
                   .filter(MismatchDetail.test_result_id.in_(result_ids),
                           MismatchDetail.accepted.is_(False)))
            for (mtype,) in q:
                dim = dimension_for(mtype or "")
                if dim:
                    rule_hits[dim] += 1

        for r in results:
            total_rows += max(r.source_row_count or 0, 1)
            total_value_mismatches += r.value_mismatch_count or 0
            total_missing += (r.missing_in_target_count or 0) + \
                             (r.missing_in_source_count or 0)

        # accuracy: value-mismatch rate over compared rows
        dim_scores["accuracy"] = _clamp(
            100.0 * (1 - total_value_mismatches / max(total_rows, 1)))
        # consistency: row presence rate, then rule-violation penalties
        dim_scores["consistency"] = _clamp(
            100.0 * (1 - total_missing / max(total_rows, 1)))
        for dim, hits in rule_hits.items():
            penalty = min(_MAX_PENALTY, hits * _RULE_PENALTY)
            dim_scores[dim] = _clamp(dim_scores[dim] - penalty)

        pass_rate = 100.0 * sum(
            1 for r in results if r.effective_status == "PASSED"
        ) / len(results) if results else 0.0

        score = _clamp(0.4 * pass_rate + 0.6 * (
            sum(dim_scores.values()) / len(dim_scores)))

        entries.append({
            "job": job,
            "runs_considered": len(results),
            "pass_rate": _clamp(pass_rate),
            "dimensions": dim_scores,
            "score": score,
        })

    overall = _clamp(sum(e["score"] for e in entries) / len(entries)) \
        if entries else 0.0
    return {"jobs": entries, "overall_score": overall,
            "job_count": len(entries), "window": window}
```

Note on `effective_status`: `TestResult` defines a property right after the override columns (`models.py:164`) — confirm its name with `grep -n "def effective_status\|@property" -A 3 etl_framework/repository/models.py`. If it is named differently (e.g. it returns `override_status or status`), use that name, or fall back to `(r.override_status or r.status)` inline.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_scorecard_service.py -v`
Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add api/services/scorecard_service.py tests/unit/test_scorecard_service.py
git commit -m "feat(scorecard): DAMA dimension scorecard service over existing run data"
```

---

### Task 5: Scorecard API route

**Files:**
- Create: `api/routes/quality.py`
- Modify: `api/main.py:67` (router registration block)
- Test: `tests/unit/test_quality_route.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_quality_route.py`. First inspect how an existing lightweight route test builds its client — `tests/test_settings_routes.py` is small; mirror its app/client/db-override fixture exactly. The assertions to write:

```python
def test_scorecard_endpoint_shape(client):
    resp = client.get("/api/quality/scorecard")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"jobs", "overall_score", "job_count", "window"}


def test_scorecard_window_param(client):
    resp = client.get("/api/quality/scorecard?window=5")
    assert resp.status_code == 200
    assert resp.json()["window"] == 5


def test_scorecard_window_bounds(client):
    assert client.get("/api/quality/scorecard?window=0").status_code == 422
    assert client.get("/api/quality/scorecard?window=1000").status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_quality_route.py -v`
Expected: FAIL with 404 (route not registered).

- [ ] **Step 3: Implement the route**

Create `api/routes/quality.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.dependencies import get_session
from api.services.scorecard_service import compute_scorecard

router = APIRouter(tags=["quality"])


@router.get("/scorecard")
def get_scorecard(
    window: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_session),
) -> dict:
    """Per-job data-quality dimension scorecard over recent runs."""
    return compute_scorecard(db, window=window)
```

In `api/main.py`, add next to the other imports of route modules and register after line 67:

```python
from api.routes import quality as quality_routes
app.include_router(quality_routes.router, prefix="/api/quality")
```

(Match the import style at the top of `api/main.py` — the other routes are imported there, not inline.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_quality_route.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add api/routes/quality.py api/main.py tests/unit/test_quality_route.py
git commit -m "feat(scorecard): GET /api/quality/scorecard endpoint"
```

---

### Task 6: Scorecard UI sub-tab (History → Scorecard)

**Files:**
- Modify: `frontend/index.html:2371` (History sub-tab button row + panels)
- Modify: `frontend/app.js:242` (state) and the sub-tab load logic near `frontend/app.js:1809`

- [ ] **Step 1: Add the sub-tab button**

In `frontend/index.html`, after the Coverage button (line 2371), add — copying the exact class bindings of the adjacent buttons (the `:class` expression below is abbreviated; replicate the neighbours'):

```html
<button @click="historySubTab='scorecard'; loadScorecard()"
        :class="historySubTab==='scorecard' ? 'text-indigo-400 border-indigo-400' : 'text-slate-400 border-transparent'"
        class="px-3 py-1.5 text-sm border-b-2">Scorecard</button>
```

- [ ] **Step 2: Add the panel**

After the coverage panel `<div x-show="historySubTab==='coverage'">...</div>` (find its closing tag), add:

```html
<div x-show="historySubTab==='scorecard'">
  <div class="flex items-center gap-3 mb-3">
    <span class="text-sm text-slate-400">Window</span>
    <select x-model.number="scorecardWindow" @change="loadScorecard()"
            class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm">
      <option value="5">5 runs</option>
      <option value="10">10 runs</option>
      <option value="25">25 runs</option>
    </select>
    <template x-if="scorecard">
      <span class="ml-auto text-lg font-semibold"
            :class="scorecard.overall_score >= 90 ? 'text-emerald-400' : scorecard.overall_score >= 70 ? 'text-amber-400' : 'text-rose-400'"
            x-text="'Overall: ' + scorecard.overall_score + '%'"></span>
    </template>
  </div>
  <template x-if="scorecard && scorecard.jobs.length === 0">
    <p class="text-slate-500 text-sm">No run results yet.</p>
  </template>
  <table x-show="scorecard && scorecard.jobs.length" class="w-full text-sm">
    <thead>
      <tr class="text-left text-slate-400 border-b border-slate-700">
        <th class="py-2 pr-4">Job</th>
        <th class="py-2 pr-4">Score</th>
        <th class="py-2 pr-4">Pass rate</th>
        <th class="py-2 pr-2">Compl.</th>
        <th class="py-2 pr-2">Uniq.</th>
        <th class="py-2 pr-2">Valid.</th>
        <th class="py-2 pr-2">Consist.</th>
        <th class="py-2 pr-2">Accur.</th>
        <th class="py-2 pr-2">Timel.</th>
        <th class="py-2">Runs</th>
      </tr>
    </thead>
    <tbody>
      <template x-for="j in (scorecard ? scorecard.jobs : [])" :key="j.job">
        <tr class="border-b border-slate-800">
          <td class="py-2 pr-4 font-medium" x-text="j.job"></td>
          <td class="py-2 pr-4">
            <span :class="j.score >= 90 ? 'text-emerald-400' : j.score >= 70 ? 'text-amber-400' : 'text-rose-400'"
                  x-text="j.score + '%'"></span>
          </td>
          <td class="py-2 pr-4" x-text="j.pass_rate + '%'"></td>
          <td class="py-2 pr-2" x-text="j.dimensions.completeness"></td>
          <td class="py-2 pr-2" x-text="j.dimensions.uniqueness"></td>
          <td class="py-2 pr-2" x-text="j.dimensions.validity"></td>
          <td class="py-2 pr-2" x-text="j.dimensions.consistency"></td>
          <td class="py-2 pr-2" x-text="j.dimensions.accuracy"></td>
          <td class="py-2 pr-2" x-text="j.dimensions.timeliness"></td>
          <td class="py-2" x-text="j.runs_considered"></td>
        </tr>
      </template>
    </tbody>
  </table>
</div>
```

Match table/utility classes to the adjacent Coverage panel where they differ.

- [ ] **Step 3: Add the state and loader in `frontend/app.js`**

Near `historySubTab: 'runs',` (line 242) add:

```javascript
scorecard: null,
scorecardWindow: 10,
```

Add the loader method beside the other `loadXxx()` methods (find `loadCoverage` with `grep -n "loadCoverage" frontend/app.js` and place next to it, matching its fetch/auth-header pattern exactly):

```javascript
async loadScorecard() {
  try {
    const resp = await this.apiFetch(`/api/quality/scorecard?window=${this.scorecardWindow}`);
    this.scorecard = await resp.json();
  } catch (e) {
    this.toast('Failed to load scorecard', 'error');
  }
},
```

Important: `apiFetch`/`toast` above are stand-ins — use whatever helper `loadCoverage` actually uses for authenticated fetches and error toasts (copy its body shape verbatim).

- [ ] **Step 4: Manual verify**

Run the server, trigger a simulation run from Launch, open History → Scorecard. Table renders with per-dimension scores; changing Window refetches.

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/app.js
git commit -m "feat(scorecard): History tab Scorecard sub-tab with dimension table"
```

---

### Task 7: Notification channels — Slack / Teams payload formatters

**Files:**
- Modify: `etl_framework/repository/models.py:262` (`NotificationHook`)
- Modify: `etl_framework/repository/database.py` (shim)
- Modify: `api/services/notifier.py`
- Modify: `api/routes/notifications.py` (accept/return `channel`)
- Test: `tests/unit/test_notifier_channels.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_notifier_channels.py`:

```python
"""Per-channel payload formatting for notification hooks."""
from api.services.notifier import format_payload


BASE = {
    "run_id": "abc-123",
    "status": "FAILED",
    "completed_at": "2026-07-16T12:00:00+00:00",
    "source_env": "dev",
    "target_env": "prod",
}


def test_generic_channel_passes_payload_through():
    assert format_payload("generic", BASE) == BASE
    assert format_payload("", BASE) == BASE          # legacy hooks
    assert format_payload(None, BASE) == BASE


def test_slack_channel_wraps_in_text_blocks():
    out = format_payload("slack", BASE)
    assert "text" in out
    assert "abc-123" in out["text"]
    assert "FAILED" in out["text"]


def test_teams_channel_builds_messagecard():
    out = format_payload("teams", BASE)
    assert out["@type"] == "MessageCard"
    assert out["themeColor"] == "D64545"  # red for FAILED
    assert any("abc-123" in str(f) for f in out["sections"][0]["facts"])


def test_teams_passed_run_is_green():
    out = format_payload("teams", {**BASE, "status": "COMPLETED"})
    assert out["themeColor"] == "36A64F"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_notifier_channels.py -v`
Expected: FAIL — `ImportError: cannot import name 'format_payload'`.

- [ ] **Step 3: Add the `channel` column + shim**

`etl_framework/repository/models.py`, inside `NotificationHook` after `enabled`:

```python
    # Delivery channel: "generic" (raw JSON webhook), "slack", "teams", "email".
    channel = Column(String(16), nullable=False, default="generic")
```

`etl_framework/repository/database.py`, in the shim block:

```python
    if "notification_hooks" in tables:
        ensure_column(conn, "notification_hooks", "channel",
                      "ALTER TABLE notification_hooks ADD COLUMN channel VARCHAR(16) NOT NULL DEFAULT 'generic'")
```

- [ ] **Step 4: Implement `format_payload` in `api/services/notifier.py`**

Add above `def notify(`:

```python
_STATUS_COLORS = {  # Teams MessageCard themeColor per outcome
    "COMPLETED": "36A64F", "PASSED": "36A64F",
    "FAILED": "D64545", "ERROR": "D64545",
    "CANCELLED": "AAAAAA",
}


def format_payload(channel: str | None, payload: dict) -> dict:
    """Shape a run-event payload for the hook's delivery channel."""
    if not channel or channel == "generic":
        return payload

    status = str(payload.get("status", ""))
    run_id = payload.get("run_id", "")
    envs = f"{payload.get('source_env', '?')} → {payload.get('target_env', '?')}"

    if channel == "slack":
        emoji = ":white_check_mark:" if status in ("COMPLETED", "PASSED") \
            else ":x:" if status in ("FAILED", "ERROR") else ":no_entry_sign:"
        return {"text": f"{emoji} ETL run *{run_id}* ({envs}) finished: *{status}*"}

    if channel == "teams":
        return {
            "@type": "MessageCard",
            "@context": "http://schema.org/extensions",
            "themeColor": _STATUS_COLORS.get(status, "AAAAAA"),
            "summary": f"ETL run {status}",
            "sections": [{
                "activityTitle": f"ETL run finished: {status}",
                "facts": [
                    {"name": "Run ID", "value": str(run_id)},
                    {"name": "Environments", "value": envs},
                    {"name": "Completed", "value": str(payload.get("completed_at", ""))},
                ],
            }],
        }

    return payload  # unknown channel: degrade to generic
```

Then, inside the `notify()` hook loop, where the payload is handed to `_post`/`_post_and_track`, wrap it: pass `format_payload(getattr(hook, "channel", "generic"), payload)` instead of `payload`. Read the loop body first — apply at the single point where the request body is finalized so both the tracked and untracked paths get the shaped payload.

- [ ] **Step 5: Expose `channel` in the notifications API**

In `api/routes/notifications.py`: add `channel: str = "generic"` to the create/update Pydantic models and `channel: str` to the output model; validate value in `("generic", "slack", "teams", "email")` with a 422 otherwise; pass through to the ORM object on create/update. Follow the file's existing model/handler shapes.

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/unit/test_notifier_channels.py -v`
Expected: 4 PASSED.
Also run: `python -m pytest tests/unit -k notif -q` — existing notification tests still PASS.

- [ ] **Step 7: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py api/services/notifier.py api/routes/notifications.py tests/unit/test_notifier_channels.py
git commit -m "feat(alerts): slack and teams channel formatting on notification hooks"
```

---

### Task 8: Notification channels — email (SMTP) delivery + UI picker

**Files:**
- Modify: `api/services/notifier.py`
- Modify: `frontend/index.html` (Notifications card), `frontend/app.js`
- Test: `tests/unit/test_notifier_email.py`

Email hooks store `mailto:addr1,addr2` in the existing `url` column (no schema change). SMTP config comes from env vars — this app already uses env-var config (`ETL_DATABASE_URL` pattern).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_notifier_email.py`:

```python
"""SMTP email delivery for channel='email' hooks."""
from unittest.mock import MagicMock, patch

import pytest

from api.services.notifier import _send_email, parse_mailto


def test_parse_mailto_extracts_recipients():
    assert parse_mailto("mailto:a@x.com,b@y.com") == ["a@x.com", "b@y.com"]
    assert parse_mailto("mailto:a@x.com") == ["a@x.com"]
    assert parse_mailto("https://hooks.slack.com/x") == []


def test_send_email_noop_without_smtp_host(monkeypatch):
    monkeypatch.delenv("ETL_SMTP_HOST", raising=False)
    result = _send_email(["a@x.com"], "subj", "body")
    assert result.success is False
    assert "ETL_SMTP_HOST" in result.error


@patch("smtplib.SMTP")
def test_send_email_uses_smtp_env(mock_smtp, monkeypatch):
    monkeypatch.setenv("ETL_SMTP_HOST", "mail.internal")
    monkeypatch.setenv("ETL_SMTP_PORT", "587")
    monkeypatch.setenv("ETL_SMTP_FROM", "etl@corp.local")
    server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = server

    result = _send_email(["a@x.com"], "ETL run FAILED", "run abc-123 failed")

    assert result.success is True
    mock_smtp.assert_called_once_with("mail.internal", 587, timeout=10)
    server.send_message.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_notifier_email.py -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement in `api/services/notifier.py`**

```python
import os
import smtplib
from email.message import EmailMessage


def parse_mailto(url: str) -> list[str]:
    """Extract recipient list from a mailto: pseudo-URL, else []."""
    if not url.startswith("mailto:"):
        return []
    return [a.strip() for a in url[len("mailto:"):].split(",") if a.strip()]


def _send_email(recipients: list[str], subject: str, body: str) -> "DeliveryResult":
    host = os.environ.get("ETL_SMTP_HOST", "")
    if not host:
        return DeliveryResult(success=False,
                              error="ETL_SMTP_HOST not configured", status_code=None)
    port = int(os.environ.get("ETL_SMTP_PORT", "25"))
    sender = os.environ.get("ETL_SMTP_FROM", "etl-framework@localhost")
    user = os.environ.get("ETL_SMTP_USER", "")
    password = os.environ.get("ETL_SMTP_PASSWORD", "")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if os.environ.get("ETL_SMTP_STARTTLS", "").lower() in ("1", "true"):
                server.starttls()
            if user:
                server.login(user, password)
            server.send_message(msg)
        return DeliveryResult(success=True, error=None, status_code=None)
    except Exception as exc:  # noqa: BLE001 — delivery is fire-and-forget
        logger.warning("Email delivery failed: %s", exc)
        return DeliveryResult(success=False, error=str(exc), status_code=None)
```

Check `DeliveryResult`'s actual fields first (`grep -n "class DeliveryResult" -A 5 api/services/notifier.py`) and construct it with its real field names/order.

Then in the `notify()` hook loop: when `getattr(hook, "channel", "") == "email"`, instead of calling `_post`, dispatch (on the same background thread the webhook path uses):

```python
            recipients = parse_mailto(hook.url)
            subject = f"ETL run {payload.get('status', '')}: {payload.get('run_id', '')}"
            body = json.dumps(payload, indent=2, default=str)
            # reuse the same threading + delivery-tracking wrapper as _post
```

Wire it through the same tracking path (`_post_and_track` records DeliveryResult) — add an `email` branch there so deliveries appear in the existing delivery history. The SSRF guard (`_is_ssrf_target`) must NOT run for `mailto:` URLs (it would block on resolution failure); skip it when `parse_mailto(hook.url)` is non-empty.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/test_notifier_email.py tests/unit/test_notifier_channels.py -v`
Expected: all PASSED.

- [ ] **Step 5: Add the channel picker to the Notifications UI**

Find the webhook-create form: `grep -n "Notifications" frontend/index.html` then locate the URL input. Add a channel `<select>` bound to the hook form state (match adjacent field classes):

```html
<label class="block text-xs text-slate-400 mt-2">Channel</label>
<select x-model="newHookChannel"
        class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm">
  <option value="generic">Generic webhook (raw JSON)</option>
  <option value="slack">Slack incoming webhook</option>
  <option value="teams">Microsoft Teams webhook</option>
  <option value="email">Email (mailto:a@x.com,b@y.com)</option>
</select>
```

In `frontend/app.js`: add `newHookChannel: 'generic',` beside the existing hook-form state (find with `grep -n "newHook" frontend/app.js`), include `channel: this.newHookChannel` in the create payload, and show the channel as a badge in the hook list row.

- [ ] **Step 6: Update README capability list**

In `README.md`, extend the webhook bullet: "…with optional HMAC-SHA256 signing **and per-hook channel formatting: generic JSON, Slack, Microsoft Teams, or email (SMTP via `ETL_SMTP_HOST`/`ETL_SMTP_PORT`/`ETL_SMTP_FROM`/`ETL_SMTP_USER`/`ETL_SMTP_PASSWORD`/`ETL_SMTP_STARTTLS`)**."

- [ ] **Step 7: Commit**

```bash
git add api/services/notifier.py frontend/index.html frontend/app.js README.md tests/unit/test_notifier_email.py
git commit -m "feat(alerts): email channel via SMTP env config + channel picker UI"
```

---

### Task 9: Command palette (Ctrl+K) in the UI

**Files:**
- Modify: `frontend/index.html` (overlay markup, inside the root Alpine scope)
- Modify: `frontend/app.js` (state + actions)

Self-contained: navigate to any tab/sub-tab, jump to a job by name, trigger common actions. No backend changes.

- [ ] **Step 1: Add state and command list in `frontend/app.js`**

Near the top-level state (beside `historySubTab`), add:

```javascript
paletteOpen: false,
paletteQuery: '',
paletteIndex: 0,
```

Add methods beside the other UI helpers:

```javascript
paletteCommands() {
  const nav = [
    { label: 'Go to Config', run: () => this.activeTab = 'config' },
    { label: 'Go to Launch', run: () => this.activeTab = 'launch' },
    { label: 'Go to Monitor', run: () => this.activeTab = 'monitor' },
    { label: 'Go to History', run: () => this.activeTab = 'history' },
    { label: 'Go to Adapters', run: () => this.activeTab = 'adapters' },
    { label: 'Go to Reports', run: () => this.activeTab = 'reports' },
    { label: 'Go to Compare', run: () => this.activeTab = 'compare' },
    { label: 'Go to Contracts', run: () => this.activeTab = 'contracts' },
    { label: 'History: Scorecard', run: () => { this.activeTab = 'history'; this.historySubTab = 'scorecard'; this.loadScorecard(); } },
    { label: 'History: Coverage', run: () => { this.activeTab = 'history'; this.historySubTab = 'coverage'; this.loadCoverage(); } },
    { label: 'History: Trends', run: () => { this.activeTab = 'history'; this.historySubTab = 'trends'; } },
    { label: 'History: Audit log', run: () => { this.activeTab = 'history'; this.historySubTab = 'audit'; this.loadAudit(); } },
    { label: 'New Job', run: () => { this.activeTab = 'launch'; this.openNewJob(); } },
  ];
  const jobs = (this.jobs || []).map(j => ({
    label: `Job: ${j.name}`,
    run: () => { this.activeTab = 'launch'; this.editJob(j); },
  }));
  const q = this.paletteQuery.toLowerCase();
  return [...nav, ...jobs]
    .filter(c => !q || c.label.toLowerCase().includes(q))
    .slice(0, 12);
},
openPalette() {
  this.paletteOpen = true;
  this.paletteQuery = '';
  this.paletteIndex = 0;
  this.$nextTick(() => this.$refs.paletteInput && this.$refs.paletteInput.focus());
},
runPaletteCommand() {
  const cmds = this.paletteCommands();
  const cmd = cmds[this.paletteIndex] || cmds[0];
  if (cmd) { this.paletteOpen = false; cmd.run(); }
},
```

Adjust three references to reality before saving: (a) the tab id strings — read the `tabs: [` array at `frontend/app.js:112` and use its exact tab ids; (b) `this.jobs` — use whatever property holds the job catalog (find with `grep -n "jobs:" frontend/app.js`); (c) `openNewJob`/`editJob` — use the actual method names that open the job modal (find with `grep -n "editJob\|newJob" frontend/app.js`). Drop the `New Job` and `Job:` entries if no clean method exists.

- [ ] **Step 2: Register the global shortcut**

In the Alpine component's `init()` (find with `grep -n "init()" frontend/app.js`), add:

```javascript
window.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    this.openPalette();
  }
  if (e.key === 'Escape' && this.paletteOpen) this.paletteOpen = false;
});
```

- [ ] **Step 3: Add the overlay markup**

In `frontend/index.html`, just before the closing tag of the root Alpine scope element (the element carrying `x-data`), add:

```html
<!-- Command palette (Ctrl+K) -->
<div x-show="paletteOpen" x-cloak
     class="fixed inset-0 z-50 flex items-start justify-center pt-24 bg-black/60"
     @click.self="paletteOpen = false">
  <div class="w-full max-w-lg bg-slate-900 border border-slate-700 rounded-lg shadow-2xl overflow-hidden">
    <input x-ref="paletteInput" x-model="paletteQuery" @input="paletteIndex = 0"
           @keydown.arrow-down.prevent="paletteIndex = Math.min(paletteIndex + 1, paletteCommands().length - 1)"
           @keydown.arrow-up.prevent="paletteIndex = Math.max(paletteIndex - 1, 0)"
           @keydown.enter.prevent="runPaletteCommand()"
           placeholder="Type a command or job name…"
           class="w-full bg-transparent px-4 py-3 text-sm text-slate-100 outline-none border-b border-slate-700">
    <ul class="max-h-72 overflow-y-auto">
      <template x-for="(cmd, i) in paletteCommands()" :key="cmd.label">
        <li @click="paletteIndex = i; runPaletteCommand()"
            :class="i === paletteIndex ? 'bg-indigo-600/30 text-slate-100' : 'text-slate-300'"
            class="px-4 py-2 text-sm cursor-pointer" x-text="cmd.label"></li>
      </template>
      <li x-show="paletteCommands().length === 0"
          class="px-4 py-3 text-sm text-slate-500">No matches</li>
    </ul>
    <div class="px-4 py-2 text-[11px] text-slate-500 border-t border-slate-800">
      ↑↓ navigate · Enter run · Esc close
    </div>
  </div>
</div>
```

- [ ] **Step 4: Rebuild Tailwind if new utility classes were introduced**

Run: `npm run build:css`
Expected: `frontend/vendor/tailwind.css` regenerated. (Skip only if every class above already appears elsewhere in the codebase.)

- [ ] **Step 5: Manual verify**

Serve the app, press Ctrl+K: overlay opens focused; typing filters; arrow keys move selection; Enter navigates to the tab; Esc closes; clicking the backdrop closes.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/app.js frontend/vendor/tailwind.css
git commit -m "feat(ui): Ctrl+K command palette for tab, sub-tab, and job navigation"
```

---

### Task 10: Documentation

**Files:**
- Modify: `README.md` (Capabilities list, Using The Web UI, auth section)
- Modify: `docs/auth.md`

- [ ] **Step 1: README capability bullets**

Add to the Capabilities list:

```markdown
- **Role-based access control** — every API token has a role: `viewer` (read-only + Compare/Gate checks), `operator` (run and manage jobs), or `admin` (full access including token and settings management). Legacy tokens keep working: `is_admin` maps to `admin`, everything else to `operator`.
- **DQ Scorecard** — `GET /api/quality/scorecard?window=10` rolls recent run results and DQ violations up into six DAMA quality dimensions (completeness, uniqueness, validity, consistency, accuracy, timeliness) per job, with an overall score. Browse it in History → Scorecard.
- **Alert channels** — notification hooks now have a channel: generic JSON webhook, Slack, Microsoft Teams, or email (SMTP configured via `ETL_SMTP_*` env vars).
- **Command palette** — press `Ctrl+K` (`Cmd+K` on macOS) anywhere in the UI to jump to any tab, History sub-tab, or job.
```

- [ ] **Step 2: docs/auth.md**

Add a "Roles" section documenting the three roles, the permission matrix (read vs mutate vs admin surfaces, the viewer-allowed POST prefixes `/api/compare/`, `/api/gates/`, `/api/auth/verify`), the legacy derivation rule, and a curl example creating a viewer token (`"role": "viewer"` in the POST /api/tokens body).

- [ ] **Step 3: Full test suite**

Run: `python -m pytest tests/unit -q`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/auth.md
git commit -m "docs: RBAC roles, DQ scorecard, alert channels, command palette"
```

---

## Self-Review Notes

- Spec coverage: RBAC (Tasks 1–3), scorecard (4–6), alert channels (7–8), UI palette (9), docs (10). Deferred items are explicitly listed with rationale.
- Where this plan says "copy the adjacent pattern," that is deliberate: the exact fixture/helper names in `tests/test_auth_middleware.py`, the `apiFetch`-style helper in `app.js`, and the `DeliveryResult` field order must come from the file at execution time — the plan gives the grep command to find each one and the exact behaviour to implement.
- Type consistency checked: `role_permits(role, method, path)` used identically in Tasks 2–3; `format_payload(channel, payload)` in Task 7 matches its call site; `compute_scorecard(db, window)` matches route usage in Task 5; `loadScorecard()` referenced by both Task 6 and Task 9 palette entries.
