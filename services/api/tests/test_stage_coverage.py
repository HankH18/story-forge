"""DEVIATIONS 5.17: the story has to contain the sounds the stage teaches.

§8 promotes on accuracy over the GPCs introduced AT the child's stage, and the
only thing that moves those counters is a tap-a-word question — which can only
ask about a grapheme the STORY already contains. 5.16 fixed the question node's
*choice*; nothing had yet fixed what it was choosing from. Stage 3 — the
suffix stage since the eleven-stage renumber (DEVIATIONS 12.1-12.5) — teaches
only word-final `-s -es -ed -ing`, so a story with no suffixed word makes
promotion unreachable however well the child reads.

These tests pin the generation-side half: the prompt names the allowlist words
that carry the stage's new sounds, it names only words the *grader* would credit,
it never names a word the child could not be asked about, and it asks as a
preference — so a stage or a story that genuinely cannot carry one still
produces a valid story.

The end of the chain is verified against `llm/questions.sound_targets` itself
rather than against a restatement of it: a story that takes the hint must
actually yield a question tier that earns evidence.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.decoder import MAX_STAGE, load_gpcs
from storyforge.llm.generate import (
    PRACTICE_HEADER,
    PRACTICE_WORD_LIMIT,
    SYSTEM_RULES,
    allowlist_stage,
    build_practice_block,
    build_vocabulary_block,
    practice_words,
)
from storyforge.llm.questions import sound_targets
from storyforge.mastery import word_gpc_ids

# The prompt's own window, imported rather than re-spelled as `LIMIT 400`: a test
# that measured a different slice of `allowed_words` from the one the model is
# handed would be measuring nothing (`pipeline.load_vocabulary`).
from storyforge.pipeline import PROMPT_WORD_LIMIT
from storyforge.precompute import load_blocklist

# A suffix-stage-shaped allowlist: mostly stages 1-2, with the handful of
# suffixed words that stage 3 is the first to make readable. Frequency-ordered,
# as the `allowed_words` table delivers it.
STAGE4_ALLOWED = [
    "and", "in", "it", "at", "can", "did", "man", "sat", "cat", "dad",
    "sit", "camp", "hat", "top", "run", "big", "red", "let", "must", "back",
    "get", "him", "not", "but", "for", "up", "had", "van", "job", "that",
    "with", "this", "will", "just", "them", "then", "much", "long", "thing", "went",
    # the suffix stage lives here — plus two sight words and one blocklisted
    # word that the aligner is perfectly happy to call stage 3.
    "is", "was", "dogs", "kids", "hands", "runs", "jumped", "asked", "helped",
    "killed", "things",
]
STAGE4_SIGHT = ["the", "a", "i", "is", "to", "no", "go", "he", "she", "we", "me", "was",
                "said", "you", "they"]

# Every word here is decodable at stage 1, so stage 1's own sounds are
# unavoidable — the section has nothing to add.
STAGE1_ALLOWED = ["and", "in", "it", "at", "can", "did", "man", "sat", "cat", "dad"]

# The shape DEVIATIONS 11.86 measured, reduced to a fixture: more words carrying
# the stage's COMMONEST new sound than there are slots, and the other two sounds
# it teaches sitting past the cut. Every `s|Z` word below is a real stage-3
# allowlist word in its real frequency order; `helped` (`ed|T`) and `lived`
# (`ed|D`) are real too, and rarer than all of them. The 40 leading fillers are
# there because `PRACTICE_SATURATION` measures scarcity over the whole candidate
# list — without them the section would fall silent and prove nothing.
STARVED_ALLOWED = STAGE4_ALLOWED[:40] + [
    "as", "his", "has", "things", "kids", "hands", "fans", "plans", "jobs",
    "levels", "happens", "records", "seconds", "runs", "songs", "tells",
    "classes", "dogs", "ends", "forms", "wins", "legs", "taxes", "films",
    "begins", "brings",
] + ["helped", "lived"]

# Takes the hint: "kids" and "jumped" both need a stage-3 suffix GPC.
STAGE4_STORY_WITH = (
    "The kids ran up a hill. A big dog sat on a mat. "
    "The kids jumped and did not stop. The dog ran back."
)
# Same shape, same length, no stage-3 word anywhere.
STAGE4_STORY_WITHOUT = (
    "The man ran up a hill. A big dog sat on a mat. "
    "The man did not stop. The dog ran back."
)


def stage_gpcs(stage: int) -> frozenset[str]:
    return frozenset(g.id for g in load_gpcs() if g.stage == stage)


def offered(allowed=None, sight=None) -> tuple[str, ...]:
    return practice_words(
        tuple(STAGE4_ALLOWED if allowed is None else allowed),
        frozenset(STAGE4_SIGHT if sight is None else sight),
    )


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


# --- what the prompt is allowed to offer -------------------------------------
# TWO quantifiers, and §8 needs both (DEVIATIONS 11.86-11.89). The test below is
# the first — every offered WORD names an introduced GPC — and it was the only one
# asserted for two phases. It is satisfied by twenty-four copies of one `s|Z`
# word, which is close to what the live stage-3 list actually was. The second —
# every introduced GPC the allowlist can EXPRESS is named by some offered word —
# is `test_every_introduced_sound_the_live_allowlist_can_express_is_offered`, at
# the foot of this file. Neither implies the other, and neither replaces the
# other. Both fixture-based tests here run on the 40-word `STAGE4_ALLOWED`, which
# never reaches `PRACTICE_WORD_LIMIT`, so the second one is deliberately driven
# off the live table: the fixture is precisely why the omission was invisible.
def test_every_offered_word_would_earn_evidence():
    """The load-bearing property. A word the grader would not credit is worse
    than no word at all: the story looks like it closed the loop and did not."""
    words = offered()
    assert words
    for word in words:
        assert stage_gpcs(3) & set(word_gpc_ids(word, 3)), word


def test_offers_the_suffixed_words_the_stage_actually_teaches():
    assert set(offered()) >= {"dogs", "kids", "hands", "runs", "jumped", "asked", "helped"}


def test_nothing_is_invented():
    """§6's lever is the allowlist. A word from outside it would be unreadable
    and would fail the gate — the prompt may only re-point at what it was given."""
    assert set(offered()) <= {w.lower() for w in STAGE4_ALLOWED}


def test_sight_words_are_never_offered():
    """`is` and `was` need `s|Z` and so look like stage-3 evidence to the
    aligner, but `sound_targets` refuses sight words as tap-a-word options — the
    child recognised the shape, they did not sound it out. A story that
    satisfied the ask with `is` would satisfy nothing."""
    assert "is" not in offered() and "was" not in offered()


def test_blocklisted_words_are_never_offered():
    """A shortlist the writer is told to PREFER must clear §13's deterministic
    layer. `killed` is stage-3 by suffix and blocklisted; both are true."""
    assert "killed" in load_blocklist()
    assert stage_gpcs(3) & set(word_gpc_ids("killed", 3))
    assert "killed" not in offered()


def test_the_list_is_bounded():
    big = STAGE4_ALLOWED + [f"{stem}s" for stem in ("cat", "hat", "top", "van", "job", "camp")]
    assert len(practice_words(tuple(big * 3), frozenset(STAGE4_SIGHT))) <= PRACTICE_WORD_LIMIT


def test_the_offer_is_frequency_ordered():
    """Inherited from `allowed_words`, and it matters: the head of the list is
    the part a thirty-word story can actually carry."""
    words = offered()
    ranks = [STAGE4_ALLOWED.index(w) for w in words]
    assert ranks == sorted(ranks)


def test_an_offer_that_fits_is_returned_exactly_as_it_came():
    """Coverage selection only decides what SURVIVES the limit.

    Below `PRACTICE_WORD_LIMIT` nothing is dropped and nothing is reordered, so
    every fixture-based expectation above still describes the same list it did
    before DEVIATIONS 11.87. Stated as an assertion rather than left as an
    argument about two loops."""
    words = offered()
    assert len(words) < PRACTICE_WORD_LIMIT
    assert list(words) == [w for w in STAGE4_ALLOWED if w in set(words)]


def test_the_limit_is_spent_on_the_stage_s_SOUNDS_not_on_its_commonest_one():
    """DEVIATIONS 11.86, as a fixture. `picked[:24]` gave all twenty-four slots
    to `s|Z` and never named `ed|T` or `ed|D` — two of the three sounds this
    stage teaches that the list can express — so no story taking the hint could
    ever be asked about them, and §8 could never collect the evidence.

    Note what is NOT claimed: the frequent words are not dropped, and `s|Z` still
    gets the largest share. The slots are shared, not reassigned."""
    words = practice_words(tuple(STARVED_ALLOWED), frozenset(STAGE4_SIGHT))
    assert len(words) == PRACTICE_WORD_LIMIT
    named = {gpc for w in words for gpc in stage_gpcs(3) & set(word_gpc_ids(w, 3))}
    assert named == {"s|Z", "ed|T", "ed|D"}
    assert "helped" in words and "lived" in words
    # still an allowlist word, still frequency-ordered, still the common head
    assert set(words) <= set(STARVED_ALLOWED)
    ranks = [STARVED_ALLOWED.index(w) for w in words]
    assert ranks == sorted(ranks)
    assert words[0] == "as"


# --- when the section must stay silent ---------------------------------------
def test_a_saturated_allowlist_offers_nothing():
    """Every stage-1 word uses a stage-1 sound, so a stage-1 story cannot miss
    them. Naming a sample would only narrow the vocabulary for no gain."""
    assert practice_words(tuple(STAGE1_ALLOWED)) == ()
    assert build_practice_block(STAGE1_ALLOWED, ["the", "a"]) == ""


def test_an_empty_allowlist_is_safe():
    assert practice_words(()) == ()
    assert build_practice_block([], []) == ""


def test_an_undecodable_allowlist_is_safe():
    assert practice_words(("zzzz", "qqqq")) == ()


# --- prompt assembly and cache economics -------------------------------------
def test_the_vocabulary_block_still_carries_every_word_and_count():
    """The pre-existing contract. The section is an addition, not a rewrite."""
    block = build_vocabulary_block("Endings", STAGE4_ALLOWED, STAGE4_SIGHT)
    for word in STAGE4_ALLOWED + STAGE4_SIGHT:
        assert word in block
    assert f"({len(STAGE4_ALLOWED)})" in block
    assert f"({len(STAGE4_SIGHT)})" in block
    assert "Endings" in block


def test_the_section_is_appended_not_interleaved():
    """Prompt caching is prefix-matched, so the existing head of the block has
    to stay byte-identical — otherwise every stage's cache entry is orphaned."""
    block = build_vocabulary_block("Endings", STAGE4_ALLOWED, STAGE4_SIGHT)
    section = build_practice_block(STAGE4_ALLOWED, STAGE4_SIGHT)
    assert section, "the suffix-stage block must carry the practice section"
    assert block.endswith(section)

    head = block[: -len(section)]
    assert PRACTICE_HEADER not in head
    # The head is exactly the pre-5.17 block: stage, permitted words, sight words.
    assert head.startswith("STAGE: Endings\n\nPERMITTED WORDS")
    assert head.endswith(", ".join(sorted(STAGE4_SIGHT)))


def test_the_block_is_byte_identical_across_calls():
    """Two stories at one stage must produce one cache entry, not two."""
    args = ("Endings", STAGE4_ALLOWED, STAGE4_SIGHT)
    assert build_vocabulary_block(*args) == build_vocabulary_block(*args)


def test_system_rules_stay_free_of_stage_material():
    """STATE invariant 10 / DEVIATIONS 3.5: `SYSTEM_RULES` is the frozen cache
    prefix. Per-stage detail there would invalidate every cached prefix on every
    request and multiply cost — which is exactly why the section lives in the
    vocabulary block instead."""
    assert PRACTICE_HEADER not in SYSTEM_RULES
    for word in offered():
        assert word not in SYSTEM_RULES.split()


def test_the_ask_is_a_preference():
    """A hard requirement would turn an impossible stage or a very short story
    into a generation failure. §0's gate is decodability, never this."""
    block = build_practice_block(STAGE4_ALLOWED, STAGE4_SIGHT)
    prose = block.split("\n\n")[-1]
    assert "preference" in prose
    assert "still fine" in prose
    for imperative in ("must", "always", "every", "required"):
        assert imperative not in prose.lower().split()


# --- reading the stage off the allowlist -------------------------------------
def test_the_stage_is_read_off_the_allowlist():
    assert allowlist_stage(tuple(STAGE4_ALLOWED)) == 3
    assert allowlist_stage(tuple(STAGE1_ALLOWED)) == 1
    assert allowlist_stage(()) is None


# --- the end of the chain: does a hinted story earn evidence? -----------------
def _scope(story: str) -> str:
    targets = sound_targets(
        story_text=story,
        stage_id=3,
        allowed_words=STAGE4_ALLOWED,
        sight_words=STAGE4_SIGHT,
    )
    return targets[0].scope if targets else "none"


def test_a_story_that_takes_the_hint_can_be_asked_a_stage_earning_question():
    """The whole point of 5.17, checked against the question node itself."""
    assert _scope(STAGE4_STORY_WITH) in ("current_stage_onset", "current_stage_word")


def test_the_same_story_without_a_stage_word_falls_back():
    """The gap being closed, stated as a fact rather than as a worry: identical
    shape, no suffixed word, no evidence available."""
    assert _scope(STAGE4_STORY_WITHOUT) == "older_stage_fallback"


# --- against the real precomputed allowlist ----------------------------------
async def test_the_real_allowlists_are_recognised(session):
    """Offline fixtures prove the logic; this proves it against the artifact the
    prompt is actually built from. The top stage (11, review) introduces no
    GPCs at all, so its allowlist genuinely looks like stage 10's — documented,
    not silently wrong."""
    for stage in range(1, MAX_STAGE + 1):
        rows = [
            r[0]
            for r in await session.execute(
                text(
                    "SELECT word FROM allowed_words WHERE stage_id = :s "
                    "ORDER BY freq_rank LIMIT 400"
                ),
                {"s": stage},
            )
        ]
        assert rows, f"stage {stage} has no allowlist — run `make precompute`"
        expected = MAX_STAGE - 1 if stage == MAX_STAGE else stage
        assert allowlist_stage(tuple(rows)) == expected, stage


async def test_the_suffix_stage_really_has_words_to_offer(session):
    """5.17's premise, measured rather than assumed: the top-400 window for the
    one stage that could not close the loop does contain suffixed words."""
    rows = [
        r[0]
        for r in await session.execute(
            text(
                "SELECT word FROM allowed_words WHERE stage_id = 3 ORDER BY freq_rank LIMIT 400"
            )
        )
    ]
    sight = {
        r[0]
        for r in await session.execute(
            text("SELECT word FROM sight_words WHERE stage_id <= 3")
        )
    }
    words = practice_words(tuple(rows), frozenset(sight))
    assert len(words) == PRACTICE_WORD_LIMIT
    for word in words:
        assert stage_gpcs(3) & set(word_gpc_ids(word, 3)), word


# --- the coverage direction, against the LIVE allowlist (DEVIATIONS 11.86-11.89)
# Every assertion below reads `allowed_words` for all eleven stages, because the
# omission this closes was invisible to the synthetic fixture by construction: 40
# words never reach the limit, so nothing was ever cut, so no sound was ever lost.
async def _live_window(session, stage: int) -> tuple[tuple[str, ...], frozenset[str]]:
    """Exactly the vocabulary `pipeline.load_vocabulary` hands the prompt."""
    rows = tuple(
        r[0]
        for r in await session.execute(
            text(
                "SELECT word FROM allowed_words WHERE stage_id = :s "
                "ORDER BY freq_rank LIMIT :n"
            ),
            {"s": stage, "n": PROMPT_WORD_LIMIT},
        )
    )
    assert rows, f"stage {stage} has no allowlist — run `make precompute`"
    sight = frozenset(
        r[0].strip().lower()
        for r in await session.execute(
            text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": stage}
        )
    )
    return rows, sight


def _expressible(window: tuple[str, ...], sight: frozenset[str], stage: int) -> dict[str, str]:
    """Introduced GPCs this window CAN name -> the first allowlist word naming one.

    Derived from the raw window, the decoder and the blocklist — never from
    `practice_words`, so a bug in the picker cannot make the assertions pass.
    Sight words and blocklisted words are excluded because the picker excludes
    them: a sound only reachable through `is` or `killed` is not reachable.
    """
    blocked = load_blocklist()
    out: dict[str, str] = {}
    for raw in window:
        word = raw.strip().lower()
        if not word or word in sight or word in blocked:
            continue
        for gpc in stage_gpcs(stage) & set(word_gpc_ids(word, stage)):
            out.setdefault(gpc, word)
    return out


def _named(offer: tuple[str, ...], stage: int) -> set[str]:
    return {gpc for word in offer for gpc in stage_gpcs(stage) & set(word_gpc_ids(word, stage))}


async def test_the_practice_section_speaks_at_every_live_stage_but_the_saturated_one(session):
    """Pins the vacuity of the two tests below: they say nothing about a stage
    whose offer is empty, so which stages those are has to be an assertion.

    Stage 1 is silent by design (`PRACTICE_SATURATION`) — every word in its
    window uses a stage-1 sound, so naming a sample would only narrow the
    vocabulary. If any other stage ever falls silent, that is the finding."""
    silent = []
    for stage in range(1, MAX_STAGE + 1):
        window, sight = await _live_window(session, stage)
        if not practice_words(window, sight):
            silent.append(stage)
    assert silent == [1]


async def test_every_introduced_sound_the_live_allowlist_can_express_is_offered(session):
    """§8's quantifier, and the one nothing asserted (DEVIATIONS 11.86).

    A `tap_word` can only ask about a grapheme the STORY contains, and the story
    can only contain what the prompt pointed at — so an introduced GPC that no
    offered word names is a sound the stage can never be asked about, however
    well the child reads. Measured before the fix: stage 2 named 10 of the 12 its
    allowlist can express and dropped `sh|SH` (its headline digraph, §5.7's
    golden case, §17's demo sentence) while `ship` sat in the same 400 words;
    stage 4 dropped `u_e|UW`, stage 5 `oa|OW`, stage 7 `au|AO` and `oy|OY`.

    This is NOT a claim that a stage's whole inventory is reachable: several
    stages introduce sounds no word in the top-400 window spells at all
    (`or|AO.R`, `ph|F`, `ture|CH.ER`), and that is an allowlist fact no selection
    rule can fix. The claim is that nothing the allowlist CAN say is thrown away
    by the picker."""
    for stage in range(1, MAX_STAGE + 1):
        window, sight = await _live_window(session, stage)
        offer = practice_words(window, sight)
        if not offer:
            continue
        aligned = allowlist_stage(window)
        available = _expressible(window, sight, aligned)
        assert len(available) <= PRACTICE_WORD_LIMIT, (
            f"stage {stage}: {len(available)} expressible sound(s) and only "
            f"{PRACTICE_WORD_LIMIT} slots — full coverage is no longer reachable, "
            "and this test would otherwise quietly weaken into a partial one"
        )
        named = _named(offer, aligned)
        missing = {gpc: word for gpc, word in available.items() if gpc not in named}
        assert not missing, (
            f"stage {stage}: the practice list never names {sorted(missing)}, "
            f"though the allowlist can: {missing}"
        )


async def test_every_word_the_live_offer_names_still_earns_evidence(session):
    """The FIRST quantifier, on the live table rather than on the fixture.

    Coverage selection must not buy a sound by offering a word the grader would
    not credit — that is the failure the fixture-based
    `test_every_offered_word_would_earn_evidence` was written against, and §0
    forbids inventing a word regardless. Both directions, live, or the pair is
    only half measured."""
    for stage in range(1, MAX_STAGE + 1):
        window, sight = await _live_window(session, stage)
        offer = practice_words(window, sight)
        aligned = allowlist_stage(window)
        allowed = {w.strip().lower() for w in window}
        for word in offer:
            assert word in allowed, (stage, word)
            assert word not in sight, (stage, word)
            assert word not in load_blocklist(), (stage, word)
            assert stage_gpcs(aligned) & set(word_gpc_ids(word, aligned)), (stage, word)


async def test_the_live_offer_is_still_frequency_ordered(session):
    """Selection changed; ORDER did not (DEVIATIONS 11.87). The fixture cannot
    show this — it never truncates — and the head of the list is what a short
    story can actually carry."""
    for stage in range(1, MAX_STAGE + 1):
        window, sight = await _live_window(session, stage)
        offer = practice_words(window, sight)
        ranks = [window.index(word) for word in offer]
        assert ranks == sorted(ranks), stage
