"""Composable FastAPI routers, one per major app surface.

Each submodule owns a single tab's API routes and the helpers used only
by those routes. This file composes them into a single ``api_router``
that ``main.py`` mounts once under the ``/api`` prefix.

Adding a new route group:
    1. Create ``routes/<name>.py`` with an ``APIRouter()`` named ``router``.
    2. Add a one-line ``include_router`` call below.
"""

from fastapi import APIRouter

from . import conversations, health, inference, metrics, settings, tools

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(conversations.router)
api_router.include_router(inference.router)
api_router.include_router(tools.router)
api_router.include_router(settings.router)
api_router.include_router(metrics.router)
