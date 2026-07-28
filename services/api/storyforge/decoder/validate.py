"""The §5.1 contract: ``validate_text``.

Pure and deterministic. No network, no DB, no clock. This is the gate the whole
product rests on — §0: it is never weakened to make generation easier.
"""

from __future__ import annotations

from functools import lru_cache

from storyforge.decoder.resources import load_gpcs, load_lexicon
from storyforge.decoder.search import align, count_alignments, minimal_missing
from storyforge.decoder.tokenizer import tokenize
from storyforge.decoder.types import TextReport, WordReport, WordVerdict


@lru_cache(maxsize=16)
def mastered_ids(stage: int) -> frozenset[str]:
    """Cumulative GPC ids mastered at ``stage`` — the union of all stages <= it (§4)."""
    return frozenset(g.id for g in load_gpcs() if g.stage <= stage)


def _normalise(words) -> frozenset[str]:
    return frozenset(w.strip().lower() for w in words if w and w.strip())


def validate_word(
    word: str,
    mastered: frozenset[str],
    sight_words: frozenset[str] = frozenset(),
    story_words: frozenset[str] = frozenset(),
) -> WordReport:
    if word in sight_words:
        return WordReport(word=word, verdict=WordVerdict.SIGHT)
    if word in story_words:
        return WordReport(word=word, verdict=WordVerdict.STORY)

    prons = load_lexicon().get(word)
    if not prons:
        return WordReport(word=word, verdict=WordVerdict.OOV)

    path, n_ok = align(word, prons, mastered)
    if path is not None:
        return WordReport(
            word=word,
            verdict=WordVerdict.DECODABLE,
            alignment=[g.id for step in path for g in step.gpcs],
            schwa_relaxed=any(step.schwa_relaxed for step in path),
            ambiguity_flag=count_alignments(word, prons, mastered) > 1,
            # §5.2.5 lets any pronunciation pass. When only some do, the child may
            # still meet a pronunciation they cannot decode — surfaced, not fixed.
            heteronym_flag=len(prons) > 1 and n_ok < len(prons),
        )

    missing, is_minimal, alignable = minimal_missing(word, prons, mastered)
    return WordReport(
        word=word,
        verdict=WordVerdict.VIOLATION,
        missing_gpcs=missing,
        missing_minimal=is_minimal,
        alignable=alignable,
    )


def validate_text(
    text: str,
    mastered_gpcs: frozenset[str],
    sight_words: frozenset[str] = frozenset(),
    story_words: frozenset[str] = frozenset(),
) -> TextReport:
    """§5.1. ``mastered_gpcs`` holds GPC ids (``"sh|SH"``); use
    :func:`mastered_ids` to derive them from a stage."""
    sight = _normalise(sight_words)
    story = _normalise(story_words)

    reports: list[WordReport] = []
    for token in tokenize(text):
        if not token.is_word:
            reports.append(WordReport(word=token.raw, verdict=WordVerdict.PUNCT))
            continue
        reports.append(validate_word(token.text, mastered_gpcs, sight, story))

    counts = {v.value: 0 for v in WordVerdict}
    for r in reports:
        counts[r.verdict.value] += 1

    # §5.1: sight and story words are excluded from the denominator; punctuation
    # is not scored at all.
    scored = counts["decodable"] + counts["violation"] + counts["oov"]
    decodability = counts["decodable"] / scored if scored else 1.0

    return TextReport(
        words=reports,
        decodability=decodability,
        violations=[r for r in reports if r.verdict is WordVerdict.VIOLATION],
        story_word_count=counts["story"],
        counts=counts,
        scored_word_count=scored,
        schwa_relaxed_count=sum(1 for r in reports if r.schwa_relaxed),
        ambiguity_count=sum(1 for r in reports if r.ambiguity_flag),
        heteronym_count=sum(1 for r in reports if r.heteronym_flag),
        unalignable_count=sum(1 for r in reports if not r.alignable),
    )
