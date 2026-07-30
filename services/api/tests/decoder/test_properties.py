"""§5.7 property tests (hypothesis)."""

from __future__ import annotations

import string
import subprocess
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from storyforge.decoder import (
    MAX_STAGE,
    load_lexicon,
    mastered_ids,
    validate_text,
    validate_word,
)
from storyforge.decoder.types import WordVerdict

WORDS = sorted(w for w in load_lexicon() if w.isalpha() and 2 <= len(w) <= 9)
SLOW = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _min_stage(word: str) -> int | None:
    """Lowest stage at which ``word`` is decodable, or None."""
    for stage in range(1, MAX_STAGE + 1):
        if validate_word(word, mastered_ids(stage)).verdict is WordVerdict.DECODABLE:
            return stage
    return None


# --- (a) --------------------------------------------------------------------
# §5.7a is phrased over `allowed_words[stage]`, which is a Phase 2 artifact. This
# tests the same invariant more strongly and is available now: a word decodable
# at its minimum stage must NOT be decodable one stage earlier. That is exactly
# what makes the precomputed allowlist trustworthy.
@given(st.sampled_from(WORDS))
@SLOW
def test_min_stage_is_a_genuine_boundary(word):
    stage = _min_stage(word)
    if stage is None:
        return
    assert validate_word(word, mastered_ids(stage)).verdict is WordVerdict.DECODABLE
    if stage > 1:
        assert validate_word(word, mastered_ids(stage - 1)).verdict is not WordVerdict.DECODABLE


# --- (b) --------------------------------------------------------------------
@given(st.sampled_from(WORDS), st.integers(min_value=1, max_value=MAX_STAGE))
@SLOW
def test_validator_is_deterministic(word, stage):
    mastered = mastered_ids(stage)
    assert validate_word(word, mastered).model_dump() == validate_word(word, mastered).model_dump()


def test_deterministic_across_hash_seeds():
    """The real determinism hazard is set/dict iteration order leaking into
    `missing_gpcs`. Two subprocesses with different PYTHONHASHSEED must agree
    byte for byte — an in-process repeat could never catch this."""
    script = (
        "from storyforge.decoder import validate_text, mastered_ids;"
        "r = validate_text('The knight sees a beautiful dragon City finger singer',"
        " mastered_ids(3), frozenset({'the','a'}), frozenset());"
        "print(r.model_dump_json())"
    )
    runs = [
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout
        for seed in ("0", "1", "12345")
    ]
    assert runs[0] == runs[1] == runs[2]


# --- (c) --------------------------------------------------------------------
@given(st.sampled_from(WORDS), st.integers(min_value=1, max_value=MAX_STAGE - 1))
@SLOW
def test_decodability_is_monotonic_in_stage(word, stage):
    """Only holds because stages and sight lists are cumulative — see
    test_inventory.test_stage_sets_are_cumulative."""
    if validate_word(word, mastered_ids(stage)).verdict is WordVerdict.DECODABLE:
        assert validate_word(word, mastered_ids(stage + 1)).verdict is WordVerdict.DECODABLE


@given(st.text(alphabet=string.ascii_lowercase + " .,'", min_size=1, max_size=40))
@SLOW
def test_text_decodability_never_decreases(text):
    lo = validate_text(text, mastered_ids(3)).decodability
    hi = validate_text(text, mastered_ids(MAX_STAGE)).decodability
    assert hi >= lo - 1e-9


# --- crash-freedom on garbage ------------------------------------------------
@given(st.text(min_size=0, max_size=60))
@SLOW
def test_never_raises_on_arbitrary_text(text):
    report = validate_text(text, mastered_ids(6))
    assert 0.0 <= report.decodability <= 1.0
    assert all(w.verdict in WordVerdict for w in report.words)
