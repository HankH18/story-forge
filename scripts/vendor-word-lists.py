#!/usr/bin/env python
"""Vendor the two word lists §5.4's corpus choice turned out to need.

DEVIATIONS 11.7-11.9. §5.4 specified "intersect with SUBTLEX top ~20k", and
`data/frequency-en.csv` is exactly that: adult film-subtitle frequency. It proves
a word is common in adult speech and says nothing about whether a five-year-old
knows it, which is how `amid`, `lebron` and `nixon` reached a stage-1 word bank.

Two lists answer the two halves of that:

`data/child-vocabulary.txt`
    The Dale-Chall 3,000 familiar words — words known to at least 80% of
    fourth-graders. A direct measurement of the question §5.4 failed to ask.
    Source: `textstat`'s `easy_words.txt`, the standard machine-readable form.

`data/common-words-en.txt`
    The LOWERCASE headwords of the Hunspell en_US dictionary. The signal is
    capitalisation in the source `.dic`, which is precisely what the frequency
    list threw away — `wordfreq` is lowercased, so `Nixon` and `nixon` are
    indistinguishable in it. An entry that appears only capitalised is a proper
    noun; `data/common-words-en.txt` is the complement, so membership means "a
    common English word, not a name".

Like CMUdict and the frequency list, these decide which words may reach a
generation prompt at all, so they are proof surface: vendored and SHA-pinned
rather than resolved at runtime, and pinned in the decoder's PINS.md so
`tests/decoder/test_inventory.py` asserts them on every run.

Run:  cd services/api && uv run python ../../scripts/vendor-word-lists.py
"""

from __future__ import annotations

import hashlib
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "services/api/storyforge/decoder/data/PINS.md"

CHILD_URL = (
    "https://raw.githubusercontent.com/textstat/textstat/main/textstat/resources/en/easy_words.txt"
)
# The LibreOffice dictionaries repo is the upstream of the en_US Hunspell
# dictionary that ships with LibreOffice, Firefox and Chrome.
HUNSPELL_URL = "https://raw.githubusercontent.com/LibreOffice/dictionaries/master/en/en_US.dic"


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as fh:  # noqa: S310 - pinned https URL
        return fh.read().decode("utf-8", errors="replace")


def _write_sorted(dest: Path, header: list[str], words: set[str]) -> None:
    """One word per line, sorted, with a provenance header.

    ONE WORD PER LINE is the same invariant `data/blocklist.txt` carries: a
    space-separated rewrite of that file once loaded 22 entries instead of 240 and
    the only symptom was a row count (DEVIATIONS 6.15, 8.17). Lines and entries
    are kept equal here for the same reason.
    """
    body = "\n".join(f"# {line}".rstrip() for line in header)
    dest.write_text(body + "\n\n" + "\n".join(sorted(words)) + "\n", encoding="utf-8")


def build_child_vocabulary(raw: str) -> set[str]:
    """Dale-Chall familiar words, alphabetic entries only.

    The published list contains a handful of hyphenated and apostrophised forms
    (`bow-wow`, `aren't`). `allowed_words` holds single tokens as the validator
    tokenises them, so those cannot match anything and are dropped rather than
    stored as dead rows.
    """
    return {w for w in (line.strip().lower() for line in raw.split()) if w.isalpha()}


def build_common_words(raw: str) -> set[str]:
    """Hunspell headwords that appear LOWERCASE, i.e. common words not names.

    A `.dic` line is `headword/AFFIXFLAGS`; the first line is the entry count.
    A word is kept if any of its entries is lowercase, so `march` (month and verb)
    survives while `Nixon` does not.
    """
    lower: set[str] = set()
    upper: set[str] = set()
    for line in raw.splitlines()[1:]:
        word = line.split("/")[0].strip()
        if not word or not word.isalpha():
            continue
        (lower if word[0].islower() else upper).add(word.lower())
    # `upper - lower` is the proper-noun set; report it so the split is visible.
    print(f"  hunspell: {len(lower):,} lowercase, {len(upper - lower):,} capitalised-only")
    return lower


def pin(dest: Path) -> None:
    blob = dest.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    rel = dest.relative_to(ROOT)
    row = f"| `{rel}` | {len(blob):,} | `{digest}` |"
    text = PINS.read_text(encoding="utf-8")
    pattern = rf"^\| `{re.escape(str(rel))}` \|.*$"
    if re.search(pattern, text, flags=re.M):
        text = re.sub(pattern, row, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + row + "\n"
    PINS.write_text(text, encoding="utf-8")
    print(f"  {rel}  {len(blob):,} bytes  sha256 {digest}")


def main() -> None:
    child = ROOT / "data/child-vocabulary.txt"
    words = build_child_vocabulary(_fetch(CHILD_URL))
    _write_sorted(
        child,
        [
            "Dale-Chall 3,000 familiar words: known to >=80% of fourth-graders.",
            "",
            "§5.4 specified SUBTLEX (adult film-subtitle frequency) as the vocabulary",
            "filter. It proves a word is common in ADULT speech and says nothing about",
            "whether a five-year-old knows it -- see docs/DEVIATIONS.md 11.7-11.8. This",
            "list is the positive register filter that replaces it.",
            "",
            f"Vendored by scripts/vendor-word-lists.py from {CHILD_URL}",
            "(textstat's easy_words.txt, the standard machine-readable form of the list).",
            "SHA-pinned in storyforge/decoder/data/PINS.md. ONE WORD PER LINE.",
            "",
            "Matching is morphology-aware: a listed word's regular inflections count as",
            "familiar too, which is Dale-Chall's own convention. `precompute.inflect`",
            "generates them, so `cats`/`maps`/`sits` need no line here.",
        ],
        words,
    )
    print(f"child vocabulary: {len(words):,} words")
    pin(child)

    common = ROOT / "data/common-words-en.txt"
    words = build_common_words(_fetch(HUNSPELL_URL))
    _write_sorted(
        common,
        [
            "Common English words: Hunspell en_US headwords that appear LOWERCASE.",
            "",
            "The proper-noun filter (docs/DEVIATIONS.md 11.9). `data/frequency-en.csv`",
            "is lowercased, so `Nixon` and `nixon` are indistinguishable in it; a",
            "Hunspell .dic keeps the case, and an entry that appears only capitalised is",
            "a name. This file is the complement: membership means 'a common English",
            "word, not a proper noun'. Words absent entirely (`lebron`, `aca`, `cas`)",
            "are excluded by the same test.",
            "",
            f"Vendored by scripts/vendor-word-lists.py from {HUNSPELL_URL}",
            "(upstream of the dictionary shipped with LibreOffice, Firefox and Chrome).",
            "SHA-pinned in storyforge/decoder/data/PINS.md. ONE WORD PER LINE.",
        ],
        words,
    )
    print(f"common words: {len(words):,} words")
    pin(common)


if __name__ == "__main__":
    main()
