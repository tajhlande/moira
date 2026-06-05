from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from moira.tools.base import ToolDefinition

DEFAULT_USER_ID = "default"


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
    thread_id: str
    tool_executions: list = field(default_factory=list)
    report: dict | None = None
    budget_limit: float = 0.0
    budget_consumed: float = 0.0
    error: str = ""
    status: str = "running"
    started_at: str = ""
    completed_at: str = ""
    total_elapsed_ms: int = 0


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
    purpose: str | None = None
    model: str | None = None
    call_count: int | None = None
    input_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None
    prompt_time_ms: float | None = None
    gen_time_ms: float | None = None
    error: str = ""
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
    async def delete_conversation(self, conversation_id: str) -> bool: ...

    @abstractmethod
    async def cleanup_stale_runs(self) -> int: ...


class ModelPreferencesRepository(ABC):
    @abstractmethod
    async def get_preferences(self, user_id: str) -> ModelPreferences: ...

    @abstractmethod
    async def set_preferences(self, preferences: ModelPreferences) -> None: ...


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
