"""FastAPI application entrypoint.

The frontend is a static SPA; all state and secrets live behind this service
(§2). Every route is under ``/api/*`` (§9).
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from storyforge.api import catalog, health, kids, readings, stories, validate
from storyforge.auth import auth_config

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)

#: Present on EVERY response, from every route, including 401s and 404s.
#:
#: The Phase 6 audit's finding 0 was that §18's `DEMO_BYPASS_AUTH` was
#: undetectable over HTTP — a server log line and nothing else — so a browser
#: session was driven against a completely unauthenticated API for a phase, and
#: two agents concluded opposite things about whether auth was enforced. The
#: value is emitted in both states on purpose: a *missing* header would be
#: ambiguous between "enforced" and "an older build", which is the same silence
#: in a new place (DEVIATIONS 7.25).
AUTH_MODE_HEADER = "X-Storyforge-Auth"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Story Forge API",
        version="0.1.0",
        description=(
            "Infinite, interest-matched reading content with a machine-verified "
            "decodability guarantee."
        ),
    )

    # The Vite dev server runs on a different origin locally. Production serves
    # the SPA as a static site (§3), so this list stays dev-only.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Without this the browser hides the disclosure header from any
        # cross-origin caller, which is every caller that is not going through
        # the Vite proxy — i.e. exactly the ones most likely to be confused
        # about which API they are talking to.
        expose_headers=[AUTH_MODE_HEADER],
    )

    @app.middleware("http")
    async def _disclose_auth_mode(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers[AUTH_MODE_HEADER] = (
            "demo-bypass" if auth_config().demo_bypass else "enforced"
        )
        return response

    app.include_router(health.router)
    # §9's `(public, cached)` pair. Mounted from Phase 8: they were specified in
    # §9 and never routed, so every kid-session load 404'd twice on
    # `GET /api/themes` while the SPA fell back to a copy of the seed CSVs that
    # nothing could tell had drifted (AUDIT-PHASE6 C12, DEVIATIONS 8.60).
    app.include_router(catalog.router)
    app.include_router(validate.router)
    app.include_router(stories.router)
    app.include_router(kids.router)
    app.include_router(readings.router)
    return app


app = create_app()
