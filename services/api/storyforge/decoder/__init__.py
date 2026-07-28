"""storyforge.decoder — the decodability validator (§5).

THE CORE. A pure package: no network, no DB, no clock, no framework imports.
Importable identically by the API, the eval CLI and the tests — enforced by
`tests/decoder/test_purity.py`.

    from storyforge.decoder import validate_text, mastered_ids

    report = validate_text("The cat sat.", mastered_ids(1), sight_words={"the"})
    report.decodability  # 1.0

§0: this gate is never weakened to make generation easier. If generation
struggles, fix the prompts and allowlists.
"""

from storyforge.decoder.resources import load_gpcs, load_lexicon
from storyforge.decoder.types import GPC, TextReport, WordReport, WordVerdict
from storyforge.decoder.validate import mastered_ids, validate_text, validate_word

__all__ = [
    "GPC",
    "TextReport",
    "WordReport",
    "WordVerdict",
    "inventory_size",
    "lexicon_size",
    "load_gpcs",
    "load_lexicon",
    "mastered_ids",
    "validate_text",
    "validate_word",
]


def lexicon_size() -> int:
    """Entry count of the vendored CMUdict snapshot — surfaced by /api/health."""
    return len(load_lexicon())


def inventory_size() -> int:
    """GPC count in the master inventory — surfaced by /api/health."""
    return len(load_gpcs())
