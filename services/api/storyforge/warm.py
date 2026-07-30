"""Warm pool (§7).

`make warm` pre-generates >=3 stories per (stage, theme) through the FULL
pipeline and marks them `status='warm'`. **Every stage and every theme** — that
sentence was in this docstring, in `docs/STATE.md` and in §7 for five phases while
the CLI defaulted to `--stages 1,2,3 --themes dragons` and the Makefile target
passed no arguments, so `make warm` filled a corner of the matrix. §17's
demo (dragons, now stage 2 under the eleven-stage ladder) was a cell that was
covered, and the §7 ladder
answers an empty pair by serving a LOWER stage — correctly, and reporting to
nobody. So a first deploy shipped nine empty stages and the demo passed
(AUDIT-PHASE7 F4, DEVIATIONS 8.40-8.48). The defaults are now `1..11` / `all`, and
because a correct default is still only a default there is a gate:

    python -m storyforge.warm --check-coverage   # non-zero while any pair is short

Three guards, none sufficient alone: the defaults, the gate (which `docs/DEPLOY.md`
§4 requires and which costs nothing), and `run_warm` reading the pool back at the
end so a pair whose every story was REJECTED fails the run — which neither
`api_fail` nor the unenriched check can see.

Two more things fall out of the pool's design:

- **The kid never sees an error.** Every §7 failure path — validation_fail,
  api_fail, moderation_fail, budget_exhausted, timeout — terminates in a
  warm-pool serve rather than an error screen.
  `run_warm` itself is subject to the same rule, and for five phases it was not:
  an `anthropic.APITimeoutError` out of the GENERATING node propagated straight
  out of the loop and discarded every remaining (stage, theme) pair in the run.
  It is now a per-story outcome — recorded, printed, named in the non-zero exit
  and survived (DEVIATIONS 8.4). A *signal* still terminates the run.
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

**A story with no questions is not servable, and the pool count says so.** The
paragraph above was true and insufficient: `run_warm` reported six stories that
reached the pool unenriched and exited non-zero, and all six went on being served
anyway, because nothing outside this module's exit code cared (DEVIATIONS
8.50-8.51). `api/stories.py::ANSWERABLE_SQL` is now the boundary, and
`pool_coverage`/`pool_depth` import it — so a hollow story makes the pair look
SHALLOW rather than full, which is the difference between a red `--check-coverage`
and a child getting a silent §7 fallback. The generator side of the same defect is
in `llm/questions.py`: every sound it offers is now provably answerable, and the
`tap_word` triple is composed locally when the model's retries are spent (8.53,
8.54).

**Repair is not forward-only.** The same shape of bug came back once already:
the §13 question-moderation pass was added, and `--backfill` selected only
stories with ZERO question rows — so the stories that already held UNMODERATED
questions were the exact set the fix could never reach. `_UNENRICHED_SQL` now
matches a missing §13 receipt as well as a missing question, and `run_backfill`
REPLACES question rows rather than only filling gaps. The one thing it will not
replace is a row a child has answered: `answers.question_id` cascades, so those
stories take `_remoderate_questions`, which judges the text that is already
there.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select, text

from storyforge import obs
from storyforge.api.stories import ANSWERABLE_SQL, SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.decoder import MAX_STAGE, MIN_STAGE, mastered_ids
from storyforge.llm.client import fast_model
from storyforge.llm.moderate import (
    UNGRAMMATICAL_PREFIX,
    ModerationResult,
    blocklist_check,
    moderate,
)
from storyforge.llm.pricing import Usage
from storyforge.llm.questions import (
    GeneratedQuestion,
    QuestionSet,
    choose_cue,
    cue_candidates,
    cue_leaks,
    generate_questions,
    story_word_tokens,
    tap_word_prompt,
    word_graphemes,
)
from storyforge.llm.scene_tags import SceneTagResult, select_asset, tag_segments

# §8's evidence rule, imported rather than restated: `tap_word` is the only
# question type that ever moved `gpc_stats`, so it is the only one whose row is
# irreplaceable once a child has answered it.
from storyforge.mastery import MASTERY_QTYPES
from storyforge.models import Asset, Question, Story, StorySegment
from storyforge.pipeline import PipelineResult, load_vocabulary, run_pipeline

TARGET_PER_PAIR = 3

#: §7's matrix — "≥3 stories per (stage, theme)" over every stage and every theme
#: — spelled the way §14's command line spells it (`eval.py` DEFAULT_STAGES /
#: DEFAULT_THEMES), so the documented command and the running one cannot drift.
#:
#: These were `"1,2,3"` and `"dragons"` for five phases (DEVIATIONS 8.40). The
#: `warm` Makefile target and `docs/DEPLOY.md`'s first-deploy step both pass no
#: arguments, so a fresh deployment got 9 stories across 3 stages and 1 of 3
#: themes — while this module's own docstring, `docs/STATE.md` and §7 all promised
#: the full matrix. §17's demo is dragons at one low stage, a cell that was
#: covered, and the §7 ladder degrades to a lower stage silently and correctly, so
#: the demo passed and nine empty stages were invisible.
DEFAULT_STAGES = "1..11"
DEFAULT_THEMES = "all"


def parse_stages(spec: str) -> list[int]:
    """`"1..11"`, `"all"`, `"3,6"`, `"1..4,7,9..11"` -> a sorted, deduplicated list.

    A second implementation of `eval.parse_stages` on purpose, not an oversight
    (DEVIATIONS 8.46): `eval.py` imports `warm._split_segments`, so importing the
    other way at module level is a cycle and doing it inside a function drags the
    whole §14 harness into every `make warm`. Bad input raises rather than
    silently yielding a smaller matrix than the operator asked for — the whole
    finding this parser exists to serve is a run that quietly covered less than it
    claimed. Unlike `eval`'s copy this one is BOUNDED: a stage outside
    `MIN_STAGE..MAX_STAGE` is refused, so the failure direction is a refusal.
    """
    if spec.strip() == "all":
        return list(range(MIN_STAGE, MAX_STAGE + 1))
    out: set[int] = set()
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        lo, sep, hi = part.partition("..")
        try:
            bounds = (int(lo), int(hi)) if sep else (int(part), int(part))
        except ValueError as exc:
            raise SystemExit(f"bad --stages value {part!r}") from exc
        if bounds[1] < bounds[0]:
            raise SystemExit(f"bad --stages range {part!r}: {bounds[1]} < {bounds[0]}")
        out.update(range(bounds[0], bounds[1] + 1))
    if not out:
        raise SystemExit("--stages selected no stages")
    outside = sorted(s for s in out if not MIN_STAGE <= s <= MAX_STAGE)
    if outside:
        raise SystemExit(f"--stages names stage(s) {outside} outside {MIN_STAGE}..{MAX_STAGE}")
    return sorted(out)


def resolve_themes(spec: str, known: Sequence[str]) -> list[str]:
    """`"all"` -> every seeded theme; otherwise the named ones, validated here.

    `"all"` over an empty `themes` table is a refusal, not an empty list: `make
    warm --themes all` on an unseeded database would otherwise generate nothing
    and exit 0, which is the same vacuous-green shape `assert_matrix_seeded`
    refuses (DEVIATIONS 8.44).
    """
    if spec.strip() == "all":
        if not known:
            raise SystemExit("--themes all matched no themes — run `make seed`")
        return sorted(known)
    themes = [t.strip() for t in spec.split(",") if t.strip()]
    if not themes:
        raise SystemExit("--themes selected no themes")
    unknown = [t for t in themes if t not in known]
    if unknown:
        raise SystemExit(f"unknown theme(s) {unknown} — run `make seed`")
    return themes


def _body_verdict(mod: ModerationResult) -> dict:
    """The §13 receipt for a story BODY, as `gen_meta.moderation`.

    Same five keys `_record_verdict` writes for the question set, deliberately:
    the two receipts answer the same question about two different strings, and
    when they disagreed in shape the body's grammar verdict was the one that went
    missing (DEVIATIONS 6.24). `grammar_note` is the only field that names the
    offending sentence, so dropping it turns "this story is ungrammatical" into
    an unfalsifiable claim.
    """
    return {
        "approved": mod.approved,
        "layer": mod.layer,
        "reason": mod.reason,
        "well_formed": mod.well_formed,
        "grammar_note": mod.grammar_note,
    }


_DEPTH_SQL = text(
    "SELECT count(*) FROM stories s "
    "WHERE s.stage_id = :s AND s.theme_id = :t AND s.status = 'warm' "
    f"AND {ANSWERABLE_SQL.format(story='s')}"
)


async def pool_depth(session, stage_id: int, theme_id: int) -> int:
    """How many stories this pair still holds as `warm` — the REFILL number.

    Deliberately not the same count as `pool_coverage` below, and the difference
    is documented rather than left to be rediscovered (DEVIATIONS 8.42). §7 asks
    for generate-ahead, so a story a child has read (`warm -> complete`,
    `readings.complete_reading`) should be replaced: this is a policy about what to
    generate next. The coverage GATE asks a different question — can a child at
    this (stage, theme) be served their own stage and theme at all — and its
    answer is `SERVABLE_STATUSES`, because that is what `_SERVE_SQL` serves.

    So coverage can pass while this still wants to generate. That is the two
    policies doing their separate jobs.

    What the two DO now agree about is `ANSWERABLE_SQL` (DEVIATIONS 8.51). A
    question-less story is not servable, so counting one here would be worse than
    counting it in coverage: this number decides how many stories to GENERATE, and
    three dead-end `warm` rows in a pair would make `make warm` skip that pair
    forever — the gap could never fill, and the operator's remedy for a red
    coverage gate ("re-run `make warm`") would do nothing at all.
    """
    return (
        await session.execute(_DEPTH_SQL, {"s": stage_id, "t": theme_id})
    ).scalar_one()


# --- §7 coverage: the floor, as a refusal (DEVIATIONS 8.41) -------------------


@dataclass(frozen=True)
class PairDepth:
    """One cell of §7's (stage, theme) matrix and how many stories a kid can get."""

    stage_id: int
    theme_slug: str
    servable: int


# CROSS JOIN, and the status filter is in the ON clause rather than the WHERE:
# a pair with no stories at all has to come back as a row with 0, and a WHERE
# would drop exactly the rows the gate exists to find. That is the whole query.
#
# `ANSWERABLE_SQL` sits in the same ON clause for the same reason (DEVIATIONS
# 8.51): a story with no comprehension question is not servable, so counting one
# here would report a pair at target that a child cannot be served from — and §7's
# ladder answers that pair with a lower stage, silently and correctly, which is
# AUDIT-PHASE7 F4 reached by a second route.
_COVERAGE_SQL = text(
    f"""
    SELECT st.id AS stage_id, t.slug AS theme_slug, count(s.id) AS servable
    FROM stages st
    CROSS JOIN themes t
    LEFT JOIN stories s
      ON s.stage_id = st.id
     AND s.theme_id = t.id
     AND s.status = ANY(:statuses)
     AND {ANSWERABLE_SQL.format(story="s")}
    GROUP BY st.id, t.slug
    ORDER BY st.id, t.slug
    """
)


async def pool_coverage(session) -> list[PairDepth]:
    """Servable depth for every (stage, theme) pair. One query, no API calls.

    `SERVABLE_STATUSES` and `ANSWERABLE_SQL` are both imported from the endpoint,
    never re-spelled here, so neither a fourth `status` value nor a change to what
    "servable" means can make this gate and the serving path disagree — the same
    discipline `eval.py` uses for `EVAL_STATUS`.
    """
    rows = (await session.execute(_COVERAGE_SQL, {"statuses": list(SERVABLE_STATUSES)})).all()
    return [PairDepth(r.stage_id, r.theme_slug, r.servable) for r in rows]


def coverage_gaps(rows: Sequence[PairDepth], target: int) -> list[PairDepth]:
    """The pairs a child cannot be served from. Shallowest first, then by pair."""
    return sorted(
        (r for r in rows if r.servable < target),
        key=lambda r: (r.servable, r.stage_id, r.theme_slug),
    )


def assert_matrix_seeded(rows: Sequence[PairDepth]) -> None:
    """Refuse to answer at all when the matrix itself is missing.

    A coverage check over zero pairs finds zero gaps and exits 0. On a fresh
    deploy — the exact scenario this gate is for — `stages` and `themes` are empty
    until `make seed` runs, so the naive gate reports a green pool on an empty
    database. That is the failure mode, not an edge case (DEVIATIONS 8.44).
    """
    stages = sorted({r.stage_id for r in rows})
    themes = sorted({r.theme_slug for r in rows})
    expected = list(range(MIN_STAGE, MAX_STAGE + 1))
    if stages != expected or not themes:
        raise SystemExit(
            f"the (stage, theme) matrix itself is not seeded: {len(stages)} stage(s) "
            f"{stages}, {len(themes)} theme(s) {themes} — expected stages "
            f"{MIN_STAGE}..{MAX_STAGE} and at least one theme. Run `make seed` first.\n"
            "Refusing rather than reporting: a coverage check over an empty matrix "
            "finds no gaps and would exit 0."
        )


def render_coverage(rows: Sequence[PairDepth], target: int) -> list[str]:
    """The whole matrix as a table, then the gaps by name. Always the FULL matrix.

    A run told `--stages 1,2,3` that reports only on stages 1-3 prints a clean
    table and teaches the operator nothing, which is the defect (DEVIATIONS 8.43).
    """
    themes = sorted({r.theme_slug for r in rows})
    depth = {(r.stage_id, r.theme_slug): r.servable for r in rows}
    width = max([len(t) for t in themes] + [5])
    out = [
        "",
        f"POOL COVERAGE — servable ({'|'.join(SERVABLE_STATUSES)}) stories per "
        f"(stage, theme), target {target}:",
        "  stage  " + "  ".join(t.rjust(width) for t in themes),
    ]
    for stage in sorted({r.stage_id for r in rows}):
        cells = []
        for theme in themes:
            n = depth.get((stage, theme), 0)
            cells.append((str(n) if n >= target else f"{n} !").rjust(width))
        out.append(f"  {stage:>5}  " + "  ".join(cells))

    gaps = coverage_gaps(rows, target)
    at_target = len(rows) - len(gaps)
    out.append(f"  {at_target} of {len(rows)} pair(s) at target.")
    if gaps:
        out.append(f"  {len(gaps)} below target (marked !):")
        out += [f"    stage {g.stage_id} / {g.theme_slug}: {g.servable}" for g in gaps]
    return out


#: What an operator does about a gap. One string, because it is printed from three
#: places and a remedy that disagrees with itself is not a remedy.
_FILL_HINT = (
    "Fill it with `python -m storyforge.warm` (defaults to §7's full "
    f"{MAX_STAGE - MIN_STAGE + 1} stages x every theme, and fills only the "
    "shortfall — `--dry-run` prices it first). Verify with "
    "`python -m storyforge.warm --check-coverage`."
)

#: Why a gap is invisible without this gate, in the terms §7 uses. Kept next to
#: the exit so the message names the consequence and not just the number.
_SILENT_FALLBACK = (
    "Every child at those pairs is served a §7 fallback — a lower stage, or "
    "another theme — which the ladder does correctly and reports to nobody."
)


async def run_coverage_check(target: int = TARGET_PER_PAIR) -> None:
    """§7's floor as an exit code. No API client is constructed; nothing is spent.

    This is the step `docs/DEPLOY.md` §4 requires after the warm run, and it lives
    here rather than in the Makefile because that step is executed in a
    DigitalOcean console with bare `python -m …` — `make` does not exist there
    (DEVIATIONS 8.41).
    """
    async with SessionLocal() as session:
        rows = await pool_coverage(session)
    assert_matrix_seeded(rows)
    for line in render_coverage(rows, target):
        # stdout is block-buffered when this runs under a pipe, and `SystemExit`'s
        # message goes to stderr — without the flush the verdict prints ABOVE the
        # table it is a verdict on.
        print(line, flush=True)

    gaps = coverage_gaps(rows, target)
    if gaps:
        raise SystemExit(
            f"\n{len(gaps)} of {len(rows)} (stage, theme) pair(s) hold fewer than "
            f"{target} servable story/stories.\n{_SILENT_FALLBACK}\n{_FILL_HINT}"
        )
    print(f"\nOK — all {len(rows)} (stage, theme) pair(s) hold >= {target} servable story/stories.")


_OBSERVED_COST_SQL = text(
    """
    SELECT count(*) AS n, avg((gen_meta->>'cost_usd')::float) AS mean
    FROM stories
    WHERE status = ANY(:statuses) AND gen_meta ? 'cost_usd'
    """
)


async def observed_cost_per_story(session) -> tuple[int, float | None]:
    """`(n, mean $/story)` over the servable pool's own recorded costs.

    Measured, not assumed. `gen_meta.cost_usd` is written by `_record_enrichment`
    and already folds enrichment into the story's cost (DEVIATIONS 5.16), so it is
    the right multiplier for "one more warm story". Returns `None` when there is no
    history rather than a plausible-looking zero — AUDIT-PHASE7 F13.4 is a price
    lookup that returned `0.0` on a miss and reported it as a cost.
    """
    row = (await session.execute(_OBSERVED_COST_SQL, {"statuses": list(SERVABLE_STATUSES)})).one()
    return int(row.n), (float(row.mean) if row.mean is not None else None)


async def run_dry_run(stages: Sequence[int], themes: Sequence[str], target: int) -> None:
    """What `make warm` would generate, and roughly what it would cost. No API call.

    DEPLOY.md's warm step says "SLOW. Costs money." and gave the operator no way to
    find out how much before paying it — which matters more now that the default is
    the full matrix (DEVIATIONS 8.45).
    """
    async with SessionLocal() as session:
        theme_ids = {
            slug: tid
            for tid, slug in (await session.execute(text("SELECT id, slug FROM themes"))).all()
        }
        resolve_themes(",".join(themes), list(theme_ids))
        plan: list[tuple[int, str, int, int]] = []
        for stage in stages:
            for theme in themes:
                have = await pool_depth(session, stage, theme_ids[theme])
                plan.append((stage, theme, have, max(0, target - have)))
        coverage = await pool_coverage(session)
        n_priced, mean_cost = await observed_cost_per_story(session)

    print(
        f"--dry-run: {len(plan)} (stage, theme) pair(s) requested, target {target} "
        "warm story/stories each. No API calls."
    )
    for stage, theme, have, need in plan:
        verb = "skip" if need == 0 else f"generate {need}"
        print(f"  stage {stage} / {theme}: warm depth {have} — {verb}")
    to_make = sum(need for *_, need in plan)
    print(f"\n  {to_make} story/stories would be generated.")
    if mean_cost is None:
        print(
            "  Spend: unknown — no story in the pool carries a recorded "
            "`gen_meta.cost_usd`, so there is nothing to extrapolate from."
        )
    else:
        print(
            f"  Spend: ~${to_make * mean_cost:.2f}, extrapolated from the "
            f"{n_priced} priced story/stories already in the pool "
            f"(mean ${mean_cost:.5f} each, enrichment included). A FLOOR: those "
            f"stories are concentrated at the low stages and stages "
            f"{MAX_STAGE - 3}-{MAX_STAGE} are longer."
        )
    for line in render_coverage(coverage, target):
        print(line, flush=True)


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
            # The §13 receipt for the BODY. `well_formed`/`grammar_note` are here
            # because DEVIATIONS 4.12 added them to the verdict and this row
            # dropped them on the floor: the grammar half was computed, folded
            # into `approved`, and then never recorded, so
            # `gen_meta->'moderation'->>'well_formed'` was NULL on every story
            # ever written — including ones generated after the fix (6.24).
            "moderation": (_body_verdict(mod) if mod else None),
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

    Rejection drops the QUESTIONS, never the story: the story already cleared the
    gate and its own moderation pass, so §13's verdict on a second model's text is
    not evidence about the body.

    **This docstring used to end "and a story with no questions is a defined,
    servable state (§7)". It is a defined state and it is NOT a servable one**
    (DEVIATIONS 8.50). That sentence was the codebase's own statement of the
    defect: six `warm` stories carried zero questions after a full `make warm`, and
    every check looked at `status`, which was still `warm`. A child served one
    reads to the end and the session stops — no question screen, no `tap_word`
    answer, no `gpc_stats` movement, no §8 promotion signal. `ANSWERABLE_SQL` now
    refuses it at the serve boundary, and `pool_coverage` stops counting it, so a
    story this function strips of every question leaves the pool visibly shallower
    instead of invisibly hollow. The story row still stays — `--backfill` is what
    repairs it, and it selects on `SERVABLE_STATUSES`.
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

    Both nodes degrade rather than raise, and neither failure is worth discarding a
    story that already cleared the decodability gate. But the two degrade to very
    different places, and this docstring used to flatten them: a story with no
    picture renders the §06 striped placeholder and reads fine, whereas a story
    with no questions is a dead-end session and is no longer servable at all
    (`ANSWERABLE_SQL`, DEVIATIONS 8.50). So the story row survives either way —
    raising here would lose the generation spend for nothing — and only one of the
    two outcomes leaves something a child can be given.

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
        # A child has answered some of this story's questions. `answers` cascades
        # off `questions`, so QUESTION_GEN is not run at all — its result could
        # only be written by deleting those rows. `_remoderate_questions` puts
        # the EXISTING text through §13 instead, which is the part that was
        # actually missing. Empty on the generation path (a story seconds old
        # has no readers), so that path is unchanged.
        answered = await answered_questions(session, story.id)

        nodes = [_tag_node(bodies=bodies, theme_slug=theme_slug, story_id=story.id, on=today)]
        if not answered:
            nodes.append(
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
                    # Deterministic per story: the same story must always produce
                    # the same questions, or the eval harness cannot reproduce a run.
                    seed=story.id,
                )
            )
        results = await asyncio.gather(*nodes, return_exceptions=True)
        tagging, qset = results[0], (results[1] if len(results) > 1 else None)

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

        if qset is None:
            written, mod_cost = await _remoderate_questions(
                session, story, meta, today, protected=mastery_bearing(answered)
            )
            cost += mod_cost
            meta["questions"] = written
        elif isinstance(qset, BaseException):
            meta["questions_error"] = f"{type(qset).__name__}: {qset}"
            meta["questions"] = 0
        else:
            cost += _usage_cost(qset.usage, today)
            written, mod_cost = await _write_questions(session, story, qset, meta, today)
            cost += mod_cost
            meta["questions"] = written
            meta["questions_dropped"] = qset.dropped
            # DEVIATIONS 8.54. Which questions this module composed itself because
            # the model's retries were spent. Recorded rather than smoothed over:
            # the fallback exists so a child is never left with a dead-end
            # session, not so an operator stops hearing about a model that has
            # stopped producing usable questions.
            meta["questions_derived"] = list(qset.derived)
            # §8: which stage's sound the phonics question practises. Persisted
            # so "does the mastery loop close at this stage?" is answerable from
            # the database instead of by re-running the node.
            meta["tap_word_target"] = qset.tap_word_target
            # DEVIATIONS 11.67. The ADDITIONAL phonics questions, which exist so
            # §8's ≥10-observation floor is reachable inside §8's own 5 stories.
            # Kept out of `questions_derived` on purpose — see `QuestionSet` —
            # because that field is read as a set and would raise 8.58's outage
            # alarm on any story whose `mcq2` was merely dropped.
            meta["extra_tap_words"] = qset.extra_tap_words

        meta["cost_usd"] = round(cost, 6)
        meta["trace_id"] = enriching.trace_id
        enriching.update(output=meta)

    return meta


#: Question rows a child has already answered. `answers.question_id` is
#: ON DELETE CASCADE, so any DELETE that touches one silently takes the answer
#: row with it — which is why the replace below is written as a SQL predicate
#: rather than as a Python `if`: the statement is structurally incapable of
#: cascading, whatever the caller believes.
_ANSWERED_SQL = text(
    """
    SELECT q.id, q.qtype
    FROM questions q
    WHERE q.story_id = :story
      AND EXISTS (SELECT 1 FROM answers a WHERE a.question_id = q.id)
    """
)

#: Replace this story's question rows — but never one that carries an answer.
_DELETE_UNANSWERED_SQL = text(
    """
    DELETE FROM questions q
    WHERE q.story_id = :story
      AND NOT EXISTS (SELECT 1 FROM answers a WHERE a.question_id = q.id)
    RETURNING q.id
    """
)


async def answered_questions(session, story_id: int) -> dict[int, str]:
    """`{question_id: qtype}` for rows this story's readers have answered."""
    rows = (await session.execute(_ANSWERED_SQL, {"story": story_id})).all()
    return {row.id: row.qtype for row in rows}


def mastery_bearing(answered: dict[int, str]) -> set[int]:
    """The answered rows whose deletion would destroy §8 evidence.

    §8 credits `gpc_stats` from `tap_word` answers only (`MASTERY_QTYPES`); an
    `mcq2` answer is comprehension evidence and moves no counter. The
    distinction matters because §13 may legitimately require a rejected `mcq2`
    to stop being served, and refusing to remove it would leave unmoderated
    text in front of a child forever.
    """
    return {qid for qid, qtype in answered.items() if qtype in MASTERY_QTYPES}


async def _existing_questions(session, story_id: int) -> list[Question]:
    return list(
        (
            await session.execute(
                select(Question)
                .where(Question.story_id == story_id)
                .order_by(Question.idx, Question.id)
            )
        ).scalars()
    )


def _as_generated(row: Question) -> GeneratedQuestion:
    """A persisted row in the shape §13's moderation helpers already read."""
    return GeneratedQuestion(
        qtype=row.qtype,
        idx=row.idx,
        prompt=row.prompt,
        payload=dict(row.payload or {}),
        answer=dict(row.answer or {}),
    )


async def _apply_tags(session, story: Story, segments, tagging: SceneTagResult) -> None:
    """Write per-segment tags and pick each segment's picture (§12)."""
    pool = (
        await session.execute(select(Asset).where(Asset.theme_id == story.theme_id))
    ).scalars().all()
    for seg, tags in zip(segments, tagging.tags, strict=False):
        seg.scene_tags = list(tags)
        chosen = select_asset(tags, pool)
        seg.asset_id = chosen.id if chosen else None


def _record_verdict(meta: dict, verdict: ModerationResult) -> None:
    """The row's §13 receipt. Its presence is what `_UNENRICHED_SQL` looks for,
    so writing it is the difference between "moderated and approved" and "never
    looked at" — which were indistinguishable before, and NULL on 9 of 28."""
    meta["questions_moderation"] = {
        "approved": verdict.approved,
        "layer": verdict.layer,
        "reason": verdict.reason,
        "blocked_words": verdict.blocked_words,
        "well_formed": verdict.well_formed,
    }


def _survivors(questions: Sequence, verdict: ModerationResult) -> list:
    """§13's keep-policy, shared by the write and the in-place repair.

    A blocklist hit is unattributable — it can be in a string layer 3 never saw
    — so nothing survives it. A layer-3 verdict judges only the text layer 3 was
    GIVEN, so dropping a tap-a-word question over an ungrammatical mcq2 would
    punish a string that was never judged, and it is the only type carrying §8
    mastery evidence.
    """
    if verdict.approved:
        return list(questions)
    if verdict.layer == "blocklist":
        return []
    return [q for q in questions if q.qtype in LAYER3_EXEMPT_QTYPES]


async def _write_questions(
    session, story: Story, qset: QuestionSet, meta: dict, on: date
) -> tuple[int, float]:
    """Moderate the question text (§13), then persist what survives.

    Returns (rows written, moderation cost). Nothing is added before the verdict
    is in: an approved-then-delete flow would leave the window in which a
    rejected string is fetchable.

    **Existing rows are REPLACED, in this transaction.** The §13 question pass
    was forward-only when it shipped: `_UNENRICHED_SQL` matched a story only if
    it had zero question rows, so the nine servable stories that already carried
    UNMODERATED questions were skipped by every `--backfill` run forever, and
    story 17 went on serving the option that motivated the fix, `"The man ran
    back stop."` There was no code path anywhere that re-moderated a question
    row. There is now, and it is this one: the old rows go before the new ones
    land, so the story is never simultaneously holding both, and the DELETE
    cannot touch a row a child has answered (`_DELETE_UNANSWERED_SQL`).
    """
    removed = (await session.execute(_DELETE_UNANSWERED_SQL, {"story": story.id})).scalars().all()
    if removed:
        meta["questions_replaced"] = len(removed)

    if not qset.questions:
        return 0, 0.0

    verdict = await _moderate_questions(qset.questions, on)
    _record_verdict(meta, verdict)
    cost = _usage_cost(verdict.usage, on)

    # The story stays either way. It passed its own moderation pass; only the
    # second model's free text is in question here. Live: story #9's mcq2 asked
    # "Did the man ran off?"; its tap-a-word question was fine.
    keep = _survivors(qset.questions, verdict)
    if not verdict.approved:
        meta["questions_rejected"] = [q.qtype for q in qset.questions if q not in keep]
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
    # The types that actually SURVIVED, not the ones generated: §13 can reject an
    # `mcq2` after the fact, so "what did the child end up with" is only knowable
    # here. `enrichment_problems` reads it against `questions_derived`.
    meta["questions_written"] = [q.qtype for q in keep]
    return len(keep), cost


async def _remoderate_questions(
    session, story: Story, meta: dict, on: date, *, protected: set[int]
) -> tuple[int, float]:
    """§13 over question rows that exist and must not be regenerated.

    Taken when a child has already answered one of this story's questions.
    Regenerating would mean deleting those rows, and `answers.question_id`
    cascades — so the reading history and, for a `tap_word`, the §8 evidence
    behind a promotion decision would go with them. Skipping the story instead
    was the other option, and it is the wrong one: that is precisely how story
    17 kept serving `"The man ran back stop."`

    So the existing text is put through the SAME §13 path a fresh set goes
    through, and the verdict is recorded. Only rows the verdict rejects are
    removed, and a row in `protected` is never removed — it is reported as an
    unresolved hazard instead, loudly, so the operator makes that call rather
    than this function making it silently.
    """
    rows = await _existing_questions(session, story.id)
    if not rows:
        return 0, 0.0

    meta["questions_source"] = "existing rows (answered; not regenerated)"
    meta["questions_answered"] = sorted(protected | set())
    verdict = await _moderate_questions([_as_generated(r) for r in rows], on)
    _record_verdict(meta, verdict)
    cost = _usage_cost(verdict.usage, on)

    if verdict.approved:
        return len(rows), cost

    # `_survivors` reads only `.qtype`, which a persisted row carries too — so
    # the keep-policy is literally the same code as the fresh-write path.
    keep = _survivors(rows, verdict)

    meta["questions_rejected"] = [r.qtype for r in rows if r not in keep]
    stuck, survivors = [], list(keep)
    for row in rows:
        if row in keep:
            continue
        if row.id in protected:
            # §8 evidence outranks §13 removal here, and the residual hazard is
            # named rather than swallowed: `enrichment_problems` turns this into
            # a non-zero exit.
            stuck.append(row.id)
            survivors.append(row)
            continue
        await session.delete(row)
    if stuck:
        meta["questions_unremovable"] = sorted(stuck)

    survivors.sort(key=lambda r: (r.idx, r.id))
    for idx, row in enumerate(survivors):
        row.idx = idx
    return len(survivors), cost


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
    if meta.get("questions_unremovable"):
        # §13 rejected a question a child has already answered. Removing it would
        # cascade §8 mastery evidence, so this function refuses to decide: the
        # run fails and an operator weighs one child's counters against a string
        # every future reader will see.
        problems.append(
            "questions rejected by §13 but NOT removed (deleting them would "
            f"cascade §8 mastery evidence): question id(s) {meta['questions_unremovable']}"
        )
    if not meta.get("questions"):
        why = meta.get("questions_moderation")
        if why and not why.get("approved"):
            problems.append(f"0 questions — moderation rejected them: {why.get('reason')}")
        elif not meta.get("questions_error"):
            problems.append(f"0 questions — dropped: {meta.get('questions_dropped')}")
    else:
        # DEVIATIONS 8.54's own downside, closed. The derived tap-a-word question
        # keeps a story out of the dead-end state, and that is exactly what makes
        # it capable of hiding an outage: three unparseable responses used to
        # leave 0 questions and fail the run, and would now leave 1 and pass it.
        # So the test is not "was anything derived" — one fumble in eleven stories
        # is the measured normal rate and failing every run on it would make this
        # exit code worthless — but "did the model contribute NOTHING that
        # survived", which is the api_fail signature §7 wants to be loud.
        derived = set(meta.get("questions_derived") or ())
        written = list(meta.get("questions_written") or ())
        if written and derived.issuperset(written):
            problems.append(
                f"every surviving question was composed locally after the model's "
                f"retries were spent ({sorted(written)}) — the model contributed "
                f"nothing usable: {meta.get('questions_dropped')}"
            )
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
    if meta.get("questions_derived"):
        # Not a blocking problem (see `enrichment_problems`), but never silent: an
        # operator who runs `make warm` does not run SQL, and a rising derived rate
        # at some stage is a finding about the PROMPT.
        print(
            f"      {meta['questions_derived']} composed locally after the model's "
            f"retries were spent (§11.3 satisfied, prompt is not)"
        )
    mod = meta.get("questions_moderation")
    if mod and not mod.get("approved"):
        print(f"      questions REJECTED by moderation ({mod['layer']}): {mod['reason']}")
    problems = enrichment_problems(meta)
    for problem in problems:
        print(f"      ! {problem}")
    return problems


def _finish(
    summary: str,
    failures: dict,
    api_failures: Sequence[tuple[int, str, str]] = (),
    coverage_gaps_: Sequence[PairDepth] = (),
    target: int = TARGET_PER_PAIR,
) -> None:
    """The one exit point that decides whether the run succeeded.

    Three kinds of failure, kept on separate lines because they have different
    remedies (DEVIATIONS 8.4, 8.43). `failures` is a story that reached the pool
    unenriched — the fix is `--backfill`. `api_failures` is a cell that produced
    no story at all, usually `anthropic.APITimeoutError`; nothing is wrong with
    the pool, it is just shallower than the target, and the fix is to re-run
    `make warm`, which `pool_depth` already makes idempotent.

    `coverage_gaps_` is the third, and it is the only one that looks at the POOL
    rather than at this run's bookkeeping. It is passed non-empty only when the run
    covered the whole matrix and pairs are still short — which the other two
    cannot detect: a pair whose every generation succeeded and was then REJECTED
    (gate or moderation) leaves `failures` and `api_failures` both empty, `made`
    non-zero, and the pair empty. That is a second route to AUDIT-PHASE7 F4 and it
    survives the default-matrix fix, so the exit code has to key off the pool.
    """
    print(f"\n{summary}", flush=True)
    if api_failures:
        print(
            f"\n{len(api_failures)} generation(s) never produced a story "
            "(§7 api_fail — the pool keeps its current depth):"
        )
        for stage, theme, why in api_failures:
            print(f"  stage {stage} / {theme}: {why}")

    if not failures and not api_failures and not coverage_gaps_:
        return

    parts = []
    if failures:
        lines = "\n".join(
            f"  #{sid}: {'; '.join(why)}" for sid, why in sorted(failures.items())
        )
        parts.append(
            f"{len(failures)} story/stories reached the pool unenriched:\n{lines}\n"
            "Re-run with --backfill once the cause is fixed."
        )
    if api_failures:
        lines = "\n".join(f"  stage {s} / {t}: {why}" for s, t, why in api_failures)
        parts.append(
            f"{len(api_failures)} generation(s) failed outright, so those "
            f"(stage, theme) pairs are below target:\n{lines}\n"
            "Re-run `make warm` — it fills only the shortfall."
        )
    if coverage_gaps_:
        lines = "\n".join(
            f"  stage {g.stage_id} / {g.theme_slug}: {g.servable}" for g in coverage_gaps_
        )
        parts.append(
            f"this run covered §7's whole matrix and {len(coverage_gaps_)} "
            f"(stage, theme) pair(s) still hold fewer than {target} servable "
            f"story/stories:\n{lines}\n{_SILENT_FALLBACK}\n"
            "Re-run `make warm`; if a pair stays empty its stories are being "
            "REJECTED, not lost — look at `status='rejected'` for that pair."
        )
    # Loud and non-zero. The whole point of Phase 4's audit: a run that
    # printed success with both enrichment nodes gutted.
    raise SystemExit("\n\n".join(parts))


async def run_warm(stages: list[int], themes: list[str], target: int, effort: str):
    today = date.today()
    made = skipped = rejected = derived = 0
    total_cost = 0.0
    failures: dict[int, list[str]] = {}
    # DEVIATIONS 8.4: (stage, theme, "ExcType: message") per generation that
    # produced no story at all. Was an uncaught exception out of `run_warm`.
    api_failures: list[tuple[int, str, str]] = []

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
                    # §7: `api_fail` and `timeout` each map to a fallback like
                    # any other failure in the taxonomy, and this was the one
                    # caller that let them out (DEVIATIONS 8.4). One slow
                    # `anthropic.APITimeoutError` used to discard every
                    # remaining (stage, theme) pair in the run.
                    try:
                        result = await run_pipeline(session, stage, theme, effort=effort)
                    except BaseException as exc:  # noqa: BLE001 — a dead cell is not a dead run
                        # A signal is not an api_fail; let it out (as eval.py does).
                        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                            raise
                        # Nothing is uncommitted here — the loop commits per
                        # story — so this is defence against a poisoned session
                        # turning one recoverable cell into a broken run.
                        await session.rollback()
                        why = f"{type(exc).__name__}: {exc}"
                        api_failures.append((stage, theme, why))
                        print(f"    API FAIL stage {stage} / {theme} — {why}", flush=True)
                        continue
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
                        derived += 1 if (meta or {}).get("questions_derived") else 0
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

        # DEVIATIONS 8.43: the run's verdict on the thing the pool is FOR, read
        # back from the database rather than accumulated from the loop. Same
        # session, after every commit.
        coverage = await pool_coverage(session)

    full_matrix = set(stages) >= set(range(MIN_STAGE, MAX_STAGE + 1)) and set(themes) >= set(
        theme_ids
    )
    assert_matrix_seeded(coverage)
    for line in render_coverage(coverage, target):
        print(line, flush=True)
    gaps = coverage_gaps(coverage, target)
    if gaps and not full_matrix:
        # A slice did what it was asked, so this is not a failure — but the
        # operator sees the whole picture and the command that closes it, which is
        # the half AUDIT-PHASE7 F4 says nobody ever saw.
        print(
            f"\n  This run covered {len(stages)} of {MAX_STAGE} stage(s) and "
            f"{len(themes)} of {len(theme_ids)} theme(s) by request, so the gaps "
            f"above are expected.\n  {_SILENT_FALLBACK}\n  {_FILL_HINT}"
        )

    if made == 0 and skipped == 0:
        # Still the first thing checked, and now it names the reason when the
        # reason is that every generation failed rather than that every story
        # was rejected — two very different operator problems.
        detail = ""
        if api_failures:
            detail = (
                f" — {len(api_failures)} generation(s) failed outright, "
                f"first: {api_failures[0][2]}"
            )
        raise SystemExit(f"make warm produced no warm stories{detail}")
    _finish(
        f"{made} warm, {rejected} rejected, {len(api_failures)} api_fail, "
        f"{skipped} pair(s) already full"
        # An aggregate rather than an exit code (DEVIATIONS 8.54): the measured
        # rate at which the model needs catching is ~11% of stories, so failing
        # the run on it would make the exit code worthless — but a number the
        # operator can watch move is the whole point of recording it.
        + (f", {derived} tap-a-word question(s) composed locally" if derived else "")
        + f". Cost ${total_cost:.4f}",
        failures,
        api_failures,
        gaps if full_matrix else (),
        target,
    )


# --- backfill ----------------------------------------------------------------
# Servable stories whose questions are missing, have never been through §13, or
# cannot move §8.
#
# Three clauses, and each one was added after the previous set failed to reach the
# rows it was written for. The first version had only `NOT EXISTS (questions)`,
# which made the §13 question-moderation pass FORWARD-ONLY: a story that already
# carried questions — written before that pass existed — matched nothing, so every
# `--backfill` run skipped it, forever, and no other code path re-moderated a
# question row. Nine of 28 servable stories were in that state; story 17 was still
# serving the option that motivated the fix in the first place.
#
# The third clause is the same lesson a second time (DEVIATIONS 8.56). After the
# six zero-question stories were repaired, a live sweep of all 36 pairs found
# serves returning an `mcq2` and no `tap_word`. Those are not dead-end sessions —
# the child gets a question screen — but `MASTERY_QTYPES` is `("tap_word",)`, so
# they move no `gpc_stats` counter and earn nothing toward promotion. Six servable
# stories were in that state and **not one of them was reachable**: they have
# questions and they have a §13 receipt, so the first two clauses were both
# satisfied. Five of the six had dropped `tap_word` for the same "close to the same
# length" reason 8.53 is about.
#
# Idempotence is by RECEIPT, not by hope, exactly as `_UNGRADED_BODY_SQL` does it:
# `describe_target` writes `source: null` when nothing wrote a tap-a-word question,
# so a story this sweep has already tried and failed on carries the key with a null
# value and is excluded. A selected story therefore either gains a `tap_word` or
# records that it cannot have one — a second run selects nothing either way.
_UNENRICHED_SQL = text(
    """
    SELECT s.id, t.slug AS theme_slug
    FROM stories s
    JOIN themes t ON t.id = s.theme_id
    WHERE s.status = ANY(:statuses)
      AND (CAST(:only AS int[]) IS NULL OR s.id = ANY(CAST(:only AS int[])))
      AND (
            NOT EXISTS (SELECT 1 FROM questions q WHERE q.story_id = s.id)
            OR s.gen_meta -> 'enrichment' -> 'questions_moderation' IS NULL
            OR (
                 -- §8 cannot move without one of these, whatever else the story
                 -- carries. Spelled from `MASTERY_QTYPES` so this sweep and the
                 -- grader in `POST /api/readings/{id}/answers` cannot disagree.
                 NOT EXISTS (
                   SELECT 1 FROM questions q
                   WHERE q.story_id = s.id AND q.qtype = ANY(:mastery_qtypes)
                 )
                 -- ...unless a previous run already established that this story
                 -- cannot have one. `source` present-and-null is that receipt.
                 --
                 -- `coalesce(..., false)` is load-bearing, not defensive. When
                 -- `tap_word_target` is absent entirely, `? 'source'` is NULL
                 -- rather than false, so the AND is NULL, the NOT is NULL, and the
                 -- whole three-way OR evaluates to NULL — which WHERE treats as
                 -- false. The story silently drops out of its own repair sweep. A
                 -- test caught this on a hand-built row; the six live stories hid
                 -- it because they all carried a `tap_word_target` from their first
                 -- enrichment, so `? 'source'` was false for them and not NULL.
                 AND NOT coalesce(
                   s.gen_meta -> 'enrichment' -> 'tap_word_target' ? 'source'
                   AND s.gen_meta -> 'enrichment' -> 'tap_word_target' ->> 'source' IS NULL,
                   false
                 )
               )
          )
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


#: Every tap-a-word row in the pool, with what it needs to re-derive its prompt.
_TAP_WORD_ROWS_SQL = text(
    """
    SELECT q.id, q.prompt, q.payload ->> 'cue_word' AS cue,
           s.stage_id, s.id AS story_id, s.story_words, t.slug AS theme_slug
    FROM questions q
    JOIN stories s ON s.id = q.story_id
    JOIN themes t ON t.id = s.theme_id
    WHERE q.qtype = 'tap_word'
      AND s.status = ANY(:statuses)
      AND (CAST(:only AS int[]) IS NULL OR s.id = ANY(CAST(:only AS int[])))
    ORDER BY q.id
    """
)


#: Every tap-a-word row, at EVERY status, with the story text a new cue must
#: avoid. Status-blind on purpose — see `repair_leaky_cues`.
_TAP_WORD_CUE_ROWS_SQL = text(
    """
    SELECT q.id, q.prompt, q.payload, q.answer,
           s.id AS story_id, s.stage_id, s.status, s.story_words,
           t.slug AS theme_slug,
           coalesce(string_agg(sg.body, ' ' ORDER BY sg.idx), '') AS body,
           (SELECT count(*) FROM answers a WHERE a.question_id = q.id) AS n_answers
    FROM questions q
    JOIN stories s ON s.id = q.story_id
    JOIN themes t ON t.id = s.theme_id
    LEFT JOIN story_segments sg ON sg.story_id = s.id
    WHERE q.qtype = 'tap_word'
      AND (CAST(:statuses AS text[]) IS NULL OR s.status = ANY(CAST(:statuses AS text[])))
      AND (CAST(:only AS int[]) IS NULL OR s.id = ANY(CAST(:only AS int[])))
    GROUP BY q.id, q.prompt, q.payload, q.answer, s.id, s.stage_id, s.status,
             s.story_words, t.slug
    ORDER BY q.id
    """
)


async def repair_leaky_cues(
    session, target_status: Sequence[str] | None = None, only: Sequence[int] | None = None
) -> tuple[int, list[int], list[int]]:
    """Re-cue every row whose hint word points at an option by shape (7.14).

    The cue and its prompt are pure functions of (grapheme, option set, stage
    vocabulary) — `cue_candidates` / `choose_cue` / `tap_word_prompt`, the same
    ones the node calls — so a repaired row is the row the fixed node would have
    written, computed here with no API call.

    **`options` and `answer` are never touched, and nothing is ever deleted.**
    That is the whole design constraint rather than a nicety: `answers.
    question_id` is `ON DELETE CASCADE`, `MASTERY_QTYPES` evidence lives on those
    rows, and an agent has already destroyed one by regenerating a question a
    child had answered. Moving only the hint leaves every recorded answer
    meaning exactly what it meant — while stating, as 7.15 does rather than
    silently repairing, that what it meant may not be trustworthy.

    Status-blind by default (`target_status=None`). The defect is a property of
    the row's content, not of whether the story is servable, and the one answered
    instance in the live pool sat on a `rejected` story that no sweep filtered
    to servable would ever have reached.

    Returns (rows repaired, ids left alone, ids repaired that carry answers).
    Idempotent: a repaired row no longer leaks, so a second run selects nothing.
    """
    rows = (
        await session.execute(
            _TAP_WORD_CUE_ROWS_SQL,
            {
                "statuses": list(target_status) if target_status else None,
                "only": list(only) if only else None,
            },
        )
    ).all()

    vocab_cache: dict[tuple[int, str], object] = {}
    repaired, stuck, answered = 0, [], []
    for row in rows:
        payload = dict(row.payload or {})
        cue = str(payload.get("cue_word") or "").strip().lower()
        options = [str(o).strip().lower() for o in (payload.get("options") or [])]
        if not cue or not options or not cue_leaks(cue, options):
            continue

        answer = dict(row.answer or {})
        grapheme = str(answer.get("target_grapheme") or "").strip().lower()
        if not grapheme:
            # Pre-4.9 rows kept no grapheme anywhere. Re-derive it from the
            # answer the way `check_tap_word` proved it: the first GPC of the
            # correct word. A row with no recoverable answer is left alone.
            correct = str(answer.get("correct") or "").strip().lower()
            found = word_graphemes(correct, mastered_ids(row.stage_id)) if correct else None
            grapheme = found[0] if found else ""
        if not grapheme:
            stuck.append(row.id)
            continue

        key = (row.stage_id, row.theme_slug)
        if key not in vocab_cache:
            vocab_cache[key] = await load_vocabulary(session, row.stage_id, row.theme_slug)
        vocab = vocab_cache[key]

        story_words = list(row.story_words or [])
        # The cue may not be a word of this story — `_cue_index`'s own rule, so
        # the replacement can never turn out to be one of the options.
        candidates = cue_candidates(
            vocab.allowed_words,
            row.stage_id,
            exclude=set(story_word_tokens(row.body)) | set(options) | set(story_words),
        ).get(grapheme, [])
        fresh = choose_cue(candidates, options)
        prompt = (
            tap_word_prompt(fresh, row.stage_id, vocab.sight_words, story_words) if fresh else None
        )
        # §13 layer 1 reads the result, exactly as `refresh_tap_word_prompts` does.
        if fresh is None or prompt is None or blocklist_check(prompt):
            stuck.append(row.id)
            continue

        payload["cue_word"] = fresh
        await session.execute(
            text("UPDATE questions SET prompt = :p, payload = CAST(:j AS jsonb) WHERE id = :i"),
            {"p": prompt, "j": json.dumps(payload), "i": row.id},
        )
        repaired += 1
        if row.n_answers:
            answered.append(row.id)
    return repaired, stuck, answered


async def refresh_tap_word_prompts(
    session, target_status: Sequence[str] = SERVABLE_STATUSES, only: Sequence[int] | None = None
) -> tuple[int, list[int]]:
    """Re-derive every tap-a-word prompt from the current `PROMPT_FRAMES` ladder.

    A tap-a-word prompt is a PURE function of (cue word, stage) —
    `tap_word_prompt`, tried richest-first against the gate — not text a model
    wrote. So when the ladder is rebuilt, the rows already in the pool can be
    brought up to it in place: no regeneration, no API call, and, decisively, no
    DELETE. That last point is what makes this the right tool here. Nineteen of
    28 live rows were still reading `Tap: get.` after the ladder was fixed, and
    the stories a child had already read (§8 evidence, `answers` cascading off
    `questions`) could not be regenerated at all — this reaches those too.

    Layer 1 still reads the result. The frames are fixed strings today, but
    "fixed" is a property of the current source file, and §13 layer 1 is a pure
    function that costs nothing.

    Returns (rows updated, ids left alone). Idempotent: a second run is a no-op.
    """
    rows = (
        await session.execute(
            _TAP_WORD_ROWS_SQL,
            {"statuses": list(target_status), "only": list(only) if only else None},
        )
    ).all()

    vocab_cache: dict[tuple[int, str], object] = {}
    updated, skipped = 0, []
    for row in rows:
        if not row.cue:  # legacy row with no cue word — nothing to re-derive from
            skipped.append(row.id)
            continue
        key = (row.stage_id, row.theme_slug)
        if key not in vocab_cache:
            vocab_cache[key] = await load_vocabulary(session, row.stage_id, row.theme_slug)
        vocab = vocab_cache[key]
        # The STORY's own story words, not the theme bank's — exact parity with
        # what `check_tap_word` validated the prompt against when it was written.
        prompt = tap_word_prompt(
            row.cue, row.stage_id, vocab.sight_words, list(row.story_words or [])
        )
        if prompt is None or prompt == row.prompt or blocklist_check(prompt):
            if prompt is None or blocklist_check(prompt):
                skipped.append(row.id)
            continue
        await session.execute(
            text("UPDATE questions SET prompt = :p WHERE id = :i"), {"p": prompt, "i": row.id}
        )
        updated += 1
    return updated, skipped


def _report_cue_repair(repaired: int, stuck: Sequence[int], answered: Sequence[int]) -> None:
    """Print what `repair_leaky_cues` did — including what it could not do."""
    if repaired:
        print(f"  re-cued {repaired} tap-a-word row(s) whose hint pointed at an option (7.14)")
    if answered:
        print(
            f"    {len(answered)} of them already carry recorded answers: {sorted(answered)}. "
            "The rows are kept and the answers are untouched, but the §8 evidence "
            "they produced was collected against a leaking hint — see DEVIATIONS 7.15."
        )
    if stuck:
        print(f"    could NOT re-cue {len(stuck)} row(s), left as they are: {sorted(stuck)}")


async def run_cue_repair(only: Sequence[int] | None = None) -> None:
    """`--cue-repair`: sweep every tap-a-word row for the 7.14 hint leak."""
    async with SessionLocal() as session:
        repaired, stuck, answered = await repair_leaky_cues(session, None, only)
        await session.commit()
    if not repaired and not stuck:
        print("  no tap-a-word row's hint points at an option — nothing to repair")
        return
    _report_cue_repair(repaired, stuck, answered)


async def run_backfill(
    target_status: Sequence[str] = SERVABLE_STATUSES,
    only: Sequence[int] | None = None,
) -> None:
    """Enrich servable stories with missing or unmoderated questions (§7, §13).

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
        # Before the prompt refresh: a repaired cue produces a new prompt, and
        # the refresh below would otherwise re-derive the OLD one.
        _report_cue_repair(*await repair_leaky_cues(session, None, only))
        rewritten, unchanged = await refresh_tap_word_prompts(session, target_status, only)
        if rewritten:
            print(f"  re-derived {rewritten} tap-a-word prompt(s) from the current frame ladder")
        if unchanged:
            print(f"  left {len(unchanged)} tap-a-word prompt(s) alone: {unchanged}")
        await session.commit()

        rows = (
            await session.execute(
                _UNENRICHED_SQL,
                {
                    "statuses": list(target_status),
                    "only": list(only) if only else None,
                    "mastery_qtypes": list(MASTERY_QTYPES),
                },
            )
        ).all()
        if not rows:
            print(
                "  every servable story has questions, a §13 verdict on them and a "
                f"{'/'.join(MASTERY_QTYPES)} question §8 can count — nothing to backfill"
            )
            return

        print(
            f"  {len(rows)} servable story/stories with questions that are missing, "
            f"unmoderated, or cannot move §8"
        )
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


# --- grammar backfill (DEVIATIONS 6.24-6.26) ---------------------------------
# Servable stories whose BODY has never been judged for grammar.
#
# The predicate is the idempotence guarantee: `well_formed` is written for every
# story this sweep touches, whatever the verdict, so a second run selects the
# empty set and makes no API call. It is deliberately `IS NULL` on the key rather
# than a `= false` / marker column — "never judged" and "judged and fine" were
# indistinguishable before (DEVIATIONS 6.24), which is the whole defect.
_UNGRADED_BODY_SQL = text(
    """
    SELECT s.id, s.stage_id, s.status, t.slug AS theme_slug
    FROM stories s
    JOIN themes t ON t.id = s.theme_id
    WHERE s.status = ANY(:statuses)
      AND (CAST(:only AS int[]) IS NULL OR s.id = ANY(CAST(:only AS int[])))
      AND s.gen_meta -> 'moderation' -> 'well_formed' IS NULL
    ORDER BY s.id
    """
)


async def story_body(session, story_id: int) -> str:
    """The story's text as §13 judged it, rebuilt from `story_segments`.

    `stories` never persisted `PipelineResult.final_text` — the segments ARE the
    story after `persist()` runs — so any pass that wants to re-judge a body has
    to reconstruct it. This is the same reconstruction `enrich()` uses on the
    `--backfill` path (`" ".join(bodies)`), shared rather than restated so the
    two repair paths cannot end up judging different text.

    It is lossless in practice, not by assumption: `_split_segments` splits on
    `". "` / `"! "` / `"? "` and re-appends a terminal stop to the one fragment
    that can lack it, so joining on a single space returns the original. A test
    asserts the round trip over every servable story rather than trusting this
    paragraph.
    """
    bodies = (
        (
            await session.execute(
                select(StorySegment.body)
                .where(StorySegment.story_id == story_id)
                .order_by(StorySegment.idx)
            )
        )
        .scalars()
        .all()
    )
    return " ".join(b.strip() for b in bodies if b and b.strip())


def _grammar_note(verdict: ModerationResult) -> str:
    """The sentence that failed, for the operator. Never empty on a failure."""
    return verdict.grammar_note.strip() or verdict.reason


def _rejected_on_content(verdict: ModerationResult) -> bool:
    """Did the SAFETY half fire, as opposed to only the grammar half?

    `moderate()` folds `well_formed` into `approved`, so a rejected verdict does
    not say which half objected. It does leave one signal: the reason is
    rewritten with `UNGRAMMATICAL_PREFIX` exactly when the model approved the
    content and only the grammar failed. A story can fail BOTH, and collapsing
    the two would let a grammar failure hide behind a content one — which is the
    number this sweep exists to produce.
    """
    return not verdict.approved and not verdict.reason.startswith(UNGRAMMATICAL_PREFIX)


async def run_grammar_backfill(
    target_status: Sequence[str] = SERVABLE_STATUSES,
    only: Sequence[int] | None = None,
) -> None:
    """Re-run §13 over servable story BODIES that carry no grammar verdict.

    DEVIATIONS 4.12 added the grammar half of the moderation verdict and wired it
    into the gate, so every story generated after it was judged. It was never
    applied backwards, and `persist()` did not even record the field (6.24), so
    the 28 stories already in the pool had never been asked the question. The
    *question* receipts said `well_formed` on 28 of 28; the bodies said nothing.

    This is the backwards application. It is not a grammar-only check — it calls
    the same `moderate()` the generation path calls, so a body that fails on
    SAFETY is caught too, and the two are reported separately so the grammar
    number stays a clean answer to "how much bad grammar is already in the pool".

    A story the verdict rejects moves to `status='rejected'` and stops being
    served (DEVIATIONS 6.26). Nothing is deleted: `answers` cascades off
    `questions` which cascades off `stories`, so a DELETE here would destroy §8
    mastery evidence for a defect in the story's prose. The evidence outlives the
    story, which is the correct ordering — a promotion already granted on those
    counters must stay reproducible.
    """
    today = date.today()
    total_cost = 0.0
    judged = 0
    grammar_failures: list[str] = []
    safety_failures: list[str] = []
    pulled: list[int] = []

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                _UNGRADED_BODY_SQL,
                {"statuses": list(target_status), "only": list(only) if only else None},
            )
        ).all()
        if not rows:
            print("  every servable story body carries a grammar verdict — nothing to backfill")
            return

        print(f"  {len(rows)} servable story/stories with no grammar verdict on the body")
        for row in rows:
            story = await session.get(Story, row.id)
            if story is None:  # pragma: no cover — deleted mid-run
                continue
            body = await story_body(session, story.id)
            if not body:
                # No segments at all. Judging "" would return a meaningless
                # verdict and writing one would mark the story judged forever,
                # so it is left alone and named instead.
                print(f"    story #{story.id}: no segment text to judge — skipped")
                continue

            verdict = await moderate(body)
            judged += 1
            total_cost += _usage_cost(verdict.usage, today)

            receipt = {
                # The Phase 3 receipt is kept where it can still be read: it
                # records what was known when the story was written, and this
                # sweep is a later, better-informed pass, not a correction of
                # the original.
                "generation": (story.gen_meta or {}).get("moderation"),
                **_body_verdict(verdict),
                "source": "grammar-backfill",
            }
            if not verdict.approved:
                # WHEN it left the pool, not just that it did.
                #
                # Until this sweep existed, `rejected` was a birth state: a story
                # was rejected before it was ever servable, so "a rejected story
                # has a `last_served_at`" was an unambiguous alarm that
                # `_SERVE_SQL` had leaked one. This sweep creates the first
                # rejected rows that were legitimately served — while they were
                # still warm — and without a timestamp the alarm cannot tell that
                # case from a real leak, so it would have to be deleted rather
                # than kept. Recording the moment keeps the child-safety question
                # ("was it served AFTER it was pulled?") answerable, which is the
                # one that actually matters.
                receipt["pulled_at"] = datetime.now(UTC).isoformat()
            story.gen_meta = {**(story.gen_meta or {}), "moderation": receipt}
            if not verdict.approved:
                # The two halves are counted separately and a story can fail
                # BOTH. `moderate()` puts the safety reason in `reason` when it
                # fires, so the grammar note is the only place the offending
                # sentence survives — print it explicitly rather than letting a
                # safety line stand in for a grammar verdict.
                if not verdict.well_formed:
                    grammar_failures.append(f"#{story.id}: {_grammar_note(verdict)}")
                if _rejected_on_content(verdict):
                    safety_failures.append(f"#{story.id}: {verdict.reason}")
                story.status = "rejected"
                pulled.append(story.id)
                why = _grammar_note(verdict) if not verdict.well_formed else verdict.reason
                print(
                    f"    REJECT #{story.id} (stage {story.stage_id}) "
                    f"well_formed={verdict.well_formed} — {why}"
                )
            else:
                print(f"    ok     #{story.id} (stage {story.stage_id})")
            await session.commit()

    print(
        f"\n{judged} body/bodies judged, {len(grammar_failures)} FAILED the grammar "
        f"check, {len(pulled)} pulled from the pool. Cost ${total_cost:.4f}"
    )
    if grammar_failures:
        print("  ungrammatical (DEVIATIONS 4.12), now `rejected`:")
        for line in grammar_failures:
            print(f"    {line}")
    if safety_failures:
        # Not necessarily disjoint from the list above: a story can be both, and
        # collapsing them would let a grammar failure hide behind a content one.
        print("  also rejected on content (§13 layer 3):")
        for line in safety_failures:
            print(f"    {line}")
    if pulled:
        # Loud, because the servable pool just got smaller and DEVIATIONS 6.7
        # already notes stages where promotion is unreachable for want of
        # distinct stories. Not an exit code: the sweep did its job.
        print(f"  {len(pulled)} story/stories left the servable pool: {sorted(pulled)}")
        print("  Run `make warm` to refill. Nothing was deleted; §8 evidence is intact.")


async def _run(a: argparse.Namespace) -> None:
    """Resolve the matrix, then generate (or price) it.

    Split out of `main` because `--themes all` needs the database to know what
    "all" is, and `--stages` has to be rejected before a session is opened — a bad
    matrix is an argument error, not a database error.
    """
    stages = parse_stages(a.stages)
    async with SessionLocal() as session:
        known = list((await session.execute(text("SELECT slug FROM themes"))).scalars().all())
    themes = resolve_themes(a.themes, known)
    if a.dry_run:
        await run_dry_run(stages, themes, a.target)
    else:
        await run_warm(stages, themes, a.target, a.effort)


def main() -> None:
    p = argparse.ArgumentParser(prog="storyforge.warm")
    p.add_argument(
        "--stages",
        default=DEFAULT_STAGES,
        help=f"'1..11', 'all', '3,6', '1..4,9..11' (default: {DEFAULT_STAGES} — §7's full matrix)",
    )
    p.add_argument(
        "--themes",
        default=DEFAULT_THEMES,
        help=f"'all' or comma-separated theme slugs (default: {DEFAULT_THEMES})",
    )
    p.add_argument("-n", "--target", type=int, default=TARGET_PER_PAIR)
    p.add_argument("--effort", default="medium")
    p.add_argument(
        "--check-coverage",
        action="store_true",
        help=(
            "report servable depth for every (stage, theme) pair and exit NON-ZERO "
            "while any is below --target, then exit. No API calls, nothing spent. "
            "This is the post-deploy gate (§7, DEVIATIONS 8.41)"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "print what would be generated and roughly what it would cost, then "
            "exit. No API calls (DEVIATIONS 8.45)"
        ),
    )
    p.add_argument(
        "--backfill",
        action="store_true",
        help=(
            "enrich existing servable stories whose questions are missing or have "
            "never been through §13 moderation, then exit"
        ),
    )
    p.add_argument(
        "--grammar-backfill",
        action="store_true",
        help=(
            "re-run §13 over servable story BODIES that carry no `well_formed` "
            "verdict, reject the ungrammatical ones, then exit (DEVIATIONS 6.25)"
        ),
    )
    p.add_argument(
        "--cue-repair",
        action="store_true",
        help=(
            "re-cue tap-a-word rows whose hint word points at an option by shape, "
            "at every status, then exit (DEVIATIONS 7.14/7.15)"
        ),
    )
    p.add_argument(
        "--only", help="restrict --backfill / --grammar-backfill to these comma-separated ids"
    )
    a = p.parse_args()
    only = [int(x) for x in a.only.split(",")] if a.only else None
    try:
        if a.check_coverage:
            asyncio.run(run_coverage_check(a.target))
        elif a.cue_repair:
            asyncio.run(run_cue_repair(only=only))
        elif a.grammar_backfill:
            asyncio.run(run_grammar_backfill(only=only))
        elif a.backfill:
            asyncio.run(run_backfill(only=only))
        else:
            asyncio.run(_run(a))
    finally:
        # §15: Langfuse batches exports on a background thread. A CLI that runs
        # for seconds can exit with every span still queued — the integration
        # then looks fine and records nothing. Flush explicitly, including on
        # the `raise SystemExit` paths above. (`obs` also registers an atexit
        # hook, so a hard exit elsewhere is still covered.)
        obs.flush()


if __name__ == "__main__":
    main()
