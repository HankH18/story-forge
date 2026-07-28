"""§5.7 golden cases — the Phase 1 Definition of Done."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from storyforge.decoder import mastered_ids, validate_text, validate_word

from .conftest import sight_words

CASES = yaml.safe_load((Path(__file__).parent / "golden.yaml").read_text(encoding="utf-8"))
WORD_CASES = [c for c in CASES if "word" in c]
TEXT_CASES = [c for c in CASES if "text" in c]


@pytest.mark.parametrize("case", WORD_CASES, ids=lambda c: c["id"])
def test_golden_word(case):
    report = validate_word(
        case["word"],
        mastered_ids(case["stage"]),
        sight_words(case["stage"]),
        frozenset(case.get("story_words", [])),
    )

    assert report.verdict.value == case["expect"], (
        f"{case['word']}@{case['stage']}: expected {case['expect']}, "
        f"got {report.verdict.value} (missing={report.missing_gpcs})"
    )

    if "missing" in case:
        assert report.missing_gpcs == case["missing"]

    # Asserting the path pins the reasoning, not just the answer.
    if "alignment" in case:
        assert report.alignment == case["alignment"]

    for flag, expected in case.get("flags", {}).items():
        assert getattr(report, flag) == expected, f"{flag} on {case['word']}"


@pytest.mark.parametrize("case", TEXT_CASES, ids=lambda c: c["id"])
def test_golden_text(case):
    report = validate_text(
        case["text"],
        mastered_ids(case["stage"]),
        sight_words(case["stage"]),
        frozenset(case.get("story_words", [])),
    )
    by_word = {w.word: w for w in report.words}

    for word, expected in case["expect_verdicts"].items():
        assert by_word[word].verdict.value == expected, word

    for word, missing in case.get("expect_missing", {}).items():
        assert by_word[word].missing_gpcs == missing, word

    assert report.decodability == pytest.approx(case["expect_decodability"])
    assert report.story_word_count == case["expect_story_words"]
    assert report.counts["oov"] == case["expect_oov"]
