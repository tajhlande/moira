import logging
from dataclasses import dataclass

from moira.config import InferenceConfig
from moira.inference.client import InferenceClient, ModelInfo
from moira.persistence.interfaces import ModelPreferences, ModelPreferencesRepository

logger = logging.getLogger(__name__)


@dataclass
class ResolvedModel:
    model_id: str
    client: InferenceClient
    native_tool_calling: bool = False
    provider_type: str = "completions"


class ModelRegistry:
    def __init__(
        self,
        config: InferenceConfig,
        prefs_repo: ModelPreferencesRepository,
        user_id: str,
    ):
        self._config = config
        self._prefs_repo = prefs_repo
        self._user_id = user_id
        self._clients: dict[str, InferenceClient] = {}
        self._available_models: list[ModelInfo] = []

    def add_client(self, name: str, client: InferenceClient) -> None:
        self._clients[name] = client

    async def refresh_models(self) -> list[ModelInfo]:
        logger.info("Refreshing model list from %d endpoints", len(self._clients))
        all_models: list[ModelInfo] = []
        for name, client in self._clients.items():
            try:
                models = await client.list_models()
                # Tag each model with its source endpoint so resolve() can
                # find the correct client later.
                for m in models:
                    m.source_endpoint = name
                logger.info("Discovered %d models from endpoint '%s'", len(models), name)
                all_models.extend(models)
            except Exception as exc:
                # Endpoints that fail to respond are silently skipped. If ALL
                # endpoints fail, the registry will have an empty model list and
                # resolve() will raise a ValueError.
                logger.warning("Failed to list models from endpoint '%s': %s", name, exc)
        self._available_models = all_models
        return all_models

    def get_available_models(self) -> list[ModelInfo]:
        return list(self._available_models)

    async def resolve(self, purpose: str) -> ResolvedModel:
        logger.debug("Resolving model for purpose '%s'", purpose)
        prefs = await self._prefs_repo.get_preferences(self._user_id)

        endpoint_name = ""
        model_id = ""
        if purpose == "intelligence":
            endpoint_name = (
                prefs.intelligence_endpoint or self._config.models.intelligence_endpoint
            )
            model_id = prefs.intelligence_model or self._config.models.intelligence_model
        elif purpose == "task":
            endpoint_name = prefs.task_endpoint or self._config.models.task_endpoint
            model_id = prefs.task_model or self._config.models.task_model

        if not endpoint_name or not model_id:
            raise ValueError(f"No {purpose} model assigned. Assign one via PUT /api/models.")

        # Look up the client directly by endpoint name rather than scanning
        # all models. This ensures correct routing when the same model ID
        # exists on multiple endpoints.
        client = self._clients.get(endpoint_name)
        if client is None:
            raise ValueError(
                f"Endpoint '{endpoint_name}' not found for {purpose} model "
                f"'{model_id}'. Available: {list(self._clients.keys())}"
            )

        # Verify the model actually exists on the endpoint.
        for m in self._available_models:
            if m.id == model_id and m.source_endpoint == endpoint_name:
                # Look up per-model capabilities from the provider config.
                native_tool_calling = False
                provider_type = "completions"
                for ep in self._config.providers:
                    if ep.name == endpoint_name:
                        provider_type = ep.provider_type
                        for mc in ep.models:
                            if mc.id == model_id:
                                native_tool_calling = mc.native_tool_calling
                        break
                return ResolvedModel(
                    model_id=model_id,
                    client=client,
                    native_tool_calling=native_tool_calling,
                    provider_type=provider_type,
                )

        raise ValueError(f"Model '{model_id}' not found on endpoint '{endpoint_name}'")

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
