# Tool Secrets & Spec-from-Implementation Plan

## Summary

Add encrypted secrets storage to tools and make the implementation class the authoritative source for tool specification (config schema, secret schema, argument schema). The database stores registration and configuration data only — never specification.

## Design Decisions

1. **Repo-layer encryption** — decrypt on load, encrypt on save. `ToolDefinition.secrets` is always plaintext in memory.
2. **Separate `GET /tools/{name}/spec` endpoint** — keeps the main tool response lean.
3. **Empty defaults on `BaseTool`** — tools that need no config or secrets don't declare anything.
4. **Fernet encryption** via `cryptography` package. Per-tool key derived from `PBKDF2(MOIRA_SECRETS_KEY, salt=tool_name.encode())`.
5. **Plaintext fallback** — when `MOIRA_SECRETS_KEY` is not set, secrets stored as plaintext JSON with a startup warning.

## Database

### Migration 007

```sql
ALTER TABLE tools ADD COLUMN secrets TEXT NOT NULL DEFAULT '{}';
```

Schema version bumps to 7.

### `secrets` column format

When encrypted:
```json
{
  "api_key": "encrypted:GAhD...base64...",
  "subscription_key": "encrypted:Qm3x...base64..."
}
```

When plaintext (no `MOIRA_SECRETS_KEY`):
```json
{
  "api_key": "BSA-xxx"
}
```

## Tool Specification from Implementation

### `BaseTool` class attributes

```python
class BaseTool(ABC):
    config_schema: dict[str, Any] = {}    # JSON Schema for config values
    secret_schema: dict[str, Any] = {}    # JSON Schema for secret values

    @classmethod
    def get_spec(cls) -> dict:
        return {
            "config_schema": cls.config_schema,
            "secret_schema": cls.secret_schema,
        }
```

Subclasses override `config_schema` and `secret_schema` to declare what they need. Example for a web search tool:

```python
class WebSearchTool(BaseTool):
    config_schema = {
        "type": "object",
        "properties": {
            "base_url": {
                "type": "string",
                "description": "Search API base URL",
                "default": "https://api.search.brave.com/res/v1/web/search",
            },
        },
    }
    secret_schema = {
        "type": "object",
        "properties": {
            "api_key": {
                "type": "string",
                "description": "Brave Search API key",
            },
        },
        "required": ["api_key"],
    }
```

Tools without config or secrets (like `CalculatorTool`) use the empty defaults — no declaration needed.

## Encryption Module — `moira/tools/secrets.py`

Functions:

- `is_encryption_configured() -> bool` — checks `MOIRA_SECRETS_KEY` env var
- `get_master_key() -> str | None` — returns `MOIRA_SECRETS_KEY` value
- `encrypt_secrets(secrets: dict[str, str], tool_name: str) -> str` — encrypts each value with Fernet key derived via `PBKDF2(master_key, salt=tool_name.encode())`, returns JSON string
- `decrypt_secrets(raw: str, tool_name: str) -> dict[str, str]` — reverses encryption. If `MOIRA_SECRETS_KEY` is None, parses as plaintext JSON

Uses `cryptography.fernet.Fernet` with `cryptography.hazmat.primitives.kdf.pbkdf2.PBKDF2HMAC` for key derivation.

## `ToolDefinition` Changes

- Add `secrets: dict[str, str] = field(default_factory=dict)`
- `to_dict()` excludes `secrets`, adds `secret_keys: list[str]`
- No separate `to_dict_with_secrets()` — the unlock endpoint constructs its response from the decrypted definition directly

## `SqliteToolRepository` Changes

- `save_tool()` — if `tool.secrets` is non-empty, encrypt before writing to DB
- `_row_to_defn()` — decrypt the `secrets` column after reading from DB

## API Changes

### `GET /tools/{name}/spec` (new)

Resolves the implementation class, calls `get_spec()`, returns config and secret schemas. Returns `{config_schema: {}, secret_schema: {}}` for tools with no implementation or no declared schemas.

### `PATCH /tools/{name}` (updated)

Accepts optional `secrets` dict in the body. Encrypts before storage. Values are merged: new keys overwrite, passing `null` for a key deletes it.

### `GET /tools` and `GET /tools/{name}` (updated)

Response includes `secret_keys: ["api_key", ...]` instead of `secrets`. No secret values ever exposed.

### `POST /tools/{name}/unlock-secrets` (future)

Requires affirmative user action (lock/unlock button). Returns decrypted secret key names and values. UX design deferred — the endpoint is a placeholder for now.

## Frontend Changes

- **API client**: add `secret_keys: string[]` to `ToolInfo`, add `getToolSpec()` method
- **Tools store**: add `secret_keys` to `ToolDefinition`
- **ToolDetailView**: show "Secrets" section with key names, lock/unlock placeholder button, "Configuration Schema" from spec endpoint (deferred to later UX work)
- **Implementation info box**: no change (already shows config values)

## `service_setup.py` Changes

- At startup, log whether `MOIRA_SECRETS_KEY` is configured
- If not configured, log a warning that secrets will be stored as plaintext

## New Dependency

`cryptography` added to `pyproject.toml`

## Files to Create

| File | Description |
|------|-------------|
| `persistence/sqlite/migrations/007_secrets.sql` | Migration adding `secrets` column |
| `tools/secrets.py` | Encryption/decryption helpers |

## Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add `cryptography` dependency |
| `persistence/sqlite/schema.py` | Version bump to 7 |
| `tools/base.py` | `secrets` field on `ToolDefinition`, `config_schema`/`secret_schema`/`get_spec()` on `BaseTool`, `get_secret()` method |
| `persistence/sqlite/repos.py` | Encrypt on save, decrypt on load |
| `api/router.py` | New `/spec` endpoint, updated `PATCH` for secrets |
| `service_setup.py` | Log encryption status at startup |
| `frontend/src/api/client.ts` | `secret_keys` in `ToolInfo`, `getToolSpec()` |
| `frontend/src/stores/tools.ts` | `secret_keys` in `ToolDefinition` |
| `frontend/src/components/ToolDetailView.vue` | Secrets section with lock/unlock placeholder |

## Implementation Order

1. Add `cryptography` to `pyproject.toml`
2. Create `moira/tools/secrets.py`
3. Migration 007
4. Update `ToolDefinition` and `BaseTool`
5. Update `SqliteToolRepository`
6. Update `service_setup.py`
7. Add API endpoints
8. Update frontend (API client, store, detail view)
9. Add tests (encryption round-trip, spec endpoint, secret storage)
10. Full test suite, lint, typecheck
