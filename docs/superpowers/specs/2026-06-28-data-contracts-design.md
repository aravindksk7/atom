# Data Contracts + Ownership Layer — Design Spec

**Date:** 2026-06-28
**Status:** Approved
**Context:** ETL Test Framework (`c:/atom`)

---

## Problem

When a run fails in production, finding the right owner and tracking resolution to closure is fully manual. Failures are caught but routing them to the right team, communicating SLA expectations, and confirming resolution are not supported by the framework.

## Goal

Add a Data Contracts system that:
- Identifies an owner for each dataset/pipeline
- Automatically opens a breach record when the source job fails
- Tracks SLA compliance with a countdown timer
- Auto-resolves when the source job passes again
- Escalates to a secondary target if the SLA is missed
- Exposes breach history so quality trends are measurable over time

---

## Decisions Made

| Question | Decision |
|---|---|
| Contract granularity | Separate entity (`/api/contracts`) — not embedded in jobs |
| Contract content | Descriptive — derived live from `source_job`'s DQ rules and schema |
| Breach enforcement | SLA timer + auto-resolve + escalation |
| UI placement | Dedicated **Contracts** tab in the top nav |

---

## Data Model

### Table: `contracts`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `name` | TEXT | UNIQUE NOT NULL | e.g. `payments_v1` |
| `version` | TEXT | NOT NULL | semantic, e.g. `1.0`, `1.1` |
| `source_job` | TEXT | FK → jobs.name | derives rules/schema from this job |
| `owner` | TEXT | NOT NULL | email or team name, breach notifications sent here |
| `sla_hours` | REAL | NOT NULL | breach SLA target in hours |
| `consumers` | TEXT | JSON list | team names that depend on this contract |
| `breach_severity` | TEXT | `error`/`warn`, DEFAULT `error` | severity of a breach event |
| `active` | INTEGER | DEFAULT 1 | soft-delete flag |
| `created_at` | DATETIME | NOT NULL | |
| `updated_at` | DATETIME | NOT NULL | |

### Table: `contract_versions`

Append-only log of version bumps — no rule snapshots needed since rules are always derived live from `source_job`.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `contract_id` | INTEGER | FK → contracts.id | |
| `version` | TEXT | NOT NULL | the version string at time of bump |
| `bump_type` | TEXT | `minor`/`major` | |
| `note` | TEXT | nullable | optional reason for the bump |
| `bumped_at` | DATETIME | NOT NULL | |

### Table: `contract_breaches`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | INTEGER | PK | |
| `contract_id` | INTEGER | FK → contracts.id | |
| `run_id` | TEXT | FK → runs.id | run that triggered the breach |
| `breach_type` | TEXT | NOT NULL | `dq_violation`, `sla_breach`, `schema_change` |
| `opened_at` | DATETIME | NOT NULL | |
| `resolved_at` | DATETIME | nullable | null while open |
| `resolution_run_id` | TEXT | FK → runs.id, nullable | run that auto-resolved the breach |
| `escalated` | INTEGER | DEFAULT 0 | 1 after `sla_hours` elapsed without resolution |
| `escalated_at` | DATETIME | nullable | |
| `duration_hours` | REAL | nullable | computed on resolve: `(resolved_at - opened_at)` in hours |

---

## API Routes

All routes require the existing `BearerTokenMiddleware`. Mounted at `/api/contracts`.

### Contract CRUD

```
GET    /api/contracts                        List all contracts with current status (OK/BREACHED/OVERDUE)
POST   /api/contracts                        Create a contract
GET    /api/contracts/{name}                 Get contract metadata + current status
PUT    /api/contracts/{name}                 Update owner, sla_hours, consumers, version
DELETE /api/contracts/{name}                 Soft-delete (sets active=false)
```

### Contract State and History

```
GET    /api/contracts/{name}/status          Returns: { status: "OK"|"BREACHED"|"OVERDUE", open_breach: {...} }
GET    /api/contracts/{name}/breaches        Full breach history with durations and met_sla flag
GET    /api/contracts/{name}/breaches/open   Currently open breaches only
```

### Derived Views (no extra storage)

```
GET    /api/contracts/{name}/rules           DQ rules pulled live from source_job
GET    /api/contracts/{name}/schema          Latest schema snapshot from source_job
```

### Version Management

```
POST   /api/contracts/{name}/version         Bump version (body: { bump: "minor"|"major" })
GET    /api/contracts/{name}/versions        Version history (immutable records per version)
```

### `POST /api/contracts` Request Body

```json
{
  "name": "payments_v1",
  "source_job": "payments_reconciliation",
  "owner": "data-platform@company.com",
  "sla_hours": 4,
  "consumers": ["finance-team", "reporting-team"],
  "breach_severity": "error",
  "version": "1.0"
}
```

---

## Breach Lifecycle

### Trigger: Post-Run Hook

After every run completes, the run executor calls a new `ContractBreachChecker` component. This is purely additive — no changes to the reconciliation engine or DQ evaluation.

### Opening a Breach

Fires when a job result is `FAILED` or `ERROR`:

1. Look up all active contracts where `source_job` matches the failed job name
2. For each contract, if no open breach exists → insert a `contract_breaches` row:
   - `breach_type` derived from failure reason:
     - DQ rule violation → `dq_violation`
     - Schema snapshot diff → `schema_change`
     - Freshness job failure → `sla_breach`
3. Fire `contract.breached` webhook event to `contract.owner`
4. Existing run result is **unchanged** — breach is a parallel record, not a run override

### Auto-Resolving a Breach

Fires when a job result is `PASSED`:

1. Find all open `contract_breaches` for contracts where `source_job` = this job
2. Set `resolved_at = now`, `resolution_run_id = current_run_id`
3. Compute `duration_hours = (resolved_at - opened_at)`
4. Fire `contract.resolved` webhook event to owner, including:
   - `duration_hours`
   - `met_sla` (bool: `duration_hours <= sla_hours`)

### Escalation (Background Task)

A scheduled task runs every 15 minutes (via existing APScheduler):

1. Query open breaches where `opened_at < now - sla_hours` and `escalated = false`
2. Set `escalated = true`, `escalated_at = now`
3. Fire `contract.escalated` webhook event — can target a separate escalation endpoint

### New Webhook Event Types

Added to the existing `notifications` system alongside existing event types:

| Event | Payload extras |
|---|---|
| `contract.breached` | `contract_name`, `source_job`, `run_id`, `breach_type`, `owner` |
| `contract.resolved` | + `duration_hours`, `met_sla` (bool) |
| `contract.escalated` | + `hours_overdue` |

---

## UI Integration

### New Contracts Tab

Added to the existing Alpine.js top nav alongside Config / Launch / Monitor / History / Reports / Compare.

**Contracts list panel (left):**
- One row per contract showing name + status chip (OK green / BREACHED red / OVERDUE amber)
- Clicking a row opens breach detail in the right panel

**Breach detail panel (right):**
- Owner, source job link, SLA value, time elapsed, time remaining
- Breach type and triggering run link
- Breach history table: date, duration, met SLA (✓/✗)

**Config tab addition:**
- Compact summary widget: `3 contracts · 1 BREACHED · 1 OVERDUE → View all`
- Inline form to create a new contract (name, source job dropdown, owner, SLA hours)

---

## Testing Strategy

### Unit Tests — `tests/unit/test_contracts.py`

- Contract CRUD: create, get, list, update, soft-delete
- Breach open: failed job → breach row inserted, correct `breach_type` inferred
- Auto-resolve: passing job → `resolved_at` set, `duration_hours` correct, `met_sla` computed
- Escalation: breach older than `sla_hours` → `escalated=true` + `escalated_at` set
- Derived rules: rules fetched live from `source_job` config, not stored on contract
- Version bump: creates new immutable record, previous versions still queryable
- Validation: non-existent `source_job` rejected at creation

### Integration Tests — `tests/integration/test_contracts_integration.py`

- Full lifecycle against in-memory SQLite:
  1. Create contract
  2. Trigger run that fails → assert breach `OPEN` + `contract.breached` webhook fired
  3. Trigger passing run → assert breach `RESOLVED`, `duration_hours` populated, `met_sla` correct
- Escalation scheduler tick → breach marked `OVERDUE` after `sla_hours`

### Property-Based Tests — `tests/property/test_contracts_property.py`

Using Hypothesis:
- `duration_hours >= 0` for any resolved breach
- `met_sla == (duration_hours <= sla_hours)` always
- A contract with a non-existent `source_job` always fails validation
- No open breach has `resolved_at` set

### Existing Test Extensions

- `tests/unit/test_run_executor.py` — post-run contract check hook coverage
- `tests/unit/test_notifier.py` — `contract.breached` / `contract.resolved` / `contract.escalated` events

---

## Out of Scope (deferred)

- Prescriptive contracts that own their own DQ rules independently of jobs
- Contract-to-contract dependencies
- Consumer subscription management (notify consumers on contract change)
- Contract export to external registries (Confluent, OpenAPI)
- Multi-version active contracts (only latest version active at a time)
