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

from storyforge.decoder import MAX_STAGE, load_lexicon, mastered_ids, validate_word
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
# unlocked. Currently 44.75% at 93 GPCs — was 44.62% at 92 before `wh`->/w/ was
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


def test_gate_has_not_dissolved():
    lexicon = load_lexicon()
    words = sorted(w for w in lexicon if w.isalpha() and 2 <= len(w) <= 9)
    sample = random.Random(VACUITY_SEED).sample(words, VACUITY_SAMPLE_SIZE)

    # The TOP stage — the whole cumulative inventory. The ladder renumber
    # (DEVIATIONS 12.6) moved the top label from 12 to 11 without moving the
    # set: the top stage's mastered set is the full 93-GPC inventory either
    # way, so the measured ratio did not move (re-measured, not assumed).
    top = mastered_ids(MAX_STAGE)
    decodable = sum(
        1 for w in sample if validate_word(w, top).verdict is WordVerdict.DECODABLE
    )
    ratio = decodable / VACUITY_SAMPLE_SIZE

    assert ratio <= VACUITY_CEILING, (
        f"{ratio:.1%} of sampled adult-dictionary words are now decodable. The "
        f"inventory has grown past the point where the guarantee means anything "
        f"— §18: the strength is its smallness. Justify each new GPC against a "
        f"real scope & sequence, or drop it."
    )
    assert ratio >= VACUITY_FLOOR, (
        f"only {ratio:.1%} decodable — the inventory lost coverage and "
        f"generation will starve for vocabulary."
    )
