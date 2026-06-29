from unittest.mock import AsyncMock

import pytest

from moira.inference.client import InferenceClient
from moira.inference.registry import ModelRegistry, _provider_slug
from moira.persistence.interfaces import (
    InferenceModelRow,
    InferenceProvider,
    InferenceProviderRepository,
    ModelPreferences,
    ModelPreferencesRepository,
)


class FakePrefsRepo(ModelPreferencesRepository):
    def __init__(self):
        self._prefs = ModelPreferences(user_id="default")

    async def get_preferences(self, user_id: str) -> ModelPreferences:
        return self._prefs

    async def set_preferences(self, preferences: ModelPreferences) -> None:
        self._prefs = preferences


class FakeProviderRepo(InferenceProviderRepository):
    def __init__(self):
        self._providers: dict[str, InferenceProvider] = {}
        self._models: dict[tuple[str, str], InferenceModelRow] = {}

    async def get_all_providers(self) -> list[InferenceProvider]:
        return list(self._providers.values())

    async def get_provider(self, slug: str) -> InferenceProvider | None:
        return self._providers.get(slug)

    async def upsert_provider(self, provider: InferenceProvider) -> None:
        self._providers[provider.slug] = provider

    async def delete_provider(self, slug: str) -> bool:
        if slug in self._providers:
            del self._providers[slug]
            for key in list(self._models.keys()):
                if key[0] == slug:
                    del self._models[key]
            return True
        return False

    async def get_models(self, provider_slug: str) -> list[InferenceModelRow]:
        return [m for (ps, _), m in self._models.items() if ps == provider_slug]

    async def get_all_models(self) -> list[InferenceModelRow]:
        return list(self._models.values())

    async def upsert_model(
        self, provider_slug: str, model_id: str, native_tool_calling: bool
    ) -> None:
        self._models[(provider_slug, model_id)] = InferenceModelRow(
            provider_slug=provider_slug,
            model_id=model_id,
            native_tool_calling=native_tool_calling,
        )

    async def upsert_discovered_models(self, provider_slug: str, model_ids: list[str]) -> None:
        for mid in model_ids:
            if (provider_slug, mid) not in self._models:
                self._models[(provider_slug, mid)] = InferenceModelRow(
                    provider_slug=provider_slug,
                    model_id=mid,
                    native_tool_calling=False,
                )

    async def delete_stale_models(self, provider_slug: str, current_model_ids: list[str]) -> None:
        current = set(current_model_ids)
        for key in list(self._models.keys()):
            if key[0] == provider_slug and key[1] not in current:
                del self._models[key]


def _build_registry(
    providers: dict[str, InferenceProvider] | None = None,
    models: dict[tuple[str, str], InferenceModelRow] | None = None,
    prefs: ModelPreferences | None = None,
) -> tuple[ModelRegistry, FakeProviderRepo, FakePrefsRepo]:
    provider_repo = FakeProviderRepo()
    if providers:
        provider_repo._providers = dict(providers)
    if models:
        provider_repo._models = dict(models)

    prefs_repo = FakePrefsRepo()
    if prefs:
        prefs_repo._prefs = prefs

    registry = ModelRegistry(
        provider_repo=provider_repo,
        prefs_repo=prefs_repo,
        user_id="default",
    )
    return registry, provider_repo, prefs_repo


@pytest.mark.asyncio
async def test_resolve_from_prefs():
    providers = {
        "local-lab": InferenceProvider(
            slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
        ),
    }
    models = {
        ("local-lab", "llama3:70b"): InferenceModelRow("local-lab", "llama3:70b"),
        ("local-lab", "llama3:3b"): InferenceModelRow("local-lab", "llama3:3b"),
    }
    prefs = ModelPreferences(
        user_id="default",
        intelligence_endpoint="local-lab",
        intelligence_model="llama3:70b",
        task_endpoint="local-lab",
        task_model="llama3:3b",
    )
    registry, _, _ = _build_registry(providers, models, prefs)
    registry.add_client("local-lab", AsyncMock(spec=InferenceClient))

    resolved = await registry.resolve("intelligence")
    assert resolved.model_id == "llama3:70b"

    resolved = await registry.resolve("task")
    assert resolved.model_id == "llama3:3b"


@pytest.mark.asyncio
async def test_resolve_different_providers():
    providers = {
        "local-lab": InferenceProvider(
            slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
        ),
        "cloud": InferenceProvider(
            slug="cloud", display_name="Cloud", base_url="http://cloud:8080/v1"
        ),
    }
    models = {
        ("local-lab", "llama3:70b"): InferenceModelRow("local-lab", "llama3:70b"),
        ("cloud", "gpt-4o-mini"): InferenceModelRow("cloud", "gpt-4o-mini"),
    }
    prefs = ModelPreferences(
        user_id="default",
        intelligence_endpoint="local-lab",
        intelligence_model="llama3:70b",
        task_endpoint="cloud",
        task_model="gpt-4o-mini",
    )
    registry, _, _ = _build_registry(providers, models, prefs)
    local_client = AsyncMock(spec=InferenceClient)
    cloud_client = AsyncMock(spec=InferenceClient)
    registry.add_client("local-lab", local_client)
    registry.add_client("cloud", cloud_client)

    resolved = await registry.resolve("intelligence")
    assert resolved.model_id == "llama3:70b"
    assert resolved.client is local_client

    resolved = await registry.resolve("task")
    assert resolved.model_id == "gpt-4o-mini"
    assert resolved.client is cloud_client


@pytest.mark.asyncio
async def test_no_assignment_raises():
    registry, _, _ = _build_registry()
    with pytest.raises(ValueError, match="No intelligence model assigned"):
        await registry.resolve("intelligence")


@pytest.mark.asyncio
async def test_set_assignments():
    registry, _, prefs_repo = _build_registry()
    await registry.set_assignments(
        intelligence_endpoint="ep-a",
        intelligence_model="model-x",
        task_endpoint="ep-b",
        task_model="model-y",
    )
    assert prefs_repo._prefs.intelligence_endpoint == "ep-a"
    assert prefs_repo._prefs.intelligence_model == "model-x"
    assert prefs_repo._prefs.task_endpoint == "ep-b"
    assert prefs_repo._prefs.task_model == "model-y"


@pytest.mark.asyncio
async def test_resolve_reads_capability_flags_from_db():
    providers = {
        "local-lab": InferenceProvider(
            slug="local-lab",
            display_name="Local Lab",
            base_url="http://localhost:8080/v1",
            provider_type="completions",
        ),
    }
    models = {
        ("local-lab", "tool-model"): InferenceModelRow(
            "local-lab", "tool-model", native_tool_calling=True
        ),
        ("local-lab", "plain-model"): InferenceModelRow(
            "local-lab", "plain-model", native_tool_calling=False
        ),
    }
    prefs = ModelPreferences(
        user_id="default",
        intelligence_endpoint="local-lab",
        intelligence_model="tool-model",
        task_endpoint="local-lab",
        task_model="plain-model",
    )
    registry, _, _ = _build_registry(providers, models, prefs)
    registry.add_client("local-lab", AsyncMock(spec=InferenceClient))

    resolved = await registry.resolve("intelligence")
    assert resolved.native_tool_calling is True
    assert resolved.provider_type == "completions"

    resolved = await registry.resolve("task")
    assert resolved.native_tool_calling is False


@pytest.mark.asyncio
async def test_set_model_capability():
    providers = {
        "local-lab": InferenceProvider(
            slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
        ),
    }
    models = {
        ("local-lab", "test-model"): InferenceModelRow(
            "local-lab", "test-model", native_tool_calling=False
        ),
    }
    registry, provider_repo, _ = _build_registry(providers, models)
    await registry.set_model_capability("local-lab", "test-model", True)

    db_models = await provider_repo.get_models("local-lab")
    assert db_models[0].native_tool_calling is True


@pytest.mark.asyncio
async def test_resolve_api_key_from_env(monkeypatch):
    # Slugs use underscores in env var names: local-lab -> MOIRA_API_KEY_LOCAL_LAB
    monkeypatch.setenv("MOIRA_API_KEY_LOCAL_LAB", "secret-key-123")
    registry, _, _ = _build_registry()
    key = await registry.resolve_api_key("local-lab", "")
    assert key == "secret-key-123"


@pytest.mark.asyncio
async def test_resolve_api_key_empty_when_no_source(monkeypatch):
    monkeypatch.delenv("MOIRA_API_KEY_LOCAL_LAB", raising=False)
    registry, _, _ = _build_registry()
    key = await registry.resolve_api_key("local-lab", "")
    assert key == ""


class TestProviderSlug:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("OpenRouter", "open-router"),
            ("Local", "local"),
            ("My Provider", "my-provider"),
            ("API-Key", "api-key"),
            ("openai", "openai"),
            ("Qwen3.6", "qwen3-6"),
            ("TestModel_ABC", "test-model-abc"),
            ("Ollama2", "ollama2"),
            ("vLLM", "v-llm"),
            ("Local Lab", "local-lab"),
            ("123API", "p123-api"),
            ("   ", "provider"),
            ("!!!", "provider"),
        ],
    )
    def test_slug(self, name, expected):
        assert _provider_slug(name) == expected

    def test_slug_always_kebab_case(self):
        """Slug must only contain [a-z0-9-] — no underscores, no uppercase."""
        import re

        pattern = re.compile(r"^[a-z0-9-]+$")
        for name in ["OpenRouter", "My Cool API", "weird@#$name", "123Start", "Test_Model"]:
            slug = _provider_slug(name)
            assert pattern.match(slug), f"Slug '{slug}' for '{name}' failed pattern"
            assert "--" not in slug, f"Slug '{slug}' has consecutive hyphens"
            assert not slug.startswith("-"), f"Slug '{slug}' starts with hyphen"
            assert not slug.endswith("-"), f"Slug '{slug}' ends with hyphen"
