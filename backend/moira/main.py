import logging
import os
from contextlib import asynccontextmanager
from importlib.metadata import version
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

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


class SPAStaticFiles(StaticFiles):
    """StaticFiles subclass that serves index.html for SPA client-side routes.

    Vue Router uses history mode, so client-side routes like /conversation/new
    are not real files on disk. Without this fallback, a direct navigation or
    page refresh on any route other than / would 404. This subclass catches
    404s and returns index.html, letting the Vue router handle the route.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except (HTTPException, StarletteHTTPException) as ex:
            if ex.status_code == 404:
                return await super().get_response("index.html", scope)
            raise ex


def show_banner():
    """
    Show the ASCII banner on the console
    """
    try:
        banner_path = Path(__file__).parent / "resources" / "banner.txt"
        with open(banner_path, "r") as f:
            banner_lines = f.read()
        logger.info("\n\n" + banner_lines)
    except Exception:
        logger.warning("Unable to load and display banner")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting MOiRA")
    config = load_config()
    await init_services(config)

    # clean up stale data in the database
    from typing import cast

    from moira.persistence.interfaces import ConversationRepository
    from moira.service_setup import service_provider

    repo = cast(ConversationRepository, service_provider("conversation_repository"))
    await repo.cleanup_stale_runs()

    # show our launch banner
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


def _resolve_static_dir() -> Path | None:
    """Resolve the frontend static files directory.

    Resolution order:
    1. MOIRA_STATIC_DIR env var (Docker containers)
    2. <repo_root>/frontend/dist (standalone production build)

    Returns None if no directory exists (dev mode — frontend served by Vite).
    """
    env_static = os.environ.get("MOIRA_STATIC_DIR")
    if env_static:
        p = Path(env_static)
        if p.is_dir():
            return p

    repo_root = Path(__file__).resolve().parent.parent.parent
    frontend_dist = repo_root / "frontend" / "dist"
    if frontend_dist.is_dir():
        return frontend_dist

    return None


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

    from moira.api.router import router
    from moira.api.streaming import streaming_router

    app.include_router(router, prefix="/api")
    # The streaming router overrides the /conversations/{id}/messages endpoint
    # with an SSE version. It must be registered after the base router so
    # its route takes precedence (FastAPI uses last-registered match).
    app.include_router(streaming_router, prefix="/api")

    # Health check endpoint for Docker healthcheck and deployment verification
    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # Serve frontend static files (production mode). In dev mode the frontend
    # is served by the Vite dev server and this mount is skipped.
    static_dir = _resolve_static_dir()
    if static_dir:
        app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="spa")
        logger.info(
            "Serving frontend from %s (same-origin — CORS not needed)",
            static_dir,
        )
    else:
        logger.info(
            "Dev mode — frontend served by Vite. CORS origins: %s",
            cors_origins,
        )

    return app


app = create_app()
