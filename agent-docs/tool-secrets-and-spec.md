# Credential Store & Spec-from-Implementation Plan

## Summary

Build a generic encrypted credential store as a separate database table and injectable service. Credentials are stored independently of any tool or other domain object. The store accepts arbitrary JSON-shaped secrets (API keys, username/password pairs, bearer tokens, and future types) and retrieves them by dot-qualified name. Encryption uses Fernet with per-credential HKDF key derivation. The spec-from-implementation system (tool classes declaring their config and secret schemas) is retained but tool integration with the credential store is deferred.

## Design Decisions

1. **Separate `credentials` table** — credentials live in their own table, not attached to tools or any other entity. The credential store is a standalone lockbox.
2. **Per-credential random salt** — each credential gets its own salt, stored alongside the encrypted data. Key derived via `HKDF(master_key, salt)`.
3. **Arbitrary JSON values, no fixed type enum** — the store accepts any JSON object as a credential value. The shape is defined by the value itself, not a database enum. Common shapes (API key, username/password, bearer token) have Python-side TypedDict definitions for convenience and validation, but the database imposes no schema on the encrypted blob.
4. **Dot-qualified names with reserved namespaces** — credentials are retrieved by name (e.g., `brave.api_key`, `jira.auth`). Names match `[a-zA-Z_][a-zA-Z0-9_.]*`. The namespaces `system.*`, `user.*`, and `tool.*` are reserved for future use and should not be used for user-defined credentials in this phase.
5. **No implicit plaintext storage** — when `MOIRA_SECRETS_KEY` is not set, the credential service refuses to operate unless `MOIRA_ALLOW_PLAINTEXT_SECRETS=true` is explicitly set. This prevents accidental plaintext storage in any environment. When plaintext mode is active, a startup warning is logged and the service functions without encryption.
6. **Injectable service** — `CredentialService` is constructed during `service_setup` and available via the DI container. Callers get/set credentials by name without knowledge of encryption.
7. **No tool coupling in this phase** — the credential store is generic. Wiring tools to look up their own secrets from the store is deferred to a later phase.
8. **Encryption versioning from day one** — an `encryption_version` column supports future algorithm migration without schema redesign.

## Database

### Migration 007

```sql
CREATE TABLE credentials (
    owner TEXT NOT NULL DEFAULT 'system',
    name TEXT NOT NULL,
    encrypted_data TEXT NOT NULL,
    salt TEXT NOT NULL,
    encryption_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (owner, name)
);
```

Schema version bumps to 7.

### Column details

- **`owner`** — identifies who owns the credential. Defaults to `'system'`. Part of the composite primary key with `name`. Reserved for future multi-user or multi-scope support. Current code always uses `'system'`.
- **`name`** — dot-qualified identifier (e.g., `brave.api_key`). Part of the composite primary key with `owner`.
- **`encrypted_data`** — Fernet-encrypted JSON string. The plaintext is an arbitrary JSON object representing the secret value.
- **`salt`** — base64-encoded random 16-byte value, generated once at credential creation time. Never regenerated on update.
- **`encryption_version`** — identifies the encryption algorithm and parameters used. Version 1 = HKDF-SHA256 + Fernet. Future versions may use different KDFs or ciphers.
- **`created_at`** / **`updated_at`** — timestamps. `created_at` is set once on insert; `updated_at` is refreshed on every update.

### Example credential values (plaintext, before encryption)

```json
{"key": "BSA-xxx"}
```

```json
{"username": "admin", "password": "s3cret"}
```

```json
{"token": "eyJhbGci..."}
```

```json
{"client_id": "abc", "client_secret": "def", "tenant_id": "xyz"}
```

The store does not impose a schema on these values. The calling code (or future tool-binding layer) is responsible for knowing what shape to expect.

## Encryption Module — `moira/tools/secrets.py`

> Despite the module path, this is a general-purpose encryption helper. The module may be relocated to `moira/security/` or `moira/crypto/` in a future phase if the working directory structure warrants it. For now it lives adjacent to the tools code since tools are the first anticipated consumer.

Functions:

- `is_encryption_configured() -> bool` — returns `True` if `MOIRA_SECRETS_KEY` is set
- `is_plaintext_allowed() -> bool` — returns `True` if `MOIRA_ALLOW_PLAINTEXT_SECRETS=true`
- `get_master_key() -> str | None` — returns `MOIRA_SECRETS_KEY` value
- `generate_salt() -> str` — returns base64-encoded 16 random bytes
- `derive_key(master_key: str, salt: str) -> bytes` — derives a 32-byte key via `HKDF(algorithm=SHA256, length=32, salt=base64.b64decode(salt), info=b"moira-credential-encryption")`
- `encrypt_value(plaintext: str, master_key: str, salt: str) -> str` — derives key, constructs Fernet, returns ciphertext
- `decrypt_value(ciphertext: str, master_key: str, salt: str) -> str` — reverses encryption

Uses `cryptography.fernet.Fernet` with `cryptography.hazmat.primitives.kdf.hkdf.HKDF`.

The HKDF output (32 bytes) is base64-url-encoded to produce the 44-byte key Fernet requires.

Rationale for HKDF over PBKDF2: `MOIRA_SECRETS_KEY` should be a high-entropy random secret (32+ bytes), not a human-typed password. HKDF is the correct KDF for high-entropy input — it provides key separation via salt without the unnecessary computational cost of PBKDF2's iteration count. If the master key is low-entropy (a password), it should be replaced with a proper random secret, not compensated for with a slow KDF.

## Credential Value Types — `moira/tools/credential_types.py`

TypedDict definitions for common credential shapes. These are convenience types for Python-side validation — the database stores arbitrary JSON and does not enforce these shapes.

```python
class ApiKeyValue(TypedDict):
    key: str

class UsernamePasswordValue(TypedDict):
    username: str
    password: str

class BearerTokenValue(TypedDict):
    token: str
```

`CredentialValue` is `dict[str, Any]` — the service accepts and returns arbitrary dictionaries. The TypedDicts above are for callers who want type-narrowing on known shapes.

Name validation: names must match `re.compile(r"^[a-zA-Z_][a-zA-Z0-9_.]*$")`.

Reserved namespaces (not enforced in code yet, but should not be used for user-defined credentials):
- `system.*` — for platform-level secrets in future phases
- `service.*` — for credentials shared across tools by upstream service (e.g., `service.brave.api_key`, `service.jira.auth`)
- `provider.*` — for LLM inference provider credentials (e.g., `provider.local.api_key`, `provider.openai.api_key`)
- `tool.*` — for tool-specific credential bindings (e.g., `tool.web_search.api_key`). Each tool's secrets live under `tool.<tool_name>.<secret_key>`.
- `user.*` — for user-scoped secrets in future phases

## Credential Service — `moira/tools/credential_service.py`

```python
class CredentialService:
    DEFAULT_OWNER = "system"

    def __init__(self, repo: CredentialRepository, master_key: str | None): ...

    async def get_credential(self, name: str, owner: str = DEFAULT_OWNER) -> dict[str, Any] | None
    async def store_credential(self, name: str, value: dict[str, Any], owner: str = DEFAULT_OWNER) -> None
    async def delete_credential(self, name: str, owner: str = DEFAULT_OWNER) -> bool
    async def list_credentials(self, owner: str = DEFAULT_OWNER) -> list[CredentialInfo]
```

- `get_credential` — reads from repo, decrypts, returns dict. Returns `None` if not found.
- `store_credential` — validates name format, generates salt on first creation (reuses existing salt on update), encrypts value, persists via repo using upsert.
- `delete_credential` — removes by owner+name. Returns `True` if deleted, `False` if not found.
- `list_credentials` — returns name, encryption_version, created_at, updated_at for credentials owned by `owner`. Never returns decrypted values.

`CredentialInfo` is a dataclass with `owner`, `name`, `encryption_version`, `created_at`, `updated_at` — no secret material.

### Encryption availability

On construction:
- If `master_key` is set → encryption enabled, normal operation.
- If `master_key` is None and `MOIRA_ALLOW_PLAINTEXT_SECRETS=true` → plaintext mode, startup warning logged.
- If `master_key` is None and plaintext not allowed → `CredentialService` raises `ConfigurationError` at construction time. The application should handle this by either disabling credential functionality or refusing to start.

### Salt handling

Salt is generated once at credential creation and never changed. On update, the existing salt is reused to re-encrypt the new value. Salt rotation provides no meaningful security benefit — its purpose is uniqueness between credentials, not forward secrecy. If the master key is compromised, all credentials should be rotated regardless of individual salts.

## Credential Repository — `moira/persistence/sqlite/repos.py`

New `SqliteCredentialRepository` class (or separate file if preferred):

- `get_by_name(owner: str, name: str) -> CredentialRow | None` — returns raw row (encrypted_data, salt, encryption_version, timestamps)
- `save(owner: str, name: str, encrypted_data: str, salt: str, encryption_version: int) -> None` — uses `INSERT ... ON CONFLICT(owner, name) DO UPDATE SET encrypted_data=..., salt=..., encryption_version=..., updated_at=datetime('now')` to preserve `created_at`.
- `delete(owner: str, name: str) -> bool`
- `list_all(owner: str | None = None) -> list[CredentialRow]` — excludes `encrypted_data`, returns owner + name + encryption_version + timestamps. If `owner` is None, returns all rows; otherwise filters by owner.

`CredentialRow` is a simple named tuple or dataclass matching the table columns.

## Spec-from-Implementation (Retained, No Tool Wiring)

The following changes to `BaseTool` are retained from the original plan. These allow tools to declare what secrets they *need* without coupling to how secrets are *stored*. The wiring between tool secret schemas and the credential store is deferred.

### `BaseTool` class attributes

```python
class BaseTool(ABC):
    config_schema: dict[str, Any] = {}
    secret_schema: dict[str, Any] = {}

    @classmethod
    def get_spec(cls) -> dict:
        return {
            "config_schema": cls.config_schema,
            "secret_schema": cls.secret_schema,
        }
```

Subclasses override `config_schema` and `secret_schema` to declare what they need. Example:

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

### `GET /tools/{name}/spec` endpoint (retained)

Resolves the implementation class, calls `get_spec()`, returns config and secret schemas. This is informational — it tells the UI what the tool expects. It does not read or write the credential store.

## API Changes

### `GET /api/credentials` (new)

Query params: optional `owner` (defaults to `system`).
Returns list of credentials for that owner (owner, name, encryption_version, created_at, updated_at). No secret values.

### `POST /api/credentials` (new)

Body: `{owner?: string, name: string, value: {...}}`
Validates name format. `owner` defaults to `"system"`. Stores encrypted credential.
Returns the stored credential info (no value).

### `GET /api/credentials/{name}` (new)

Query params: optional `owner` (defaults to `system`).
Returns credential info (owner, name, encryption_version, created_at, updated_at). No secret value.
Returns 404 if not found.

### `DELETE /api/credentials/{name}` (new)

Query params: optional `owner` (defaults to `system`).
Deletes credential. Returns 204 or 404.

### `GET /tools/{name}/spec` (new, from original plan)

Returns config_schema and secret_schema from the tool implementation class. No credential store interaction.

### `PATCH /tools/{name}` (no change from current)

No secrets-related changes in this phase. Tool secrets wiring is deferred.

## `service_setup.py` Changes

- Construct `SqliteCredentialRepository` during `init_services`
- Read `MOIRA_SECRETS_KEY` and `MOIRA_ALLOW_PLAINTEXT_SECRETS`
- Construct `CredentialService` with repo and master key
  - If no master key and plaintext not allowed: log error and skip credential service registration (or refuse startup depending on application policy)
  - If no master key but plaintext allowed: log warning, register service in plaintext mode
  - If master key present: normal operation, log that encryption is configured
- Register `CredentialService` in the DI container

## Frontend Changes

Minimal in this phase — no tool-specific credential UI.

- **API client**: add credential CRUD methods (`listCredentials`, `createCredential`, `deleteCredential`)
- **Credential store (Pinia)**: basic store for credential list
- **No views or components yet** — the credential management UI is deferred

## New Dependency

`cryptography` added to `pyproject.toml`

## Files to Create

| File | Description |
|------|-------------|
| `moira/persistence/sqlite/migrations/007_credentials.sql` | Migration creating `credentials` table |
| `moira/tools/secrets.py` | Encryption/decryption helpers (Fernet + HKDF) |
| `moira/tools/credential_types.py` | TypedDict definitions, name validation, reserved namespaces |
| `moira/tools/credential_service.py` | Injectable `CredentialService` |

## Files to Modify

| File | Change |
|------|--------|
| `pyproject.toml` | Add `cryptography` dependency |
| `moira/persistence/sqlite/schema.py` | Version bump to 7 |
| `moira/tools/base.py` | `config_schema`/`secret_schema`/`get_spec()` on `BaseTool` |
| `moira/persistence/sqlite/repos.py` | Add `SqliteCredentialRepository` |
| `moira/api/router.py` | New credential endpoints, new `/tools/{name}/spec` endpoint |
| `moira/service_setup.py` | Construct and register `CredentialService`, log encryption status |
| `frontend/src/api/client.ts` | Add credential CRUD methods |
| `README.md` | Add `MOIRA_SECRETS_KEY` generation instructions to setup section |

## Documentation

Add the following to `README.md` install/setup instructions:

```bash
openssl rand -base64 32
```

Generate a `MOIRA_SECRETS_KEY` by running the command above and placing the output in your `.env` file or environment. This is required for encrypted credential storage. For local development only, you may set `MOIRA_ALLOW_PLAINTEXT_SECRETS=true` instead, but this should never be used in production.

## Implementation Order

1. Add `cryptography` to `pyproject.toml`
2. Create `moira/tools/secrets.py` (encryption helpers with HKDF)
3. Create `moira/tools/credential_types.py` (types + validation)
4. Migration 007
5. Create `SqliteCredentialRepository` in `moira/persistence/sqlite/repos.py`
6. Create `moira/tools/credential_service.py`
7. Update `moira/tools/base.py` (spec-from-implementation)
8. Update `moira/service_setup.py` (DI registration with encryption availability checks)
9. Add API endpoints (credentials CRUD + tool spec)
10. Update frontend API client
11. Add tests (encryption round-trip, credential CRUD, name validation, plaintext mode with explicit opt-in, encryption_version persistence, upsert preserving created_at)
12. Full test suite, lint, typecheck

## Deferred to Later Phase

- **Tool credential binding**: wiring tool secret schemas to the credential store.

  **Default binding (no mapping needed):** By convention, a tool's secret keys map to credentials under the `tool.<tool_name>` namespace. A tool declaring `secret_schema` with a key `api_key` would look up `tool.web_search.api_key`. This is fully derivable — the naming convention *is* the binding. No mapping table or configuration is required. Keys not explicitly mapped always fall back to `tool.<tool_name>.<secret_key>`.

  **Cross-namespace references for shared credentials:** Tools that share the same upstream credential (e.g., multiple tools using the same Brave API key) can declare a `credential_refs` class attribute to point outside their namespace:

  ```python
  class BraveSearchTool(BaseTool):
      secret_schema = {
          "properties": {"api_key": {"type": "string"}},
          "required": ["api_key"],
      }
      credential_refs = {"api_key": "service.brave.api_key"}
  ```

  When `credential_refs` is present, the binding layer uses the explicit mapping for listed keys. Keys not in `credential_refs` still fall back to the default namespace. The shared credential lives under `service.*` (e.g., `service.brave.api_key`), avoiding duplication across tools.

  The credential store itself is unaware of this convention — it just stores and retrieves by name. The binding logic lives entirely in the tool execution layer.

- Tool secrets UI (credential assignment per tool)
- Credential rotation or expiry
- Enforcement of reserved namespaces (`system.*`, `service.*`, `provider.*`, `tool.*`, `user.*`)
- Decide on reveal vs. write-only model for the credential management UI. If reveal is needed, add a scoped `POST /api/credentials/{name}/unlock` endpoint with appropriate safeguards at that time.
- Consider additional auth or audit logging for credential access
