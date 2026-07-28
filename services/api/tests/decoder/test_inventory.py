"""Inventory and lexicon integrity.

The decodability claim is only reproducible if the proof surface — the pinned
dictionary and the GPC inventory — cannot drift silently.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from storyforge.decoder import load_gpcs, load_lexicon

DATA = Path(__file__).resolve().parents[2] / "storyforge/decoder/data"
REPO_ROOT = Path(__file__).resolve().parents[4]


# --- pinned artifacts --------------------------------------------------------
def test_vendored_artifacts_match_their_pins():
    """§3: "Pin the dict — it is part of the proof"."""
    pins = (DATA / "PINS.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| `([^`]+)` \| ([\d,]+) \| `([0-9a-f]{64})` \|$", pins, re.M)
    assert rows, "PINS.md has no artifact rows"

    for name, size, expected in rows:
        blob = (DATA / name).read_bytes()
        assert len(blob) == int(size.replace(",", "")), f"{name} size drifted"
        assert hashlib.sha256(blob).hexdigest() == expected, (
            f"{name} content drifted from its pin — the proof surface changed. "
            f"If intentional, re-run scripts/vendor-cmudict.py and review the diff."
        )


def test_lexicon_parses_the_words_the_goldens_depend_on():
    lex = load_lexicon()
    assert len(lex) > 100_000
    # Pronunciation variants must collapse onto the base word, or §5.2.5's
    # "any pronunciation may pass" silently loses candidates and `read` breaks.
    assert len(lex["read"]) == 2
    assert ("R", "IY1", "D") in lex["read"]
    assert "zorp" not in lex
    # The distinction the singer/finger pair rests on.
    assert lex["singer"] == (("S", "IH1", "NG", "ER0"),)
    assert lex["finger"] == (("F", "IH1", "NG", "G", "ER0"),)


def test_lexicon_load_is_idempotent():
    assert load_lexicon() is load_lexicon()


# --- inventory ---------------------------------------------------------------
def test_gpc_ids_are_unique():
    ids = [g.id for g in load_gpcs()]
    assert len(ids) == len(set(ids))


def test_every_stage_unlocks_something():
    stages = {g.stage for g in load_gpcs()}
    # Stage 12 is review only (Appendix A) — it lifts the syllable cap, which is
    # a generation-side filter, not a new GPC.
    assert set(range(1, 12)).issubset(stages)


def test_stage_sets_are_cumulative():
    """Monotonicity (§5.7c) only holds because mastery is a union over stages."""
    from storyforge.decoder import mastered_ids

    for stage in range(1, 12):
        assert mastered_ids(stage) <= mastered_ids(stage + 1)


@pytest.mark.parametrize("gpc_id", ["o|AH", "i|AY", "gh|SIL", "s|SH", "a|EY"])
def test_deliberately_absent_gpcs_stay_absent(gpc_id):
    """These absences are load-bearing.

    `o|AH` in particular: if it were added, "dragon" would become decodable by an
    exact match and `schwa_relaxed` would silently flip to False — the §5.2.6
    golden would still pass its verdict while no longer testing the rule.
    The bare long vowels (`i|AY`, `a|EY`) are what make `night@5` and `cake@4`
    report a *minimal* missing set instead of finding a shortcut.
    """
    assert gpc_id not in {g.id for g in load_gpcs()}


def test_schwa_relaxation_is_bounded_to_single_letters():
    """If vowel teams could relax, the rule becomes a universal solvent."""
    for gpc in load_gpcs():
        if gpc.schwa_ok:
            assert gpc.kind == "single", f"{gpc.id} is schwa_ok but kind={gpc.kind}"
            assert len(gpc.grapheme) == 1


def test_seed_symlink_is_the_same_bytes_as_the_package_copy():
    """docs/DEVIATIONS.md 1.4: `data/seed/gpcs.csv` is a symlink, so `make seed`
    and the validator cannot disagree about the inventory."""
    seed = REPO_ROOT / "data/seed/gpcs.csv"
    if not seed.exists():
        pytest.skip("not running from a repo checkout")
    assert seed.resolve() == (DATA / "gpcs.csv").resolve()
