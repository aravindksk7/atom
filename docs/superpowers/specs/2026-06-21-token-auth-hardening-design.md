# Token Auth Hardening Design

**Date:** 2026-06-21
**Status:** Approved
**Scope:** Harden the existing Bearer token system for CI/CD pipeline use

---

## Context

The app is an internal ETL reconciliation tool used by team leads and CI/CD pipelines
(GitHub Actions / GitLab CI). Tokens are stored as pipeline secrets and used for
automated job triggers, run exports, and status checks.

**Gaps identified in the current implementation:**

| Gap | Risk |
|-----|------|
| `/api/tokens` fully exempt from auth | Unauthenticated callers can list all tokens and revoke any token |
| DB write (`last_used_at`) on every request | Write pressure at CI burst rates |
| No in-memory token cache | Every API call hits the DB |
| No admin/regular token distinction | Any token can manage all other tokens |
| Badge SVG exemption covers all `/api/runs/` | Unintentionally public run endpoints |
| `allow_origins=["*"]` CORS | Any origin can make credentialed requests |
| Failed auth attempts not audited | Brute-force attempts invisible in logs |
| No way to identify tokens without full value | Hard to match CI secret to a DB row |

---

## Chosen Approach: Hardened Token System

Two-tier tokens (admin / regular), in-memory cache, lazy writes, bootstrap path,
strict CORS, failed-auth audit, and token hints for identification.

---

## 1. Data Model

**File:** `etl_framework/repository/models.py` — `ApiToken`

Two new columns, one DB migration:

```
ApiToken
  + is_admin    Boolean   NOT NULL DEFAULT FALSE
  + token_hint  String(8) NOT NULL
```

- `is_admin` — gates token management endpoints. Admin tokens can create, list,
  and revoke other tokens. Regular tokens cannot.
- `token_hint` — the last 8 characters of the raw token, stored at creation time.
  Safe to display; lets you match a CI pipeline secret to its DB row without
  exposing the full token value.

No other model changes. Existing `expires_at`, `enabled`, `last_used_at`, and
`name` fields are sufficient.

**Migration:** Add both columns with `ALTER TABLE api_tokens ADD COLUMN ...`.
Existing rows get `is_admin=FALSE`, `token_hint=''` (blank hint for pre-existing
tokens is acceptable — they were created before hints existed).

---

## 2. Auth Middleware

**File:** `api/middleware/auth.py`

### 2a. In-memory token cache

Module-level dict:

```python
_cache: dict[str, tuple[ApiToken, float]] = {}
_CACHE_TTL = 30.0  # seconds
```

Keyed by the SHA-256 hash of the raw token (same value stored in DB — no extra
computation). On each authenticated request:

1. Compute hash of incoming raw token.
2. Check `_cache`: if hit and age < 30s, use cached `ApiToken` — skip DB.
3. On miss: DB lookup as today; populate cache on success.
4. On `DELETE /api/tokens/{id}` (revoke): the route calls `TokenRepository.revoke()`
   which now returns the `token_hash` of the revoked row. The route then calls
   `evict_token_cache(token_hash)` — a module-level function exported from
   `api/middleware/auth.py` — to immediately remove the entry from `_cache`.
   Revocation takes effect on the next request, not after 30s TTL expiry.

**Staleness window:** A token that reaches its `expires_at` naturally may remain
valid for up to 30s if cached. Acceptable trade-off for this use case.

### 2b. Lazy `last_used_at`

Current behaviour writes to DB on every authenticated request. New rule:

> Only update `last_used_at` if the stored value is `None` **or** the stored value
> is more than 5 minutes before `now(UTC)`.

A CI pipeline making 100 requests/min causes at most 1 DB write per 5 minutes per
token, down from 100.

### 2c. Failed auth audit

On any 401 response (missing header, invalid token, expired token, disabled token),
call `AuditService` with:

```
event:    "token.auth_failed"
resource: "token"
resource_id: None
metadata: { ip, path, method, reason }
actor:    None
```

This makes brute-force and misconfigured CI pipeline attempts visible in the
existing audit log without adding new infrastructure.

### 2d. Exempt path fix

**Current (insecure):**
```python
_EXEMPT_PREFIXES = ("/api/health", "/api/tokens", "/api/runs/")
```

**New:**
```python
import re

_EXEMPT_PREFIXES = ("/api/health",)
_EXEMPT_EXACT    = {"/", "/api/health"}
_EXEMPT_PATTERNS = [re.compile(r"^/api/runs/[^/]+/badge\.svg$")]
```

`POST /api/tokens` is a special case: the middleware checks if `path == "/api/tokens"`
and `method == "POST"` — if so, it passes the request through without a token.
The route itself enforces the admin guard (or allows bootstrap if zero tokens exist).
`GET /api/tokens` and `DELETE /api/tokens/{id}` are not exempt — they require a
valid token from the middleware and admin status from the route guard.

The middleware evaluates patterns only after prefix/exact checks fail — negligible
overhead. All other `/api/runs/` endpoints now require a valid token.

---

## 3. Routes & Permissions

**Files:** `api/routes/tokens.py`, `api/dependencies.py`

### 3a. Admin guard dependency

New FastAPI dependency in `api/dependencies.py`:

```python
def require_admin(request: Request):
    token = getattr(request.state, "token", None)
    if token is None or not token.is_admin:
        raise HTTPException(status_code=403, detail="Admin token required")
```

Applied to:

| Endpoint | Before | After |
|---|---|---|
| `POST /api/tokens` | unauthenticated | admin only (except bootstrap — see 3b) |
| `GET /api/tokens` | unauthenticated | admin only |
| `DELETE /api/tokens/{id}` | unauthenticated | admin only |

### 3b. Bootstrap path

`POST /api/tokens` checks the token count before the admin guard runs:

1. If DB contains **zero tokens**: allow the call without any `Authorization` header,
   force `is_admin=True` on the created token, and log at WARNING level:
   `"Bootstrap admin token created — store this value securely, it will not be shown again"`.
2. If DB contains **≥1 token**: the standard admin guard applies.

This is the one-time first-deploy step. Operators run a single `curl` or use the
UI setup wizard to create the first admin token. All subsequent token management
requires that admin token (or another admin token created by it).

### 3c. Token create/list response

`TokenCreatedOut` adds `token_hint: str` (returned alongside `raw_token`).
`TokenOut` (list response) adds `token_hint: str`.
`raw_token` continues to be shown once only in `TokenCreatedOut`.

### 3d. TokenRepository changes

- `create()` computes `token_hint = raw[-8:]` and stores it.
- `revoke()` returns the `token_hash` of the revoked token (so the middleware can
  evict it from cache without a second DB query).

---

## 4. CORS Configuration

**File:** `api/main.py`

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

Production deployments set `CORS_ORIGINS=https://internal-tool.company.com` in
their Compose/Helm env. Local dev works out of the box with the default.

Cache TTL (30s) and lazy-write interval (5min) are constants in code, not env vars.
They are not deployment-specific values.

---

## 5. Frontend Changes

**File:** `frontend/app.js`

- The token setup wizard (`showAuthModal`) already handles the happy path.
- After the bootstrap create, the UI receives `token_hint` in the response —
  display it alongside the raw token in the "copy now" dialog so users know
  what to store in their CI secrets manager.
- Token list table gains a `Hint` column showing `…{token_hint}` for each row.
- No change to localStorage storage — the raw token is still stored under
  `etl_token`. This is acceptable for an internal tool; the token is already
  scoped to the domain.

---

## 6. Files Changed

| File | Change |
|------|--------|
| `etl_framework/repository/models.py` | Add `is_admin`, `token_hint` to `ApiToken` |
| `etl_framework/repository/repository.py` | `create()` stores hint; `revoke()` returns hash |
| `api/middleware/auth.py` | Cache, lazy write, failed-auth audit, exempt path fix |
| `api/dependencies.py` | Add `require_admin` dependency |
| `api/routes/tokens.py` | Apply admin guard + bootstrap logic; update response models |
| `api/routes/auth.py` | No change |
| `api/main.py` | CORS env var |
| `frontend/app.js` | Show `token_hint` in wizard + token list |
| `alembic/versions/XXXX_token_admin_hint.py` | Migration: add two columns |

---

## 7. What This Does Not Include

- Token scopes / resource-level permissions (YAGNI — two tiers is enough)
- JWT / short-lived access tokens (overkill for internal tool)
- Rate limiting on `/api/tokens` (can be added at the reverse-proxy layer)
- Token rotation endpoint (create new + revoke old is sufficient for now)
- IP allowlisting on tokens (not requested)
