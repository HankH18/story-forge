"""Generate -> validate -> rewrite loop (§6, §7).

The thesis in code: the model drafts, the validator judges, and the validator is
never softened to let a draft through. When a draft fails, the rewrite node gets
the *specific* violating words and what is wrong with each — not "try again".

    fetch vocabulary (DB)
      └─ GENERATE ──► VALIDATE ──pass──► done
                          │fail
                          └─► REWRITE (attempt <= 3) ──► VALIDATE
                                   │ exhausted
                                   └─► rejected (Phase 3 warm pool takes over)

This is the subset of §7's state machine that eval-smoke needs. The full machine
(cache check, moderation, enrichment, warm-pool fallback) lands with the API.
"""

from __future__ import annotations

import time
from datetime import date

from pydantic import BaseModel, Field
from sqlalchemy import text

from storyforge import obs
from storyforge.decoder import TextReport, mastered_ids, validate_text
from storyforge.llm.client import GENERATION_TIMEOUT_S, generation_model, get_client
from storyforge.llm.generate import (
    SYSTEM_RULES,
    StoryDraft,
    build_request_block,
    build_vocabulary_block,
)
from storyforge.llm.moderate import ModerationResult, moderate
from storyforge.llm.pricing import Usage, usage_from_response

# §6: the prompt gets the top-N allowed words by frequency, not the whole table.
PROMPT_WORD_LIMIT = 400
# §7: bounded rewrite budget. Never raised to make a draft pass.
MAX_REWRITES = 3
# §1 / §7: the gate. Never lowered.
DECODABILITY_GATE = 0.95
MAX_STORY_WORDS = 3


class Vocabulary(BaseModel):
    stage_id: int
    stage_name: str
    target_min: int
    target_max: int
    allowed_words: list[str]
    sight_words: list[str]
    story_words: dict[str, str]


class Attempt(BaseModel):
    n: int
    kind: str  # "generate" | "rewrite"
    text: str
    decodability: float
    violations: list[str]
    usage: Usage
    # §14 wants latency PER NODE, and the pipeline had no clock at all — a
    # harness timing `run_pipeline` from outside can only ever report one number
    # for four nodes (DEVIATIONS 7.42). Both default to 0.0, so every existing
    # caller constructs an Attempt unchanged and no control flow moves.
    latency_ms: float = 0.0  # GENERATING / REWRITING — the API call
    validate_ms: float = 0.0  # VALIDATING — the pure function


class PipelineResult(BaseModel):
    stage_id: int
    theme: str
    passed: bool
    attempts: list[Attempt] = Field(default_factory=list)
    final_text: str = ""
    final_report: TextReport | None = None
    title: str = ""
    story_words: list[str] = Field(default_factory=list)
    moderation: ModerationResult | None = None
    # §15: persisted on `stories.langfuse_trace_id`. None whenever tracing is off
    # or failed — the run itself is unaffected either way.
    langfuse_trace_id: str | None = None

    # §14 per-node latency (DEVIATIONS 7.42). `moderation_ms` is None when the
    # story never reached MODERATING, which is a different fact from "moderation
    # took 0 ms".
    vocabulary_ms: float = 0.0
    moderation_ms: float | None = None
    total_ms: float = 0.0

    @property
    def rewrite_depth(self) -> int:
        return max(0, len(self.attempts) - 1)

    def total_cost_usd(self, on) -> float:
        cost = sum(a.usage.cost_usd(on) for a in self.attempts)
        if self.moderation is not None and self.moderation.usage is not None:
            cost += self.moderation.usage.cost_usd(on)
        return cost

    def generation_cost_usd(self, on) -> float:
        """GENERATING + REWRITING only — the Sonnet half of the bill."""
        return sum(a.usage.cost_usd(on) for a in self.attempts)

    def moderation_cost_usd(self, on) -> float:
        """MODERATING only. 0.0 when layer 1 short-circuited (no API call)."""
        if self.moderation is None or self.moderation.usage is None:
            return 0.0
        return self.moderation.usage.cost_usd(on)


async def load_vocabulary(session, stage_id: int, theme_slug: str) -> Vocabulary:
    stage = (
        await session.execute(
            text(
                "SELECT name, target_len_min, target_len_max FROM stages WHERE id = :s"
            ),
            {"s": stage_id},
        )
    ).first()
    if stage is None:
        raise SystemExit(f"stage {stage_id} not seeded — run `make seed`")

    allowed = [
        r[0]
        for r in await session.execute(
            text(
                "SELECT word FROM allowed_words WHERE stage_id = :s "
                "ORDER BY freq_rank LIMIT :n"
            ),
            {"s": stage_id, "n": PROMPT_WORD_LIMIT},
        )
    ]
    if not allowed:
        raise SystemExit(f"no allowed_words for stage {stage_id} — run `make precompute`")

    sight = [
        r[0]
        for r in await session.execute(
            text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": stage_id}
        )
    ]
    story = {
        r[0]: r[1]
        for r in await session.execute(
            text(
                "SELECT w.word, w.gloss FROM story_word_bank w "
                "JOIN themes t ON t.id = w.theme_id "
                "WHERE t.slug = :t AND w.min_stage <= :s"
            ),
            {"t": theme_slug, "s": stage_id},
        )
    }
    return Vocabulary(
        stage_id=stage_id,
        stage_name=stage[0],
        target_min=stage[1],
        target_max=stage[2],
        allowed_words=allowed,
        sight_words=sight,
        story_words=story,
    )


def draft_text(draft: StoryDraft) -> str:
    return " ".join(seg.text.strip() for seg in draft.segments).strip()


def validate_draft(vocab: Vocabulary, body: str, used_story_words: list[str]) -> TextReport:
    """Validate a draft against the stage's mastered GPCs.

    `used_story_words` is the model's own CLAIM about which story words it used,
    and story words are excluded from the decodability denominator — so an
    unchecked claim lets the generator shrink its own denominator and inflate its
    score. Observed in testing: a draft declared "king", "big", "class" and "red"
    as story words, none of which were ever offered, cutting the scored word
    count from 11 to 6 and reporting 100%.

    §0: the LLM is an untrusted generator; a pure function is the gate. So the
    claim is intersected with the words actually OFFERED from the story-word
    bank. Anything invented falls through to normal scoring.
    """
    offered = {w.lower() for w in vocab.story_words}
    honoured = frozenset(w.lower() for w in used_story_words) & offered
    return validate_text(
        body,
        mastered_ids(vocab.stage_id),
        frozenset(vocab.sight_words),
        honoured,
    )


def passes_gate(report: TextReport) -> bool:
    """§7: pass requires >=95% decodability AND zero OOV AND <=3 story words."""
    return (
        report.decodability >= DECODABILITY_GATE
        and report.counts.get("oov", 0) == 0
        # DISTINCT, not occurrences: §4 caps how many new picture-chip words a
        # child meets, and repeating one is exactly what a decodable reader does.
        and len(report.distinct_story_words) <= MAX_STORY_WORDS
    )


def nearest_allowed(word: str, allowed: list[str], limit: int = 5) -> list[str]:
    """§6's explicit v1 fallback: same-initial-letter frequency neighbours.

    The doc is emphatic — do NOT build an embedding service for this. `allowed`
    arrives frequency-ordered, so a prefix scan already yields common words first.
    """
    if not word:
        return []
    same = [w for w in allowed if w.startswith(word[0]) and w != word]
    return same[:limit]


def build_rewrite_block(report: TextReport, vocab: Vocabulary, body: str) -> str:
    """§6: the rewrite node sees only the violating words, why each failed, and
    concrete replacements — not the whole story and not a bare 'try again'."""
    lines = []
    for w in report.violations:
        if w.missing_gpcs:
            why = f"uses letter-sounds not yet taught ({', '.join(w.missing_gpcs)})"
        else:
            why = "cannot be sounded out with any taught letter-sounds"
        swaps = nearest_allowed(w.word, vocab.allowed_words)
        lines.append(
            f'- "{w.word}" — {why}. Replace with one of: '
            + (", ".join(swaps) if swaps else "any permitted word")
        )

    oov = [w.word for w in report.words if w.verdict.value == "oov"]
    if oov:
        lines.append(f"- Unknown words that must be removed entirely: {', '.join(oov)}")

    return (
        "Your story used words this child cannot read. Rewrite it.\n\n"
        f"THE STORY YOU WROTE:\n{body}\n\n"
        f"PROBLEMS ({len(lines)}):\n" + "\n".join(lines) + "\n\n"
        "Keep the same story and the same length. Change only what is listed. "
        "Every replacement must come from the permitted, sight, or story lists."
    )


async def _call(vocab: Vocabulary, theme: str, user_block: str, effort: str):
    client = get_client().with_options(timeout=GENERATION_TIMEOUT_S)
    model = generation_model()
    response = await client.messages.parse(
        model=model,
        max_tokens=8000,
        output_config={"effort": effort},
        system=[
            {"type": "text", "text": SYSTEM_RULES},
            {
                "type": "text",
                "text": build_vocabulary_block(
                    vocab.stage_name, vocab.allowed_words, vocab.sight_words
                ),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user_block}],
        output_format=StoryDraft,
    )
    if response.parsed_output is None:
        raise ValueError(f"schema parse failed (stop_reason={response.stop_reason})")
    return response.parsed_output, usage_from_response(model, response.usage)


def _trace_summary(result: PipelineResult) -> dict:
    """The trace's output payload (§15). Must not raise — it is evaluated on the
    generation path, so a telemetry-shaped failure here would fail the run."""
    try:
        cost = round(result.total_cost_usd(date.today()), 6)
    except Exception:  # noqa: BLE001 — an unpriced model costs us a number, not a story
        cost = None
    return {
        "passed": result.passed,
        "title": result.title,
        "text": result.final_text,
        "decodability": result.final_report.decodability if result.final_report else None,
        "rewrite_depth": result.rewrite_depth,
        "story_words": result.story_words,
        "cost_usd": cost,
        "moderation": (
            {"approved": result.moderation.approved, "layer": result.moderation.layer}
            if result.moderation
            else None
        ),
    }


async def run_pipeline(
    session,
    stage_id: int,
    theme_slug: str,
    effort: str = "medium",
    moderate_output: bool = True,
) -> PipelineResult:
    t_run = time.perf_counter()
    t_vocab = time.perf_counter()
    vocab = await load_vocabulary(session, stage_id, theme_slug)
    result = PipelineResult(stage_id=stage_id, theme=theme_slug, passed=False)
    result.vocabulary_ms = (time.perf_counter() - t_vocab) * 1000

    user_block = build_request_block(
        theme_slug, vocab.story_words, vocab.target_min, vocab.target_max
    )
    kind = "generate"

    # §15: one trace per pipeline run, one span per §7 node. `obs` is inert when
    # Langfuse is unconfigured or broken, so this block behaves identically
    # whether or not anyone is watching.
    with obs.span(
        obs.TRACE_NAME,
        input={
            "stage_id": stage_id,
            "stage": vocab.stage_name,
            "theme": theme_slug,
            "effort": effort,
        },
        metadata={"stage_id": stage_id, "theme": theme_slug, "moderated": moderate_output},
    ) as trace:
        result.langfuse_trace_id = trace.trace_id

        for n in range(MAX_REWRITES + 1):
            with obs.generation(
                "GENERATING" if kind == "generate" else "REWRITING",
                input=user_block,
                model=generation_model(),
                model_parameters={"max_tokens": 8000, "effort": effort},
                metadata={"attempt": n, "stage_id": stage_id, "theme": theme_slug},
            ) as gen_span:
                t_node = time.perf_counter()
                draft, usage = await _call(vocab, theme_slug, user_block, effort)
                gen_ms = (time.perf_counter() - t_node) * 1000
                body = draft_text(draft)
                gen_span.update(
                    output={
                        "title": draft.title,
                        "text": body,
                        "story_words_claimed": draft.story_words_used,
                    },
                    usage=usage,
                )

            with obs.span(
                "VALIDATING",
                input={"text": body, "story_words_claimed": draft.story_words_used},
                metadata={"attempt": n, "stage_id": stage_id},
            ) as val_span:
                t_node = time.perf_counter()
                report = validate_draft(vocab, body, draft.story_words_used)
                gate = passes_gate(report)
                val_ms = (time.perf_counter() - t_node) * 1000
                val_span.update(
                    output={
                        "decodability": report.decodability,
                        "counts": report.counts,
                        "violations": [w.word for w in report.violations],
                        "distinct_story_words": report.distinct_story_words,
                        "passes_gate": gate,
                    },
                    level=None if gate else "WARNING",
                    status_message=None if gate else "below the gate — rewriting",
                )

            result.attempts.append(
                Attempt(
                    n=n,
                    kind=kind,
                    text=body,
                    decodability=report.decodability,
                    violations=[w.word for w in report.violations],
                    usage=usage,
                    latency_ms=gen_ms,
                    validate_ms=val_ms,
                )
            )
            result.final_text = body
            result.final_report = report

            if gate:
                result.title = draft.title
                result.story_words = report.distinct_story_words
                if moderate_output:
                    # §7: MODERATING sits between the validator passing and
                    # PERSISTED. A story that clears the decodability gate but
                    # fails moderation is rejected, not served.
                    with obs.generation(
                        "MODERATING",
                        input=body,
                        metadata={"attempt": n, "stage_id": stage_id},
                    ) as mod_span:
                        t_node = time.perf_counter()
                        result.moderation = await moderate(body)
                        result.moderation_ms = (time.perf_counter() - t_node) * 1000
                        result.passed = result.moderation.approved
                        mod_span.update(
                            output={
                                "approved": result.moderation.approved,
                                "layer": result.moderation.layer,
                                "reason": result.moderation.reason,
                                "blocked_words": result.moderation.blocked_words,
                                # 4.12: `approved` already folds this in, so the
                                # gate needs nothing more — but without it on the
                                # span a grammar rejection is indistinguishable
                                # from a safety one in Langfuse.
                                "well_formed": result.moderation.well_formed,
                            },
                            usage=result.moderation.usage,
                            level=None if result.passed else "WARNING",
                        )
                else:
                    result.passed = True
                result.total_ms = (time.perf_counter() - t_run) * 1000
                trace.update(output=_trace_summary(result))
                return result

            # §0: fix the prompt, never the gate.
            user_block = build_rewrite_block(report, vocab, body)
            kind = "rewrite"

        # §7: budget_exhausted. Not an error — the warm pool takes over.
        result.total_ms = (time.perf_counter() - t_run) * 1000
        trace.update(
            output=_trace_summary(result),
            level="WARNING",
            status_message="budget_exhausted",
        )
        return result
