"""Moderation node (§6, §13).

§13 requires three independent layers on every child-facing string:

    1. deterministic blocklist   <- runs FIRST, free, cannot be talked out of it
    2. allowlist-constrained generation  (the prompt + the §5 validator)
    3. this moderation pass      <- belt and suspenders

Layer 1 is a pure function and runs before any API call, so a blocked word costs
nothing and cannot be argued with. Layer 3 is a Haiku-class model judging tone
and content the other two layers cannot see — "the man hit the dog" is perfectly
decodable and perfectly blocklist-clean.

§6 is explicit that this runs *even though* the allowlist already constrains
heavily. For a child-facing product the cost of a false negative is not
symmetric with the cost of a false positive.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from storyforge.llm.client import FAST_TIMEOUT_S, fast_model, get_client
from storyforge.llm.pricing import Usage, usage_from_response
from storyforge.precompute import load_blocklist

MODERATION_SYSTEM = """You review short stories written for children aged 5-10 \
who are learning to read.

These are storybook adventures. Dragons breathe fire, caves are dark, and \
characters run and hide. That is the genre working as intended — mild peril is \
expected and must be APPROVED. The test is not "could anything here be scary?" \
but "would this distress a child reading alone at bedtime?"

APPROVE, even though they involve a hazard:
- A dragon that is big, breathes fire, roars, or flies.
- A character who is scared, runs, hides, or gets lost and is then found.
- Dark caves, storms, deep water, high places.
- A problem that gets resolved.

REJECT:
- Anyone or anything is hurt, injured, killed, or dies.
- Cruelty, bullying, or a character deliberately harming another.
- Weapons used against a character.
- Romance, substances, bodily functions, or bathroom humour.
- Real brands, real people, or real places presented as real.
- A frightening situation left unresolved at the end.

The prose will be extremely simple and repetitive. That is the reading format \
working as intended and is never a reason to reject.

Judge content and tone only. When the only concern is that a storybook hazard \
exists without anyone being harmed, approve."""


class ModerationVerdict(BaseModel):
    approved: bool = Field(description="True only if the story is safe for ages 5-10.")
    reason: str = Field(description="One sentence. If rejecting, name the specific problem.")


class ModerationResult(BaseModel):
    approved: bool
    reason: str
    layer: str  # "blocklist" | "model"
    blocked_words: list[str] = Field(default_factory=list)
    usage: Usage | None = None


def blocklist_check(body: str) -> list[str]:
    """Layer 1 — deterministic, exact-match, free (§6: "runs first, free").

    Tokenised the same way the validator tokenises, so "kill." and "Kill" are
    caught. Exact match only, per docs/DEVIATIONS.md 2.4.
    """
    from storyforge.decoder.tokenizer import tokenize

    blocked = load_blocklist()
    return sorted({t.text for t in tokenize(body) if t.is_word and t.text in blocked})


async def moderate(body: str) -> ModerationResult:
    """Run both layers. Short-circuits on the blocklist so a rejected story
    never costs an API call."""
    hits = blocklist_check(body)
    if hits:
        return ModerationResult(
            approved=False,
            reason=f"blocklisted words present: {', '.join(hits)}",
            layer="blocklist",
            blocked_words=hits,
        )

    client = get_client().with_options(timeout=FAST_TIMEOUT_S)
    model = fast_model()
    response = await client.messages.parse(
        model=model,
        max_tokens=1000,
        system=MODERATION_SYSTEM,
        messages=[{"role": "user", "content": f"Story:\n\n{body}"}],
        output_format=ModerationVerdict,
    )
    verdict = response.parsed_output
    if verdict is None:
        # §7: a schema-parse failure is an api_fail. Fail CLOSED — an
        # unparseable moderation result must never read as approval.
        return ModerationResult(
            approved=False,
            reason=f"moderation response unparseable (stop_reason={response.stop_reason})",
            layer="model",
            usage=usage_from_response(model, response.usage),
        )

    return ModerationResult(
        approved=verdict.approved,
        reason=verdict.reason,
        layer="model",
        usage=usage_from_response(model, response.usage),
    )
