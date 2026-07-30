"""Anti-goldens — the tripwire that keeps the guarantee from dissolving.

The golden suite (§5.7) proves the validator ACCEPTS what it should. Nothing
there stops the inventory from growing until it accepts everything, and a gate
that rejects nothing proves nothing — while still reporting a healthy 97% on the
eval dashboard.

That failure is invisible from the accept side, so it gets its own tests.

§5.2.5 makes the validator an existence check: a word passes if ANY alignment
using mastered GPCs exists. So every GPC added strictly widens what passes and
can never narrow it. Growth is therefore only safe when bounded from the other
direction — which is what this file is.

The two failure directions are not symmetric:

  missing GPC -> a readable word is wrongly rejected -> the allowlist shrinks and
                 stories get repetitive. Annoying, visible, self-correcting.
  extra GPC   -> an unreadable word is certified -> a five-year-old is handed
                 "beautiful". Invisible, and it breaks the product's one promise.

§18 is explicit that the guarantee's strength is its smallness.
"""

from __future__ import annotations

import random

import pytest

from storyforge.decoder import MAX_STAGE, load_gpcs, load_lexicon, mastered_ids, validate_word
from storyforge.decoder.types import WordVerdict

# Words whose spelling-to-sound mapping is genuinely beyond a K-5 decodable
# reader. Each needs a correspondence no early phonics sequence teaches, listed
# so a future reader can see WHY rather than guessing.
NEVER_DECODABLE = {
    "beautiful": "eau -> /juː/",
    "colonel": "the spelling has no r; the pronunciation does",
    "yacht": "ach -> silent",
    "once": "o -> /w/",
    "one": "o -> /w/",
    "of": "f -> /v/",
    "laugh": "augh -> /af/",
    "through": "ough -> /uː/",
    "island": "silent s",
    "eight": "eigh -> /eɪ/",
    "sure": "s -> /ʃ/",
    "busy": "u -> /ɪ/",
    "women": "o -> /ɪ/",
    "two": "silent w",
    "friend": "ie -> /ɛ/",
    "said": "ai -> /ɛ/",
}


@pytest.mark.parametrize("word", sorted(NEVER_DECODABLE), ids=sorted(NEVER_DECODABLE))
def test_stays_undecodable_at_every_stage(word):
    """Checked with NO sight-word allowlist, so alignment is actually exercised.

    Several of these ARE sight words in the real curriculum ("said", "one") —
    that is how a child meets them, and it is the correct product behaviour. The
    point here is narrower: the *aligner* must never claim to have sounded them
    out.
    """
    for stage in range(1, MAX_STAGE + 1):
        verdict = validate_word(word, mastered_ids(stage)).verdict
        assert verdict is not WordVerdict.DECODABLE, (
            f"{word!r} became decodable at stage {stage}. It requires "
            f"{NEVER_DECODABLE[word]}, which no early phonics sequence teaches. "
            f"A GPC was added that widened the gate too far."
        )


# Words that were unspellable before the doubling / silent-e / `wa` rules landed.
# Locked so the fix cannot silently regress. Stages follow the eleven-stage
# ladder (DEVIATIONS 12.6); each boundary below is re-proved every run, because
# the test also asserts NOT-decodable one stage earlier.
MUST_BE_DECODABLE = {
    "happy": 5, "funny": 5, "sunny": 5, "bunny": 5, "puppy": 5,
    "water": 6, "house": 7, "mouse": 7, "little": 9,
    "rabbit": 1, "kitten": 1, "carrot": 1, "egg": 1, "have": 4,
}


@pytest.mark.parametrize("word,stage", sorted(MUST_BE_DECODABLE.items()))
def test_common_early_reader_words_are_decodable(word, stage):
    assert validate_word(word, mastered_ids(stage)).verdict is WordVerdict.DECODABLE
    if stage > 1:
        assert (
            validate_word(word, mastered_ids(stage - 1)).verdict is not WordVerdict.DECODABLE
        ), f"{word} is decodable earlier than stage {stage} — update the expectation"


# --- the vacuity metric ------------------------------------------------------
# Fraction of an adult dictionary the inventory can spell with EVERYTHING
# unlocked — and "everything unlocked" now means the GPC inventory itself, never
# a stage number (DEVIATIONS 21.3; see `full_inventory` below for the drift this
# closes). Currently 44.75% at 93 GPCs — was 44.62% at 92 before `wh`->/w/ was
# added (docs/DEVIATIONS.md 8.13 records the +0.13pt as the price of 31 words).
# COMMENT ONLY: the SEED, SAMPLE SIZE, FLOOR and CEILING below are untouched.
# The ratchet has now fired on a change someone actually wanted rather than on a
# sabotage — an `o`->/oʊ/ row to rescue `so`/`old`/`gold` lands at 50.4% and is
# refused (8.12). That is this guard doing its job, not a nuisance.
#
# This is a RATCHET, not a wide band. The ceiling sits ~5 points above the
# measured value, which is deliberate: raising it must be a conscious act with a
# justification attached, not something a batch of new rows drifts past.
#
# Honest limitation, established by sabotage-testing this file: the metric alone
# does NOT cleanly separate good growth from bad. The ten justified rows added in
# this commit moved it +8.8 points; eleven junk vowel rules moved it +14.2. Those
# distributions overlap. So the named NEVER_DECODABLE list above is the PRIMARY
# guard — it caught both sabotages by name — and this metric is a backstop for
# gross dissolution (a 400-entry descriptive import would land near 90%).
#
# A first draft of this test used a 0.60 ceiling and let an eleven-rule junk
# import pass at 58.8%. A guard that has never been fired at is not a guard.
VACUITY_SAMPLE_SIZE = 800
VACUITY_SEED = 20260728
VACUITY_FLOOR = 0.35
VACUITY_CEILING = 0.50


def full_inventory() -> frozenset[str]:
    """Every GPC in `gpcs.csv`, with no reference to any stage number.

    **This, and not `mastered_ids(MAX_STAGE)`, is what the ratchet is about
    (DEVIATIONS 21.3).** The ratchet's question is "how permissive has the
    INVENTORY become" — a property of a 93-row CSV. Expressed as "what does the
    top stage decode" it became a property of a hand-maintained integer as well,
    and the two coincide only by the convention that the top stage is cumulative
    review. That convention is pinned by a literal `MAX_STAGE == 11` in
    `test_inventory.py` — the exact line a re-stage edits — so the ratchet was
    reading its own subject off the thing most likely to move.

    Measured, at the seed and sample size below, over the CURRENT inventory:

        stage  8  ->  44.00%     stage 10  ->  44.75%
        stage  9  ->  44.375%    stage 11  ->  44.75%   (= full inventory)

    So the recorded 44.75% survived the twelve-to-eleven renumber intact and still
    describes the same measurement it did before it (DEVIATIONS 12.6 claimed this
    and it re-measures true) — but only because both 12 and 11 sat at or above the
    highest stage any GPC is taught at. Leave a GPC above `MAX_STAGE`, or retire
    two more stages, and the same code reports 44.00% while nothing about
    permissiveness has changed: five points of unearned headroom under the 50%
    ceiling, arriving silently and in the safe-looking direction. A ratchet whose
    reading moves for reasons unrelated to permissiveness is not measuring
    permissiveness.
    """
    return frozenset(g.id for g in load_gpcs())


def vacuity_ratio(mastered: frozenset[str]) -> float:
    """Fraction of the fixed adult-dictionary sample `mastered` can spell."""
    lexicon = load_lexicon()
    words = sorted(w for w in lexicon if w.isalpha() and 2 <= len(w) <= 9)
    sample = random.Random(VACUITY_SEED).sample(words, VACUITY_SAMPLE_SIZE)
    decodable = sum(
        1 for w in sample if validate_word(w, mastered).verdict is WordVerdict.DECODABLE
    )
    return decodable / VACUITY_SAMPLE_SIZE


def test_gate_has_not_dissolved():
    inventory = full_inventory()
    ratio = vacuity_ratio(inventory)

    assert ratio <= VACUITY_CEILING, (
        f"{ratio:.1%} of sampled adult-dictionary words are decodable with the "
        f"whole {len(inventory)}-GPC inventory unlocked. It has grown past the "
        f"point where the guarantee means anything — §18: the strength is its "
        f"smallness. Justify each new GPC against a real scope & sequence, or "
        f"drop it."
    )
    assert ratio >= VACUITY_FLOOR, (
        f"only {ratio:.1%} decodable across {len(inventory)} GPCs — the inventory "
        f"lost coverage and generation will starve for vocabulary."
    )


def test_the_ratchet_is_invariant_under_re_staging():
    """DEVIATIONS 21.3 — the ratchet's subject, asserted where it is measured.

    Two claims, and the second is what stops the first from being decorative.

    1. **Every GPC is reachable**: the top stage of the ladder teaches the whole
       inventory. A GPC stranded above `MAX_STAGE` is a rule no child is ever
       taught, and it would silently shrink what the ratchet measures. This is the
       premise `test_gate_has_not_dissolved` used to assume; it now has to hold.
    2. **The reading really does depend on the stage label**, so relying on that
       label was a live hazard and not a theoretical one. If a smaller mastered
       set scored the same, `mastered_ids(MAX_STAGE)` would have been a harmless
       spelling of `full_inventory()` and none of this would matter.
    """
    inventory = full_inventory()
    assert mastered_ids(MAX_STAGE) == inventory, (
        f"stage {MAX_STAGE} does not teach the whole inventory: "
        f"{sorted(inventory - mastered_ids(MAX_STAGE))} are taught above the top "
        f"stage, so no child ever reaches them — and the vacuity ratchet would "
        f"have measured a subset and reported unearned headroom."
    )
    assert vacuity_ratio(mastered_ids(MAX_STAGE - 3)) < vacuity_ratio(inventory)
