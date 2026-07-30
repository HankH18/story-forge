#!/usr/bin/env python
"""Sweep the servable pool for stories that describe a PERSON's body (11.93).

Why this exists. `SYSTEM_RULES` rule 7 (DEVIATIONS 8.29) forbids generation from
describing a person's body, and 8.29 recorded in as many words that it is
PREVENTION ONLY: "`moderate()` still has no REJECT bullet for appearance-based
content, so a story that gets past the prompt is not caught downstream". 11.92
adds that bullet. This script is the other half — the BACKLOG, which prevention
by construction cannot reach.

Why a deterministic scan and not a re-moderation. Re-running §13 over the pool
would cost real money, would be stochastic, and — decisively — would not have
found anything before 11.92, because layer 3 had no bullet for a description.
`moderate.person_body_hits` is a pure function over the story text: free, the
same answer every time, and re-runnable as a gate. It also has no false-negative
mode that a judge has: a body adjective governed by a person noun either is in
the text or is not.

    python scripts/sweep_person_body.py            # report; exit 1 if any hit
    python scripts/sweep_person_body.py --apply    # pull the hits from the pool
    python scripts/sweep_person_body.py --all      # report every status, not just servable

What `--apply` does and does not do. A hit moves to `status='rejected'`, which is
the whole of "stop serving this story" (`SERVABLE_STATUSES` is what `_SERVE_SQL`
filters on). Nothing is DELETED: `answers.question_id` and `questions.story_id`
are both ON DELETE CASCADE, so a DELETE here would destroy the §8 mastery
evidence a promotion may already have been granted on (DEVIATIONS 6.26). The
receipt is written the same shape `run_grammar_backfill` writes: the previous
moderation verdict is preserved under `generation`, and `pulled_at` records WHEN
the story left the pool, so "was it served after it was pulled?" stays answerable.

Idempotent: a re-run finds the pulled stories are no longer servable and selects
nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/api"))

from sqlalchemy import text  # noqa: E402

from storyforge.api.stories import ANSWERABLE_SQL, SERVABLE_STATUSES  # noqa: E402
from storyforge.db import SessionLocal  # noqa: E402
from storyforge.llm.moderate import person_body_hits, proper_names  # noqa: E402

#: `c42bd32` shipped `SYSTEM_RULES` rule 7. Stories written before it were never
#: prevented and (until 11.92) never detected; stories after it are the measure of
#: whether prevention works. Reported per story so the two populations stay
#: distinguishable in the output rather than in a claim.
RULE_7_SHIPPED = "2026-07-29 21:03:24-05"

_SQL = f"""
SELECT s.id, s.stage_id, s.status, t.slug AS theme,
       s.created_at < CAST(:shipped AS timestamptz) AS predates_rule_7,
       string_agg(g.body, ' ' ORDER BY g.idx) AS body
FROM stories s
JOIN themes t ON t.id = s.theme_id
JOIN story_segments g ON g.story_id = s.id
WHERE (:all_statuses OR (s.status = ANY(:statuses) AND {ANSWERABLE_SQL.format(story="s")}))
GROUP BY s.id, t.slug
ORDER BY s.id
"""


async def run(apply: bool, all_statuses: bool) -> int:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text(_SQL),
                {
                    "shipped": RULE_7_SHIPPED,
                    "statuses": list(SERVABLE_STATUSES),
                    "all_statuses": all_statuses,
                },
            )
        ).all()
        if not rows:
            print("no stories to scan")
            return 0

        names = proper_names([r.body for r in rows])
        before = after = 0
        offenders: list[tuple] = []
        for row in rows:
            hits = person_body_hits(row.body, names)
            if row.predates_rule_7:
                before += 1
            else:
                after += 1
            if hits:
                offenders.append((row, hits))

        scanned = "all statuses" if all_statuses else "servable"
        print(
            f"scanned {len(rows)} {scanned} stories — {before} written before "
            f"`SYSTEM_RULES` rule 7 shipped, {after} after"
        )
        pre_hits = sum(1 for row, _ in offenders if row.predates_rule_7)
        print(
            f"  {pre_hits} of {before} before the rule describe a person's body; "
            f"{len(offenders) - pre_hits} of {after} after"
        )
        for row, hits in offenders:
            when = "PRE-rule-7" if row.predates_rule_7 else "post-rule-7"
            print(f"    #{row.id} (stage {row.stage_id} / {row.theme}, {row.status}, {when})")
            for sentence, matched in hits:
                print(f'        [{matched}]  "{sentence}"')

        if not offenders:
            print("  clean")
            return 0
        if not apply:
            print("\n  report only — re-run with --apply to pull these from the pool")
            return 1

        for row, hits in offenders:
            story = (
                await session.execute(
                    text("SELECT gen_meta FROM stories WHERE id = :i"), {"i": row.id}
                )
            ).scalar_one()
            meta = dict(story or {})
            meta["moderation"] = {
                # What was known when the story was written, kept where it can
                # still be read: this sweep is a later, better-informed pass.
                "generation": meta.get("moderation"),
                "approved": False,
                "layer": "scan",
                "reason": "describes a person's body (SYSTEM_RULES rule 7, DEVIATIONS 11.92): "
                + "; ".join(f'"{s}"' for s, _ in hits),
                "well_formed": (meta.get("moderation") or {}).get("well_formed"),
                "source": "person-body-sweep",
                "pulled_at": datetime.now(UTC).isoformat(),
            }
            await session.execute(
                text("UPDATE stories SET status = 'rejected', gen_meta = CAST(:m AS jsonb) "
                     "WHERE id = :i"),
                {"i": row.id, "m": __import__("json").dumps(meta)},
            )
            print(f"    PULLED #{row.id} — status rejected, {len(hits)} sentence(s) recorded")
        await session.commit()
        print(f"\n  {len(offenders)} story/stories pulled. Nothing deleted.")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="pull the hits from the pool")
    ap.add_argument("--all", dest="all_statuses", action="store_true", help="scan every status")
    args = ap.parse_args()
    return asyncio.run(run(args.apply, args.all_statuses))


if __name__ == "__main__":
    raise SystemExit(main())
