"""Shared decoder-test fixtures."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SEED = REPO_ROOT / "data/seed"


@lru_cache(maxsize=1)
def _sight_by_stage() -> dict[int, set[str]]:
    rows = [
        ln
        for ln in (SEED / "sight_words.csv").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    by_stage: dict[int, set[str]] = {}
    for row in csv.DictReader(rows):
        by_stage.setdefault(int(row["stage"]), set()).add(row["word"].strip().lower())
    return by_stage


def sight_words(stage: int) -> frozenset[str]:
    """Cumulative sight-word allowlist at ``stage`` (§4)."""
    by_stage = _sight_by_stage()
    return frozenset().union(*(by_stage.get(s, set()) for s in range(1, stage + 1)))
