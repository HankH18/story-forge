"""Core value types for the decoder. Pure data — no I/O, no framework."""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple

from pydantic import BaseModel, Field


class WordVerdict(str, Enum):
    """§5.1. Deliberately six values — no seventh.

    A word CMUdict knows but the inventory cannot align at *any* stage is a
    VIOLATION with an empty ``missing_gpcs`` and ``alignable=False``, not a new
    verdict. A rising unalignable rate in eval is the signal that the inventory
    is too small (§18).
    """

    DECODABLE = "decodable"
    SIGHT = "sight"
    STORY = "story"
    VIOLATION = "violation"
    OOV = "oov"
    PUNCT = "punct"


class GPC(NamedTuple):
    """A grapheme-phoneme correspondence.

    Hashable and ordered, so sets of GPCs iterate deterministically — the
    determinism property test (§5.7b) depends on this.
    """

    grapheme: str
    phonemes: tuple[str, ...]
    kind: str
    position: str
    stage: int
    schwa_ok: bool

    @property
    def id(self) -> str:
        """Stable identifier used in reports and golden fixtures, e.g. ``sh|SH``."""
        return f"{self.grapheme}|{'.'.join(self.phonemes) or 'SIL'}"


class Step(NamedTuple):
    """One edge in an alignment: letters consumed, phonemes consumed, GPCs used.

    ``gpcs`` is a tuple because a split-digraph step contributes two (the split
    vowel and the consonant it straddles).
    """

    letters: str
    phonemes: tuple[str, ...]
    gpcs: tuple[GPC, ...]
    schwa_relaxed: bool = False


class WordReport(BaseModel):
    """§5.1, plus additive fields (docs/DEVIATIONS.md 1.5) that surface honest
    limitations rather than hiding them."""

    word: str
    verdict: WordVerdict
    alignment: list[str] = Field(default_factory=list)
    missing_gpcs: list[str] = Field(default_factory=list)
    ambiguity_flag: bool = False
    schwa_relaxed: bool = False

    # True when the word has an inventory path at some stage. False means the
    # inventory cannot spell it at all (e.g. "beautiful", "of").
    alignable: bool = True
    # False when the minimal-missing-set search hit its bound and fell back to a
    # non-minimal answer, so eval never mistakes an approximation for a proof.
    missing_minimal: bool = True
    # True when the word has multiple pronunciations and only some are decodable
    # — §5.2.5 lets any pronunciation pass, which can over-credit heteronyms.
    heteronym_flag: bool = False


class TextReport(BaseModel):
    """§5.1."""

    words: list[WordReport]
    decodability: float
    violations: list[WordReport]
    # Token occurrences of story words — equals counts["story"]. Repetition is
    # good in a decodable reader, so this is a reporting metric, NOT the gate.
    story_word_count: int
    # The DISTINCT story words. §4's "<=3 per story" is a limit on how many new
    # picture-chip words a child must absorb, so this is what §7 gates on —
    # using "dragon" four times is still one story word.
    distinct_story_words: list[str] = Field(default_factory=list)

    # Additive reporting surface for the eval harness (§14).
    counts: dict[str, int] = Field(default_factory=dict)
    scored_word_count: int = 0
    schwa_relaxed_count: int = 0
    ambiguity_count: int = 0
    heteronym_count: int = 0
    unalignable_count: int = 0
