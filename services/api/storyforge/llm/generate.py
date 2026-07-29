"""Story generation node (§6).

The primary lever is **allowlist-constrained generation, not retry pressure**
(§6). The prompt hands the model the exact vocabulary it may use; the validator
(§5) is the gate that proves it complied.

Prompt layout is chosen for cache economics (see shared prompt-caching rules):
render order is tools -> system -> messages, so the STABLE material goes in
`system` and the per-request material goes in `messages`.

    system[0]  frozen rules            never changes  ─┐ cached together,
    system[1]  stage vocabulary        changes per stage┘ keyed per stage
    messages   theme + story words + length  per request (after the breakpoint)

Generating N stories for one stage therefore pays the vocabulary cost once.

**The story also has to contain the material the stage teaches** (DEVIATIONS
5.17). §8 promotes on accuracy over the GPCs introduced AT the child's stage,
and the only evidence source is a tap-a-word question — which can only ask about
a grapheme the STORY contains. Stage 4 teaches nothing but word-final `-s -es
-ed -ing`, so a story that happens to use no suffixed word leaves promotion
unreachable however well the child reads it. The allowlist already holds the
words that would fix that; the prompt simply never pointed at them.
`practice_words` names them, inside the cached half of the prompt.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field

from storyforge.decoder import WordVerdict, load_gpcs, mastered_ids, validate_word
from storyforge.llm.client import GENERATION_TIMEOUT_S, generation_model, get_client
from storyforge.llm.pricing import Usage, usage_from_response

# Imported, never reimplemented: the prompt must point at the words the GRADER
# credits. A second copy of "which GPCs does this word use" would drift, and the
# drift would be invisible — stories would look right and earn nothing.
from storyforge.mastery import MAX_STAGE, MIN_STAGE, word_gpc_ids

# §13's deterministic layer, the same list `llm/moderate.py` screens finished
# stories with. A shortlist a story writer is told to PREFER has to clear it —
# a word is far likelier to reach a child from a 24-word "use these" list than
# from rank 300 of a flat 400-word allowlist.
from storyforge.precompute import load_blocklist

# Frozen. Any edit invalidates every cached prefix, so keep per-request detail out.
SYSTEM_RULES = """You write decodable stories for children learning to read.

A decodable story uses ONLY words the child has been taught to sound out. This is
not a style preference — a word outside the permitted list is unreadable for this
child and makes the story useless.

Rules, in priority order:
1. Every word must come from the PERMITTED WORDS list, the SIGHT WORDS list, or
   the STORY WORDS list. There are no other options. If you cannot express an
   idea with those words, change the idea.
2. Never inflect or modify a permitted word. If the list has "run" you may not
   write "running" unless "running" is itself listed.
3. Use at most the story words you are given, and only those.
4. Every sentence must be correct English. Subject and verb agree, verb tense is
   right, and no word is missing or left over. A small word list tempts you to
   grab a listed word that fits the sounds but breaks the sentence:
   "He did not came back." and "It had a got time." are both wrong.
   Never do that. Change what happens in the story instead, so that a sentence
   you can write correctly is enough to say it.
5. Short sentences. Concrete actions. A beginning, a middle, and an end.
6. This register sounds simple by design. "Sam sat. A cat sat on Sam." is
   correct output, not a first draft to improve on. Simple is not the same as
   broken: keep it plain AND grammatical.

Read every sentence back before you finish. Write the story and nothing else."""


class StorySegment(BaseModel):
    text: str = Field(description="One screen of story text. Plain prose, no markdown.")


class StoryDraft(BaseModel):
    """Structured generation output.

    §6 calls for "structured outputs enforced via Anthropic tool-use JSON schema".
    The API now has native structured outputs (`output_config.format`), which
    constrains the response directly instead of routing through a tool call —
    strictly better here (no tool-call parsing, no risk of a text response when a
    tool call was expected). Logged as docs/DEVIATIONS.md 3.1.
    """

    title: str = Field(description="Three to six words, from the permitted vocabulary.")
    segments: list[StorySegment] = Field(description="Story screens in reading order.")
    story_words_used: list[str] = Field(
        description="Which of the offered story words actually appear in the text."
    )


class GenerationResult(BaseModel):
    draft: StoryDraft
    usage: Usage
    stop_reason: str | None = None


# --- the stage's newly-taught sounds (DEVIATIONS 5.17) ------------------------
#: How many practice words to name. Long enough that three themes can each find
#: something usable, short enough that the list reads as a hint rather than as a
#: second, narrower allowlist.
PRACTICE_WORD_LIMIT = 24

#: Above this share of the allowlist, the stage's new sounds are unavoidable —
#: a stage-1 story cannot help but use stage-1 letters — and naming a sample of
#: them would only shrink the vocabulary the model feels free to use. The whole
#: point of the section is stages where the new material is SCARCE.
PRACTICE_SATURATION = 0.5

#: The section header. Named because the request block does not know the stage
#: and so cannot reference it; the ask travels with the list instead.
PRACTICE_HEADER = "NEW SOUNDS AT THIS STAGE"


@lru_cache(maxsize=16)
def _stage_gpc_ids(stage: int) -> frozenset[str]:
    """The GPC ids INTRODUCED at `stage` — §8's promotion denominator.

    `mastered_ids` is cumulative; this is the thin slice the stage teaches.
    """
    return frozenset(g.id for g in load_gpcs() if g.stage == stage)


@lru_cache(maxsize=8192)
def _first_decodable_stage(word: str) -> int | None:
    """The earliest stage whose mastered set can sound `word` out, or None.

    Decodability is monotone in stage (the inventory is cumulative), so this is
    well defined, and it is exactly `precompute`'s `min_stage`.
    """
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        if validate_word(word, mastered_ids(stage)).verdict is WordVerdict.DECODABLE:
            return stage
    return None


def allowlist_stage(allowed_words: tuple[str, ...]) -> int | None:
    """Which stage this allowlist belongs to, read off the allowlist itself.

    `build_vocabulary_block` is handed a stage NAME, not a stage id, and it is
    called from three places (here, the rewrite loop, the question node) that
    would all have to change to pass one. It does not need to: the allowlist for
    stage N is every word decodable at N, so the newest sound it represents IS N.

    One honest limitation. Stage 12 introduces no GPCs at all — it lifts the
    syllable cap and reviews — so its allowlist looks like stage 11's and this
    returns 11. That is the right answer to the question the caller is really
    asking ("what are the newest sounds in here?"), and §8 cannot promote out of
    stage 12 anyway, so no evidence is owed at that stage.
    """
    stages = [s for s in (_first_decodable_stage(w) for w in allowed_words) if s is not None]
    return max(stages) if stages else None


@lru_cache(maxsize=32)
def practice_words(
    allowed_words: tuple[str, ...],
    sight_words: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Allowlist words that need a GPC this stage is the first to teach.

    These are the only words in the prompt that can earn a stage-4 reader a step
    toward promotion, and `llm/questions.py` cannot conjure one the story never
    used. Frequency order is inherited from `allowed_words`, so the head of the
    list is the part a five-year-old story can actually carry.

    Sight words are dropped: `questions.sound_targets` refuses them as tap-a-word
    options (the child recognised the shape, they did not decode it), so a story
    that satisfies the ask with "is" would satisfy nothing.

    Blocklisted words are dropped too, and they still count in the denominator —
    removing a word must never make the list look scarcer than the stage is.

    Empty when the stage teaches nothing new, and empty when the new sounds
    saturate the allowlist — see `PRACTICE_SATURATION`.
    """
    stage = allowlist_stage(allowed_words)
    if stage is None:
        return ()
    introduced = _stage_gpc_ids(stage)
    if not introduced:
        return ()

    blocked = load_blocklist()
    candidates = 0
    picked: list[str] = []
    seen: set[str] = set()
    for raw in allowed_words:
        word = raw.strip().lower()
        if not word or word in seen:
            continue
        seen.add(word)
        if word in sight_words:
            continue
        candidates += 1
        if word in blocked:
            continue
        if introduced & set(word_gpc_ids(word, stage)):
            picked.append(word)

    if not picked or len(picked) > candidates * PRACTICE_SATURATION:
        return ()
    return tuple(picked[:PRACTICE_WORD_LIMIT])


def build_practice_block(allowed_words: list[str], sight_words: list[str]) -> str:
    """The practice section, or "" when the stage has nothing scarce to practise.

    A PREFERENCE, stated as one. A stage whose sounds genuinely cannot appear in
    a 30-word story, or a story too short to fit one, must still be a valid story
    — the gate is decodability (§0), never this.
    """
    practice = practice_words(
        tuple(allowed_words),
        frozenset(w.strip().lower() for w in sight_words if w and w.strip()),
    )
    if not practice:
        return ""
    return (
        f"\n\n{PRACTICE_HEADER} ({len(practice)}) — permitted words that use a "
        "letter-sound this stage is the first to teach:\n"
        + ", ".join(practice)
        + "\n\nThese are what the child is learning to sound out right now, so "
        "prefer them where a sentence can carry one naturally. That is a "
        "preference and never a licence to break a rule above: a correct, plain "
        "sentence using none of them beats a bent sentence using three, and text "
        "that happens to use none of them is still fine."
    )


def build_vocabulary_block(
    stage_name: str,
    allowed_words: list[str],
    sight_words: list[str],
) -> str:
    """The stage's vocabulary — stable across every story for a stage, so this is
    the cached half of the prompt.

    The practice section is stage-derived and therefore belongs HERE rather than
    in the request block: it is byte-identical for every story at a stage, so it
    is written to the prompt cache once and read back at 0.1x on every later
    request. Putting it in `messages` would re-send it as fresh input every time
    for no benefit, and putting it in `SYSTEM_RULES` would be worse still — that
    prefix is frozen precisely so per-stage material cannot invalidate it.
    """
    return (
        f"STAGE: {stage_name}\n\n"
        f"PERMITTED WORDS ({len(allowed_words)}), most common first:\n"
        + ", ".join(allowed_words)
        + f"\n\nSIGHT WORDS ({len(sight_words)}) — permitted, learned by sight:\n"
        + ", ".join(sorted(sight_words))
        + build_practice_block(allowed_words, sight_words)
    )


def build_request_block(
    theme: str,
    story_words: dict[str, str],
    target_min: int,
    target_max: int,
) -> str:
    offered = (
        "\n".join(f"- {w}: {gloss}" for w, gloss in sorted(story_words.items()))
        or "- (none)"
    )
    return (
        f"Write a {theme} story of roughly {target_min}-{target_max} words.\n\n"
        f"STORY WORDS you may use (at most 3, only from this list):\n{offered}\n\n"
        "Every other word must come from the permitted or sight lists above."
    )


async def generate_story(
    *,
    stage_name: str,
    allowed_words: list[str],
    sight_words: list[str],
    theme: str,
    story_words: dict[str, str],
    target_min: int,
    target_max: int,
    effort: str = "medium",
) -> GenerationResult:
    """One generation attempt. Returns the draft plus token accounting.

    Does NOT validate — that is the caller's job via `storyforge.decoder`. Keeping
    generation and validation separate is the whole architecture: the model is an
    untrusted generator, the validator is the gate (§0).
    """
    client = get_client().with_options(timeout=GENERATION_TIMEOUT_S)
    model = generation_model()

    response = await client.messages.parse(
        model=model,
        # Generous: on Sonnet 5 adaptive thinking is ON when `thinking` is
        # omitted, and thinking counts against max_tokens. A tight budget here
        # yields a response that is mostly thinking plus a truncated story
        # (stop_reason "max_tokens"), which looks like a bad model rather than a
        # bad parameter.
        max_tokens=8000,
        output_config={"effort": effort},
        system=[
            {"type": "text", "text": SYSTEM_RULES},
            {
                "type": "text",
                "text": build_vocabulary_block(stage_name, allowed_words, sight_words),
                # Breakpoint at the end of the stable prefix: every story for
                # this stage reads the vocabulary from cache.
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[
            {
                "role": "user",
                "content": build_request_block(theme, story_words, target_min, target_max),
            }
        ],
        output_format=StoryDraft,
    )

    if response.parsed_output is None:
        raise ValueError(
            f"structured output failed to parse (stop_reason={response.stop_reason})"
        )

    return GenerationResult(
        draft=response.parsed_output,
        usage=usage_from_response(model, response.usage),
        stop_reason=response.stop_reason,
    )
