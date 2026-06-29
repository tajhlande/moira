# Inference Provider Configuration Migration to System Settings

## Goal

Move inference provider configuration (base URL, API key, provider type) and model
selection (role assignments, capability flags) out of the YAML config file and into
database-backed system settings, with automatic model discovery from providers via
their OpenAI-compatible `/models` endpoint.

## Motivation

Currently, adding or changing an inference provider requires editing the YAML config
file and restarting the application. This is friction for a self-hosted platform that
should be configurable at runtime. The system already has:

- Model discovery (`InferenceClient.list_models()` → `ModelRegistry.refresh_models()`)
- DB-backed role assignments (`model_preferences` table)
- An encrypted credential store (`credentials` table + `CredentialService`)
- A settings infrastructure (`settings` table, `SettingsService`, API routes)

But providers themselves are still defined only in the config file and read once at
startup. This plan makes providers fully database-backed and runtime-manageable.

## Key Decisions

1. **API key storage**: Encrypted via the existing `CredentialService`. Provider rows
   store a `credential_name` reference (e.g. `"provider:local"`), not plaintext keys.
2. **API routes**: Clean cutover. Old `/api/models` GET/PUT endpoints removed and
   replaced with `/api/inference/*`. Frontend updated in the same changeset.
3. **Config file**: The `inference:` section is removed from the config template and
   the code that reads it. No seeding path from config — providers must be added via
   the UI/API. The config file remains required for database path, embedding config,
   and app settings.
4. **Dedicated tables** (not the scalar `settings` table): Providers and their models
   are lists of complex objects needing individual CRUD — a dedicated table is the
   right pattern, consistent with `model_preferences`, `tools`, `credentials`.

## Schema (Migration 019)

```sql
CREATE TABLE IF NOT EXISTS inference_providers (
    name            TEXT PRIMARY KEY,
    base_url        TEXT NOT NULL,
    provider_type   TEXT NOT NULL DEFAULT 'completions',
    credential_name TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inference_models (
    provider_name       TEXT NOT NULL REFERENCES inference_providers(name) ON DELETE CASCADE,
    model_id            TEXT NOT NULL,
    native_tool_calling INTEGER NOT NULL DEFAULT 0,
    discovered_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (provider_name, model_id)
);
```

API keys live in the existing encrypted `credentials` table, keyed by
`credential_name`.

The existing `model_preferences` table is unchanged — it continues to store role
assignments (`intelligence_endpoint`, `intelligence_model`, `task_endpoint`,
`task_model`).

## API Key Resolution

When creating or updating a provider, the API key is stored as an encrypted
credential via `CredentialService.store_credential(name=f"provider:{name}",
value={"api_key": key})`. At client creation time, the registry resolves the key:

1. Env var `MOIRA_API_KEY_{NAME}` (if set — backward compat for existing deployments)
2. Credential `provider:{name}` (decrypted via `CredentialService`)
3. Empty string (no key — some providers like local Ollama don't require one)

The `CredentialService` needs a `get_value(name) → dict | None` method added (it
currently only has `store_credential` and `list_credentials`, which returns metadata
without decrypted values).

## Implementation Phases

### Phase 1: Schema + Repository + Interfaces

**Files created:**
- `backend/moira/persistence/sqlite/migrations/019_inference_providers.sql`
- `backend/moira/persistence/sqlite/repos/inference_providers.py`

**Files modified:**
- `backend/moira/persistence/sqlite/schema.py` — bump `CURRENT_VERSION` to 19
- `backend/moira/persistence/interfaces.py` — add `InferenceProvider`,
  `InferenceModelRow` dataclasses + `InferenceProviderRepository` ABC

**Repository methods:**
- `get_all_providers() → list[InferenceProvider]`
- `get_provider(name) → InferenceProvider | None`
- `upsert_provider(provider) → None`
- `delete_provider(name) → bool`
- `get_models(provider_name) → list[InferenceModelRow]`
- `get_all_models() → list[InferenceModelRow]`
- `upsert_model(provider_name, model_id, native_tool_calling) → None`
- `upsert_discovered_models(provider_name, model_ids: list[str]) → None` — inserts
  new entries without overwriting existing `native_tool_calling` flags
- `delete_stale_models(provider_name, current_model_ids: list[str]) → None`

### Phase 2: Registry Refactor

**Files modified:**
- `backend/moira/inference/registry.py` — the core change
- `backend/moira/services/credential_service.py` — add `get_value(name) → dict | None`

**`ModelRegistry` constructor changes:**
- Takes `provider_repo: InferenceProviderRepository` and
  `credential_service: CredentialService` instead of `config: InferenceConfig`
- Retains `prefs_repo` and `user_id` for role assignment resolution

**Dynamic client lifecycle:**
- `async add_provider(name, base_url, api_key, provider_type)` — store credential,
  upsert provider row, create + start `InferenceClient`, add to `_clients`, refresh
  models for this provider, persist discovered models to `inference_models`
- `async update_provider(name, base_url, api_key, provider_type)` — if URL/key/type
  changed: stop old client, start new one, persist, refresh models
- `async remove_provider(name)` — stop client, remove from `_clients` +
  `_available_models`, delete provider row + models (FK cascade), delete credential
- `async refresh_models(provider_name=None)` — refresh one or all providers.
  Persist discovered models via `upsert_discovered_models` +
  `delete_stale_models`.

**`resolve()` changes:**
- Reads `provider_type` and `native_tool_calling` from `inference_models` table
  via the repo, instead of from `InferenceConfig.providers[].models`
- Assignment fallback reads from `model_preferences` only (no config fallback)

**API key resolution** (private method):
- `_resolve_api_key(provider_name, credential_name) → str` — env var → credential → ""

### Phase 3: Config Cleanup

**Files modified:**
- `backend/moira/config.py` — remove `InferenceConfig`, `InferenceEndpointConfig`,
  `InferenceModelsConfig`, `ModelCapabilityConfig`. Remove `inference` field from
  `MoiraConfig`.
- `backend/moira/service_setup.py` — remove all code reading
  `config.inference.providers` / `config.inference.models`. Replace with
  DB-backed provider loading via `provider_repo.get_all_providers()`.
- `config/moira-config-template.yaml` — remove the `inference:` section

**Startup flow change:**
```
providers = await provider_repo.get_all_providers()
for p in providers:
    api_key = registry._resolve_api_key(p.name, p.credential_name)
    client = InferenceClient(base_url=p.base_url, api_key=api_key, ...)
    await client.start()
    registry.add_client(p.name, client)
await registry.refresh_models()
```

If no providers exist (fresh deployment), the registry starts empty. User adds
providers via the UI.

### Phase 4: API Endpoints

**Files created:**
- `backend/moira/api/routes/inference.py`

**Files modified:**
- `backend/moira/api/routes/__init__.py` — mount `inference.router`
- `backend/moira/api/routes/conversations.py` — remove `GET /models` and
  `PUT /models`

**Endpoints:**

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| GET | `/api/inference/providers` | — | List providers (metadata only, no keys) |
| POST | `/api/inference/providers` | `{name, base_url, api_key, provider_type}` | Add provider + auto-discover models |
| PUT | `/api/inference/providers/{name}` | `{base_url, api_key, provider_type}` | Update provider |
| DELETE | `/api/inference/providers/{name}` | — | Remove provider + models + credential |
| POST | `/api/inference/providers/{name}/refresh` | — | Re-discover models for one provider |
| POST | `/api/inference/refresh` | — | Re-discover all providers |
| GET | `/api/inference/models` | — | List discovered models grouped by provider |
| PUT | `/api/inference/models/assign` | `{intelligence: {endpoint, model}, task: {endpoint, model}}` | Set role assignments |
| PATCH | `/api/inference/models/{provider}/{model_id}` | `{native_tool_calling: bool}` | Toggle capability flag |

### Phase 5: Frontend

**Files created:**
- `frontend/src/components/SettingsInference.vue`

**Files modified:**
- `frontend/src/api/client.ts` — replace `getModels`/`setModels` with
  inference-specific API calls
- `frontend/src/router/index.ts` — add `/settings/inference` route
- `frontend/src/components/SettingsSidebar.vue` — add "Inference" menu item

**UI layout:**
- Provider cards (name, URL, type, model count) with edit/delete buttons
- Add provider button → modal form (name, base_url, api_key, provider_type)
- Per-provider collapsible model list with `native_tool_calling` toggle per model
- Role assignment section: two dropdowns (intelligence/task) selecting from the full
  model list, grouped by provider name
- Refresh button to re-trigger model discovery

### Phase 6: Tests

**Files created:**
- `backend/tests/test_inference_providers.py` — repo CRUD, model upsert/stale cleanup
- `backend/tests/test_inference_api.py` — provider CRUD endpoints, model refresh,
  assignment, capability toggle

**Files modified:**
- `backend/tests/test_model_registry.py` (if exists) or relevant test files — update
  for dynamic add/remove/update provider, credential resolution
- Any test referencing `InferenceConfig` or `/models` endpoints

## File Impact Summary

| Action | Files |
|--------|-------|
| New | `migrations/019_inference_providers.sql`, `repos/inference_providers.py`, `routes/inference.py`, `SettingsInference.vue`, `test_inference_providers.py`, `test_inference_api.py` |
| Modified | `registry.py`, `service_setup.py`, `config.py`, `interfaces.py`, `conversations.py`, `api/routes/__init__.py`, `credential_service.py`, `client.ts`, `router/index.ts`, `SettingsSidebar.vue`, `config/moira-config-template.yaml`, `schema.py` |

## Provider Slug Architecture (Migration 020)

### Problem

The original schema used `name` as both the human-readable display name AND the
primary key. This meant renaming a provider broke all FK references, and names
with spaces or special characters caused issues in URL paths and env var lookups.

### Solution

Split into two fields:
- `slug` — kebab-case internal identifier (PK, immutable after creation)
- `display_name` — human-readable name (editable)

### Slug Generation Rules

The slug is derived from the display name using these rules:

1. Insert a hyphen before uppercase letters preceded by lowercase/digit
   (camelCase splitting): `"OpenRouter"` → `"open-router"`
2. Replace whitespace runs with single hyphens
3. Lowercase everything
4. Replace any character not in `[a-z0-9-]` with a hyphen
5. Collapse consecutive hyphens into one
6. Strip leading/trailing hyphens
7. If empty, fall back to `"provider"`
8. If starts with a digit, prefix `"p"` (URL safety)

Uniqueness: if the generated slug already exists, append `-1`, `-2`, etc.
until a free slug is found. The slug is immutable after creation.

Examples:
- `"OpenRouter"` → `"open-router"`
- `"Local Lab"` → `"local-lab"`
- `"123API"` → `"p123-api"`
- `"Ollama2"` → `"ollama2"`

### Derived Identifier Conventions

Two internal identifiers are derived from the slug:

| Context | Convention | Example |
|---------|-----------|---------|
| Env var | `MOIRA_API_KEY_{SLUG}` with hyphens → underscores, uppercased | `open-router` → `MOIRA_API_KEY_OPEN_ROUTER` |
| Credential name | `provider.{SLUG}` with hyphens → underscores | `open-router` → `provider.open_router` |

The credential name validator (`[a-zA-Z_][a-zA-Z0-9_.]*`) does not allow
hyphens, so they are replaced with underscores in credential names.

### API Shape

```
POST /api/inference/providers
  Body: { display_name, base_url, api_key, provider_type }
  Response: { slug, display_name, base_url, ... }

GET /api/inference/providers
  Response: { providers: [{ slug, display_name, ... }] }

PUT /api/inference/providers/{slug}
  Body: { display_name?, base_url, api_key, provider_type }

DELETE /api/inference/providers/{slug}

GET /api/inference/models
  Response: { models: [{ id, provider: slug, ... }] }
```

### Migration 020

Wipes existing provider data (providers, models, model_preferences endpoints).
Users re-enter providers via the UI. The `inference_providers` table is recreated
with `slug` as PK and `display_name` column. The `inference_models` table FK is
updated to reference `provider_slug`.

## Breaking Changes

- Existing deployments using config-file providers must re-enter provider config via
  the UI/API after upgrading. No migration seeding from config.
- `GET /api/models` and `PUT /api/models` removed — frontend must use new
  `/api/inference/*` endpoints.
- `InferenceConfig` and related Pydantic models removed from `config.py`.
- Migration 020: provider `name` field replaced by `slug` + `display_name`.
  Existing provider data is wiped; model assignments reset.
