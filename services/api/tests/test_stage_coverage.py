"""DEVIATIONS 5.17: the story has to contain the sounds the stage teaches.

§8 promotes on accuracy over the GPCs introduced AT the child's stage, and the
only thing that moves those counters is a tap-a-word question — which can only
ask about a grapheme the STORY already contains. 5.16 fixed the question node's
*choice*; nothing had yet fixed what it was choosing from. Stage 4 teaches only
word-final `-s -es -ed -ing`, so a story with no suffixed word makes promotion
unreachable however well the child reads.

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
from storyforge.decoder import load_gpcs
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
from storyforge.mastery import MAX_STAGE, word_gpc_ids
from storyforge.precompute import load_blocklist

# A stage-4-shaped allowlist: mostly stages 1-3, with the handful of suffixed
# words that stage 4 is the first to make readable. Frequency-ordered, as the
# `allowed_words` table delivers it.
STAGE4_ALLOWED = [
    "and", "in", "it", "at", "can", "did", "man", "sat", "cat", "dad",
    "sit", "camp", "hat", "top", "run", "big", "red", "let", "must", "back",
    "get", "him", "not", "but", "for", "up", "had", "van", "job", "that",
    "with", "this", "will", "just", "them", "then", "much", "long", "thing", "went",
    # stage 4 lives here — plus two sight words and one blocklisted word that
    # the aligner is perfectly happy to call stage 4.
    "is", "was", "dogs", "kids", "hands", "runs", "jumped", "asked", "helped",
    "killed", "things",
]
STAGE4_SIGHT = ["the", "a", "i", "is", "to", "no", "go", "he", "she", "we", "me", "was",
                "said", "you", "they"]

# Every word here is decodable at stage 1, so stage 1's own sounds are
# unavoidable — the section has nothing to add.
STAGE1_ALLOWED = ["and", "in", "it", "at", "can", "did", "man", "sat", "cat", "dad"]

# Takes the hint: "kids" and "jumped" both need a stage-4 suffix GPC.
STAGE4_STORY_WITH = (
    "The kids ran up a hill. A big dog sat on a mat. "
    "The kids jumped and did not stop. The dog ran back."
)
# Same shape, same length, no stage-4 word anywhere.
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
def test_every_offered_word_would_earn_evidence():
    """The load-bearing property. A word the grader would not credit is worse
    than no word at all: the story looks like it closed the loop and did not."""
    words = offered()
    assert words
    for word in words:
        assert stage_gpcs(4) & set(word_gpc_ids(word, 4)), word


def test_offers_the_suffixed_words_the_stage_actually_teaches():
    assert set(offered()) >= {"dogs", "kids", "hands", "runs", "jumped", "asked", "helped"}


def test_nothing_is_invented():
    """§6's lever is the allowlist. A word from outside it would be unreadable
    and would fail the gate — the prompt may only re-point at what it was given."""
    assert set(offered()) <= {w.lower() for w in STAGE4_ALLOWED}


def test_sight_words_are_never_offered():
    """`is` and `was` need `s|Z` and so look like stage-4 evidence to the
    aligner, but `sound_targets` refuses sight words as tap-a-word options — the
    child recognised the shape, they did not sound it out. A story that
    satisfied the ask with `is` would satisfy nothing."""
    assert "is" not in offered() and "was" not in offered()


def test_blocklisted_words_are_never_offered():
    """A shortlist the writer is told to PREFER must clear §13's deterministic
    layer. `killed` is stage-4 by suffix and blocklisted; both are true."""
    assert "killed" in load_blocklist()
    assert stage_gpcs(4) & set(word_gpc_ids("killed", 4))
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
    assert section, "the stage-4 block must carry the practice section"
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
    assert allowlist_stage(tuple(STAGE4_ALLOWED)) == 4
    assert allowlist_stage(tuple(STAGE1_ALLOWED)) == 1
    assert allowlist_stage(()) is None


# --- the end of the chain: does a hinted story earn evidence? -----------------
def _scope(story: str) -> str:
    targets = sound_targets(
        story_text=story,
        stage_id=4,
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
    prompt is actually built from. Stage 12 introduces no GPCs at all, so its
    allowlist genuinely looks like stage 11's — documented, not silently wrong."""
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
        expected = 11 if stage == MAX_STAGE else stage
        assert allowlist_stage(tuple(rows)) == expected, stage


async def test_stage_four_really_has_words_to_offer(session):
    """5.17's premise, measured rather than assumed: the top-400 window for the
    one stage that could not close the loop does contain suffixed words."""
    rows = [
        r[0]
        for r in await session.execute(
            text(
                "SELECT word FROM allowed_words WHERE stage_id = 4 ORDER BY freq_rank LIMIT 400"
            )
        )
    ]
    sight = {
        r[0]
        for r in await session.execute(
            text("SELECT word FROM sight_words WHERE stage_id <= 4")
        )
    }
    words = practice_words(tuple(rows), frozenset(sight))
    assert len(words) == PRACTICE_WORD_LIMIT
    for word in words:
        assert stage_gpcs(4) & set(word_gpc_ids(word, 4)), word
