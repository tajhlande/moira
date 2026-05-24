import logging
import os

from moira.config import MoiraConfig, resolve_db_path
from moira.inference.client import InferenceClient
from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    DEFAULT_USER_ID,
    ModelPreferencesRepository,
    SessionRepository,
)

logger = logging.getLogger(__name__)

# Simple service locator for dependency injection. Services are registered by
# string key during init_services() and retrieved via service_provider().
#
# Callers must cast() the return type because the locator is untyped. This is
# a deliberate trade-off: it allows test injection (init_services accepts
# optional mock repos) and keeps wiring in one place, at the cost of
# runtime-only key validation.
#
# Known service keys:
#   "session_repository"                   -> SessionRepository
#   "model_preferences_repository"         -> ModelPreferencesRepository
#   "model_registry"                       -> ModelRegistry
#   "inference_client:{endpoint_name}"     -> InferenceClient
#   "config"                               -> MoiraConfig
_services: dict[str, object] = {}


def service_provider(name: str) -> object:
    if name not in _services:
        raise RuntimeError(f"Service '{name}' not available. Call init_services() first.")
    return _services[name]


async def init_services(
    config: MoiraConfig,
    session_repo: SessionRepository | None = None,
    prefs_repo: ModelPreferencesRepository | None = None,
) -> None:
    logger.info("Initializing services")
    from moira.persistence.sqlite.schema import run_migrations

    db_path = resolve_db_path(config)

    # Repositories are optionally injectable for testing. If either is None,
    # the default SQLite implementation is created and migrations run.
    # Partial injection is supported: if only one repo is provided, the
    # other is still created from SQLite, both sharing the same db_path.
    if session_repo is None or prefs_repo is None:
        from moira.persistence.sqlite.repos import (
            SqliteModelPreferencesRepository,
            SqliteSessionRepository,
        )

        run_migrations(db_path)
        session_repo = session_repo or SqliteSessionRepository(db_path)
        prefs_repo = prefs_repo or SqliteModelPreferencesRepository(db_path)
        logger.info("Created SQLite repositories at %s", db_path)

    _services["session_repository"] = session_repo
    _services["model_preferences_repository"] = prefs_repo

    clients: dict[str, InferenceClient] = {}
    for ep in config.inference.endpoints:
        # API key resolution: check MOIRA_API_KEY_{NAME} env var first
        # (uppercased endpoint name), then fall back to config. This keeps
        # secrets out of config files while still allowing in-config keys
        # for local dev.
        env_key = f"MOIRA_API_KEY_{ep.name.upper()}"
        api_key = os.environ.get(env_key, ep.api_key)
        if api_key:
            logger.debug("Using API key from %s for endpoint '%s'", env_key, ep.name)
        logger.info("Connecting to inference endpoint '%s' at %s", ep.name, ep.base_url)
        client = InferenceClient(base_url=ep.base_url, api_key=api_key)
        await client.start()
        clients[ep.name] = client
        _services[f"inference_client:{ep.name}"] = client

    registry = ModelRegistry(
        config=config.inference,
        prefs_repo=prefs_repo,
        user_id=DEFAULT_USER_ID,
    )
    for name, client in clients.items():
        registry.add_client(name, client)
    await registry.refresh_models()
    logger.info(
        "Model registry initialized with %d models from %d endpoints",
        len(registry.get_available_models()),
        len(clients),
    )
    _services["model_registry"] = registry

    _services["config"] = config


async def shutdown_services() -> None:
    # Only InferenceClient requires explicit async cleanup (closing the httpx
    # connection pool). SQLite repos use per-call connections that close
    # automatically via finally blocks. If connection pooling is added later,
    # repos will need cleanup here as well.
    logger.info("Shutting down services")
    from moira.inference.client import InferenceClient

    for key in list(_services.keys()):
        svc = _services.pop(key)
        if isinstance(svc, InferenceClient):
            await svc.stop()
