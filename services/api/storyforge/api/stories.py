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

A story that is picked and then refused by one of the two gates below (no
comprehension question, or a serve-time decodability failure) is **excluded and the
ladder carries on descending until the pool at or below this reader is exhausted**
(`_serve_attempt_budget`). That was three attempts for the whole request until
AUDIT-PHASE7 F4 — a rung of unservable rows spent them all at the top and the child
got a 503 while a correct story sat one stage down — and then `3 * 2 * stage` of
them, which is the same defect with a bigger number: one rung can still spend a
budget that is a formula rather than a fact about the pool (DEVIATIONS 9.71).

**Errors that remain errors.** The never-an-error invariant covers *pool
exhaustion*, not requests that name something which does not exist. An unknown
stage or theme is a caller bug — kid mode picks from `GET /api/themes` — and is
answered 404 exactly as `POST /api/validate` answers an unseeded stage. The one
5xx is a completely empty pool (`make warm` never ran), which is an operator
failure at boot, not a state a kid can reach at runtime.

`status='rejected'` stories are never served by any path, from any rung, by id or
by request. They failed the decodability gate or moderation and are retained only
as eval evidence (§7, warm.py).

**Nor is a story with no comprehension question, whatever its status**
(`ANSWERABLE_SQL`, DEVIATIONS 8.50). §11.3 makes the question screen half of what
a session is, and §8 takes every promotion signal it has from a `tap_word` answer,
so a story with no `questions` row is a session that reads and then just stops —
the child earns nothing and no counter moves. Six `warm` stories were in that
state after a full `make warm`, and every check in the codebase looked at
`status`, which was still `warm`, and passed. It is now a predicate on the row,
checked in `_SERVE_SQL`, again on the loaded DTO, and again in `GET
/api/stories/{id}` — and, decisively, in `warm.pool_coverage`, because a gate that
counts stories the serving path will not serve reports a pair as full while every
child at it gets a silent §7 fallback (8.51).

**The stage comes from the CHILD, never from the caller** (DEVIATIONS 7.21).
The body is §9's `{kid_id, theme_id}`: `kid_id` is resolved through
`auth.owned_kid`, so the anchor for every decision above is a row this caller
owns, and an id belonging to another family is the same 404 a nonexistent one
gets. This was not always true. Until the Phase 6 audit the body carried a raw
`stage_id` (DEVIATIONS 4.7, deferred when `kids` had no owner yet), and the
paragraph above called `stage_id <= :stage` "a child-safety bound" while the
number it bounded was chosen by the client. `/read/1648?stage=12` served a
stage-1 reader a story measuring **13.0% decodable at their own stage**. §0 says
the LLM is untrusted and a pure function is the gate; the client gets exactly the
same treatment.

`stage_id` survives as an **admin-only** preview knob — §11's admin portal and
the eval path both need to ask for a stage with no child in the room — and may
never be combined with `kid_id`. It grants an admin nothing new: `GET
/api/stories/{id}` below serves any servable story to any authenticated parent
regardless of stage, because a story is a pool row and not a per-family artifact.

Both endpoints require a verified Clerk parent session (§9). Note what that does
and does not mean here: the same warm story is served to every child at that
stage, so there is no owner to scope `GET /api/stories/{id}` to beyond "is
authenticated". §9's "owner-scoped" is satisfied by the routes that DO carry
ownership: a story only enters a child's record through `POST /api/readings`,
which resolves `kid_id` through `auth.owned_kid` **and now also refuses a story
above that kid's stage** (DEVIATIONS 7.22). The `rejected` filter below is the
other content boundary on this module and it is not a matter of who is asking.

**Defence in depth.** The story picked below is re-validated against the
reader's own stage before it is returned (`_is_decodable_for`, DEVIATIONS 7.23).
The pipeline already gated it at generation time, and §5.7c monotonicity says a
story decodable at stage N stays decodable above it — so this should never fire.
It exists because C2 existed: a story that is 13% decodable *for the child
receiving it* should be inexpressible, not merely unlikely, and `validate_text`
is pure and costs ~0.5 ms.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.auth import Principal, owned_kid, require_parent
from storyforge.db import get_session
from storyforge.decoder import mastered_ids, validate_text

# Imported, not restated: §1's 95% is one number and the serve-time re-check
# must be the same gate the pipeline applied, not a second opinion that can
# drift away from it.
from storyforge.pipeline import DECODABILITY_GATE

log = logging.getLogger(__name__)

router = APIRouter(tags=["stories"])

# §7: `complete` (already read by some kid) stays in rotation behind `warm`
# through last_served_at ordering. `rejected` is not in this set and never will
# be — that is the child-safety invariant this module exists to hold.
SERVABLE_STATUSES = ("warm", "complete")

#: The second half of "servable", and the reason it is a predicate on the ROW
#: rather than a fourth `status` value (DEVIATIONS 8.50).
#:
#: A story with no `questions` row is a dead-end session: the child reads it, the
#: question screen never comes, and §8 gets no `tap_word` answer — so no
#: `gpc_stats` movement and no promotion signal. Six `warm` stories were in
#: exactly that state after a full `make warm` (stages 7, 8 and 10), and nothing
#: refused them, because `status` was the only thing either this module or the
#: coverage gate ever looked at. `warm.py`'s own docstring called it "a defined,
#: servable state (§7)"; it is a defined state and it is not a servable one.
#:
#: Not a status, for the reason the Phase 4 block of DEVIATIONS is about: those
#: rows are repaired by `warm.run_backfill`, which selects
#: `status = ANY(SERVABLE_STATUSES)`, so moving them out of that set would put
#: them beyond the reach of the one command that fixes them — the same
#: forward-only trap the §13 question pass fell into. The story stays `warm`, and
#: `rejected` would be a lie: it failed neither the gate nor moderation.
#:
#: `{story}` is the alias of `stories` in the caller's statement. Imported and
#: formatted by everything that has an opinion about servability — `_SERVE_SQL`
#: below, `POST /api/readings`, and `warm.pool_coverage`/`warm.pool_depth` — and
#: never re-spelled, exactly as `SERVABLE_STATUSES` is not (8.51). The gate and
#: the serving path disagreeing about which stories exist is how a pair reports
#: itself full while every child at it is served a silent §7 fallback.
ANSWERABLE_SQL = "EXISTS (SELECT 1 FROM questions q WHERE q.story_id = {story}.id)"

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


def _rate_limit_key(request: Request, principal: Principal) -> str:
    """Identify the caller.

    §9 says per *user*, and now that Clerk verifies one, this keys on the Clerk
    id rather than the client address — which is what DEVIATIONS 4.10 said would
    change when auth landed. The IP was both too coarse (a whole family behind
    one NAT shared a budget) and too weak (rotating addresses reset it); a
    `clerk_id` cannot be spoofed without a Clerk-signed token.

    Still in-process, so behind N workers the effective limit remains 10N/min —
    that half of DEVIATIONS 4.10 needs a shared counter and is Phase 7's.
    """
    if principal.clerk_id:
        return f"user:{principal.clerk_id}"
    return f"ip:{request.client.host if request.client else 'anonymous'}"


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
    """§9 `POST /api/stories/request {kid_id, theme_id}`.

    `kid_id` is the anchor and the only one a parent may use. The stage is read
    from the kid row after `auth.owned_kid` has proved the caller owns it, so
    there is no number in this body that decides what a child reads.

    `stage_id` is the admin preview knob (module docstring, DEVIATIONS 7.21).
    It is `None` for every parent request and is refused outright for one, which
    is checked in the handler rather than here: a 422 that says "stage_id is
    admin-only" is a better answer than a schema that pretends the field does
    not exist.
    """

    kid_id: int | None = Field(default=None, ge=1)
    stage_id: int | None = Field(default=None, ge=1, le=11)
    # Kid mode holds slugs (`GET /api/themes`); the admin portal holds ids.
    theme_id_or_slug: int | str


# --- queries -----------------------------------------------------------------
# One statement so the pick and the stamp cannot interleave. SKIP LOCKED means
# two concurrent requests take two different stories instead of both taking the
# least-recently-served one.
#
# `:excluded` carries the ids a previous attempt served and then rejected at the
# serve-time decodability re-check. It is never empty (the caller seeds it with
# a sentinel), so Postgres always knows the array's element type.
_SERVE_SQL = text(
    f"""
    UPDATE stories SET last_served_at = now()
    WHERE id = (
        SELECT id FROM stories
        WHERE status = ANY(:statuses) AND stage_id <= :stage
          AND NOT (id = ANY(:excluded))
          AND {ANSWERABLE_SQL.format(story="stories")}
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

# Every story the request above can ever return for this reader, before
# `:excluded` takes any of them away — `_SERVE_SQL`'s `WHERE` clause with the
# `ORDER BY` and the exclusion removed. It sits here, next to the statement it
# mirrors, for `ANSWERABLE_SQL`'s reason: a count that stops agreeing with the
# pick is a budget that stops meaning anything (DEVIATIONS 8.51, 9.71).
_SERVE_CANDIDATES_SQL = text(
    f"""
    SELECT count(*) FROM stories
    WHERE status = ANY(:statuses) AND stage_id <= :stage
      AND {ANSWERABLE_SQL.format(story="stories")}
    """
)


async def _serve_attempt_budget(session: AsyncSession, stage_id: int) -> int:
    """How many picks one request may make: the size of the set it picks FROM.

    **A fact about the pool, not a formula over the ladder**, and that distinction
    is AUDIT-PHASE7 **F4**, filed twice. It was `range(SERVE_ATTEMPTS)` = 3 picks
    for the whole request: three unservable rows at the top rung spent it, §7's
    ladder **never descended**, and the child was answered **503** while a correct
    story sat one stage down. DEVIATIONS 9.66 made it `SERVE_ATTEMPTS * 2 * stage`
    and called that "`SERVE_ATTEMPTS` on every rung the ladder can reach" — but the
    loop has no per-rung reset, so one rung still spends all of it, and the
    boundary just moved. Measured on a copy of the live pool: a stage-1 reader with
    24 good stories in the pool got 0 errors in 30 requests at five planted bad
    rows and four 503s at six; a stage-2 reader descended on 12 of 12 requests at
    eleven bad rows and on **0 of 12** at twelve (DEVIATIONS 9.71).

    §01 says a child never sees an error and §7 makes the warm pool the answer to
    *every* failure path; a 503 with a readable story in the database is the worst
    version of one.

    So the budget is now the number of servable, answerable stories at or below
    this reader's stage, which is the exact point at which the walk can have
    nothing left to look at: every rejection appends the picked id to `:excluded`
    and `_SERVE_SQL` will not return an excluded id, so after N picks an N-row
    candidate set is empty. **The loop stops when the pool is genuinely exhausted
    and at no other time**, which is what makes the 503 mean what its text says.

    Rejected, in DEVIATIONS 9.72: a per-(stage, theme) tier counter — a stage-1
    reader has ONE rung, so the good stories are IN the bad rung and abandoning it
    after three failures is the same 503; a larger constant — the same entry a
    third time; a wall-clock budget — the answer would depend on how busy the box
    is. And, from 9.67 and unchanged, filtering `_SERVE_SQL` on
    `stories.decodability >= :gate`: the rows this catches claim `1.0` in that
    column, exactly like the four live stories whose stored verdict was a fiction,
    and only re-running the decoder can see it.

    Costs one `count(*)` per request — 0.582 ms on the live pool, on
    `ix_stories_pool` (DEVIATIONS 9.73). The healthy path is one pick; the count
    only decides when to give up.
    """
    return int(
        (
            await session.execute(
                _SERVE_CANDIDATES_SQL,
                {"statuses": list(SERVABLE_STATUSES), "stage": stage_id},
            )
        ).scalar_one()
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


async def _resolve_stage(
    session: AsyncSession, principal: Principal, body: StoryRequest
) -> int:
    """The stage this request is anchored to (§9, DEVIATIONS 7.21).

    Exactly one anchor is legal per request, and the parent-facing one is the
    child. `owned_kid` is what makes it an anchor rather than another number the
    caller typed: it 404s for a kid this caller does not own, in a body
    byte-identical to the one a nonexistent kid gets.
    """
    if body.kid_id is not None and body.stage_id is not None:
        raise HTTPException(
            status_code=422,
            detail=(
                "send kid_id or stage_id, not both — a child's stage is a fact about "
                "the child, so there is nothing for a second opinion to mean (§9)"
            ),
        )

    if body.kid_id is not None:
        kid = await owned_kid(session, principal, body.kid_id)
        return int(kid.stage_id)

    if body.stage_id is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "kid_id is required (§9) — the stage a story is written for is read "
                "from the reader, never taken from the request"
            ),
        )

    if not principal.is_admin:
        raise HTTPException(
            status_code=422,
            detail=(
                "stage_id is an admin preview field; send kid_id instead (§9). A "
                "client-chosen stage decides what a child reads, so it is not a "
                "client-chosen number."
            ),
        )
    return int(body.stage_id)


async def _sight_words(session: AsyncSession, stage_id: int) -> frozenset[str]:
    """Cumulative sight-word allowlist at this stage (§4) — same query as §5.6."""
    rows = await session.execute(
        text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": stage_id}
    )
    return frozenset(r[0] for r in rows)


def _is_decodable_for(dto: StoryDTO, stage_id: int, sight: frozenset[str]) -> float:
    """Re-run §5's gate on the story about to be served, at the READER's stage.

    Not the story's own stage: the question a serve-time check has to answer is
    "can the child in front of this screen read this", which is what nothing in
    the request path ever asked (DEVIATIONS 7.23). The inputs are the pipeline's
    — `mastered_ids`, the cumulative sight words, and the story's own persisted
    story words, which are exempt from the denominator by §5.1 because they ship
    as picture chips with audio.
    """
    body = " ".join(seg.body for seg in dto.segments).strip()
    if not body:
        return 0.0
    report = validate_text(
        body,
        mastered_ids(stage_id),
        sight,
        frozenset(w.lower() for w in dto.story_words),
    )
    return report.decodability


# --- endpoints ---------------------------------------------------------------
@router.post("/api/stories/request", response_model=StoryDTO)
async def request_story(
    body: StoryRequest,
    request: Request,
    principal: Principal = Depends(require_parent),
    session: AsyncSession = Depends(get_session),
) -> StoryDTO:
    """Serve a story for this reader's (stage, theme) from the warm pool (§7).

    Least-recently-served wins; `last_served_at` is stamped as part of the same
    statement that picks the row. `status` is intentionally left alone: §8 defines
    `complete` as "a kid read it", and that fact belongs to `POST /api/readings`
    (Phase 5). Flipping it here would drain the warm pool on every request,
    including previews that no child ever sees, and make `make warm` churn.
    """
    retry_after = limiter.hit(_rate_limit_key(request, principal))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit is {RATE_LIMIT_PER_MINUTE} story requests per minute (§9)",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )

    stage_id = await _resolve_stage(session, principal, body)

    stage = (
        await session.execute(text("SELECT 1 FROM stages WHERE id = :s"), {"s": stage_id})
    ).first()
    if stage is None:
        raise HTTPException(
            status_code=404, detail=f"stage {stage_id} not seeded — run `make seed`"
        )
    theme_id, theme_slug = await _resolve_theme(session, body.theme_id_or_slug)
    sight = await _sight_words(session, stage_id)

    # 0 is not a story id; it seeds the array so Postgres can type it, and it
    # keeps the empty-exclusion case on the same code path as the rest.
    excluded: list[int] = [0]
    undecodable: list[tuple[int, float]] = []
    dead_end: list[int] = []

    # Every rejection below appends to `excluded`, which is what makes the next
    # pick step ALONG §7's ladder — through the rest of this rung and then down —
    # rather than re-offer the row just refused (DEVIATIONS 9.66).
    # `stage_id <= :stage` never moves, so descending is all it can do.
    #
    # The budget is the size of the candidate set, so it is spent at exactly the
    # moment `excluded` covers every story this reader could have been served and
    # not one pick before (DEVIATIONS 9.71). It is a bound on a loop that already
    # terminates, kept so that a future branch which forgets to exclude the row it
    # rejected is a 503 with a log line rather than a hung request.
    for _attempt in range(await _serve_attempt_budget(session, stage_id)):
        served = (
            await session.execute(
                _SERVE_SQL,
                {
                    "statuses": list(SERVABLE_STATUSES),
                    "stage": stage_id,
                    "theme": theme_id,
                    "excluded": excluded,
                },
            )
        ).first()
        await session.commit()

        if served is None:
            break

        dto = await _load_story(session, served.id)
        if dto is None:  # pragma: no cover — deleted between the UPDATE and the read
            raise HTTPException(status_code=503, detail="story vanished mid-request")

        if not dto.questions:
            # Should be unreachable: `_SERVE_SQL` requires `ANSWERABLE_SQL`. Kept
            # for the reason 7.23 gives about the check below — this is the state
            # a child actually meets as a session that just stops, so it should be
            # inexpressible rather than merely unlikely, and reading a list that
            # is already loaded costs nothing. It also covers the one window the
            # SQL cannot: a question row deleted between the `UPDATE … RETURNING`
            # and this read.
            log.error(
                "SERVE DEAD END: story %s is servable and carries no questions (§11.3) "
                "— refusing to serve it and picking another. Repair with "
                "`python -m storyforge.warm --backfill --only %s`",
                dto.id,
                dto.id,
            )
            dead_end.append(dto.id)
            excluded.append(dto.id)
            continue

        decodability = _is_decodable_for(dto, stage_id, sight)
        if decodability < DECODABILITY_GATE:
            # Should be unreachable: the pipeline gated this story at generation
            # and §5.7c monotonicity carries the verdict upward. Loud, because
            # reaching it means a row in the pool is not what `stories` says.
            log.error(
                "SERVE GATE: story %s measures %.3f decodable at stage %s (§1 needs "
                "%.2f) — refusing to serve it and picking another",
                dto.id,
                decodability,
                stage_id,
                DECODABILITY_GATE,
            )
            undecodable.append((dto.id, decodability))
            excluded.append(dto.id)
            continue

        dto.fallback = _fallback(dto, stage_id, theme_id, theme_slug)
        return dto

    # Not a kid-reachable state: either no non-rejected story exists at or below
    # this stage anywhere in the database (`make warm` has never run), or every
    # candidate failed the gate above. Fabricating a story, or serving one the
    # gate rejected, would each be worse than saying so.
    detail = (
        f"no story available at or below stage {stage_id} — "
        "the warm pool is empty, run `make warm`"
    )
    if undecodable:
        detail = (
            f"no story at or below stage {stage_id} passes §1's "
            f"{DECODABILITY_GATE:.0%} gate for this reader; "
            f"rejected {', '.join(f'{i} ({d:.1%})' for i, d in undecodable)}"
        )
    if dead_end:
        detail = (
            f"no story at or below stage {stage_id} both passes §1's gate and "
            f"carries a comprehension question (§11.3); story/stories "
            f"{', '.join(str(i) for i in dead_end)} have none — repair with "
            f"`python -m storyforge.warm --backfill --only "
            f"{','.join(str(i) for i in dead_end)}`"
        )
    raise HTTPException(status_code=503, detail=detail)


@router.get("/api/stories/{story_id}", response_model=StoryDTO)
async def get_story(
    story_id: int,
    principal: Principal = Depends(require_parent),
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
    if not dto.questions:
        # The same boundary as `_SERVE_SQL`'s (DEVIATIONS 8.50), applied here for
        # the same reason the `rejected` filter is: a content rule that only the
        # request endpoint enforced would be one `GET` away from irrelevant. Kid
        # mode re-fetches by id (`apps/web/src/lib/api.ts`), so this path can put
        # a dead-end session in front of a child just as directly.
        raise HTTPException(status_code=404, detail=f"story {story_id} not found")
    return dto
