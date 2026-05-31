from abc import ABC, abstractmethod
from dataclasses import dataclass, field

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
    execution_steps: list = field(default_factory=list)
    tool_executions: list = field(default_factory=list)
    verification_attempts: list = field(default_factory=list)
    report: dict | None = None
    budget_limit: float = 0.0
    budget_consumed: float = 0.0
    error: str = ""
    status: str = "running"
    started_at: str = ""
    completed_at: str = ""
    total_elapsed_ms: int = 0


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


class ModelPreferencesRepository(ABC):
    @abstractmethod
    async def get_preferences(self, user_id: str) -> ModelPreferences: ...

    @abstractmethod
    async def set_preferences(self, preferences: ModelPreferences) -> None: ...


class ToolRepository(ABC):
    @abstractmethod
    async def get_all_tools(self) -> list: ...

    @abstractmethod
    async def get_tool(self, name: str): ...

    @abstractmethod
    async def save_tool(self, tool) -> None: ...

    @abstractmethod
    async def delete_tool(self, name: str) -> bool: ...

    @abstractmethod
    async def set_enabled(self, name: str, enabled: bool) -> bool: ...

    @abstractmethod
    async def get_all_groups(self) -> list: ...

    @abstractmethod
    async def save_group(self, group) -> None: ...
