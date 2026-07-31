"""Pull the stories whose words the CURRENT allowlist no longer permits (DEVIATIONS 32.5).

`--check-vocabulary` is the one gate with **no free repair**, and its own hint says
why: a prompt can be re-derived (`--backfill`) and a leaking question can be removed
(`--mcq2-repair`), but a word sits inside a sentence. Dropping it leaves a hole and
swapping it is a rewrite, which is a generation call. So this module is the
re-status half, and it is the whole of it: it never deletes, never edits story text
and never generates. **Refilling is a separate, PAID `python -m storyforge.warm`**
and is deliberately not run from here — a remedy that quietly started spending money
would be the wrong shape (the same argument `quarantine_story_words` makes).

**Coverage is the reason this command prints more than it does.** These stories
cluster by construction: the offences come from a precomputed input moving, and one
`make warm` run fills one band of stages, so an entire (stage, theme) pair can leave
the pool at once. Measured on the pool this was written against, the eleven
WRONG-STAGE stories were **nine of stage 5 — every servable story at that stage, in
all three themes** — and §7's ladder answers an empty pair silently, by serving
another theme or a lower stage, and reports it to nobody (8.40-8.48). An operator
who runs this and stops has made the pool worse than they found it, so the refill
command is printed with the word SPENDS on it and the shortfall is computed for
them.

    python -m storyforge.quarantine_vocabulary --dry-run   # free, writes nothing
    python -m storyforge.quarantine_vocabulary             # free, re-statuses
    python -m storyforge.warm --dry-run --stages .. --themes ..   # prices the refill
    python -m storyforge.warm --stages .. --themes ..             # SPENDS

Why a module rather than another `warm.py` flag: same answer as 31.3. This is a
one-shot remediation of a backlog, not a standing gate, and `warm.py` is already
5,000 lines. It is inside the package rather than in `scripts/` because
`services/api/Dockerfile` copies `storyforge/` and `data/` and nothing else — a
remediation an operator cannot run in the container they deployed is not one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.warm import TARGET_PER_PAIR, pool_coverage, vocabulary_offences

#: The pull record, at the TOP level of `gen_meta` and never under `enrichment` —
#: `_record_enrichment` replaces that sub-object wholesale by design, which erased
#: four rows of two real children's history once already (DEVIATIONS 11.117).
#:
#: The SAME key an earlier one-shot sweep used (22.6, 13 rows still in the pool) and
#: the same record shape, so `tests/test_phase3.py::_PULL_PATHS` — which already
#: registers `vocabulary_quarantine` — keeps speaking about one population instead
#: of two. A second key for the same reason would make "was this row pulled?" a
#: question with two answers.
QUARANTINE_KEY = "vocabulary_quarantine"

#: `at` is not decoration. `tests/test_phase3.py::_PULLED_AT` coalesces every pull
#: path into one SQL expression so "was this rejected row served AFTER it was
#: pulled" stays answerable across producers, and that test's FOURTH assertion fails
#: any recognised pull record carrying no timestamp.
_REASON = (
    "the current `allowed_words` does not permit this word at this story's stage. A "
    "precomputed input moved after the story was generated (e.g. DEVIATIONS 21.2's "
    "syllable cap, which counts the pronunciations that actually ALIGN at the stage) "
    "and nothing re-read the pool. There is no free repair: the word is load-bearing "
    "inside a sentence, so the story is replaced rather than edited."
)


async def offending_stories(
    session, only: Sequence[int] | None = None
) -> dict[int, list[str]]:
    """`{story_id: [word, ...]}` for every servable story `--check-vocabulary` names.

    **Every class, not one.** `classify_offence` sorts the REPORT and is explicitly
    documented as never deciding the exit code, because "a misclassified word is
    still an offence and still fails the gate" — so selecting on the class here
    would make a heuristic load-bearing in a command that re-statuses rows. The
    remedy is the same for all three classes anyway: there is no free repair for any
    of them.

    Read from `vocabulary_offences` rather than recomputed, so this and the gate can
    never disagree about which stories they are talking about.
    """
    census = await vocabulary_offences(session)
    out: dict[int, list[str]] = {}
    for offence in census.offences:
        if only is not None and offence.story_id not in only:
            continue
        out.setdefault(offence.story_id, []).append(offence.word)
    return {sid: sorted(set(words)) for sid, words in out.items()}


async def quarantine(
    session, offending: dict[int, list[str]]
) -> list[tuple[int, int, str, list[str]]]:
    """Re-status each story and record WHY. Returns `(id, stage, previous_status, words)`.

    **Nothing is deleted, and that is load-bearing rather than tidy.**
    `answers.question_id` is `ON DELETE CASCADE` and `questions.story_id` cascades
    from `stories`, so a DELETE here would erase a real child's recorded history to
    fix our defect. A status change leaves every `readings` and `answers` row intact
    and simply stops `_SERVE_SQL` offering the story.

    Idempotent: the write removes the row from `SERVABLE_STATUSES`, and
    `vocabulary_offences` reads servable stories only, so a second run selects
    nothing.
    """
    if not offending:
        return []
    rows = (
        await session.execute(
            text("SELECT id, stage_id, status FROM stories WHERE id = ANY(:ids) ORDER BY id"),
            {"ids": sorted(offending)},
        )
    ).all()
    out: list[tuple[int, int, str, list[str]]] = []
    for row in rows:
        await session.execute(
            text(
                # `CAST(:key AS text)` is not decoration: `jsonb_build_object` is
                # variadic "any", so Postgres cannot infer a bound parameter's type
                # there and raises IndeterminateDatatype (DEVIATIONS 31.6).
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
                        "gate": "storyforge.warm --check-vocabulary",
                        "deviation": "32.5",
                        "previous_status": row.status,
                        "words": offending[row.id],
                        "reason": _REASON,
                    }
                ),
            },
        )
        out.append((row.id, row.stage_id, row.status, offending[row.id]))
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
    """Report, act, then RE-READ the pool and report the residual (11.84's rule)."""
    async with SessionLocal() as session:
        before = await vocabulary_offences(session)
        offending = await offending_stories(session, only)

        print(
            f"\nVOCABULARY QUARANTINE — {len(before.story_ids)} of {before.stories} "
            f"servable story/stories use a word the CURRENT `allowed_words` does not "
            f"permit at their stage; {len(offending)} selected"
        )
        if not offending:
            print("  nothing to pull. The pool agrees with the current vocabulary.")
            return

        rows = (
            await session.execute(
                text(
                    "SELECT s.id, s.stage_id, s.status, t.slug FROM stories s "
                    "JOIN themes t ON t.id = s.theme_id WHERE s.id = ANY(:i) ORDER BY s.id"
                ),
                {"i": sorted(offending)},
            )
        ).all()
        for row in rows:
            print(
                f"    story {row.id} stage {row.stage_id} / {row.slug} "
                f"({row.status}) — {offending[row.id]}"
            )

        if dry_run:
            print("\n  --dry-run: nothing written. Re-run without it to quarantine.")
            return

        pulled = await quarantine(session, offending)
        await session.commit()
        after = await vocabulary_offences(session)
        kept, readings, answers = await _history(session)
        coverage = await pool_coverage(session)

    print(f"\n  pulled {len(pulled)} story/stories from the servable pool.")
    print(
        f"  NOTHING DELETED: {kept} quarantined row(s) still exist, with {readings} "
        f"reading(s) and {answers} answer(s) preserved."
    )
    print(
        f"  residual: {len(after.story_ids)} of {after.stories} servable story/stories "
        f"still offend (was {len(before.story_ids)} of {before.stories})."
    )

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
            "  Then `--check-coverage`, then `--check-vocabulary`."
        )
    else:
        print(f"\n  COVERAGE: every pair still holds >= {TARGET_PER_PAIR}. No refill needed.")


def main() -> None:
    p = argparse.ArgumentParser(prog="storyforge.quarantine_vocabulary")
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
