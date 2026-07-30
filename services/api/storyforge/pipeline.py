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
from functools import lru_cache

from pydantic import BaseModel, Field
from sqlalchemy import text

from storyforge import obs
from storyforge.decoder import (
    MAX_STAGE,
    TextReport,
    WordVerdict,
    mastered_ids,
    validate_text,
    validate_word,
)
from storyforge.llm.client import generation_model, generation_timeout, get_client
from storyforge.llm.generate import (
    MAX_OUTPUT_TOKENS,
    StoryDraft,
    build_request_block,
    generation_system_blocks,
)
from storyforge.llm.moderate import ModerationResult, child_facing_text, moderate
from storyforge.llm.narrative import NarrativeResult, judge_narrative
from storyforge.llm.pricing import Usage, usage_from_response

# §6: the prompt gets the top-N allowed words by frequency, not the whole table.
PROMPT_WORD_LIMIT = 400
# §7: bounded rewrite budget. Never raised to make a draft pass.
MAX_REWRITES = 3
# §1 / §7: the gate. Never lowered.
DECODABILITY_GATE = 0.95
MAX_STORY_WORDS = 3

#: §5.5's ceiling, and the number that was missing entirely (DEVIATIONS 14.20).
#:
#: A story word is a word the child is GIVEN — a picture chip with tap-to-hear,
#: excluded from the decodability denominator — instead of decoding. There are
#: `MAX_STORY_WORDS` = 3 of them per story and no more, so every slot spent is a
#: slot bought. Until now `story_word_bank.min_stage` was the only bound on which
#: words could fill one, and `min_stage` is a hand-set SEMANTIC floor ("is this
#: concept age-appropriate here") with no relationship whatever to whether the
#: child can read the word. Measured over the live pool: **225 of 326 picture-chip
#: slots (69.0%) were spent on words the reader could already decode at their own
#: stage**, and `orbit` — decodable at stage 1, `min_stage` 4 — could only ever be
#: offered where it was useless.
#:
#: The rule, as one sentence: *a story word must be a word the reader cannot
#: decode at their own stage, and must either be permanently undecodable within
#: this GPC inventory or become decodable within `STORY_WORD_MAX_LOOKAHEAD`
#: stages.*
#:
#: Why both ends, and why "never" sits on the permitted side of the ceiling
#: rather than falling off it:
#:
#: - **Floor (>= 1).** A chip over a word the child can sound out does three bad
#:   things at once: it spends one of three slots, it paints a picture over the
#:   exact decoding practice §1 exists to deliver, and it removes a decodable word
#:   from the scored denominator — the same denominator-shrinking STATE invariant
#:   2 refuses when the MODEL claims it. The bank claiming it is no better.
#: - **Ceiling (<= 3).** The distinction is *substitutability*. For `castle`,
#:   `penguin`, `treasure` — words the 93-GPC inventory can never align at any
#:   stage — no amount of teaching replaces the chip; it is the only route in, for
#:   the whole curriculum, and it is permanently correct. For `giraffe`
#:   (decodable at stage 8) offered to a stage-1 reader, teaching WILL replace the
#:   chip: the only question is whether the child meets the word now as a picture
#:   or in seven stages as their own decoding. Spending a scarce slot on a word
#:   the curriculum is about to teach properly is whole-word memorisation bought
#:   at the cost of a picture chip a permanently-undecodable word needed.
#:
#: Three, not two or four, is where the bank stops being able to fill the slot:
#: at 3 every (theme, stage) keeps at least one candidate, at 2 the stage-1
#: dragons offer loses `cave` and `fire` and §18's "themes at stages 1-2 lean on
#: story words + pictures for interest" starts to bite. It is a lookahead in
#: STAGES rather than in GPCs because that is the unit the child's own progress is
#: measured in (§8 promotes a stage at a time), so "three stages" is "the next
#: three things you will be taught".
STORY_WORD_MAX_LOOKAHEAD = 3


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
    # DEVIATIONS 11.47. A MEASUREMENT, never a gate: `passed` is computed above
    # without reading this field, and `tests/test_narrative.py` sets the worst
    # possible verdict and asserts the story still passes. None means the judge was
    # not asked, which is the default and is a different fact from "scored badly"
    # (a failed judge records `score = 0`, below `SCORE_MIN`).
    narrative: NarrativeResult | None = None
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

    def narrative_cost_usd(self, on) -> float:
        """The narrative judge only, and deliberately OUTSIDE `total_cost_usd`.

        `total_cost_usd` is what it costs to PRODUCE a story, and that is the figure
        `docs/EVAL.md` reports as cost per accepted story. The narrative judge is
        instrumentation: it is off by default, it never runs in `make warm`, and
        folding it in would make a before/after comparison of production cost read
        as a cost regression when the only thing that changed is that the after run
        was measured. Reported on its own line instead.
        """
        if self.narrative is None or self.narrative.usage is None:
            return 0.0
        return self.narrative.usage.cost_usd(on)


# --- §5.5's ceiling on a story word (DEVIATIONS 14.20) -----------------------


@lru_cache(maxsize=8192)
def decodable_from_stage(word: str) -> int | None:
    """The first stage whose mastered GPCs admit a complete alignment of `word`.

    `None` is "never within this inventory" — `castle`, `penguin`, `treasure`,
    `alien`, `astronaut`, `scales`, `feathers`, `burrow` are all `None` today, and
    that is a fact about English, not a defect: the 93-GPC inventory has no path
    for a silent `t` or `su` -> /ZH/ at any stage. It is a categorically different
    answer from a large number, and every caller here treats it that way.

    Cached because the answer is a pure function of the word and the frozen
    inventory, and a story-word gate over the whole pool asks it thousands of
    times. The decoder's own `lru_cache` on the lattice does the heavy half.
    """
    lowered = word.strip().lower()
    if not lowered:
        return None
    for stage in range(1, MAX_STAGE + 1):
        if validate_word(lowered, mastered_ids(stage)).verdict is WordVerdict.DECODABLE:
            return stage
    return None


def story_word_lookahead(word: str, stage_id: int) -> int | None:
    """How many stages of instruction stand between this reader and this word.

    `<= 0` means the reader can already decode it — a picture chip would be
    spent teaching nothing. `None` means they never will be able to, which is the
    case a picture chip is FOR.
    """
    first = decodable_from_stage(word)
    return None if first is None else first - stage_id


def story_word_is_eligible(
    word: str, stage_id: int, lookahead: int = STORY_WORD_MAX_LOOKAHEAD
) -> bool:
    """§5.5's band — see `STORY_WORD_MAX_LOOKAHEAD` for the rule and the measurement."""
    ahead = story_word_lookahead(word, stage_id)
    return ahead is None or 1 <= ahead <= lookahead


def story_word_refusal(
    word: str, stage_id: int, lookahead: int = STORY_WORD_MAX_LOOKAHEAD
) -> str | None:
    """Why `word` may not be a story word at `stage_id`, or `None` if it may.

    A sentence rather than a boolean, because the two refusals have opposite
    remedies — one wastes a slot, the other misuses one — and a gate that printed
    "ineligible" for both would teach an operator nothing.
    """
    ahead = story_word_lookahead(word, stage_id)
    if ahead is None or 1 <= ahead <= lookahead:
        return None
    first = decodable_from_stage(word)
    if ahead <= 0:
        return (
            f"already decodable at stage {first} — a stage-{stage_id} reader can "
            f"sound it out, so the chip spends one of {MAX_STORY_WORDS} slots "
            "hiding the practice, and drops a decodable word out of the "
            "decodability denominator"
        )
    return (
        f"decodable at stage {first}, {ahead} stages ahead of a stage-{stage_id} "
        f"reader (ceiling {lookahead}) — the curriculum will teach this word "
        "properly before the chip could ever be cashed in"
    )


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
    # Two filters, and they are answering different questions. `min_stage` is the
    # SEMANTIC floor a human set — is this concept age-appropriate here — and only
    # the seed file knows it. `story_word_is_eligible` is the DECODABILITY band and
    # only the decoder knows it. Enforced here, at generation time, so the model is
    # never offered a word it must not spend a chip on; `validate_draft` asks the
    # same question again on the way back, because a Vocabulary can also be built
    # by hand (DEVIATIONS 14.20).
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
        if story_word_is_eligible(r[0], stage_id)
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

    **§5.5's decodability band is NOT re-asked here, on purpose** (DEVIATIONS
    14.20). `story_word_is_eligible` is a policy about which words may be OFFERED,
    and it is enforced where the offer is built (`load_vocabulary`) and audited
    where the offer's consequences are persisted (`warm.run_story_word_check`).
    This function is a MEASUREMENT of a text against a vocabulary, and moving the
    band in here would make the same text score differently depending on a rule
    that has nothing to do with the text — including at serve time, where
    `api/stories.py::_is_decodable_for` re-scores an already-persisted story and a
    silently un-exempted word would drop a live story below the gate.
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
    """§7: pass requires TEXT, then >=95% decodability AND zero OOV AND <=3 story
    words.

    **The first clause is not a formality.** `validate_text` scores
    `counts["decodable"] / scored if scored else 1.0`, so a story with no words —
    `""`, whitespace, `"..."`, or a draft whose segments all came back empty —
    arrived here at `decodability == 1.0`, `oov == 0` and zero story words and was
    reported as FULLY DECODABLE. Every clause below was satisfied and none of them
    had been asked about anything: §1's guarantee is "every word in this story is
    one the child can read", and a story with no words satisfies that vacuously
    while proving nothing. Vacuously true is not true, and an empty story is
    never servable — a child would be handed a blank screen, press Finish, and the
    §8 evidence would record a perfect read of nothing.

    So the verdict is a hard refusal, and it is made HERE rather than in
    `validate_text` on purpose: the report is a measurement and 1.0 is the honest
    value of a ratio with an empty denominator, while this is the judgement, and
    the judgement is what §7's state machine reads. `run_pipeline` therefore
    rewrites and finally rejects an empty draft instead of paying for moderation
    and persisting it `warm`. `api/stories.py::_is_decodable_for` already scored an
    empty body 0.0 at serve time (DEVIATIONS 7.23); this is the same answer moved
    to where the story is still preventable.

    A text of nothing but SIGHT or STORY words also has `scored == 0` and is NOT
    refused: those are words, the child can read them, and 1.0 is true of them.
    The refusal is for no words at all.
    """
    if report.word_count == 0:
        return False
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


def _quoted(words: list[str]) -> str:
    return ", ".join(f'"{w}"' for w in words)


def story_word_verdict(
    report: TextReport, cap: int = MAX_STORY_WORDS
) -> tuple[list[str], list[str]]:
    """Split the DISTINCT story words into (keep, drop) against the cap.

    DEVIATIONS 9.42: the code decides which word goes, not the model. Ranked by
    occurrence count descending — the most-repeated word is the one the story
    leans on, so dropping the rarest is the smallest edit — with alphabetical
    tie-breaking so the choice is deterministic and a test can assert it.

    `drop` is empty whenever the cap is satisfied, which is what makes this safe
    to call unconditionally.
    """
    counts: dict[str, int] = {}
    for w in report.words:
        if w.verdict is WordVerdict.STORY:
            counts[w.word] = counts.get(w.word, 0) + 1
    ranked = sorted(report.distinct_story_words, key=lambda w: (-counts.get(w, 0), w))
    return ranked[:cap], ranked[cap:]


def undeclared_story_words(report: TextReport, vocab: Vocabulary) -> list[str]:
    """The OFFERED story words the text uses but the model never declared.

    DEVIATIONS 9.75, the other half of `story_word_verdict`. `validate_draft`
    honours only the intersection of the model's `story_words_used` claim with
    the offered bank (STATE invariant 2 — the claim is untrusted), so a story
    word the model forgot to list is scored as an ordinary word, fails to align
    against the stage's taught GPCs, and lands in `report.violations`. It is not
    unreadable; it is undeclared, and one empty field is the whole difference:
    the same stage-10 text measures 62.5% having used `burrow` silently and
    100.0% having said so.

    Ranked by the same rule `story_word_verdict` uses — occurrences descending,
    alphabetical tie-break — so both halves of the story-word budget are settled
    deterministically and a test can assert the outcome.
    """
    offered = {w.lower() for w in vocab.story_words}
    counts: dict[str, int] = {}
    for w in report.violations:
        if w.word.lower() in offered:
            counts[w.word] = counts.get(w.word, 0) + 1
    return sorted(counts, key=lambda w: (-counts[w], w))


def _article(word: str) -> str:
    """`a` or `an` for a theme SLUG (DEVIATIONS 9.78).

    `f"a {theme} story"` shipped "a animals story" on every `animals` rewrite,
    inside a request whose cached `SYSTEM_RULES` prefix tells the model that
    "every sentence must be correct English".
    """
    return "an" if word[:1].lower() in "aeiou" else "a"


def build_rewrite_block(
    report: TextReport, vocab: Vocabulary, body: str, theme: str = ""
) -> str:
    """§6: the rewrite node sees only the violating words, why each failed, and
    concrete replacements — not the whole story and not a bare 'try again'.

    **Three things this block used to be unable to say, all fixed together
    (DEVIATIONS 9.40-9.45, `docs/AUDIT-PHASE7-FIXES.md` F1).**

    `passes_gate` fails on three conditions and this function could describe two.
    When a draft failed on the story-word cap ALONE — 100% decodable, zero OOV, one
    story word too many — `lines` came out empty and the model was sent a false
    premise ("used words this child cannot read"), an empty `PROBLEMS (0):` list,
    and "change only what is listed". Measured in the §14 run 114 matrix: 9 stories
    took that path and **all 9 ended with zero story words**, 8 of them dragons;
    6 of 15 dragons stories at stages 2-4 shipped with no dragon in them; one draft
    came back WORSE than the 100% it was rejected at.

    The wider cause is that the rewrite block REPLACES `build_request_block` in a
    single-message conversation (`_call` sends `messages=[user_block]`, no history),
    and the cached `system` half carries only PERMITTED and SIGHT words. So from
    attempt 1 onward the prompt demanded replacements "from the permitted, sight,
    or story lists" with the story list, the theme and the length target all
    absent. Run 114: 0 of 108 un-rewritten stories lost their story words; 23 of 72
    rewritten ones did. The bank, the theme and the bounds are therefore restated
    here — all inside `messages`, after the cache breakpoint, so invariant 10 is
    untouched.

    §5.5 / STATE invariant 3 dictate the SHAPE of the cap instruction: the cap
    counts DISTINCT words and repetition is correct decodable-reader behaviour, so
    the ask is "drop the extra one, keep repeating the rest" and never "use fewer
    story words". Removing the theme from a themed story is the defect, not a
    resolution of it.

    **The FOURTH thing it could not say (DEVIATIONS 9.75-9.77).** 9.42 covered the
    story words the model DECLARED; the opposite mistake — using an offered story
    word and forgetting to list it — arrived here as an ordinary violation, and
    the loop below told the model to delete it. One message said `burrow` was
    unreadable and must go, offered `burrow` in the STORY WORDS bank, and closed
    with "every replacement must come from the permitted, sight, or story lists",
    while `nearest_allowed` proposed `by, but, back, before, best` for a burrow.
    Live: 131 violation tokens across recorded attempts were offered story words
    for their own story's theme and stage, and story 7342 (stage 10, animals,
    `warm`) is serving four of them to children with `story_words` empty.

    So the violations are partitioned on membership of the bank first. A word
    outside the bank keeps its bullet unchanged. A word inside it never reaches
    `nearest_allowed` — it is named as a story word the model already owns.

    The trap is the cap: telling a model that already declared three to declare a
    fourth trades a decodability failure for a cap failure. Both directions are
    therefore settled against ONE budget — declared keepers take their slots
    first, undeclared words take what is left, and the remainder joins `drop`,
    because with the cap full the honest instruction really is to remove them.
    When nothing is undeclared the cap strings are byte-identical to 9.42's.
    """
    offered = {w.lower() for w in vocab.story_words}
    keep, drop = story_word_verdict(report)
    undeclared = undeclared_story_words(report, vocab)
    adopt = undeclared[: max(0, MAX_STORY_WORDS - len(keep))]
    evict = undeclared[len(adopt) :]
    #: what the model ends up holding, declared or not — the cap's only books
    kept, dropped = keep + adopt, drop + evict

    # The FIFTH thing it could not say. `passes_gate` refuses a report with no
    # words at all, and none of the three loops below can describe that: there is
    # no violation, no OOV and no story word to name, so `lines` came out empty
    # and the generic fallback told a model that had written NOTHING that its
    # story was under 95% decodable. Same shape as F1 — a false premise beside
    # "change only what is listed" — so the emptiness is named first and the ask
    # is to write the story, not to edit it.
    empty = report.word_count == 0

    lines = []
    if empty:
        lines.append(
            f"- You returned no story text at all. Write the whole story: "
            f"{vocab.target_min}-{vocab.target_max} words, using only the "
            f"permitted, sight and story words."
        )
    for w in adopt:
        lines.append(
            f'- "{w}" is one of your STORY WORDS, not a word this child cannot '
            f"read. Keep it exactly as it is and add it to story_words_used — a "
            f"declared story word is free, because the child is shown a picture "
            f"for it and hears it read aloud."
        )

    unreadable = [w for w in report.violations if w.word.lower() not in offered]
    for w in unreadable:
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

    if dropped:
        lines.append(
            f"- You used {len(kept) + len(dropped)} DIFFERENT story words "
            f"({_quoted(kept + dropped)}), and at most {MAX_STORY_WORDS} different ones "
            f"are allowed. KEEP {_quoted(kept)} and use each of them as often as you "
            f"like — repeating a story word is free and costs nothing. Remove every "
            f"use of {_quoted(dropped)} instead, and say that part with a permitted or "
            f"sight word. Do NOT drop the story words you are keeping: the story "
            f"still has to be about them."
        )

    if not lines:
        # DEVIATIONS 9.45. Unreachable from a real gate failure — decodability
        # cannot fall below 1.0 with zero violations and zero OOV, so all FOUR
        # `passes_gate` conditions are describable (the fourth, the empty-text
        # refusal, is handled at the top of `lines`) — but this is a live
        # child-facing prompt and an empty problem list beside "change only what is
        # listed" is the entirety of F1. Never ship that sentence again.
        lines.append(
            f"- The story did not pass the reading check. It must be at least "
            f"{round(DECODABILITY_GATE * 100)}% decodable, contain no unknown words, "
            f"and use at most {MAX_STORY_WORDS} different story words. Fix that "
            f"without changing what the story is about."
        )

    # 9.44 made the headline conditional so it could never be false; 9.77 keeps it
    # that way. A story whose only violations were undeclared story words did NOT
    # use words this child cannot read, so that sentence is unreachable for it.
    if empty:
        headline = "Your story came back empty. There was no text in it at all."
    elif unreadable or oov:
        headline = "Your story used words this child cannot read. Rewrite it."
    elif dropped:
        headline = (
            f"Your story is readable, but it used {len(kept) + len(dropped)} DIFFERENT "
            f"story words where at most {MAX_STORY_WORDS} are allowed. Rewrite it."
        )
    elif adopt:
        headline = (
            "Your story is readable, but it used story words without declaring "
            "them. Rewrite it."
        )
    else:
        headline = "Your story did not pass the reading check. Rewrite it."

    offered = "\n".join(f"- {w}: {gloss}" for w, gloss in sorted(vocab.story_words.items()))
    bank = (
        (
            f"\n\nSTORY WORDS — at most {MAX_STORY_WORDS} DIFFERENT ones, only from "
            f"this list:\n{offered}\n\n"
            "Repeat those freely; only the number of DIFFERENT ones is capped, never "
            "how often each appears."
        )
        if offered
        else ""
    )
    about = f" It must still be {_article(theme)} {theme} story." if theme else ""

    return (
        f"{headline}\n\n"
        f"THE STORY YOU WROTE:\n{body}\n\n"
        f"PROBLEMS ({len(lines)}):\n" + "\n".join(lines) + bank + "\n\n"
        f"Keep the same story, and keep it {vocab.target_min}-{vocab.target_max} "
        f"words long.{about} Change only what is listed. Every replacement must come "
        "from the permitted, sight, or story lists."
    )


async def _call(vocab: Vocabulary, theme: str, user_block: str, effort: str):
    # The token ceiling and the clock are the SAME bound expressed two ways
    # (DEVIATIONS 8.7), so they are derived from one number rather than chosen
    # independently — `max_tokens=8000` here versus 16000 in `llm/generate.py` was
    # the drift DEVIATIONS 8.25 flagged, and it cost live cells: a generation that
    # runs out of tokens returns `stop_reason="max_tokens"`, no parseable story,
    # and §7 scores that as api_fail. Read per call so
    # `ANTHROPIC_GENERATION_TIMEOUT_S` is live rather than frozen at import.
    client = get_client().with_options(timeout=generation_timeout(MAX_OUTPUT_TOKENS))
    model = generation_model()
    response = await client.messages.parse(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        output_config={"effort": effort},
        # Shared with `llm.generate.generate_story` rather than copied: the two
        # assembled the same prompt by hand until `max_tokens` drifted between them
        # and the LIVE node — this one — kept the old ceiling (DEVIATIONS 8.7/8.25).
        # A second stage-scaled block is a second chance to drift the same way.
        system=generation_system_blocks(
            vocab.stage_name, vocab.allowed_words, vocab.sight_words
        ),
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
        "narrative": (
            {"score": result.narrative.score, "spine": result.narrative.spine}
            if result.narrative
            else None
        ),
    }


async def run_pipeline(
    session,
    stage_id: int,
    theme_slug: str,
    effort: str = "medium",
    moderate_output: bool = True,
    judge_narrative_output: bool = False,
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
                model_parameters={"max_tokens": MAX_OUTPUT_TOKENS, "effort": effort},
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
                    #
                    # DEVIATIONS 17.1: the TITLE is judged here too. It is
                    # model-authored child-facing text and it used to reach
                    # `stories.title` — and every `StoryDTO` — having passed none
                    # of §13's three layers, because `draft_text` joins segment
                    # text and nothing else. `child_facing_text` is what makes
                    # the README's "every child-facing string" true of it.
                    judged = child_facing_text(draft.title, body)
                    with obs.generation(
                        "MODERATING",
                        input=judged,
                        metadata={"attempt": n, "stage_id": stage_id},
                    ) as mod_span:
                        t_node = time.perf_counter()
                        result.moderation = await moderate(judged)
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

                # DEVIATIONS 11.47: measurement, AFTER `passed` is already decided
                # and physically unable to change it. Off by default, so `make warm`
                # spends nothing on it; the §14 harness and
                # `scripts/measure_narrative.py` turn it on. `judge_narrative` never
                # raises (it returns `error` set), so this cannot cost a story.
                if judge_narrative_output:
                    with obs.generation(
                        "NARRATIVE",
                        input=body,
                        metadata={"attempt": n, "stage_id": stage_id},
                    ) as nar_span:
                        result.narrative = await judge_narrative(body)
                        nar_span.update(
                            output=result.narrative.model_dump(exclude={"usage"}),
                            usage=result.narrative.usage,
                        )
                result.total_ms = (time.perf_counter() - t_run) * 1000
                trace.update(output=_trace_summary(result))
                return result

            # §0: fix the prompt, never the gate. `theme_slug` is passed because
            # the rewrite block REPLACES the request block in a single-message
            # conversation, so without it the model loses the theme it is writing
            # about the moment a first draft fails (DEVIATIONS 9.40, 9.43).
            user_block = build_rewrite_block(report, vocab, body, theme=theme_slug)
            kind = "rewrite"

        # §7: budget_exhausted. Not an error — the warm pool takes over.
        result.total_ms = (time.perf_counter() - t_run) * 1000
        trace.update(
            output=_trace_summary(result),
            level="WARNING",
            status_message="budget_exhausted",
        )
        return result
