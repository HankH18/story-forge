"""Phase 4: the story request API (§9), served from the warm pool (§7).

Like the Phase 2 tests these run against real Postgres and the real seeded pool.
A story-serving test that passes against an empty `stories` table would be
evidence of nothing, so these require `make seed && make warm` to have run.

The tests mutate `last_served_at` — that column exists to be mutated, and the
rotation tests are written to be order-independent so a second run of the suite
behaves identically to the first.

**Reading the pool is fine; reading an EXPECTED VALUE out of it is not**
(DEVIATIONS 7.16). Four tests here did the second thing — "`space` has no
stories", "stages 4-12 have never been warmed", "the stage-1 story we happen to
be served has story words" — and `make warm` falsified all three at once. Those
now build their own themes and stories through `pool` below and assert about
the ids they created.

**And the pool is no longer consulted even to PLACE the fixture** (DEVIATIONS
8.90-8.94). 7.16 stopped reading expected values out of the database but still
read `max(servable stage_id) + 1` to find an unoccupied stage band, so the
assumption survived one level down: that the real pool never reaches the top
stage. A full `make warm` fills all eleven stages — that is what `make warm-check` demands
— so the band stopped existing and the four tests ERRORed in setup. `Pool` now
MANUFACTURES the empty stage instead of hoping to find one: it runs the endpoint
inside one rolled-back transaction in which the live pool has been moved out of
`SERVABLE_STATUSES`. Nothing is committed, so real data is untouched and the
tests hold whatever the pool contains, full or empty.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.api.stories import (
    RATE_LIMIT_PER_MINUTE,
    SERVABLE_STATUSES,
    limiter,
)
from storyforge.db import SessionLocal
from storyforge.llm.questions import SoundTarget, TapWordDraft, check_tap_word, cue_conflict
from storyforge.main import app
from tests.owned_pool import hide_servable_pool, owned_transaction

DRAGONS = "dragons"

#: §9 caps `POST /api/stories/request` at stage 11 (`StoryRequest.stage_id`).
MAX_STAGE = 11

#: The stage the `pool` fixtures build on. A CONSTANT, deliberately: the previous
#: version of this file computed it from the live pool and that is the whole of
#: DEVIATIONS 8.90. It has exactly one requirement — a stage below it and a stage
#: above it, both inside §9's 1..11 — because the ladder tests place a story one
#: rung down (`base - 1`) and one rung up (`base + 1`, the story the ladder must
#: refuse to climb to). Any interior stage is as good as any other; the fixture
#: body is decodable at all eleven (§5.7c monotonicity).
FIXTURE_BASE_STAGE = 6
assert 1 < FIXTURE_BASE_STAGE < MAX_STAGE, "the ladder tests need base - 1 and base + 1"

#: Stage-1 decodable text, verified against the decoder at stage 1 (1.000 with
#: these story words). The inventory is cumulative (§4, §5.7c monotonicity), so
#: a fixture story carrying this body clears §1's gate at EVERY stage — which
#: matters because the request path re-checks decodability at serve time before
#: it will hand a story to a reader.
FIXTURE_SEGMENTS = (
    "A dragon sat in a cave.",
    "Sam did pat the dragon.",
    "The dragon did nap in the sand.",
)
FIXTURE_STORY_WORDS = ["cave", "dragon"]

#: Real English that no stage of §4's cumulative inventory can spell, and not a
#: sight word in it — so a story carrying this body is refused by the serve-time
#: decodability re-check at every stage. Measured in the one test that uses it
#: rather than asserted from here.
UNSERVABLE_SEGMENT = (
    "The knight thought the enormous creature might be dangerous, "
    "although its enthusiasm suggested otherwise entirely."
)

#: The largest FIXED per-request serve budget this endpoint has ever held, as a
#: function of the requested stage: `SERVE_ATTEMPTS = 3` picks on each of the two
#: theme tiers at each of the `stage` stages at or below the reader (DEVIATIONS
#: 9.66). Spelled out here rather than imported, and that is the point: the two
#: tests below plant one bad row MORE than this, so they are red against every
#: fixed budget the module has shipped — `SERVE_ATTEMPTS` alone (F4 as filed),
#: `SERVE_ATTEMPTS * 2 * stage` (F4 as first fixed, DEVIATIONS 9.71) and any
#: constant a future edit reintroduces. A budget that is derived from the DATA —
#: how many servable, answerable stories the ladder can actually reach — is the
#: only thing that passes them, which is what 9.71 argues is the requirement.
_LARGEST_FIXED_BUDGET_PER_STAGE = 3 * 2


def _rows_beyond_any_fixed_budget(stage: int) -> int:
    """How many unservable rows to plant so no fixed budget can walk past them."""
    return _LARGEST_FIXED_BUDGET_PER_STAGE * stage + 1


#: Material for `tap_word` rows written by the NODE'S OWN writer rather than by
#: hand — `check_tap_word` composes the payload and the answer key, so what this
#: test sweeps is what `generate_questions` would have persisted. Both sounds
#: come out of `FIXTURE_SEGMENTS` and both cues satisfy DEVIATIONS 7.14.
_NODE_WRITTEN_TAP_WORDS = (
    (
        TapWordDraft(target_grapheme="d", correct_word="did", other_words=["sat", "nap"]),
        SoundTarget(
            grapheme="d",
            cue="dad",
            onset_words=["did"],
            clean_words=["sat", "sam", "nap", "pat", "in"],
            stage=1,
            scope="current_stage_onset",
        ),
    ),
    (
        TapWordDraft(target_grapheme="s", correct_word="sat", other_words=["did", "nap"]),
        SoundTarget(
            grapheme="s",
            cue="sit",
            onset_words=["sat", "sam", "sand"],
            clean_words=["did", "in", "nap", "pat"],
            stage=1,
            scope="current_stage_onset",
        ),
    ),
)


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter keys on client address, which every test shares."""
    limiter.clear()
    yield
    limiter.clear()


async def _pool(session, stage_id: int, theme_slug: str = DRAGONS) -> set[int]:
    # DEVIATIONS 7.17 (closes 6.14): this filtered `status='warm'` while
    # `_SERVE_SQL` serves `warm|complete`, so the rotation test broke the moment
    # a stage-1 story flipped to `complete` — which is what reading a story
    # does. Imported from the endpoint rather than re-spelled, so the two cannot
    # drift apart again.
    rows = await session.execute(
        text(
            "SELECT s.id FROM stories s JOIN themes t ON t.id = s.theme_id "
            "WHERE s.stage_id = :stage AND t.slug = :slug AND s.status = ANY(:statuses)"
        ),
        {"stage": stage_id, "slug": theme_slug, "statuses": list(SERVABLE_STATUSES)},
    )
    return {r[0] for r in rows}


# --- fixtures the tests own outright (DEVIATIONS 7.16) -----------------------
class Pool:
    """Themes and stories that exist only inside one test's transaction.

    §7's ladder has four rungs and the lower two — nearest LOWER stage, with and
    without the requested theme — can only be provoked from a requested stage
    that holds nothing. So can STATE invariant 6, that the ladder never CLIMBS:
    proving a story one rung up is refused needs a rung the ladder would
    otherwise have to reach for. An empty stage is therefore not a convenience
    here, it is the only way to express the requirement.

    **The previous version of this class went looking for one** — `base =
    max(servable stage_id) + 1`, with an assertion that the band existed — and
    that is DEVIATIONS 8.90. It is the residue 7.16 left behind, one level below
    the thing 7.16 fixed: it stopped reading expected VALUES out of the pool but
    still bet on the pool's SHAPE, namely that stages 11 and 12 hold nothing.
    `make warm-check` demands ≥3 servable stories in all 36 (stage,theme) pairs,
    so the bet was against the product working, and a full `make warm` settled it.

    The empty stage is now MANUFACTURED. `_owned_pool` runs the test *and the
    endpoint* inside one transaction that is rolled back, and an `exclusive`
    fixture opens that transaction by moving every real servable story out of
    `SERVABLE_STATUSES` inside it. `_SERVE_SQL` then sees nothing but the rows
    this class writes, at whatever stages it chose, and `base` is a constant. Real
    data is untouched because nothing is ever committed — which is also why the
    rows below need no `DELETE` any more.

    `exclusive=False` keeps the live pool visible, for the one test whose subject
    IS the live pool (DEVIATIONS 8.92).

    justify-test-edit — VERDICT: TEST IS WRONG, and what is wrong is this FIXTURE
    PRECONDITION, not one line of what the four tests assert. The deleted
    assertion was `assert self.base + 1 <= MAX_STAGE` over `base = max(servable
    stage_id) + 1`, which claims *§9's 1..11 ladder has two consecutive empty
    stages at the top* — a fact about the operator's database, contradicted by
    `warm-check`'s own definition of a full pool. ORIGIN: commit `559f102`,
    DEVIATIONS 7.16. REVERT CHECK: nothing to revert — the four ERRORs predate
    every change here and are caused by `make warm`; the endpoint is untouched and
    `test_fallback_never_serves_a_story_above_the_requested_stage` (invariant 6
    over the live pool, no fixture) stayed green throughout. The requirement the
    assertion served — a requested stage that holds nothing — is preserved and is
    now manufactured rather than hoped for, and every ladder assertion below,
    including `"the ladder climbed a stage (STATE invariant 6)"`, is unchanged.
    Sabotaging `_SERVE_SQL` so the ladder climbs turns that one red against a full
    pool AND against an empty one, which is the evidence that the isolation did not
    quietly stop testing it (DEVIATIONS 8.90-8.94).
    """

    def __init__(self, session: AsyncSession, *, exclusive: bool) -> None:
        self.session = session
        self.exclusive = exclusive
        self.base = FIXTURE_BASE_STAGE
        self.themes: dict[str, int] = {}
        self.stories: list[int] = []
        #: How many real servable stories `start` hid. Reported by the assertion
        #: below rather than asserted about — it is a fact about the operator's
        #: database, which is the thing this class no longer bets on.
        self.hidden = 0

    async def start(self) -> Pool:
        if not self.exclusive:
            return self

        # The hide, and the assertion that it covered everything the serving path
        # counts as servable, both live in `tests/owned_pool.py` now — three files
        # need them (DEVIATIONS 9.62). Nothing about the mechanism changed.
        self.hidden = await hide_servable_pool(self.session)
        return self

    async def theme(self, name: str) -> int:
        if name not in self.themes:
            self.themes[name] = (
                await self.session.execute(
                    text(
                        "INSERT INTO themes (slug, display_name, emoji) "
                        "VALUES (:s, :d, '🧪') RETURNING id"
                    ),
                    {"s": f"zz-fixture-{name}", "d": f"Fixture {name}"},
                )
            ).scalar_one()
        return self.themes[name]

    def slug(self, name: str) -> str:
        return f"zz-fixture-{name}"

    async def story(
        self,
        *,
        stage: int,
        theme: str,
        title: str = "A Fixture Story",
        status: str = "warm",
        story_words: list[str] | None = None,
        questions: bool = True,
        segments: tuple[str, ...] = FIXTURE_SEGMENTS,
    ) -> int:
        """A fixture story that is actually servable, question included.

        `segments` defaults to text the decoder measures at 1.000 at stage 1, so a
        fixture story clears §1's gate at every stage and the serve-time re-check
        cannot refuse it for a reason the test did not intend. It is a parameter for
        exactly one caller — `test_the_ladder_descends_past_a_rung_the_serve_gate_
        refuses` needs rows the gate WILL refuse, which is the state F4 is about.

        **`questions=True` is the default because a story with none is no longer
        servable at all** (`api/stories.py::ANSWERABLE_SQL`, DEVIATIONS 8.50): six
        live `warm` stories carried zero `questions` rows, a child served one read
        to the end and got no question screen, and §8 got no evidence. So a fixture
        without one is excluded by `_SERVE_SQL` before any of the ladder, rotation
        or DTO behaviour these tests are about can be provoked — which would make
        them pass or fail for a reason none of them names.

        The row is written by `check_tap_word` rather than typed by hand, the same
        discipline `test_a_real_node_produced_question_never_ships_its_answer_key`
        uses below: what the fixture holds is then what the node would have
        persisted, so "servable" here means what it means in production. That test
        passes `questions=False` because it writes its own set and asserts over all
        of them.
        """
        theme_id = await self.theme(theme)
        story_id = (
            await self.session.execute(
                text(
                    "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                    "story_words, gen_meta) VALUES (:st, :th, :status, :title, 1.0, "
                    "CAST(:words AS text[]), '{}'::jsonb) RETURNING id"
                ),
                {
                    "st": stage,
                    "th": theme_id,
                    "status": status,
                    "title": title,
                    "words": list(FIXTURE_STORY_WORDS if story_words is None else story_words),
                },
            )
        ).scalar_one()
        for idx, body in enumerate(segments):
            await self.session.execute(
                text(
                    "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
                    "VALUES (:s, :i, :b, ARRAY[]::text[])"
                ),
                {"s": story_id, "i": idx, "b": body},
            )
        if questions:
            # Composed from `FIXTURE_SEGMENTS` even when `segments` overrides them:
            # the only job this row has is to satisfy `ANSWERABLE_SQL` so the story
            # reaches the serve-time gate, and `check_tap_word` proves its own
            # correctness against the text it was given (§11.3), which the
            # deliberately-undecodable override cannot supply an onset word from.
            draft, target = _NODE_WRITTEN_TAP_WORDS[0]
            question, problems = check_tap_word(
                draft,
                targets=[target],
                stage_id=1,
                story_text=" ".join(FIXTURE_SEGMENTS),
                story_words=FIXTURE_STORY_WORDS,
                seed=story_id,
            )
            assert question is not None, problems  # the node's own writer
            await self.session.execute(
                text(
                    "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                    "VALUES (:s, 0, :t, :p, CAST(:pl AS jsonb), CAST(:a AS jsonb))"
                ),
                {
                    "s": story_id,
                    "t": question.qtype,
                    "p": question.prompt,
                    "pl": json.dumps(question.payload),
                    "a": json.dumps(question.answer),
                },
            )
        # Releases the fixture's SAVEPOINT — not a write anyone outside this test
        # can see. The session is joined to `_owned_pool`'s transaction, which is
        # rolled back, so there is nothing here for a `stop()` to clean up and no
        # window in which a concurrent `make warm-check` can see a `zz-fixture-*`
        # theme sitting in the real `themes` table (DEVIATIONS 8.91).
        await self.session.commit()
        self.stories.append(story_id)
        return story_id


@asynccontextmanager
async def _owned_pool(*, exclusive: bool) -> AsyncIterator[Pool]:
    """One transaction, shared by the test and by the endpoint, then rolled back.

    The mechanism and the three reasons each of its pieces is load-bearing are in
    `tests/owned_pool.py`, which two other files now share (DEVIATIONS 9.62).
    """
    async with owned_transaction() as session:
        yield await Pool(session, exclusive=exclusive).start()


@pytest.fixture
async def pool() -> AsyncIterator[Pool]:
    """The fixture owns the whole visible pool. Use this for the request path."""
    async with _owned_pool(exclusive=True) as fixture:
        yield fixture


@pytest.fixture
async def shared_pool() -> AsyncIterator[Pool]:
    """Fixture rows BESIDE the live pool, not instead of it (DEVIATIONS 8.92).

    For the answer-key sweep below, whose whole strength per 7.18 is that it reads
    whatever the question node actually wrote into the real database — it caught
    7.14 that way. Hiding the pool from it would have left it green and reduced it
    to a fixture-only test, which is 8.50's `_insert_story` mistake exactly. It
    fetches stories by id and never asks the ladder for one, so it needs rows of
    its own and no isolation beyond the rollback.
    """
    async with _owned_pool(exclusive=False) as fixture:
        yield fixture


async def _request(client, stage_id: int, theme=DRAGONS) -> dict:
    resp = await client.post(
        "/api/stories/request", json={"stage_id": stage_id, "theme_id_or_slug": theme}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- happy path --------------------------------------------------------------
async def test_request_returns_a_full_dto(client, pool):
    """§11.2: kid mode renders from this response alone, with no second call.

    justify-test-edit — VERDICT: TEST IS WRONG (about WHOSE story words).
    `assert body["story_words"]` demanded that whichever stage-1 dragons story
    the round-robin happened to hand back carried at least one story word. §5.5
    says generation picks **≤3**; zero is inside the spec, so the assertion made
    a claim the product never promised. It was also provably non-deterministic:
    in one session it passed in a whole-file run and failed under `-x`, because
    `_SERVE_SQL` orders on `last_served_at` and earlier tests move it. REVERT
    CHECK: yes, it fails independently of any change made here — it was red
    before this branch touched anything.

    Not weakened: the DTO contract is now asserted against a story this test
    wrote, so `story_words` is checked for its VALUE rather than for being
    non-empty, and the served id is pinned instead of merely being "in the pool".
    """
    story_id = await pool.story(stage=pool.base, theme="alpha", title="A Full DTO")

    body = await _request(client, pool.base, theme=pool.slug("alpha"))

    assert body["id"] == story_id
    assert body["status"] == "warm"
    assert body["stage_id"] == pool.base
    assert body["theme_slug"] == pool.slug("alpha")
    assert body["title"] == "A Full DTO"
    assert 0.0 <= body["decodability"] <= 1.0
    assert body["fallback"] is None

    # Picture chips (§4) — distinct story words, never more than three (§7 gate).
    assert body["story_words"] == FIXTURE_STORY_WORDS
    assert len(body["story_words"]) == len(set(body["story_words"])) <= 3

    assert body["segments"], "a story with no segments is not renderable"
    assert [s["body"] for s in body["segments"]] == list(FIXTURE_SEGMENTS)
    for seg in body["segments"]:
        assert seg["body"].strip()
        assert set(seg) >= {"id", "idx", "body", "scene_tags", "asset", "choice_label"}


async def test_segments_come_back_in_idx_order(client):
    """The reading view pages one segment per screen (§11.2); order is the story."""
    for stage in (1, 2, 3):
        body = await _request(client, stage)
        idxs = [s["idx"] for s in body["segments"]]
        assert idxs == sorted(idxs) == list(range(len(idxs)))


async def test_dto_degrades_when_questions_and_assets_are_absent(client):
    """The QUESTION_GEN and SCENE_TAGGING nodes are being built in parallel.

    Missing rows must produce an empty list and a null asset — never a 500 and
    never an invented URL (the UI draws a striped placeholder instead).
    """
    body = await _request(client, 2)
    assert isinstance(body["questions"], list)
    for seg in body["segments"]:
        assert seg["asset"] is None or seg["asset"]["url"]


async def test_dto_carries_assets_and_questions_when_present(client, session):
    """The other half of the same contract, exercised with real rows."""
    story_id = min(await _pool(session, 1))
    seg_id = (
        await session.execute(
            text("SELECT id FROM story_segments WHERE story_id = :s ORDER BY idx LIMIT 1"),
            {"s": story_id},
        )
    ).scalar_one()

    asset_id = (
        await session.execute(
            text(
                "INSERT INTO assets (theme_id, kind, tags, url, alt_text) "
                "VALUES (1, 'segment', ARRAY['cave','dragon_friendly'], "
                "'/assets/test-cave.png', 'A friendly dragon in a cave') RETURNING id"
            )
        )
    ).scalar_one()
    # The payload is written the way the OLD node wrote it — `target_grapheme`
    # in the served payload. That is the shape 9 live rows had, and the DTO must
    # strip it whatever the row holds.
    question_id = (
        await session.execute(
            text(
                "INSERT INTO questions (story_id, segment_id, idx, qtype, prompt, payload, answer) "
                "VALUES (:st, :sg, 0, 'tap_word', 'Tap the word that says /d/og.', "
                """CAST('{"options": ["dog", "cat"], "cue_word": "did", """
                """"target_grapheme": "d"}' AS jsonb), """
                """CAST('{"correct": "dog"}' AS jsonb)) RETURNING id"""
            ),
            {"st": story_id, "sg": seg_id},
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE story_segments SET asset_id = :a WHERE id = :s"),
        {"a": asset_id, "s": seg_id},
    )
    await session.commit()

    try:
        body = (await client.get(f"/api/stories/{story_id}")).json()

        seg = next(s for s in body["segments"] if s["id"] == seg_id)
        assert seg["asset"] == {
            "id": asset_id,
            "url": "/assets/test-cave.png",
            "alt_text": "A friendly dragon in a cave",
            "tags": ["cave", "dragon_friendly"],
        }

        q = next(q for q in body["questions"] if q["id"] == question_id)
        assert q["qtype"] == "tap_word"
        assert q["options"] == ["dog", "cat"]
        assert q["segment_id"] == seg_id
        # The answer key is graded server-side (§9) and never shipped to a kid.
        # `target_grapheme` is part of that key — exactly one option starts with
        # it by construction — and IS present on the row above, so this is a real
        # test of the filter rather than of a field nobody inserted.
        assert "answer" not in q and "correct" not in q["payload"]
        assert "target_grapheme" not in q["payload"]
        assert q["payload"] == {"options": ["dog", "cat"], "cue_word": "did"}
        # §11.3 picture chips: `mcq2` options may contain story words, which are
        # by definition undecodable at this stage (Fix 5).
        assert q["story_words"] == body["story_words"]
    finally:
        await session.execute(
            text("UPDATE story_segments SET asset_id = NULL WHERE id = :s"), {"s": seg_id}
        )
        await session.execute(text("DELETE FROM questions WHERE id = :q"), {"q": question_id})
        await session.execute(text("DELETE FROM assets WHERE id = :a"), {"a": asset_id})
        await session.commit()


async def test_a_real_node_produced_question_never_ships_its_answer_key(client, shared_pool):
    """The 4.9 claim, tested against rows the QUESTION_GEN node actually wrote.

    The previous version of this assertion inserted its own payload and then
    checked for a key it had never added — it could not have failed. This one
    reads a real `tap_word` row, takes the real answer key from the database, and
    proves the served DTO does not contain it. `target_grapheme` is the one that
    mattered: `check_tap_word` guarantees exactly one option starts with it.

    justify-test-edit — VERDICT: two findings, opposite directions.
    (a) The leak assertion went red on live row 4392 (`cue_word: "the"` over
    `["sat", "in", "then"]`) — **CODE IS WRONG**. That is DEVIATIONS 7.14; the
    generator was fixed and the eight live rows repaired, and this assertion was
    not touched except to widen it, because it did its job.
    (b) `assert rows, "run make warm"` — **TEST IS WRONG**: it makes a claim
    about the operator's database rather than about the product, and fails on a
    freshly migrated one for a reason unrelated to answer keys. REVERT CHECK for
    (b): yes, it fails wherever `make warm` has not run.

    So the sweep keeps its strength — it still reads every real node-written row
    — and gains rows this test owns, built by calling `check_tap_word` itself
    rather than by hand-typing a payload, so the precondition is satisfied by
    construction. The leak check is widened from prefix-only to 7.14's full
    containment rule in both directions: under the old, prefix-only form live row
    696 (`cue_word: "win"` over the option `in`) was invisible.

    `shared_pool`, not `pool`, and that distinction is DEVIATIONS 8.92: `pool`
    hides the live pool so §7's lower rungs can be provoked, and the sweep this
    test IS would then have had nothing but its own two rows to sweep — green,
    still carrying the paragraph above about live row 4392, and no longer able to
    find another one. Nothing it asserts changed; it fetches by id and never asks
    the ladder for a story, so the isolation it needs is only the rollback.
    """
    # `questions=False`: this test writes the whole set itself and asserts over all
    # of it, so `Pool.story`'s default row would be a third, unasserted question.
    session = shared_pool.session
    story_id = await shared_pool.story(
        stage=shared_pool.base, theme="alpha", title="Node Written Questions", questions=False
    )
    for idx, (draft, target) in enumerate(_NODE_WRITTEN_TAP_WORDS):
        question, problems = check_tap_word(
            draft,
            targets=[target],
            stage_id=1,
            story_text=" ".join(FIXTURE_SEGMENTS),
            story_words=FIXTURE_STORY_WORDS,
            seed=11,
        )
        assert question is not None, problems  # the node's own writer, not a fixture
        await session.execute(
            text(
                "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                "VALUES (:s, :i, :t, :p, CAST(:pl AS jsonb), CAST(:a AS jsonb))"
            ),
            {
                "s": story_id,
                "i": idx,
                "t": question.qtype,
                "p": question.prompt,
                "pl": json.dumps(question.payload),
                "a": json.dumps(question.answer),
            },
        )
    await session.commit()

    rows = (
        await session.execute(
            text(
                "SELECT q.id, q.story_id, q.payload, q.answer FROM questions q "
                "JOIN stories s ON s.id = q.story_id "
                "WHERE q.qtype = 'tap_word' AND s.status = ANY(:statuses) "
                "ORDER BY q.id"
            ),
            {"statuses": list(SERVABLE_STATUSES)},
        )
    ).all()
    assert len(rows) >= len(_NODE_WRITTEN_TAP_WORDS), "the owned rows are missing"
    assert any(r.story_id == story_id for r in rows)

    for row in rows:
        body = (await client.get(f"/api/stories/{row.story_id}")).json()
        served = next(q for q in body["questions"] if q["id"] == row.id)
        correct = row.answer["correct"]

        assert set(served["payload"]) <= {"options", "cue_word"}, served["payload"]
        for key in ("target_grapheme", "correct", "index", "answer"):
            assert key not in served["payload"], f"question {row.id} leaks {key}"

        # And the property behind the field name: nothing served singles out ANY
        # option by shape. `options` is exempt — it must contain the answer.
        # Pointing at a wrong option is the same defect with the opposite sign
        # (7.14): it steers the child away from the word they were to decode.
        for key, value in served["payload"].items():
            if key == "options" or not isinstance(value, str):
                continue
            pointed = [o for o in served["options"] if cue_conflict(value, o)]
            assert not pointed, (
                f"question {row.id}: payload[{key!r}]={value!r} singles out {pointed} "
                f"by shape (correct answer is {correct!r})"
            )


async def test_questions_carry_the_story_words_for_picture_chips(client, session):
    """§4/§11.2: `mcq2` options are validated with the story-word exemption, so
    they may contain words that are NOT decodable at this stage ("The rocket can
    tap the moon." at stage 1). The story body chips those from
    `StoryDTO.story_words`; the question screen needs the same list or it renders
    an untaught word as plain text."""
    story_id = (
        await session.execute(
            text(
                "SELECT s.id FROM stories s JOIN questions q ON q.story_id = s.id "
                "WHERE s.status IN ('warm', 'complete') AND cardinality(s.story_words) > 0 "
                "ORDER BY s.id LIMIT 1"
            )
        )
    ).scalar()
    assert story_id, "needs a warm story with story words and questions"

    body = (await client.get(f"/api/stories/{story_id}")).json()
    assert body["questions"]
    for q in body["questions"]:
        assert q["story_words"] == body["story_words"]


# --- warm-pool mechanics (§7) ------------------------------------------------
async def test_request_stamps_last_served_at(client, session):
    before = (await session.execute(text("SELECT max(last_served_at) FROM stories"))).scalar_one()

    body = await _request(client, 3)

    served = (
        await session.execute(
            text("SELECT last_served_at FROM stories WHERE id = :i"), {"i": body["id"]}
        )
    ).scalar_one()
    assert served is not None
    assert before is None or served >= before


async def test_least_recently_served_rotation(client, session):
    """§7: the pool rotates, so a kid does not get the same story twice."""
    pool = await _pool(session, 1)
    assert len(pool) >= 2, "needs a warm pool of at least 2 — run `make warm`"

    served = [(await _request(client, 1))["id"] for _ in range(len(pool))]
    assert set(served) == pool, "rotation skipped part of the pool"
    assert len(set(served)) == len(served), "the same story was served twice in one cycle"

    # One more turn of the wheel comes back to the head of the queue.
    assert (await _request(client, 1))["id"] == served[0]


async def test_rejected_stories_are_never_served(client, shared_pool):
    """Child-safety invariant: rejected failed the gate or moderation (§7).

    justify-test-edit — VERDICT: TEST IS WRONG, and what was wrong was WHICH ROW it
    fetched. The by-id half read `assert (await
    client.get(f"/api/stories/{min(rejected)}")).status_code == 404`, and
    `min(id) WHERE status = 'rejected'` is story **1**, which carries **0**
    `questions` rows — so after DEVIATIONS 8.50 the 404 came from `stories.py:734`
    (answerability) and the status refusal at `:730` was never reached. ORIGIN:
    commit `b857860` (Phase 4), written before `ANSWERABLE_SQL` existed, so the
    choice of `min()` was harmless when it was made. REVERT CHECK: nothing to
    revert — it is green on pristine code AND green against a build with `:730`
    replaced by `if False:`, while `GET /api/stories/8` (a rejected story that DOES
    carry questions) returns **200** on that same build. 4 of the 21 rejected rows
    are answerable, so a row with teeth existed and `min()` did not pick it
    (AUDIT-PHASE7 F2, DEVIATIONS 9.60-9.61).

    Not weakened — the row is now CREATED rather than selected, and the test asserts
    its own premise so it cannot go quiet again. Created rather than searched for
    because on a correctly warmed database there is nothing to find: STATE invariant
    11 runs enrichment only after the gate AND moderation, so a story that gets
    rejected is never enriched. The four answerable rejected rows here are grammar-
    backfill casualties, and selecting on them would be 8.90's mistake with the sign
    flipped — a fixture betting the database keeps a historical accident (9.64).

    `shared_pool` and not `pool`: the request-endpoint sweep below reads the LIVE
    pool for stages 1-3, and hiding it would leave that half with nothing to sweep
    (DEVIATIONS 8.92). The rollback means neither half stamps a real
    `last_served_at` any more.
    """
    session = shared_pool.session

    # A rejected story that is answerable in every respect except its status, so
    # `status` is the only thing that can produce the 404 below.
    poison = await shared_pool.story(
        stage=1, theme="alpha", title="Rejected But Answerable", status="rejected"
    )
    assert (
        await session.execute(
            text("SELECT EXISTS (SELECT 1 FROM questions WHERE story_id = :i)"), {"i": poison}
        )
    ).scalar_one(), (
        f"story {poison} carries no question, so a 404 on it would be "
        "`ANSWERABLE_SQL`'s and this test would prove nothing about `status`"
    )

    rejected = {
        r[0]
        for r in await session.execute(text("SELECT id FROM stories WHERE status = 'rejected'"))
    }
    assert poison in rejected

    for stage in (1, 2, 3):
        for _ in range(3):
            body = await _request(client, stage)
            assert body["id"] not in rejected
            assert body["status"] != "rejected"
            limiter.clear()

    # Nor by id: a rejected story is an eval artifact, not content. `poison` is the
    # deterministic floor; any live rejected row that is also answerable is swept
    # too, because those are the rows that caught this in the first place.
    live_answerable = [
        r[0]
        for r in await session.execute(
            text(
                "SELECT s.id FROM stories s WHERE s.status = 'rejected' AND s.id <> :p "
                "AND EXISTS (SELECT 1 FROM questions q WHERE q.story_id = s.id) ORDER BY s.id"
            ),
            {"p": poison},
        )
    ]
    for story_id in (poison, *live_answerable):
        resp = await client.get(f"/api/stories/{story_id}")
        assert resp.status_code == 404, (
            f"rejected story {story_id} is fetchable by id ({resp.status_code}) — it "
            "failed the decodability gate or moderation (§7)"
        )


# --- fallback ladder ---------------------------------------------------------
async def test_empty_pair_falls_back_to_the_same_stage_and_another_theme(client, pool):
    """§7 rung 2: the kid never sees an error, and stage outranks theme.

    justify-test-edit — VERDICT: TEST IS WRONG. Its docstring read "`space` has
    no stories at all", which was a fact about the database on the day it was
    written and is not a property of the product; §7 says nothing about `space`.
    `make warm` filled `space` and the endpoint then correctly served a stage-3
    space story — rung 1, the right answer for a pair that is no longer empty —
    so the assertion failed while the code was right. REVERT CHECK: yes, the
    pool changed, not the code.

    The requirement it encodes (rung 2 — same stage, other theme — beats rung 3
    — lower stage, requested theme) is real and is now checked HARDER: the
    lower-stage same-theme story that must be declined is created here rather
    than assumed to exist, so the ordering is proved rather than hoped for.
    """
    wanted = await pool.story(stage=pool.base, theme="alpha", title="Same Stage Other Theme")
    declined = await pool.story(stage=pool.base - 1, theme="beta", title="Lower Stage Same Theme")

    body = await _request(client, pool.base, theme=pool.slug("beta"))

    assert body["id"] == wanted
    assert body["id"] != declined, "a lower stage was preferred over the requested one"
    assert body["stage_id"] == pool.base, "stage fidelity outranks theme preference"
    assert body["theme_slug"] == pool.slug("alpha")
    assert body["status"] != "rejected"
    assert body["fallback"]["kind"] == "other_theme"
    assert body["fallback"]["requested_theme_slug"] == pool.slug("beta")
    assert body["segments"]


async def test_fallback_never_serves_a_story_above_the_requested_stage(client):
    """A higher-stage story may contain GPCs this reader has never been taught."""
    for stage in (1, 2, 3):
        for theme in ("space", "animals", DRAGONS):
            body = await _request(client, stage, theme=theme)
            assert body["stage_id"] <= stage
            limiter.clear()


async def test_a_stage_with_no_pool_falls_back_to_the_nearest_lower_stage(client, pool):
    """§7 rungs 3 and 4, and STATE invariant 6: the ladder NEVER climbs.

    justify-test-edit — VERDICT: TEST IS WRONG, twice over. Its docstring said
    "stages 4-12 have never been warmed"; `make warm` warmed 4 and 5 including
    `animals`, so requesting (12, animals) found a stage-5 animals story and
    returned `lower_stage` where the test expected `lower_stage_other_theme` —
    the code was right and the assumption was stale. REVERT CHECK: yes.

    Worse, it read its own expected value out of the database
    (`highest = max(stage_id) ...; assert body["stage_id"] == highest`), so it
    asserted the endpoint's answer back at itself: an implementation that
    ignored the requested stage entirely and always served the highest story in
    the pool would have satisfied it. That is the assertion being STRENGTHENED
    here, not relaxed — `above` is a story the ladder must refuse to climb to,
    and nothing in the old test could express that.
    """
    below = await pool.story(stage=pool.base, theme="alpha", title="Nearest Lower Stage")
    above = await pool.story(stage=pool.base + 1, theme="alpha", title="Never Climb To This")
    await pool.theme("beta")  # exists, holds nothing — an empty theme, not a 404
    asked = pool.base + 1

    # Baseline: `above` sits AT the requested stage, so rung 1 serves it and the
    # next assertion is about the ladder rather than about an empty database.
    body = await _request(client, asked, theme=pool.slug("alpha"))
    assert body["id"] == above and body["fallback"] is None

    # Now ask BELOW it. A story one stage up may use GPCs this reader was never
    # taught, so the ladder must go down to `below` and never up to `above`.
    body = await _request(client, pool.base, theme=pool.slug("alpha"))
    assert body["id"] == below, "the ladder climbed a stage (STATE invariant 6)"
    assert body["stage_id"] == pool.base
    assert body["fallback"] is None

    # And the two lower-stage rungs, from a stage that holds nothing at all.
    await pool.session.execute(
        text("UPDATE stories SET status = 'rejected' WHERE id = :i"), {"i": above}
    )
    await pool.session.commit()

    body = await _request(client, asked, theme=pool.slug("beta"))
    assert body["id"] == below
    assert body["stage_id"] == pool.base < asked
    assert body["fallback"]["kind"] == "lower_stage_other_theme"
    assert body["fallback"]["requested_stage_id"] == asked

    body = await _request(client, asked, theme=pool.slug("alpha"))
    assert body["id"] == below
    assert body["stage_id"] == pool.base < asked
    assert body["fallback"]["kind"] == "lower_stage"
    assert body["fallback"]["requested_stage_id"] == asked


async def test_the_ladder_descends_past_a_rung_the_serve_gate_refuses(client, pool, session):
    """AUDIT-PHASE7 F4: a rung of unservable rows must not answer the child a 503.

    §01 says a child never sees an error and §7 makes the warm pool the answer to
    *every* failure path, so a 503 with a correct story one stage down is the worst
    version of one. `SERVE_ATTEMPTS` was the budget for the whole request, so
    `SERVE_ATTEMPTS` bad rows at the top rung spent it before the ladder descended
    (DEVIATIONS 9.66); it is now the size of the candidate set the ladder can
    reach, so nothing but an exhausted pool can stop the descent (9.71).

    The bad rows are answerable and claim `decodability 1.0` in their own column —
    exactly what a mis-persisted story looks like, and the reason a SQL filter on
    that column cannot replace the serve-time decoder pass (9.67).

    justify-test-edit — VERDICT: TEST IS WRONG, and it is the FIXTURE PRECONDITION
    that is wrong, not one assertion below it. It planted `SERVE_ATTEMPTS + 1` = 4
    bad rows at `pool.base + 1` = stage 7, where 9.66's budget is `3 * 2 * 7` = 42
    — a 10x margin, so the test could not fail for the reason its own name gives
    and 9.70's sabotage sweep only turned it red against the ORIGINAL
    `range(SERVE_ATTEMPTS)`. REVERT CHECK: no — it passes against the shipped code
    and against the fix; it is vacuous, not red. ORIGIN: commit `cd55c97`,
    DEVIATIONS 9.66. EVIDENCE the count is what was wrong: 43 rows here (and 7 in
    the same-rung test below) turn it red against the shipped per-stage budget,
    measured in DEVIATIONS 9.71. ACTION: the row count is raised past every fixed
    budget the module has held; every assertion is byte-for-byte what it was,
    except the failure message, which now quotes the number actually planted.
    BLAST RADIUS: `SERVE_ATTEMPTS` had one other reader, this file's import, and
    the constant no longer exists (9.71).
    """
    from storyforge.decoder import mastered_ids, validate_text

    asked = pool.base + 1
    planted = _rows_beyond_any_fixed_budget(asked)
    for i in range(planted):
        await pool.story(
            stage=asked,
            theme="alpha",
            title=f"Undecodable {i}",
            story_words=[],
            segments=(UNSERVABLE_SEGMENT,),
        )
    good = await pool.story(stage=pool.base, theme="alpha", title="One Rung Down")
    # The rung the ladder must never climb to, whatever else it refuses (STATE
    # invariant 6). Decodable, so only `stage_id <= :stage` can be keeping it out.
    never = await pool.story(stage=asked + 1, theme="alpha", title="Never Climb To This")

    # The premise, measured rather than asserted from a constant.
    sight = frozenset(
        r[0]
        for r in await session.execute(
            text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": asked}
        )
    )
    measured = validate_text(
        UNSERVABLE_SEGMENT, mastered_ids(asked), sight, frozenset()
    ).decodability
    assert measured < 0.95, f"the bad rows measure {measured:.3f}; they prove nothing"

    # Posted directly rather than through `_request`, so the 503 this test exists to
    # forbid is reported in the words of the requirement it breaks.
    resp = await client.post(
        "/api/stories/request",
        json={"stage_id": asked, "theme_id_or_slug": pool.slug("alpha")},
    )
    assert resp.status_code == 200, (
        f"the ladder did not descend past {planted} rows the serve-time "
        f"gate refuses ({measured:.1%} decodable): {resp.status_code} {resp.text}. §01 "
        "says a child never sees an error and §7 makes the warm pool the answer to "
        "every failure path, and a correct story was sitting one stage down"
    )
    body = resp.json()

    assert body["id"] != never and body["stage_id"] < asked, (
        f"the ladder climbed a stage (STATE invariant 6): asked for {asked}, served "
        f"{body['id']} at stage {body['stage_id']}"
    )
    assert body["id"] == good, f"expected the story one rung down, got {body['id']}"
    assert body["fallback"]["kind"] == "lower_stage"
    assert body["fallback"]["requested_stage_id"] == asked


async def test_a_wall_of_bad_rows_does_not_hide_the_good_ones_on_the_same_rung(
    client, pool, session
):
    """AUDIT-PHASE7 F4 at stage 1, where there is no rung below to descend to.

    The test above proves the ladder DESCENDS past a bad rung. This proves the
    other half, and it is the half no per-rung budget can satisfy: a stage-1
    reader has exactly one rung, so the good stories are IN the bad rung and the
    only way to reach them is to keep walking inside it. `SERVE_ATTEMPTS`
    failures per (stage, theme-match) tier — the shape DEVIATIONS 9.66 claims and
    9.71's proposal restated — would abandon the tier after three bad rows and
    answer this child a 503 with a correct story sitting behind the fourth.

    Measured against the shipped code, which walked `3 * 2 * 1` = 6 rows: five
    bad rows served the good story, six answered **503 no story at or below stage
    1 passes §1's 95% gate for this reader** (DEVIATIONS 9.71). The boundary was
    the budget, and nothing else in the pool changed.

    Every row here is `warm`, answerable and claims `decodability 1.0`, so
    `_SERVE_SQL`, `ANSWERABLE_SQL` and `warm.pool_coverage` all count them; only
    the serve-time decoder pass can tell them apart. They are written FIRST, so
    `last_served_at ASC NULLS FIRST, id ASC` puts every one of them ahead of the
    good story — the walk cannot reach it by luck.
    """
    from storyforge.decoder import mastered_ids, validate_text

    asked = 1
    planted = _rows_beyond_any_fixed_budget(asked)
    for i in range(planted):
        await pool.story(
            stage=asked,
            theme="alpha",
            title=f"Undecodable {i}",
            story_words=[],
            segments=(UNSERVABLE_SEGMENT,),
        )
    good = await pool.story(stage=asked, theme="alpha", title="Behind The Wall")

    # Both premises measured rather than asserted from a constant: the bad rows
    # really are refused at stage 1, and the good row really is servable there —
    # otherwise a 503 would be the correct answer and this test would prove nothing.
    sight = frozenset(
        r[0]
        for r in await session.execute(
            text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": asked}
        )
    )
    measured = validate_text(
        UNSERVABLE_SEGMENT, mastered_ids(asked), sight, frozenset()
    ).decodability
    assert measured < 0.95, f"the bad rows measure {measured:.3f}; they prove nothing"
    readable = validate_text(
        " ".join(FIXTURE_SEGMENTS),
        mastered_ids(asked),
        sight,
        frozenset(w.lower() for w in FIXTURE_STORY_WORDS),
    ).decodability
    assert readable >= 0.95, (
        f"the good row measures {readable:.3f} at stage {asked}; the gate would "
        "refuse it too and this test would pass on a 503 being correct"
    )

    resp = await client.post(
        "/api/stories/request",
        json={"stage_id": asked, "theme_id_or_slug": pool.slug("alpha")},
    )
    assert resp.status_code == 200, (
        f"{planted} rows the serve-time gate refuses ({measured:.1%} decodable) "
        f"buried a story that measures {readable:.1%} on the SAME rung: "
        f"{resp.status_code} {resp.text}. §01 says a child never sees an error and "
        "§7 makes the warm pool the answer to every failure path"
    )
    body = resp.json()

    assert body["id"] == good, f"expected the story behind the wall, got {body['id']}"
    assert body["stage_id"] == asked
    assert body["fallback"] is None, "the requested (stage, theme) was served — rung 1"


async def test_theme_may_be_given_as_an_id_or_a_slug(client):
    by_slug = await _request(client, 1, theme=DRAGONS)
    by_id = await _request(client, 1, theme=1)
    assert by_slug["theme_id"] == by_id["theme_id"] == 1


# --- unknown stage / theme ---------------------------------------------------
async def test_unknown_theme_is_a_404(client):
    """Pool exhaustion is not an error; naming a theme that does not exist is."""
    resp = await client.post(
        "/api/stories/request", json={"stage_id": 1, "theme_id_or_slug": "unicorns"}
    )
    assert resp.status_code == 404
    assert "unicorns" in resp.json()["detail"]

    resp = await client.post("/api/stories/request", json={"stage_id": 1, "theme_id_or_slug": 999})
    assert resp.status_code == 404


async def test_stage_outside_the_scope_and_sequence_is_a_422(client):
    for stage in (0, 13, 99):
        resp = await client.post(
            "/api/stories/request", json={"stage_id": stage, "theme_id_or_slug": DRAGONS}
        )
        assert resp.status_code == 422, stage


# --- GET /api/stories/{id} ---------------------------------------------------
async def test_get_story_returns_the_same_dto(client):
    served = await _request(client, 2)
    resp = await client.get(f"/api/stories/{served['id']}")
    assert resp.status_code == 200

    fetched = resp.json()
    # `fallback` describes one request, not the story, so it is the one field
    # that legitimately differs.
    assert fetched | {"fallback": served["fallback"]} == served


async def test_get_unknown_story_is_a_404(client):
    assert (await client.get("/api/stories/99999999")).status_code == 404


# --- rate limit (§9) ---------------------------------------------------------
async def test_rate_limit_triggers_at_ten_per_minute(client):
    for i in range(RATE_LIMIT_PER_MINUTE):
        resp = await client.post(
            "/api/stories/request", json={"stage_id": 1, "theme_id_or_slug": DRAGONS}
        )
        assert resp.status_code == 200, f"request {i + 1} should be allowed"

    resp = await client.post(
        "/api/stories/request", json={"stage_id": 1, "theme_id_or_slug": DRAGONS}
    )
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1

    # The limiter is scoped to this endpoint, not the whole app.
    assert (await client.get("/api/stories/2")).status_code in (200, 404)
