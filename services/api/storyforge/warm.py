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

**Enrichment degrades; the CLI does not.** `enrich` still refuses to fail a story
over a missing picture or a missing question (§7, DEVIATIONS 4.14) — but
`run_warm` now reports every degraded outcome and exits non-zero when a story
lands in the pool unenriched. The previous version printed `warm #17 88% 0
rewrite(s)` and exited 0 with both nodes gutted, because
`asyncio.gather(..., return_exceptions=True)` cannot tell an API blip from an
`AttributeError` and nothing downstream looked. A silent success is worse than a
loud failure: it is how nine servable stories reached the pool with no questions
and nobody noticed.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select, text

from storyforge import obs
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.llm.client import fast_model
from storyforge.llm.moderate import ModerationResult, blocklist_check, moderate
from storyforge.llm.pricing import Usage
from storyforge.llm.questions import GeneratedQuestion, QuestionSet, generate_questions
from storyforge.llm.scene_tags import SceneTagResult, select_asset, tag_segments
from storyforge.models import Asset, Question, Story, StorySegment
from storyforge.pipeline import PipelineResult, load_vocabulary, run_pipeline

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
        # §15: the row points at the trace that produced it, so any story in the
        # pool can be opened in Langfuse. None when tracing is off — a story is
        # never withheld because telemetry was unavailable.
        langfuse_trace_id=result.langfuse_trace_id,
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


#: Question types whose text is NOT authored by the model, and which therefore
#: do not go to §13's third layer. `tap_word` is the only one: its prompt is a
#: fixed frame from `PROMPT_FRAMES` filled with a cue word that `_cue_index`
#: picks from the precomputed allowlist, and `check_tap_word` REJECTS any option
#: that is not already a word of the story — which passed moderation in
#: `run_pipeline` before enrichment ever started. There is no free text in it to
#: judge. Layer 1 still reads every one of those strings (see below).
#: Anything not named here is treated as model-authored, so a new question type
#: is moderated by default rather than by omission.
LAYER3_EXEMPT_QTYPES = ("tap_word",)


def question_text(questions: Sequence[GeneratedQuestion]) -> str:
    """Every string the question screens will show a child, in reading order."""
    blocks = []
    for q in questions:
        options = q.payload.get("options") if isinstance(q.payload, dict) else None
        blocks.append("\n".join([q.prompt, *(str(o) for o in options or [])]))
    return "\n\n".join(blocks)


def authored_text(questions: Sequence[GeneratedQuestion]) -> str:
    """The question text a MODEL wrote — §13 layer 3's input."""
    return question_text([q for q in questions if q.qtype not in LAYER3_EXEMPT_QTYPES])


def _usage_cost(usage: Usage | None, on: date) -> float:
    """Cost of one node's tokens. An unpriced model costs a number, not a story."""
    if usage is None:
        return 0.0
    try:
        return usage.cost_usd(on)
    except KeyError:  # no price sheet for this model
        return 0.0


async def _tag_node(*, bodies: Sequence[str], theme_slug: str, story_id: int, on: date):
    """SCENE_TAGGING (§7) with its §15 span. Returns the node's own result."""
    with obs.generation(
        "SCENE_TAGGING",
        input={"theme": theme_slug, "segments": list(bodies)},
        model=fast_model(),
        metadata={"story_id": story_id, "segments": len(bodies)},
    ) as span:
        result = await tag_segments(segments=bodies, theme=theme_slug)
        span.update(
            output={"tags": result.tags, "degraded": result.degraded},
            usage=result.usage,
            on=on,
            level="WARNING" if result.degraded else None,
        )
        return result


async def _question_node(*, story_id: int, on: date, **kwargs):
    """QUESTION_GEN (§7, §11.3) with its §15 span."""
    with obs.generation(
        "QUESTION_GEN",
        input={"title": kwargs.get("story_title"), "text": kwargs.get("story_text")},
        model=fast_model(),
        metadata={"story_id": story_id, "stage_id": kwargs.get("stage_id")},
    ) as span:
        result = await generate_questions(**kwargs)
        span.update(
            output={
                "questions": [q.model_dump() for q in result.questions],
                "dropped": result.dropped,
                "attempts": result.attempts,
            },
            usage=result.usage,
            on=on,
            level="WARNING" if result.dropped else None,
        )
        return result


async def _moderate_questions(questions: Sequence[GeneratedQuestion], on: date):
    """§13's three layers over the question text, with its §15 span.

    The story body is moderated inside `run_pipeline`; enrichment happens after
    that returns, so until this existed every question prompt and every option —
    free text from a second model — reached the child having passed ZERO of
    §13's three layers. Live evidence from the pool: the option
    "The man ran back stop." and the *correct* answer "He did not came back."

    Layer 1 (the blocklist) reads EVERY string, including the tap-a-word
    options: it is a pure function, it is free, and it has no opinion about
    prose. Layer 3 (the Haiku judge) reads only what a model actually wrote.

    That split is not a convenience, it is a measured requirement. `moderate()`
    judges *short stories*; handed a whole question block it rejects 16 of 16
    live cases as "not a coherent story" — a 100% false-positive rate that would
    strip every question in the pool. Handed the story WITH the questions
    appended it swings the other way and approves "The man ran back stop.".
    Handed the model-authored sentences alone it is right in both directions:
    7 of 9 live question sets approved, both known-bad ones rejected by name
    ("came -> come"), a deliberately cruel option rejected on content, and a
    blocklisted one killed at layer 1 with no API call. The one false positive
    (an mcq2 whose false option reads as an unresolved scare) costs a story its
    comprehension question; §6 is explicit that those costs are not symmetric.

    Rejection drops the QUESTIONS, never the story: the story already cleared
    the gate and its own moderation pass, and a story with no questions is a
    defined, servable state (§7).
    """
    hits = blocklist_check(question_text(questions))
    if hits:
        # Layer 1 short-circuits exactly as it does in `moderate()` — a blocked
        # word costs nothing and cannot be argued with.
        return ModerationResult(
            approved=False,
            reason=f"blocklisted words present: {', '.join(hits)}",
            layer="blocklist",
            blocked_words=hits,
        )

    body = authored_text(questions)
    if not body.strip():
        return ModerationResult(
            approved=True,
            reason="no model-authored question text to judge",
            layer="blocklist",
        )

    with obs.generation(
        "MODERATING",
        input=body,
        model=fast_model(),
        metadata={"target": "questions", "questions": len(questions)},
    ) as span:
        verdict = await moderate(body)
        span.update(
            output={
                "approved": verdict.approved,
                "layer": verdict.layer,
                "reason": verdict.reason,
                "blocked_words": verdict.blocked_words,
                "well_formed": verdict.well_formed,
            },
            usage=verdict.usage,
            on=on,
            level=None if verdict.approved else "WARNING",
        )
        return verdict


async def enrich(
    session,
    story: Story,
    theme_slug: str,
    *,
    story_text: str | None = None,
    story_words: Sequence[str] | None = None,
    on: date | None = None,
) -> dict:
    """§7 ENRICHING — scene tags and comprehension questions, in parallel.

    Runs only for stories that PASSED the gate and moderation. §7 places
    ENRICHING after validation for a reason: a story that will never be served
    must not cost Haiku calls.

    Both nodes degrade rather than raise. A story with no picture renders the
    §06 striped placeholder; a story with no questions still reads fine. Neither
    is worth discarding a story that already cleared the decodability gate.

    What changed: the degradation is now *reported*. Every key in the returned
    meta is written to `gen_meta.enrichment`, `*_error` names the exception type
    verbatim so a code bug cannot masquerade as an API blip, and `run_warm`
    refuses to exit 0 on a story that ends up unenriched.

    `story_text` / `story_words` come from the `PipelineResult` on the generation
    path — the validated text and the DISTINCT story words the report actually
    found, never a model claim. Omitted (the `--backfill` path) they are
    reconstructed from the persisted segments and `stories.story_words`.
    """
    # Query segments explicitly rather than touching `story.segments`: the
    # relationship is lazy, and on the async engine an implicit lazy load raises
    # MissingGreenlet instead of quietly doing IO.
    segments = list(
        (
            await session.execute(
                select(StorySegment)
                .where(StorySegment.story_id == story.id)
                .order_by(StorySegment.idx)
            )
        ).scalars()
    )
    bodies = [seg.body for seg in segments]
    text_ = story_text if story_text is not None else " ".join(bodies)
    words = list(story_words if story_words is not None else (story.story_words or []))
    vocab = await load_vocabulary(session, story.stage_id, theme_slug)
    today = on or date.today()

    meta: dict = {}
    cost = 0.0

    # §15: the enrichment nodes belong to the trace that produced the story, not
    # to traces of their own. `run_pipeline`'s trace context has already closed
    # by the time enrichment runs, so it is re-entered by id.
    trace_context = {"trace_id": story.langfuse_trace_id} if story.langfuse_trace_id else None

    with obs.span(
        "ENRICHING",
        trace_context=trace_context,
        input={"story_id": story.id, "theme": theme_slug, "segments": len(bodies)},
        metadata={"story_id": story.id, "stage_id": story.stage_id},
    ) as enriching:
        tagging, qset = await asyncio.gather(
            _tag_node(bodies=bodies, theme_slug=theme_slug, story_id=story.id, on=today),
            _question_node(
                story_id=story.id,
                on=today,
                stage_id=story.stage_id,
                stage_name=vocab.stage_name,
                story_title=story.title,
                story_text=text_,
                allowed_words=vocab.allowed_words,
                sight_words=vocab.sight_words,
                story_words=words,
                # Deterministic per story: the same story must always produce the
                # same questions, or the eval harness cannot reproduce a run.
                seed=story.id,
            ),
            return_exceptions=True,
        )

        if isinstance(tagging, BaseException):
            meta["scene_tags_error"] = f"{type(tagging).__name__}: {tagging}"
        else:
            cost += _usage_cost(tagging.usage, today)
            await _apply_tags(session, story, segments, tagging)
            meta["scene_tags_degraded"] = tagging.degraded

        # Counted off the rows rather than off the node's reply, so a re-run over
        # an already-tagged story reports what the child would actually see.
        meta["segments"] = len(segments)
        meta["segments_tagged"] = sum(1 for s_ in segments if s_.scene_tags)
        meta["segments_with_art"] = sum(1 for s_ in segments if s_.asset_id)

        if isinstance(qset, BaseException):
            meta["questions_error"] = f"{type(qset).__name__}: {qset}"
            meta["questions"] = 0
        else:
            cost += _usage_cost(qset.usage, today)
            written, mod_cost = await _write_questions(session, story, qset, meta, today)
            cost += mod_cost
            meta["questions"] = written
            meta["questions_dropped"] = qset.dropped
            # §8: which stage's sound the phonics question practises. Persisted
            # so "does the mastery loop close at this stage?" is answerable from
            # the database instead of by re-running the node.
            meta["tap_word_target"] = qset.tap_word_target

        meta["cost_usd"] = round(cost, 6)
        meta["trace_id"] = enriching.trace_id
        enriching.update(output=meta)

    return meta


async def _apply_tags(session, story: Story, segments, tagging: SceneTagResult) -> None:
    """Write per-segment tags and pick each segment's picture (§12)."""
    pool = (
        await session.execute(select(Asset).where(Asset.theme_id == story.theme_id))
    ).scalars().all()
    for seg, tags in zip(segments, tagging.tags, strict=False):
        seg.scene_tags = list(tags)
        chosen = select_asset(tags, pool)
        seg.asset_id = chosen.id if chosen else None


async def _write_questions(
    session, story: Story, qset: QuestionSet, meta: dict, on: date
) -> tuple[int, float]:
    """Moderate the question text (§13), then persist what survives.

    Returns (rows written, moderation cost). Nothing is added before the verdict
    is in: an approved-then-delete flow would leave the window in which a
    rejected string is fetchable.
    """
    if not qset.questions:
        return 0, 0.0

    verdict = await _moderate_questions(qset.questions, on)
    meta["questions_moderation"] = {
        "approved": verdict.approved,
        "layer": verdict.layer,
        "reason": verdict.reason,
        "blocked_words": verdict.blocked_words,
        "well_formed": verdict.well_formed,
    }
    cost = _usage_cost(verdict.usage, on)

    keep = list(qset.questions)
    if not verdict.approved:
        # The story stays. It passed its own moderation pass; only the second
        # model's free text is in question here.
        if verdict.layer == "blocklist":
            # A blocked word can be anywhere, including a string layer 3 never
            # saw. Nothing survives an unattributable hit.
            keep = []
        else:
            # A layer-3 verdict is a judgement about the text layer 3 was GIVEN.
            # Dropping a tap-a-word question over an ungrammatical mcq2 would
            # punish a string that was never judged — and it is the only
            # question type that carries mastery evidence (§8), so the child
            # would also stop making progress. Live: story #9's mcq2 asked "Did
            # the man ran off?"; its tap-a-word question was fine.
            keep = [q for q in qset.questions if q.qtype in LAYER3_EXEMPT_QTYPES]
        meta["questions_rejected"] = [
            q.qtype for q in qset.questions if q not in keep
        ]
        if not keep:
            return 0, cost
        for idx, q in enumerate(keep):
            q.idx = idx

    for q in keep:
        session.add(
            Question(
                story_id=story.id,
                idx=q.idx,
                qtype=q.qtype,
                prompt=q.prompt,
                payload=q.payload,
                answer=q.answer,
            )
        )
    return len(keep), cost


def enrichment_problems(meta: dict) -> list[str]:
    """Why this story is not properly enriched — empty when it is.

    `run_warm` exits non-zero on any of these. A story in the servable pool with
    no questions is a §11.3 hole the kid meets as a missing comprehension screen,
    and a story with no tagged segment means SCENE_TAGGING produced nothing at
    all — both used to be invisible.
    """
    problems = []
    for key in ("questions_error", "scene_tags_error"):
        if meta.get(key):
            problems.append(f"{key}: {meta[key]}")
    if not meta.get("questions"):
        why = meta.get("questions_moderation")
        if why and not why.get("approved"):
            problems.append(f"0 questions — moderation rejected them: {why.get('reason')}")
        elif not meta.get("questions_error"):
            problems.append(f"0 questions — dropped: {meta.get('questions_dropped')}")
    if not meta.get("segments_tagged") and not meta.get("scene_tags_error"):
        problems.append("0 tagged segments — SCENE_TAGGING returned nothing usable")
    return problems


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


async def _record_enrichment(session, story: Story, meta: dict, base_cost: float) -> None:
    """Fold one enrichment result into the story row.

    §14/§15 report cost per run, and QUESTION_GEN + SCENE_TAGGING + the question
    moderation pass are three real Haiku calls. Leaving them out of
    `gen_meta.cost_usd` made every reported figure roughly half of what was spent.
    """
    story.gen_meta = {
        **(story.gen_meta or {}),
        "enrichment": meta,
        "cost_usd": round(base_cost + meta.get("cost_usd", 0.0), 6),
    }
    # §15: a backfilled story had no trace of its own. Point the row at the one
    # its enrichment created rather than leaving it unopenable in Langfuse.
    if not story.langfuse_trace_id and meta.get("trace_id"):
        story.langfuse_trace_id = meta["trace_id"]


def _report_enrichment(story_id: int, meta: dict) -> list[str]:
    """Print what enrichment actually did, and return its blocking problems."""
    print(
        f"    enrich #{story_id} {meta.get('questions', 0)} question(s), "
        f"{meta.get('segments_tagged', 0)}/{meta.get('segments', 0)} segment(s) tagged, "
        f"{meta.get('segments_with_art', 0)} with art, ${meta.get('cost_usd', 0.0):.4f}"
    )
    mod = meta.get("questions_moderation")
    if mod and not mod.get("approved"):
        print(f"      questions REJECTED by moderation ({mod['layer']}): {mod['reason']}")
    problems = enrichment_problems(meta)
    for problem in problems:
        print(f"      ! {problem}")
    return problems


def _finish(summary: str, failures: dict) -> None:
    """The one exit point that decides whether the run succeeded."""
    print(f"\n{summary}")
    if failures:
        lines = "\n".join(
            f"  #{sid}: {'; '.join(why)}" for sid, why in sorted(failures.items())
        )
        # Loud and non-zero. The whole point of Phase 4's audit: a run that
        # printed success with both enrichment nodes gutted.
        raise SystemExit(
            f"{len(failures)} story/stories reached the pool unenriched:\n{lines}\n"
            "Re-run with --backfill once the cause is fixed."
        )


async def run_warm(stages: list[int], themes: list[str], target: int, effort: str):
    today = date.today()
    made = skipped = rejected = 0
    total_cost = 0.0
    failures: dict[int, list[str]] = {}

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
                    story_cost = result.total_cost_usd(today)
                    meta = None
                    if result.passed:
                        meta = await enrich(
                            session,
                            story,
                            theme,
                            story_text=result.final_text,
                            story_words=result.story_words,
                            on=today,
                        )
                        await _record_enrichment(session, story, meta, story_cost)
                        story_cost += meta.get("cost_usd", 0.0)
                    await session.commit()
                    total_cost += story_cost
                    if result.passed:
                        made += 1
                        trace = (
                            f" [trace {result.langfuse_trace_id}]"
                            if result.langfuse_trace_id
                            else ""
                        )
                        print(
                            f"    warm  #{story.id} {result.final_report.decodability:.0%} "
                            f"{result.rewrite_depth} rewrite(s) — {result.title}{trace}"
                        )
                        problems = _report_enrichment(story.id, meta or {})
                        if problems:
                            failures[story.id] = problems
                    else:
                        rejected += 1
                        why = (
                            result.moderation.reason
                            if result.moderation and not result.moderation.approved
                            else f"{result.final_report.decodability:.0%} below gate"
                        )
                        print(f"    REJECT #{story.id} — {why}")

    if made == 0 and skipped == 0:
        raise SystemExit("make warm produced no warm stories")
    _finish(
        f"{made} warm, {rejected} rejected, {skipped} pair(s) already full. "
        f"Cost ${total_cost:.4f}",
        failures,
    )


# --- backfill ----------------------------------------------------------------
# Servable stories with no questions. Nine of them existed when this was written:
# they were generated in Phase 3, before the enrichment nodes did, and nothing
# ever went back for them because nothing ever noticed.
_UNENRICHED_SQL = text(
    """
    SELECT s.id, t.slug AS theme_slug
    FROM stories s
    JOIN themes t ON t.id = s.theme_id
    WHERE s.status = ANY(:statuses)
      AND (CAST(:only AS int[]) IS NULL OR s.id = ANY(CAST(:only AS int[])))
      AND NOT EXISTS (SELECT 1 FROM questions q WHERE q.story_id = s.id)
    ORDER BY s.id
    """
)

# One-time data repair, idempotent, safe to re-run: `target_grapheme` used to be
# written into the SERVED payload, where it is the whole answer key (exactly one
# option starts with it). The node now writes it into `answer`; rows written by
# the older build are moved here rather than left leaking.
_MOVE_ANSWER_KEY_SQL = text(
    """
    UPDATE questions
    SET payload = payload - 'target_grapheme',
        answer = coalesce(answer, '{}'::jsonb)
                 || jsonb_build_object('target_grapheme', payload -> 'target_grapheme')
    WHERE payload ? 'target_grapheme'
    RETURNING id
    """
)


async def run_backfill(
    target_status: Sequence[str] = SERVABLE_STATUSES,
    only: Sequence[int] | None = None,
) -> None:
    """Enrich servable stories that have no questions (§7 ENRICHING, after the fact).

    Same code path as the generation run — the point is that there is only one
    enrichment implementation, so a backfilled story is indistinguishable from a
    freshly warmed one.

    `only` restricts the run to specific story ids (`--only 12,13`). Without it
    the whole servable pool is swept, which is what the repair run wants and
    exactly what a test must not do.
    """
    today = date.today()
    total_cost = 0.0
    failures: dict[int, list[str]] = {}

    async with SessionLocal() as session:
        moved = (await session.execute(_MOVE_ANSWER_KEY_SQL)).scalars().all()
        if moved:
            print(f"  moved target_grapheme out of {len(moved)} served payload(s) into `answer`")
        await session.commit()

        rows = (
            await session.execute(
                _UNENRICHED_SQL,
                {"statuses": list(target_status), "only": list(only) if only else None},
            )
        ).all()
        if not rows:
            print("  every servable story already has questions — nothing to backfill")
            return

        print(f"  {len(rows)} servable story/stories with no questions")
        for row in rows:
            story = await session.get(Story, row.id)
            if story is None:  # pragma: no cover — deleted mid-run
                continue
            meta = await enrich(session, story, row.theme_slug, on=today)
            base = float((story.gen_meta or {}).get("cost_usd") or 0.0)
            await _record_enrichment(session, story, meta, base)
            await session.commit()
            total_cost += meta.get("cost_usd", 0.0)
            print(f"    story #{story.id} (stage {story.stage_id} / {row.theme_slug})")
            problems = _report_enrichment(story.id, meta)
            if problems:
                failures[story.id] = problems

    _finish(
        f"{len(rows) - len(failures)} of {len(rows)} backfilled. Cost ${total_cost:.4f}",
        failures,
    )


def main() -> None:
    p = argparse.ArgumentParser(prog="storyforge.warm")
    p.add_argument("--stages", default="1,2,3", help="comma-separated stage ids")
    p.add_argument("--themes", default="dragons", help="comma-separated theme slugs")
    p.add_argument("-n", "--target", type=int, default=TARGET_PER_PAIR)
    p.add_argument("--effort", default="medium")
    p.add_argument(
        "--backfill",
        action="store_true",
        help="enrich existing servable stories that have no questions, then exit",
    )
    a = p.parse_args()
    try:
        if a.backfill:
            asyncio.run(run_backfill())
        else:
            asyncio.run(
                run_warm(
                    [int(x) for x in a.stages.split(",")],
                    a.themes.split(","),
                    a.target,
                    a.effort,
                )
            )
    finally:
        # §15: Langfuse batches exports on a background thread. A CLI that runs
        # for seconds can exit with every span still queued — the integration
        # then looks fine and records nothing. Flush explicitly, including on
        # the `raise SystemExit` paths above. (`obs` also registers an atexit
        # hook, so a hard exit elsewhere is still covered.)
        obs.flush()


if __name__ == "__main__":
    main()
