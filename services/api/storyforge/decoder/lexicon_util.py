"""Pure helpers derived from CMUdict pronunciations.

Lives in the decoder (not precompute) because it is a pure function of a
pronunciation, and §6 (stage-scaled story length) and §14 (syllable metrics) both
need it. Putting it in the precompute job would guarantee a reimplementation.
"""

from __future__ import annotations

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
    """Fewest syllables across a word's pronunciations (the kindest reading)."""
    return min((syllable_count(p) for p in prons), default=1)
