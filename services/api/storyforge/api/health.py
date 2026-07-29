"""Health endpoint (§15).

§15 specifies health checks DB + CMUdict load + Anthropic reachability. CMUdict
arrives in Phase 1 and Anthropic in Phase 3, so those components report
``pending`` with the phase that implements them. ``pending`` is deliberately not
an error — but it is also not silently omitted, so the endpoint never overstates
what has actually been verified.

It also answers the question the Phase 6 audit found nobody could answer: **is
this API checking anything?** With §18's ``DEMO_BYPASS_AUTH`` on, no HTTP
response said so — the banner is a server log line — and a whole phase of
browser verification was run against an unauthenticated API by two agents who
reached opposite conclusions about it. ``demo_bypass_auth`` and ``principal``
are top-level here, and ``X-Storyforge-Auth`` is on every response from every
route (``main.py``), so one curl of anything settles it (DEVIATIONS 7.25).

``status`` deliberately does NOT go red under the bypass. It is the aggregate of
the §15 component checks, which `docs/DEPLOY.md` and the Dockerfile HEALTHCHECK
both read as "is a subsystem broken"; overloading it with a configuration
verdict would change what "error" means for two existing readers in order to say
something the dedicated field says better.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.auth import DEMO_CLERK_ID, auth_config
from storyforge.config import Settings, get_settings
from storyforge.db import get_session

router = APIRouter(tags=["health"])

ComponentStatus = Literal["ok", "pending", "error"]


class Component(BaseModel):
    status: ComponentStatus
    detail: str


class HealthReport(BaseModel):
    status: ComponentStatus
    #: True when §18's bypass is live: Clerk verification is DISABLED and every
    #: request — authenticated or not — runs as one fixed local principal.
    demo_bypass_auth: bool = False
    #: The principal every request is served as while the bypass is on, and its
    #: role. `None` when auth is enforced, because then there is no such thing.
    principal: str | None = None
    components: dict[str, Component]


async def _check_database(session: AsyncSession) -> Component:
    try:
        version = (await session.execute(text("SHOW server_version"))).scalar_one()
        return Component(status="ok", detail=f"postgres {version}")
    except Exception as exc:  # noqa: BLE001 — health must report, never raise
        return Component(status="error", detail=f"{type(exc).__name__}: {exc}")


def _check_decoder() -> Component:
    """Phase 1 replaces this with a real CMUdict + GPC inventory load."""
    try:
        from storyforge.decoder import inventory_size, lexicon_size
    except ImportError:
        return Component(status="pending", detail="decoder lands in phase 1")
    return Component(
        status="ok",
        detail=f"cmudict {lexicon_size()} entries, {inventory_size()} gpcs",
    )


def _check_anthropic(settings: Settings) -> Component:
    if settings.anthropic_api_key is None:
        return Component(status="pending", detail="generation lands in phase 3")
    return Component(status="pending", detail="reachability check lands in phase 3")


@router.get("/api/health", response_model=HealthReport)
async def health(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> HealthReport:
    components = {
        "database": await _check_database(session),
        "decoder": _check_decoder(),
        "anthropic": _check_anthropic(settings),
    }
    # Overall status is the worst component status; `pending` does not fail the
    # check, `error` does.
    overall: ComponentStatus = "ok"
    if any(c.status == "error" for c in components.values()):
        overall = "error"

    config = auth_config()
    return HealthReport(
        status=overall,
        demo_bypass_auth=config.demo_bypass,
        principal=(f"{DEMO_CLERK_ID} (role={config.demo_role})" if config.demo_bypass else None),
        components=components,
    )
