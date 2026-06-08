# System Settings Infrastructure

## Status: Phase 1 Complete

## Overview

Build a persistent, layered settings system that stores system-level configuration
in the database, replaces config-file-only budget settings with runtime-mutable
values, and provides a settings UI in the frontend.

## Layered Settings Model

Settings follow a precedence chain: conversation > project > user > system.
Higher-precedence layers override lower ones. Not every setting is configurable at
every layer. The system layer is always present and serves as the default.

| Scope          | scope_id            | Description                                                                                  |
|----------------|---------------------|----------------------------------------------------------------------------------------------|
| `system`       | `"_system"`         | Global defaults. Set by admin/config. Always present.                                        |
| `user`         | `<user_id>`         | Per-user overrides. Applied across all conversations for that user.                          |
| `project`      | `<project_id>`      | Per-project overrides. Applied to all conversations within a project.                        |
| `conversation` | `<conversation_id>` | Per-conversation overrides. Highest precedence. Set by user for a specific research session. |

## Database

### Table: `settings`

```sql
CREATE TABLE settings (
    scope    TEXT NOT NULL
             CHECK(scope IN ('system', 'user', 'project', 'conversation')),
    scope_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,
    PRIMARY KEY (scope, scope_id, key)
);
```

- `scope_id` is NOT NULL with no default — callers must always provide it explicitly
- System settings: `scope='system'`, `scope_id='_system'` (convention)
- User settings (future): `scope='user'`, `scope_id='<user_id>'`
- Project settings (future): `scope='project'`, `scope_id='<project_id>'`
- Conversation settings (future): `scope='conversation'`, `scope_id='<conversation_id>'`

### Design decision: single table vs. multiple tables

**Considered:** Separate tables per scope layer (`system_settings`, `user_settings`,
`conversation_settings`, `project_settings`).

**Chosen:** Single `settings` table with `scope` + `scope_id` columns.

**Justification:**

The layered resolution query — "find the first value across conversation → project →
user → system" — is the core read operation. With one table it's a single query:

```sql
SELECT * FROM settings
WHERE key = ?
  AND ((scope='conversation' AND scope_id=?) OR ...)
ORDER BY CASE scope
  WHEN 'conversation' THEN 0
  WHEN 'user' THEN 1
  WHEN 'system' THEN 2
END
LIMIT 1
```

With multiple tables, that becomes N queries or a UNION across tables.

The tradeoff: `scope_id` cannot have foreign keys to multiple parent tables
simultaneously. The service layer controls all writes and validates scope+scope_id
combinations before persisting, with a `CHECK` constraint on `scope` providing
DB-level integrity.

Additional factors:
- Adding a new layer is zero-migration — just write rows with a new scope value
- Settings data is small and read-heavy; no partitioning benefit from separate tables
- One repo class, one set of CRUD, one migration

### Initial seed data (system scope)

Keys are dot-delimited. Seeded from `config.py` defaults at startup if not present.
Types are defined in the service's `SETTING_DEFINITIONS` registry, not in the DB.

| Key | Default Value |
|-----|---------------|
| `budget.default_limit` | `50` |
| `budget.cost.planning` | `2` |
| `budget.cost.tool_discovery` | `1` |
| `budget.cost.tool_selection` | `2` |
| `budget.cost.research_execution` | `5` |
| `budget.cost.compression` | `1` |
| `budget.cost.draft_synthesis` | `3` |
| `budget.cost.verification` | `4` |
| `budget.cost.report_generation` | `3` |

## Key Naming Convention

Roughly:
```
<area>.<subarea>.<detail>
```

- `budget.default_limit`
- `budget.cost.<step_name>`
- `inference.temperature.<purpose>`
- `research.max_verification_retries`

exceptions permitted where appropriate

## Backend

### Data Model (`persistence/interfaces.py`)

```python
@dataclass
class SettingEntry:
    key: str
    value: str
    scope: str       # no default — callers must provide explicitly
    scope_id: str    # no default — callers must provide explicitly
```

### Repository Interface (`persistence/interfaces.py`)

```python
class SystemSettingsRepository(ABC):
    async def get(key, scope, scope_id) -> SettingEntry | None
    async def get_prefix(prefix, scope, scope_id) -> list[SettingEntry]
    async def set(entry: SettingEntry) -> None
    async def set_batch(entries: list[SettingEntry]) -> None
```

### Repository Implementation (`persistence/sqlite/repos.py`)

`SqliteSystemSettingsRepository(SystemSettingsRepository)` follows existing conventions:
- Constructor takes `db_path: str`
- `_connect()` opens fresh `sqlite3.connect()` with WAL + foreign keys
- `try/finally` with `conn.close()` on every method
- `set` and `set_batch` use `INSERT ... ON CONFLICT(scope, scope_id, key) DO UPDATE`
- Registered as `"system_settings_repository"` in service locator

### Service (`services/settings/settings_service.py`)

```python
class SettingsService:
    def __init__(self, repo: SystemSettingsRepository, config: MoiraConfig):
        self._repo = repo
        self._config = config

    async def seed_defaults() -> None
    async def get(key, scopes=None) -> ResolvedSetting | None
    async def get_prefix(prefix, scope="system", scope_id="_system") -> list[SettingEntry]
    async def set(key, value, scope="system", scope_id="_system") -> None
    async def set_batch(entries: list[SettingEntry]) -> None
```

Type information is not stored in the database. It lives in a key definitions
registry — a map of known keys to their type, default value, description, and
validation constraints.

### Key Definitions (`services/settings/definitions.py`)

A dedicated module of pure data declarations. The service imports it for validation
and seeding; the API imports it for response enrichment; the frontend fetches it
via an endpoint for dynamic UI rendering.

```python
@dataclass
class SettingDefinition:
    key: str
    type: str          # "string" | "integer" | "float" | "boolean"
    default: str
    label: str
    description: str
    group: str         # "budget", "inference", "research" — for UI grouping
    constraints: dict  # JSON Schema fragment for structural validation
```

```python
SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    "budget.default_limit": SettingDefinition(
        key="budget.default_limit", type="integer", default="50",
        label="Default Budget Limit",
        description="Default budget allocated to each research run.",
        group="budget",
        constraints={"type": "integer", "minimum": 1},
    ),
    "budget.cost.planning": SettingDefinition(
        key="budget.cost.planning", type="integer", default="2",
        label="Planning Cost Weight",
        description="Cost deducted when the planning step executes.",
        group="budget",
        constraints={"type": "integer", "minimum": 0, "maximum": 100},
    ),
    ...
}
```

### Two-Layer Validation

Writes go through two validation layers before persisting:

**Layer 1: Structural validation (JSON Schema)**

Each definition carries a `constraints` field containing a JSON Schema fragment.
The service validates the parsed value against this schema using `jsonschema`
(Python) or `ajv` (TypeScript). This handles type, range, enum, and format
constraints that are always true regardless of system state.

```python
import jsonschema

defn = SETTING_DEFINITIONS[key]
jsonschema.validate(instance=defn.parse(value), schema=defn.constraints)
```

Static enum example (valid theme choices):
```python
constraints={"type": "string", "enum": ["light", "dark", "auto"]}
```

The same `constraints` object travels through the API to the frontend, so input
validation is shared without duplicated logic.

**Layer 2: Semantic validation (service code)**

Some values require runtime context — e.g., a model name must be currently
discoverable, an endpoint must be configured. These can't be expressed as static
schemas. The service's `set()` method handles this as plain Python logic after
structural validation passes. No string dispatch, no indirection — just code that
knows which keys need runtime checks.

```python
# SettingsService.set() — full write path
async def set(self, key, value, scope, scope_id):
    defn = SETTING_DEFINITIONS.get(key)
    if defn is None:
        raise UnknownSettingError(key)

    # Layer 1: structural
    parsed = defn.parse(value)
    jsonschema.validate(instance=parsed, schema=defn.constraints)

    # Layer 2: semantic (if applicable)
    await self._validate_semantic(key, parsed)

    await self._repo.set(SettingEntry(key=key, value=value, scope=scope, scope_id=scope_id))

async def _validate_semantic(self, key, parsed_value):
    if key in ("inference.intelligence_model", "inference.task_model"):
        available = await self._model_registry.list_models()
        if parsed_value not in available:
            raise InvalidSettingValueError(f"Model '{parsed_value}' not available")
```

### Resolved Setting

```python
@dataclass
class ResolvedSetting:
    key: str
    value: str
    type: str          # from SETTING_DEFINITIONS, not from DB
    scope: str         # which scope resolved the value
    scope_id: str
```

### Typed Access for Consuming Code

The service provides a typed accessor so consuming code (e.g., `budget.py`) works
with native Python types, not raw strings:

```python
async def get_typed(self, key, scopes=None) -> int | float | bool | str | None:
    resolved = await self.get(key, scopes)
    if resolved is None:
        return None
    return SETTING_DEFINITIONS[key].parse(resolved.value)
```

```python
# budget.py
cost = await settings_service.get_typed("budget.cost.planning", scopes=[("system", "_system")])
# Returns int(2), not str("2")
```

### Service Business Logic

- **Scope chain resolution**: `get()` accepts an ordered list of `(scope, scope_id)` tuples,
  walks them in order, returns first hit with `resolved_scope` indicating which layer matched
- **Config fallback**: if no DB row found for any scope, falls back to `config.py` defaults
- **Seeding**: `seed_defaults()` called at startup, only inserts keys not already in the DB
- **Write queue**: all writes routed through `AsyncWriteQueue`

### API Endpoints

Five endpoints. No bulk-read-all or bulk-update-all.

**GET `/api/settings/definitions`** — return all known setting definitions.

Response: `{ definitions: [{ key, type, default, label, description, group, constraints }, ...] }`.

The frontend uses this to dynamically render controls (number inputs with min/max,
toggles for booleans, select dropdowns for enums, grouped by `group`). The
`constraints` field is a JSON Schema fragment that the frontend can use for
client-side validation with `ajv`.

**GET `/api/settings/{key}`** — read a specific key's resolved value.

Query params:
- `scope` (optional, repeatable) — ordered list of scopes to check, e.g.
  `?scope=conversation:conv-123&scope=user:default&scope=system`. Returns the
  first hit. Defaults to `system` only.
- Response: `{ key, value, type, label, description, constraints, scope, scope_id,
  resolved_scope }` or 404. All metadata enriched from the definitions registry.

**PUT `/api/settings/{key}`** — set a specific key for a specific scope.

Body: `{ value, scope?, scope_id? }`. Defaults to `scope="system"`,
`scope_id="_system"`. Two-layer validation: structural (JSON Schema) then
semantic (custom validator if applicable). Writes through `AsyncWriteQueue`.

**GET `/api/settings`** — list all keys matching a prefix for a scope.

Query params:
- `prefix` (optional) — dot-delimited prefix filter, e.g. `budget.cost`.
- `scope` (optional) — defaults to `system`.
- `scope_id` (optional) — defaults to `"_system"`.
- Response: `{ settings: [{ key, value, type, label, description, constraints,
  scope, scope_id }, ...] }`. All metadata enriched from definitions registry.

**PUT `/api/settings`** — batch set multiple keys for a single scope.

Body: `{ scope?, scope_id?, settings: [{ key, value }, ...] }`.
Two-layer validation on all entries before writing any. Writes through
`AsyncWriteQueue`.

**DELETE `/api/settings`** — reset keys to definition defaults.

Query params:
- `keys` (optional) — comma-separated list of keys to reset. Omit to reset all.
- `scope` (optional) — defaults to `system`.
- `scope_id` (optional) — defaults to `"_system"`.

Resets the specified keys at the given scope to their `SETTING_DEFINITIONS`
defaults. Used by system administrators to undo runtime changes.

### Registration (`service_setup.py`)

- `SqliteSystemSettingsRepository` instantiated with `db_path`
- `SettingsService` instantiated with repo + config
- Registered as `"system_settings_repository"` and `"settings_service"`
- `seed_defaults()` called during startup after migrations

### Router helpers (`router.py`)

```python
def _settings_service() -> SettingsService:
    return cast(SettingsService, service_provider("settings_service"))
```

### Config Bridge

- At startup, `seed_defaults()` inserts missing keys from `config.py` defaults
- Runtime reads go through `SettingsService.get()`; config file values serve as
  the final fallback for keys not yet in the DB

### Resolving Settings for Sync Code

Some consumers (e.g., `budget.py`) are synchronous and cannot call the async
`SettingsService`. Rather than introducing caching or state duplication, settings
are resolved **once at the async boundary** and passed down as plain data.

The run manager is already async. It already fetches per-run state like
`budget_limit`. Settings are resolved the same way — once, at run start — and
stored in the run state as plain dicts. Sync code reads from the state dict,
not from the settings service.

```python
# RunManager (async) — at run start
cost_weights = await settings_service.get_typed_prefix("budget.cost", scope="system", scope_id="_system")
# cost_weights = {"planning": 2, "tool_discovery": 1, ...}

state["cost_weights"] = cost_weights
```

Budget.py's functions change from taking `config: MoiraConfig` to taking a plain
dict of cost weights. The functions remain sync, remain pure, and become
decoupled from both `MoiraConfig` and the settings service.

```python
# Before
def get_node_cost(config: MoiraConfig, node_name: str) -> int:
    cw = config.budget.cost_weights
    return {"planning": cw.planning, ...}.get(node_name, 0)

# After
def get_node_cost(cost_weights: dict[str, int], node_name: str) -> int:
    return cost_weights.get(node_name, 0)
```

Callers already have the run state available. They pass `state["cost_weights"]`
instead of `config`. This is the same pattern already used for `budget_limit`.

This approach means:
- No cache, no frozen snapshots, no state duplication
- Settings are resolved once per run at the async boundary — the right granularity,
  since settings shouldn't change mid-run
- Sync code stays sync, pure, and testable
- Budget.py becomes decoupled from `MoiraConfig` entirely

### Seed and Reset Behavior

`seed_defaults()` is called at startup and **only inserts keys that are not already
present in the DB**. It does not overwrite existing values. This preserves runtime
changes made through the API or UI across restarts.

This means the YAML config file cannot be used to reset values to defaults. A
dedicated `reset_defaults()` method is provided for system administrators:

```python
# SettingsService
async def reset_defaults(self, keys: list[str] | None = None, scope="system", scope_id="_system"):
    """Reset specified keys (or all keys) to their definition defaults.
    Deletes the DB rows so that subsequent reads fall through to config defaults
    and the next seed_defaults() will re-insert them."""
    target_keys = keys or list(SETTING_DEFINITIONS.keys())
    for key in target_keys:
        defn = SETTING_DEFINITIONS[key]
        await self._repo.delete(key, scope, scope_id)
    await self._repo.set_batch([
        SettingEntry(key=k, value=SETTING_DEFINITIONS[k].default, scope=scope, scope_id=scope_id)
        for k in target_keys
    ])
```

Exposed via `DELETE /api/settings` with optional `keys` filter and `scope`/`scope_id`
params. Resets only the specified scope layer (defaults to system).

## Frontend

### SettingsSystem.vue

Replace the placeholder with grouped sections (grouped by applicable area, not
by workflow step):

- **Budget** — default limit (number input), cost weights table (8 rows with
  step name + number input)
- **Inference** — model assignments (move from current location)
- **Research** — future: max retries, thinking toggles
- **Advanced** — future: anything that doesn't fit above

### Settings API Client

- `getDefinitions()` — fetches all setting definitions from `GET /api/settings/definitions`
- `getSettingsByPrefix(prefix)` — fetches settings for a group via `GET /api/settings?prefix=...`
- `setSetting(key, value)` — writes a single setting via `PUT /api/settings/{key}`
- `setSettingsBatch(entries)` — batch write via `PUT /api/settings`

### Loading Initial State

On mount, the component fetches definitions and then loads settings per group
using prefix queries. Each section (budget, inference, etc.) loads independently:

```
GET /api/settings/definitions          → definitions (for control rendering)
GET /api/settings?prefix=budget        → budget section values
GET /api/settings?prefix=inference     → inference section values (future)
```

The definitions response drives control type rendering (number input, toggle,
select). The settings response provides current values.

### Validation and Error Handling

Client-side validation uses `ajv` with the `constraints` JSON Schema from the
definitions endpoint. Validation runs before the API call:

1. User changes a value
2. Validate locally against the definition's `constraints` via `ajv`
3. If invalid, show an error indicator next to the control immediately (don't send)
4. If valid, send `PUT /api/settings/{key}`
5. On success, clear any error state
6. On server rejection (e.g., semantic validation failure), show the server error
   message next to the control, then reload the value from the server to restore
   the actual persisted state

This means every control has three visual states: normal, local-validation-error,
and server-error. The local catch avoids unnecessary network round-trips for
obviously wrong values (non-numeric in an integer field, out of range, etc.).

### Reactivity

Settings are reactive — when a value is successfully saved, the local store
updates immediately. Other UI elements that display derived values (e.g., a
budget summary) update in real-time.

This adds minimal complexity because settings are resolved once per run at the
async boundary (run manager). There's no need to push changes to active runs.
A setting change in the UI takes effect on the next research run, not the
current one. The only reactive surface is the settings page itself.

## Phased Rollout

### Phase 1: Infrastructure (this iteration)
- Migration 012: create `settings` table
- `SettingEntry` dataclass in `interfaces.py`
- `SystemSettingsRepository` ABC in `interfaces.py`
- `SqliteSystemSettingsRepository` in `repos.py`
- `SettingDefinition` dataclass in `services/settings/definitions.py`
- `SETTING_DEFINITIONS` registry with budget keys
- `SettingsService` in `services/settings/settings_service.py`
- Six API endpoints: definitions, single GET/PUT, prefix GET, batch PUT, reset DELETE
- Config bridge: `seed_defaults()` at startup, budget.py reads from service
- Frontend: budget section in SettingsSystem.vue

### Phase 2: Layer expansion (future)
- User settings layer
- `SettingsService.get()` with full scope chain resolution
- Per-user overrides in UI

### Phase 3: Project and conversation layers (future)
- Project settings (when projects exist)
- Conversation-level overrides
- Settings context in run manager

## Deferred Critiques

Issues identified during design review that are deferred or intentionally ignored
for Phase 1. Revisit if they become problematic.

**`get_prefix()` enrichment location is undefined.**
The service method returns raw `SettingEntry` objects but the API response includes
type/label/description/constraints enriched from definitions. The plan doesn't specify
whether enrichment happens in the service (returning a richer type) or in the router
(ad hoc enrichment). Decide during implementation.

**Definitions endpoint returns a flat list.**
`GET /api/settings/definitions` returns all definitions as a flat array. The frontend
groups them by the `group` field for UI rendering. A pre-grouped response structure
could reduce frontend work, but flat is simpler and sufficient for the initial setting
count (~10 keys).

**Scope query parameter format is underspecified.**
`GET /api/settings/{key}?scope=conversation:conv-123&scope=user:default&scope=system`
needs precise parsing rules. The format `scope=<name>[:<id>]` with system scope defaulting
to `_system` when no colon is present should be documented explicitly during implementation.

**No migration strategy for retired settings keys.**
When a setting key is removed from `SETTING_DEFINITIONS` in a new version, its DB row
becomes orphaned — the service won't read it, but it persists. A startup cleanup step
could drop rows for unknown keys. Defer until key churn actually happens.
