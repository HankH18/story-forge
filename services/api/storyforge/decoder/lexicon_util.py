"""Pure helpers derived from CMUdict pronunciations.

Lives in the decoder (not precompute) because it is a pure function of a
pronunciation, and §6 (stage-scaled story length) and §14 (syllable metrics) both
need it. Putting it in the precompute job would guarantee a reimplementation.
"""

from __future__ import annotations

from storyforge.decoder.search import align

# ARPAbet vowels carry a stress digit; consonants never do. Counting stressed
# vowels is an exact syllable count for CMUdict, so no syllabifier is needed.
VOWELS = frozenset(
    {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UW", "UH"}
)


def syllable_count(pron: tuple[str, ...]) -> int:
    """Syllables in one pronunciation. Always >= 1."""
    n = sum(1 for p in pron if p and p[-1].isdigit() and p[:-1] in VOWELS)
    return n or 1


def min_syllables(prons: tuple[tuple[str, ...], ...]) -> int:
    """Fewest syllables across ALL of a word's pronunciations.

    **Not the number to cap a stage's allowlist on** — see `aligned_syllables`.
    This one is a fact about the dictionary entry, with no reference to what any
    child can decode, so it is the right answer only where the question is also
    stage-free (§14 corpus metrics).
    """
    return min((syllable_count(p) for p in prons), default=1)


def aligned_syllables(
    word: str, prons: tuple[tuple[str, ...], ...], mastered: frozenset[str]
) -> int | None:
    """Fewest syllables among the pronunciations that ALIGN under `mastered`.

    `None` when none of them do — the word is not decodable at this stage at all,
    which is a different answer from a large syllable count and is never a number.

    **Why this exists (DEVIATIONS 21.2).** §5.4's syllable cap is a statement
    about reading load: "a stage-5 reader is not asked to sound out a word of more
    than two syllables". `min_syllables` answers a question nobody asked — the
    fewest syllables in ANY pronunciation CMUdict lists, including the ones the
    aligner could not use at this stage — so the cap was measuring a different
    object from the one the child reads. Three live examples, all in `allowed_words`
    before this landed:

      `every`   CMUdict has /EH1 V ER0 IY0/ (3) and /EH1 V R IY0/ (2). At stage 5
                only the 3-syllable one aligns — the 2-syllable one needs a bare
                `r` between two vowels with no vowel of its own. Counted as 2,
                admitted under a cap of 2, read as three syllables.
      `towards` /T AH0 W AO1 R D Z/ (2) and /T AO1 R D Z/ (1). At stage 6 only the
                2-syllable one aligns. Counted as 1.
      `fine`    /F AY1 N/ (1) and the Italian /F IH1 N AH0/ (2). Silent-e is stage
                4, so at stages 1-3 the ONLY alignment is the 2-syllable one — the
                word is in the stage-1 allowlist, counted as one syllable, on the
                strength of a reading no stage-1 child can produce.

    Per-stage by construction, because alignability is: the set of usable
    pronunciations grows monotonically with the mastered set, so this is
    non-increasing in stage and a word can get *shorter* as the child learns more.
    Keeping the "kindest reading" convention of `min_syllables` — the fewest
    syllables the child could arrive at — narrowed to readings they can actually
    arrive at.
    """
    counts = [syllable_count(p) for p in prons if align(word, (p,), mastered)[0] is not None]
    return min(counts, default=None)
