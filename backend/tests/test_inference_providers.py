"""Tests for SqliteInferenceProviderRepository.

Tests CRUD operations on inference_providers and inference_models tables,
including model discovery persistence (upsert without clobbering capability
flags) and stale model cleanup."""

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from moira.persistence.interfaces import InferenceProvider
from moira.persistence.sqlite.repos.inference_providers import (
    SqliteInferenceProviderRepository,
)
from moira.persistence.sqlite.schema import run_migrations


@pytest_asyncio.fixture
async def repo():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        run_migrations(db_path)
        yield SqliteInferenceProviderRepository(db_path)


class TestProviderCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab",
                display_name="Local Lab",
                base_url="http://localhost:8080/v1",
                provider_type="completions",
                credential_name="provider.local_lab",
            )
        )
        result = await repo.get_provider("local-lab")
        assert result is not None
        assert result.slug == "local-lab"
        assert result.display_name == "Local Lab"
        assert result.base_url == "http://localhost:8080/v1"
        assert result.provider_type == "completions"
        assert result.credential_name == "provider.local_lab"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, repo):
        assert await repo.get_provider("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_all_providers(self, repo):
        await repo.upsert_provider(
            InferenceProvider(slug="cloud", display_name="Cloud", base_url="http://cloud:8080/v1")
        )
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        all_providers = await repo.get_all_providers()
        assert len(all_providers) == 2
        # Ordered by display_name
        assert all_providers[0].slug == "cloud"
        assert all_providers[1].slug == "local-lab"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://old:8080/v1"
            )
        )
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://new:8080/v1"
            )
        )
        result = await repo.get_provider("local-lab")
        assert result is not None
        assert result.base_url == "http://new:8080/v1"

    @pytest.mark.asyncio
    async def test_upsert_updates_display_name(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="My Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        result = await repo.get_provider("local-lab")
        assert result is not None
        assert result.display_name == "My Local Lab"

    @pytest.mark.asyncio
    async def test_delete_provider(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        assert await repo.delete_provider("local-lab") is True
        assert await repo.get_provider("local-lab") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, repo):
        assert await repo.delete_provider("nonexistent") is False

    @pytest.mark.asyncio
    async def test_delete_cascades_to_models(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        await repo.upsert_model("local-lab", "model-a", False)
        await repo.upsert_model("local-lab", "model-b", True)
        await repo.delete_provider("local-lab")
        models = await repo.get_models("local-lab")
        assert len(models) == 0


class TestModelCRUD:
    @pytest.mark.asyncio
    async def test_upsert_and_get_models(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        await repo.upsert_model("local-lab", "model-a", False)
        await repo.upsert_model("local-lab", "model-b", True)
        models = await repo.get_models("local-lab")
        assert len(models) == 2
        assert models[0].model_id == "model-a"
        assert models[0].native_tool_calling is False
        assert models[1].model_id == "model-b"
        assert models[1].native_tool_calling is True

    @pytest.mark.asyncio
    async def test_upsert_model_updates_capability(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        await repo.upsert_model("local-lab", "model-a", False)
        await repo.upsert_model("local-lab", "model-a", True)
        models = await repo.get_models("local-lab")
        assert len(models) == 1
        assert models[0].native_tool_calling is True

    @pytest.mark.asyncio
    async def test_get_all_models(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        await repo.upsert_provider(
            InferenceProvider(slug="cloud", display_name="Cloud", base_url="http://cloud:8080/v1")
        )
        await repo.upsert_model("local-lab", "model-a", False)
        await repo.upsert_model("cloud", "model-b", True)
        all_models = await repo.get_all_models()
        assert len(all_models) == 2

    @pytest.mark.asyncio
    async def test_discover_preserves_capability_flags(self, repo):
        """upsert_discovered_models must NOT overwrite native_tool_calling
        on existing entries — user-configured flags must survive refresh."""
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        # User configures a model with tool calling
        await repo.upsert_model("local-lab", "model-a", True)
        # Discovery runs and finds model-a again (plus a new model-b)
        await repo.upsert_discovered_models("local-lab", ["model-a", "model-b"])
        models = await repo.get_models("local-lab")
        assert len(models) == 2
        # model-a keeps its user-configured flag
        model_a = next(m for m in models if m.model_id == "model-a")
        assert model_a.native_tool_calling is True
        # model-b gets the default
        model_b = next(m for m in models if m.model_id == "model-b")
        assert model_b.native_tool_calling is False

    @pytest.mark.asyncio
    async def test_delete_stale_models(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        await repo.upsert_model("local-lab", "model-a", False)
        await repo.upsert_model("local-lab", "model-b", True)
        await repo.upsert_model("local-lab", "model-c", False)
        # Provider no longer serves model-b
        await repo.delete_stale_models("local-lab", ["model-a", "model-c"])
        models = await repo.get_models("local-lab")
        assert len(models) == 2
        ids = {m.model_id for m in models}
        assert ids == {"model-a", "model-c"}

    @pytest.mark.asyncio
    async def test_delete_stale_all_removed(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        await repo.upsert_model("local-lab", "model-a", False)
        await repo.delete_stale_models("local-lab", [])
        models = await repo.get_models("local-lab")
        assert len(models) == 0

    @pytest.mark.asyncio
    async def test_discover_empty_list(self, repo):
        await repo.upsert_provider(
            InferenceProvider(
                slug="local-lab", display_name="Local Lab", base_url="http://localhost:8080/v1"
            )
        )
        await repo.upsert_discovered_models("local-lab", [])
        models = await repo.get_models("local-lab")
        assert len(models) == 0
