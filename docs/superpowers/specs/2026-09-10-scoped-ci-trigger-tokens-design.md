# Scoped CI Trigger Tokens — Design Spec

**Date:** 2026-09-10
**Status:** Approved (brainstorming session)
**Sub-project:** 3 of 4 (remaining GitLab CI/CD uplift), following
`2026-09-09-sequence-cicd-launch-parity-design.md` (1) and
`2026-09-10-gitlab-native-ci-signals-design.md` (2). Final sub-project: a CI runs
dashboard in the frontend.

## Problem

`docs/superpowers/plans/2026-07-18-atom-cli-cicd.md` deferred "CI-facing API (scoped
trigger tokens, artifacts store, webhooks)" as a follow-up sub-project. Of those three,
webhooks are fully built (`api/routes/notifications.py`/`api/services/notifier.py` —
event-driven, SSRF-protected, HMAC-signed) and artifact export already exists (the `atom`
CLI's `--junit-out`/`--json-out`/`--html-out`, and `report --format junit|json|csv|html`).
Scoped trigger tokens do not: today `ApiToken` has exactly two privilege levels —
`is_admin` or not — and every non-admin token can hit every non-admin route. A CI token
pasted into a GitLab CI/CD variable has, if leaked, the same access as a logged-in human:
it can read and edit `Config` rows (which hold database/BO connection credentials), list
and revoke other tokens' effects indirectly through settings, edit jobs, and more — none
of which a pipeline that only needs to launch one selection and read its result should be
able to touch.

## Solution

Add a third token privilege level, `role = "ci_trigger"`, enforced at the single existing
authentication choke point (`BearerTokenMiddleware`) against a small, explicit allowlist
of exactly the routes the `atom` CLI and the CI scripts (`run-atom-target.sh`,
`post-gitlab-status.sh`) already call. No target-pinning (one token restricted to a
specific selection/sequence) — role-level scoping only, so one CI-trigger token can launch
any selection or sequence and read any run's result, but nothing outside that surface.

---

## 1. Data Model

### `ApiToken` (existing table — additive column)

| Column | Type | Notes |
|---|---|---|
| `role` | String(20) | not null, default `"full"`. `"full"` = today's behavior (subject to `is_admin` for admin-only routes); `"ci_trigger"` = restricted to the allowlist in §3. |

Migration via the existing lightweight helper in `etl_framework/repository/database.py`
(the same pattern already used for every other additive column in this codebase):

```python
ensure_column(conn, "api_tokens", "role",
              "ALTER TABLE api_tokens ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'full'")
```

Every existing token gets `role = "full"` — zero behavior change for anything issued
before this change.

`role` and `is_admin` are orthogonal columns but mutually exclusive in practice: a token
cannot be both `is_admin=True` and `role="ci_trigger"` (enforced at creation, §2). A
`ci_trigger` token's `is_admin` is always `False`.

---

## 2. API — `api/routes/tokens.py`

`TokenCreate` gains one field:

```python
class TokenCreate(BaseModel):
    name: str
    expires_at: datetime | None = None
    is_admin: bool = False
    role: Literal["full", "ci_trigger"] = "full"
```

`create_token` validates `not (body.is_admin and body.role == "ci_trigger")` → **422** if
violated ("a CI-trigger token cannot also be an admin token"). `TokenRepository.create`
gains a `role: str = "full"` parameter, stored verbatim on the new `ApiToken` row.

`TokenOut`/`TokenCreatedOut` gain `role: str` so callers (including the frontend) can
display it.

`rotate_token` carries `old.role` through to the replacement token, exactly as it already
does for `old.is_admin`.

`TokenPatch` is **not** extended with `role` — scope is immutable after creation, matching
`is_admin`'s existing behavior (there is no way to patch `is_admin` either). Changing a
token's role means revoking it and creating a new one, which is also the safer operational
default (no silent widening of an existing, possibly-already-distributed token).

---

## 3. Enforcement — `api/middleware/auth.py`

`BearerTokenMiddleware` is the single place every non-exempt request already passes
through to resolve its token (both the cache-hit and DB-lookup branches converge on
`request.state.token` before calling `call_next`). This is the only choke point extended —
no per-route `dependencies=[...]` annotations to remember to add on every new route,
mirroring how `require_admin` is instead applied selectively where broadening is needed,
but inverted: here we're narrowing, and narrowing must be enforced centrally or it will be
forgotten on some future route.

```python
_CI_TRIGGER_ALLOWED: list[tuple[str, re.Pattern]] = [
    ("GET",  re.compile(r"^/api/selections$")),
    ("GET",  re.compile(r"^/api/selections/\d+$")),
    ("POST", re.compile(r"^/api/selections/\d+/launch$")),
    ("GET",  re.compile(r"^/api/sequences$")),
    ("GET",  re.compile(r"^/api/sequences/\d+$")),
    ("POST", re.compile(r"^/api/sequences/\d+/launch$")),
    ("GET",  re.compile(r"^/api/runs/[^/]+$")),
    ("GET",  re.compile(r"^/api/runs/[^/]+/status$")),
    ("GET",  re.compile(r"^/api/runs/[^/]+/junit$")),
    ("GET",  re.compile(r"^/api/runs/[^/]+/markdown-summary$")),
    ("GET",  re.compile(r"^/api/runs/[^/]+/report$")),
    ("GET",  re.compile(r"^/api/runs/[^/]+/export$")),
]


def _ci_trigger_denied(request: Request) -> bool:
    return not any(
        request.method == method and pattern.match(request.url.path)
        for method, pattern in _CI_TRIGGER_ALLOWED
    )
```

Every entry corresponds to a call the `atom` CLI or the CI scripts already make:
`_resolve_target` (selections/sequences list, for name lookup), `run`'s launch call,
`_wait_for_run`'s status poll, `_write_artifacts` (junit/report, plus the run-detail GET
for `--json-out`), `report` (junit/export/report/run-detail), and
`run-atom-target.sh`'s/`post-gitlab-status.sh`'s markdown-summary fetch. Deliberately
excluded: the bare `GET /api/runs` listing endpoint (backs the `atom runs` command) — no
CI launch/gate/report flow calls it, and a token scoped to "launch and read back your own
run" has no need to browse every run ever recorded. Nothing is added "just in case" — a
`ci_trigger` token that needs a new capability later gets a new allowlist entry then, not
a speculative one now.

After a `ci_trigger` token resolves successfully (both the cache-hit branch, currently
returning at what is today `api/middleware/auth.py:101`, and the DB-lookup branch, `:141`),
check `_ci_trigger_denied(request)`; if true, return the same `403` shape the codebase
already uses elsewhere, and audit-log it:

```python
if token.role == "ci_trigger" and _ci_trigger_denied(request):
    self._audit_failure(request, "ci_trigger_scope_denied")
    return JSONResponse({"detail": "This token is not permitted to call this endpoint"},
                         status_code=403)
```

`_audit_failure` already exists and writes a `token.auth_failed` event with `reason`,
`path`, `method`, `ip` — reused as-is with a new `reason` value, no new audit-service code.

---

## 4. CLI and CI scripts

No changes. This is the point of role-level (not target-pinned) scoping: a `ci_trigger`
token is a drop-in replacement for a `full` token in every `ATOM_API_TOKEN` variable
already in use by `run-atom-target.sh`/`post-gitlab-status.sh` and the `atom` CLI — the
allowlist was built by reading exactly what they call (§3).

---

## 5. Frontend

`frontend/partials/tab-config.html`'s existing Security → API Tokens panel has a token-role
`<select x-model="newTokenRole">` (currently `user`/`admin`, mapped to `is_admin` in
`frontend/features/config.js::createToken`). It gains a third option:

```html
<option value="ci_trigger">CI Trigger (launch + read-only)</option>
```

`createToken()`'s request body becomes:

```javascript
const body = {
  name,
  is_admin: this.newTokenRole === 'admin',
  role: this.newTokenRole === 'ci_trigger' ? 'ci_trigger' : 'full',
  expires_at: ...,
};
```

`createdTokenRole` (used in the post-creation confirmation banner) extends from its
current `admin`/`user` values to also show `ci_trigger`.

The CI/CD Integration modal's existing "STEP 1 — Create an API token" text
(`frontend/partials/tab-launch.html`) is updated from a generic "create one" to explicitly
recommend the CI Trigger role, since the whole point of building it is for CI users to
actually pick it over a full-access token.

---

## 6. Error Handling

| Situation | Behaviour |
|---|---|
| `TokenCreate` with `is_admin=True` and `role="ci_trigger"` | `422`, no token created. |
| A `ci_trigger` token calls an allowlisted route | Works exactly as a `full` token would. |
| A `ci_trigger` token calls anything else | `403`, audit-logged as `token.auth_failed` with `reason="ci_trigger_scope_denied"`. The pipeline sees this as any other API error — the `atom` CLI already maps non-401/404/4xx-in-general to `AtomAPIError` → exit 3. |
| A pre-existing token (`role` defaulted to `"full"` by the migration) | Unaffected — full access exactly as before. |
| A `ci_trigger` token expires or is revoked | Handled by existing expiry/revocation logic, unchanged — scope-checking only applies to tokens that already passed the existing auth checks. |

---

## 7. Testing

- **Unit — middleware:** every allowlisted `(method, path)` combination passes for a
  `ci_trigger` token; a representative sample of disallowed routes (`GET /api/configs`,
  `GET /api/jobs`, `GET /api/tokens`, `POST /api/schedules`, `DELETE /api/runs/{id}`)
  return `403`; a `full` and an `is_admin` token are unaffected by any of the above
  (regression coverage — the new check must never fire for `role != "ci_trigger"`).
- **Unit — token creation:** `is_admin=True` + `role="ci_trigger"` → `422`; `role`
  defaults to `"full"` when omitted; `TokenOut`/`TokenCreatedOut` include `role`.
- **Unit — rotation:** `rotate_token` on a `ci_trigger` token produces a replacement that
  is still `role="ci_trigger"`.
- **Integration:** mint a `ci_trigger` token via the API, use it to drive a full
  launch → poll → junit-fetch flow against a saved selection (reusing the same fixtures as
  the existing selections/sequences launch tests), and confirm it gets `403` on at least
  one clearly out-of-scope route in the same test.

---

## Out of Scope (this sub-project)

- Target-pinned tokens (one token restricted to a specific selection/sequence id) — the
  alternative considered and explicitly not chosen during brainstorming.
- Patching an existing token's `role` — revoke and recreate instead.
- Any change to webhooks/notifications or artifact export — both already complete.
- A CI runs dashboard in the frontend (sub-project 4, the last remaining one).
