"""Allowed-vocabulary precompute (§5.4).

Runs every candidate word through the validator per stage and writes
``allowed_words``. The output is three things at once: input to the generation
prompt (§6), a browsable admin artifact, and a demo exhibit — "here is every word
a stage-3 reader is permitted to see, machine-derived."

    frequency-en.csv (top ~20k, pinned)
      └─ ∩ CMUdict ────────────────────► candidates
           └─ blocklist (EXACT match) ──► filtered
                └─ min_stage(word) ─────► first stage that can decode it
                     └─ syllable cap ───► per-stage rows
                          └─ staging table ──[one transaction]──► allowed_words

Two deliberate choices:

**Word-outer, stage-inner.** ``build_lattice`` is ``lru_cache(maxsize=8192)`` but
there are ~19k candidates. Looping stage-outer would evict every lattice between
passes and rebuild each word twelve times. Word-outer builds each lattice once
while it is hot.

**Staging table, then one transaction.** A reader must never observe a partial
allowlist: a truncated list produces duller stories while every metric still
reports success — silent failure, the worst shape for this product. All rows are
computed and staged first; the live table is swapped in a single transaction, so
a crash mid-computation leaves the previous allowlist untouched.
"""

from __future__ import annotations

import asyncio
import csv
import time
from pathlib import Path

from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.decoder import load_lexicon, mastered_ids
from storyforge.decoder.lexicon_util import min_syllables
from storyforge.decoder.search import align

DATA = Path(__file__).resolve().parents[3] / "data"
MAX_STAGE = 12


def load_blocklist() -> frozenset[str]:
    """Exact-match blocklist (§5.4). See data/blocklist.txt for why not substring."""
    lines = (DATA / "blocklist.txt").read_text(encoding="utf-8").splitlines()
    return frozenset(
        ln.strip().lower() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")
    )


def load_frequency() -> list[tuple[int, str]]:
    """(rank, word) from the vendored, SHA-pinned frequency list."""
    with (DATA / "frequency-en.csv").open(encoding="utf-8") as fh:
        return [(int(r["rank"]), r["word"]) for r in csv.DictReader(fh)]


def compute_rows(max_syllables: dict[int, int]) -> list[dict]:
    """Pure computation — no database. Returns allowed_words rows.

    Separated from persistence so the filtering logic is testable without
    Postgres, and so a slow database can never be mistaken for a slow validator.
    """
    lexicon = load_lexicon()
    blocked = load_blocklist()
    rows: list[dict] = []

    for rank, word in load_frequency():
        if word in blocked:
            continue
        prons = lexicon.get(word)
        if not prons:
            continue  # OOV against the pinned dictionary — conservative, per §18

        syllables = min_syllables(prons)

        # min_stage: first stage whose mastered set admits a complete alignment.
        # One lattice per word answers all twelve stages (the lru_cache is why
        # this loop is word-outer).
        first = next(
            (s for s in range(1, MAX_STAGE + 1) if align(word, prons, mastered_ids(s))[0]),
            None,
        )
        if first is None:
            continue  # the inventory cannot spell it at any stage

        for stage in range(first, MAX_STAGE + 1):
            if syllables > max_syllables.get(stage, 2):
                continue
            rows.append(
                {"stage_id": stage, "word": word, "syllables": syllables, "freq_rank": rank}
            )

    return rows


async def persist(session, rows: list[dict]) -> None:
    """Stage every row, then swap in one transaction (see module docstring)."""
    await session.execute(
        text(
            "CREATE TEMP TABLE allowed_staging ("
            "stage_id int, word varchar(64), syllables int, freq_rank int"
            ") ON COMMIT DROP"
        )
    )
    if rows:
        await session.execute(
            text(
                "INSERT INTO allowed_staging (stage_id, word, syllables, freq_rank) "
                "VALUES (:stage_id, :word, :syllables, :freq_rank)"
            ),
            rows,
        )
    # Single transaction: readers see the whole old allowlist or the whole new
    # one. Never a partial.
    await session.execute(text("DELETE FROM allowed_words"))
    await session.execute(
        text(
            "INSERT INTO allowed_words (stage_id, word, syllables, freq_rank) "
            "SELECT stage_id, word, syllables, freq_rank FROM allowed_staging"
        )
    )


async def run() -> dict[int, int]:
    async with SessionLocal() as session:
        stages = (
            await session.execute(text("SELECT id, max_syllables FROM stages ORDER BY id"))
        ).all()
        if not stages:
            raise SystemExit("no stages in the database — run `make seed` first")
        max_syllables = {row[0]: row[1] for row in stages}

        started = time.perf_counter()
        rows = compute_rows(max_syllables)
        computed = time.perf_counter() - started

        await persist(session, rows)
        await session.commit()

    counts: dict[int, int] = {}
    for row in rows:
        counts[row["stage_id"]] = counts.get(row["stage_id"], 0) + 1
    counts["_seconds"] = round(computed, 2)  # type: ignore[index]
    return counts


def main() -> None:
    counts = asyncio.run(run())
    seconds = counts.pop("_seconds")  # type: ignore[arg-type]
    print(f"{'stage':>5} {'words':>8}")
    for stage in sorted(counts):
        print(f"{stage:>5} {counts[stage]:>8,}")
    total = sum(counts.values())
    print(f"\n{total:,} rows across {len(counts)} stages in {seconds}s (§16 budget: 5 min)")
    # §18: a stage with almost no vocabulary starves generation. Say so loudly
    # rather than letting it surface later as "the stories got boring".
    thin = [s for s, n in counts.items() if n < 150]
    if thin:
        print(f"WARNING: stages {thin} have under 150 words — generation may starve (§18)")


if __name__ == "__main__":
    main()
