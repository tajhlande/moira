from unittest.mock import AsyncMock

import pytest

from moira.config import InferenceConfig, InferenceEndpointConfig, InferenceModelsConfig
from moira.inference.client import InferenceClient
from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import ModelPreferences, ModelPreferencesRepository


class FakePrefsRepo(ModelPreferencesRepository):
    def __init__(self):
        self._prefs = ModelPreferences(user_id="default")

    async def get_preferences(self, user_id: str) -> ModelPreferences:
        return self._prefs

    async def set_preferences(self, preferences: ModelPreferences) -> None:
        self._prefs = preferences


@pytest.mark.asyncio
async def test_model_registry_resolve_from_prefs():
    config = InferenceConfig(
        endpoints=[InferenceEndpointConfig(name="local", base_url="http://localhost:8080/v1")],
        models=InferenceModelsConfig(
            intelligence_endpoint="local",
            intelligence_model="llama3:70b",
            task_endpoint="local",
            task_model="llama3:3b",
        ),
    )
    prefs = FakePrefsRepo()
    registry = ModelRegistry(config=config, prefs_repo=prefs, user_id="default")

    from moira.inference.client import ModelInfo

    registry._available_models = [
        ModelInfo(id="llama3:70b", source_endpoint="local"),
        ModelInfo(id="llama3:3b", source_endpoint="local"),
    ]
    registry.add_client("local", AsyncMock(spec=InferenceClient))

    resolved = await registry.resolve("intelligence")
    assert resolved.model_id == "llama3:70b"

    resolved = await registry.resolve("task")
    assert resolved.model_id == "llama3:3b"


@pytest.mark.asyncio
async def test_model_registry_resolve_from_db_prefs():
    config = InferenceConfig(
        endpoints=[InferenceEndpointConfig(name="local", base_url="http://localhost:8080/v1")],
        models=InferenceModelsConfig(),
    )
    prefs = FakePrefsRepo()
    prefs._prefs = ModelPreferences(
        user_id="default",
        intelligence_endpoint="local",
        intelligence_model="custom-model",
        task_endpoint="local",
        task_model="tiny-model",
    )
    registry = ModelRegistry(config=config, prefs_repo=prefs, user_id="default")

    from moira.inference.client import ModelInfo

    registry._available_models = [
        ModelInfo(id="custom-model", source_endpoint="local"),
        ModelInfo(id="tiny-model", source_endpoint="local"),
    ]
    registry.add_client("local", AsyncMock(spec=InferenceClient))

    resolved = await registry.resolve("intelligence")
    assert resolved.model_id == "custom-model"


@pytest.mark.asyncio
async def test_model_registry_no_assignment_raises():
    config = InferenceConfig(
        endpoints=[],
        models=InferenceModelsConfig(),
    )
    prefs = FakePrefsRepo()
    registry = ModelRegistry(config=config, prefs_repo=prefs, user_id="default")

    with pytest.raises(ValueError, match="No intelligence model assigned"):
        await registry.resolve("intelligence")


@pytest.mark.asyncio
async def test_model_registry_set_assignments():
    config = InferenceConfig(
        endpoints=[],
        models=InferenceModelsConfig(),
    )
    prefs = FakePrefsRepo()
    registry = ModelRegistry(config=config, prefs_repo=prefs, user_id="default")

    await registry.set_assignments(
        intelligence_endpoint="ep-a",
        intelligence_model="model-x",
        task_endpoint="ep-b",
        task_model="model-y",
    )
    assert prefs._prefs.intelligence_endpoint == "ep-a"
    assert prefs._prefs.intelligence_model == "model-x"
    assert prefs._prefs.task_endpoint == "ep-b"
    assert prefs._prefs.task_model == "model-y"


@pytest.mark.asyncio
async def test_model_registry_resolve_different_endpoints():
    config = InferenceConfig(
        endpoints=[
            InferenceEndpointConfig(name="local", base_url="http://localhost:8080/v1"),
            InferenceEndpointConfig(name="cloud", base_url="http://cloud:8080/v1"),
        ],
        models=InferenceModelsConfig(),
    )
    prefs = FakePrefsRepo()
    prefs._prefs = ModelPreferences(
        user_id="default",
        intelligence_endpoint="local",
        intelligence_model="llama3:70b",
        task_endpoint="cloud",
        task_model="gpt-4o-mini",
    )
    registry = ModelRegistry(config=config, prefs_repo=prefs, user_id="default")

    from moira.inference.client import ModelInfo

    local_client = AsyncMock(spec=InferenceClient)
    cloud_client = AsyncMock(spec=InferenceClient)
    registry._available_models = [
        ModelInfo(id="llama3:70b", source_endpoint="local"),
        ModelInfo(id="gpt-4o-mini", source_endpoint="cloud"),
    ]
    registry.add_client("local", local_client)
    registry.add_client("cloud", cloud_client)

    resolved = await registry.resolve("intelligence")
    assert resolved.model_id == "llama3:70b"
    assert resolved.client is local_client

    resolved = await registry.resolve("task")
    assert resolved.model_id == "gpt-4o-mini"
    assert resolved.client is cloud_client
