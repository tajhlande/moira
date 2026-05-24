import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from moira.config import AppConfig, load_config
from moira.service_setup import init_services, shutdown_services

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MOiRA")
    config = load_config()
    await init_services(config)
    logger.info("MOiRA ready")
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

    app.include_router(router, prefix="/api")

    return app


app = create_app()
