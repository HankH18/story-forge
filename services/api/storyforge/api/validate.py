"""Live validation endpoint (§5.6).

Powers the admin playground's live highlighting and the §17 demo. A thin wrapper
over ``validate_text`` — the decoder already returns exactly the shape §5.1
specifies, so there is no translation layer to drift.

**Admin only (§9).** Not because the decoder is secret — it is a pure function
over a vendored dictionary and a published inventory — but because this is the
one endpoint that takes arbitrary text, and §13's three child-safety layers do
not apply to it: nothing here is blocklisted, allowlist-constrained or
moderated. A parent session must not be able to point it anywhere, and 4000
characters of unbounded input is not something to leave open either.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.auth import Principal, require_admin
from storyforge.db import get_session
from storyforge.decoder import MAX_STAGE, MIN_STAGE, TextReport, mastered_ids, validate_text

router = APIRouter(tags=["validate"])


class ValidateRequest(BaseModel):
    text: str = Field(max_length=4000)
    # DEVIATIONS 17.2: derived, never a literal. `11` was edited by hand in five
    # schemas when stage 1 was retired (12.1-12.14) and would have to be edited by
    # hand again — `tests/test_stage_bounds.py` is what turns the next renumber
    # from a silent 422 into a red suite.
    stage_id: int = Field(ge=MIN_STAGE, le=MAX_STAGE)
    # A kid's own name is added to their sight allowlist (§4), so callers need to
    # extend the list per request.
    extra_sight_words: list[str] = Field(default_factory=list, max_length=64)
    story_words: list[str] = Field(default_factory=list, max_length=8)


async def _sight_words(session: AsyncSession, stage_id: int) -> frozenset[str]:
    """Cumulative sight-word allowlist at this stage (§4)."""
    rows = await session.execute(
        text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": stage_id}
    )
    return frozenset(r[0] for r in rows)


@router.post("/api/validate", response_model=TextReport)
async def validate(
    body: ValidateRequest,
    principal: Principal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> TextReport:
    stage_exists = await session.execute(
        text("SELECT 1 FROM stages WHERE id = :s"), {"s": body.stage_id}
    )
    if stage_exists.first() is None:
        raise HTTPException(
            status_code=404,
            detail=f"stage {body.stage_id} not seeded — run `make seed`",
        )

    sight = await _sight_words(session, body.stage_id) | frozenset(
        w.strip().lower() for w in body.extra_sight_words if w.strip()
    )
    return validate_text(
        body.text,
        mastered_ids(body.stage_id),
        sight,
        frozenset(w.strip().lower() for w in body.story_words if w.strip()),
    )
