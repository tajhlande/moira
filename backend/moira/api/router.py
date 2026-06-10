"""Backward-compatible router export.

Historically, the entire API surface lived in this module. It has been
split into ``moira.api.routes.<conversations|tools|settings|metrics|health>``
to keep each file focused on a single app tab. The composed
``api_router`` is re-exported here as ``router`` so the existing
``from moira.api.router import router`` import in ``main.py`` continues
to work.

New code should prefer importing from ``moira.api.routes``.
"""

from moira.api.routes import api_router as router

__all__ = ["router"]
