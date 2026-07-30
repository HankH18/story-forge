#!/usr/bin/env python
"""Re-derive `stories.decodability` from the gate after a VOCABULARY change.

Why this exists (docs/DEVIATIONS.md 8.19). `stories.decodability` is a cached
number: the gate's verdict on the story text, computed once when the story was
written. A sight-word or GPC change moves what the gate says, so after such a
change the cache is stale — exactly as `allowed_words` is stale until
`make precompute` reruns. This is the story-row half of that rebuild.

`tests/test_eval.py::test_the_gate_still_agrees_with_what_the_harness_recorded`
is the check that notices. It compares the stored number against a live
`validate_text`, and it is NOT relaxed to accommodate a data change: the
`justify-test-edit` verdict was CODE IS WRONG (the test was green before the
change and red because of it), so the test stands untouched and the stale rows
are repaired instead. Same shape as the grammar backfill in DEVIATIONS 6.24.

What this deliberately does NOT touch: `eval_run_items.metrics`. That JSONB
holds each story's measurement as recorded AT RUN TIME and is where
`docs/EVAL.md`'s figures come from (§14: the CLI stays canonical). Rewriting it
would make a committed report unreproducible, which is the opposite of the point.
So the historical number survives in the run's own evidence, and the story row
carries what the CURRENT gate says.

Idempotent: a second run selects the same rows, finds them already in agreement,
and writes nothing.

    python scripts/rescore_stored_stories.py [--apply] [--status eval]

Without `--apply` it only reports, so the diff can be read before it is written.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))

from sqlalchemy import text  # noqa: E402

from storyforge.db import SessionLocal  # noqa: E402
from storyforge.decoder import mastered_ids, validate_text  # noqa: E402

TOLERANCE = 5e-4  # matches the test's `pytest.approx(..., abs=5e-4)`

_SQL = """
SELECT s.id, s.stage_id, s.decodability, s.story_words,
       string_agg(g.body, ' ' ORDER BY g.idx) AS body
FROM stories s JOIN story_segments g ON g.story_id = s.id
WHERE s.status = :st
GROUP BY s.id ORDER BY s.id
"""


async def run(status: str, apply: bool) -> int:
    async with SessionLocal() as session:
        rows = (await session.execute(text(_SQL), {"st": status})).all()
        if not rows:
            print(f"no stories at status={status!r} — nothing to do")
            return 0

        sight = {
            stage: frozenset(
                r[0]
                for r in await session.execute(
                    text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": stage}
                )
            )
            for stage in {r.stage_id for r in rows}
        }

        stale, regressions = [], []
        for row in rows:
            now = validate_text(
                row.body,
                mastered_ids(row.stage_id),
                sight[row.stage_id],
                frozenset(row.story_words or []),
            ).decodability
            was = float(row.decodability)
            if abs(now - was) <= TOLERANCE:
                continue
            stale.append((row.id, row.stage_id, was, now))
            if now < was:
                regressions.append((row.id, was, now))

        print(f"{len(rows)} stories at status={status!r}; {len(stale)} disagree with the gate")
        for sid, stage, was, now in stale:
            print(f"  story {sid:>6} stage {stage:>2}  {was:.4f} -> {now:.4f}")

        # A vocabulary ADDITION can only move a word out of the §5.1 denominator,
        # never into it, so every difference must be an improvement. A row that
        # got WORSE means something narrowed the gate, which is §0's forbidden
        # direction — refuse to write rather than quietly record it.
        if regressions:
            print(
                f"\nREFUSING TO WRITE: {len(regressions)} story/stories score LOWER than "
                f"recorded {regressions[:5]}. A vocabulary addition cannot do that, so the "
                f"gate narrowed — investigate before rescoring (§0)."
            )
            return 1

        if not stale:
            print("every stored number already agrees with the gate — nothing to write")
            return 0
        if not apply:
            print("\nreport only. re-run with --apply to write.")
            return 0

        await session.execute(
            text("UPDATE stories SET decodability = :d WHERE id = :i"),
            [{"i": sid, "d": round(now, 4)} for sid, _, _, now in stale],
        )
        await session.commit()
        print(f"\nrescored {len(stale)} rows. eval_run_items.metrics left untouched.")
        return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--status", default="eval", help="stories.status to rescore (default: eval)")
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.status, args.apply)))


if __name__ == "__main__":
    main()
