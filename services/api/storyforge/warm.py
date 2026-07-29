"""Warm pool (§7).

`make warm` pre-generates >=3 stories per (stage, theme) through the FULL
pipeline and marks them `status='warm'`. Two things fall out of that:

- **The kid never sees an error.** Every §7 failure path — validation_fail,
  api_fail, moderation_fail, budget_exhausted, timeout — terminates in a
  warm-pool serve rather than an error screen.
- **The demo is decoupled from live API health** (§18). Latency at demo time is
  a database read; cold generation stays honestly demonstrable from the admin
  portal.

Rejected stories are persisted too, with `status='rejected'`. They are never
served, but throwing them away would destroy the eval evidence for *why* a
(stage, theme) pair is hard.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.models import Story, StorySegment
from storyforge.pipeline import PipelineResult, run_pipeline

TARGET_PER_PAIR = 3


async def pool_depth(session, stage_id: int, theme_id: int) -> int:
    return (
        await session.execute(
            text(
                "SELECT count(*) FROM stories "
                "WHERE stage_id = :s AND theme_id = :t AND status = 'warm'"
            ),
            {"s": stage_id, "t": theme_id},
        )
    ).scalar_one()


async def persist(session, result: PipelineResult, theme_id: int, today: date) -> Story:
    """Write a pipeline result to `stories` + `story_segments`.

    Segments are split on the pipeline's draft boundaries, which are what the kid
    UI pages through (§11). §10 caps a story at 7 segments.
    """
    mod = result.moderation
    story = Story(
        stage_id=result.stage_id,
        theme_id=theme_id,
        status="warm" if result.passed else "rejected",
        title=result.title[:128],
        decodability=round(result.final_report.decodability, 4),
        story_words=result.story_words,
        gen_meta={
            "rewrite_depth": result.rewrite_depth,
            "cost_usd": round(result.total_cost_usd(today), 6),
            "attempts": [
                {
                    "n": a.n,
                    "kind": a.kind,
                    "decodability": a.decodability,
                    "violations": a.violations,
                    "input_tokens": a.usage.total_input_tokens,
                    "output_tokens": a.usage.output_tokens,
                }
                for a in result.attempts
            ],
            "moderation": (
                {"approved": mod.approved, "layer": mod.layer, "reason": mod.reason}
                if mod
                else None
            ),
            # §14 honesty metrics — surfaced, not hidden.
            "schwa_relaxed": result.final_report.schwa_relaxed_count,
            "heteronyms": result.final_report.heteronym_count,
            "unalignable": result.final_report.unalignable_count,
        },
    )
    session.add(story)
    await session.flush()

    for i, body in enumerate(_split_segments(result.final_text)):
        session.add(StorySegment(story_id=story.id, idx=i, body=body))
    return story


def _split_segments(body: str, max_segments: int = 7) -> list[str]:
    """One screen per sentence-ish chunk, capped at §10's 7 segments.

    Sentences are the natural page break for a decodable reader — the reading
    view shows one segment per screen and must not scroll (style guide §10).
    """
    marked = body.replace("! ", "|").replace("? ", "|").replace(". ", "|")
    parts = [s.strip() for s in marked.split("|")]
    parts = [p if p.endswith((".", "!", "?")) else p + "." for p in parts if p]
    if len(parts) <= max_segments:
        return parts
    # Merge the tail rather than truncate — dropping text would silently lose story.
    head, tail = parts[: max_segments - 1], parts[max_segments - 1 :]
    return head + [" ".join(tail)]


async def run_warm(stages: list[int], themes: list[str], target: int, effort: str):
    today = date.today()
    made = skipped = rejected = 0
    total_cost = 0.0

    async with SessionLocal() as session:
        theme_ids = {
            slug: tid
            for tid, slug in (await session.execute(text("SELECT id, slug FROM themes"))).all()
        }
        for stage in stages:
            for theme in themes:
                tid = theme_ids.get(theme)
                if tid is None:
                    raise SystemExit(f"unknown theme {theme!r} — run `make seed`")

                have = await pool_depth(session, stage, tid)
                need = max(0, target - have)
                if need == 0:
                    print(f"  stage {stage} / {theme}: pool has {have}, skipping")
                    skipped += 1
                    continue

                print(f"  stage {stage} / {theme}: have {have}, generating {need}")
                for _ in range(need):
                    result = await run_pipeline(session, stage, theme, effort=effort)
                    story = await persist(session, result, tid, today)
                    await session.commit()
                    total_cost += result.total_cost_usd(today)
                    if result.passed:
                        made += 1
                        print(
                            f"    warm  #{story.id} {result.final_report.decodability:.0%} "
                            f"{result.rewrite_depth} rewrite(s) — {result.title}"
                        )
                    else:
                        rejected += 1
                        why = (
                            result.moderation.reason
                            if result.moderation and not result.moderation.approved
                            else f"{result.final_report.decodability:.0%} below gate"
                        )
                        print(f"    REJECT #{story.id} — {why}")

    print(
        f"\n{made} warm, {rejected} rejected, {skipped} pair(s) already full. "
        f"Cost ${total_cost:.4f}"
    )
    if made == 0 and skipped == 0:
        raise SystemExit("make warm produced no warm stories")


def main() -> None:
    p = argparse.ArgumentParser(prog="storyforge.warm")
    p.add_argument("--stages", default="1,2,3", help="comma-separated stage ids")
    p.add_argument("--themes", default="dragons", help="comma-separated theme slugs")
    p.add_argument("-n", "--target", type=int, default=TARGET_PER_PAIR)
    p.add_argument("--effort", default="medium")
    a = p.parse_args()
    asyncio.run(
        run_warm(
            [int(x) for x in a.stages.split(",")],
            a.themes.split(","),
            a.target,
            a.effort,
        )
    )


if __name__ == "__main__":
    main()
