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

from pydantic import BaseModel, Field
from sqlalchemy import text

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

    @property
    def rewrite_depth(self) -> int:
        return max(0, len(self.attempts) - 1)

    def total_cost_usd(self, on) -> float:
        return sum(a.usage.cost_usd(on) for a in self.attempts)


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


async def run_pipeline(
    session,
    stage_id: int,
    theme_slug: str,
    effort: str = "medium",
    moderate_output: bool = True,
) -> PipelineResult:
    vocab = await load_vocabulary(session, stage_id, theme_slug)
    result = PipelineResult(stage_id=stage_id, theme=theme_slug, passed=False)

    user_block = build_request_block(
        theme_slug, vocab.story_words, vocab.target_min, vocab.target_max
    )
    kind = "generate"

    for n in range(MAX_REWRITES + 1):
        draft, usage = await _call(vocab, theme_slug, user_block, effort)
        body = draft_text(draft)
        report = validate_draft(vocab, body, draft.story_words_used)

        result.attempts.append(
            Attempt(
                n=n,
                kind=kind,
                text=body,
                decodability=report.decodability,
                violations=[w.word for w in report.violations],
                usage=usage,
            )
        )
        result.final_text = body
        result.final_report = report

        if passes_gate(report):
            result.title = draft.title
            result.story_words = report.distinct_story_words
            if moderate_output:
                # §7: MODERATING sits between the validator passing and
                # PERSISTED. A story that clears the decodability gate but fails
                # moderation is rejected, not served.
                result.moderation = await moderate(body)
                result.passed = result.moderation.approved
            else:
                result.passed = True
            return result

        # §0: fix the prompt, never the gate.
        user_block = build_rewrite_block(report, vocab, body)
        kind = "rewrite"

    return result
