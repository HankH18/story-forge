"""Seed the scope & sequence into the database (§4, Appendix A).

Idempotent: running `make seed` twice must not duplicate rows, so every insert
is an upsert keyed on the natural key.

The GPC table is seeded by importing the decoder's own ``load_gpcs()`` rather
than re-parsing ``gpcs.csv``. The database and the validator therefore cannot
disagree about the inventory — there is one parser, not two.
"""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from storyforge.db import SessionLocal
from storyforge.decoder import load_gpcs
from storyforge.models import AllowedWord, Gpc, SightWord, Stage

SEED_DIR = Path(__file__).resolve().parents[3] / "data/seed"


def _rows(name: str) -> list[dict[str, str]]:
    text = (SEED_DIR / name).read_text(encoding="utf-8")
    body = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(body))


async def seed_stages(session) -> int:
    rows = [
        {
            "id": int(r["id"]),
            "name": r["name"],
            "description": r["description"],
            "target_len_min": int(r["target_len_min"]),
            "target_len_max": int(r["target_len_max"]),
            "max_syllables": int(r["max_syllables"]),
        }
        for r in _rows("stages.csv")
    ]
    stmt = insert(Stage).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Stage.id],
            set_={
                c: stmt.excluded[c]
                for c in (
                    "name", "description", "target_len_min",
                    "target_len_max", "max_syllables",
                )
            },
        )
    )
    return len(rows)


async def seed_gpcs(session) -> int:
    """Mirror the decoder's in-memory inventory into `gpcs`."""
    rows = [
        {
            "gpc_id": g.id,
            "grapheme": g.grapheme,
            "phonemes": " ".join(g.phonemes),
            "kind": g.kind,
            "position": g.position,
            "stage_id": g.stage,
            "schwa_ok": g.schwa_ok,
        }
        for g in load_gpcs()
    ]
    stmt = insert(Gpc).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Gpc.gpc_id],
            set_={
                c: stmt.excluded[c]
                for c in ("grapheme", "phonemes", "kind", "position", "stage_id", "schwa_ok")
            },
        )
    )
    return len(rows)


async def seed_sight_words(session) -> int:
    rows = [
        {"stage_id": int(r["stage"]), "word": r["word"].strip().lower()}
        for r in _rows("sight_words.csv")
    ]
    stmt = insert(SightWord).values(rows)
    await session.execute(stmt.on_conflict_do_nothing(constraint="uq_sight_stage_word"))
    return len(rows)


async def run() -> dict[str, int]:
    async with SessionLocal() as session:
        counts = {
            "stages": await seed_stages(session),
            "gpcs": await seed_gpcs(session),
            "sight_words": await seed_sight_words(session),
        }
        await session.commit()
    return counts


def main() -> None:
    counts = asyncio.run(run())
    for table, n in counts.items():
        print(f"  seeded {table:<12} {n:>5} rows")
    # Loud, because a silently empty allowlist is the failure mode that would
    # otherwise surface as "the stories got boring" three phases later.
    print("\nNext: `make precompute` to build allowed_words.")


__all__ = ["AllowedWord", "main", "run"]


if __name__ == "__main__":
    main()
