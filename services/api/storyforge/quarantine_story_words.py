"""Pull the stories whose picture chip no free repair can fix (§5.5, DEVIATIONS 31.3).

`--check-story-words` names two populations and they have opposite remedies:

* **wasted** — a chip on a word the reader can ALREADY decode. Free to remove:
  dropping it leaves a word the child can sound out, which is the product's whole
  purpose. `storyforge.warm --story-word-repair` owns this half and touches no
  story text.
* **far** — a chip more than `STORY_WORD_MAX_LOOKAHEAD` stages ahead of the
  reader. **There is nothing to UPDATE.** The word is in the text either way, so
  dropping the chip leaves a word the child cannot decode, as plain text, with no
  picture and no way in — strictly worse than the wrong chip. The story has to
  leave the pool and be replaced.

This module is the second half, and it is the whole of it: it re-statuses, it
never deletes, and it never generates. **Refilling is a separate, PAID
`python -m storyforge.warm`** and is deliberately not run from here — a remedy
that quietly started spending money would be the wrong shape, and §7's coverage
floor is a different gate's business.

Why a module rather than another `warm.py` flag, given that is where
`--story-word-repair`, `--mcq2-repair` and `--reselect-art` all live: this is a
ONE-SHOT remediation tied to a single deviation, not a standing gate, and
`warm.py` is already 5,000 lines. It has to be inside the package rather than in
`scripts/` because `services/api/Dockerfile` copies `storyforge/` and `data/` and
nothing else — a remediation an operator cannot run in the container they actually
deployed is not a remediation.

    python -m storyforge.quarantine_story_words --dry-run   # free, writes nothing
    python -m storyforge.quarantine_story_words             # free, re-statuses
    python -m storyforge.warm --dry-run --stages .. --themes ..   # prices the refill
    python -m storyforge.warm --stages .. --themes ..             # SPENDS
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.pipeline import STORY_WORD_MAX_LOOKAHEAD
from storyforge.warm import TARGET_PER_PAIR, chip_offences, pool_coverage

#: The pull record, at the TOP level of `gen_meta` and never under `enrichment` —
#: `_record_enrichment` replaces that sub-object wholesale by design, which erased
#: four rows of two real children's history once already (DEVIATIONS 11.117).
QUARANTINE_KEY = "story_word_quarantine"

#: `at` is not decoration. `tests/test_phase3.py::_PULLED_AT` coalesces every pull
#: path into one SQL expression so that "was this rejected row served AFTER it was
#: pulled" stays answerable across producers, and that test's FOURTH assertion
#: fails any recognised pull record carrying no timestamp — exactly so a new
#: producer cannot widen the coalesce into a pass. The first version of this record
#: had no `at`, and the suite caught it rather than the reviewer.
_REASON = (
    f"picture chip more than {STORY_WORD_MAX_LOOKAHEAD} stages ahead of the reader "
    "(§5.5). The chip is load-bearing — the word is in the text either way — so "
    "there is no free repair and the story is replaced rather than edited."
)


async def far_chip_stories(session, only: Sequence[int] | None = None) -> dict[int, list[str]]:
    """`{story_id: [word, ...]}` for the OVER-the-band half only.

    The split is read from `ChipOffence.wasted` rather than recomputed, so this and
    `--story-word-repair` can never disagree about which population they own.
    Quarantining a story over a *wasted* chip would throw away a servable story to
    fix a bookkeeping error — the "data fix disguised as a purge" DEVIATIONS 22.9
    refused.
    """
    offences, _ = await chip_offences(session)
    far: dict[int, list[str]] = {}
    for offence in offences:
        if offence.wasted or (only is not None and offence.story_id not in only):
            continue
        far.setdefault(offence.story_id, []).append(offence.word)
    return {sid: sorted(set(words)) for sid, words in far.items()}


async def quarantine(session, far: dict[int, list[str]]) -> list[tuple[int, int, str, list[str]]]:
    """Re-status each story and record WHY. Returns `(id, stage, previous_status, words)`.

    **Nothing is deleted, and that is load-bearing rather than tidy.**
    `answers.question_id` is `ON DELETE CASCADE` and `questions.story_id` cascades
    from `stories`, so a DELETE here would erase a real child's recorded history to
    fix our defect. A status change leaves every `readings` and `answers` row
    intact and simply stops `_SERVE_SQL` offering the story.

    Idempotent: the write removes the row from `SERVABLE_STATUSES`, and
    `chip_offences` only reads servable stories, so a second run selects nothing.
    """
    if not far:
        return []
    rows = (
        await session.execute(
            text("SELECT id, stage_id, status FROM stories WHERE id = ANY(:ids) ORDER BY id"),
            {"ids": sorted(far)},
        )
    ).all()
    out: list[tuple[int, int, str, list[str]]] = []
    for row in rows:
        await session.execute(
            text(
                # `CAST(:key AS text)` is not decoration: `jsonb_build_object` is
                # variadic "any", so Postgres cannot infer a bound parameter's
                # type there and raises IndeterminateDatatype. Caught by
                # `tests/test_story_word_quarantine.py`, not by a reviewer.
                "UPDATE stories SET status = 'rejected', "
                "gen_meta = coalesce(gen_meta, '{}'::jsonb) "
                "         || jsonb_build_object(CAST(:key AS text), CAST(:meta AS jsonb)) "
                "WHERE id = :id"
            ),
            {
                "id": row.id,
                "key": QUARANTINE_KEY,
                "meta": json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "gate": "storyforge.warm --check-story-words",
                        "deviation": "31.3",
                        "previous_status": row.status,
                        "words": far[row.id],
                        "reason": _REASON,
                    }
                ),
            },
        )
        out.append((row.id, row.stage_id, row.status, far[row.id]))
    return out


async def _history(session) -> tuple[int, int, int]:
    """`(quarantined rows, readings, answers)` still present. The no-delete proof."""
    rows = (
        await session.execute(
            text("SELECT count(*) FROM stories WHERE gen_meta ? :k"), {"k": QUARANTINE_KEY}
        )
    ).scalar_one()
    readings = (
        await session.execute(
            text(
                "SELECT count(*) FROM readings r JOIN stories s ON s.id = r.story_id "
                "WHERE s.gen_meta ? :k"
            ),
            {"k": QUARANTINE_KEY},
        )
    ).scalar_one()
    answers = (
        await session.execute(
            text(
                "SELECT count(*) FROM answers a JOIN questions q ON q.id = a.question_id "
                "JOIN stories s ON s.id = q.story_id WHERE s.gen_meta ? :k"
            ),
            {"k": QUARANTINE_KEY},
        )
    ).scalar_one()
    return rows, readings, answers


async def run(dry_run: bool, only: Sequence[int] | None = None) -> None:
    """Report, act, then RE-READ the pool and report the residual (11.84's rule).

    Coverage is printed before and after, and that is the point of the command
    rather than a courtesy. These stories cluster: on the pool this was written
    against, the twelve were eleven space stories and one animals, and pulling them
    took (stage 1, space) and (stage 2, space) to ZERO servable stories — which
    §7's ladder answers silently, by serving another theme, and reports to nobody
    (DEVIATIONS 8.40-8.48). An operator who runs this and stops has made the pool
    worse, so the refill command is printed with the word SPENDS on it.
    """
    async with SessionLocal() as session:
        before, total = await chip_offences(session)
        far = await far_chip_stories(session, only)
        far_slots = len([o for o in before if not o.wasted])

        print(
            f"\nSTORY-WORD QUARANTINE — {far_slots} of {total} servable chip slot(s) sit "
            f"more than {STORY_WORD_MAX_LOOKAHEAD} stages ahead of their reader, "
            f"over {len(far)} story/stories"
        )
        if not far:
            print(
                "  nothing to pull. Every remaining offence is the FREE repair's "
                "(`python -m storyforge.warm --story-word-repair`), or the pool is clean."
            )
            return

        rows = (
            await session.execute(
                text(
                    "SELECT s.id, s.stage_id, s.status, t.slug FROM stories s "
                    "JOIN themes t ON t.id = s.theme_id WHERE s.id = ANY(:i) ORDER BY s.id"
                ),
                {"i": sorted(far)},
            )
        ).all()
        for row in rows:
            print(
                f"    story {row.id} stage {row.stage_id} / {row.slug} "
                f"({row.status}) — {far[row.id]}"
            )

        if dry_run:
            print("\n  --dry-run: nothing written. Re-run without it to quarantine.")
            return

        pulled = await quarantine(session, far)
        await session.commit()
        after, total_after = await chip_offences(session)
        kept, readings, answers = await _history(session)
        coverage = await pool_coverage(session)

    print(f"\n  pulled {len(pulled)} story/stories from the servable pool.")
    print(
        f"  NOTHING DELETED: {kept} quarantined row(s) still exist, with {readings} "
        f"reading(s) and {answers} answer(s) preserved."
    )
    print(
        f"  residual: {len(after)} of {total_after} slot(s) still out of band "
        f"(was {len(before)} of {total})."
    )

    # `PairDepth.servable`, NOT `.depth`. This read `.depth` and raised
    # AttributeError on the one path that matters: the coverage warning fires only
    # when `below` is non-empty, so every green run exercised the `else` branch and
    # the bug was invisible until a real quarantine emptied a pair in production.
    # The quarantine had already committed by then, so the operator lost the refill
    # instruction for a pool that was, at that moment, actually short.
    below = [p for p in coverage if p.servable < TARGET_PER_PAIR]
    if below:
        print(f"\n  COVERAGE: {len(below)} (stage, theme) pair(s) now below {TARGET_PER_PAIR}:")
        for pair in below:
            print(f"    stage {pair.stage_id} / {pair.theme_slug}: {pair.servable}")
        stages = ",".join(str(s) for s in sorted({p.stage_id for p in below}))
        themes = ",".join(sorted({p.theme_slug for p in below}))
        print(
            "  A child at an empty pair is served §7's fallback — another theme, or a "
            "lower stage — silently.\n"
            "  REFILL, and it SPENDS MONEY:\n"
            f"    python -m storyforge.warm --dry-run --stages {stages} --themes {themes}"
            "   # prices it\n"
            f"    python -m storyforge.warm --stages {stages} --themes {themes}\n"
            "  Then `--check-coverage`, then `--check-story-words`."
        )
    else:
        print(f"\n  COVERAGE: every pair still holds >= {TARGET_PER_PAIR}. No refill needed.")


def main() -> None:
    p = argparse.ArgumentParser(prog="storyforge.quarantine_story_words")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="name the stories and write nothing. Free either way — no API call.",
    )
    p.add_argument("--only", help="comma-separated story ids, for a partial run")
    a = p.parse_args()
    only = [int(x) for x in a.only.split(",")] if a.only else None
    asyncio.run(run(a.dry_run, only))


if __name__ == "__main__":
    main()
