import logging
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from moira.config import AppConfig, load_config
from moira.service_setup import init_services, shutdown_services

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
# MOiRA application code logs at DEBUG for transparency into workflow steps
# (prompts, responses, thinking). Third-party libraries (httpx, langchain,
# etc.) stay at WARNING to avoid noise.
logging.getLogger("moira").setLevel(logging.DEBUG)
logger = logging.getLogger(__name__)


def show_banner():
    """
    Show the ASCII banner on the console
    """
    try:
        with open("moira/resources/banner.txt", "r") as f:
            banner_lines = f.read()
        logger.info("\n\n" + banner_lines)
    except Exception:
        logger.warning("Unable to load and display banner")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MOiRA")
    config = load_config()
    await init_services(config)
    show_banner()
    logger.info(f"MOiRA v{version('moira-backend')} ready")
    yield
    logger.info("Shutting down MOiRA")
    await shutdown_services()


def _load_cors_origins() -> list[str]:
    # CORS middleware must be registered before startup. We try to load the
    # configured origins here; if config is unavailable (e.g. certain tests),
    # fall back to the default AppConfig origins.
    try:
        return load_config().app.cors_origins
    except SystemExit:
        fallback = AppConfig().cors_origins
        logger.warning("Unable to load config for CORS; using defaults: %s", fallback)
        return fallback


def create_app() -> FastAPI:
    app = FastAPI(title="MOiRA", version="0.1.0", lifespan=lifespan)

    cors_origins = _load_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("Configured CORS origins: %s", cors_origins)

    from moira.api.router import router
    from moira.api.streaming import streaming_router

    app.include_router(router, prefix="/api")
    # The streaming router overrides the /conversations/{id}/messages endpoint
    # with an SSE version. It must be registered after the base router so
    # its route takes precedence (FastAPI uses last-registered match).
    app.include_router(streaming_router, prefix="/api")

    return app


app = create_app()
