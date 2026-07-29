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
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from storyforge.llm.client import GENERATION_TIMEOUT_S, generation_model, get_client
from storyforge.llm.pricing import Usage, usage_from_response

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


def build_vocabulary_block(
    stage_name: str,
    allowed_words: list[str],
    sight_words: list[str],
) -> str:
    """The stage's vocabulary — stable across every story for a stage, so this is
    the cached half of the prompt."""
    return (
        f"STAGE: {stage_name}\n\n"
        f"PERMITTED WORDS ({len(allowed_words)}), most common first:\n"
        + ", ".join(allowed_words)
        + f"\n\nSIGHT WORDS ({len(sight_words)}) — permitted, learned by sight:\n"
        + ", ".join(sorted(sight_words))
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
