import logging
import os

from moira.config import MoiraConfig, resolve_db_path, resolve_lancedb_path
from moira.inference.client import InferenceClient
from moira.inference.registry import ModelRegistry
from moira.persistence.interfaces import (
    DEFAULT_USER_ID,
    ConversationRepository,
    ModelPreferencesRepository,
)

logger = logging.getLogger(__name__)

# Simple service locator for dependency injection. Services are registered by
# string key during init_services() and retrieved via service_provider().
#
# Known service keys:
#   "conversation_repository"               -> ConversationRepository
#   "model_preferences_repository"         -> ModelPreferencesRepository
#   "model_registry"                       -> ModelRegistry
#   "inference_client:{endpoint_name}"     -> InferenceClient
#   "embedding_provider"                   -> EmbeddingProvider
#   "tool_catalog"                         -> ToolCatalog
#   "tool_discovery"                       -> ToolDiscovery
#   "tool_executor"                        -> ToolExecutor
#   "tool_embedding_repo"                  -> ToolEmbeddingRepository
#   "research_graph"                       -> CompiledStateGraph
#   "config"                               -> MoiraConfig
#
# Returns object; callers must cast() to the expected type.
# Type parameters were rejected due to Pylance/pyright inability to infer
# from assignment context (TypeVar appears only once in signature).
# Function overloads were rejected as code pollution -- the overload list
# duplicates the service key documentation, requires maintenance as services
# grow, and half the return types would be Any to avoid circular imports.
#
# This is a deliberate trade-off: it allows test injection (init_services
# accepts optional mock repos) and keeps wiring in one place, at the cost
# of runtime-only key validation.
_services: dict[str, object] = {}


def service_provider(name: str) -> object:
    """Retrieve a registered service by key. Raises RuntimeError if
    init_services() has not been called or the key is unknown."""
    if name not in _services:
        raise RuntimeError(f"Service '{name}' not available. Call init_services() first.")
    return _services[name]


async def init_services(
    config: MoiraConfig,
    conversation_repo: ConversationRepository | None = None,
    prefs_repo: ModelPreferencesRepository | None = None,
) -> None:
    """Create and register all application services. Accepts optional
    pre-built repositories for testing; if omitted, SQLite implementations
    are created from config. Partial injection is supported: if only one
    repo is provided, the other is still created from SQLite, both sharing
    the same db_path."""
    logger.info("Initializing services")
    from moira.persistence.sqlite.schema import run_migrations

    db_path = resolve_db_path(config)

    if conversation_repo is None or prefs_repo is None:
        from moira.persistence.sqlite.repos import (
            SqliteConversationRepository,
            SqliteModelPreferencesRepository,
        )

        run_migrations(db_path)
        conversation_repo = conversation_repo or SqliteConversationRepository(db_path)
        prefs_repo = prefs_repo or SqliteModelPreferencesRepository(db_path)
        logger.info("Created SQLite repositories at %s", db_path)

    _services["conversation_repository"] = conversation_repo
    _services["model_preferences_repository"] = prefs_repo

    clients: dict[str, InferenceClient] = {}
    for ep in config.inference.providers:
        # API key resolution: check MOIRA_API_KEY_{NAME} env var first
        # (uppercased provider name), then fall back to config. This keeps
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

    # --- Phase 2: Embedding provider ---
    embedding_provider = _create_embedding_provider(config)
    _services["embedding_provider"] = embedding_provider
    logger.info("Embedding provider initialized: %s", config.embedding.provider)

    # --- Phase 2: Tool catalog ---
    from moira.tools.catalog import ToolCatalog

    catalog = ToolCatalog()
    if config.tools:
        catalog.load_from_config(config.tools)
    _services["tool_catalog"] = catalog

    # --- Phase 2: LanceDB tool embedding repository ---
    from moira.persistence.lancedb.tool_embeddings import ToolEmbeddingRepository

    embedding_repo = ToolEmbeddingRepository(resolve_lancedb_path(config))
    await embedding_repo.start()
    _services["tool_embedding_repo"] = embedding_repo

    # --- Phase 2: Tool discovery ---
    from moira.tools.discovery import ToolDiscovery

    discovery = ToolDiscovery(
        embedding_provider=embedding_provider,
        embedding_repo=embedding_repo,
    )
    if config.tools:
        await discovery.ingest_tools(catalog.get_all())
    _services["tool_discovery"] = discovery

    # --- Phase 2: Tool executor ---
    from moira.tools.executor import ToolExecutor

    executor = ToolExecutor()
    executor.register_tools(catalog.get_all())
    _services["tool_executor"] = executor

    # --- Phase 2: LangGraph research workflow ---
    from moira.workflow.graph import compile_graph

    # The graph runs in an async context (SSE streaming via FastAPI), so we
    # must use AsyncSqliteSaver. We open an aiosqlite connection directly and
    # keep it alive for the application lifetime, closing it in shutdown.
    _checkpointer_conn = None
    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        _checkpointer_conn = await aiosqlite.connect(db_path)
        saver = AsyncSqliteSaver(_checkpointer_conn)
        await saver.setup()
        research_graph = compile_graph(config, checkpointer=saver)
        _services["_checkpointer_conn"] = _checkpointer_conn
    except Exception as e:
        logger.warning("Checkpoint saver unavailable, compiling without: %s", e)
        research_graph = compile_graph(config)
    _services["research_graph"] = research_graph

    # --- Phase 2: Run manager (background graph execution) ---
    from moira.workflow.run_manager import RunManager

    _services["run_manager"] = RunManager()

    _services["config"] = config


async def shutdown_services() -> None:
    # Only InferenceClient requires explicit async cleanup (closing the httpx
    # connection pool). SQLite repos use per-call connections that close
    # automatically via finally blocks. If connection pooling is added later,
    # repos will need cleanup here as well.
    logger.info("Shutting down services")
    from moira.inference.client import InferenceClient

    # Clean up the async aiosqlite connection used by the checkpoint saver.
    conn = _services.pop("_checkpointer_conn", None)
    if conn is not None:
        try:
            await conn.close()
        except Exception:
            pass

    for key in list(_services.keys()):
        svc = _services.pop(key)
        if isinstance(svc, InferenceClient):
            await svc.stop()


def _create_embedding_provider(config: MoiraConfig):
    """Factory for embedding providers based on config. Currently supports
    'local' (sentence-transformers). Extensible for remote endpoints."""
    if config.embedding.provider == "local":
        from moira.embeddings.local import LocalEmbeddingProvider

        return LocalEmbeddingProvider(model_name=config.embedding.model)
    raise ValueError(f"Unknown embedding provider: {config.embedding.provider}")
