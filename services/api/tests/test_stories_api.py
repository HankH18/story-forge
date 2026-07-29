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
the ids they created. The database is still consulted, but only to place the
fixture where nothing else is, which is the difference between a fixture and an
assumption.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import text

from storyforge.api.stories import RATE_LIMIT_PER_MINUTE, SERVABLE_STATUSES, limiter
from storyforge.db import SessionLocal
from storyforge.llm.questions import SoundTarget, TapWordDraft, check_tap_word, cue_conflict
from storyforge.main import app

DRAGONS = "dragons"

#: §9 caps `POST /api/stories/request` at stage 12 (`StoryRequest.stage_id`).
MAX_STAGE = 12

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
    """Themes and stories created by one test and deleted when it ends.

    The trick that makes this deterministic without owning the whole database is
    `_SERVE_SQL`'s first sort key, `stage_id DESC` under `stage_id <= :stage`: a
    story alone at the TOP of the ladder is the winner of every request at or
    above its stage, whatever else the pool holds. `base` is therefore the
    lowest stage with no servable story anywhere — the one place a fixture is
    guaranteed to be alone — and `base + 1` is guaranteed empty, which is what
    the lower-stage rungs of §7's ladder need in order to be provoked at all.

    The live pool is read ONCE, to find that gap. Nothing here asserts about it.
    """

    def __init__(self, session) -> None:
        self.session = session
        self.base = 0
        self.themes: dict[str, int] = {}
        self.stories: list[int] = []

    async def start(self) -> Pool:
        top = (
            await self.session.execute(
                text("SELECT coalesce(max(stage_id), 0) FROM stories WHERE status = ANY(:s)"),
                {"s": list(SERVABLE_STATUSES)},
            )
        ).scalar_one()
        self.base = int(top) + 1
        assert self.base + 1 <= MAX_STAGE, (
            f"no free stage band above {top}: the ladder's lower rungs cannot be "
            "provoked without a stage that holds nothing"
        )
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
    ) -> int:
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
        for idx, body in enumerate(FIXTURE_SEGMENTS):
            await self.session.execute(
                text(
                    "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
                    "VALUES (:s, :i, :b, ARRAY[]::text[])"
                ),
                {"s": story_id, "i": idx, "b": body},
            )
        await self.session.commit()
        self.stories.append(story_id)
        return story_id

    async def stop(self) -> None:
        await self.session.rollback()
        if self.stories:
            # `story_segments`, `questions`, `readings` all cascade off `stories`.
            await self.session.execute(
                text("DELETE FROM stories WHERE id = ANY(:ids)"), {"ids": self.stories}
            )
        if self.themes:
            await self.session.execute(
                text("DELETE FROM themes WHERE id = ANY(:ids)"),
                {"ids": list(self.themes.values())},
            )
        await self.session.commit()


@pytest.fixture
async def pool(session):
    fixture = await Pool(session).start()
    try:
        yield fixture
    finally:
        await fixture.stop()


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


async def test_a_real_node_produced_question_never_ships_its_answer_key(client, session, pool):
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
    """
    story_id = await pool.story(stage=pool.base, theme="alpha", title="Node Written Questions")
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


async def test_rejected_stories_are_never_served(client, session):
    """Child-safety invariant: rejected failed the gate or moderation (§7)."""
    rejected = {
        r[0]
        for r in await session.execute(text("SELECT id FROM stories WHERE status = 'rejected'"))
    }
    assert rejected, "no rejected stories in the pool — this test would be vacuous"

    for stage in (1, 2, 3):
        for _ in range(3):
            body = await _request(client, stage)
            assert body["id"] not in rejected
            assert body["status"] != "rejected"
            limiter.clear()

    # Nor by id: a rejected story is an eval artifact, not content.
    assert (await client.get(f"/api/stories/{min(rejected)}")).status_code == 404


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
