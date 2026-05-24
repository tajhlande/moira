from abc import ABC, abstractmethod
from dataclasses import dataclass

DEFAULT_USER_ID = "default"


@dataclass
class Message:
    id: int | None
    session_id: str
    role: str
    content: str
    created_at: str


@dataclass
class Session:
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


class SessionRepository(ABC):
    @abstractmethod
    async def create_session(self, user_id: str, title: str) -> Session: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> Session | None: ...

    @abstractmethod
    async def list_sessions(self, user_id: str) -> list[Session]: ...

    @abstractmethod
    async def insert_message(self, session_id: str, role: str, content: str) -> Message: ...

    @abstractmethod
    async def get_messages(self, session_id: str) -> list[Message]: ...


class ModelPreferencesRepository(ABC):
    @abstractmethod
    async def get_preferences(self, user_id: str) -> ModelPreferences: ...

    @abstractmethod
    async def set_preferences(self, preferences: ModelPreferences) -> None: ...
