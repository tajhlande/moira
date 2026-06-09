# Dynamic Tool Discovery — Design Plan

## Summary

Build a system where users can register external APIs as Moira tools through a guided wizard. The user provides an API endpoint or OpenAPI description document, the system introspects the API to build typed `ToolDefinition` entries, stores any required credentials in the encrypted credential store, and registers the tools in the catalog so they are available for semantic discovery and research execution.

## Use Case Garden Paths

### A. OpenAPI API hosting its own description document

The user provides the API base URL. The system probes well-known paths to find the spec.

### B. OpenAPI API with user-supplied description URL or file upload

The user provides the API base URL plus a separate URL or uploaded file for the spec document.

### C. Swagger 2.0 APIs

Swagger 2.0 specs are structurally similar to OpenAPI 3.x. `prance` converts them internally. The user experience is identical to paths A and B — the format difference is transparent.

### D. Non-standardized APIs

APIs with no machine-readable description document at all (no OpenAPI, no Swagger). The user would need to manually describe endpoints. This is a stretch goal, not in initial scope. If pursued, the wizard would offer a form-based tool creation mode where the user manually enters the method, path, parameters, and auth requirements for each endpoint.

---

## Tool Naming Scheme

Every generated tool name must be globally unique across all sources — built-in tools, YAML-defined tools, and ingested API tools. The naming scheme uses a **source prefix** derived from the API source's group slug, followed by a **method identifier** derived from the operation.

### Source Identity Chain

The group slug is the central identity for an API source. It flows from the spec to multiple places:

```
spec info.title ("Weather API")
    ↓ default (user can override in wizard Step 4)
group_name ("Weather API")
    ↓ derive_slug()
group_slug ("weather_api")
    ↓ used in
    ├── tools.group_name   — groups tools in the catalog (like "standard" groups built-ins)
    ├── tool_groups table  — display row for the group ("Weather API" → "weather_api")
    ├── tool name prefix   — weather_api__get_current
    └── credential ref     — service.weather_api.auth
```

The URL is never used as a group identifier. URLs are implementation details; the API title from the spec is the human-friendly name. The user can override the group name during ingestion.

### Format

```
<group_slug>__<method_id>
```

- `group_slug`: derived from the `group_name` (defaults to the API title from the spec). Lowercased, spaces and hyphens replaced with underscores, non-alphanumeric characters stripped, truncated to 24 characters. Examples: `weather_api`, `pokeapi`, `github_rest`.
- `method_id`: derived from each operation. Deterministic — the same operation always produces the same method_id. Priority:
  1. `operationId` from the spec, sanitized to snake_case (lowercase, non-alphanumeric → `_`, collapsed)
  2. `{method}_{non_param_segments}` — the HTTP method lowercased + all non-parameter path segments joined with underscores. Path segments enclosed in braces (`{param}`) are parameter placeholders and are skipped. Examples:
     - `GET /current` → `get_current`
     - `POST /orders` → `post_orders`
     - `GET /pokemon/{name}` → `get_pokemon`
     - `GET /books/{book_id}/reviews` → `get_books_reviews`
     - `GET /repos/{owner}/{repo}/issues` → `get_repos_issues`
- `__` (double underscore) separates source from method. This delimiter never appears in built-in tool names (which use single underscores), making provenance visually scannable.

### Why double underscore, not dot-qualified

Dot-qualified names like `weather_api.get_current` were considered — they read naturally and align with the codebase's existing credential naming convention (`service.weather_api.auth`). However, dot-qualified names are rejected if the system ever adopts native function calling through major LLM providers:

- **OpenAI** tool names must match `^[a-zA-Z0-9_-]+$` — no dots allowed
- **Anthropic** tool names must match `^[a-zA-Z0-9_-]{1,128}$` — same restriction

The current inference pipeline uses text-based tool call parsing (the model outputs JSON with a `"tool"` key), which has no character restrictions. But adopting the most restrictive naming convention now avoids a migration later if native function calling is added. Double underscore satisfies all current and anticipated constraints: text parsing, OpenAI, Anthropic, SQLite, and the existing codebase conventions.

### Examples

| API Source  | Operation                                 | Tool Name                       |
|-------------|-------------------------------------------|---------------------------------|
| Weather API | `GET /current`, operationId: `getCurrent` | `weather_api__get_current`      |
| Weather API | `POST /forecast`, no operationId          | `weather_api__post_forecast`    |
| PokeAPI     | `GET /pokemon/{name}`                     | `pokeapi__get_pokemon`          |
| PokeAPI     | `GET /berry/{id}`                         | `pokeapi__get_berry`            |
| GitHub REST | `GET /repos/{owner}/{repo}/issues`        | `github_rest__get_repos_issues` |
| Media API   | `GET /books/{book_id}/reviews`            | `media_api__get_books_reviews`  |
| Media API   | `GET /movies/{movie_id}/reviews`          | `media_api__get_movies_reviews` |

### Collision Handling

Using all non-parameter path segments (not just the last one) makes collisions rare — two operations must share the same method and identical non-parameter path structure. If it does happen, append `_2`, `_3`, etc. to the second occurrence, determined by iteration order in the spec.

### Uniqueness Guarantee

The source prefix + double-underscore delimiter ensures no collision with:
- Built-in tools (`web_search`, `url_content`, `calculator`) — no `__`
- YAML-defined tools — user-controlled names, no `__`
- Tools from other API sources — different `group_slug`

If a user re-ingests the same API with a different group name, the tools are treated as distinct. If they use the same group name, the refresh flow handles it (see Provenance and Refresh).

---

## Provenance and Refresh

### Tracking Where Tools Came From

Every ingested tool carries two provenance markers:

1. **`source_id`** in `ToolDefinition.config` — the UUID of the `api_sources` row that created this tool. This is the primary link.
2. **`operation_fingerprint`** in `ToolDefinition.config` — a deterministic hash of `{method}:{path}` (e.g., `"GET:/current"`). This identifies the specific operation within the source, even if the spec is re-ordered or the operationId changes.

```json
{
    "source_id": "a1b2c3d4-...",
    "operation_fingerprint": "GET:/current",
    ...
}
```

The `api_sources` table tracks the source as a whole:

```sql
CREATE TABLE api_sources (
    id TEXT PRIMARY KEY,              -- UUID
    name TEXT NOT NULL,               -- User-friendly name
    base_url TEXT NOT NULL,
    spec_url TEXT,                    -- NULL if uploaded
    spec_format TEXT NOT NULL,        -- "openapi_3_0", "openapi_3_1", "swagger_2"
    auth_type TEXT,
    group_name TEXT NOT NULL,         -- Also used as group_slug for naming
    tool_count INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### How Refresh Works

When the user clicks "Refresh" on an API source:

1. System re-fetches the spec from `spec_url` (or prompts user to re-upload if `spec_url` is NULL)
2. Parses the new spec into a fresh list of `ToolCandidate`
3. Queries existing tools where `config.source_id = <source_id>` — gets the current tool set
4. For each existing tool, extracts its `operation_fingerprint`
5. For each new ToolCandidate, computes its fingerprint (`method:path`)
6. Produces a **diff** with three categories:

| Category      | Meaning                                                       | Action                                                          |
|---------------|---------------------------------------------------------------|-----------------------------------------------------------------|
| **Unchanged** | Fingerprint matches existing tool, no parameter changes       | Keep as-is                                                      |
| **Modified**  | Fingerprint matches but parameters/schema/description changed | Update tool in place (preserve enabled state, config overrides) |
| **New**       | Fingerprint not found in existing tools                       | Present for selection (same as initial ingestion)               |
| **Removed**   | Existing tool's fingerprint not found in new spec             | Flag for user decision — disable or delete                      |

7. User reviews the diff and selects which new tools to add, which modified tools to update, and what to do with removed tools
8. Commit applies changes atomically

This approach is resilient to spec re-ordering, operationId changes, and description edits. The `operation_fingerprint` (method + path) is the stable identifier because changing an operation's method or path is effectively creating a new operation.

### Deleting an API Source

When the user deletes an API source:

1. Find all tools with `config.source_id = <source_id>`
2. Remove each from the in-memory `ToolCatalog`
3. Remove each from LanceDB embeddings via `ToolDiscovery`
4. Delete the shared credential (`service.<group_slug>.auth`)
5. Delete each tool row from the `tools` table
6. Delete the `api_sources` row
7. All of this runs as a single atomic operation through the write queue

## Design Decisions

| Decision               | Resolution                                                                           |
|------------------------|--------------------------------------------------------------------------------------|
| Wizard UX              | Full page (complexity merits it)                                                     |
| Auth to fetch spec     | Deferred — future use case                                                           |
| API credentials        | Inform only — tools requiring auth are registered disabled with explanation          |
| Operation hard limit   | 200 per ingestion                                                                    |
| Spec content storage   | Don't store full spec — just `spec_url` for re-fetch; file uploads require re-upload |
| Pruning stale tools    | User-driven via refresh, not automatic                                               |
| Operation discovery UX | Grouped by tag with collapsible sections + search bar for filtering                  |
| Cost tracking          | Deferred entirely to a later phase — no cost fields in this design                   |

## Architecture

### New Components

```
moira/services/tool_ingestion/
├── __init__.py
├── spec_parser.py          # Parse OpenAPI/Swagger specs → ToolCandidate list
├── spec_resolver.py        # Fetch spec from URL, probe well-known paths, handle uploads
├── auth_analyzer.py        # Extract security requirements from spec
└── tool_provisioner.py     # Orchestrate parsing → catalog registration (credentials deferred)
```

### Data Flow

```
User provides URL or file
       │
       ▼
  spec_resolver.py
  - Probe well-known paths (path A)
  - Fetch from explicit URL (path B)
  - Accept file upload (path B)
       │
       ▼
  spec_parser.py
  - Parse OpenAPI 3.x or Swagger 2.0 (via prance)
  - Extract operations → list[ToolCandidate]
  - Each candidate: name, description, method, path, params, request body, responses
       │
       ▼
  auth_analyzer.py
  - Extract security schemes from spec
  - Classify auth type (API key, bearer, basic, none)
  - Inform user: "This API requires authentication"
       │
       ▼
  User reviews, selects operations via wizard
       │
       ▼
  tool_provisioner.py
  - For each selected ToolCandidate:
    1. Build ToolDefinition with RESTTool implementation
    2. If auth required: set enabled=false, store auth_type in config
    3. Save ToolDefinition to tools table
    4. Embed description in LanceDB via ToolDiscovery
    5. Add to in-memory ToolCatalog
```

## Intermediate Representations

### ToolCandidate

```python
@dataclass
class ToolCandidate:
    """Intermediate representation of a tool extracted from an API spec.
    Presented to the user for selection before provisioning."""

    name: str                          # Generated tool name (e.g., "weather_get_current")
    description: str                   # From operation.summary + description
    method: str                        # GET, POST, etc.
    path: str                          # e.g., "/weather/current"
    parameters: list[ParameterSpec]    # Path, query, header parameters
    request_body: RequestBodySpec | None
    responses: dict[str, str]          # Status code → description
    security_requirements: list[str]   # Names of required security schemes
    tags: list[str]                    # From the spec's tags field
    operation_id: str | None           # Original operationId from spec
    deprecated: bool
```

### ParameterSpec and RequestBodySpec

```python
@dataclass
class ParameterSpec:
    name: str
    location: str       # "query", "path", "header", "cookie"
    required: bool
    schema_def: dict    # JSON Schema for this parameter
    description: str

@dataclass
class RequestBodySpec:
    content_type: str   # e.g., "application/json"
    schema_def: dict    # JSON Schema for request body
    required: bool
    description: str
```

### ParsedSpec

```python
@dataclass
class ParsedSpec:
    title: str
    description: str
    version: str                      # "openapi_3_0", "openapi_3_1", "swagger_2"
    server_urls: list[str]
    security_schemes: dict[str, SecuritySchemeInfo]
    operations: list[ToolCandidate]
    spec_url: str | None              # For re-fetch
```

### SecuritySchemeInfo

```python
@dataclass
class SecuritySchemeInfo:
    scheme_type: str       # "api_key_header", "api_key_query", "bearer", "basic", "none"
    name: str              # Header name, query param name, etc.
    location: str          # "header", "query" — for API key types
    description: str
```

## Auth Types

Extracted from OpenAPI `securitySchemes`. Initial scope:

| Type             | OpenAPI `type`           | Credential Shape                         | How RESTTool Uses It             |
|------------------|--------------------------|------------------------------------------|----------------------------------|
| API Key (header) | `apiKey`, `in: header`   | `{"api_key": "..."}`                     | Set as named header              |
| API Key (query)  | `apiKey`, `in: query`    | `{"api_key": "..."}`                     | Append to query params           |
| Bearer Token     | `http`, `scheme: bearer` | `{"token": "..."}`                       | `Authorization: Bearer <token>`  |
| Basic Auth       | `http`, `scheme: basic`  | `{"username": "...", "password": "..."}` | `Authorization: Basic <encoded>` |

Deferred: OAuth2 client_credentials (natural follow-up), OAuth2 authorization_code (requires browser redirect flow).

## RESTTool Enhancements

Current `RESTTool` is minimal — takes `endpoint` and `method` from config, sends everything as either params (GET) or JSON body (everything else). Needs to become more sophisticated:

1. **Path parameter resolution** — `/weather/{city}` + `{city: "London"}` → `/weather/London`
2. **Query/body/header separation** — use `x-parameter-location` in the argument schema to route each parameter correctly
3. **Credential injection** — deferred to a later phase. When implemented, RESTTool will look up credentials from `CredentialService` using the convention `service.<group_slug>.auth` and inject them as headers/query params based on `auth_type` in config. This is where the deferred tool-to-credential binding from the tool-secrets plan will land.

### Argument Schema Generation

For each operation, generate a JSON Schema that describes the tool's arguments:

```python
def build_argument_schema(params: list[ParameterSpec], body: RequestBodySpec | None) -> dict:
    properties = {}
    required = []

    for param in params:
        properties[param.name] = {
            **param.schema_def,
            "description": param.description,
            "x-parameter-location": param.location,
        }
        if param.required:
            required.append(param.name)

    if body:
        for prop_name, prop_schema in body.schema_def.get("properties", {}).items():
            properties[prop_name] = prop_schema
        required.extend(body.schema_def.get("required", []))

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
```

The `x-parameter-location` extension tells `RESTTool` where to route each argument — path, query, or header. Body parameters are everything not tagged with a location.

### Credential Resolution (Deferred)

When credentials are implemented in a future phase, RESTTool will resolve them at execution time. The design is preserved here for reference:

```python
async def _resolve_auth(self) -> dict[str, str]:
    config = self.definition.config
    auth_type = config.get("auth_type", "none")
    if auth_type == "none":
        return {}

    credential_ref = config.get("credential_ref", f"tool.{self.name}.auth")
    cred_svc = service_provider("credential_service")
    creds = await cred_svc.get_credential(credential_ref)
    if not creds:
        logger.warning("No credentials found for tool '%s' (ref: %s)", self.name, credential_ref)
        return {}

    if auth_type == "bearer":
        return {"Authorization": f"Bearer {creds['token']}"}
    elif auth_type == "api_key_header":
        header_name = config.get("auth_header_name", "X-API-Key")
        return {header_name: creds["api_key"]}
    elif auth_type == "api_key_query":
        # Handled separately — merged into query params
        return {}
    elif auth_type == "basic":
        import base64
        encoded = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    return {}
```

### Credential Binding Convention (Deferred)

When tools are provisioned with credentials (future phase), a single credential will be stored per API source (shared across all tools from that source):

```
service.<group_slug>.auth
```

Each tool's `config` will include `"credential_ref": "service.weather_api.auth"`.

This avoids duplicating the same API key across N tools from one API. The convention follows the `service.*` namespace from the tool-secrets plan.

## Spec Parser Design

### Library: `prance`

`prance` resolves `$ref` references (including cross-file and HTTP references) and supports both Swagger 2.0 and OpenAPI 3.x. It handles the spec-to-dict conversion cleanly.

### Spec Resolver — Probing Strategy

For path A (API hosts its own spec), probe these paths in order:

```python
SPEC_PROBE_PATHS = [
    "/openapi.json",
    "/openapi.yaml",
    "/swagger.json",
    "/swagger.yaml",
    "/v3/api-docs",
    "/v2/api-docs",
    "/api-docs",
    "/api/docs",
    "/docs/spec",
    "/.well-known/openapi.json",
    "/.well-known/schema-discovery",
]
```

For each path, try GET with `Accept: application/json, application/yaml`. First successful parse wins. If none found, report failure and suggest path B (user provides spec URL or uploads file).

### Handling Large Specs

Some APIs have hundreds of operations. The wizard should:
- Show operations grouped by tag with collapsible sections
- Provide a search bar that flattens into filtered results
- Default to "Deselect All" for APIs with > 50 operations
- Hard limit of 200 operations per ingestion
- Warn the user about registering many tools (impact on semantic discovery quality)

## Database Changes

### Migration 013: `api_sources` table

```sql
CREATE TABLE api_sources (
    id TEXT PRIMARY KEY,              -- UUID
    name TEXT NOT NULL,               -- User-friendly name
    base_url TEXT NOT NULL,
    spec_url TEXT,                    -- URL where the spec was fetched; NULL if uploaded
    spec_format TEXT NOT NULL,        -- "openapi_3_0", "openapi_3_1", "swagger_2"
    auth_type TEXT,                   -- "api_key_header", "api_key_query", "bearer", "basic", "none"
    group_name TEXT NOT NULL,         -- Tool group name for tools from this source
    tool_count INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### `tools` table: no schema changes

Generated tools use the existing `tools` table. New fields live in `ToolDefinition.config` as JSON:

```json
{
    "base_url": "https://api.weather.com/v2",
    "endpoint": "/current",
    "method": "GET",
    "parameters": [
        {"name": "city", "location": "query", "required": true, "schema": {"type": "string"}}
    ],
    "auth_type": "api_key_header",
    "auth_header_name": "X-API-Key",
    "credential_ref": "service.weather_api.auth",
    "source_id": "uuid-of-api-source",
    "operation_fingerprint": "GET:/current"
}
```

The `implementation` field is set to `moira.tools.rest_tool.RESTTool` for all generated tools.

### Why not store spec content?

After provisioning, every tool already stores its own operation details in config. The spec's useful information has been decomposed into per-tool records. The spec is only needed again for:

- **Re-provisioning** (add more tools) — re-fetch from `spec_url` or user re-uploads
- **Refreshing** (detect API changes) — re-fetch from `spec_url`
- **Debugging** — per-tool config captures what matters

For file uploads (no `spec_url`), the user re-uploads to add more tools. This is a reasonable tradeoff for an edge case that avoids storing potentially large specs in the DB.

## API Endpoints

### `POST /api/tools/ingest/start`

Start the ingestion wizard. Read-only — no tools are created. Body:

```json
{
    "url": "https://api.weather.com",
    "spec_url": null,
    "spec_content": null,
    "group_name": null
}
```

Response: `IngestPreview`

```json
{
    "source_id": "uuid",
    "api_title": "Weather API",
    "api_description": "...",
    "api_version": "2.1.0",
    "spec_format": "openapi_3_0",
    "server_urls": ["https://api.weather.com/v2"],
    "security_schemes": {
        "api_key_header": {
            "scheme_type": "api_key_header",
            "name": "X-API-Key",
            "location": "header",
            "description": "API key for authentication"
        }
    },
    "operations": [
        {
            "name": "weather_get_current",
            "description": "Get current weather for a city",
            "method": "GET",
            "path": "/current",
            "parameters": [
                {
                    "name": "city",
                    "location": "query",
                    "required": true,
                    "schema_def": {"type": "string"},
                    "description": "City name"
                }
            ],
            "request_body": null,
            "tags": ["weather"],
            "deprecated": false,
            "security_requirements": ["api_key_header"]
        }
    ],
    "total_operations": 12,
    "auth_required": true,
    "auth_type": "api_key_header"
}
```

### `POST /api/tools/ingest/commit`

User selects operations. Body:

```json
{
    "source_id": "uuid",
    "server_url": "https://api.weather.com/v2",
    "selected_operations": ["weather_get_current", "weather_get_forecast"],
    "group_name": "Weather API",
    "is_default": false
}
```

Returns list of created tool names and status per tool. Tools from auth-required APIs are created with `enabled=false`.

### `GET /api/tools/ingest/sources`

List all ingested API sources.

### `DELETE /api/tools/ingest/sources/{source_id}`

Delete an API source and all its tools. Removes tools from catalog, embeddings, credentials, and database.

### `POST /api/tools/ingest/sources/{source_id}/refresh`

Re-fetch the spec from `spec_url`, diff against current tools, return a preview of changes. User must commit the refresh separately.

### `GET /api/tools/ingest/sources/{source_id}/preview`

Return the stored preview (operations list) by re-parsing the spec from `spec_url`. Requires re-fetch. Returns same shape as `/start` response.

## Frontend — Step-by-Step UX

### Settings Navigation

Add a "Tools" item to the settings sidebar (alongside "System" and "Analytics"):

```
Settings Sidebar:
  - System
  - Tools        ← NEW
  - Analytics
```

The Tools settings page has two sections:
1. **API Sources and Registered Tools** — management view (see Tool Management Screen below)
2. **"Add API" button** — navigates to the full-page ingestion wizard

---

### Ingestion Wizard — Full Page

The wizard is a full-page route (e.g., `/settings/tools/ingest`), not a modal. A breadcrumb or back link returns to the Tools management screen. The wizard has a step indicator at the top showing progress.

---

#### Step 1: Provide API Details

**Fields:**
- **API Base URL** (required) — text input, e.g., `https://pokeapi.co/api/v2`
- **Spec URL** (optional) — text input, "I have a separate URL for the API description document"
- **File Upload** (optional) — drag-and-drop zone for JSON/YAML files, "Upload an OpenAPI or Swagger file"

**Interaction rules:**
- Exactly one of the three inputs must be provided: base URL (auto-probe), spec URL, or file upload
- If the user provides both a base URL and a spec URL, the base URL is used for the API server and the spec URL for fetching the description
- If the user uploads a file, the base URL is still required (the spec doesn't always contain usable server URLs)
- "Analyze" button — calls `POST /api/tools/ingest/start`
- Loading state: spinner with "Analyzing API..." text
- Error states:
  - Network error: "Could not reach the API at `<url>`. Check the URL and try again."
  - Spec parse error: "The document at `<url>` is not a valid OpenAPI or Swagger spec."
  - No spec found (path A only): "No API description found at `<base_url>`. Try providing the spec URL directly."

**Garden path differences:**

| Path | User provides | System does |
|---|---|---|
| A — Auto-discover | Base URL only | Probes well-known paths, uses first successful fetch |
| B — Spec URL | Base URL + spec URL | Fetches spec from the explicit URL |
| B — File upload | Base URL + file | Parses the uploaded file directly (no network fetch for spec) |
| C — Swagger 2.0 | Same as A or B | Same fetch, but parser detects Swagger 2.0 and converts internally. Transparent to the user — the spec version badge in Step 2 shows "Swagger 2.0". |
| D — Non-standard | N/A | Not supported in initial scope. Wizard does not offer this path. |

---

#### Step 2: Review API

**Displayed information (read-only):**
- API Title (from `info.title`)
- API Description (from `info.description`, collapsible if long)
- Spec version badge: "OpenAPI 3.0", "OpenAPI 3.1", or "Swagger 2.0"
- Server URL selector (dropdown if spec lists multiple servers; single display if only one)

**Security section (conditional):**
- If the spec declares security schemes, a warning card is shown:
  - "This API requires authentication (API Key in header)"
  - "Tools from this API will be registered as disabled until credentials are configured."
  - Auth type displayed in plain language: "API Key (sent in header)", "Bearer Token", "Basic Authentication"
- If the spec declares no security, shows "This API does not require authentication" with a success badge

**Navigation:**
- "Back" returns to Step 1 (preserves input)
- "Continue" proceeds to Step 3

**Garden path differences:** None — all paths converge after the spec is successfully parsed. Step 2 is identical regardless of how the spec was obtained.

---

#### Step 3: Select Operations

**Layout:**
- Header: "Select Operations" with counter "12 of 47 selected"
- Search bar at top — filters operations by name, path, or description across all tags
- Two toggle buttons: "Group by Tag" (default) and "Flat List"
- Below: the operations selection area

**Grouped by Tag view (default):**
- Each spec tag gets a collapsible section
- Section header: tag name, count ("Weather — 8 operations"), "Select All" checkbox
- Inside: table of operations with columns:
  - Checkbox
  - Method badge (color-coded: GET=green, POST=blue, PUT=orange, PATCH=yellow, DELETE=red)
  - Path
  - Description (from operation summary)
  - Generated tool name (preview, e.g., `weather_api__get_current`)
  - Deprecated badge (if applicable)

**Flat List view / Search results:**
- Same table columns, no grouping
- Search filters in real-time as user types
- "Select All Visible" checkbox in header

**Selection rules:**
- Deprecated operations are deselected by default but still selectable
- Hard limit: warn banner at 100+ selected, block at 200 with message "Maximum 200 operations per ingestion. Deselect some operations to continue."
- For APIs with > 50 operations, all operations start deselected
- For APIs with ≤ 50 operations, all non-deprecated operations start selected

**Navigation:**
- "Back" returns to Step 2
- "Continue" requires at least one operation selected

**Garden path differences:** None — all paths converge.

---

#### Step 4: Configure

**Fields:**
- **Group Name** (text input, defaults to API title from spec) — this determines the `group_slug` used in tool naming
- **Generated slug preview** — read-only, shows the derived `group_slug`, e.g., "weather_api"
- **Tool name preview** — shows first 3 tool names as examples, e.g., `weather_api__get_current`
- **Make available by default** (toggle, default: off) — when on, tools are marked `is_default=true` and always included in research runs without needing semantic discovery
- **Summary card**: "8 tools will be registered from Weather API with API Key authentication"

**Navigation:**
- "Back" returns to Step 3 (preserves selection)
- "Register Tools" button — calls `POST /api/tools/ingest/commit`

**Garden path differences:** None.

---

#### Step 5: Confirm

**Layout:**
- Progress bar at top: "Registering 8 of 8 tools..."
- Tool list with real-time status:
  - Pending: gray spinner
  - Success (no auth required): green checkmark + tool name
  - Registered disabled (auth required): yellow lock icon + tool name + "Disabled — credentials required"
  - Failed: red X + tool name + expandable error message
- Overall status card:
  - All enabled: "8 tools registered and enabled!"
  - Some disabled: "5 tools enabled, 3 disabled pending credentials" with info badge
  - All disabled: "8 tools registered but disabled — credentials required for this API"
  - Partial failure: "7 of 8 tools registered" with warning badge

**Navigation:**
- "Done" button — navigates back to the Tools management screen
- If errors occurred, "Retry Failed" button re-attempts only the failed tools

**Garden path differences:** None.

---

### Tool Management Screen

The main Tools settings page (`/settings/tools`) shows all registered tools organized by their source. This is the landing page for tool management after ingestion.

#### Layout

**Header:**
- "Tools" title
- "Add API" button (primary action) — navigates to ingestion wizard

**Built-in Tools section (always present):**
- Collapsible section: "Built-in Tools — 3 tools"
- Table rows: name, description, enabled toggle
- These cannot be deleted or reconfigured (they come from the codebase)

**API Sources (one section per ingested source):**
- Each source is a collapsible section with a header:
  - Source icon/color badge (assigned per source for visual distinction)
  - Source name (e.g., "Weather API")
  - Base URL (truncated, tooltip for full URL)
  - Tool count badge (e.g., "8 tools")
  - "Refresh" button (disabled if source has no `spec_url`)
  - "Delete Source" button (with confirmation dialog: "Delete Weather API and all 8 tools?")
- Inside each source section:
  - Auth status line:
    - No auth required: "No authentication required" with success badge
    - Auth required: "Requires API Key (header)" with warning badge + "Tools are disabled until credentials are configured"
  - Table of tools with columns:
    - Tool name (e.g., `weather_api__get_current`)
    - Method badge (GET/POST/etc.)
    - Path (e.g., `/current`)
    - Enabled toggle (disabled/locked if auth is required and credentials not yet configured — tooltip: "Configure credentials for this API to enable tools")
    - Delete button (individual tool, with confirmation)
  - Clicking a tool row expands to show:
    - Full description
    - Argument schema (rendered as a readable parameter table)
    - Auth type
    - Source provenance: "From Weather API, registered Jun 8, 2026"

#### Refresh Flow

When the user clicks "Refresh" on an API source:

1. System re-fetches the spec (or prompts re-upload if no `spec_url`)
2. Loading state: "Checking for changes..."
3. **Diff display** — a modal or sub-page showing three categories:
   - **Unchanged** (N tools) — collapsed, no action needed
   - **Modified** (N tools) — expanded, showing a side-by-side diff of parameter changes. Each tool has "Keep Existing" / "Update" radio buttons
   - **New** (N tools) — table with checkboxes, same selection UX as wizard Step 3
   - **Removed** (N tools) — "These operations no longer exist in the API spec." Each has "Disable" / "Keep" / "Delete" radio buttons
4. "Apply Changes" commits the diff
5. Progress display similar to wizard Step 5

#### Update Credentials

Clicking "Update Credentials" on a source opens a dialog with the same credential input form as wizard Step 2, pre-populated with a masked version of the current value (if the credential store supports it) or empty inputs. The new value overwrites the stored credential.

#### Empty State

If no API sources exist, the page shows:
- "No external APIs registered yet."
- Brief description: "Connect external APIs to expand your agent's capabilities. Provide an API endpoint and Moira will discover available operations."
- "Add API" button (prominent, centered)

## Error Handling

### Spec Fetch Failures

- Network errors → return clear error to user with the URL that failed
- Invalid spec (parse error) → return parse error with details
- No spec found at probed paths → suggest providing spec URL or uploading file

### Auth-Required APIs at Runtime

- Tools registered as `enabled=false` for auth-required APIs are not included in the active tool set during research runs. The LLM never sees them.
- When credentials are eventually wired up (future phase), enabling the tools makes them available immediately — no re-ingestion needed.

### Partial Provisioning Failures

If some tools in a batch fail (name collision, schema too complex), the `/commit` endpoint returns partial success with a list of succeeded and failed tools. Failed tools include a reason.

## Files to Create

| File | Description |
|---|---|
| `backend/moira/services/tool_ingestion/__init__.py` | Package init |
| `backend/moira/services/tool_ingestion/spec_parser.py` | OpenAPI/Swagger spec → ToolCandidate list |
| `backend/moira/services/tool_ingestion/spec_resolver.py` | Fetch spec from URL, probe paths, accept uploads |
| `backend/moira/services/tool_ingestion/auth_analyzer.py` | Extract and classify security requirements |
| `backend/moira/services/tool_ingestion/tool_provisioner.py` | Orchestrate parsing → catalog registration → embeddings |
| `backend/moira/persistence/sqlite/migrations/013_api_sources.sql` | `api_sources` table |
| `frontend/src/components/SettingsTools.vue` | Tools settings page |
| `frontend/src/components/ToolIngestWizard.vue` | Full-page multi-step ingestion wizard |

## Files to Modify

| File | Change |
|---|---|
| `backend/moira/persistence/sqlite/schema.py` | Version bump to 13 |
| `backend/moira/persistence/interfaces.py` | `ApiSourceRepository` ABC, new dataclasses |
| `backend/moira/persistence/sqlite/repos.py` | `SqliteApiSourceRepository` |
| `backend/moira/tools/rest_tool.py` | Path parameter resolution, query/body separation |
| `backend/moira/tools/executor.py` | Register RESTTool instances for ingested tools |
| `backend/moira/service_setup.py` | Register new services |
| `backend/moira/api/router.py` | New ingestion endpoints |
| `frontend/src/api/client.ts` | Ingestion API methods and types |
| `frontend/src/components/SettingsSidebar.vue` | Add "Tools" menu item |
| `backend/pyproject.toml` | Add `prance` dependency |

## Implementation Order

### Phase 1: Backend Foundation
1. Add `prance` dependency
2. Create migration 013 (`api_sources` table)
3. Build `spec_parser.py` — parse spec → ToolCandidate list
4. Build `spec_resolver.py` — fetch/probe/upload logic
5. Build `auth_analyzer.py` — security scheme extraction

### Phase 2: RESTTool Enhancement
6. Enhance `RESTTool` with path parameter resolution, query/body separation
7. Update `ToolExecutor` to register RESTTool instances for ingested tools

### Phase 3: Provisioning Pipeline
9. Build `tool_provisioner.py` — end-to-end orchestration
10. Build `ApiSourceRepository` — persist API source metadata
11. Register all new services in `service_setup.py`

### Phase 4: API Endpoints
12. Add ingestion endpoints to `router.py`
13. Add tests for parser, resolver, provisioner, and API endpoints

### Phase 5: Frontend
14. Add ingestion API types and methods to `client.ts`
15. Build `ToolIngestWizard.vue` — full-page multi-step wizard
16. Build `SettingsTools.vue` — tools management page
17. Add "Tools" to settings sidebar

## Open Questions

1. **Should ingested tools be re-embedded when the embedding model changes?** The existing `ToolDiscovery` handles this — tools are embedded when ingested. If the model changes, all tools need re-embedding regardless. Not specific to this feature.

2. **Rate limiting for external API calls?** Not in scope. The tool executor has timeout and retry. Per-tool rate limiting can be added to `RESTTool` config if needed.

3. **Should ingested tools survive database reset?** No more than any other data. The source `spec_url` is stored, so re-provisioning is possible after a reset if the external API is still available. A future "export/import" feature could help.

4. **File upload size limit?** Specs can be large. Set a reasonable upload limit (e.g., 10MB) and reject larger files with a suggestion to provide a URL instead.

## Integration Test APIs

The following public APIs exercise the full range of capabilities that dynamic tool discovery must handle. All are free, require no authentication, and are suitable for automated integration tests.

### Open-Meteo

- **URL**: `https://open-meteo.com/` (spec discoverable via probe)
- **Characteristics**: Search endpoints, query parameters, nested response schemas, optional parameters with defaults
- **Exercises**: Argument schema generation for optional params, response parsing, query parameter routing

### PokeAPI

- **URL**: `https://pokeapi.co/api/v2/` (spec not served by the API — use path B with spec URL `https://raw.githubusercontent.com/PokeAPI/pokeapi/refs/heads/master/openapi.yml`)
- **Characteristics**: 50+ endpoints, path parameters (`{id}` and `{name}`), pagination (`?limit=20&offset=0`), large response bodies, related resource links
- **Exercises**: Large operation count handling, path parameter resolution, pagination, tool naming for many similar endpoints

### TVMaze API

- **URL**: `https://api.tvmaze.com/` (no official OpenAPI spec — use path B: provide spec URL or file)
- **Characteristics**: Search endpoints with query parameters, path parameters for detail lookups, embedded/nested objects in responses
- **Exercises**: Tool chaining (search → detail lookup), nested response schemas, query parameter routing

### Open Brewery DB

- **URL**: `https://api.openbrewerydb.org/` (no official OpenAPI spec — use path B or D)
- **Characteristics**: Simple REST endpoints, query parameters for filtering, pagination, search
- **Exercises**: Basic CRUD-style endpoints, query-only parameters, pagination patterns

### The Met Collection API

- **URL**: `https://collectionapi.metmuseum.org/public/collection/v1/` (no official OpenAPI spec — use path B or D)
- **Characteristics**: Search endpoint returning IDs, detail lookup by object ID, large JSON responses with nested objects and arrays, optional fields that may be null
- **Exercises**: Tool chaining (search → detail), handling large responses, nullable/optional response fields, path parameter resolution

### What These APIs Exercise Together

| Capability         | Open-Meteo | PokeAPI | TVMaze | Brewery DB | Met |
|--------------------|------------|---------|--------|------------|-----|
| Path parameters    |            | x       | x      |            | x   |
| Query parameters   | x          | x       | x      | x          | x   |
| Pagination         |            | x       | x      | x          |     |
| Nested schemas     | x          | x       | x      |            | x   |
| Large responses    |            | x       |        |            | x   |
| Tool chaining      |            |         | x      |            | x   |
| Optional params    | x          | x       | x      | x          | x   |
| Many endpoints     |            | x       |        |            |     |
| Spec auto-discover | x          |         |        |            |     |
