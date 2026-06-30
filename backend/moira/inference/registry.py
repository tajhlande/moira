import logging
import os
import re
from dataclasses import dataclass

from moira.inference.client import InferenceClient, ModelInfo
from moira.persistence.interfaces import (
    ConversationModelRepository,
    InferenceProviderRepository,
    ModelPreferences,
    ModelPreferencesRepository,
)

logger = logging.getLogger(__name__)


def _provider_slug(name: str) -> str:
    """Convert a display name to a kebab-case slug.

    Rules:
    - camelCase boundaries get hyphenated: "OpenRouter" -> "open-router"
    - Whitespace and punctuation become hyphens
    - Lowercased
    - Consecutive hyphens collapsed
    - Leading/trailing hyphens stripped
    - Empty result falls back to "provider"
    - Leading digit gets "p" prefix (URL safety)
    """
    s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"-\1", name)
    s = re.sub(r"\s+", "-", s)
    s = s.lower()
    s = re.sub(r"[^a-z0-9-]", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    if not s:
        s = "provider"
    if s[0].isdigit():
        s = f"p{s}"
    return s


def _cred_name_from_slug(slug: str) -> str:
    """Derive a credential name from a slug.

    Credential names must match [a-zA-Z_][a-zA-Z0-9_.]* — no hyphens.
    Hyphens in the slug are replaced with underscores.
    """
    return f"provider.{slug.replace('-', '_')}"


def _env_suffix_from_slug(slug: str) -> str:
    """Derive the env var suffix from a slug.

    Hyphens are replaced with underscores and the result is uppercased,
    following the convention documented in the migration doc.
    """
    return slug.upper().replace("-", "_")


@dataclass
class ResolvedModel:
    model_id: str
    client: InferenceClient
    native_tool_calling: bool = False
    provider_type: str = "completions"


class ModelRegistry:
    """Manages inference provider clients and model resolution.

    Providers are DB-backed via InferenceProviderRepository. Clients are
    created dynamically when a provider is added/updated and stopped when
    removed. Model discovery results are persisted to inference_models so
    that capability flags survive restarts.

    Each provider is identified by a kebab-case slug derived from its
    display_name. The slug is immutable after creation.
    """

    def __init__(
        self,
        provider_repo: InferenceProviderRepository,
        prefs_repo: ModelPreferencesRepository,
        user_id: str,
        credential_service=None,
        conversation_model_repo: ConversationModelRepository | None = None,
    ):
        self._provider_repo = provider_repo
        self._prefs_repo = prefs_repo
        self._user_id = user_id
        self._credential_service = credential_service
        self._conversation_model_repo = conversation_model_repo
        self._clients: dict[str, InferenceClient] = {}
        self._available_models: list[ModelInfo] = []
        self._provider_errors: dict[str, str] = {}

    def add_client(self, slug: str, client: InferenceClient) -> None:
        """Register a pre-built client. Used at startup to load DB-persisted
        providers without going through add_provider's credential flow."""
        self._clients[slug] = client

    async def resolve_api_key(self, slug: str, credential_name: str) -> str:
        """Resolve a provider's API key.

        Resolution order:
        1. Env var ``MOIRA_API_KEY_{SLUG}`` (hyphens -> underscores, uppercased)
        2. Encrypted credential via CredentialService
        3. Empty string (some providers don't require a key)
        """
        env_key = f"MOIRA_API_KEY_{_env_suffix_from_slug(slug)}"
        api_key = os.environ.get(env_key, "")
        if api_key:
            logger.debug("Using API key from %s for provider '%s'", env_key, slug)
            return api_key

        if credential_name and self._credential_service:
            cred = await self._credential_service.get_credential(credential_name)
            if cred and isinstance(cred, dict):
                return cred.get("api_key", "")

        return ""

    async def add_provider(
        self,
        display_name: str,
        base_url: str,
        api_key: str,
        provider_type: str = "completions",
    ) -> str:
        """Create and register a new inference provider.

        Generates a kebab-case slug from display_name, ensuring uniqueness
        by appending -1, -2, etc. if needed. Stores the API key as an
        encrypted credential (when non-empty), persists the provider row,
        creates and starts an InferenceClient, and triggers model discovery.

        Returns the generated slug.
        """
        slug = _provider_slug(display_name)
        base_slug = slug
        counter = 1
        while await self._provider_repo.get_provider(slug) is not None:
            slug = f"{base_slug}-{counter}"
            counter += 1

        credential_name = ""
        if api_key:
            credential_name = _cred_name_from_slug(slug)
            if self._credential_service:
                await self._credential_service.store_credential(
                    name=credential_name,
                    value={"api_key": api_key},
                )
            else:
                logger.warning(
                    "Credential service unavailable — API key for provider '%s' "
                    "will not be persisted. Set MOIRA_SECRETS_KEY or enable "
                    "plaintext mode.",
                    slug,
                )

        from moira.persistence.interfaces import InferenceProvider

        await self._provider_repo.upsert_provider(
            InferenceProvider(
                slug=slug,
                display_name=display_name,
                base_url=base_url,
                provider_type=provider_type,
                credential_name=credential_name,
            )
        )

        client = InferenceClient(
            base_url=base_url,
            api_key=api_key,
            provider_type=provider_type,
        )
        await client.start()
        self._clients[slug] = client
        logger.info(
            "Added inference provider '%s' (slug='%s') at %s",
            display_name,
            slug,
            base_url,
        )
        await self.refresh_models(slug)
        return slug

    async def update_provider(
        self,
        slug: str,
        base_url: str,
        api_key: str,
        provider_type: str = "completions",
        display_name: str | None = None,
    ) -> None:
        """Update an existing provider's connection details.

        The slug is immutable. If display_name is provided, it updates the
        human-readable name without changing the slug. If base_url,
        provider_type, or api_key changed, the old client is stopped and
        a new one started. Model discovery re-runs for the provider.
        """
        existing = await self._provider_repo.get_provider(slug)
        if existing is None:
            raise ValueError(f"Provider '{slug}' not found")

        effective_display_name = (
            display_name if display_name is not None else existing.display_name
        )

        credential_name = existing.credential_name
        if api_key:
            if not credential_name:
                credential_name = _cred_name_from_slug(slug)
            if self._credential_service:
                await self._credential_service.store_credential(
                    name=credential_name,
                    value={"api_key": api_key},
                )

        from moira.persistence.interfaces import InferenceProvider

        await self._provider_repo.upsert_provider(
            InferenceProvider(
                slug=slug,
                display_name=effective_display_name,
                base_url=base_url,
                provider_type=provider_type,
                credential_name=credential_name,
            )
        )

        # Determine if connection details changed enough to warrant a new client
        url_changed = existing.base_url != base_url
        type_changed = existing.provider_type != provider_type

        # Resolve the effective key to check if it changed
        old_key = await self.resolve_api_key(slug, existing.credential_name)
        key_changed = old_key != api_key

        if url_changed or type_changed or key_changed:
            old_client = self._clients.pop(slug, None)
            if old_client:
                await old_client.stop()

            client = InferenceClient(
                base_url=base_url,
                api_key=api_key,
                provider_type=provider_type,
            )
            await client.start()
            self._clients[slug] = client
            logger.info("Updated inference provider '%s'", slug)
            await self.refresh_models(slug)
        else:
            logger.debug("Provider '%s' unchanged, no client restart needed", slug)

    async def remove_provider(self, slug: str) -> bool:
        """Stop and remove a provider, its client, discovered models, and
        stored credential. Returns False if the provider didn't exist."""
        existing = await self._provider_repo.get_provider(slug)
        if existing is None:
            return False

        client = self._clients.pop(slug, None)
        if client:
            await client.stop()

        # Remove cached models belonging to this provider
        self._available_models = [m for m in self._available_models if m.source_endpoint != slug]
        self._provider_errors.pop(slug, None)

        await self._provider_repo.delete_provider(slug)

        if existing.credential_name and self._credential_service:
            await self._credential_service.delete_credential(existing.credential_name)

        logger.info("Removed inference provider '%s'", slug)
        return True

    async def refresh_models(self, provider_slug: str | None = None) -> list[ModelInfo]:
        """Discover models from one or all providers via GET /models.

        Discovered models are persisted to inference_models (upsert without
        overwriting capability flags). Models that are no longer served by
        the provider are cleaned up. The in-memory _available_models cache
        is rebuilt from all providers on each call.
        """
        if provider_slug:
            return await self._refresh_single(provider_slug)
        return await self._refresh_all()

    async def _refresh_single(self, provider_slug: str) -> list[ModelInfo]:
        """Refresh model discovery for a single provider."""
        client = self._clients.get(provider_slug)
        if client is None:
            logger.warning("Cannot refresh models — provider '%s' not found", provider_slug)
            return []

        try:
            models = await client.list_models()
            model_ids = [m.id for m in models]
            for m in models:
                m.source_endpoint = provider_slug

            # Persist discovered models without clobbering capability flags
            await self._provider_repo.upsert_discovered_models(provider_slug, model_ids)
            await self._provider_repo.delete_stale_models(provider_slug, model_ids)

            self._provider_errors.pop(provider_slug, None)
            logger.info("Discovered %d models from provider '%s'", len(models), provider_slug)
        except Exception as exc:
            logger.warning("Failed to list models from provider '%s': %s", provider_slug, exc)
            self._provider_errors[provider_slug] = str(exc)
            models = []

        # Rebuild the full in-memory cache from all providers
        await self._rebuild_available_models()
        return models

    async def _refresh_all(self) -> list[ModelInfo]:
        """Refresh model discovery for all providers."""
        logger.info("Refreshing models from %d providers", len(self._clients))

        for slug in list(self._clients.keys()):
            client = self._clients[slug]
            try:
                models = await client.list_models()
                model_ids = [m.id for m in models]
                for m in models:
                    m.source_endpoint = slug

                await self._provider_repo.upsert_discovered_models(slug, model_ids)
                await self._provider_repo.delete_stale_models(slug, model_ids)

                self._provider_errors.pop(slug, None)
                logger.info("Discovered %d models from provider '%s'", len(models), slug)
            except Exception as exc:
                logger.warning("Failed to list models from provider '%s': %s", slug, exc)
                self._provider_errors[slug] = str(exc)

        await self._rebuild_available_models()
        return list(self._available_models)

    async def _rebuild_available_models(self) -> None:
        """Rebuild the in-memory model cache from the DB, preserving
        capability flags set by the user."""
        db_models = await self._provider_repo.get_all_models()
        self._available_models = [
            ModelInfo(
                id=m.model_id,
                source_endpoint=m.provider_slug,
            )
            for m in db_models
        ]

    def get_available_models(self) -> list[ModelInfo]:
        return list(self._available_models)

    def get_provider_errors(self) -> dict[str, str]:
        """Return per-provider discovery errors from the last refresh."""
        return dict(self._provider_errors)

    async def resolve(self, purpose: str, conversation_id: str = "") -> ResolvedModel:
        """Resolve a model for the given purpose (intelligence or task).

        For intelligence, checks conversation-level override first (when
        conversation_id is provided and a conversation_model_repo is
        configured), then falls back to global model_preferences.

        Reads role assignments from model_preferences (DB), then looks up
        the provider client and capability flags from the DB.
        """
        logger.debug(
            "Resolving model for purpose '%s' (conversation_id='%s')",
            purpose,
            conversation_id,
        )
        prefs = await self._prefs_repo.get_preferences(self._user_id)

        endpoint_slug = ""
        model_id = ""
        if purpose == "intelligence":
            # Check per-conversation override first
            if conversation_id and self._conversation_model_repo:
                override = await self._conversation_model_repo.get_override(conversation_id)
                if override is not None:
                    endpoint_slug = override.intelligence_endpoint
                    model_id = override.intelligence_model
                    logger.debug(
                        "Using conversation override: %s/%s",
                        endpoint_slug,
                        model_id,
                    )
            # Fall back to global default
            if not endpoint_slug or not model_id:
                endpoint_slug = prefs.intelligence_endpoint
                model_id = prefs.intelligence_model
        elif purpose == "task":
            endpoint_slug = prefs.task_endpoint
            model_id = prefs.task_model

        if not endpoint_slug or not model_id:
            raise ValueError(
                f"No {purpose} model assigned. Assign one via PUT /api/inference/models/assign."
            )

        client = self._clients.get(endpoint_slug)
        if client is None:
            raise ValueError(
                f"Provider '{endpoint_slug}' not found for {purpose} model "
                f"'{model_id}'. Available: {list(self._clients.keys())}"
            )

        # Read capability flags and provider_type from DB
        provider = await self._provider_repo.get_provider(endpoint_slug)
        if provider is None:
            raise ValueError(f"Provider '{endpoint_slug}' not found in database")

        native_tool_calling = False
        models = await self._provider_repo.get_models(endpoint_slug)
        for m in models:
            if m.model_id == model_id:
                native_tool_calling = m.native_tool_calling
                break

        return ResolvedModel(
            model_id=model_id,
            client=client,
            native_tool_calling=native_tool_calling,
            provider_type=provider.provider_type,
        )

    async def set_assignments(
        self,
        intelligence_endpoint: str,
        intelligence_model: str,
        task_endpoint: str,
        task_model: str,
    ) -> None:
        logger.info(
            "Setting model assignments: intelligence=%s/%s, task=%s/%s",
            intelligence_endpoint,
            intelligence_model,
            task_endpoint,
            task_model,
        )
        await self._prefs_repo.set_preferences(
            ModelPreferences(
                user_id=self._user_id,
                intelligence_endpoint=intelligence_endpoint,
                intelligence_model=intelligence_model,
                task_endpoint=task_endpoint,
                task_model=task_model,
            )
        )

    async def set_model_capability(
        self,
        provider_slug: str,
        model_id: str,
        native_tool_calling: bool,
    ) -> None:
        """Update the native_tool_calling flag for a specific model."""
        await self._provider_repo.upsert_model(provider_slug, model_id, native_tool_calling)
        logger.info(
            "Set native_tool_calling=%s for model '%s' on provider '%s'",
            native_tool_calling,
            model_id,
            provider_slug,
        )

    async def validate_connection(
        self,
        base_url: str,
        api_key: str,
        provider_type: str = "completions",
        slug: str = "",
    ) -> tuple[bool, str, int]:
        """Test connectivity to a provider without persisting anything.

        Creates a temporary InferenceClient with a short timeout, calls
        GET /models, and returns the result. Cleans up the client after.

        When ``slug`` is provided and ``api_key`` is empty, resolves the
        existing provider's credential (used when editing and the user
        left the API key field blank).

        Returns ``(valid, error_message, model_count)``.
        """
        # Resolve existing credential when editing (blank key = keep existing)
        if not api_key and slug:
            existing = await self._provider_repo.get_provider(slug)
            if existing:
                api_key = await self.resolve_api_key(slug, existing.credential_name)

        client = InferenceClient(
            base_url=base_url,
            api_key=api_key,
            provider_type=provider_type,
            timeout=15.0,
        )
        await client.start()
        try:
            models = await client.list_models()
            logger.info("Validation succeeded for %s: %d models", base_url, len(models))
            return True, "", len(models)
        except Exception as exc:
            logger.warning("Validation failed for %s: %s", base_url, exc)
            return False, str(exc), 0
        finally:
            await client.stop()
