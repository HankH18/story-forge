"""Readings, answers, progress and stage promotion (§9), the write side of §8.

`api/stories.py` hands a child a story. This module records what happened next:
which segments they chose (§10), which questions they got right, and — once the
reading is complete — what that says about the grapheme-phoneme correspondences
they can actually decode (§8). It is the only writer of `gpc_stats`, and
`storyforge.mastery` holds every decision it makes as a pure function.

Four properties are load-bearing here.

**The answer key never leaves the server.** `api/stories.py` omits it from the
StoryDTO on purpose, so `POST /api/readings/{id}/answers` takes the child's tap
and returns a verdict. §8 promotes on these counts; a client that could see the
key could manufacture mastery. `mastery.grade_answer` is total over every Python
value, so a hostile payload is a verdict or a 4xx, never a 500.

**Completion is idempotent, by a latch rather than by a check.**
`UPDATE readings SET completed_at = now() WHERE id = :id AND completed_at IS
NULL RETURNING ...` is one statement, so exactly one caller can ever observe the
transition — two concurrent completes, or a client that retries on a dropped
response, cannot double-count `gpc_stats` or promote twice. A read-then-write
would race; this cannot.

**Kids have no auth identity and no PII.** §13: a kid row is a first name and a
stage. There is no email, no date of birth, no free-text field a child types
into, and `answers` stores a boolean rather than what the child tapped. A kid is
therefore never a principal — every route below authenticates the *parent* and
then reaches the kid through `auth.owned_kid` / `auth.owned_reading`, both of
which filter on `kids.parent_id`. A kid or reading id belonging to another
family is 404 here, not 403: the response must not confirm that the id exists.
Kid creation and listing moved to `api/kids.py` when that ownership arrived.

**Branching is schema-only.** §10's generator has not landed, so no story yet
contains a branch point. `POST /api/readings/{id}/choice` is real anyway — it
validates the segment against the reading's own story and appends to
`readings.path`, and it already rejects a choice label the story does not offer
if the segment does have children. What is missing is stories that branch, not
the recording of branches.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.api.kids import KidDTO
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.auth import Principal, owned_kid, owned_reading, require_parent
from storyforge.db import get_session
from storyforge.mastery import (
    MASTERY_QTYPES,
    THRESHOLDS,
    PromotionDecision,
    WordEvidence,
    accuracy_of,
    evaluate_promotion,
    gpc_increments,
    grade_answer,
    single_word,
)

router = APIRouter(tags=["readings"])

#: §11's dashboard shows "recent readings", not all of them.
RECENT_READINGS_LIMIT = 20
#: A tapped option, not an essay. Bounds the grader's input at the edge.
MAX_RESPONSE_CHARS = 256
MAX_CHOICE_CHARS = 128

BoundedResponse = Annotated[str, StringConstraints(max_length=MAX_RESPONSE_CHARS)]
ChoiceLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CHOICE_CHARS)
]


# --- request bodies ----------------------------------------------------------
class ReadingCreate(BaseModel):
    """§9 `POST /api/readings {kid_id, story_id}`."""

    kid_id: int = Field(ge=1)
    story_id: int = Field(ge=1)


class ChoiceCreate(BaseModel):
    """§9 `POST /api/readings/{id}/choice {segment_id, choice}`."""

    segment_id: int = Field(ge=1)
    #: The branch label the child tapped — §10 renders these as two big cards.
    choice: ChoiceLabel


class AnswerCreate(BaseModel):
    """§9 `POST /api/readings/{id}/answers {question_id, response}`.

    `response` is the option the child tapped: its text, or its 0-based index
    into the `options` the StoryDTO shipped. The union is the outer guard — a
    dict, a list or a null is a 422 here rather than reaching the grader — and
    `mastery.grade_answer` is total anyway, so neither layer is trusted alone.
    """

    question_id: int = Field(ge=1)
    response: BoundedResponse | int

    @field_validator("response", mode="before")
    @classmethod
    def _not_a_boolean(cls, value: object) -> object:
        """`bool` is a subclass of `int`, and Pydantic coerces it.

        Live-observed: `{"response": true}` arrived at the grader as the integer
        `1` and was graded as a tap on option 1 — which happened to be the
        correct answer. `mastery.grade_answer` refuses bools, but it never saw
        one, because the coercion happened first. Rejecting it here is what
        makes the two layers agree; without this the API's behaviour and the
        pure function's documented behaviour differ, and only one of them has a
        test.
        """
        if isinstance(value, bool):
            raise ValueError("response must be the option's text or its index, not a boolean")
        return value


# --- DTOs --------------------------------------------------------------------
class ChoiceDTO(BaseModel):
    """One entry of `readings.path` (§10)."""

    segment_id: int
    choice: str
    at: str


class ReadingDTO(BaseModel):
    """§9 returns "reading_id"; this is that, plus the state a client needs to
    resume without a second call."""

    id: int
    kid_id: int
    story_id: int
    #: The kid's stage when the reading STARTED — see `models.Reading`.
    stage_id: int
    started_at: datetime
    completed_at: datetime | None = None
    choices: list[ChoiceDTO] = Field(default_factory=list)


class AnswerResultDTO(BaseModel):
    """The graded verdict (§9). Still no answer key: a wrong answer is told it
    is wrong, not what the right one was."""

    question_id: int
    #: How THIS response graded — kid mode needs it to show the tap's feedback.
    correct: bool
    #: False when the question had already been answered in this reading. The
    #: first answer is the one that counts, so "tap until green" cannot inflate
    #: `gpc_stats`; the child still gets honest feedback on the later tap.
    recorded: bool
    #: The verdict that will feed mastery — equals `correct` on a first answer.
    recorded_correct: bool


class GpcDeltaDTO(BaseModel):
    gpc_id: str
    n_seen: int
    n_correct: int


class CriterionDTO(BaseModel):
    name: str
    met: bool
    detail: str


class PromotionDTO(BaseModel):
    """§8's rule, its inputs and its verdict — the whole thing, displayable."""

    promote: bool
    from_stage: int
    to_stage: int | None = None
    stories_completed: int
    stories_required: int
    gpc_seen: int
    gpc_correct: int
    accuracy: float | None = None
    accuracy_threshold: float
    min_observations: int
    criteria: list[CriterionDTO] = Field(default_factory=list)


class CompleteResultDTO(BaseModel):
    reading: ReadingDTO
    #: False on a replay. The counters below are then empty and nothing moved.
    newly_completed: bool
    #: True when this kid had already completed THIS story at this stage. The
    #: reading is real and counts as a reading; it just yields no new mastery
    #: evidence (§8). Shipped rather than left implicit because otherwise an
    #: empty `gpc_updates` is ambiguous — it would mean either "you skipped the
    #: questions" or "you have read this one before", and §11.4's celebration
    #: screen has to say something different in each case.
    repeat_reading: bool = False
    #: What this reading added to `gpc_stats` — the celebration screen's deltas
    #: (§11.4: "GPC progress bar deltas").
    gpc_updates: list[GpcDeltaDTO] = Field(default_factory=list)
    promotion: PromotionDTO
    promoted: bool = False


class GpcAccuracyDTO(BaseModel):
    """One row of §11's per-GPC accuracy table."""

    gpc_id: str
    grapheme: str
    phonemes: str
    stage_id: int
    n_seen: int
    n_correct: int
    accuracy: float | None = None
    #: True for the correspondences this stage teaches — the ones promotion
    #: is actually measured over.
    current_stage: bool = False


class RecentReadingDTO(BaseModel):
    id: int
    story_id: int
    story_title: str = ""
    theme_slug: str = ""
    story_stage_id: int
    stage_id: int
    started_at: datetime
    completed_at: datetime | None = None
    questions_answered: int = 0
    questions_correct: int = 0
    choices: list[ChoiceDTO] = Field(default_factory=list)


class ThresholdsDTO(BaseModel):
    stories_required: int
    accuracy_threshold: float
    min_observations: int
    max_stage: int


class ProgressDTO(BaseModel):
    """§9 `GET /api/kids/{id}/progress` → stage, gpc_stats, readings summary."""

    kid: KidDTO
    stage_name: str = ""
    stage_description: str = ""
    readings_started: int = 0
    readings_completed: int = 0
    readings_completed_at_stage: int = 0
    #: §8's thresholds, shipped so the dashboard shows the rule, not just a bar.
    thresholds: ThresholdsDTO
    promotion: PromotionDTO
    gpc_accuracy: list[GpcAccuracyDTO] = Field(default_factory=list)
    recent_readings: list[RecentReadingDTO] = Field(default_factory=list)


# --- queries -----------------------------------------------------------------
# The whole idempotency guarantee. One statement: the row that was NULL becomes
# NOW() and returns; a second caller matches nothing and returns nothing.
_COMPLETE_SQL = text(
    """
    UPDATE readings SET completed_at = now()
    WHERE id = :id AND completed_at IS NULL
    RETURNING id, kid_id, story_id, stage_id, path, started_at, completed_at
    """
)

_APPEND_CHOICE_SQL = text(
    """
    UPDATE readings
    SET path = jsonb_set(
        COALESCE(path, '{}'::jsonb),
        '{choices}',
        COALESCE(path -> 'choices', '[]'::jsonb) || CAST(:entry AS jsonb),
        true
    )
    WHERE id = :id AND completed_at IS NULL
    RETURNING id, kid_id, story_id, stage_id, path, started_at, completed_at
    """
)

# `answers` has a unique constraint on (reading_id, question_id), so DO NOTHING
# is what makes "first answer wins" atomic rather than a check-then-insert race.
_INSERT_ANSWER_SQL = text(
    """
    INSERT INTO answers (reading_id, question_id, correct)
    VALUES (:reading_id, :question_id, :correct)
    ON CONFLICT ON CONSTRAINT uq_answer_reading_question DO NOTHING
    RETURNING id, correct
    """
)

# The JOIN against `gpcs` is a filter, not decoration: `gpc_stats.gpc_id` is a
# FK, and an inventory id the database has never been seeded with would raise a
# 500 mid-completion. Dropping it instead loses one counter; the reading still
# completes and the child still sees their stars.
_UPSERT_GPC_STATS_SQL = text(
    """
    INSERT INTO gpc_stats (kid_id, gpc_id, n_seen, n_correct)
    SELECT :kid_id, d.gpc_id, d.n_seen, d.n_correct
    FROM unnest(CAST(:gpc_ids AS text[]), CAST(:seen AS int[]), CAST(:correct AS int[]))
         AS d(gpc_id, n_seen, n_correct)
    JOIN gpcs g ON g.gpc_id = d.gpc_id
    ON CONFLICT (kid_id, gpc_id) DO UPDATE
        SET n_seen = gpc_stats.n_seen + EXCLUDED.n_seen,
            n_correct = gpc_stats.n_correct + EXCLUDED.n_correct
    RETURNING gpc_id, n_seen, n_correct
    """
)

# §8's accuracy denominator: the correspondences THIS stage teaches.
_STAGE_EVIDENCE_SQL = text(
    """
    SELECT COALESCE(SUM(s.n_seen), 0) AS n_seen,
           COALESCE(SUM(s.n_correct), 0) AS n_correct
    FROM gpc_stats s
    JOIN gpcs g ON g.gpc_id = s.gpc_id
    WHERE s.kid_id = :kid_id AND g.stage_id = :stage_id
    """
)

# §8: "≥5 stories completed at current stage". DISTINCT story_id, because
# `count(*)` counted READINGS — and re-reading one story five times is one story
# completed five times, not five stories.
#
# This was not only forgeable, it was the DEFAULT. `POST /api/stories/request`
# serves least-recently-served from the pool, so wherever a (stage, theme) pair
# holds fewer than five stories a child pressing "read another" is handed the
# same story back and promotes on it. Live: a stage-5 kid reached stage 6 having
# read one story, answering one question about one word ten times, in 31 HTTP
# calls. §8's rule is only worth anything if the count means what it says.
_STORIES_AT_STAGE_SQL = text(
    """
    SELECT count(DISTINCT story_id) FROM readings
    WHERE kid_id = :kid_id AND stage_id = :stage_id AND completed_at IS NOT NULL
    """
)

# The other half of the same hole. The story count was the visible symptom; the
# evidence counters were the deeper one, because `MIN_GPC_OBSERVATIONS = 10`
# (DEVIATIONS 5.5) exists to stop promotion on a single lucky tap — and ten
# replays of that single tap satisfied it exactly. A GPC instance is evidence
# the first time a child meets it in a story; the second time they already know
# what the answer was.
_ALREADY_READ_SQL = text(
    """
    SELECT 1 FROM readings
    WHERE kid_id = :kid_id AND story_id = :story_id AND stage_id = :stage_id
      AND completed_at IS NOT NULL AND id <> :reading_id
    LIMIT 1
    """
)

# Every taught correspondence, observed or not — a zero row is information
# ("never practised"), and dropping it would make the table look complete.
_GPC_ACCURACY_SQL = text(
    """
    SELECT g.gpc_id, g.grapheme, g.phonemes, g.stage_id,
           COALESCE(s.n_seen, 0) AS n_seen, COALESCE(s.n_correct, 0) AS n_correct
    FROM gpcs g
    LEFT JOIN gpc_stats s ON s.gpc_id = g.gpc_id AND s.kid_id = :kid_id
    WHERE g.stage_id <= :stage_id OR s.kid_id IS NOT NULL
    ORDER BY g.stage_id, g.gpc_id
    """
)

_RECENT_READINGS_SQL = text(
    """
    SELECT r.id, r.story_id, r.stage_id, r.path, r.started_at, r.completed_at,
           st.title AS story_title, st.stage_id AS story_stage_id,
           COALESCE(t.slug, '') AS theme_slug,
           count(a.id) AS questions_answered,
           count(a.id) FILTER (WHERE a.correct) AS questions_correct
    FROM readings r
    JOIN stories st ON st.id = r.story_id
    LEFT JOIN themes t ON t.id = st.theme_id
    LEFT JOIN answers a ON a.reading_id = r.id
    WHERE r.kid_id = :kid_id
    GROUP BY r.id, st.title, st.stage_id, t.slug
    ORDER BY r.started_at DESC, r.id DESC
    LIMIT :limit
    """
)

# The mastery evidence for one reading, in one read: what was asked, how it was
# answered, and the stage the story was proved decodable at.
_READING_ANSWERS_SQL = text(
    """
    SELECT q.qtype, q.answer, a.correct
    FROM answers a
    JOIN questions q ON q.id = a.question_id
    WHERE a.reading_id = :reading_id
    ORDER BY a.id
    """
)


# --- helpers -----------------------------------------------------------------
def _choices(path: Any) -> list[ChoiceDTO]:
    """Project `readings.path` defensively — it is JSONB and hand-editable."""
    if not isinstance(path, dict) or not isinstance(path.get("choices"), list):
        return []
    out = []
    for entry in path["choices"]:
        if isinstance(entry, dict) and isinstance(entry.get("segment_id"), int):
            out.append(
                ChoiceDTO(
                    segment_id=entry["segment_id"],
                    choice=str(entry.get("choice", "")),
                    at=str(entry.get("at", "")),
                )
            )
    return out


def _reading_dto(row: Any) -> ReadingDTO:
    return ReadingDTO(
        id=row.id,
        kid_id=row.kid_id,
        story_id=row.story_id,
        stage_id=row.stage_id,
        started_at=row.started_at,
        completed_at=row.completed_at,
        choices=_choices(row.path),
    )


def _promotion_dto(decision: PromotionDecision) -> PromotionDTO:
    return PromotionDTO(
        promote=decision.promote,
        from_stage=decision.from_stage,
        to_stage=decision.to_stage,
        stories_completed=decision.stories_completed,
        stories_required=decision.stories_required,
        gpc_seen=decision.gpc_seen,
        gpc_correct=decision.gpc_correct,
        accuracy=decision.accuracy,
        accuracy_threshold=decision.accuracy_threshold,
        min_observations=decision.min_observations,
        criteria=[CriterionDTO(**vars(c)) for c in decision.criteria],
    )


async def _standing(session: AsyncSession, kid_id: int, stage_id: int) -> PromotionDecision:
    """Where this kid stands against §8's rule right now. Read-only."""
    completed = (
        await session.execute(_STORIES_AT_STAGE_SQL, {"kid_id": kid_id, "stage_id": stage_id})
    ).scalar_one()
    evidence = (
        await session.execute(_STAGE_EVIDENCE_SQL, {"kid_id": kid_id, "stage_id": stage_id})
    ).one()
    return evaluate_promotion(
        stage_id=stage_id,
        stories_completed=int(completed),
        gpc_seen=int(evidence.n_seen),
        gpc_correct=int(evidence.n_correct),
    )


# --- kids (§9) ---------------------------------------------------------------
@router.get("/api/kids/{kid_id}/progress", response_model=ProgressDTO, tags=["kids"])
async def kid_progress(
    kid_id: int,
    principal: Principal = Depends(require_parent),
    session: AsyncSession = Depends(get_session),
) -> ProgressDTO:
    """§9 → stage, gpc_stats, readings summary. The parent dashboard (§11).

    Everything §8's promotion rule uses is returned alongside the verdict — the
    thresholds, the counts, and a per-criterion reason — so the dashboard can
    explain *why* the child has not moved up rather than showing a bar that has
    not filled.

    This is the richest kid-scoped read in the API — a name, a stage, every
    grapheme the child has struggled with and their last twenty readings — so it
    is also the one an ownership mistake would hurt most. `owned_kid` is the
    only way in, and another parent's kid id is 404.
    """
    kid = await owned_kid(session, principal, kid_id)

    stage = (
        await session.execute(
            text("SELECT name, description FROM stages WHERE id = :s"), {"s": kid.stage_id}
        )
    ).first()

    totals = (
        await session.execute(
            text(
                "SELECT count(*) AS started, "
                "count(*) FILTER (WHERE completed_at IS NOT NULL) AS completed "
                "FROM readings WHERE kid_id = :k"
            ),
            {"k": kid_id},
        )
    ).one()

    decision = await _standing(session, kid_id, kid.stage_id)

    gpcs = await session.execute(_GPC_ACCURACY_SQL, {"kid_id": kid_id, "stage_id": kid.stage_id})
    recent = await session.execute(
        _RECENT_READINGS_SQL, {"kid_id": kid_id, "limit": RECENT_READINGS_LIMIT}
    )

    return ProgressDTO(
        kid=KidDTO(
            id=kid.id,
            display_name=kid.display_name,
            stage_id=kid.stage_id,
            created_at=kid.created_at,
        ),
        stage_name=stage.name if stage else "",
        stage_description=stage.description if stage else "",
        readings_started=int(totals.started),
        readings_completed=int(totals.completed),
        readings_completed_at_stage=decision.stories_completed,
        thresholds=ThresholdsDTO(**vars(THRESHOLDS)),
        promotion=_promotion_dto(decision),
        gpc_accuracy=[
            GpcAccuracyDTO(
                gpc_id=g.gpc_id,
                grapheme=g.grapheme,
                phonemes=g.phonemes,
                stage_id=g.stage_id,
                n_seen=g.n_seen,
                n_correct=g.n_correct,
                accuracy=accuracy_of(g.n_seen, g.n_correct),
                current_stage=g.stage_id == kid.stage_id,
            )
            for g in gpcs
        ],
        recent_readings=[
            RecentReadingDTO(
                id=r.id,
                story_id=r.story_id,
                story_title=r.story_title or "",
                theme_slug=r.theme_slug or "",
                story_stage_id=r.story_stage_id,
                stage_id=r.stage_id,
                started_at=r.started_at,
                completed_at=r.completed_at,
                questions_answered=int(r.questions_answered),
                questions_correct=int(r.questions_correct),
                choices=_choices(r.path),
            )
            for r in recent
        ],
    )


# --- readings (§9) -----------------------------------------------------------
@router.post("/api/readings", response_model=ReadingDTO, status_code=201)
async def start_reading(
    body: ReadingCreate,
    principal: Principal = Depends(require_parent),
    session: AsyncSession = Depends(get_session),
) -> ReadingDTO:
    """Begin a reading (§9) → reading_id.

    `stage_id` is stamped from the kid, not the story. §7's fallback ladder can
    serve a *lower*-stage story when a pool is empty, and §8 counts "stories
    completed at current stage" — so the story's own stage would undercount the
    promotion criterion every time the ladder fired.

    A `rejected` story cannot be read. `api/stories.py` will not serve one by
    request or by id; this is the same invariant on the write path, so a stale
    or guessed id cannot get one in front of a child either.

    `kid_id` arrives in the body, so this is the endpoint where a wrong-owner id
    is cheapest to try. It is resolved through `owned_kid`, so a reading can
    only ever be started against a kid the caller owns — and the refusal is the
    same 404 an unknown kid gets, which is what stops the endpoint being used to
    enumerate other families.
    """
    kid = await owned_kid(session, principal, body.kid_id)

    story = (
        await session.execute(
            text("SELECT id, status FROM stories WHERE id = :id"), {"id": body.story_id}
        )
    ).first()
    if story is None or story.status not in SERVABLE_STATUSES:
        raise HTTPException(status_code=404, detail=f"story {body.story_id} not found")

    row = (
        await session.execute(
            text(
                "INSERT INTO readings (kid_id, story_id, stage_id, path) "
                "VALUES (:kid, :story, :stage, CAST('{\"choices\": []}' AS jsonb)) "
                "RETURNING id, kid_id, story_id, stage_id, path, started_at, completed_at"
            ),
            {"kid": kid.id, "story": body.story_id, "stage": kid.stage_id},
        )
    ).one()
    await session.commit()
    return _reading_dto(row)


@router.post("/api/readings/{reading_id}/choice", response_model=ReadingDTO)
async def record_choice(
    reading_id: int,
    body: ChoiceCreate,
    principal: Principal = Depends(require_parent),
    session: AsyncSession = Depends(get_session),
) -> ReadingDTO:
    """Record a branch choice (§9, §10) into `readings.path`.

    Branching is schema-only until §10's generator lands, so in practice no
    served story has children yet. The validation is written for the story shape
    that is coming: the segment must belong to *this reading's* story, and if
    that segment does have children with labels, the choice must be one of them.
    A path entry naming a segment from another story would make the dashboard's
    "Maya chose the cave path" a fiction — and a path entry written by another
    family would make it a lie, which is why the reading is resolved through
    `owned_reading` before any of the segment checks run.
    """
    reading = await owned_reading(session, principal, reading_id)

    segment = (
        await session.execute(
            text("SELECT id FROM story_segments WHERE id = :seg AND story_id = :story"),
            {"seg": body.segment_id, "story": reading.story_id},
        )
    ).first()
    if segment is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"segment {body.segment_id} is not part of story {reading.story_id}, "
                f"which is what reading {reading_id} is reading"
            ),
        )

    offered = [
        r[0]
        for r in await session.execute(
            text(
                "SELECT choice_label FROM story_segments "
                "WHERE parent_segment_id = :seg AND choice_label IS NOT NULL ORDER BY idx"
            ),
            {"seg": body.segment_id},
        )
    ]
    if offered and body.choice not in offered:
        raise HTTPException(
            status_code=422,
            detail=f"choice must be one of {offered!r} for segment {body.segment_id}",
        )

    # Serialised with json.dumps and passed as a BOUND parameter — the choice
    # label reaches here from a tap, and string-formatting it into a jsonb
    # literal would be a JSON-injection hole in the dashboard's own record.
    entry = json.dumps(
        [
            {
                "segment_id": body.segment_id,
                "choice": body.choice,
                "at": datetime.now(UTC).isoformat(),
            }
        ]
    )
    row = (await session.execute(_APPEND_CHOICE_SQL, {"id": reading_id, "entry": entry})).first()
    if row is None:
        raise HTTPException(
            status_code=409,
            detail=f"reading {reading_id} is already complete; its path cannot change",
        )
    await session.commit()
    return _reading_dto(row)


@router.post("/api/readings/{reading_id}/answers", response_model=AnswerResultDTO)
async def record_answer(
    reading_id: int,
    body: AnswerCreate,
    principal: Principal = Depends(require_parent),
    session: AsyncSession = Depends(get_session),
) -> AnswerResultDTO:
    """Grade one response server-side (§9) and record it (§8).

    The key stays here. The response says whether the tap was right and nothing
    more — not the correct option, not the index — so a client cannot learn the
    key by asking, and §8's counts stay worth promoting on.

    First answer wins. The unique constraint plus `ON CONFLICT DO NOTHING` makes
    that atomic, so a client that retries a question until it guesses correctly
    changes the feedback it sees and not the mastery signal.

    Ownership matters here for a second reason beyond privacy: this endpoint
    *writes* the evidence §8 promotes on. Without `owned_reading`, a stranger
    could push wrong answers into another child's record and hold them back.
    """
    reading = await owned_reading(session, principal, reading_id)
    if reading.completed_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"reading {reading_id} is already complete — its answers are "
                "already counted in gpc_stats and cannot change"
            ),
        )

    question = (
        await session.execute(
            text(
                "SELECT id, qtype, payload, answer FROM questions "
                "WHERE id = :q AND story_id = :story"
            ),
            {"q": body.question_id, "story": reading.story_id},
        )
    ).first()
    if question is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"question {body.question_id} is not part of story {reading.story_id}, "
                f"which is what reading {reading_id} is reading"
            ),
        )

    correct = grade_answer(
        response=body.response, payload=question.payload, answer=question.answer
    )
    if correct is None:
        # An unusable key is an operator/generation fault, not a wrong child.
        # Recording False here would punish the reader for a bad question, and
        # recording True would forge mastery — so neither is written.
        raise HTTPException(
            status_code=409,
            detail=(
                f"question {body.question_id} has no usable answer key and cannot "
                "be graded; it should not have been served"
            ),
        )

    inserted = (
        await session.execute(
            _INSERT_ANSWER_SQL,
            {"reading_id": reading_id, "question_id": body.question_id, "correct": correct},
        )
    ).first()
    await session.commit()

    if inserted is not None:
        return AnswerResultDTO(
            question_id=body.question_id,
            correct=correct,
            recorded=True,
            recorded_correct=correct,
        )

    stored = (
        await session.execute(
            text("SELECT correct FROM answers WHERE reading_id = :r AND question_id = :q"),
            {"r": reading_id, "q": body.question_id},
        )
    ).scalar_one()
    return AnswerResultDTO(
        question_id=body.question_id,
        correct=correct,
        recorded=False,
        recorded_correct=bool(stored),
    )


@router.post("/api/readings/{reading_id}/complete", response_model=CompleteResultDTO)
async def complete_reading(
    reading_id: int,
    principal: Principal = Depends(require_parent),
    session: AsyncSession = Depends(get_session),
) -> CompleteResultDTO:
    """Finish a reading: update `gpc_stats`, then evaluate promotion (§8).

    The conditional UPDATE is the latch. Everything below it — the counter
    increments, the story's `status`, the stage advance — runs at most once per
    reading, no matter how many times this is called or how many callers call it
    at the same time. A replay returns the same reading with `newly_completed:
    false`, empty deltas and the kid's current standing.

    Evidence comes from `tap_word` answers only (`mastery.MASTERY_QTYPES`), and
    each word's correspondences come from the decoder's own alignment at the
    STORY's stage — never from inspecting spelling here.

    **Evidence is per distinct story, not per reading.** Re-reading a story the
    kid has already finished at this stage completes normally and adds nothing
    to `gpc_stats`, and `_STORIES_AT_STAGE_SQL` counts distinct stories. Both
    are the same fix: §8's promotion rule says "5 stories", and a child who
    reads one story five times has read one story. Left as it was, the mastery
    signal was forgeable by anyone with a loop — and reachable by accident,
    because a thin warm pool hands the same story back.

    The ownership check runs BEFORE the latch, not after. This endpoint mutates
    three tables and can promote a child a stage; refusing afterwards would 404
    the attacker while leaving the damage done. `owned_reading` is a read, so
    the cost of getting the order right is one indexed query.
    """
    reading = await owned_reading(session, principal, reading_id)
    latched = (await session.execute(_COMPLETE_SQL, {"id": reading.id})).first()

    if latched is None:
        kid = await owned_kid(session, principal, reading.kid_id)
        return CompleteResultDTO(
            reading=_reading_dto(reading),
            newly_completed=False,
            gpc_updates=[],
            promotion=_promotion_dto(await _standing(session, kid.id, kid.stage_id)),
            promoted=False,
        )

    story_stage = (
        await session.execute(
            text("SELECT stage_id FROM stories WHERE id = :s"), {"s": latched.story_id}
        )
    ).scalar_one()

    # A story this kid has already finished at this stage yields NO further
    # evidence. The first pass is a child decoding a word; the second is a child
    # remembering which of three options was green last time, and §8's counters
    # cannot tell those apart. Without this, `MIN_GPC_OBSERVATIONS` (DEVIATIONS
    # 5.5) — the floor that exists precisely so one lucky tap cannot promote —
    # is cleared by replaying that one lucky tap ten times.
    #
    # The reading still completes, and still counts as a reading. It just stops
    # being new proof, which is the honest reading of what a re-read is.
    repeat = (
        await session.execute(
            _ALREADY_READ_SQL,
            {
                "kid_id": latched.kid_id,
                "story_id": latched.story_id,
                "stage_id": latched.stage_id,
                "reading_id": latched.id,
            },
        )
    ).first() is not None

    # §8: increment for every GPC instance in words the kid answered about.
    answers = await session.execute(_READING_ANSWERS_SQL, {"reading_id": reading_id})
    evidence: list[WordEvidence] = []
    for row in answers:
        if row.qtype not in MASTERY_QTYPES:
            continue
        key = row.answer if isinstance(row.answer, dict) else {}
        word = single_word(key.get("correct"))
        if word is None:
            continue
        evidence.append(
            WordEvidence(word=word, stage_id=int(story_stage), correct=bool(row.correct))
        )

    deltas = [] if repeat else gpc_increments(evidence)
    updates: list[GpcDeltaDTO] = []
    if deltas:
        rows = await session.execute(
            _UPSERT_GPC_STATS_SQL,
            {
                "kid_id": latched.kid_id,
                "gpc_ids": [d.gpc_id for d in deltas],
                "seen": [d.n_seen for d in deltas],
                "correct": [d.n_correct for d in deltas],
            },
        )
        updates = [
            GpcDeltaDTO(gpc_id=r.gpc_id, n_seen=r.n_seen, n_correct=r.n_correct) for r in rows
        ]

    # §8 defines `complete` as "a kid read it" — which is knowable here and
    # nowhere earlier. `api/stories.py` deliberately only stamps last_served_at
    # (DEVIATIONS 4.8) so that admin previews do not drain the warm pool.
    await session.execute(
        text("UPDATE stories SET status = 'complete' WHERE id = :s AND status = 'warm'"),
        {"s": latched.story_id},
    )

    kid = await owned_kid(session, principal, latched.kid_id)
    decision = await _standing(session, kid.id, kid.stage_id)

    promoted = False
    if decision.promote and decision.to_stage is not None:
        # Guarded on the stage we decided from, so two concurrent completions
        # cannot advance the same kid twice.
        moved = (
            await session.execute(
                text(
                    "UPDATE kids SET stage_id = :next WHERE id = :k AND stage_id = :cur "
                    "RETURNING stage_id"
                ),
                {"next": decision.to_stage, "k": kid.id, "cur": decision.from_stage},
            )
        ).first()
        promoted = moved is not None

    await session.commit()

    return CompleteResultDTO(
        reading=_reading_dto(latched),
        newly_completed=True,
        repeat_reading=repeat,
        gpc_updates=updates,
        promotion=_promotion_dto(decision),
        promoted=promoted,
    )


__all__ = ["router"]
