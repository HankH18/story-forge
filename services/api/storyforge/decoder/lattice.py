"""Alignment lattice construction (§5.2.4).

A lattice node is ``(i, j)`` — ``i`` letters and ``j`` phonemes consumed. An edge
is a :class:`Step`: one inventory-legal move. Nothing here knows about stages or
mastery; it enumerates every way the inventory *could* spell the word. Stage
filtering happens in :mod:`search`, which is what lets one lattice answer all
eleven stages (§5.4's precompute budget depends on that).

This module is deliberately dumb. §18: when a golden case fights the inventory,
edit ``gpcs.csv`` — never this file.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

from storyforge.decoder.resources import load_gpcs
from storyforge.decoder.types import GPC, Step

# §5.2.6: a reduced vowel in an unstressed syllable. Restricted to AH0/IH0
# exactly as the spec states — notably NOT ER0, which would open a far wider door.
SCHWA_PHONES = frozenset({"AH", "IH"})

VOWEL_LETTERS = frozenset("aeiou")

Lattice = dict[tuple[int, int], list[Step]]


def strip_stress(phone: str) -> tuple[str, int | None]:
    """``"AH0"`` -> ``("AH", 0)``. Consonants carry no digit -> ``(phone, None)``."""
    if phone and phone[-1].isdigit():
        return phone[:-1], int(phone[-1])
    return phone, None


@lru_cache(maxsize=4096)
def _by_grapheme() -> dict[str, tuple[GPC, ...]]:
    index: dict[str, list[GPC]] = defaultdict(list)
    for gpc in load_gpcs():
        index[gpc.grapheme].append(gpc)
    return {g: tuple(v) for g, v in index.items()}


def _position_ok(gpc: GPC, start: int, end: int, word_len: int) -> bool:
    if gpc.position == "initial":
        return start == 0
    if gpc.position == "final":
        return end == word_len
    return True


def _phonemes_match(gpc: GPC, bases: tuple[str, ...], j: int) -> bool:
    n = len(gpc.phonemes)
    return bases[j : j + n] == gpc.phonemes


@lru_cache(maxsize=8192)
def build_lattice(word: str, pron: tuple[str, ...]) -> Lattice:
    """Enumerate every inventory-legal step for one (spelling, pronunciation).

    Cached: :func:`search.min_stage` walks the same lattice once per word instead
    of rebuilding it eleven times.
    """
    bases: tuple[str, ...] = tuple(strip_stress(p)[0] for p in pron)
    stresses: tuple[int | None, ...] = tuple(strip_stress(p)[1] for p in pron)
    n_letters, n_phones = len(word), len(bases)
    index = _by_grapheme()
    lattice: Lattice = defaultdict(list)

    for i in range(n_letters):
        # Longest graphemes are 4 letters ("ture"); a split digraph spans 3.
        for span in range(1, 5):
            end = i + span
            if end > n_letters:
                break
            chunk = word[i:end]
            for gpc in index.get(chunk, ()):
                if not _position_ok(gpc, i, end, n_letters):
                    continue
                for j in range(n_phones + 1):
                    if _phonemes_match(gpc, bases, j):
                        lattice[(i, j)].append(
                            Step(chunk, gpc.phonemes, (gpc,))
                        )

            # --- §5.2.6 schwa relaxation -------------------------------------
            # A single vowel letter whose citation GPC is `schwa_ok` may match a
            # reduced AH0/IH0. The GPC credited is the CITATION one, so
            # "dragon"'s `o` passes at stage 2 via (o,/ɒ/) — and `schwa_relaxed`
            # is set so eval can report how often the guarantee leaned on it.
            if span == 1 and chunk in VOWEL_LETTERS:
                for j, (base, stress) in enumerate(zip(bases, stresses, strict=True)):
                    if base not in SCHWA_PHONES or stress != 0:
                        continue
                    for gpc in index.get(chunk, ()):
                        if not gpc.schwa_ok or len(gpc.phonemes) != 1:
                            continue
                        if gpc.phonemes[0] == base:
                            continue  # exact match already added above
                        lattice[(i, j)].append(
                            Step(chunk, (base,), (gpc,), schwa_relaxed=True)
                        )

        # --- split digraphs (a_e, i_e, o_e, u_e, e_e) -------------------------
        # §5.2.4 matches these as "vowel + exactly-one-consonant + final e". They
        # break a plain 2-D DP because the vowel's phoneme and the straddled
        # consonant's phoneme are not adjacent to the vowel's letter. Modelled as
        # ONE composite edge consuming 3 letters and emitting 2 GPCs — which
        # exceeds §5.2.4's literal "0-2 phonemes" cap (docs/DEVIATIONS.md 1.2).
        if i + 3 <= n_letters and word[i] in VOWEL_LETTERS and word[i + 2] == "e":
            if i + 3 != n_letters:
                continue  # the `e` must be word-final
            mid = word[i + 1]
            if mid in VOWEL_LETTERS:
                continue  # "exactly one consonant"
            for split in index.get(f"{word[i]}_e", ()):
                for cons in index.get(mid, ()):
                    combined = split.phonemes + cons.phonemes
                    for j in range(n_phones + 1):
                        if bases[j : j + len(combined)] == combined:
                            lattice[(i, j)].append(
                                Step(word[i : i + 3], combined, (split, cons))
                            )

    return dict(lattice)
