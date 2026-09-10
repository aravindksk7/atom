# Authentication Guide

All API endpoints require a Bearer token unless noted. Tokens are managed via `/api/tokens`.

---

## Bootstrap (first run)

On a fresh install the database has no tokens. The first `POST /api/tokens` is unauthenticated and always creates an admin token.

```bash
curl -sS -X POST http://localhost:8000/api/tokens \
  -H "Content-Type: application/json" \
  -d '{"name": "admin"}'
```

Response (token shown **once only** — save it now):

```json
{
  "id": 1,
  "name": "admin",
  "is_admin": true,
  "enabled": true,
  "expires_at": null,
  "token_hint": "a1b2c3d4",
  "raw_token": "etl_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

Store `raw_token` in a password manager or secret vault immediately. It cannot be retrieved again.

After bootstrap, `POST /api/tokens` requires an admin `Authorization` header.

---

## Using a token

Pass the raw token as a Bearer header on every request:

```bash
curl http://localhost:8000/api/jobs \
  -H "Authorization: Bearer etl_xxxx..."
```

The Web UI prompts for the token on first load and stores it in `sessionStorage` (cleared when the browser tab closes).

---

## Token operations (admin only)

### Create a standard user token

```bash
curl -sS -X POST http://localhost:8000/api/tokens \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "alice",
    "is_admin": false,
    "expires_at": "2027-01-01T00:00:00Z"
  }'
```

| Field | Required | Notes |
|---|---|---|
| `name` | Yes | Label for the token — must be unique |
| `is_admin` | No | `false` by default |
| `role` | No | `full` (default) or `ci_trigger` — see [Token roles](#token-roles). Cannot be `ci_trigger` together with `is_admin: true`. |
| `expires_at` | No | ISO-8601 UTC. Capped at 2 years from now. Omit for no expiry. |

### List tokens

```bash
curl http://localhost:8000/api/tokens \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

Returns all tokens with `token_hint` (last 8 chars) — full raw token is never returned after creation.

### Update expiry or disable a token

```bash
# Extend expiry
curl -sS -X PATCH http://localhost:8000/api/tokens/2 \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"expires_at": "2027-06-01T00:00:00Z"}'

# Disable without deleting
curl -sS -X PATCH http://localhost:8000/api/tokens/2 \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

### Revoke a token

```bash
curl -sS -X DELETE http://localhost:8000/api/tokens/2 \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

Returns `204 No Content`. The token is invalidated immediately (cache evicted).

### Rotate a token

Issues a new token with the same name, role, and expiry, then revokes the old one atomically. Use during credential rotation so there is no gap in access.

```bash
curl -sS -X POST http://localhost:8000/api/tokens/2/rotate \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
```

Response is the same shape as token creation, including `raw_token`. The old token is invalid immediately.

### Verify your token

```bash
curl http://localhost:8000/api/auth/verify \
  -H "Authorization: Bearer <TOKEN>"
```

```json
{"ok": true, "actor": "alice", "token_id": 3, "is_admin": false}
```

---

## Token roles

| Role | `is_admin` | `role` | Permitted operations |
|---|---|---|---|
| Admin | `true` | `full` | All endpoints + token management (`POST/GET/PATCH/DELETE/rotate /api/tokens`) |
| Standard | `false` | `full` | All read + write endpoints except token management |
| CI Trigger | `false` | `ci_trigger` | Only: list/read Job Selections and Execution Sequences, launch either, and read/export a run's result (status, JUnit, markdown summary, HTML report, CSV export). Everything else — configs, jobs, other tokens, settings, schedules, the audit log — returns `403`, even though the token is otherwise valid. Enforced centrally in `BearerTokenMiddleware`, not per-route, so it can't be bypassed by a new endpoint someone forgets to lock down. |

A `ci_trigger` token is a drop-in replacement for a `full` (`is_admin: false`) token in any
`ATOM_API_TOKEN` CI/CD variable — the `atom` CLI and `scripts/ci/run-atom-target.sh` don't
need to know or care which kind they were given. Create one from the Web UI (Config →
Security → Add User Access → Role → **CI Trigger**) or via the API with `"role":
"ci_trigger"` on `POST /api/tokens`.

---

## CI/CD integration

Never hardcode tokens. Use the short-lived mint-use-revoke pattern:

1. Store a long-lived admin token in your CI secrets vault (`ATOM_ADMIN_TOKEN`).
2. At job start, mint a short-lived token (1 hour TTL).
3. Use that token for all API calls in the job.
4. In an `always`-run cleanup step, revoke the pipeline token by ID.

**Which role to mint** depends on what the job actually calls. A pipeline that launches a
saved Job Selection or Execution Sequence via `atom run` /
`scripts/ci/run-atom-target.sh` (see [docs/cli.md](cli.md)) only ever needs `GET
/api/selections`, `GET /api/sequences`, their `/launch` endpoints, and `GET
/api/runs/{id}/...` — mint `role: "ci_trigger"` for these; it has no way to read `Config`
rows (database/BO connection credentials) or manage other tokens. The included
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) predates this role and instead
resolves jobs directly (`GET /api/jobs`) and triggers an ad-hoc run
(`POST /api/runs` with a raw `job_sequence`) — neither of those two endpoints is on the
`ci_trigger` allowlist today, so that workflow still needs a `full` (`is_admin: false`)
token, not `ci_trigger`.

The included [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) implements this pattern end-to-end.

**Required GitHub configuration:**

| Type | Name | Value |
|---|---|---|
| Secret | `ATOM_ADMIN_TOKEN` | Raw value of a long-lived admin token |
| Variable | `ATOM_API_URL` | Base URL of the API, e.g. `https://atom.internal` |

---

## Security notes

- Tokens are stored as SHA-256 hashes; the raw value is never persisted.
- The in-memory cache has a 30-second TTL — revocation takes effect within 30 seconds.
- `expires_at` beyond 2 years from creation is silently clamped.
- All token events (created, updated, revoked, rotated, auth failures) are written to the audit log at `/api/audit`.
- The Web UI stores the token in `sessionStorage`, not `localStorage` — it clears when the browser tab closes.
