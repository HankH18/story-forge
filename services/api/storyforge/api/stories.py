"""Story request + fetch endpoints (§9), served from the warm pool (§7).

`POST /api/stories/request` is the kid-mode entry point. §7 makes the warm pool
the answer to *every* failure path — validation_fail, api_fail, moderation_fail,
budget_exhausted, timeout all terminate in a warm-pool serve — so "the kid never
sees an error" is a property of this endpoint, not of the pipeline.

Two consequences shape the code below.

**The fallback ladder.** When the requested (stage, theme) pool is empty we serve
the best available story rather than an error. "Best" is decided by the
decodability guarantee, which is the product:

1. requested stage, requested theme      — what was asked for
2. requested stage, any theme            — theme is preference; stage is safety
3. nearest LOWER stage, requested theme
4. nearest LOWER stage, any theme

A story written for a *higher* stage may contain GPCs this reader has never been
taught, which breaks the guarantee outright, so the ladder never climbs — the
`stage_id <= :stage` filter is a child-safety bound, not an optimisation. A
*lower* stage is always safe: the GPC inventory and the sight-word list are both
cumulative (§4), so anything decodable at stage N is decodable at stage N+1
(the §5.7c monotonicity property). It is easier than ideal, never unsafe.
Ties inside a rung break least-recently-served, then by id for determinism.

The whole ladder is one `ORDER BY`; see `_SERVE_SQL`.

**Errors that remain errors.** The never-an-error invariant covers *pool
exhaustion*, not requests that name something which does not exist. An unknown
stage or theme is a caller bug — kid mode picks from `GET /api/themes` — and is
answered 404 exactly as `POST /api/validate` answers an unseeded stage. The one
5xx is a completely empty pool (`make warm` never ran), which is an operator
failure at boot, not a state a kid can reach at runtime.

`status='rejected'` stories are never served by any path, from any rung, by id or
by request. They failed the decodability gate or moderation and are retained only
as eval evidence (§7, warm.py).

Deviations from §9, both because their dependencies land later:
- The body is `{stage_id, theme_id_or_slug}` rather than `{kid_id, theme_id}`;
  `kids` arrives in Phase 5, at which point kid_id → stage_id is one lookup.
- §9 marks `GET /api/stories/{id}` owner-scoped; Clerk lands in Phase 6.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.db import get_session

router = APIRouter(tags=["stories"])

# §7: `complete` (already read by some kid) stays in rotation behind `warm`
# through last_served_at ordering. `rejected` is not in this set and never will
# be — that is the child-safety invariant this module exists to hold.
SERVABLE_STATUSES = ("warm", "complete")

RATE_LIMIT_PER_MINUTE = 10  # §9
RATE_LIMIT_WINDOW_S = 60.0


# --- rate limiting -----------------------------------------------------------
class _SlidingWindowLimiter:
    """In-process sliding-window limiter.

    LIMITATION, stated rather than hidden: this counts requests *per process*.
    Behind multiple uvicorn workers (or multiple containers) the effective limit
    is 10/min × workers, so this is not a correct implementation of §9's
    10/min/user — it is a cheap guard against a runaway client that adds no
    dependency and no shared state. A correct limiter needs a store both workers
    can see (Redis INCR with an expiry, or a Postgres counter). Phase 7 picks the
    deployment topology; that is when this should be replaced.

    No lock: FastAPI runs async handlers on one event-loop thread, and there is
    no await between the read and the write below, so the check-and-append is
    atomic with respect to other requests.
    """

    def __init__(self, limit: int, window_s: float) -> None:
        self._limit = limit
        self._window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> float | None:
        """Record a request. Returns seconds-to-wait if it should be refused."""
        now = time.monotonic()
        if len(self._hits) > 1024:
            self._prune(now)
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window_s:
            hits.popleft()
        if len(hits) >= self._limit:
            return self._window_s - (now - hits[0])
        hits.append(now)
        return None

    def _prune(self, now: float) -> None:
        stale = [k for k, d in self._hits.items() if not d or now - d[-1] >= self._window_s]
        for k in stale:
            del self._hits[k]

    def clear(self) -> None:
        """Reset all counters. Tests only."""
        self._hits.clear()


limiter = _SlidingWindowLimiter(RATE_LIMIT_PER_MINUTE, RATE_LIMIT_WINDOW_S)


def _rate_limit_key(request: Request) -> str:
    """Identify the caller.

    §9 says per *user*; users arrive with Clerk in Phase 6 (§13). Until the JWT
    dependency exists the closest honest stand-in is the client address, which
    over-limits families behind one NAT and under-limits a client that rotates
    addresses. Swap this for the Clerk user id, not the IP, the moment auth lands.
    """
    return request.client.host if request.client else "anonymous"


# --- DTOs (§11.2) ------------------------------------------------------------
class AssetDTO(BaseModel):
    """A curated illustration (§12). Absent → the UI draws a striped placeholder."""

    id: int
    url: str
    alt_text: str
    tags: list[str] = Field(default_factory=list)


class SegmentDTO(BaseModel):
    """One screen of the reading view (§11.2)."""

    id: int
    idx: int
    body: str
    scene_tags: list[str] = Field(default_factory=list)
    # §10 branching: present in the schema now, generated from Phase 5 on.
    parent_segment_id: int | None = None
    choice_label: str | None = None
    asset: AssetDTO | None = None


class QuestionDTO(BaseModel):
    """A comprehension question (§11.3).

    The answer key is deliberately absent. Grading happens server-side at
    `POST /api/readings/{id}/answers` (§9) because that is also what writes
    `answers` and `gpc_stats` (§8) — so shipping the key to the client would buy
    no round trip and would make the mastery signal trivially forgeable.

    "Absent" has to mean absent in *fact*, not by intention. `payload` used to be
    forwarded verbatim, and `tap_word` payloads carried `target_grapheme` — from
    which the answer is `[o for o in options if o.startswith(grapheme)][0]`,
    because the generator PROVES exactly one option starts with it. The node no
    longer writes it there, and `_public_payload` strips it (and any other
    key-shaped field) on the way out, so a row written by an older build cannot
    leak one either.
    """

    id: int
    segment_id: int | None = None
    idx: int = 0
    qtype: str
    prompt: str
    # Projection of payload["options"] for both qtypes (§8 model docstring).
    options: list[str] = Field(default_factory=list)
    # Everything the UI may see, minus answer-key fields (see `_public_payload`).
    payload: dict[str, Any] = Field(default_factory=dict)
    # The parent story's distinct story words (§4). `mcq2` options are validated
    # with the story-word exemption — words that are by definition NOT decodable
    # at this stage — so an option can legitimately contain "rocket" or "moon".
    # The story body renders those as picture chips from `StoryDTO.story_words`;
    # without the same list here the question screen has no way to chip them and
    # a stage-1 reader is handed a word nobody has taught them.
    story_words: list[str] = Field(default_factory=list)


class FallbackDTO(BaseModel):
    """Set when the served story is not the requested (stage, theme) pair.

    Never an error — it exists so kid mode can say "we picked a different one"
    and so the admin portal can see which pairs need refilling.
    """

    kind: Literal["other_theme", "lower_stage", "lower_stage_other_theme"]
    requested_stage_id: int
    requested_theme_id: int
    requested_theme_slug: str
    detail: str


class StoryDTO(BaseModel):
    """Everything kid mode needs to render a story with no second round trip."""

    id: int
    stage_id: int
    theme_id: int
    theme_slug: str = ""
    theme_display_name: str = ""
    status: str
    title: str
    decodability: float
    # Distinct story words actually used — one picture chip each (§4, §11.2).
    story_words: list[str] = Field(default_factory=list)
    segments: list[SegmentDTO] = Field(default_factory=list)
    # Empty until the QUESTION_GEN node populates them (§7); never null.
    questions: list[QuestionDTO] = Field(default_factory=list)
    fallback: FallbackDTO | None = None


class StoryRequest(BaseModel):
    stage_id: int = Field(ge=1, le=12)
    # Kid mode holds slugs (`GET /api/themes`); the admin portal holds ids.
    theme_id_or_slug: int | str


# --- queries -----------------------------------------------------------------
# One statement so the pick and the stamp cannot interleave. SKIP LOCKED means
# two concurrent requests take two different stories instead of both taking the
# least-recently-served one.
_SERVE_SQL = text(
    """
    UPDATE stories SET last_served_at = now()
    WHERE id = (
        SELECT id FROM stories
        WHERE status = ANY(:statuses) AND stage_id <= :stage
        ORDER BY stage_id DESC,                  -- never above; nearest below
                 (theme_id = :theme) DESC,       -- requested theme first
                 last_served_at ASC NULLS FIRST, -- least recently served (§7)
                 id ASC                          -- deterministic tiebreak
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id
    """
)

_STORY_SQL = text(
    """
    SELECT st.id, st.stage_id, st.theme_id, st.status, st.title, st.decodability,
           st.story_words, t.slug AS theme_slug, t.display_name AS theme_display_name
    FROM stories st
    LEFT JOIN themes t ON t.id = st.theme_id
    WHERE st.id = :id
    """
)

# LEFT JOIN twice over: assets may hold no row yet (the SCENE_TAGGING node is
# Phase 4 work in parallel), and `story_segments.asset_id` is a plain int rather
# than a FK (DEVIATIONS 3.9) so it can dangle. Either way the segment renders
# with asset=null and the UI shows its placeholder. A URL is never invented.
_SEGMENTS_SQL = text(
    """
    SELECT sg.id, sg.idx, sg.body, sg.scene_tags, sg.parent_segment_id, sg.choice_label,
           a.id AS asset_id, a.url AS asset_url, a.alt_text AS asset_alt_text,
           a.tags AS asset_tags
    FROM story_segments sg
    LEFT JOIN assets a ON a.id = sg.asset_id
    WHERE sg.story_id = :id
    ORDER BY sg.idx
    """
)

_QUESTIONS_SQL = text(
    """
    SELECT id, segment_id, idx, qtype, prompt, payload
    FROM questions
    WHERE story_id = :id
    ORDER BY idx, id
    """
)


async def _resolve_theme(session: AsyncSession, value: int | str) -> tuple[int, str]:
    raw = str(value).strip()
    row = (
        await session.execute(
            text(
                "SELECT id, slug FROM themes WHERE slug = :slug OR id = :id "
                "ORDER BY (slug = :slug) DESC LIMIT 1"
            ),
            {"slug": raw.lower(), "id": int(raw) if raw.isdigit() else None},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown theme {raw!r} — run `make seed`")
    return int(row.id), str(row.slug)


def _options(payload: Any) -> list[str]:
    """Defensive projection — the question-gen node is being built in parallel."""
    if isinstance(payload, dict) and isinstance(payload.get("options"), list):
        return [str(o) for o in payload["options"]]
    return []


#: Payload keys that identify the correct option and therefore must not be
#: served (§9, DEVIATIONS 4.9). `target_grapheme` is the live one — exactly one
#: option starts with it, by construction. The rest are the shapes the `answer`
#: column uses, listed so a future node that writes the key into the wrong column
#: fails safe rather than shipping it.
ANSWER_KEY_FIELDS = frozenset({"target_grapheme", "correct", "index", "answer"})


def _public_payload(payload: Any) -> dict[str, Any]:
    """The payload minus anything that gives the answer away.

    A denylist rather than an allowlist because the payload is deliberately
    open-ended — the question-gen node adds fields (`cue_word`) and they should
    reach the UI without a change here. The cost of that openness is exactly this
    filter, and it is applied at the one place every question leaves the server.
    """
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k not in ANSWER_KEY_FIELDS}


async def _load_story(session: AsyncSession, story_id: int) -> StoryDTO | None:
    story = (await session.execute(_STORY_SQL, {"id": story_id})).first()
    if story is None:
        return None

    segments = (await session.execute(_SEGMENTS_SQL, {"id": story_id})).all()
    questions = (await session.execute(_QUESTIONS_SQL, {"id": story_id})).all()

    return StoryDTO(
        id=story.id,
        stage_id=story.stage_id,
        theme_id=story.theme_id,
        theme_slug=story.theme_slug or "",
        theme_display_name=story.theme_display_name or "",
        status=story.status,
        title=story.title,
        # Numeric(5,4) arrives as Decimal; JSON wants a float.
        decodability=float(story.decodability or 0),
        story_words=list(story.story_words or []),
        segments=[
            SegmentDTO(
                id=s.id,
                idx=s.idx,
                body=s.body,
                scene_tags=list(s.scene_tags or []),
                parent_segment_id=s.parent_segment_id,
                choice_label=s.choice_label,
                asset=(
                    AssetDTO(
                        id=s.asset_id,
                        url=s.asset_url,
                        alt_text=s.asset_alt_text,
                        tags=list(s.asset_tags or []),
                    )
                    if s.asset_id is not None and s.asset_url
                    else None
                ),
            )
            for s in segments
        ],
        questions=[
            QuestionDTO(
                id=q.id,
                segment_id=q.segment_id,
                idx=q.idx,
                qtype=q.qtype,
                prompt=q.prompt,
                options=_options(q.payload),
                payload=_public_payload(q.payload),
                story_words=list(story.story_words or []),
            )
            for q in questions
        ],
    )


def _fallback(dto: StoryDTO, stage_id: int, theme_id: int, theme_slug: str) -> FallbackDTO | None:
    wrong_theme = dto.theme_id != theme_id
    wrong_stage = dto.stage_id != stage_id
    if not wrong_theme and not wrong_stage:
        return None
    if wrong_theme and wrong_stage:
        kind: Literal["other_theme", "lower_stage", "lower_stage_other_theme"] = (
            "lower_stage_other_theme"
        )
    elif wrong_theme:
        kind = "other_theme"
    else:
        kind = "lower_stage"
    return FallbackDTO(
        kind=kind,
        requested_stage_id=stage_id,
        requested_theme_id=theme_id,
        requested_theme_slug=theme_slug,
        detail=(
            f"warm pool empty for stage {stage_id} / {theme_slug}; "
            f"served stage {dto.stage_id} / {dto.theme_slug or dto.theme_id} instead"
        ),
    )


# --- endpoints ---------------------------------------------------------------
@router.post("/api/stories/request", response_model=StoryDTO)
async def request_story(
    body: StoryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> StoryDTO:
    """Serve a story for (stage, theme) from the warm pool (§7).

    Least-recently-served wins; `last_served_at` is stamped as part of the same
    statement that picks the row. `status` is intentionally left alone: §8 defines
    `complete` as "a kid read it", and that fact belongs to `POST /api/readings`
    (Phase 5). Flipping it here would drain the warm pool on every request,
    including previews that no child ever sees, and make `make warm` churn.
    """
    retry_after = limiter.hit(_rate_limit_key(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit is {RATE_LIMIT_PER_MINUTE} story requests per minute (§9)",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )

    stage = (
        await session.execute(text("SELECT 1 FROM stages WHERE id = :s"), {"s": body.stage_id})
    ).first()
    if stage is None:
        raise HTTPException(
            status_code=404, detail=f"stage {body.stage_id} not seeded — run `make seed`"
        )
    theme_id, theme_slug = await _resolve_theme(session, body.theme_id_or_slug)

    served = (
        await session.execute(
            _SERVE_SQL,
            {"statuses": list(SERVABLE_STATUSES), "stage": body.stage_id, "theme": theme_id},
        )
    ).first()
    await session.commit()

    if served is None:
        # Not a kid-reachable state: it means no non-rejected story exists at or
        # below this stage anywhere in the database, i.e. `make warm` has never
        # run. Fabricating a story would be the one thing worse than saying so.
        raise HTTPException(
            status_code=503,
            detail=(
                f"no story available at or below stage {body.stage_id} — "
                "the warm pool is empty, run `make warm`"
            ),
        )

    dto = await _load_story(session, served.id)
    if dto is None:  # pragma: no cover — deleted between the UPDATE and the read
        raise HTTPException(status_code=503, detail="story vanished mid-request")
    dto.fallback = _fallback(dto, body.stage_id, theme_id, theme_slug)
    return dto


@router.get("/api/stories/{story_id}", response_model=StoryDTO)
async def get_story(
    story_id: int,
    session: AsyncSession = Depends(get_session),
) -> StoryDTO:
    """Re-fetch a story by id (§9). Same DTO; does not touch `last_served_at`."""
    dto = await _load_story(session, story_id)
    if dto is None:
        raise HTTPException(status_code=404, detail=f"story {story_id} not found")
    if dto.status not in SERVABLE_STATUSES:
        # Child-safety invariant: a rejected story failed the decodability gate
        # or moderation. It is an eval artifact and is not fetchable by id either.
        raise HTTPException(status_code=404, detail=f"story {story_id} not found")
    return dto
