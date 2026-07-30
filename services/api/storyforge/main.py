"""FastAPI application entrypoint.

The frontend is a static SPA; all state and secrets live behind this service
(§2). Every route is under ``/api/*`` (§9).
"""

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse, Response

from storyforge.api import admin, catalog, health, kids, readings, stories, validate
from storyforge.auth import auth_config

log = logging.getLogger(__name__)

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
    # §9's `/api/admin/*` block and §11's admin portal. Every route on this router
    # is role-gated by the router itself (`dependencies=[Depends(require_admin)]`),
    # so mounting it cannot widen anything by accident (DEVIATIONS 9.1).
    app.include_router(admin.router)
    _mount_spa(app)
    return app


#: Where the built SPA lands in the production image (DEVIATIONS 10.1). Absent in
#: local dev, where Vite serves the app on :5173 and proxies `/api` here.
SPA_DIR = Path(os.environ.get("STORYFORGE_SPA_DIR", "/app/web"))

#: One year, the conventional maximum. Safe ONLY because Vite fingerprints every
#: file under `assets/` with a content hash, so a changed file is a changed URL and
#: nothing here is ever revised in place. `StaticFiles` sets no `Cache-Control` of
#: its own, so without this the browser revalidates every asset on every load and
#: the `no-cache` on `index.html` is the only caching rule in play — half a policy,
#: which is worse than either half alone.
_ASSET_CACHE = "public, max-age=31536000, immutable"


class _ImmutableAssets(StaticFiles):
    async def get_response(self, path: str, scope) -> Response:  # type: ignore[override]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = _ASSET_CACHE
        return response


def _mount_spa(app: FastAPI) -> None:
    """Serve the built SPA from this service, or don't, if it isn't there.

    §2 says the frontend is a static SPA holding no state and no secrets — that is
    a statement about what it CONTAINS, not about which process hands it over. One
    origin means no CORS, no `VITE_API_URL` baked into the bundle, one instance to
    pay for and one thing to patch, which is the same argument DEVIATIONS 7.4 used
    to reject a container for the web tier.

    Registered LAST, after every router, so nothing here can shadow `/api/*`.

    Three rules, each of which is a bug if inverted:

    1. An unknown `/api/...` path must 404 as JSON. It must NOT fall through to
       `index.html` — that turns a typo'd endpoint into a 200 with an HTML body,
       and a client parsing it gets a JSON error about `<` instead of a 404. The
       API's own 404 is the honest answer even though the SPA is the fallback for
       everything else.
    2. Any other unmatched path returns `index.html`, because React Router uses
       `BrowserRouter`: `/admin/evals/114` and `/read/1648` are client routes, and
       a hard refresh on either must not 404.
    3. `index.html` is `no-cache` while hashed assets are immutable. Inverting that
       is the classic deploy failure — a cached `index.html` keeps pointing at
       asset hashes the new build no longer has, so the app half-loads for exactly
       the users who visited before.

    If the directory is missing the app still boots. The API must not depend on
    the frontend having been built; `make dev` never builds it.
    """
    index = SPA_DIR / "index.html"
    if not index.is_file():
        log.info("SPA not mounted: %s absent (expected in local dev)", index)
        return

    assets = SPA_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", _ImmutableAssets(directory=assets), name="assets")

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def spa(spa_path: str) -> Response:
        # Rule 1. `include_router` already claimed every real `/api/*` route, so
        # reaching here with an api/ prefix means it does not exist.
        if spa_path == "api" or spa_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = (SPA_DIR / spa_path).resolve()
        # `resolve()` + the prefix check together stop `../` escaping the tree.
        if (
            spa_path
            and candidate.is_file()
            and candidate.is_relative_to(SPA_DIR.resolve())
        ):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    log.info("SPA mounted from %s", SPA_DIR)


app = create_app()
