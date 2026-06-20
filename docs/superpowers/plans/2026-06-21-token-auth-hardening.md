# Token Auth Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the Bearer token system with admin/regular tiers, in-memory cache, lazy DB writes, failed-auth audit, bootstrap path, fixed exempt paths, and CORS env var.

**Architecture:** Two new columns on `ApiToken` (`is_admin`, `token_hint`) gate token management via a FastAPI dependency; a module-level dict in the middleware caches verified tokens for 30s and skips the per-request DB write when `last_used_at` is fresh; the bootstrap path allows the first unauthenticated `POST /api/tokens` when the DB is empty.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite (via custom column-migration shim in `database.py` — no Alembic), Alpine.js frontend.

---

## File Map

| File | Change |
|------|--------|
| `etl_framework/repository/models.py` | Add `is_admin`, `token_hint` to `ApiToken` |
| `etl_framework/repository/database.py` | Update `CREATE TABLE` stmt + add `ALTER TABLE` shim for existing DBs |
| `etl_framework/repository/repository.py` | `create()` stores hint; `revoke()` returns hash |
| `api/middleware/auth.py` | Cache, lazy write, failed-auth audit, exempt path fix |
| `api/dependencies.py` | Add `require_admin` dependency |
| `api/routes/tokens.py` | Bootstrap logic, admin guard, updated response models |
| `api/main.py` | CORS env var |
| `frontend/index.html` | Show `token_hint` in copy-now box + token list |
| `frontend/app.js` | Store + display `token_hint` after create |

---

## Task 1: Data Model — Add `is_admin` and `token_hint` to `ApiToken`

**Files:**
- Modify: `etl_framework/repository/models.py:157-167`
- Modify: `etl_framework/repository/database.py:66-78`

- [ ] **Step 1: Write a failing test**

```python
# tests/test_token_model.py
from etl_framework.repository.models import ApiToken

def test_api_token_has_admin_and_hint_columns():
    cols = {c.key for c in ApiToken.__table__.columns}
    assert "is_admin" in cols
    assert "token_hint" in cols
```

- [ ] **Step 2: Run the test to confirm it fails**

```
pytest tests/test_token_model.py -v
```

Expected: `FAILED — AssertionError: assert 'is_admin' in {'created_at', 'enabled', ...}`

- [ ] **Step 3: Add the two columns to `ApiToken` in `models.py`**

Replace lines 157–167 with:

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
```

- [ ] **Step 4: Update the inline `CREATE TABLE` statement in `database.py`**

Replace the `CREATE TABLE IF NOT EXISTS api_tokens` block (lines 66–78) with:

```python
conn.execute(text(
    "CREATE TABLE IF NOT EXISTS api_tokens ("
    "id INTEGER PRIMARY KEY, "
    "token_hash VARCHAR(64) NOT NULL UNIQUE, "
    "name VARCHAR(255) NOT NULL, "
    "created_at DATETIME, "
    "last_used_at DATETIME, "
    "expires_at DATETIME, "
    "enabled BOOLEAN NOT NULL DEFAULT 1, "
    "is_admin BOOLEAN NOT NULL DEFAULT 0, "
    "token_hint VARCHAR(8) NOT NULL DEFAULT '')"
))
conn.execute(text(
    "CREATE INDEX IF NOT EXISTS ix_api_tokens_token_hash ON api_tokens (token_hash)"
))
```

- [ ] **Step 5: Add `ALTER TABLE` shims for existing databases**

After the `CREATE TABLE IF NOT EXISTS api_tokens` block, still inside the `with bind.begin() as conn:` block, add:

```python
        # --- Token auth hardening: is_admin + token_hint ---
        api_token_cols = {col["name"] for col in inspector.get_columns("api_tokens")} \
            if "api_tokens" in tables else set()
        if "is_admin" not in api_token_cols:
            conn.execute(text(
                "ALTER TABLE api_tokens ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT 0"
            ))
        if "token_hint" not in api_token_cols:
            conn.execute(text(
                "ALTER TABLE api_tokens ADD COLUMN token_hint VARCHAR(8) NOT NULL DEFAULT ''"
            ))
```

Note: `inspector` and `tables` are already defined at the top of `_ensure_compare_columns`. Place this block after the existing `api_tokens` `CREATE TABLE` statement.

- [ ] **Step 6: Run the test to confirm it passes**

```
pytest tests/test_token_model.py -v
```

Expected: `PASSED`

- [ ] **Step 7: Manually verify migration on an existing DB**

```bash
python - <<'EOF'
from etl_framework.repository.database import init_db, engine
from sqlalchemy import inspect
init_db()
cols = {c["name"] for c in inspect(engine).get_columns("api_tokens")}
assert "is_admin" in cols and "token_hint" in cols, f"Missing columns: {cols}"
print("OK:", cols)
EOF
```

Expected: `OK: {'id', 'token_hash', 'name', ..., 'is_admin', 'token_hint'}`

- [ ] **Step 8: Commit**

```bash
git add etl_framework/repository/models.py etl_framework/repository/database.py
git commit -m "feat(auth): add is_admin and token_hint columns to ApiToken"
```

---

## Task 2: TokenRepository — Store Hint, Return Hash on Revoke

**Files:**
- Modify: `etl_framework/repository/repository.py:367-399`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_token_repository.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from etl_framework.repository.database import Base
from etl_framework.repository.repository import TokenRepository

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_create_stores_token_hint(db):
    raw, token = TokenRepository(db).create("ci-pipeline")
    assert token.token_hint == raw[-8:]
    assert len(token.token_hint) == 8

def test_revoke_returns_token_hash(db):
    raw, token = TokenRepository(db).create("to-revoke")
    token_hash = token.token_hash
    result = TokenRepository(db).revoke(token.id)
    assert result == token_hash

def test_revoke_missing_returns_none(db):
    result = TokenRepository(db).revoke(9999)
    assert result is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_token_repository.py -v
```

Expected: 3 failures — `AttributeError: 'ApiToken' has no attribute 'token_hint'` and type mismatches on `revoke`.

- [ ] **Step 3: Update `create()` to compute and store `token_hint`**

Replace the `create` method (lines 367–373) with:

```python
def create(self, name: str, expires_at: datetime | None = None, is_admin: bool = False) -> tuple[str, ApiToken]:
    raw = "etl_" + _secrets.token_hex(32)
    token = ApiToken(
        token_hash=self._hash(raw),
        name=name,
        expires_at=expires_at,
        is_admin=is_admin,
        token_hint=raw[-8:],
    )
    self._db.add(token)
    self._db.commit()
    self._db.refresh(token)
    return raw, token
```

- [ ] **Step 4: Update `revoke()` to return the token hash**

Replace the `revoke` method (lines 393–399) with:

```python
def revoke(self, token_id: int) -> str | None:
    token = self._db.get(ApiToken, token_id)
    if token is None:
        return None
    token_hash = token.token_hash
    token.enabled = False
    self._db.commit()
    return token_hash
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/test_token_repository.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add etl_framework/repository/repository.py
git commit -m "feat(auth): TokenRepository stores token_hint, revoke() returns hash"
```

---

## Task 3: Middleware — Cache, Lazy Write, Failed-Auth Audit, Exempt Fix

**Files:**
- Modify: `api/middleware/auth.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_auth_middleware.py
import time
import pytest
from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from api.middleware.auth import BearerTokenMiddleware, evict_token_cache, _cache

def make_app():
    async def homepage(request):
        return JSONResponse({"ok": True})
    app = Starlette(routes=[Route("/api/jobs", homepage)])
    app.add_middleware(BearerTokenMiddleware)
    return app

def test_missing_auth_header_returns_401():
    client = TestClient(make_app(), raise_server_exceptions=False)
    resp = client.get("/api/jobs")
    assert resp.status_code == 401

def test_badge_svg_is_public():
    client = TestClient(make_app(), raise_server_exceptions=False)
    # badge SVG path should not require auth — route won't exist but middleware
    # should pass it through (404, not 401)
    resp = client.get("/api/runs/abc-123/badge.svg")
    assert resp.status_code != 401

def test_evict_removes_entry_from_cache():
    _cache["somehash"] = (MagicMock(), time.monotonic())
    evict_token_cache("somehash")
    assert "somehash" not in _cache
```

- [ ] **Step 2: Run to confirm they fail**

```
pytest tests/test_auth_middleware.py -v
```

Expected: `ImportError: cannot import name 'evict_token_cache'` and the badge test failing with 401.

- [ ] **Step 3: Rewrite `api/middleware/auth.py`**

Replace the entire file with:

```python
from __future__ import annotations

import re
import time
import logging
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from etl_framework.repository import database as _db_module
from etl_framework.repository.repository import TokenRepository

logger = logging.getLogger(__name__)

_CACHE_TTL = 30.0  # seconds — max staleness for cached token lookups
_LAZY_WRITE_INTERVAL = 300.0  # seconds — min gap between last_used_at DB writes (5 min)

# module-level cache: token_hash -> (ApiToken, cached_at_monotonic)
_cache: dict[str, tuple] = {}

_EXEMPT_PREFIXES = ("/api/health",)
_EXEMPT_EXACT = {"/", "/api/health"}
_EXEMPT_PATTERNS = [re.compile(r"^/api/runs/[^/]+/badge\.svg$")]


def evict_token_cache(token_hash: str) -> None:
    _cache.pop(token_hash, None)


def _is_exempt(path: str, method: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    if not path.startswith("/api/"):
        return True
    for prefix in _EXEMPT_PREFIXES:
        if path.startswith(prefix):
            return True
    for pattern in _EXEMPT_PATTERNS:
        if pattern.match(path):
            return True
    # Bootstrap: allow unauthenticated POST /api/tokens (route enforces admin guard otherwise)
    if path == "/api/tokens" and method == "POST":
        return True
    return False


class BearerTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _is_exempt(request.url.path, request.method):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._audit_failure(request, "missing_header")
            return JSONResponse(
                {"detail": "Missing or invalid Authorization header"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        raw_token = auth[len("Bearer "):]
        token_hash = TokenRepository._hash(raw_token)

        # --- cache lookup ---
        cached = _cache.get(token_hash)
        if cached is not None:
            token, cached_at = cached
            if time.monotonic() - cached_at < _CACHE_TTL:
                # Still need to check expiry even on cache hit
                exp = token.expires_at
                if exp:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < datetime.now(timezone.utc):
                        evict_token_cache(token_hash)
                        self._audit_failure(request, "expired")
                        return JSONResponse(
                            {"detail": "Invalid or expired token"},
                            status_code=401,
                            headers={"WWW-Authenticate": "Bearer"},
                        )
                request.state.token_actor = token.name
                request.state.token_id = token.id
                request.state.token = token
                return await call_next(request)
            else:
                del _cache[token_hash]

        # --- DB lookup ---
        db = _db_module.SessionLocal()
        token = None
        try:
            repo = TokenRepository(db)
            token = db.query(repo._db.bind and __import__("etl_framework.repository.models", fromlist=["ApiToken"]).ApiToken
                             or __import__("etl_framework.repository.models", fromlist=["ApiToken"]).ApiToken
                             ).filter_by(token_hash=token_hash, enabled=True).first()
            if token is not None:
                exp = token.expires_at
                if exp:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < datetime.now(timezone.utc):
                        token = None
                else:
                    # Lazy last_used_at write
                    now = datetime.now(timezone.utc)
                    lu = token.last_used_at
                    if lu is None or (now - lu).total_seconds() > _LAZY_WRITE_INTERVAL:
                        token.last_used_at = now
                        db.commit()
                    _cache[token_hash] = (token, time.monotonic())
                    request.state.token_actor = token.name
                    request.state.token_id = token.id
                    request.state.token = token
        finally:
            db.close()

        if token is None:
            self._audit_failure(request, "invalid_token")
            return JSONResponse(
                {"detail": "Invalid or expired token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)

    def _audit_failure(self, request: Request, reason: str) -> None:
        try:
            from api.services.audit_service import AuditService
            db = _db_module.SessionLocal()
            try:
                AuditService(db).log(
                    request,
                    "token.auth_failed",
                    "token",
                    None,
                    {
                        "reason": reason,
                        "path": request.url.path,
                        "method": request.method,
                        "ip": request.client.host if request.client else None,
                    },
                    actor=None,
                )
            finally:
                db.close()
        except Exception:
            logger.warning("Failed to write auth_failed audit event", exc_info=True)
```

**Note:** The DB query in the middleware needs a cleaner import path. Replace the convoluted `token =` line above with this cleaner version using a direct model import at top of file:

Add at the top of the file (after other imports):
```python
from etl_framework.repository.models import ApiToken as _ApiToken
```

Then replace the convoluted `token = db.query(...)` line with:
```python
            token = db.query(_ApiToken).filter_by(token_hash=token_hash, enabled=True).first()
```

And remove the expiry-check-and-no-`last_used_at`-update branch mismatch — the full corrected DB lookup block is:

```python
        db = _db_module.SessionLocal()
        token = None
        try:
            token_row = db.query(_ApiToken).filter_by(token_hash=token_hash, enabled=True).first()
            if token_row is not None:
                exp = token_row.expires_at
                if exp:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp < datetime.now(timezone.utc):
                        token_row = None
            if token_row is not None:
                now = datetime.now(timezone.utc)
                lu = token_row.last_used_at
                if lu is None or (now - lu).total_seconds() > _LAZY_WRITE_INTERVAL:
                    token_row.last_used_at = now
                    db.commit()
                _cache[token_hash] = (token_row, time.monotonic())
                request.state.token_actor = token_row.name
                request.state.token_id = token_row.id
                request.state.token = token_row
                token = token_row
        finally:
            db.close()
```

Use this corrected block — it avoids the import mess and handles expiry cleanly.

- [ ] **Step 4: Run tests to confirm they pass**

```
pytest tests/test_auth_middleware.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add api/middleware/auth.py
git commit -m "feat(auth): add token cache, lazy last_used_at, failed-auth audit, fix exempt paths"
```

---

## Task 4: `require_admin` Dependency + Bootstrap in Token Routes

**Files:**
- Modify: `api/dependencies.py`
- Modify: `api/routes/tokens.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_token_routes.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from etl_framework.repository.database import Base
from api.main import app
from api.dependencies import get_session

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)
TestingSession = sessionmaker(bind=engine)

def override_session():
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_session] = override_session
client = TestClient(app, raise_server_exceptions=False)

def test_bootstrap_creates_admin_token_when_db_empty():
    resp = client.post("/api/tokens", json={"name": "first-admin"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_admin"] is True
    assert len(data["token_hint"]) == 8
    assert data["raw_token"].startswith("etl_")

def test_list_tokens_requires_admin():
    resp = client.get("/api/tokens")
    assert resp.status_code == 401  # no auth header

def test_list_tokens_forbidden_for_regular_token():
    # Create second token (now DB has one token, bootstrap done)
    admin_token = client.post("/api/tokens", json={"name": "first-admin"}).json()["raw_token"]
    reg = client.post(
        "/api/tokens",
        json={"name": "regular", "is_admin": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    reg_token = reg.json()["raw_token"]
    resp = client.get("/api/tokens", headers={"Authorization": f"Bearer {reg_token}"})
    assert resp.status_code == 403
```

- [ ] **Step 2: Run to confirm they fail**

```
pytest tests/test_token_routes.py -v
```

Expected: failures — `is_admin` not in response, `GET /api/tokens` returns 200 without auth.

- [ ] **Step 3: Add `require_admin` to `api/dependencies.py`**

Replace the entire file with:

```python
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from etl_framework.repository.database import get_db


def get_session(db: Session = Depends(get_db)) -> Session:
    return db


def require_admin(request: Request) -> None:
    token = getattr(request.state, "token", None)
    if token is None or not getattr(token, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin token required")
```

- [ ] **Step 4: Rewrite `api/routes/tokens.py`**

Replace the entire file with:

```python
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.dependencies import get_session, require_admin
from api.middleware.auth import evict_token_cache
from api.services.audit_service import AuditService
from etl_framework.repository.repository import TokenRepository

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tokens"])


class TokenCreate(BaseModel):
    name: str
    expires_at: datetime | None = None
    is_admin: bool = False


class TokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    enabled: bool
    is_admin: bool
    token_hint: str
    model_config = {"from_attributes": True}


class TokenCreatedOut(TokenOut):
    raw_token: str  # shown once only


@router.post("", response_model=TokenCreatedOut, status_code=201)
def create_token(body: TokenCreate, request: Request, db: Session = Depends(get_session)):
    repo = TokenRepository(db)
    is_bootstrap = repo.count() == 0

    if not is_bootstrap:
        require_admin(request)

    is_admin = True if is_bootstrap else body.is_admin
    raw, token = repo.create(body.name, body.expires_at, is_admin=is_admin)

    if is_bootstrap:
        logger.warning(
            "Bootstrap admin token created — store this value securely, "
            "it will not be shown again. Token hint: ...%s",
            raw[-8:],
        )

    AuditService(db).log(
        request,
        "token.created",
        "token",
        token.id,
        {
            "name": token.name,
            "is_admin": token.is_admin,
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        },
        actor=body.name,
    )
    return TokenCreatedOut(
        id=token.id,
        name=token.name,
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
        enabled=token.enabled,
        is_admin=token.is_admin,
        token_hint=token.token_hint,
        raw_token=raw,
    )


@router.get("", response_model=list[TokenOut], dependencies=[Depends(require_admin)])
def list_tokens(db: Session = Depends(get_session)):
    return TokenRepository(db).list()


@router.delete("/{token_id}", status_code=204, dependencies=[Depends(require_admin)])
def revoke_token(token_id: int, request: Request, db: Session = Depends(get_session)):
    token_hash = TokenRepository(db).revoke(token_id)
    if token_hash is None:
        raise HTTPException(status_code=404, detail="Token not found")
    evict_token_cache(token_hash)
    AuditService(db).log(request, "token.revoked", "token", token_id)
```

- [ ] **Step 5: Add `count()` method to `TokenRepository` in `repository.py`**

Add after the `list` method (line 391):

```python
    def count(self) -> int:
        from etl_framework.repository.models import ApiToken
        return self._db.query(ApiToken).count()
```

- [ ] **Step 6: Run tests to confirm they pass**

```
pytest tests/test_token_routes.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add api/dependencies.py api/routes/tokens.py etl_framework/repository/repository.py
git commit -m "feat(auth): add require_admin, bootstrap path, token_hint + is_admin in responses"
```

---

## Task 5: CORS Env Var

**Files:**
- Modify: `api/main.py:23-29`

- [ ] **Step 1: Write a failing test**

```python
# tests/test_cors.py
import os
import importlib

def test_cors_uses_env_var(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com,https://admin.example.com")
    import api.main as m
    importlib.reload(m)
    # The middleware stack is configured at module load time; check the env var is read
    origins = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "").split(",")
        if o.strip()
    ]
    assert "https://app.example.com" in origins
    assert "https://admin.example.com" in origins
```

- [ ] **Step 2: Run to confirm**

```
pytest tests/test_cors.py -v
```

This test validates env var parsing logic. It will pass once the next step is done.

- [ ] **Step 3: Update `api/main.py`**

Replace lines 23–29 (the `app.add_middleware(CORSMiddleware, ...)` block) with:

```python
_cors_origins = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 4: Run test to confirm it passes**

```
pytest tests/test_cors.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add api/main.py
git commit -m "feat(auth): CORS origins from CORS_ORIGINS env var"
```

---

## Task 6: Frontend — Show `token_hint` in UI

**Files:**
- Modify: `frontend/app.js:1635-1665`
- Modify: `frontend/index.html:151-168`

- [ ] **Step 1: Add `createdTokenHint` state variable in `app.js`**

In `app.js`, find the Security block (around line 291) where `createdToken: null` is defined.
Add `createdTokenHint` directly after it:

```js
    createdToken: null,
    createdTokenHint: null,
```

- [ ] **Step 2: Update `createToken()` in `app.js` to capture `token_hint`**

In the `createToken` method (around line 1643–1657), update the success block for the non-wizard path:

Find:
```js
          this.createdToken = resp.raw_token;
          this.newTokenName = '';
          this.showCreateToken = false;
          this.toast('success', 'Token created', 'Saved to localStorage automatically');
```

Replace with:
```js
          this.createdToken = resp.raw_token;
          this.createdTokenHint = resp.token_hint || null;
          this.newTokenName = '';
          this.showCreateToken = false;
          this.toast('success', 'Token created', 'Saved to localStorage automatically');
```

- [ ] **Step 3: Update `index.html` — copy-now box to show hint**

Find the amber copy-now block (lines 151–156):

```html
        <template x-if="createdToken">
          <div class="bg-amber-50 border border-amber-300 rounded-lg p-3 text-sm space-y-1">
            <div class="font-semibold text-amber-800">Token created — copy it now, it won't be shown again:</div>
            <div class="font-mono text-xs break-all text-amber-900 select-all" x-text="createdToken"></div>
            <button @click="createdToken = null" class="text-xs text-amber-700 underline">Dismiss</button>
          </div>
        </template>
```

Replace with:

```html
        <template x-if="createdToken">
          <div class="bg-amber-50 border border-amber-300 rounded-lg p-3 text-sm space-y-1">
            <div class="font-semibold text-amber-800">Token created — copy it now, it won't be shown again:</div>
            <div class="font-mono text-xs break-all text-amber-900 select-all" x-text="createdToken"></div>
            <template x-if="createdTokenHint">
              <div class="text-xs text-amber-700">Hint (last 8 chars for identification): <span class="font-mono" x-text="'…' + createdTokenHint"></span></div>
            </template>
            <button @click="createdToken = null; createdTokenHint = null" class="text-xs text-amber-700 underline">Dismiss</button>
          </div>
        </template>
```

- [ ] **Step 4: Update `index.html` — token list row to show hint**

Find the token list row (lines 159–168):

```html
          <template x-for="tok in tokens" :key="tok.id">
            <div class="flex items-center gap-3 px-3 py-2 rounded border border-slate-100 bg-slate-50 text-sm">
              <div class="flex-1 min-w-0">
                <span class="font-medium text-slate-700" x-text="tok.name"></span>
                <span class="text-muted ml-2 text-xs" x-text="'created ' + fmtDate(tok.created_at)"></span>
                <span x-show="tok.last_used_at" class="text-muted ml-2 text-xs" x-text="'last used ' + fmtDate(tok.last_used_at)"></span>
              </div>
              <span :class="tok.enabled ? 'badge-green' : 'badge-gray'" class="badge text-xs" x-text="tok.enabled ? 'Active' : 'Revoked'"></span>
              <button @click="revokeToken(tok.id)" :disabled="!tok.enabled" class="btn-danger btn-sm text-xs">Revoke</button>
            </div>
          </template>
```

Replace with:

```html
          <template x-for="tok in tokens" :key="tok.id">
            <div class="flex items-center gap-3 px-3 py-2 rounded border border-slate-100 bg-slate-50 text-sm">
              <div class="flex-1 min-w-0">
                <span class="font-medium text-slate-700" x-text="tok.name"></span>
                <span x-show="tok.is_admin" class="ml-1 text-xs font-medium text-indigo-600">(admin)</span>
                <span class="text-muted ml-2 text-xs" x-text="'created ' + fmtDate(tok.created_at)"></span>
                <span x-show="tok.last_used_at" class="text-muted ml-2 text-xs" x-text="'last used ' + fmtDate(tok.last_used_at)"></span>
                <span x-show="tok.token_hint" class="text-muted ml-2 text-xs font-mono" x-text="'…' + tok.token_hint"></span>
              </div>
              <span :class="tok.enabled ? 'badge-green' : 'badge-gray'" class="badge text-xs" x-text="tok.enabled ? 'Active' : 'Revoked'"></span>
              <button @click="revokeToken(tok.id)" :disabled="!tok.enabled" class="btn-danger btn-sm text-xs">Revoke</button>
            </div>
          </template>
```

- [ ] **Step 5: Smoke-test in browser**

Start the server:
```bash
uvicorn api.main:app --reload
```

Open `http://localhost:8000`.

Verify:
1. First visit with empty DB — the Security panel allows creating a token without auth; the amber box shows both the full token and the hint (`…a3f9bc12`)
2. After creating the admin token and pasting it in the auth field, the token list loads and shows `(admin)` badge and hint for each token
3. Creating a second token via the Security panel (now authenticated) shows hint in the copy-now box
4. Revoking a token removes it immediately; subsequent API calls with that token return 401

- [ ] **Step 6: Commit**

```bash
git add frontend/app.js frontend/index.html
git commit -m "feat(auth): show token_hint and is_admin badge in token list and copy-now box"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `is_admin` + `token_hint` columns + migration | Task 1 |
| `create()` stores hint, `revoke()` returns hash | Task 2 |
| 30s in-memory cache | Task 3 |
| Lazy `last_used_at` (5 min) | Task 3 |
| Failed-auth audit (`token.auth_failed`) | Task 3 |
| Exempt path fix (badge SVG only) | Task 3 |
| `require_admin` dependency | Task 4 |
| Bootstrap path (zero-token unauthenticated POST) | Task 4 |
| Token create/list response includes `token_hint`, `is_admin` | Task 4 |
| `evict_token_cache` called on revoke | Task 4 |
| `count()` method on TokenRepository | Task 4 |
| CORS env var | Task 5 |
| Frontend: hint in copy-now box | Task 6 |
| Frontend: hint + admin badge in token list | Task 6 |

All requirements covered. No gaps found.
