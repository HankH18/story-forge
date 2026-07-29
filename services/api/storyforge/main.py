"""FastAPI application entrypoint.

The frontend is a static SPA; all state and secrets live behind this service
(§2). Every route is under ``/api/*`` (§9).
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from storyforge.api import health, kids, readings, stories, validate

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)


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
    )

    app.include_router(health.router)
    app.include_router(validate.router)
    app.include_router(stories.router)
    app.include_router(kids.router)
    app.include_router(readings.router)
    return app


app = create_app()
