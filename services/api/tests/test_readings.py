"""Phase 5: kids, readings, answers and promotion (§9), against real Postgres.

Like the Phase 2 and 4 suites these run against the real seeded database and the
real warm pool. A promotion test that passes with no stories in the table would
be evidence of nothing.

Two pieces of hygiene, because this module *writes*:

- Every kid it creates is deleted on teardown. `readings`, `answers` and
  `gpc_stats` all cascade from `kids`, so one delete undoes the whole session.
- `POST /api/readings/{id}/complete` flips a story's `status` from `warm` to
  `complete` (§8: "a kid read it"), and `tests/test_stories_api.py` asserts a
  freshly served story is still `warm`. Statuses are snapshotted and restored,
  so a second run of the suite sees exactly what the first one did.

The controlled-question fixture inserts its own `tap_word` row rather than
leaning on whichever question the warm pool happens to hold. Mastery arithmetic
has to be asserted against known numbers, and "the current pool's first question
targets a stage-2 grapheme" is not a fact the suite should depend on.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.main import app
from storyforge.mastery import (
    MAX_STAGE,
    MIN_GPC_OBSERVATIONS,
    STORIES_REQUIRED_AT_STAGE,
    stage_evidence,
    word_gpc_ids,
)

# "sit" aligns to exactly three stage-1 correspondences (s|S, i|IH, t|T), so
# five correct readings give 15 observations — comfortably over the evidence
# floor, and the arithmetic is checkable by hand.
TAP_WORD = "sit"
TAP_OPTIONS = ["sit", "map", "bed"]
TAP_GRAPHEME = "s"
TAP_STAGE = 1
TAP_BODY = "Sam sat."

# The SECOND (stage, word) pair, and it is above stage 1 on purpose (DEVIATIONS
# 11.79). Stage 1 is the one rung of the ladder where a word's whole alignment
# and the correspondences the stage INTRODUCES are the same set — `sit` is
# `s|S, i|IH, t|T` and all three are stage-1 rows — so at stage 1 a promotion
# test cannot tell the two apart, and every promotion test in this file ran
# there. `oatmeal` aligns to FIVE correspondences at stage 5 and exactly TWO of
# them (`oa|OW`, `ea|IY`) are stage-5 rows, which is what
# `_STAGE_EVIDENCE_SQL` sums: five clean readings are worth 10, not 25, and 10
# is `MIN_GPC_OBSERVATIONS` on the nose. The boundary, deliberately.
HIGH_TAP_WORD = "oatmeal"
HIGH_TAP_OPTIONS = ["oatmeal", "rainy", "sleepy"]
HIGH_TAP_GRAPHEME = "oa"
HIGH_TAP_STAGE = 5
#: 1.000 decodable at stage 5 (`the` is the only sight word), and it contains
#: all three options — a tap-a-word question about a word the story never says
#: is not a question `check_tap_word` would ever have let through.
HIGH_TAP_BODY = "Sam had oatmeal on a rainy day. The pup was sleepy."


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
async def sandbox(session):
    """Undo everything this test wrote: its kids, and any story it completed."""
    before = {
        r.id: r.status
        for r in await session.execute(text("SELECT id, status FROM stories"))
    }
    kid_ids: list[int] = []
    yield kid_ids
    if kid_ids:
        await session.execute(
            text("DELETE FROM kids WHERE id = ANY(CAST(:ids AS int[]))"), {"ids": kid_ids}
        )
    await session.execute(
        text(
            "UPDATE stories s SET status = v.status "
            "FROM unnest(CAST(:ids AS int[]), CAST(:statuses AS text[])) AS v(id, status) "
            "WHERE s.id = v.id AND s.status <> v.status"
        ),
        {"ids": list(before), "statuses": list(before.values())},
    )
    await session.commit()


@pytest.fixture
async def story_at_stage_1(session):
    """A real, servable stage-1 story from the warm pool."""
    story_id = (
        await session.execute(
            text(
                "SELECT id FROM stories WHERE stage_id = :s "
                "AND status IN ('warm', 'complete') ORDER BY id LIMIT 1"
            ),
            {"s": TAP_STAGE},
        )
    ).scalar_one_or_none()
    assert story_id is not None, "no stage-1 story in the pool — run `make seed && make warm`"
    return story_id


@pytest.fixture
async def tap_question(session, story_at_stage_1):
    """A tap_word question with a hand-checked answer key, cleaned up after."""
    qid = (
        await session.execute(
            text(
                "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                "VALUES (:st, 99, 'tap_word', 'Tap: sit.', "
                "CAST(:payload AS jsonb), CAST(:answer AS jsonb)) RETURNING id"
            ),
            {
                "st": story_at_stage_1,
                "payload": '{"options": ["sit", "map", "bed"], "target_grapheme": "s"}',
                "answer": '{"index": 0, "correct": "sit"}',
            },
        )
    ).scalar_one()
    await session.commit()
    try:
        yield qid
    finally:
        await session.execute(text("DELETE FROM questions WHERE id = :q"), {"q": qid})
        await session.commit()


@pytest.fixture
async def story_pool(session):
    """Factory: `STORIES_REQUIRED_AT_STAGE + 1` DIFFERENT stories at ONE stage,
    each with a tap_word question carrying the same hand-checked answer key.

    The stage is a parameter because it has to be one. `distinct_stories` below
    is the stage-1 instance this file has always had; `distinct_stories_above_
    stage_one` is the same fixture at stage 5, and the difference between what
    those two prove is DEVIATIONS 11.79.
    """
    created: list[int] = []

    async def build(
        *, stage_id: int, word: str, options: list[str], grapheme: str, body: str
    ) -> list[tuple[int, int]]:
        assert options[0] == word, "the answer key indexes option 0"
        theme_id = (
            await session.execute(text("SELECT id FROM themes ORDER BY id LIMIT 1"))
        ).scalar_one()

        readables = []
        for n in range(STORIES_REQUIRED_AT_STAGE + 1):
            story_id = (
                await session.execute(
                    text(
                        "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                        "story_words, gen_meta) VALUES (:s, :t, 'warm', :title, 1.0, "
                        "CAST('{}' AS text[]), CAST('{}' AS jsonb)) RETURNING id"
                    ),
                    {"s": stage_id, "t": theme_id, "title": f"Distinct {stage_id}-{n}"},
                )
            ).scalar_one()
            created.append(story_id)
            await session.execute(
                text(
                    "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
                    "VALUES (:st, 0, :body, CAST('{}' AS text[]))"
                ),
                {"st": story_id, "body": body},
            )
            qid = (
                await session.execute(
                    text(
                        "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                        "VALUES (:st, 99, 'tap_word', :prompt, "
                        "CAST(:payload AS jsonb), CAST(:answer AS jsonb)) RETURNING id"
                    ),
                    {
                        "st": story_id,
                        "prompt": f"Tap: {word}.",
                        "payload": json.dumps(
                            {"options": options, "target_grapheme": grapheme}
                        ),
                        "answer": json.dumps({"index": 0, "correct": word}),
                    },
                )
            ).scalar_one()
            readables.append((story_id, qid))
        await session.commit()
        return readables

    try:
        yield build
    finally:
        # `story_segments`, `questions` and `readings` all cascade from stories.
        if created:
            await session.execute(
                text("DELETE FROM stories WHERE id = ANY(CAST(:ids AS int[]))"),
                {"ids": created},
            )
            await session.commit()


@pytest.fixture
async def distinct_stories(story_pool):
    """`STORIES_REQUIRED_AT_STAGE + 1` DIFFERENT stage-1 stories, each with a
    tap_word question carrying the same hand-checked answer key.

    This fixture exists because the promotion tests below were wrong, and they
    were wrong in the way that matters: they read ONE story five times and
    asserted §8's "≥5 stories completed at current stage" was satisfied. It was
    — `_STORIES_AT_STAGE_SQL` counted readings, not stories — so the tests
    pinned the defect as the contract. Live, a stage-5 kid was promoted to
    stage 6 having read one story and answered one question about one word ten
    times, in 31 HTTP calls. `MIN_GPC_OBSERVATIONS = 10` (DEVIATIONS 5.5), the
    floor that exists so a single lucky tap cannot promote, was cleared by
    replaying that single lucky tap.

    The stories are created here rather than drawn from the warm pool because
    the pool currently holds four servable stage-1 stories and the rule needs
    five. A test of the promotion rule must not be silently satisfiable by pool
    depth in either direction, so it brings its own.
    """
    return await story_pool(
        stage_id=TAP_STAGE,
        word=TAP_WORD,
        options=TAP_OPTIONS,
        grapheme=TAP_GRAPHEME,
        body=TAP_BODY,
    )


@pytest.fixture
async def distinct_stories_above_stage_one(story_pool):
    """The same pool at `HIGH_TAP_STAGE`, where the alignment and the stage's
    own slice of it are different sizes (DEVIATIONS 11.79)."""
    return await story_pool(
        stage_id=HIGH_TAP_STAGE,
        word=HIGH_TAP_WORD,
        options=HIGH_TAP_OPTIONS,
        grapheme=HIGH_TAP_GRAPHEME,
        body=HIGH_TAP_BODY,
    )


async def _introduced_gpc_ids(session, stage_id: int) -> frozenset[str]:
    """The correspondences the DATABASE says this stage INTRODUCES.

    Read from `gpcs` rather than recomputed from the decoder's CSV because
    `readings._STAGE_EVIDENCE_SQL` joins these exact rows (`g.stage_id =
    :stage_id`). A guard that formed its own opinion about which stage teaches
    which grapheme would be free to disagree with the product it is guarding
    and would still print a green dot.
    """
    rows = await session.execute(
        text("SELECT gpc_id FROM gpcs WHERE stage_id = :s"), {"s": stage_id}
    )
    return frozenset(rows.scalars())


async def _evidence_per_reading(session, word: str, stage_id: int) -> int:
    """What one correct tap on `word` adds to §8's `gpc_seen` at `stage_id`.

    `mastery.stage_evidence` is the product's own arithmetic for this — the
    quantity `warm.stage_supply` gates on and `_STAGE_EVIDENCE_SQL` sums. It is
    NOT `len(word_gpc_ids(word, stage_id))`: that is the whole alignment, every
    instance the child is credited as having *seen*, and the stage filter is
    applied later, at read time.
    """
    return stage_evidence(
        [word],
        stage_id=stage_id,
        introduced=await _introduced_gpc_ids(session, stage_id),
    )


async def _new_kid(client, sandbox, stage_id=TAP_STAGE, name="Testkid") -> int:
    resp = await client.post("/api/kids", json={"display_name": name, "stage_id": stage_id})
    assert resp.status_code == 201, resp.text
    kid_id = resp.json()["id"]
    sandbox.append(kid_id)
    return kid_id


async def _start(client, kid_id: int, story_id: int) -> int:
    resp = await client.post("/api/readings", json={"kid_id": kid_id, "story_id": story_id})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _answer(client, reading_id: int, question_id: int, response) -> dict:
    resp = await client.post(
        f"/api/readings/{reading_id}/answers",
        json={"question_id": question_id, "response": response},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _read_once(
    client,
    kid_id,
    story_id,
    question_id,
    *,
    correct: bool,
    word: str = TAP_WORD,
    wrong: str = "map",
) -> dict:
    """One whole kid session: start, answer, complete.

    `wrong` is an OFFERED option rather than junk, because a wrong tap in this
    product is a child tapping the other word on the screen.
    """
    reading_id = await _start(client, kid_id, story_id)
    await _answer(client, reading_id, question_id, word if correct else wrong)
    resp = await client.post(f"/api/readings/{reading_id}/complete")
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- kids (§9, §13) ----------------------------------------------------------
async def test_create_and_list_a_kid(client, sandbox):
    kid_id = await _new_kid(client, sandbox, stage_id=2, name="  Maya  ")

    body = (await client.get("/api/kids")).json()
    kid = next(k for k in body if k["id"] == kid_id)
    assert kid["display_name"] == "Maya", "whitespace should be stripped"
    assert kid["stage_id"] == 2


async def test_a_kid_row_carries_no_pii_beyond_a_first_name(client, sandbox, session):
    """§13: kids have no account and no PII beyond a display name.

    Asserted against the actual table, not the DTO — a column nobody returns is
    still a column something can fill.
    """
    await _new_kid(client, sandbox)
    columns = {
        r[0]
        for r in await session.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = 'kids'")
        )
    }
    assert columns == {"id", "parent_id", "display_name", "stage_id", "created_at"}
    for forbidden in ("email", "dob", "date_of_birth", "birthday", "phone", "last_name", "photo"):
        assert forbidden not in columns


async def test_an_email_shaped_display_name_is_refused(client):
    """The cheapest way for a child's email to arrive is a parent pasting it."""
    resp = await client.post(
        "/api/kids", json={"display_name": "kid@example.com", "stage_id": 1}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": "", "stage_id": 1},
        {"display_name": "   ", "stage_id": 1},
        {"display_name": "x" * 41, "stage_id": 1},
        {"display_name": "Ana", "stage_id": 0},
        {"display_name": "Ana", "stage_id": 13},
        {"display_name": "Ana"},
        {"stage_id": 1},
    ],
)
async def test_bad_kid_bodies_are_422(client, body):
    assert (await client.post("/api/kids", json=body)).status_code == 422


async def test_progress_for_an_unknown_kid_is_404(client):
    assert (await client.get("/api/kids/99999999/progress")).status_code == 404


# --- readings (§9) -----------------------------------------------------------
async def test_start_a_reading(client, sandbox, story_at_stage_1):
    kid_id = await _new_kid(client, sandbox, stage_id=3)
    resp = await client.post(
        "/api/readings", json={"kid_id": kid_id, "story_id": story_at_stage_1}
    )
    assert resp.status_code == 201

    body = resp.json()
    assert body["kid_id"] == kid_id
    assert body["story_id"] == story_at_stage_1
    # The KID's stage, not the story's — §7's ladder serves lower-stage stories
    # and §8 counts stories completed at the kid's current stage.
    assert body["stage_id"] == 3
    assert body["completed_at"] is None
    assert body["choices"] == []


async def test_a_rejected_story_cannot_be_read(client, sandbox, session):
    """Same child-safety invariant as `api/stories.py`, on the write path."""
    rejected = (
        await session.execute(text("SELECT id FROM stories WHERE status = 'rejected' LIMIT 1"))
    ).scalar_one_or_none()
    assert rejected is not None, "no rejected stories — this test would be vacuous"

    kid_id = await _new_kid(client, sandbox)
    resp = await client.post("/api/readings", json={"kid_id": kid_id, "story_id": rejected})
    assert resp.status_code == 404


async def test_a_reading_needs_a_real_kid_and_a_real_story(client, sandbox, story_at_stage_1):
    kid_id = await _new_kid(client, sandbox)
    assert (
        await client.post("/api/readings", json={"kid_id": 99999999, "story_id": story_at_stage_1})
    ).status_code == 404
    assert (
        await client.post("/api/readings", json={"kid_id": kid_id, "story_id": 99999999})
    ).status_code == 404


# --- choices (§10) -----------------------------------------------------------
async def test_a_choice_lands_in_the_reading_path(client, sandbox, session, story_at_stage_1):
    """§10: `readings.path` is what makes the dashboard say "Maya chose the cave
    path". Branching stories are not generated yet; the record is."""
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    segment_id = (
        await session.execute(
            text("SELECT id FROM story_segments WHERE story_id = :s ORDER BY idx LIMIT 1"),
            {"s": story_at_stage_1},
        )
    ).scalar_one()

    resp = await client.post(
        f"/api/readings/{reading_id}/choice",
        json={"segment_id": segment_id, "choice": "Go in the cave"},
    )
    assert resp.status_code == 200, resp.text
    recorded = resp.json()["choices"]
    assert recorded == [
        {"segment_id": segment_id, "choice": "Go in the cave", "at": recorded[0]["at"]}
    ]
    assert recorded[0]["at"], "the dashboard orders a path by when each choice was made"

    # Choices append rather than replace — a story has up to two branch points.
    resp = await client.post(
        f"/api/readings/{reading_id}/choice",
        json={"segment_id": segment_id, "choice": "Run away"},
    )
    assert [c["choice"] for c in resp.json()["choices"]] == ["Go in the cave", "Run away"]


async def test_a_segment_from_another_story_is_refused(client, sandbox, session, story_at_stage_1):
    """A path entry naming someone else's segment makes the dashboard fiction."""
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    other = (
        await session.execute(
            text("SELECT id FROM story_segments WHERE story_id <> :s ORDER BY id LIMIT 1"),
            {"s": story_at_stage_1},
        )
    ).scalar_one()

    resp = await client.post(
        f"/api/readings/{reading_id}/choice", json={"segment_id": other, "choice": "nope"}
    )
    assert resp.status_code == 404


async def test_a_choice_the_story_does_not_offer_is_refused(
    client, sandbox, session, story_at_stage_1
):
    """Written for the story shape §10 will generate: when a segment really does
    have labelled children, only those labels are legal."""
    parent_id, next_idx = (
        await session.execute(
            text(
                "SELECT id, (SELECT max(idx) + 1 FROM story_segments WHERE story_id = :s) "
                "FROM story_segments WHERE story_id = :s ORDER BY idx LIMIT 1"
            ),
            {"s": story_at_stage_1},
        )
    ).one()
    branch_id = (
        await session.execute(
            text(
                "INSERT INTO story_segments "
                "(story_id, idx, parent_segment_id, choice_label, body, scene_tags) "
                "VALUES (:s, :idx, :p, 'Go in the cave', 'Sam ran in.', ARRAY[]::text[]) "
                "RETURNING id"
            ),
            {"s": story_at_stage_1, "idx": next_idx, "p": parent_id},
        )
    ).scalar_one()
    await session.commit()

    try:
        kid_id = await _new_kid(client, sandbox)
        reading_id = await _start(client, kid_id, story_at_stage_1)

        bad = await client.post(
            f"/api/readings/{reading_id}/choice",
            json={"segment_id": parent_id, "choice": "Fly to the moon"},
        )
        assert bad.status_code == 422
        assert "Go in the cave" in bad.json()["detail"]

        good = await client.post(
            f"/api/readings/{reading_id}/choice",
            json={"segment_id": parent_id, "choice": "Go in the cave"},
        )
        assert good.status_code == 200
    finally:
        await session.execute(
            text("DELETE FROM story_segments WHERE id = :i"), {"i": branch_id}
        )
        await session.commit()


async def test_a_completed_reading_takes_no_more_choices(
    client, sandbox, session, story_at_stage_1
):
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    segment_id = (
        await session.execute(
            text("SELECT id FROM story_segments WHERE story_id = :s ORDER BY idx LIMIT 1"),
            {"s": story_at_stage_1},
        )
    ).scalar_one()
    assert (await client.post(f"/api/readings/{reading_id}/complete")).status_code == 200

    resp = await client.post(
        f"/api/readings/{reading_id}/choice", json={"segment_id": segment_id, "choice": "late"}
    )
    assert resp.status_code == 409


# --- answers: server-side grading (§9) ---------------------------------------
async def test_a_correct_and_an_incorrect_tap_are_graded_here(
    client, sandbox, story_at_stage_1, tap_question
):
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)

    right = await _answer(client, reading_id, tap_question, TAP_WORD)
    assert right == {
        "question_id": tap_question,
        "correct": True,
        "recorded": True,
        "recorded_correct": True,
    }

    other = await _start(client, kid_id, story_at_stage_1)
    wrong = await _answer(client, other, tap_question, "map")
    assert wrong["correct"] is False


async def test_the_answer_key_never_appears_in_a_response(
    client, sandbox, story_at_stage_1, tap_question
):
    """§8's mastery signal is only worth promoting on while it is unforgeable —
    so a wrong answer is told it is wrong, and not what the right one was."""
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)

    body = (
        await client.post(
            f"/api/readings/{reading_id}/answers",
            json={"question_id": tap_question, "response": "map"},
        )
    ).text
    assert TAP_WORD not in body
    assert "correct_word" not in body and "index" not in body

    story = (await client.get(f"/api/stories/{story_at_stage_1}")).json()
    question = next(q for q in story["questions"] if q["id"] == tap_question)
    assert "answer" not in question and "correct" not in question["payload"]


async def test_an_index_response_is_graded_against_the_shuffled_key(
    client, sandbox, story_at_stage_1, tap_question
):
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    assert (await _answer(client, reading_id, tap_question, 0))["correct"] is True

    other = await _start(client, kid_id, story_at_stage_1)
    assert (await _answer(client, other, tap_question, 2))["correct"] is False


@pytest.mark.parametrize(
    "response",
    [
        {"correct": "sit"},
        ["sit"],
        None,
        True,
        3.5,
        {"$ne": None},
        [[["sit"]]],
    ],
)
async def test_a_hostile_response_payload_never_500s(
    client, sandbox, story_at_stage_1, tap_question, response
):
    """A client can post anything. It gets a verdict or a 4xx, never a 500."""
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)

    resp = await client.post(
        f"/api/readings/{reading_id}/answers",
        json={"question_id": tap_question, "response": response},
    )
    assert resp.status_code in (200, 422), resp.text
    if resp.status_code == 200:
        assert resp.json()["correct"] is False


async def test_a_boolean_response_is_refused_rather_than_coerced_to_an_index(
    client, sandbox, story_at_stage_1, tap_question
):
    """Regression, caught live rather than reasoned about.

    `bool` is a subclass of `int` and Pydantic coerces it, so `{"response":
    true}` reached the grader as the integer 1 and was graded as a tap on option
    1 — which in the story under test was the correct answer. The pure grader
    refuses bools; it simply never saw one. The two layers now agree.
    """
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)

    for value in (True, False):
        resp = await client.post(
            f"/api/readings/{reading_id}/answers",
            json={"question_id": tap_question, "response": value},
        )
        assert resp.status_code == 422, f"{value!r} was accepted as an index"


async def test_an_oversized_response_is_rejected_at_the_edge(
    client, sandbox, story_at_stage_1, tap_question
):
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    resp = await client.post(
        f"/api/readings/{reading_id}/answers",
        json={"question_id": tap_question, "response": "s" * 100_000},
    )
    assert resp.status_code == 422


async def test_a_question_from_another_story_cannot_be_answered(
    client, sandbox, session, story_at_stage_1, tap_question
):
    """Otherwise a kid could farm mastery off an easier story's questions."""
    foreign = (
        await session.execute(
            text("SELECT id FROM questions WHERE story_id <> :s ORDER BY id LIMIT 1"),
            {"s": story_at_stage_1},
        )
    ).scalar_one_or_none()
    assert foreign is not None, "needs questions on another story — run `make warm`"

    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    resp = await client.post(
        f"/api/readings/{reading_id}/answers",
        json={"question_id": foreign, "response": "sit"},
    )
    assert resp.status_code == 404


async def test_a_question_with_an_unusable_key_is_a_409_not_a_500(
    client, sandbox, session, story_at_stage_1
):
    """`None` from the grader means "this cannot be graded" — which must not be
    silently recorded as a wrong answer against the child."""
    qid = (
        await session.execute(
            text(
                "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                "VALUES (:st, 98, 'tap_word', 'Tap: broken.', "
                """CAST('{"options": ["sit", "map"]}' AS jsonb), CAST('{}' AS jsonb)) """
                "RETURNING id"
            ),
            {"st": story_at_stage_1},
        )
    ).scalar_one()
    await session.commit()
    try:
        kid_id = await _new_kid(client, sandbox)
        reading_id = await _start(client, kid_id, story_at_stage_1)
        resp = await client.post(
            f"/api/readings/{reading_id}/answers", json={"question_id": qid, "response": "sit"}
        )
        assert resp.status_code == 409
        assert (
            await session.execute(
                text("SELECT count(*) FROM answers WHERE question_id = :q"), {"q": qid}
            )
        ).scalar_one() == 0
    finally:
        await session.execute(text("DELETE FROM questions WHERE id = :q"), {"q": qid})
        await session.commit()


async def test_the_first_answer_wins(client, sandbox, session, story_at_stage_1, tap_question):
    """"Tap until it goes green" must change the feedback and not the record."""
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)

    first = await _answer(client, reading_id, tap_question, "map")
    assert first == {
        "question_id": tap_question,
        "correct": False,
        "recorded": True,
        "recorded_correct": False,
    }

    retry = await _answer(client, reading_id, tap_question, TAP_WORD)
    assert retry["correct"] is True, "the child is told their new tap was right"
    assert retry["recorded"] is False
    assert retry["recorded_correct"] is False, "but mastery keeps the first attempt"

    rows = (
        await session.execute(
            text("SELECT count(*), bool_or(correct) FROM answers WHERE reading_id = :r"),
            {"r": reading_id},
        )
    ).one()
    assert rows == (1, False)

    body = (await client.post(f"/api/readings/{reading_id}/complete")).json()
    assert all(u["n_correct"] == 0 for u in body["gpc_updates"])


async def test_answering_after_completion_is_refused(
    client, sandbox, story_at_stage_1, tap_question
):
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    assert (await client.post(f"/api/readings/{reading_id}/complete")).status_code == 200

    resp = await client.post(
        f"/api/readings/{reading_id}/answers",
        json={"question_id": tap_question, "response": TAP_WORD},
    )
    assert resp.status_code == 409


# --- completion and gpc_stats (§8) -------------------------------------------
async def test_completing_a_reading_writes_gpc_stats(
    client, sandbox, session, story_at_stage_1, tap_question
):
    """§8: increment for every GPC instance in the words the kid answered about."""
    kid_id = await _new_kid(client, sandbox)
    body = await _read_once(client, kid_id, story_at_stage_1, tap_question, correct=True)

    assert body["newly_completed"] is True
    assert body["reading"]["completed_at"] is not None
    assert {u["gpc_id"] for u in body["gpc_updates"]} == set(word_gpc_ids(TAP_WORD, TAP_STAGE))

    rows = {
        r.gpc_id: (r.n_seen, r.n_correct)
        for r in await session.execute(
            text("SELECT gpc_id, n_seen, n_correct FROM gpc_stats WHERE kid_id = :k"),
            {"k": kid_id},
        )
    }
    assert rows == {gpc: (1, 1) for gpc in word_gpc_ids(TAP_WORD, TAP_STAGE)}


async def test_a_wrong_tap_is_seen_but_not_correct(
    client, sandbox, session, story_at_stage_1, tap_question
):
    kid_id = await _new_kid(client, sandbox)
    await _read_once(client, kid_id, story_at_stage_1, tap_question, correct=False)

    rows = [
        (r.n_seen, r.n_correct)
        for r in await session.execute(
            text("SELECT n_seen, n_correct FROM gpc_stats WHERE kid_id = :k"), {"k": kid_id}
        )
    ]
    assert rows and all(row == (1, 0) for row in rows)


async def test_completing_the_same_reading_twice_does_not_double_count(
    client, sandbox, session, story_at_stage_1, tap_question
):
    """The whole idempotency claim, exercised rather than argued."""
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    await _answer(client, reading_id, tap_question, TAP_WORD)

    first = (await client.post(f"/api/readings/{reading_id}/complete")).json()
    assert first["newly_completed"] is True
    assert first["gpc_updates"]

    for _ in range(3):
        again = await client.post(f"/api/readings/{reading_id}/complete")
        assert again.status_code == 200
        assert again.json()["newly_completed"] is False
        assert again.json()["gpc_updates"] == []
        assert again.json()["promoted"] is False
    # completed_at is the latch: it must still be the FIRST completion.
    assert again.json()["reading"]["completed_at"] == first["reading"]["completed_at"]

    rows = {
        r.gpc_id: (r.n_seen, r.n_correct)
        for r in await session.execute(
            text("SELECT gpc_id, n_seen, n_correct FROM gpc_stats WHERE kid_id = :k"),
            {"k": kid_id},
        )
    }
    assert rows == {gpc: (1, 1) for gpc in word_gpc_ids(TAP_WORD, TAP_STAGE)}


async def test_concurrent_completions_still_count_once(
    client, sandbox, session, story_at_stage_1, tap_question
):
    """A dropped response and a retry look exactly like this."""
    import asyncio

    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    await _answer(client, reading_id, tap_question, TAP_WORD)

    results = await asyncio.gather(
        *(client.post(f"/api/readings/{reading_id}/complete") for _ in range(5))
    )
    assert [r.status_code for r in results] == [200] * 5
    assert sum(r.json()["newly_completed"] for r in results) == 1

    seen = (
        await session.execute(
            text("SELECT max(n_seen) FROM gpc_stats WHERE kid_id = :k"), {"k": kid_id}
        )
    ).scalar_one()
    assert seen == 1


async def test_an_unanswered_reading_completes_with_no_evidence(
    client, sandbox, session, story_at_stage_1
):
    """A child who read but skipped the questions still finishes the story."""
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)

    body = (await client.post(f"/api/readings/{reading_id}/complete")).json()
    assert body["newly_completed"] is True
    assert body["gpc_updates"] == []
    assert (
        await session.execute(
            text("SELECT count(*) FROM gpc_stats WHERE kid_id = :k"), {"k": kid_id}
        )
    ).scalar_one() == 0


async def test_completing_an_unknown_reading_is_404(client):
    assert (await client.post("/api/readings/99999999/complete")).status_code == 404


async def test_completion_marks_the_story_read(client, sandbox, session, story_at_stage_1):
    """§8 defines `complete` as "a kid read it" — which is knowable only here.
    `api/stories.py` deliberately only stamps `last_served_at` (DEVIATIONS 4.8)."""
    await session.execute(
        text("UPDATE stories SET status = 'warm' WHERE id = :s"), {"s": story_at_stage_1}
    )
    await session.commit()

    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    await client.post(f"/api/readings/{reading_id}/complete")

    status = (
        await session.execute(
            text("SELECT status FROM stories WHERE id = :s"), {"s": story_at_stage_1}
        )
    ).scalar_one()
    assert status == "complete"


async def test_only_tap_word_questions_move_the_counters(
    client, sandbox, session, story_at_stage_1
):
    """An `mcq2` is comprehension. Getting it right says the child followed the
    plot and says nothing about whether they can decode /sh/ — and crediting
    every grapheme in a whole sentence would swamp the honest signal."""
    qid = (
        await session.execute(
            text(
                "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                "VALUES (:st, 97, 'mcq2', 'Did the cat sit?', "
                """CAST('{"options": ["The cat sat.", "The cat ran."]}' AS jsonb), """
                """CAST('{"index": 0, "correct": "The cat sat."}' AS jsonb)) RETURNING id"""
            ),
            {"st": story_at_stage_1},
        )
    ).scalar_one()
    await session.commit()
    try:
        kid_id = await _new_kid(client, sandbox)
        reading_id = await _start(client, kid_id, story_at_stage_1)
        graded = await _answer(client, reading_id, qid, "The cat sat.")
        assert graded["correct"] is True, "mcq2 is still graded server-side"

        body = (await client.post(f"/api/readings/{reading_id}/complete")).json()
        assert body["gpc_updates"] == []
        assert (
            await session.execute(
                text("SELECT count(*) FROM gpc_stats WHERE kid_id = :k"), {"k": kid_id}
            )
        ).scalar_one() == 0
    finally:
        await session.execute(text("DELETE FROM questions WHERE id = :q"), {"q": qid})
        await session.commit()


# --- promotion (§8) ----------------------------------------------------------
async def test_five_clean_readings_promote_the_kid(
    client, sandbox, session, distinct_stories
):
    """§8's rule end to end: 5 stories at stage 1, 15 GPC observations, 100%.

    CORRECTED: this read one story five times and called it five stories. §8
    says "≥5 stories completed at current stage"; `distinct_stories` now
    supplies five different ones, so the docstring and the test agree.
    """
    kid_id = await _new_kid(client, sandbox, stage_id=TAP_STAGE)
    # justify-test-edit, VERDICT: TEST IS WRONG (DEVIATIONS 11.79). This read
    # `len(word_gpc_ids(TAP_WORD, TAP_STAGE))` — the WHOLE alignment — while
    # `_STAGE_EVIDENCE_SQL` sums only rows with `g.stage_id = :stage_id`. The two
    # are equal at stage 1 and at no other stage (measured over all 120 servable
    # `tap_word` rows), so the guard passed at every stage while the promotion it
    # guards happens at one. The replacement is a filtered subcount of the same
    # list: strictly smaller, so the assertion got harder, not weaker.
    per_reading = await _evidence_per_reading(session, TAP_WORD, TAP_STAGE)
    assert per_reading * STORIES_REQUIRED_AT_STAGE >= MIN_GPC_OBSERVATIONS

    for n in range(1, STORIES_REQUIRED_AT_STAGE + 1):
        story_id, question_id = distinct_stories[n - 1]
        body = await _read_once(client, kid_id, story_id, question_id, correct=True)
        assert body["promotion"]["stories_completed"] == n
        if n < STORIES_REQUIRED_AT_STAGE:
            assert body["promoted"] is False, f"promoted after only {n} stories"

    assert body["promoted"] is True
    assert body["promotion"]["promote"] is True
    assert body["promotion"]["from_stage"] == 1
    assert body["promotion"]["to_stage"] == 2
    assert body["promotion"]["accuracy"] == 1.0
    # The docstring's "15 GPC observations", asserted rather than asserted-about.
    assert body["promotion"]["gpc_seen"] == per_reading * STORIES_REQUIRED_AT_STAGE == 15

    stage = (
        await session.execute(text("SELECT stage_id FROM kids WHERE id = :k"), {"k": kid_id})
    ).scalar_one()
    assert stage == 2


async def test_five_clean_readings_promote_the_kid_above_stage_one(
    client, sandbox, session, distinct_stories_above_stage_one
):
    """The same loop, closed at a stage where the two GPC counts DISAGREE.

    Every other promotion test in this file runs at stage 1, and stage 1 is the
    one rung where a word's whole alignment IS the set of correspondences the
    stage introduces. §8's denominator is the second of those
    (`evaluate_promotion`: "not the cumulative set a stage-N reader has been
    taught"), so at stage 1 a promotion test cannot tell a grader that counts
    the stage's own sounds from one that counts every sound in the word — and
    that is why six terminal stages shipped under a green promotion suite
    (DEVIATIONS 11.70, 11.79).

    At stage 5 the two numbers are 2 and 5. Five clean readings are worth
    exactly `MIN_GPC_OBSERVATIONS`, so this also pins the boundary: promotion
    lands on the fifth story, not the fourth.
    """
    kid_id = await _new_kid(client, sandbox, stage_id=HIGH_TAP_STAGE)

    per_reading = await _evidence_per_reading(session, HIGH_TAP_WORD, HIGH_TAP_STAGE)
    aligned = len(word_gpc_ids(HIGH_TAP_WORD, HIGH_TAP_STAGE))
    assert per_reading < aligned, (
        f"{HIGH_TAP_WORD!r} is worth {per_reading} at stage {HIGH_TAP_STAGE} and aligns "
        f"to {aligned} correspondences — this test only proves anything at a stage "
        f"where those differ, which is every stage except stage 1"
    )
    assert per_reading * STORIES_REQUIRED_AT_STAGE == MIN_GPC_OBSERVATIONS

    for n in range(1, STORIES_REQUIRED_AT_STAGE + 1):
        story_id, question_id = distinct_stories_above_stage_one[n - 1]
        body = await _read_once(
            client,
            kid_id,
            story_id,
            question_id,
            correct=True,
            word=HIGH_TAP_WORD,
            wrong=HIGH_TAP_OPTIONS[1],
        )
        assert body["promotion"]["stories_completed"] == n
        # The claim the stage-1 tests cannot make: the runtime credited the
        # stage's OWN sounds, not every sound the word needed.
        assert body["promotion"]["gpc_seen"] == per_reading * n, (
            f"reading {n}: the grader counted {body['promotion']['gpc_seen']} "
            f"stage-{HIGH_TAP_STAGE} instances, not {per_reading * n}"
        )
        if n < STORIES_REQUIRED_AT_STAGE:
            assert body["promoted"] is False, f"promoted after only {n} stories"

    assert body["promoted"] is True
    assert body["promotion"]["gpc_seen"] == MIN_GPC_OBSERVATIONS
    assert body["promotion"]["from_stage"] == HIGH_TAP_STAGE
    assert body["promotion"]["to_stage"] == HIGH_TAP_STAGE + 1
    assert body["promotion"]["accuracy"] == 1.0

    stage = (
        await session.execute(text("SELECT stage_id FROM kids WHERE id = :k"), {"k": kid_id})
    ).scalar_one()
    assert stage == HIGH_TAP_STAGE + 1


async def test_promotion_stops_below_the_accuracy_threshold(
    client, sandbox, session, distinct_stories
):
    """Five stories, plenty of evidence, 60% correct — no promotion, and the
    dashboard is told which clause failed."""
    kid_id = await _new_kid(client, sandbox, stage_id=TAP_STAGE)
    for n in range(STORIES_REQUIRED_AT_STAGE):
        story_id, question_id = distinct_stories[n]
        body = await _read_once(client, kid_id, story_id, question_id, correct=n < 3)

    assert body["promoted"] is False
    assert body["promotion"]["stories_completed"] == STORIES_REQUIRED_AT_STAGE
    assert body["promotion"]["accuracy"] == pytest.approx(0.6)
    failed = [c for c in body["promotion"]["criteria"] if not c["met"]]
    assert [c["name"] for c in failed] == ["accuracy"]
    assert "need 80%" in failed[0]["detail"]

    stage = (
        await session.execute(text("SELECT stage_id FROM kids WHERE id = :k"), {"k": kid_id})
    ).scalar_one()
    assert stage == TAP_STAGE


async def test_promotion_needs_evidence_not_just_finished_stories(
    client, sandbox, session, distinct_stories
):
    """Five DIFFERENT stories with no questions answered is no proof either.

    The distinctness matters to what this test proves. Five replays of one
    story would fail the story criterion as well, so the test would pass while
    demonstrating the wrong clause.
    """
    kid_id = await _new_kid(client, sandbox, stage_id=TAP_STAGE)
    for n in range(STORIES_REQUIRED_AT_STAGE):
        reading_id = await _start(client, kid_id, distinct_stories[n][0])
        body = (await client.post(f"/api/readings/{reading_id}/complete")).json()

    assert body["promotion"]["stories_completed"] == STORIES_REQUIRED_AT_STAGE

    assert body["promoted"] is False
    assert body["promotion"]["accuracy"] is None
    failed = {c["name"] for c in body["promotion"]["criteria"] if not c["met"]}
    assert failed == {"evidence", "accuracy"}


async def test_promotion_resets_the_story_count_at_the_new_stage(
    client, sandbox, distinct_stories
):
    """`readings.stage_id` is the kid's stage at the time, so the count for the
    stage just left does not carry into the next one."""
    kid_id = await _new_kid(client, sandbox, stage_id=TAP_STAGE)
    for n in range(STORIES_REQUIRED_AT_STAGE):
        story_id, question_id = distinct_stories[n]
        body = await _read_once(client, kid_id, story_id, question_id, correct=True)
    assert body["promoted"] is True

    sixth_story, sixth_question = distinct_stories[STORIES_REQUIRED_AT_STAGE]
    after = await _read_once(client, kid_id, sixth_story, sixth_question, correct=True)
    assert after["promotion"]["from_stage"] == 2
    assert after["promotion"]["stories_completed"] == 1, "the counter is per stage"
    assert after["promoted"] is False


async def test_replaying_one_story_never_promotes(
    client, sandbox, session, story_at_stage_1, tap_question
):
    """The forged promotion, as an attack. §8's counts must be unforgeable.

    Live before the fix: a stage-5 kid reached stage 6 having read ONE story and
    answered ONE question about ONE word ten times — 31 HTTP calls — because
    `_STORIES_AT_STAGE_SQL` counted readings and every replay re-credited the
    same GPC instances, clearing `MIN_GPC_OBSERVATIONS` (DEVIATIONS 5.5) with
    the single lucky tap that floor exists to reject.

    Ten replays here. Twice `STORIES_REQUIRED_AT_STAGE`, five times the evidence
    floor, 100% accurate — and still one story, so still no promotion.
    """
    kid_id = await _new_kid(client, sandbox, stage_id=TAP_STAGE)

    for i in range(10):
        body = await _read_once(client, kid_id, story_at_stage_1, tap_question, correct=True)
        assert body["newly_completed"] is True, "each replay is a real, separate reading"
        assert body["promotion"]["stories_completed"] == 1, (
            f"replay {i + 1} counted as story {body['promotion']['stories_completed']}"
        )
        assert body["promoted"] is False

    # The evidence counters saw the word once, not ten times.
    rows = {
        r.gpc_id: (r.n_seen, r.n_correct)
        for r in await session.execute(
            text("SELECT gpc_id, n_seen, n_correct FROM gpc_stats WHERE kid_id = :k"),
            {"k": kid_id},
        )
    }
    assert rows == {gpc: (1, 1) for gpc in word_gpc_ids(TAP_WORD, TAP_STAGE)}

    stage = (
        await session.execute(text("SELECT stage_id FROM kids WHERE id = :k"), {"k": kid_id})
    ).scalar_one()
    assert stage == TAP_STAGE, "ten replays of one story promoted the kid"


async def test_a_repeat_reading_completes_but_says_it_added_nothing(
    client, sandbox, story_at_stage_1, tap_question
):
    """Re-reading is normal and allowed — a thin warm pool hands the same story
    back, and children re-read favourites. It just is not new evidence.

    `repeat_reading` is on the DTO because an empty `gpc_updates` is otherwise
    ambiguous: §11.4's celebration screen has to tell "you skipped the
    questions" apart from "you have read this one before".
    """
    kid_id = await _new_kid(client, sandbox, stage_id=TAP_STAGE)

    first = await _read_once(client, kid_id, story_at_stage_1, tap_question, correct=True)
    assert first["repeat_reading"] is False
    assert first["gpc_updates"]

    second = await _read_once(client, kid_id, story_at_stage_1, tap_question, correct=True)
    assert second["newly_completed"] is True, "the reading itself still completes"
    assert second["repeat_reading"] is True
    assert second["gpc_updates"] == []


# --- progress (§9, §11) ------------------------------------------------------
async def test_progress_reports_the_rule_as_well_as_the_verdict(
    client, sandbox, distinct_stories
):
    """§8: "thresholds in config, shown on dashboard". Interpretable by design."""
    kid_id = await _new_kid(client, sandbox, stage_id=TAP_STAGE, name="Maya")
    await _read_once(client, kid_id, *distinct_stories[0], correct=True)
    await _read_once(client, kid_id, *distinct_stories[1], correct=False)

    body = (await client.get(f"/api/kids/{kid_id}/progress")).json()

    assert body["kid"]["display_name"] == "Maya"
    assert body["kid"]["stage_id"] == TAP_STAGE
    assert body["stage_name"]
    assert body["readings_started"] == body["readings_completed"] == 2
    assert body["readings_completed_at_stage"] == 2

    assert body["thresholds"] == {
        "stories_required": STORIES_REQUIRED_AT_STAGE,
        "accuracy_threshold": 0.8,
        "min_observations": MIN_GPC_OBSERVATIONS,
        # `MAX_STAGE`, not a literal, and imported from the same module as its
        # two siblings above. This said 12, and the eleven-stage renumber turned
        # it into a claim about the LADDER wearing the costume of a claim about
        # the dashboard contract (DEVIATIONS 12.x). The proposition — §8's rule
        # is published verbatim and not merely its verdict — is unchanged, and
        # the comparison is still exact equality over the whole dict.
        "max_stage": MAX_STAGE,
    }
    assert body["promotion"]["promote"] is False
    assert len(body["promotion"]["criteria"]) == 4
    assert all(c["detail"] for c in body["promotion"]["criteria"])


async def test_progress_shows_per_gpc_accuracy_including_never_practised_ones(
    client, sandbox, distinct_stories
):
    """§11's table. A zero row is information — "never practised" — so dropping
    it would make the table look complete when it is not.

    Two DIFFERENT stories: re-reading one contributes no second observation
    (§8, `complete_reading`), so the 1-of-2 arithmetic below needs two.
    """
    kid_id = await _new_kid(client, sandbox, stage_id=TAP_STAGE)
    await _read_once(client, kid_id, *distinct_stories[0], correct=True)
    await _read_once(client, kid_id, *distinct_stories[1], correct=False)

    body = (await client.get(f"/api/kids/{kid_id}/progress")).json()
    rows = {r["gpc_id"]: r for r in body["gpc_accuracy"]}

    for gpc_id in word_gpc_ids(TAP_WORD, TAP_STAGE):
        assert rows[gpc_id]["n_seen"] == 2
        assert rows[gpc_id]["n_correct"] == 1
        assert rows[gpc_id]["accuracy"] == 0.5
        assert rows[gpc_id]["grapheme"] == gpc_id.split("|")[0]
        assert rows[gpc_id]["current_stage"] is True

    untouched = [r for r in body["gpc_accuracy"] if r["n_seen"] == 0]
    assert untouched, "the table should list taught correspondences with no evidence yet"
    assert all(r["accuracy"] is None for r in untouched)
    # Only what a stage-1 reader has been taught (§4: cumulative).
    assert {r["stage_id"] for r in body["gpc_accuracy"]} == {TAP_STAGE}


async def test_progress_lists_recent_readings_with_their_chosen_paths(
    client, sandbox, session, story_at_stage_1, tap_question
):
    """§11: "recent readings with chosen paths"."""
    kid_id = await _new_kid(client, sandbox)
    reading_id = await _start(client, kid_id, story_at_stage_1)
    segment_id = (
        await session.execute(
            text("SELECT id FROM story_segments WHERE story_id = :s ORDER BY idx LIMIT 1"),
            {"s": story_at_stage_1},
        )
    ).scalar_one()
    await client.post(
        f"/api/readings/{reading_id}/choice",
        json={"segment_id": segment_id, "choice": "Go in the cave"},
    )
    await _answer(client, reading_id, tap_question, TAP_WORD)
    await client.post(f"/api/readings/{reading_id}/complete")

    body = (await client.get(f"/api/kids/{kid_id}/progress")).json()
    recent = body["recent_readings"][0]
    assert recent["id"] == reading_id
    assert recent["story_id"] == story_at_stage_1
    assert recent["story_title"]
    assert recent["questions_answered"] == 1
    assert recent["questions_correct"] == 1
    assert recent["completed_at"] is not None
    assert [c["choice"] for c in recent["choices"]] == ["Go in the cave"]


async def test_a_brand_new_kid_has_an_empty_but_complete_progress_page(client, sandbox):
    kid_id = await _new_kid(client, sandbox, stage_id=4)
    body = (await client.get(f"/api/kids/{kid_id}/progress")).json()

    assert body["readings_started"] == 0
    assert body["recent_readings"] == []
    assert body["promotion"]["promote"] is False
    assert body["promotion"]["accuracy"] is None
    # The table is populated from the inventory, not from evidence, so a new kid
    # sees every correspondence they have been taught at zero.
    assert body["gpc_accuracy"]
    assert all(r["n_seen"] == 0 for r in body["gpc_accuracy"])
    assert {r["stage_id"] for r in body["gpc_accuracy"]} == {1, 2, 3, 4}
