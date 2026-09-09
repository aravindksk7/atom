# Scoped CI Trigger Tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a third token privilege level, `role="ci_trigger"`, enforced centrally in
`BearerTokenMiddleware` against a small allowlist, so a CI token can launch selections and
sequences and read run results but cannot touch configs, jobs, other tokens, or settings.

**Architecture:** One additive column (`ApiToken.role`, migrated the same way every other
column in this codebase has been), one new field threaded through the existing token
create/rotate code path, and one allow/deny check added at the single point every
authenticated request already passes through. No new tables, no per-route annotations to
remember, no changes to the `atom` CLI or CI scripts (the allowlist was built from what
they already call).

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy (server), Alpine.js (frontend).

**Spec:** `docs/superpowers/specs/2026-09-10-scoped-ci-trigger-tokens-design.md`

---

## File Structure

| File | Change |
|---|---|
| `etl_framework/repository/models.py` | `ApiToken` gains `role` column. |
| `etl_framework/repository/database.py` | Migration: `ensure_column` for `api_tokens.role`. |
| `etl_framework/repository/repository.py` | `TokenRepository.create` gains `role` param. |
| `api/routes/tokens.py` | `TokenCreate`/`TokenOut`/`TokenCreatedOut` gain `role`; conflict validation; `rotate_token` carries `role` through. |
| `api/middleware/auth.py` | Allowlist + scope check at both `call_next` return points. |
| `frontend/features/config.js` | `newTokenRole`/`createdTokenRole` extend to `ci_trigger`; `createToken()` sends `role`. |
| `frontend/partials/tab-config.html` | Role `<select>` gains a third option; token row shows the role; confirmation banner text extends. |
| `frontend/partials/tab-launch.html` | CI/CD modal's "STEP 1" text recommends the CI Trigger role. |
| `frontend/index.html` | Rebuilt from the above two partials (generated artifact). |
| `tests/unit/test_auth.py` | New tests: token creation validation, rotation role-preservation, middleware allow/deny table. |
| `tests/unit/test_ci_trigger_scope.py` | New file: end-to-end launch+poll+junit flow through a `ci_trigger` token, plus a 403 on an out-of-scope route. |

---

## Task 1: `ApiToken.role` column + migration

**Files:**
- Modify: `etl_framework/repository/models.py`
- Modify: `etl_framework/repository/database.py`
- Test: `tests/unit/test_auth.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_auth.py`, in the "TokenRepository unit tests" section (after
`test_list_returns_all_tokens`, before the "Middleware integration" section header):

```python
def test_create_defaults_role_to_full():
    db = _session()
    _, token = TokenRepository(db).create("test")
    assert token.role == "full"


def test_create_accepts_ci_trigger_role():
    db = _session()
    _, token = TokenRepository(db).create("ci-bot", role="ci_trigger")
    assert token.role == "ci_trigger"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_auth.py -v -k role`
Expected: FAIL — `AttributeError: 'ApiToken' object has no attribute 'role'` (or
`TypeError: create() got an unexpected keyword argument 'role'`).

- [ ] **Step 3: Add the model column**

In `etl_framework/repository/models.py`, in the `ApiToken` class (currently lines
298-309), add one line after `token_hint`:

```python
class ApiToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    token_hint = Column(String(8), nullable=False, default="")
    role = Column(String(20), nullable=False, default="full")
```

- [ ] **Step 4: Add the migration**

In `etl_framework/repository/database.py`, in `_ensure_compare_columns`, immediately after
the existing line (currently line 113):

```python
        ensure_column(conn, "api_tokens", "token_hint", "ALTER TABLE api_tokens ADD COLUMN token_hint VARCHAR(8) NOT NULL DEFAULT ''")
```

add:

```python
        ensure_column(conn, "api_tokens", "role", "ALTER TABLE api_tokens ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'full'")
```

- [ ] **Step 5: Add the `role` parameter to `TokenRepository.create`**

In `etl_framework/repository/repository.py`, `TokenRepository.create` (currently lines
899-917), change the signature and the constructed row:

```python
    def create(self, name: str, expires_at: datetime | None = None, is_admin: bool = False,
               role: str = "full") -> tuple[str, ApiToken]:
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
            token_hint=raw[-8:],
            role=role,
        )
        self._db.add(token)
        self._db.commit()
        self._db.refresh(token)
        return raw, token
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_auth.py -v`
Expected: PASS — every test in the file (confirms the new column/param didn't break any
existing token test).

- [ ] **Step 7: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py etl_framework/repository/repository.py tests/unit/test_auth.py
git commit -m "feat(tokens): add role column, default full, ci_trigger accepted"
```

---

## Task 2: `TokenCreate`/`TokenOut` API surface + rotation

**Files:**
- Modify: `api/routes/tokens.py`
- Test: `tests/unit/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_auth.py`, after the tests added in Task 1:

```python
def test_token_create_rejects_admin_and_ci_trigger_combo(client):
    c, _ = client
    resp = c.post("/api/tokens", json={"name": "bootstrap"})  # bootstrap admin token
    admin_raw = resp.json()["raw_token"]
    resp = c.post(
        "/api/tokens",
        json={"name": "bad-combo", "is_admin": True, "role": "ci_trigger"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert resp.status_code == 422


def test_token_create_defaults_role_full_in_response(client):
    c, _ = client
    resp = c.post("/api/tokens", json={"name": "bootstrap"})
    assert resp.json()["role"] == "full"


def test_token_create_ci_trigger_role_in_response(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    resp = c.post(
        "/api/tokens",
        json={"name": "ci-bot", "role": "ci_trigger"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "ci_trigger"
    assert resp.json()["is_admin"] is False


def test_rotate_preserves_ci_trigger_role(client):
    c, engine = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    created = c.post(
        "/api/tokens",
        json={"name": "ci-bot", "role": "ci_trigger"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    ).json()
    resp = c.post(
        f"/api/tokens/{created['id']}/rotate",
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "ci_trigger"
```

These use the `client` fixture already defined further down in the same file (the
`TestClient`-with-real-middleware one) — since it's referenced before its own definition
in file order, move these four tests to just after the `client` fixture definition
(after `test_token_creation_endpoint_is_exempt`, at the end of the file) rather than
inline with the plain `TokenRepository` unit tests from Task 1.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_auth.py -v -k "combo or ci_trigger or rotate_preserves"`
Expected: FAIL — `422` tests get `201` instead (no conflict validation yet), and
`role` assertions get `KeyError` (field doesn't exist in the response yet).

- [ ] **Step 3: Add `role` to the schemas and validate the conflict**

In `api/routes/tokens.py`, update the three schema classes (currently lines 24-43):

```python
from typing import Literal


class TokenCreate(BaseModel):
    name: str
    expires_at: datetime | None = None
    is_admin: bool = False
    role: Literal["full", "ci_trigger"] = "full"


class TokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    enabled: bool
    is_admin: bool
    token_hint: str
    role: str
    model_config = {"from_attributes": True}


class TokenCreatedOut(TokenOut):
    raw_token: str  # shown once only
```

(Add `from typing import Literal` to the existing import block at the top of the file.)

In `create_token` (currently lines 81-123), add the conflict check right after resolving
`is_bootstrap`/`is_admin` and before `repo.create(...)`:

```python
        is_admin = True if is_bootstrap else body.is_admin
        if is_admin and body.role == "ci_trigger":
            raise HTTPException(
                status_code=422,
                detail="A token cannot be both an admin token and a ci_trigger token",
            )
        raw, token = repo.create(body.name, body.expires_at, is_admin=is_admin, role=body.role)
```

Update the two `TokenCreatedOut(...)` constructions in `create_token` and `rotate_token`
to include `role=token.role` / `role=new_token.role` respectively.

- [ ] **Step 4: Carry `role` through rotation**

In `rotate_token` (currently lines 167-194), change:

```python
    raw, new_token = repo.create(old.name, old.expires_at, is_admin=old.is_admin)
```

to:

```python
    raw, new_token = repo.create(old.name, old.expires_at, is_admin=old.is_admin, role=old.role)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_auth.py -v`
Expected: PASS — the whole file.

- [ ] **Step 6: Run the whole unit suite to check nothing regressed**

Run: `python -m pytest tests/unit -q`
Expected: PASS, same count as before this task plus the new tests, 0 failures.

- [ ] **Step 7: Commit**

```bash
git add api/routes/tokens.py tests/unit/test_auth.py
git commit -m "feat(tokens): expose role on the API, reject admin+ci_trigger combo"
```

---

## Task 3: Middleware enforcement

**Files:**
- Modify: `api/middleware/auth.py`
- Test: `tests/unit/test_auth.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_auth.py` (after the Task 2 tests, still using the `client`
fixture):

```python
def _create_ci_trigger_token(c, admin_raw: str) -> str:
    resp = c.post(
        "/api/tokens",
        json={"name": "ci-bot", "role": "ci_trigger"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    return resp.json()["raw_token"]


def test_ci_trigger_token_allowed_on_selections_list(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    resp = c.get("/api/selections", headers={"Authorization": f"Bearer {ci_raw}"})
    assert resp.status_code == 200


def test_ci_trigger_token_allowed_on_sequences_list(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    resp = c.get("/api/sequences", headers={"Authorization": f"Bearer {ci_raw}"})
    assert resp.status_code == 200


def test_ci_trigger_token_allowed_on_run_status(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    resp = c.get("/api/runs/nonexistent-run-id/status", headers={"Authorization": f"Bearer {ci_raw}"})
    # 404 (route reached, run not found) proves the scope check let it through --
    # a scope denial would be 403, never 404.
    assert resp.status_code == 404


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/configs"),
    ("GET", "/api/jobs"),
    ("GET", "/api/tokens"),
    ("POST", "/api/schedules"),
    ("GET", "/api/runs"),  # bare list -- deliberately excluded, see spec
    ("DELETE", "/api/runs/some-run-id"),
])
def test_ci_trigger_token_denied_outside_allowlist(client, method, path):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    resp = c.request(method, path, headers={"Authorization": f"Bearer {ci_raw}"})
    assert resp.status_code == 403


def test_full_token_unaffected_by_scope_check(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    full_raw = c.post(
        "/api/tokens", json={"name": "full-user"},
        headers={"Authorization": f"Bearer {admin_raw}"},
    ).json()["raw_token"]
    resp = c.get("/api/configs", headers={"Authorization": f"Bearer {full_raw}"})
    assert resp.status_code == 200


def test_ci_trigger_scope_denial_is_audit_logged(client):
    c, _ = client
    admin_raw = c.post("/api/tokens", json={"name": "bootstrap"}).json()["raw_token"]
    ci_raw = _create_ci_trigger_token(c, admin_raw)
    c.get("/api/configs", headers={"Authorization": f"Bearer {ci_raw}"})
    resp = c.get("/api/audit", headers={"Authorization": f"Bearer {admin_raw}"})
    assert resp.status_code == 200
    reasons = [e.get("diff", {}).get("reason") for e in resp.json() if e.get("action") == "token.auth_failed"]
    assert "ci_trigger_scope_denied" in reasons
```

(`GET /api/audit`, mounted at `api/main.py`'s `prefix="/api/audit"`, returns
`list[AuditEventOut]`, whose per-event dict payload field is `diff` — confirmed against
`api/schemas.py::AuditEventOut` and `_audit_failure`'s call in `api/middleware/auth.py`,
which passes `{"reason": reason, "path": ..., "method": ..., "ip": ...}` as that
positional argument.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_auth.py -v -k "ci_trigger or full_token_unaffected"`
Expected: FAIL — every allowlisted-route test currently passes already (no enforcement
exists yet, so nothing is denied), but every "denied" test gets `200`/`201` instead of
`403`, and the audit-log test finds no `ci_trigger_scope_denied` reason.

- [ ] **Step 3: Add the allowlist and enforcement**

In `api/middleware/auth.py`, after the existing `_EXEMPT_PATTERNS` block (currently ending
at line 26) and before `_has_sap_bo_auth` (currently line 29), add:

```python
_CI_TRIGGER_ALLOWED: list[tuple[str, re.Pattern]] = [
    ("GET", re.compile(r"^/api/selections$")),
    ("GET", re.compile(r"^/api/selections/\d+$")),
    ("POST", re.compile(r"^/api/selections/\d+/launch$")),
    ("GET", re.compile(r"^/api/sequences$")),
    ("GET", re.compile(r"^/api/sequences/\d+$")),
    ("POST", re.compile(r"^/api/sequences/\d+/launch$")),
    ("GET", re.compile(r"^/api/runs/[^/]+$")),
    ("GET", re.compile(r"^/api/runs/[^/]+/status$")),
    ("GET", re.compile(r"^/api/runs/[^/]+/junit$")),
    ("GET", re.compile(r"^/api/runs/[^/]+/markdown-summary$")),
    ("GET", re.compile(r"^/api/runs/[^/]+/report$")),
    ("GET", re.compile(r"^/api/runs/[^/]+/export$")),
]


def _ci_trigger_denied(method: str, path: str) -> bool:
    return not any(
        method == allowed_method and pattern.match(path)
        for allowed_method, pattern in _CI_TRIGGER_ALLOWED
    )
```

Then, inside `BearerTokenMiddleware.dispatch`, add the check at **both** points that
currently do `return await call_next(request)` after a successful token resolution.

Cache-hit branch — currently (lines 94-101):

```python
                request.state.token_actor = token.name
                request.state.token_id = token.id
                request.state.token = token
                return await call_next(request)
```

becomes:

```python
                request.state.token_actor = token.name
                request.state.token_id = token.id
                request.state.token = token
                if token.role == "ci_trigger" and _ci_trigger_denied(request.method, request.url.path):
                    self._audit_failure(request, "ci_trigger_scope_denied")
                    return JSONResponse(
                        {"detail": "This token is not permitted to call this endpoint"},
                        status_code=403,
                    )
                return await call_next(request)
```

DB-lookup branch — currently (lines 133-141):

```python
        if token is None:
            self._audit_failure(request, "invalid_token")
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
```

becomes:

```python
        if token is None:
            self._audit_failure(request, "invalid_token")
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        if token.role == "ci_trigger" and _ci_trigger_denied(request.method, request.url.path):
            self._audit_failure(request, "ci_trigger_scope_denied")
            return JSONResponse(
                {"detail": "This token is not permitted to call this endpoint"},
                status_code=403,
            )

        return await call_next(request)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_auth.py -v`
Expected: PASS — the whole file.

- [ ] **Step 5: Run the whole unit suite to check nothing regressed**

Run: `python -m pytest tests/unit -q`
Expected: PASS, 0 failures — in particular, every existing test using a plain (`role`
defaulted to `"full"`) token anywhere in the suite must be completely unaffected, since
the new check only ever fires for `role == "ci_trigger"`.

- [ ] **Step 6: Commit**

```bash
git add api/middleware/auth.py tests/unit/test_auth.py
git commit -m "feat(auth): enforce ci_trigger token scope at the middleware choke point"
```

---

## Task 4: Frontend — token role UI

**Files:**
- Modify: `frontend/features/config.js`
- Modify: `frontend/partials/tab-config.html`
- Modify: `frontend/partials/tab-launch.html`

No Python test covers frontend JS in this repo. Verification is a manual check in Step 5.

- [ ] **Step 1: Extend state defaults**

In `frontend/features/config.js`, no change needed to the state block itself (`newTokenRole: 'user'` and `createdTokenRole: 'user'`, currently lines 47 and 51, stay as-is — `'user'`/`'admin'`/`'ci_trigger'` are all valid values of the same field, just extending which strings it can hold).

- [ ] **Step 2: Send `role` on create, extend the confirmation label**

In `frontend/features/config.js`, `createToken()` (currently lines 488-525), change the
request body construction:

```javascript
        const body = {
          name,
          is_admin: fromAuthWizard || this.newTokenRole === 'admin',
          role: !fromAuthWizard && this.newTokenRole === 'ci_trigger' ? 'ci_trigger' : 'full',
          expires_at: !fromAuthWizard && this.newTokenExpiresAt
            ? new Date(this.newTokenExpiresAt).toISOString()
            : null,
        };
```

and the post-creation state (currently `this.createdTokenRole = resp.is_admin ? 'admin' : 'user';`):

```javascript
          this.createdTokenRole = resp.is_admin ? 'admin' : (resp.role === 'ci_trigger' ? 'ci_trigger' : 'user');
```

- [ ] **Step 3: Add the role option and row display**

In `frontend/partials/tab-config.html`, the role `<select>` (currently lines 282-285):

```html
<select x-model="newTokenRole" class="field-input field-select" data-testid="security-new-token-role-select" id="a11y-config-role">
  <option value="user">Standard user</option>
  <option value="admin">Administrator</option>
  <option value="ci_trigger">CI Trigger (launch + read-only)</option>
</select>
```

The confirmation banner heading (currently line 296):

```html
<div class="font-semibold text-amber-800">Access created for <span x-text="createdTokenRole === 'admin' ? 'an administrator' : (createdTokenRole === 'ci_trigger' ? 'a CI trigger' : 'a standard user')"></span> — copy this token now:</div>
```

The per-token row label (currently line 310):

```html
<span class="ml-1 text-xs font-medium" :class="tok.is_admin ? 'text-indigo-600' : (tok.role === 'ci_trigger' ? 'text-emerald-600' : 'text-slate-500')" x-text="tok.is_admin ? '(administrator)' : (tok.role === 'ci_trigger' ? '(CI trigger)' : '(standard user)')"></span>
```

- [ ] **Step 4: Update the CI/CD modal's onboarding text**

In `frontend/partials/tab-launch.html`, the STEP 1 paragraph (currently lines 1585-1586):

```html
<div class="label">STEP 1 — Create an API token</div>
<p class="text-muted">Use the Tokens section to create one — pick the <strong>CI Trigger</strong> role so this pipeline can only launch runs and read results, not touch configs or other tokens — then store it as a masked/protected GitLab CI/CD variable.</p>
```

- [ ] **Step 5: Rebuild the generated HTML and verify manually**

Run: `node scripts/build-html.js`
Expected: only `frontend/index.html` changes (plus, per the known build-script quirk from
prior CI/CD work, possibly a line-ending-only touch to an unrelated file — if so, revert
that unrelated file with `git checkout -- <file>` before committing, don't include it).

If a dev server is reasonably available in this environment: start it, go to Config →
Security, open "+ Add User Access", confirm the role dropdown shows "CI Trigger (launch +
read-only)", create one, confirm the confirmation banner and the token's row both say
"CI trigger" / "(CI trigger)". Then open the Launch tab's GitLab CI modal and confirm STEP
1 recommends the CI Trigger role.

If a dev server isn't reasonably available, do a static trace instead (re-read the final
state of all three files and confirm the Alpine bindings reference real, existing state
fields) and say so explicitly in your report.

- [ ] **Step 6: Commit**

```bash
git add frontend/features/config.js frontend/partials/tab-config.html frontend/partials/tab-launch.html frontend/index.html
git commit -m "feat(ui): add CI Trigger token role to the Security panel and CI/CD modal"
```

---

## Task 5: End-to-end scope test — real launch flow through a `ci_trigger` token

**Files:**
- Create: `tests/unit/test_ci_trigger_scope.py`

- [ ] **Step 1: Write the test**

```python
"""End-to-end: a role=ci_trigger token can drive the real launch/poll/artifact flow the
atom CLI and CI scripts use, and gets 403 on anything outside that surface.

Unlike test_auth.py's allow/deny table (which checks route-by-route in isolation), this
exercises one full, real Job Selection launch through a ci_trigger token, the same way
scripts/ci/run-atom-target.sh actually would.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch):
    from api.main import app
    from etl_framework.repository.database import Base
    from etl_framework.repository import database as _db_module
    import etl_framework.repository.models  # noqa: F401
    from etl_framework.repository.repository import JobRepository, JobSelectionRepository, TokenRepository

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    monkeypatch.setattr(_db_module, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr("api.routes.selections._execute_run", lambda *a, **k: None)

    with Session(engine) as db:
        admin_raw, _ = TokenRepository(db).create("admin", is_admin=True)
        ci_raw, _ = TokenRepository(db).create("ci-bot", role="ci_trigger")
        JobRepository(db).create({
            "name": "orders_recon", "description": "", "tags": [],
            "job_type": "reconciliation", "query": "SELECT 1", "key_columns": ["id"],
            "exclude_columns": [], "params": {}, "enabled": True,
        })
        selection = JobSelectionRepository(db).create(
            name="nightly", description="", tags=[], job_sequence=["orders_recon"],
            run_settings={},
        )
        selection_id = selection.id

    return {
        "client": TestClient(app),
        "admin_raw": admin_raw,
        "ci_raw": ci_raw,
        "selection_id": selection_id,
    }


def test_ci_trigger_token_drives_real_launch_and_artifact_fetch(client):
    c = client["client"]
    headers = {"Authorization": f"Bearer {client['ci_raw']}"}

    launch_resp = c.post(
        f"/api/selections/{client['selection_id']}/launch",
        json={"source_env": "dev"}, headers=headers,
    )
    assert launch_resp.status_code == 202, launch_resp.text
    run_id = launch_resp.json()["run_id"]

    status_resp = c.get(f"/api/runs/{run_id}/status", headers=headers)
    assert status_resp.status_code == 200

    junit_resp = c.get(f"/api/runs/{run_id}/junit", headers=headers)
    assert junit_resp.status_code == 200


def test_ci_trigger_token_still_denied_on_configs(client):
    c = client["client"]
    resp = c.get("/api/configs", headers={"Authorization": f"Bearer {client['ci_raw']}"})
    assert resp.status_code == 403
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/unit/test_ci_trigger_scope.py -v`
Expected: PASS — both tests.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_ci_trigger_scope.py
git commit -m "test(auth): cover a real launch+artifact flow through a ci_trigger token"
```

---

## Task 6: Full verification pass

- [ ] **Step 1: Run the full unit suite**

Run: `python -m pytest tests/unit -q`
Expected: PASS, 0 failures.

- [ ] **Step 2: Confirm the CLI and CI scripts need no changes**

Run: `grep -rn "ci_trigger\|role" scripts/ci/ etl_framework/cli/`
Expected: no output — confirms nothing in the CI-facing tooling references the new
concept, which is the intended outcome (a `ci_trigger` token is a drop-in replacement for
a `full` token in `ATOM_API_TOKEN`, no tooling change required).

- [ ] **Step 3: Spot-check the migration is idempotent**

Run (from repo root, using a throwaway SQLite file):
```bash
python -c "
from etl_framework.repository.database import init_db
init_db()
init_db()
print('ok: init_db is idempotent')
"
```
Expected: `ok: init_db is idempotent`, no errors (confirms `ensure_column` doesn't choke
on a column that already exists, run twice in a row).
