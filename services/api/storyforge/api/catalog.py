"""§9's two catalogue routes: ``GET /api/stages`` and ``GET /api/themes``.

Both are listed in §9 as ``(public, cached)`` and neither existed until now, so
every kid-session load fired ``GET /api/themes`` and got a **404** — twice per
load under StrictMode (AUDIT-PHASE6 C12). The SPA absorbed it: both getters go
through ``lib/api.ts``'s ``optional()`` and fall back to a hand-copy of
``data/seed/{themes,stages}.csv``. So the cost was never the 404 itself. It was
that the copy could drift from the seed with nothing to notice, and the drift has
one sharp edge: a theme added to ``themes.csv`` without editing ``lib/api.ts`` in
the same commit becomes a picker card whose ``POST /api/stories/request`` answers
``404 unknown theme 'unicorns' — run make seed``. A 404 is not retryable, so kid
mode burns its three attempts and parks on the printer screen (C10). C12 feeding
C10 is the whole reason this file is worth five lines of SQL.

**Public — no ``require_parent``.** This and ``/api/health`` are the only routes
in the service that take no principal, which is §9's own division. What they
return is ``data/seed/*.csv``: eleven stage names and three theme names, all of
them committed to the repository. There is no child in either payload (§13), an
auth requirement would buy nothing, and it would make the theme picker's first
paint wait on Clerk having loaded.

**Cached, because "cached" in §9 is a property of the response.** Both send
``Cache-Control: public, max-age=300``. The stage list changes when the scope and
sequence changes, which is to say never; the theme list changes when ``make seed``
runs.

**Why ``GET /api/themes`` reports depth and does not filter on it.** The obvious
worry about this route is that it hands a child a theme the warm pool cannot
serve. It cannot: ``_SERVE_SQL`` orders ``stage_id DESC, (theme_id = :theme)
DESC``, so an empty (stage, theme) cell is answered with a lower stage, or another
theme, or both, with ``fallback.kind`` naming which — never a 404 and never an
empty result (§7). The only theme that dead-ends a child is one the ``themes``
table does not have, and a route that reads that table cannot return one.

Filtering would also *create* the failure it is meant to prevent: §07 fixes the
picker at 3-6 cards, and a filter that emptied the list leaves a child a screen
with nothing to tap. So the depth is reported and not enforced —
``servable_stories`` per theme, counted with ``SERVABLE_STATUSES`` imported from
the serving endpoint rather than re-spelled, so a fourth ``status`` value cannot
make this number and the serving path disagree (the discipline ``warm.py``'s
coverage gate uses, DEVIATIONS 8.42). Nothing in the SPA reads it; it is there so
"this theme is offered" and "this theme has content" can be compared in one
request (DEVIATIONS 8.60, 8.68).

**It is an upper bound, and the name of the constant says why.** ``status`` is the
half of "servable" that lives on the row's status column; the serving path is
entitled to refuse an individual row for other reasons (a serve-time decodability
re-check today, and per-row predicates added since). A number that is high by a
row or two is the right failure direction for a field whose only job is to make a
drift visible; a second copy of the serving path's full predicate, kept in step by
hand, is the wrong one.
"""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Imported, never re-spelled: what counts as servable is the serving endpoint's
# decision, and a second copy of the tuple is a second answer waiting to drift.
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import get_session

router = APIRouter(tags=["catalog"])

#: §9: "(public, cached)". Five minutes is long enough that a theme picker paints
#: from cache on every session of an afternoon, short enough that `make seed`
#: adding a theme shows up without an explanation.
CACHE_CONTROL = "public, max-age=300"


class ThemeOut(BaseModel):
    """One theme card (§07), plus how deep the pool is behind it."""

    id: int
    slug: str
    display_name: str
    #: Style guide §07 permits an emoji in exactly one place, and this is it —
    #: a pre-asset fallback until real cover art exists.
    emoji: str
    #: Stories in this theme across ALL stages whose `status` is servable
    #: (`warm`|`complete`). An upper bound on what a child can be served here, and
    #: reported rather than used to hide a card — see the module docstring.
    servable_stories: int


class StageOut(BaseModel):
    """One row of §4's scope and sequence, as a parent has to read it.

    `target_len_min/max` and `max_syllables` are deliberately not here. They are
    generation parameters, this route is public, and no client has a use for them
    — "Stage 7" plus a sentence is what the add-kid form needs.
    """

    id: int
    name: str
    description: str


# The status filter is in the ON clause, not the WHERE: a theme with no servable
# story at all has to come back as a row with 0, and a WHERE would drop exactly
# the row an operator would want to see. Same shape as `warm._COVERAGE_SQL`.
_THEMES_SQL = text(
    """
    SELECT t.id, t.slug, t.display_name, t.emoji, count(s.id) AS servable_stories
    FROM themes t
    LEFT JOIN stories s
      ON s.theme_id = t.id
     AND s.status = ANY(:statuses)
    GROUP BY t.id, t.slug, t.display_name, t.emoji
    ORDER BY t.id
    """
)

_STAGES_SQL = text("SELECT id, name, description FROM stages ORDER BY id")


@router.get("/api/themes", response_model=list[ThemeOut])
async def list_themes(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> list[ThemeOut]:
    """Every seeded theme, in seed order, with its servable depth.

    Ordered by id and never filtered, so the ids the SPA's seed-mirror fallback
    carries are the ids this route returns (`lib/api.ts`, DEVIATIONS 8.61).
    """
    response.headers["cache-control"] = CACHE_CONTROL
    rows = (await session.execute(_THEMES_SQL, {"statuses": list(SERVABLE_STATUSES)})).all()
    return [
        ThemeOut(
            id=int(r.id),
            slug=str(r.slug),
            display_name=str(r.display_name),
            emoji=str(r.emoji),
            servable_stories=int(r.servable_stories),
        )
        for r in rows
    ]


@router.get("/api/stages", response_model=list[StageOut])
async def list_stages(
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> list[StageOut]:
    """§4's eleven stages in id order — which is curriculum order."""
    response.headers["cache-control"] = CACHE_CONTROL
    rows = (await session.execute(_STAGES_SQL)).all()
    return [StageOut(id=int(r.id), name=str(r.name), description=str(r.description)) for r in rows]
