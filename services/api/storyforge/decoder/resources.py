"""The ONLY module in `storyforge.decoder` permitted to touch the filesystem.

Everything else in the package is a pure function of the data these loaders
return. `tests/decoder/test_purity.py` enforces that by AST-scanning every other
module for I/O imports and `open()` calls.

Both loaders are `lru_cache`d, so a process reads each artifact once and every
caller — API, eval CLI, tests — sees byte-identical inputs.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from functools import lru_cache
from importlib import resources

from storyforge.decoder.types import GPC

_DATA = "storyforge.decoder.data"


def _read_text(name: str) -> str:
    return resources.files(_DATA).joinpath(name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_lexicon() -> dict[str, tuple[tuple[str, ...], ...]]:
    """Parse the vendored CMUdict snapshot into ``word -> (pronunciation, ...)``.

    Stress digits are retained here; the aligner strips them per phone and reads
    the digit back for the §5.2.6 schwa rule, which needs to know that a vowel is
    unstressed.

    CMUdict spells pronunciation variants ``word(2)``. Those are collapsed onto
    the base word so §5.2.5's "any pronunciation may pass" sees all of them —
    this is what makes ``read`` decodable at stage 6.
    """
    entries: dict[str, list[tuple[str, ...]]] = {}
    for line in _read_text("cmudict.dict").splitlines():
        line = line.split("#", 1)[0].strip()  # strip trailing comments
        if not line:
            continue
        head, _, rest = line.partition(" ")
        if not rest:
            continue
        word = head
        if word.endswith(")") and "(" in word:
            word = word[: word.rindex("(")]
        entries.setdefault(word, []).append(tuple(rest.split()))
    return {w: tuple(p) for w, p in entries.items()}


def _csv_rows(text: str) -> Iterator[dict[str, str]]:
    # Comment lines let the inventory document itself; csv.DictReader has no
    # notion of them, so filter before parsing.
    body = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    yield from csv.DictReader(body)


@lru_cache(maxsize=1)
def load_gpcs() -> tuple[GPC, ...]:
    """Parse ``gpcs.csv`` into the master inventory, sorted for determinism."""
    gpcs = [
        GPC(
            grapheme=row["grapheme"].strip(),
            phonemes=tuple(row["phonemes"].split()),
            kind=row["kind"].strip(),
            position=row["position"].strip(),
            stage=int(row["stage"]),
            schwa_ok=row["schwa_ok"].strip() == "1",
        )
        for row in _csv_rows(_read_text("gpcs.csv"))
    ]
    seen: set[str] = set()
    for g in gpcs:
        if g.id in seen:
            raise ValueError(f"duplicate GPC id in gpcs.csv: {g.id}")
        seen.add(g.id)
    return tuple(sorted(gpcs))
