# Custom Runtime Variables — Design

**Goal:** Let a user define named variables (date / text / number / alphanumeric) on the Config page, with a global default and an optional per-Config override. Jobs reference a variable as `{{var_name}}` anywhere in their `query` or `params`. At launch time the value is resolved once per run and substituted into every job — so a sequence of jobs pulling the same value (e.g. a report date) is edited in one place instead of N job definitions.

**Tech stack:** Python (FastAPI, Pydantic, SQLAlchemy), Alpine.js frontend, pytest / Playwright.

---

## 1. Data model

### 1.1 Global variable definitions — new table

```python
class CustomVariable(Base):
    __tablename__ = "custom_variables"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    var_type = Column(String(20), nullable=False)  # 'text' | 'number' | 'date' | 'alphanumeric'
    default_value = Column(Text, nullable=True)
    description = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
```

Added to `etl_framework/repository/models.py`, registered via `Base.metadata.create_all` (new table, so no `ensure_table` shim needed — `init_db()` creates it fresh on any DB that doesn't have it yet, same as any other new table added going forward).

`name` is restricted to `^[A-Za-z_][A-Za-z0-9_]*$` (a valid placeholder identifier) — enforced at the API layer, not the DB.

### 1.2 Per-Config overrides — reuse `config_json`

No new table. `SavedConfig.config_json` (already an untyped JSON blob holding `connections`, `api_endpoints`, etc.) gains an optional `variables` key:

```json
{"db_host": "...", "connections": {...}, "variables": {"run_date": "2026-09-08", "batch_id": "B17"}}
```

This is consistent with how `connections`/`api_endpoints` already live in the same blob, and needs zero migration. `EnvironmentConfig` (the Pydantic model used for connection-resolution validation) does not declare a `variables` field — Pydantic v2 ignores undeclared keys by default (no `extra="forbid"` on that model), so this key rides along harmlessly through `validate_config`/`resolve_connection` without needing a schema change there.

A global variable that gets deleted leaves any matching key in some config's `variables` blob inert — resolution (below) only iterates currently-defined globals, so a stray override key is simply never read. No cascade delete needed.

## 2. Validation per type

Enforced in the Pydantic schema for create/update, applied identically to a global `default_value` and to any per-Config override value:

| Type | Rule |
|---|---|
| `text` | any string |
| `alphanumeric` | `^[A-Za-z0-9]+$` — letters and digits only |
| `number` | `^-?\d+(\.\d+)?$` |
| `date` | literal `YYYY-MM-DD`, **or** a dynamic expression `today`, `today-N`, `today+N` (case-insensitive, N = integer days) |

A dynamic date expression is stored as-is (e.g. `"today-1"`) and only resolved to a concrete date at launch time (§4) — so a default of `today-1` means "yesterday" on every run without ever being edited.

## 3. API

New router `api/routes/variables.py`, mounted at `/api/variables`:

- `GET /api/variables` → `list[CustomVariableOut]`
- `POST /api/variables` (`CustomVariableCreate: name, var_type, default_value, description`) → `CustomVariableOut`, 201
- `PUT /api/variables/{id}` (`CustomVariableUpdate`, partial) → `CustomVariableOut`
- `DELETE /api/variables/{id}` → 204

Validation errors follow the existing `FrameworkErrorOut` shape used by `configs.py::validate_config`. Renaming a variable does **not** rename it inside existing `SavedConfig.config_json["variables"]` blobs — an override under the old name simply becomes inert, same as a delete. This is called out in the UI (see §5) rather than auto-migrated, to avoid silently rewriting every config's JSON on a rename.

Config CRUD (`api/routes/configs.py`) is otherwise unchanged — `variables` just rides through `config_data` like any other key already does.

## 4. Resolution & substitution

New module `api/services/variable_resolution.py`:

```python
def resolve_variables(db: Session, config_id: int | None, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """global default -> per-config override -> launch override, then evaluate
    dynamic date expressions ('today', 'today-N', 'today+N') to concrete ISO dates."""

def substitute_in_job(job: JobDefinition, variables: dict[str, str]) -> JobDefinition:
    """Returns a copy of job with {{name}} replaced in `query` and in every
    string leaf reachable inside `params` (dicts/lists walked recursively —
    covers bo_parameters, headers, query_params, etc.)."""
```

`resolve_variables` is called **once per run**, not once per job — critical so `today` (and any launch override) is stable across every step of a sequence, even one that straddles midnight. It's called at launch time (§4.1) and the resulting dict is stored on the run's `config_snapshot["variables"]`, both for audit/debugging (visible on the run detail view) and so background workers (which build their own `RunExecutor` per step — see `_run_dag_step` in `api/services/run_executor.py`) all read the same already-resolved values instead of re-resolving.

Substitution is wired into `RunExecutor._build_jobs_index` (`api/services/run_executor.py:328`): after each `JobDefinition` is loaded, `substitute_in_job` is applied using `self._config_snapshot.get("variables", {})` before the job reaches `_build_case`/execution. An unresolved placeholder (typo'd name) is left verbatim rather than raising — it'll surface as a query/param that doesn't do what's intended, visible in the job's own failure, not swallowed silently by the substitution step. (A follow-up could add a preflight lint that lists any `{{...}}` left over across the resolved job set, but that's out of scope here.)

### 4.1 Launch-time override

`JobSelectionLaunchRequest` and `SequenceLaunchRequest` (`api/schemas.py`) each gain:

```python
variable_overrides: dict[str, str] = Field(default_factory=dict)
```

`launch_selection`/`launch_sequence` (`api/routes/selections.py`, `api/routes/sequences.py`) call `resolve_variables(db, trigger.config_id, body.variable_overrides)` right where `config_snapshot` is built, and store the result at `config_snapshot["variables"]`. Overrides here are one-off — never written back to the Config's stored `variables`.

## 5. Frontend

### 5.1 Config tab — Custom Variables panel

New panel (`frontend/features/config.js` + a new partial), sibling to the existing Configs list:

```
Custom Variables
  run_date   [date]          default: 2026-09-08
  batch_id   [alphanumeric]  default: (none)
  [+ Add variable]
```

Add/edit is a small inline form (name, type select, default value — date type gets a native date input *plus* a text fallback for `today±N`, following the same dual-input pattern already used elsewhere for BO job params). Delete asks for confirmation and warns if the name is currently referenced by any saved job's `query`/`params` (best-effort substring scan across `this.jobs`, client-side, informational only — not a hard block, since a job could reference a variable that gets recreated later).

### 5.2 Config edit modal — Variable Overrides section

Inside `editConfig`/`openNewConfigModal` (`frontend/features/config.js`), a new `configModal.variables: {name: value}` populated from `config_data.variables || {}`, rendered as one row per currently-defined global variable with an optional override input (blank = inherit). `_configDataFromModal` adds a `variables` key mirroring the `connections`/`api_endpoints` pattern already there (only non-blank overrides are included).

### 5.3 Launch modals

The job/sequence launch flow (`frontend/features/launch.js`) gets an optional "Variable overrides" block (same blank-means-default convention), wired into the existing launch payload alongside `source_env`/`target_env`/`config_id`.

### 5.4 CLI

`etl_framework/cli/app.py::run` gains a repeatable option:

```python
var: list[str] = typer.Option([], "--var", help="Override a variable for this run only, NAME=VALUE (repeatable)")
```

Parsed into `variable_overrides` on the launch payload, alongside the existing `ci_context` construction.

## 6. Error handling

- Invalid `var_type`/value on create or update → 422 with `FrameworkErrorOut`, same shape as config validation errors.
- Duplicate `name` on create → 409 (mirrors `ApiToken`'s existing duplicate-name handling pattern).
- Malformed `--var` (`app.py`) → `typer.BadParameter`, matching the existing `--output`/`--target-type` validation style.
- A `today±N` expression that fails to parse at resolution time (shouldn't happen if validated on save, but data can predate a validation tightening) falls back to leaving the literal string as the value — same "don't blow up the run over one variable" stance as the unresolved-placeholder case above.

## 7. Testing

- `tests/unit/test_variable_resolution.py`: precedence (global → override → launch-override), `today`/`today±N` resolution, `substitute_in_job` over `query` and nested `params` (dict + list of dicts).
- `tests/unit/test_variables_routes.py`: CRUD, validation per type, duplicate-name 409.
- `tests/unit/test_run_executor_variables.py`: a job whose `query` contains `{{var}}` gets it substituted before the case runs, including a nested-`params` case; `tests/unit/test_run_trigger_variables.py` confirms the sequence-level resolve-once behavior via a call-count assertion (`resolve_variables` invoked exactly once per launch, covering both the selection and sequence launch paths).

## 8. Out of scope (explicitly deferred)

- Renaming a variable auto-migrating existing per-Config override keys.
- A preflight lint surfacing unresolved `{{...}}` placeholders before a run starts.
- Variable types beyond the four requested (no boolean/enum/list type).
- A Playwright e2e test exercising the full UI → launch → substitution path. Coverage for this feature turned out to be strong enough at the route/unit level (variable CRUD, config-save validation, resolve-once-per-run, substitution into `query`/`params`, both launch paths) that a browser-driven e2e test was judged not worth its maintenance cost during implementation; descoped rather than added.
