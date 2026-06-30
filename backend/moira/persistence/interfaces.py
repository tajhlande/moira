from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from moira.tools.base import ToolDefinition

DEFAULT_USER_ID = "default"

SCOPE_SYSTEM = "system"
SCOPE_USER = "user"
SCOPE_PROJECT = "project"
SCOPE_CONVERSATION = "conversation"
SYSTEM_SCOPE_ID = "_system"


@dataclass
class Message:
    id: int | None
    conversation_id: str
    role: str
    content: str
    created_at: str


@dataclass
class Conversation:
    id: str
    user_id: str
    title: str
    created_at: str


@dataclass
class ModelPreferences:
    user_id: str
    intelligence_endpoint: str = ""
    intelligence_model: str = ""
    task_endpoint: str = ""
    task_model: str = ""


@dataclass
class WorkflowRun:
    id: str
    conversation_id: str
    user_message_id: int
    status: str = "running"
    budget_limit: float = 0.0
    total_cost: float = 0.0
    generation_reason: str = ""
    started_at: str = ""
    completed_at: str | None = ""
    total_elapsed_ms: int | None = 0
    updated_at: str = ""
    knowledge_snapshot: str = ""
    state_version: int = 1
    # Legacy fields preserved for migration compatibility
    thread_id: str = ""
    tool_executions: list = field(default_factory=list)
    report: dict | None = None
    budget_consumed: float = 0.0
    error: str = ""


@dataclass
class WorkflowStep:
    """One row per graph node invocation. Carries execution metadata
    (node name, status, budget, timing) and inference metadata (model,
    purpose, token counts, provider timing) when the node called the
    inference provider."""

    id: int | None
    workflow_run_id: str
    node_name: str
    label: str
    status: str
    cost: float
    budget_remaining: float
    started_at: str
    elapsed_ms: int
    tool_call_cost: float = 0.0
    purpose: str | None = None
    model: str | None = None
    call_count: int | None = None
    input_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None
    prompt_time_ms: float | None = None
    gen_time_ms: float | None = None
    error: str = ""
    step_version: int = 1
    tool_call_count: int = 0
    detail: dict | None = None


class WorkflowStepRepository(ABC):
    @abstractmethod
    async def save_step(self, step: WorkflowStep) -> int: ...

    @abstractmethod
    async def get_steps_for_run(self, workflow_run_id: str) -> list[WorkflowStep]: ...


@dataclass
class CredentialRow:
    owner: str
    name: str
    encrypted_data: str
    salt: str
    encryption_version: int
    created_at: str
    updated_at: str


class ConversationRepository(ABC):
    @abstractmethod
    async def create_conversation(self, user_id: str, title: str) -> Conversation: ...

    @abstractmethod
    async def get_conversation(self, conversation_id: str) -> Conversation | None: ...

    @abstractmethod
    async def list_conversations(self, user_id: str) -> list[Conversation]: ...

    @abstractmethod
    async def update_conversation(
        self, conversation_id: str, title: str
    ) -> Conversation | None: ...

    @abstractmethod
    async def insert_message(self, conversation_id: str, role: str, content: str) -> Message: ...

    @abstractmethod
    async def get_messages(self, conversation_id: str) -> list[Message]: ...

    @abstractmethod
    async def save_workflow_run(self, run: WorkflowRun) -> None: ...

    @abstractmethod
    async def get_workflow_runs(self, conversation_id: str) -> list[WorkflowRun]: ...

    @abstractmethod
    async def get_workflow_run(self, run_id: str) -> WorkflowRun | None: ...

    @abstractmethod
    async def delete_conversation(self, conversation_id: str) -> bool: ...

    @abstractmethod
    async def truncate_from_message(self, conversation_id: str, user_message_id: int) -> bool:
        """Delete the workflow run for the given user message and all messages
        (and their runs) with id >= user_message_id. Keeps the user message
        itself so it can be re-submitted. Returns True if anything was deleted."""
        ...

    @abstractmethod
    async def cleanup_stale_runs(self) -> int: ...


class ModelPreferencesRepository(ABC):
    @abstractmethod
    async def get_preferences(self, user_id: str) -> ModelPreferences: ...

    @abstractmethod
    async def set_preferences(self, preferences: ModelPreferences) -> None: ...


@dataclass
class InferenceProvider:
    """A configured inference provider (OpenAI-compatible endpoint).

    API keys are stored encrypted in the credentials table, referenced
    by credential_name. This dataclass holds only connection metadata.

    slug is a kebab-case identifier derived from display_name at creation
    time and is immutable thereafter. display_name is user-editable."""

    slug: str
    display_name: str
    base_url: str
    provider_type: str = "completions"
    credential_name: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class InferenceModelRow:
    """A discovered model on a provider, with user-configurable capability flags."""

    provider_slug: str
    model_id: str
    native_tool_calling: bool = False
    discovered_at: str = ""


class InferenceProviderRepository(ABC):
    """Persistence interface for inference_providers + inference_models tables."""

    @abstractmethod
    async def get_all_providers(self) -> list[InferenceProvider]: ...

    @abstractmethod
    async def get_provider(self, slug: str) -> InferenceProvider | None: ...

    @abstractmethod
    async def upsert_provider(self, provider: InferenceProvider) -> None: ...

    @abstractmethod
    async def delete_provider(self, slug: str) -> bool: ...

    @abstractmethod
    async def get_models(self, provider_slug: str) -> list[InferenceModelRow]: ...

    @abstractmethod
    async def get_all_models(self) -> list[InferenceModelRow]: ...

    @abstractmethod
    async def upsert_model(
        self, provider_slug: str, model_id: str, native_tool_calling: bool
    ) -> None: ...

    @abstractmethod
    async def upsert_discovered_models(self, provider_slug: str, model_ids: list[str]) -> None: ...

    @abstractmethod
    async def delete_stale_models(
        self, provider_slug: str, current_model_ids: list[str]
    ) -> None: ...


@dataclass
class ConversationModelOverride:
    """Per-conversation intelligence model override.

    When a row exists, its endpoint + model are used instead of the
    global default from model_preferences. Only intelligence model is
    stored here — task model is always global."""

    conversation_id: str
    intelligence_endpoint: str
    intelligence_model: str
    updated_at: str = ""


class ConversationModelRepository(ABC):
    """Persistence interface for the conversation_models table."""

    @abstractmethod
    async def get_override(self, conversation_id: str) -> ConversationModelOverride | None: ...

    @abstractmethod
    async def upsert_override(self, override: ConversationModelOverride) -> None: ...

    @abstractmethod
    async def delete_override(self, conversation_id: str) -> bool: ...


class ToolRepository(ABC):
    @abstractmethod
    async def get_all_tools(self) -> list[ToolDefinition]: ...

    @abstractmethod
    async def get_tool(self, name: str) -> ToolDefinition | None: ...

    @abstractmethod
    async def save_tool(self, tool: ToolDefinition) -> None: ...

    @abstractmethod
    async def delete_tool(self, name: str) -> bool: ...

    @abstractmethod
    async def set_enabled(self, name: str, enabled: bool) -> bool: ...

    @abstractmethod
    async def get_all_groups(self) -> list: ...

    @abstractmethod
    async def save_group(self, group) -> None: ...

    @abstractmethod
    async def delete_group(self, name: str) -> bool: ...


class CredentialRepository(ABC):
    @abstractmethod
    async def get_by_name(self, owner: str, name: str) -> CredentialRow | None: ...

    @abstractmethod
    async def save(
        self,
        owner: str,
        name: str,
        encrypted_data: str,
        salt: str,
        encryption_version: int,
    ) -> None: ...

    @abstractmethod
    async def delete(self, owner: str, name: str) -> bool: ...

    @abstractmethod
    async def list_all(self, owner: str | None = None) -> list[CredentialRow]: ...


@dataclass
class SettingEntry:
    """A single persisted setting row. Maps directly to the settings table.

    Used by the repository layer for raw DB reads/writes and passed through
    the service layer. Value is always a string; type information lives in
    the SettingDefinition registry, not here."""

    key: str
    value: str
    scope: str
    scope_id: str


@dataclass
class ResolvedSetting:
    """A setting value after scope-chain resolution, enriched with type info.

    Returned by SettingsService.get() for API consumers. Carries which scope
    layer actually resolved the value (useful when multiple scopes are
    searched in precedence order). Type comes from SettingDefinition, not
    from the DB."""

    key: str
    value: str
    type: str
    scope: str
    scope_id: str


class SystemSettingsRepository(ABC):
    """Persistence interface for the settings table.

    Phase 1 only supports single-scope reads. Multi-scope resolution
    (conversation > project > user > system) is handled by SettingsService,
    not by the repo. The repo is scope-unaware beyond filtering rows by
    (scope, scope_id)."""

    @abstractmethod
    async def get(self, key: str, scope: str, scope_id: str) -> SettingEntry | None: ...

    @abstractmethod
    async def get_prefix(self, prefix: str, scope: str, scope_id: str) -> list[SettingEntry]: ...

    @abstractmethod
    async def set(self, entry: SettingEntry) -> None: ...

    @abstractmethod
    async def set_batch(self, entries: list[SettingEntry]) -> None: ...

    @abstractmethod
    async def delete(self, key: str, scope: str, scope_id: str) -> bool: ...


# --- Tool Ingestion: intermediate representations ---


@dataclass
class ParameterSpec:
    """A single parameter extracted from an OpenAPI operation."""

    name: str
    location: str  # "query", "path", "header", "cookie"
    required: bool
    schema_def: dict  # JSON Schema for this parameter
    description: str


@dataclass
class RequestBodySpec:
    """The request body schema extracted from an OpenAPI operation."""

    content_type: str  # e.g., "application/json"
    schema_def: dict  # JSON Schema for request body
    required: bool
    description: str


@dataclass
class SecuritySchemeInfo:
    """A security scheme extracted from an OpenAPI spec's components."""

    scheme_type: str  # "api_key_header", "api_key_query", "bearer", "basic", "none"
    name: str  # Header name, query param name, etc.
    location: str  # "header", "query" — for API key types
    description: str


@dataclass
class ToolCandidate:
    """Intermediate representation of a tool extracted from an API spec.
    Presented to the user for selection before provisioning."""

    name: str  # Generated tool name (e.g., "weather_api__get_current")
    description: str  # From operation.summary + description
    method: str  # GET, POST, etc.
    path: str  # e.g., "/weather/current"
    parameters: list[ParameterSpec]  # Path, query, header parameters
    request_body: RequestBodySpec | None
    responses: dict[str, str]  # Status code → description
    security_requirements: list[str]  # Names of required security schemes
    tags: list[str]  # From the spec's tags field
    operation_id: str | None  # Original operationId from spec
    deprecated: bool


@dataclass
class ParsedSpec:
    """The result of parsing an OpenAPI or Swagger spec."""

    title: str
    description: str
    version: str  # "openapi_3_0", "openapi_3_1", "swagger_2"
    server_urls: list[str]
    security_schemes: dict[str, SecuritySchemeInfo]
    operations: list[ToolCandidate]
    spec_url: str | None  # For re-fetch


@dataclass
class ApiSource:
    """Row from the api_sources table. Tracks an ingested external API."""

    id: str
    name: str
    base_url: str
    spec_url: str | None
    spec_format: str  # "openapi_3_0", "openapi_3_1", "swagger_2"
    auth_type: str | None  # "api_key_header", "bearer", "basic", "none", None
    group_name: str
    tool_count: int
    enabled: bool
    created_at: str
    updated_at: str


class ApiSourceRepository(ABC):
    """Persistence interface for the api_sources table."""

    @abstractmethod
    async def get(self, source_id: str) -> ApiSource | None: ...

    @abstractmethod
    async def get_all(self) -> list[ApiSource]: ...

    @abstractmethod
    async def save(self, source: ApiSource) -> None: ...

    @abstractmethod
    async def update_tool_count(self, source_id: str, tool_count: int) -> None: ...

    @abstractmethod
    async def delete(self, source_id: str) -> bool: ...
