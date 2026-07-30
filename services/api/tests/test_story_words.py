"""§5.5's decodability band on a story word, and the sixth gate (DEVIATIONS 14.20).

The defect: a story word is a word the child is GIVEN — a picture chip with
tap-to-hear, excluded from the decodability denominator — instead of decoding, and
there are exactly `MAX_STORY_WORDS` = 3 slots per story. Nothing bounded which
words could fill one. `story_word_bank.min_stage` was the only filter, and it is a
hand-set SEMANTIC floor ("is this concept age-appropriate here") with no relation
to whether the reader can already read the word.

Measured over the live pool before the fix, 326 chip slots on 119 servable stories:

    distance = min_decodable_stage(word) - story.stage_id

    d <= 0 (reader can already decode it)   225   69.0%
    d in 1..3 (the band)                     48   14.7%
    d in 4..7                                23    7.1%
    never decodable in this inventory        30    9.2%

`dragon` is decodable from stage 1, so every dragons story on the ladder carried a
picture chip over a word its reader could sound out — while `castle`, which no
stage of this 93-GPC inventory can ever align, is the word the chip was invented
for. Seven of the twenty-four bank entries (comet, dragon, forest, orbit, planet,
rabbit, rocket) are decodable from stage 1 and are not story words at all under any
reading of §5.5.

Two halves, tested as two propositions:

1. the BAND is a pure function of (word, stage) over the frozen inventory, and
   `load_vocabulary` applies it so a story generated from now on cannot carry an
   out-of-band chip (`test_the_band_*`, `test_the_offer_*`);
2. the GATE reads what is already in the pool and refuses (`test_the_gate_*`);
3. the free half of the backlog is REPAIRABLE without an API call
   (`test_the_repair_*`, DEVIATIONS 14.40).

The repair's first draft said there was no free repair at all. That was right
about a word ABOVE the reader's stage — the word stays in the text either way, so
removing the picture leaves a word the child cannot read and now has no way into —
and wrong about the 225 that dominate the finding, where dropping the chip leaves
a word the child can sound out, which is the product's entire purpose. Measured:
182 of the 225 are free, and the other 43 are held back by a SECOND condition
(`test_the_repair_leaves_*`) that the first analysis missed.

Nothing here calls Anthropic.
"""

from __future__ import annotations

import pytest

from storyforge import warm
from storyforge.api.stories import ANSWERABLE_SQL
from storyforge.db import SessionLocal
from storyforge.decoder import MAX_STAGE, MIN_STAGE
from storyforge.pipeline import (
    DECODABILITY_GATE,
    MAX_STORY_WORDS,
    STORY_WORD_MAX_LOOKAHEAD,
    decodable_from_stage,
    load_vocabulary,
    story_word_is_eligible,
    story_word_lookahead,
    story_word_refusal,
)

#: Measured with the decoder, not asserted from memory — `test_the_measured_bank_*`
#: below re-derives every one of these and fails if the inventory moves.
NEVER_DECODABLE = ("alien", "astronaut", "burrow", "castle", "feathers", "penguin",
                   "scales", "treasure")
DECODABLE_AT_STAGE_1 = ("comet", "dragon", "forest", "orbit", "planet", "rabbit", "rocket")


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


# --- the band, as a pure function --------------------------------------------
def test_the_measured_bank_distances_are_what_the_decoder_says():
    """The numbers this whole finding rests on, re-derived rather than quoted.

    If the GPC inventory ever changes, these move — and they should turn this file
    red rather than leave a docstring quietly describing a different product.
    """
    for word in NEVER_DECODABLE:
        assert decodable_from_stage(word) is None, word
    for word in DECODABLE_AT_STAGE_1:
        assert decodable_from_stage(word) == 1, word
    assert decodable_from_stage("wings") == 3
    assert decodable_from_stage("cave") == decodable_from_stage("fire") == 4
    assert decodable_from_stage("stars") == 6
    assert decodable_from_stage("moon") == 7
    assert decodable_from_stage("giraffe") == decodable_from_stage("knight") == 8


def test_a_word_the_reader_can_already_decode_is_refused_at_every_stage():
    """The floor, and 225 of the 326 live chips. `dragon` is the whole finding in
    one word: the theme's own name, decodable from stage 1, chipped anyway."""
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        assert story_word_is_eligible("dragon", stage) is False, stage
        reason = story_word_refusal("dragon", stage)
        assert reason is not None and "already decodable" in reason
        # The refusal has to name BOTH costs, because they are different costs.
        assert "slots" in reason and "denominator" in reason


def test_a_word_this_inventory_can_never_align_is_eligible_at_every_stage():
    """The ceiling's deliberate exception, and the reason it is not arbitrary.

    No amount of teaching replaces the chip for `castle` or `penguin` — the
    inventory has no path for a silent `t` or for `su` -> /ZH/ at any stage — so
    the chip is the only route in and is permanently correct. Without this, the
    top of the ladder would have no story words at all: stages 10 and 11 teach no
    new GPCs, so every alignable word is already decodable there.
    """
    for word in NEVER_DECODABLE:
        for stage in range(MIN_STAGE, MAX_STAGE + 1):
            assert story_word_is_eligible(word, stage) is True, (word, stage)
            assert story_word_refusal(word, stage) is None
            assert story_word_lookahead(word, stage) is None


def test_a_word_further_ahead_than_the_lookahead_is_refused():
    """The ceiling. `giraffe` becomes decodable at stage 8; offered to a stage-1
    reader the chip is seven stages of whole-word memorisation for a word the
    curriculum teaches properly, and it spends a slot `penguin` needed."""
    assert story_word_lookahead("giraffe", 1) == 7
    assert story_word_is_eligible("giraffe", 1) is False
    reason = story_word_refusal("giraffe", 1)
    assert "7 stages ahead" in reason and f"ceiling {STORY_WORD_MAX_LOOKAHEAD}" in reason
    # ...and it becomes legitimate exactly when it comes into range.
    assert story_word_is_eligible("giraffe", 4) is False  # 4 ahead
    assert story_word_is_eligible("giraffe", 5) is True  # 3 ahead


def test_the_band_boundary_is_exact_on_both_sides():
    """Every alignable bank word, at every stage — the band and nothing else.

    A rule stated as an inequality is worth exactly as much as its two edges, and
    both edges here are one-off-able.
    """
    for word in ("wings", "cave", "fire", "whiskers", "stars", "moon", "giraffe",
                 "elephant", "knight", *DECODABLE_AT_STAGE_1):
        first = decodable_from_stage(word)
        assert first is not None, word
        expected = {
            s
            for s in range(MIN_STAGE, MAX_STAGE + 1)
            if 1 <= first - s <= STORY_WORD_MAX_LOOKAHEAD
        }
        actual = {
            s for s in range(MIN_STAGE, MAX_STAGE + 1) if story_word_is_eligible(word, s)
        }
        assert actual == expected, word
        # The word is never eligible at the stage that teaches it, or after.
        assert first not in actual and first - 1 in actual or first == 1


def test_the_lookahead_is_a_number_and_not_a_synonym_for_no_bound():
    """A ceiling that admits everything is documentation. This one refuses 23 of
    the pool's 326 chips on its own, before the floor refuses anything."""
    assert 1 <= STORY_WORD_MAX_LOOKAHEAD < MAX_STAGE
    assert story_word_is_eligible("moon", 1) is False  # 6 ahead
    assert story_word_is_eligible("stars", 1) is False  # 5 ahead


# --- the offer: enforcement at GENERATION time --------------------------------
async def test_the_offer_contains_only_words_inside_the_band(session):
    """`load_vocabulary` is where the model is handed its bank. The band is
    enforced there, so a story generated after this change cannot carry an
    out-of-band chip at all — which is why the gate below has no free repair."""
    themes = [r[0] for r in await session.execute(warm.text("SELECT slug FROM themes"))]
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        for theme in themes:
            vocab = await load_vocabulary(session, stage, theme)
            offside = [
                w for w in vocab.story_words if not story_word_is_eligible(w, stage)
            ]
            assert not offside, f"stage {stage} / {theme} offers {offside}"


async def test_no_stage_and_theme_is_left_with_nothing_to_offer(session):
    """§18: "themes at stages 1-2 lean on story words + pictures for interest."

    A band that empties a cell reproduces AUDIT-PHASE7 F1 — "a dragons story with
    no dragon in it" — by a second route, so this is the counterweight to the two
    tests above and it is the reason `alien`/`astronaut` moved to `min_stage` 1:
    both are permanently unalignable, which makes them the two best story words
    space has, and `min_stage` 2 was withholding them from stage 1, leaving that
    cell EMPTY.
    """
    themes = [r[0] for r in await session.execute(warm.text("SELECT slug FROM themes"))]
    empty = []
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        for theme in themes:
            vocab = await load_vocabulary(session, stage, theme)
            if not vocab.story_words:
                empty.append((stage, theme))
    assert not empty, f"(stage, theme) cells with no story word at all: {empty}"


async def test_the_offer_is_never_larger_than_the_slots_are_scarce(session):
    """Not a bound on the offer — a reminder of what the offer is spent on.

    The cap is on the STORY (`MAX_STORY_WORDS`), not on the bank, so an offer may
    be wider than three. What must hold is that a child is never shown more chips
    than §4 permits, and that is `passes_gate`'s job; this asserts the two numbers
    are still the same product.
    """
    assert MAX_STORY_WORDS == 3
    vocab = await load_vocabulary(session, 2, "dragons")
    assert len(vocab.story_words) >= 1


# --- the gate: what is already in the pool ------------------------------------
def _offence(story_id, stage, theme, word, lookahead):
    return warm.ChipOffence(
        story_id=story_id,
        stage_id=stage,
        theme_slug=theme,
        word=word,
        reason=story_word_refusal(word, stage) or "",
        lookahead=lookahead,
    )


def test_the_gate_names_every_chip_and_which_kind_of_waste_it_is(monkeypatch, capsys):
    """8.43's rule: a gap is named, never counted. And the two halves are
    separated, because they have opposite remedies — one slot is spent teaching
    nothing, the other is spent teaching something too early."""

    async def offences(session):
        return (
            [
                _offence(2, 1, "dragons", "dragon", -0),
                _offence(21, 1, "space", "moon", 6),
            ],
            326,
        )

    async def dead(session):
        return [("dragons", "dragon")]

    monkeypatch.setattr(warm, "chip_offences", offences)
    monkeypatch.setattr(warm, "dead_bank_entries", dead)
    with pytest.raises(SystemExit) as exc:
        warm.asyncio.run(warm.run_story_word_check())

    message = str(exc.value)
    assert "2 of 326 picture-chip slot(s)" in message
    assert "1 spent on a word the child could already read" in message
    assert "1 on a word the curriculum will teach properly first" in message
    # BOTH remedies, named, and the reason they are different remedies.
    #
    # This assertion read `"NO free repair" in message` for one commit, and it was
    # pinning a FALSE claim: the hint generalised from the too-far chips (where the
    # word stays unreadable, so there really is no free fix) to the 225 that
    # dominate the finding, where the child can already read the word. Replaced,
    # not relaxed — it now requires the message to name the free repair by its flag
    # AND to keep the argument for why the rest is not free (DEVIATIONS 14.40).
    assert "--story-word-repair" in message
    assert "strictly worse" in message
    assert "load-bearing" in message

    out = capsys.readouterr().out
    assert "story 2 stage 1 / dragons: 'dragon'" in out
    assert "story 21 stage 1 / space: 'moon'" in out
    # The inert bank rows are reported and are deliberately NOT the exit code.
    assert "dragons/dragon" in out and "operator's call" in out


def test_the_gate_exits_zero_on_a_pool_inside_the_band(monkeypatch, capsys):
    """A gate that cannot pass is a broken build, not a guard."""

    async def clean(session):
        return [], 42

    async def none(session):
        return []

    monkeypatch.setattr(warm, "chip_offences", clean)
    monkeypatch.setattr(warm, "dead_bank_entries", none)
    warm.asyncio.run(warm.run_story_word_check())
    assert "OK — all 42 chip(s)" in capsys.readouterr().out


def test_the_gate_counts_only_chips_a_child_can_actually_be_shown():
    """Same `SERVABLE_STATUSES` + `ANSWERABLE_SQL` predicate as every other gate,
    imported and never re-spelled (DEVIATIONS 8.42/8.51): a chip on a story the
    serving path refuses is not a chip any child is shown, and counting one would
    make this gate and `_SERVE_SQL` disagree about the same pool."""
    sql = str(warm._CHIPS_SQL)
    assert ":statuses" in sql
    assert ANSWERABLE_SQL.format(story="s") in sql


@pytest.fixture
async def owned_story(session):
    """One servable stage-1 dragons story carrying a KNOWN pair of chips.

    The gate reads the live pool, so a test that only asserts over whatever the
    pool happens to hold passes vacuously the moment the pool is clean — which is
    exactly what a sabotage run caught here: `chip_offences` was made to report
    nothing and every assertion in this file still passed. This row is the
    positive control. It is inserted, asserted over, and deleted; the two chips
    are one of each kind, so the gate must find one and must NOT find the other.
    """
    theme_id = (
        await session.execute(warm.text("SELECT id FROM themes WHERE slug = 'dragons'"))
    ).scalar_one()
    story_id = (
        await session.execute(
            warm.text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (1, :th, 'warm', 'Story Word Fixture', "
                "1.0, CAST(:words AS text[]), '{}'::jsonb) RETURNING id"
            ),
            # `dragon` is decodable from stage 1 — an offence. `castle` can never
            # be aligned — permanently legitimate.
            {"th": theme_id, "words": ["dragon", "castle"]},
        )
    ).scalar_one()
    await session.execute(
        warm.text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', 'Tap: sit.', CAST(:p AS jsonb), CAST(:a AS jsonb))"
        ),
        {
            "s": story_id,
            "p": '{"options": ["sat", "did", "nap"], "cue_word": "sit"}',
            "a": '{"correct": "sat", "index": 0, "target_grapheme": "s"}',
        },
    )
    await session.commit()
    try:
        yield story_id
    finally:
        await session.rollback()
        await session.execute(
            warm.text("DELETE FROM stories WHERE id = :id"), {"id": story_id}
        )
        await session.commit()


async def test_the_gate_finds_a_known_bad_chip_and_leaves_a_known_good_one(
    owned_story, session
):
    """The positive control the rest of this file needs to not be vacuous."""
    offences, total = await warm.chip_offences(session)
    mine = {o.word: o for o in offences if o.story_id == owned_story}
    assert set(mine) == {"dragon"}, "the gate must find `dragon` and only `dragon`"
    assert mine["dragon"].wasted is True
    assert mine["dragon"].stage_id == 1 and mine["dragon"].theme_slug == "dragons"
    assert "already decodable" in mine["dragon"].reason
    # Both chips are counted in the denominator; only one is an offence.
    assert total >= 2


async def test_the_gate_agrees_with_the_offer_it_is_auditing(session):
    """The two halves cannot drift: the gate's judgement IS `story_word_refusal`,
    which is the function `load_vocabulary` filters on. A gate with its own copy
    of the rule is how DEVIATIONS 11.60 shipped a retired prompt to 120 children
    under a green suite.
    """
    offences, total = await warm.chip_offences(session)
    assert total > 0, "no servable story carries a chip — the gate would be vacuous"
    for o in offences:
        assert story_word_is_eligible(o.word, o.stage_id) is False
        assert o.reason == story_word_refusal(o.word, o.stage_id)
        assert o.wasted == (o.lookahead is not None and o.lookahead <= 0)


async def test_the_gate_counts_slots_and_not_occurrences(session):
    """STATE invariant 3. `stories.story_words` is the DISTINCT list the reading
    view renders chips from, so the denominator is chip SLOTS — repeating a word
    is correct decodable-reader behaviour and buys no extra chip."""
    rows = (
        await session.execute(
            warm.text(
                "SELECT s.story_words FROM stories s "
                f"WHERE s.status = ANY(:st) AND {ANSWERABLE_SQL.format(story='s')}"
            ),
            {"st": list(warm.SERVABLE_STATUSES)},
        )
    ).all()
    expected = sum(len(r.story_words or []) for r in rows)
    _offences, total = await warm.chip_offences(session)
    assert total == expected
    for r in rows:
        words = list(r.story_words or [])
        assert len(set(words)) == len(words)
        assert len(words) <= MAX_STORY_WORDS


# --- the free repair (DEVIATIONS 14.40-14.42) ---------------------------------
def test_the_repair_drops_only_what_the_reader_can_already_decode():
    """Condition 1. A chip above the reader's stage is load-bearing and is kept.

    The word stays in the story either way — the repair never edits text — so
    removing the picture from a word the child cannot decode swaps a wasted chip
    for an unreadable word. That is strictly worse, and it is why the 23 too-far
    offences are not repairable at any price short of regeneration.
    """
    permits_everything = frozenset({"cave", "fire", "giraffe", "castle", "moon"})
    # stage 4: cave and fire are decodable AT 4 (lookahead 0); giraffe is 4 ahead;
    # castle is never decodable at all.
    drop, keep = warm.droppable_chips(["cave", "fire", "giraffe", "castle"], 4, permits_everything)
    assert drop == ["cave", "fire"]
    assert keep == ["giraffe", "castle"]
    # A permanently unalignable word is never dropped, at any stage, even though
    # it is "not decodable at the reader's stage" — it is the chip's whole reason.
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        d, k = warm.droppable_chips(["castle"], stage, permits_everything)
        assert (d, k) == ([], ["castle"]), stage


def test_the_repair_leaves_a_decodable_word_the_vocabulary_does_not_permit():
    """Condition 2 — the one the first analysis missed, and it holds back 43 slots.

    `dragon` and `planet` are decodable from stage 1 but appear in NO stage's
    `allowed_words` (§5.4's register/frequency filters drop them). They are in
    these stories only under the story-word exemption, so de-chipping one converts
    a licensed word into a word the stage's own vocabulary does not permit —
    trading a wasted chip for a vocabulary violation, which is a real defect
    rather than a bookkeeping one.
    """
    permits_neither = frozenset({"cave"})
    drop, keep = warm.droppable_chips(["dragon", "cave"], 4, permits_neither)
    assert drop == ["cave"], "the permitted one goes"
    assert keep == ["dragon"], "the unpermitted one stays chipped"
    # Both conditions are required, not either.
    assert warm.droppable_chips(["dragon"], 4, frozenset({"dragon"})) == (["dragon"], [])


async def test_the_repair_matches_the_gate_it_is_repairing(session):
    """Nothing the repair drops may be a chip the gate would have called legal.

    The two read the same `story_word_lookahead`; this is what stops the repair
    quietly widening the band it is supposed to be enforcing.
    """
    allowed, sight = await warm.stage_vocabularies(session)
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        permitted = allowed.get(stage, frozenset()) | sight.get(stage, frozenset())
        bank = [r[0] for r in await session.execute(warm.text("SELECT word FROM story_word_bank"))]
        drop, _keep = warm.droppable_chips(bank, stage, permitted)
        for word in drop:
            assert story_word_refusal(word, stage) is not None, (
                f"{word!r} at stage {stage} is a LEGAL chip and the repair would drop it"
            )


@pytest.fixture
async def repairable_story(session):
    """A stage-4 dragons story with one of each kind of chip.

    `cave`/`fire` are decodable at stage 4 and in `allowed_words` there — free.
    `castle` can never be aligned — kept. The text is untouched by the repair, so
    it must still be a real, gate-clearing story afterwards.
    """
    theme_id = (
        await session.execute(warm.text("SELECT id FROM themes WHERE slug = 'dragons'"))
    ).scalar_one()
    story_id = (
        await session.execute(
            warm.text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (4, :th, 'warm', 'Repair Fixture', "
                "1.0, CAST(:w AS text[]), '{}'::jsonb) RETURNING id"
            ),
            {"th": theme_id, "w": ["cave", "fire", "castle"]},
        )
    ).scalar_one()
    await session.execute(
        warm.text(
            "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
            "VALUES (:s, 0, :b, ARRAY[]::text[])"
        ),
        {"s": story_id, "b": "The cave had fire. The castle is big. A cat sat on it."},
    )
    await session.execute(
        warm.text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', 'Tap: sit.', CAST(:p AS jsonb), CAST(:a AS jsonb))"
        ),
        {
            "s": story_id,
            "p": '{"options": ["sat", "did", "nap"], "cue_word": "sit"}',
            "a": '{"correct": "sat", "index": 0, "target_grapheme": "s"}',
        },
    )
    await session.commit()
    try:
        yield story_id
    finally:
        await session.rollback()
        await session.execute(
            warm.text("DELETE FROM stories WHERE id = :id"), {"id": story_id}
        )
        await session.commit()


async def test_the_repair_drops_the_free_chips_and_keeps_the_load_bearing_one(
    repairable_story, session
):
    """End to end against a real row, including the postcondition read back."""
    repairs, refused = await warm.repair_wasted_chips(
        session, warm.SERVABLE_STATUSES, only=[repairable_story]
    )
    await session.commit()
    assert refused == []
    assert len(repairs) == 1
    r = repairs[0]
    assert sorted(r.dropped) == ["cave", "fire"]
    assert list(r.kept) == ["castle"]
    # Decodability may only move toward 1.0 — the word leaves the exempt set and
    # enters the denominator as `decodable`.
    assert r.after >= r.before

    row = (
        await session.execute(
            warm.text("SELECT story_words, decodability FROM stories WHERE id = :i"),
            {"i": repairable_story},
        )
    ).first()
    assert list(row.story_words) == ["castle"]
    assert float(row.decodability) >= DECODABILITY_GATE

    # The text is NEVER edited — that is what makes this free and reversible.
    body = (
        await session.execute(
            warm.text("SELECT body FROM story_segments WHERE story_id = :i"),
            {"i": repairable_story},
        )
    ).scalar_one()
    assert "cave" in body and "fire" in body and "castle" in body

    # And the question is untouched, so no §8 evidence can be cascaded away.
    assert (
        await session.execute(
            warm.text("SELECT count(*) FROM questions WHERE story_id = :i"),
            {"i": repairable_story},
        )
    ).scalar_one() == 1


async def test_the_repair_is_idempotent(repairable_story, session):
    """It selects on a property of the row, so the second run has nothing to do."""
    first, _ = await warm.repair_wasted_chips(
        session, warm.SERVABLE_STATUSES, only=[repairable_story]
    )
    await session.commit()
    second, refused = await warm.repair_wasted_chips(
        session, warm.SERVABLE_STATUSES, only=[repairable_story]
    )
    await session.commit()
    assert len(first) == 1 and second == [] and refused == []


async def test_the_repaired_story_still_passes_the_gate_for_every_reader(
    repairable_story, session
):
    """§7 serves a story to any reader at or ABOVE its stage, so the postcondition
    has to hold at all of them — not just at the story's own."""
    await warm.repair_wasted_chips(session, warm.SERVABLE_STATUSES, only=[repairable_story])
    await session.commit()
    row = (
        await session.execute(
            warm.text(
                "SELECT s.stage_id, s.story_words, "
                "(SELECT string_agg(g.body, ' ' ORDER BY g.idx) FROM story_segments g "
                " WHERE g.story_id = s.id) AS body FROM stories s WHERE s.id = :i"
            ),
            {"i": repairable_story},
        )
    ).first()
    _allowed, sight = await warm.stage_vocabularies(session)
    for reader in range(row.stage_id, MAX_STAGE + 1):
        report = warm.validate_text(
            row.body,
            warm.mastered_ids(reader),
            sight.get(reader, frozenset()),
            frozenset(row.story_words),
        )
        assert report.decodability >= DECODABILITY_GATE, (reader, report.decodability)


async def test_the_repair_refuses_a_drop_that_would_lower_decodability(
    repairable_story, session, monkeypatch
):
    """The postcondition, tested at the seam it exists to protect.

    Conditions 1 and 2 make this branch unreachable on real data — which is
    exactly why a sabotage run could delete the guard and leave every test green.
    So the guard is exercised by making the SPLIT wrong, which is the failure it
    is a proof against: `droppable_chips` gaining a bug and proposing a word the
    reader cannot decode. The repair must re-score, refuse, and write nothing.
    """
    monkeypatch.setattr(
        warm, "droppable_chips", lambda words, stage, permitted: (["castle"], ["cave", "fire"])
    )
    repairs, refused = await warm.repair_wasted_chips(
        session, warm.SERVABLE_STATUSES, only=[repairable_story]
    )
    await session.commit()

    assert repairs == []
    assert refused == [repairable_story]
    # Nothing was written: the row still carries all three chips.
    row = (
        await session.execute(
            warm.text("SELECT story_words, decodability FROM stories WHERE id = :i"),
            {"i": repairable_story},
        )
    ).first()
    assert sorted(row.story_words) == ["castle", "cave", "fire"]
    assert float(row.decodability) == 1.0
