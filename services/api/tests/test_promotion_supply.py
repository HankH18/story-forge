"""Six of the eleven stages were terminal dead ends, and every gate was green.

The defect these tests exist for (DEVIATIONS 11.70). `MIN_GPC_OBSERVATIONS = 10`
is a floor over the GPCs a stage *introduces* (5.5), and 11.67 justified leaving
it at 10 by promising it would be "reachable in the five stories §8 asks for" —
a promise argued from a MEAN, 1.95 observations per story. A mean cannot see an
exhausted pool. Measured per stage over the live pool, the best five stories at
stage 9 supply **5** observations and the whole stage supplies **6**: a child who
reads every story stage 9 holds, perfectly, is never promoted, because a re-read
yields no further evidence by design (`_ALREADY_READ_SQL`). Six stages were in
that state. `make warm-check` was green throughout, and correctly so — it counts
stories a child can be SERVED, which is a different floor.

Two propositions, tested separately, because the second is what makes the first
worth anything:

1. **The arithmetic is the runtime's.** A gate that computed evidence its own way
   would be a green light that lies. `test_the_gate_predicts_*` build a pool the
   fixture owns, then drive the REAL HTTP routes (`POST /api/readings`,
   `/answers`, `/complete`) with a fresh kid answering everything correctly, and
   assert the gate's verdict against what the product actually did — promoted,
   or stuck forever with a perfect score.
2. **The gate refuses, by exit code, and says what a terminal stage costs a
   child** (`test_the_gate_exits_*`), and it cannot pass vacuously on an unseeded
   ladder — a check over zero stages finds zero gaps (8.44, one query later).

Nothing here calls Anthropic. The fixture's `tap_word` rows are composed by the
product's own pure functions — `sound_targets` → `tap_word_options` →
`check_tap_word`, the unchanged judge — so "a story worth one observation" is a
row the enrichment node would really persist, not a strawman typed by hand.

SABOTAGE CHECKED four ways, and the record below is what actually printed rather
than what was expected to.

**The whole fix reverted** (`git stash` over `mastery.py` + `warm.py`): all 12
tests fail at collection. That is the weakest of the four — it only says the code
exists — so the three semantic ones are the ones that matter.

**`stage_evidence` counts EVERY aligned GPC** instead of the ones the stage
introduces, which is how this gate would most plausibly be written wrong (it
passes the pure tests and every CLI test):

    test_the_gate_predicts_a_terminal_stage_end_to_end       FAILED  (3, 3, …) != (1, 1, …)
    test_the_gate_predicts_a_promotable_stage_end_to_end     FAILED  (3, …)   != (2, …)
    test_the_gate_separates_needing_more_stories_from_impossible  FAILED  15 != 5
    test_only_the_sounds_the_stage_introduces_are_counted    FAILED  3 != 0
    test_a_story_whose_questions_carry_no_evidence_…         FAILED  (3, 0)   != (1, 0)

Stage 2 becomes worth 27 observations and the gate calls it promotable — while
the kid the same test just watched read all nine stories perfectly is still at
stage 2.

**`reachable_observations` sums the FIRST five instead of the best five:**

    test_the_reachability_window_is_the_best_five_…          FAILED  5 != 21

Exactly one test catches that, and it is the pure one, because `stage_supply`
hands the gate an already-descending tuple. Worth stating rather than glossing:
the sort matters to `promotion_gaps` and `stories_needed` over any other caller's
list, and the FIRST version of that test used data whose first five and best five
both summed to 15 — it passed with the sort deleted, which is a test agreeing
with the defect it exists to forbid.

**The top-stage exemption removed** (a gate that cannot pass is not a guard):

    test_the_top_stage_is_never_a_gap                        FAILED  11 in [11]
    test_the_gate_exits_non_zero_…                           FAILED  3 stages, not 2
    test_the_gate_exits_zero_on_a_pool_that_can_supply_…     FAILED  SystemExit: stage 11
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from sqlalchemy import text

from storyforge import warm
from storyforge.decoder import MAX_STAGE, MIN_STAGE, load_gpcs
from storyforge.llm.questions import (
    TapWordDraft,
    check_tap_word,
    sound_targets,
    tap_word_options,
)
from storyforge.main import app
from storyforge.mastery import (
    MIN_GPC_OBSERVATIONS,
    STORIES_REQUIRED_AT_STAGE,
    promotion_is_reachable,
    reachable_observations,
    stage_evidence,
    stories_needed,
    word_gpc_ids,
)
from storyforge.pipeline import load_vocabulary
from tests.owned_pool import hide_servable_pool, owned_transaction

#: The fixture stage. Interior: it needs a stage that introduces sounds (so a
#: story can be worth something) and one above it to be promoted to.
STAGE = 2
assert MIN_STAGE < STAGE < MAX_STAGE

#: Stage-2 decodable text, measured at 1.000 with no story words. It carries two
#: sounds stage 2 introduces, and they are worth different amounts, which is the
#: whole fixture: `sh` credits ONE stage-2 correspondence in `ship`, `w` credits
#: TWO in `wish` (`w|W` and `sh|SH`). One is the live pool's actual shape at
#: stages 6-9; the other clears the floor in exactly five stories.
FIXTURE_SEGMENTS = (
    "Sam had a ship.",
    "The ship did sit on a rock.",
    "A fish did flip in the sun.",
    "Sam did wish for a chip.",
)

THIN_SOUND = "sh"  # -> "ship", 1 observation
RICH_SOUND = "w"  # -> "wish", 2 observations

#: Pinned against the inventory itself rather than trusted, so a renumbered or
#: re-cut `gpcs.csv` fails HERE, naming the fixture, instead of silently turning
#: every pool below into a pool worth nothing (12.5's lesson).
_INTRODUCED = frozenset(g.id for g in load_gpcs() if g.stage == STAGE)
assert [i for i in word_gpc_ids("ship", STAGE) if i in _INTRODUCED] == ["sh|SH"]
assert [i for i in word_gpc_ids("wish", STAGE) if i in _INTRODUCED] == ["w|W", "sh|SH"]

#: The live shape this reproduces: nine stories, one observation each, floor ten.
TERMINAL_STORIES = 9
#: 5 x 2 = 10 = the floor, in exactly the five stories §8 asks for.
PROMOTABLE_STORIES = STORIES_REQUIRED_AT_STAGE


class Supply:
    """A pool the fixture owns entirely, at one stage, with known evidence.

    `hide_servable_pool` first, for the reason `tests/owned_pool.py` gives at
    length: without it these tests would be measuring the operator's database,
    and the numbers below would mean whatever `make warm` last did.
    """

    def __init__(self, session) -> None:
        self.session = session
        self.theme_id = 0
        self.hidden = 0
        self.stories: list[int] = []

    async def start(self) -> Supply:
        self.hidden = await hide_servable_pool(self.session)
        self.theme_id = (
            await self.session.execute(
                text(
                    "INSERT INTO themes (slug, display_name, emoji) "
                    "VALUES ('zz-supply', 'Supply Fixture', '🧪') RETURNING id"
                )
            )
        ).scalar_one()
        vocab = await load_vocabulary(self.session, STAGE, "zz-supply")
        self.targets = sound_targets(
            story_text=" ".join(FIXTURE_SEGMENTS),
            stage_id=STAGE,
            allowed_words=vocab.allowed_words,
            sight_words=vocab.sight_words,
        )
        self.sight_words = vocab.sight_words
        return self

    def question(self, grapheme: str, seed: int):
        """The row the enrichment node would write for this sound. Its judge."""
        target = next((t for t in self.targets if t.grapheme == grapheme), None)
        assert target is not None, (
            f"{grapheme} is not one of the sounds this story can ask about: "
            f"{[t.grapheme for t in self.targets]}"
        )
        options = tap_word_options(target)
        assert options is not None, f"no legal option set for {grapheme}"
        question, problems = check_tap_word(
            TapWordDraft(
                target_grapheme=grapheme,
                correct_word=options[0],
                other_words=list(options[1:]),
            ),
            targets=self.targets,
            stage_id=STAGE,
            story_text=" ".join(FIXTURE_SEGMENTS),
            sight_words=self.sight_words,
            seed=seed,
        )
        assert question is not None, problems
        return question

    async def story(self, grapheme: str) -> tuple[int, int, str]:
        """One servable story at STAGE. Returns (story, question, credited word)."""
        story_id = (
            await self.session.execute(
                text(
                    "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                    "story_words, gen_meta) VALUES (:st, :th, 'warm', :title, 1.0, "
                    "ARRAY[]::text[], '{}'::jsonb) RETURNING id"
                ),
                {"st": STAGE, "th": self.theme_id, "title": f"Supply {len(self.stories)}"},
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
        question = self.question(grapheme, seed=story_id)
        question_id = (
            await self.session.execute(
                text(
                    "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                    "VALUES (:s, 0, :t, :p, CAST(:pl AS jsonb), CAST(:a AS jsonb)) RETURNING id"
                ),
                {
                    "s": story_id,
                    "t": question.qtype,
                    "p": question.prompt,
                    "pl": json.dumps(question.payload),
                    "a": json.dumps(question.answer),
                },
            )
        ).scalar_one()
        # Releases the fixture's SAVEPOINT only — the enclosing transaction is
        # rolled back, so nothing here is visible to anyone else, ever.
        await self.session.commit()
        self.stories.append(story_id)
        return story_id, question_id, question.answer["correct"]

    async def at_stage(self) -> warm.StageSupply:
        """What the gate says about STAGE, over the pool this fixture owns."""
        rows = await warm.stage_supply(self.session)
        return next(r for r in rows if r.stage_id == STAGE)


@asynccontextmanager
async def _supply() -> AsyncIterator[tuple[Supply, httpx.AsyncClient]]:
    async with owned_transaction() as session:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield await Supply(session).start(), client


@pytest.fixture
async def supply() -> AsyncIterator[tuple[Supply, httpx.AsyncClient]]:
    async with _supply() as pair:
        yield pair


async def _read_perfectly(client, kid_id: int, story_id: int, question_id: int, word: str) -> dict:
    """One whole kid session through the real routes, answered correctly."""
    started = await client.post("/api/readings", json={"kid_id": kid_id, "story_id": story_id})
    assert started.status_code == 201, started.text
    reading_id = started.json()["id"]

    answered = await client.post(
        f"/api/readings/{reading_id}/answers",
        json={"question_id": question_id, "response": word},
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["correct"] is True, "the fixture answered its own question wrong"

    done = await client.post(f"/api/readings/{reading_id}/complete")
    assert done.status_code == 200, done.text
    return done.json()


async def _kid_at_stage(client) -> int:
    created = await client.post(
        "/api/kids", json={"display_name": "Supply", "stage_id": STAGE}
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


# --- proposition 1: the gate's arithmetic is the runtime's -------------------
async def test_the_gate_predicts_a_terminal_stage_end_to_end(supply):
    """THE FINDING, reproduced through the HTTP routes a child uses.

    Nine stories, one observation each, floor ten — the live shape of stages 6
    and 7. The kid reads every one of them, answers every question correctly,
    and is never promoted; §8's story clause is satisfied nearly twice over and
    the evidence clause can never be. The only thing in the system that can say
    so is the gate, and it must say so before any child is in that state.
    """
    fixture, client = supply
    stories = [await fixture.story(THIN_SOUND) for _ in range(TERMINAL_STORIES)]
    kid_id = await _kid_at_stage(client)

    for story_id, question_id, word in stories:
        body = await _read_perfectly(client, kid_id, story_id, question_id, word)

    promotion = body["promotion"]
    assert body["promoted"] is False
    assert promotion["stories_completed"] == TERMINAL_STORIES > STORIES_REQUIRED_AT_STAGE
    assert promotion["accuracy"] == 1.0, "a perfect reader, refused"
    assert promotion["gpc_seen"] == TERMINAL_STORIES < MIN_GPC_OBSERVATIONS
    assert [c["name"] for c in promotion["criteria"] if not c["met"]] == ["evidence"]

    # And a tenth reading cannot exist: there is no tenth story, and re-reading
    # one of the nine adds nothing (DEVIATIONS 5.5). The stage is a dead end.
    again = await _read_perfectly(client, kid_id, *stories[0][:2], stories[0][2])
    assert again["repeat_reading"] is True
    assert again["gpc_updates"] == []
    assert again["promotion"]["gpc_seen"] == TERMINAL_STORIES

    row = await fixture.at_stage()
    assert row.stories == TERMINAL_STORIES
    assert row.per_story == (1,) * TERMINAL_STORIES
    assert row.total == TERMINAL_STORIES == promotion["gpc_seen"], (
        "the gate's ceiling for the whole stage must equal what a perfect reader "
        "actually earned from every story in it"
    )
    assert row.terminal is True
    assert row in warm.promotion_gaps(await warm.stage_supply(fixture.session))


async def test_the_gate_predicts_a_promotable_stage_end_to_end(supply):
    """The other direction, and it is not decoration: a gate that cannot pass is
    a broken build, not a guard.

    Five stories worth two observations each is exactly the floor in exactly the
    five stories §8 asks for — the boundary, deliberately, because a gate that
    only agreed with the runtime far from the threshold would agree about
    nothing that matters.
    """
    fixture, client = supply
    stories = [await fixture.story(RICH_SOUND) for _ in range(PROMOTABLE_STORIES)]

    row = await fixture.at_stage()
    assert row.per_story == (2,) * PROMOTABLE_STORIES
    assert row.reachable == MIN_GPC_OBSERVATIONS
    assert row.terminal is False
    assert warm.promotion_gaps([row]) == []

    kid_id = await _kid_at_stage(client)
    for n, (story_id, question_id, word) in enumerate(stories, start=1):
        body = await _read_perfectly(client, kid_id, story_id, question_id, word)
        if n < PROMOTABLE_STORIES:
            assert body["promoted"] is False, f"promoted after only {n} stories"

    assert body["promoted"] is True
    assert body["promotion"]["gpc_seen"] == MIN_GPC_OBSERVATIONS
    assert body["promotion"]["to_stage"] == STAGE + 1
    stage_now = (
        await fixture.session.execute(
            text("SELECT stage_id FROM kids WHERE id = :k"), {"k": kid_id}
        )
    ).scalar_one()
    assert stage_now == STAGE + 1


async def test_the_gate_separates_needing_more_stories_from_impossible(supply):
    """Ten stories worth one each: the gate is RED and the child IS promoted.

    This is the live shape of stages 2 and 4, and it is why the report prints two
    numbers. §8 asks for five stories; this pool makes a child read ten to clear
    a floor documented as reachable in five (11.67). That is a real finding and a
    fixable one — and it is NOT the dead end above, which no amount of reading
    fixes. A gate that collapsed the two would send an operator to `make warm`
    for a stage more stories cannot help.
    """
    fixture, client = supply
    stories = [await fixture.story(THIN_SOUND) for _ in range(MIN_GPC_OBSERVATIONS)]

    row = await fixture.at_stage()
    assert row.reachable == STORIES_REQUIRED_AT_STAGE < MIN_GPC_OBSERVATIONS
    assert row.terminal is False
    assert stories_needed(row.per_story) == MIN_GPC_OBSERVATIONS
    assert warm.promotion_gaps([row]) == [row], "short of the floor within five stories"

    kid_id = await _kid_at_stage(client)
    for n, (story_id, question_id, word) in enumerate(stories, start=1):
        body = await _read_perfectly(client, kid_id, story_id, question_id, word)
        assert body["promoted"] is (n == stories_needed(row.per_story))


async def test_only_the_sounds_the_stage_introduces_are_counted(supply):
    """A perfectly good question about an OLDER sound is worth zero.

    `sound_targets`' `older_stage_fallback`, which is why stages 9-11 are where
    they are. The counting rule is not "GPCs in the answer"; it is
    `readings._STAGE_EVIDENCE_SQL`'s rule, the ones the stage teaches — and the
    obvious wrong implementation of this gate passes every other test here.
    """
    assert stage_evidence(["sit"], stage_id=STAGE, introduced=_INTRODUCED) == 0
    assert len(word_gpc_ids("sit", STAGE)) == 3, "decodable, and worth nothing"

    fixture, client = supply
    story_id, question_id, word = await fixture.story(THIN_SOUND)
    kid_id = await _kid_at_stage(client)
    body = await _read_perfectly(client, kid_id, story_id, question_id, word)

    # The runtime agrees: three correspondences moved, one of them stage 2's.
    assert body["promotion"]["gpc_seen"] == 1
    assert len(body["gpc_updates"]) == len(word_gpc_ids(word, STAGE)) == 3


async def test_a_story_whose_questions_carry_no_evidence_still_counts_as_a_story(supply):
    """"9 stories, 2 observations" is the sentence an operator needs.

    An `mcq2`-only story is servable (`ANSWERABLE_SQL`) and a child can read it,
    so the gate counts it — worth nothing. An inner join to `questions` on
    `MASTERY_QTYPES` would have dropped the row entirely and reported a smaller,
    healthier-looking stage.
    """
    fixture, _ = supply
    await fixture.story(THIN_SOUND)
    story_id = (
        await fixture.session.execute(
            text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (:st, :th, 'warm', 'Comprehension only', "
                "1.0, ARRAY[]::text[], '{}'::jsonb) RETURNING id"
            ),
            {"st": STAGE, "th": fixture.theme_id},
        )
    ).scalar_one()
    await fixture.session.execute(
        text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'mcq2', 'Did Sam get a ship?', CAST(:p AS jsonb), "
            "CAST(:a AS jsonb))"
        ),
        {
            "s": story_id,
            "p": '{"options": ["Sam had a ship.", "Sam had a rock."]}',
            "a": '{"correct": "Sam had a ship.", "index": 0}',
        },
    )
    await fixture.session.commit()

    row = await fixture.at_stage()
    assert row.stories == 2
    assert row.per_story == (1, 0)


async def test_an_answer_key_the_runtime_would_refuse_contributes_nothing(supply):
    """The gate must not report evidence `complete_reading` would never credit.

    A key holding a phrase is refused there by `mastery.single_word`, so a story
    carrying one is worth zero here — measured by the same function rather than
    by a second opinion about what a word is.
    """
    fixture, _ = supply
    story_id, question_id, _ = await fixture.story(THIN_SOUND)
    await fixture.session.execute(
        text("UPDATE questions SET answer = CAST(:a AS jsonb) WHERE id = :q"),
        {"a": '{"correct": "the ship", "index": 0}', "q": question_id},
    )
    await fixture.session.commit()

    row = await fixture.at_stage()
    assert row.per_story == (0,)


# --- proposition 2: the gate refuses, and says what it costs ------------------
def _ladder(**observations: tuple[int, ...]) -> list[warm.StageSupply]:
    """A full ladder, promotable everywhere, minus whatever the caller starves."""
    rich = (2,) * STORIES_REQUIRED_AT_STAGE
    return [
        warm.StageSupply(
            stage_id=stage,
            per_story=observations.get(str(stage), () if stage == MAX_STAGE else rich),
        )
        for stage in range(MIN_STAGE, MAX_STAGE + 1)
    ]


def test_the_top_stage_is_never_a_gap():
    """It introduces no correspondences and there is nothing above it, so its
    supply is always zero. A gate that reported it could never pass."""
    assert MAX_STAGE not in [g.stage_id for g in warm.promotion_gaps(_ladder())]
    assert promotion_is_reachable(stage_id=MAX_STAGE, per_story=())
    assert not promotion_is_reachable(stage_id=MAX_STAGE - 1, per_story=())


def test_the_reachability_window_is_the_best_five_stories_section_8_asks_for():
    """Not the first five, not five times the mean — the mean is the statistic
    that hid this defect for a whole phase (11.67).

    The two lists below are ORDER-SENSITIVE on purpose. The first version of this
    test used `[1, 1, 2, 10, 1, 1, 1]`, whose first five and best five both sum
    to 15, so it passed with the sort deleted — a test that agreed with the
    defect it was written to forbid.
    """
    assert reachable_observations([1, 1, 1, 1, 1, 9, 9]) == 9 + 9 + 1 + 1 + 1
    assert reachable_observations([]) == 0
    # Twenty stories worth one each is not "20 observations available": §8 asks
    # for five stories, and five of these carry five.
    assert reachable_observations([1] * 20) == STORIES_REQUIRED_AT_STAGE
    assert stories_needed([1] * 20) == MIN_GPC_OBSERVATIONS
    assert stories_needed([1, 1, 1, 1, 1, MIN_GPC_OBSERVATIONS]) == 1
    # And the difference the report exists to print.
    assert stories_needed([1] * 9) is None
    assert stories_needed([2] * 5) == 5


def test_the_gate_exits_non_zero_and_says_what_a_terminal_stage_costs_a_child(
    monkeypatch, capsys
):
    """The gate's value is that sentence as much as the exit code: nothing in the
    product can say it, because the dashboard's shortfall line is identical
    whether the child needs one more story or whether no story exists."""

    async def starved(session):
        return _ladder(**{"3": (1,) * 7, "6": (1,) * STORIES_REQUIRED_AT_STAGE})

    monkeypatch.setattr(warm, "stage_supply", starved)
    with pytest.raises(SystemExit) as exc:
        await_sync(warm.run_promotability_check())

    message = str(exc.value)
    assert "2 stage(s) cannot supply" in message
    assert "stage(s) [3, 6]" in message and "ANY number of readings" in message
    assert "never promoted" in message
    assert "--check-promotability" in message
    # The remedy names its own limit, or it sends an operator to spend money on
    # stories that cannot help (11.73).
    assert "older_stage_fallback" in message

    out = capsys.readouterr().out
    assert "TERMINAL" in out
    assert "8 of 10 stage(s) can promote a reader" in out
    assert f"stage 3: 5 of {MIN_GPC_OBSERVATIONS} observations" in out
    assert "top of the ladder" in out


def test_the_gate_exits_zero_on_a_pool_that_can_supply_the_floor(monkeypatch, capsys):
    async def full(session):
        return _ladder()

    monkeypatch.setattr(warm, "stage_supply", full)
    await_sync(warm.run_promotability_check())
    assert "OK — every stage below" in capsys.readouterr().out


def test_the_gate_refuses_to_pass_vacuously_on_an_unseeded_ladder():
    """A supply check over zero stages finds zero gaps and exits 0 — on a fresh
    deploy, which is the exact scenario this gate is for (cf. 8.44)."""
    assert warm.promotion_gaps([]) == []  # which is why the assert below exists

    with pytest.raises(SystemExit) as exc:
        warm.assert_ladder_seeded([])
    assert "not seeded" in str(exc.value) and "make seed" in str(exc.value)

    with pytest.raises(SystemExit):
        warm.assert_ladder_seeded([r for r in _ladder() if r.stage_id != MAX_STAGE])
    warm.assert_ladder_seeded(_ladder())


def test_the_two_gates_measure_different_floors():
    """§7's gate counts stories a child can be SERVED; §8's counts the evidence
    those stories carry. Three servable stories per pair satisfies the first
    completely while supplying two of the ten observations the second needs — the
    state the live pool was in, with `warm-check` green (11.70)."""
    served = warm.PairDepth(stage_id=STAGE, theme_slug="dragons", servable=3)
    assert warm.coverage_gaps([served], warm.TARGET_PER_PAIR) == []

    supplied = warm.StageSupply(stage_id=STAGE, per_story=(1, 1))
    assert warm.promotion_gaps([supplied]) == [supplied]
    assert supplied.terminal is True


def await_sync(coro):
    """`run_promotability_check` owns no event loop; these three tests are sync."""
    import asyncio

    return asyncio.run(coro)
