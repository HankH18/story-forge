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

Layer 3 also carries the **grammar** check (docs/DEVIATIONS.md 4.12). The
validator proves decodability and this node judges safety; nothing looked at
whether the sentence was well-formed English, and live warm-pool output shipped
"He did not came back." — 100% decodable and wrong. It rides here because this
is already a Haiku call on the final text, so the extra judgement is nearly
free, and because the answer belongs in the same gate: an ungrammatical story is
rejected exactly like an unsafe one. The decodability gate is NOT lowered to
accommodate grammar — the warm pool absorbs the rejection.
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
exists without anyone being harmed, approve.

SEPARATELY, judge whether the story is WELL-FORMED ENGLISH (docs/DEVIATIONS.md \
4.12). The writer works from a tiny word list, so it sometimes grabs a word that \
fits the sounds but breaks the sentence. This is a different question from \
safety: answer it independently in `well_formed`.

Judge each sentence ON ITS OWN. Set well_formed = false only when a single \
sentence is unmistakably broken AND you can name the one wrong word and the \
word that should replace it:
- Wrong verb form after a helper: "He did not came back."   (came -> come)
- Subject and verb disagree:      "A dragon sit in a cave."  (sit -> sits)
- A stray or missing word, so the sentence cannot parse: \
"The dragon had a got time."   (a got -> a good)
- A pronoun in the wrong case, even where speech would allow it: \
"The dragon and him ran up the hill."   (him -> he)

Everything below is the decodable-reader format working as intended and is \
NEVER a reason to set well_formed = false:
- Very short sentences, fragments and one-word beats: "Sam sat.", \
"Up, up, up.", "Fun!", "The end."
- The same word, sentence or pattern repeated many times.
- A tiny, plain vocabulary; odd, bald or childish phrasing; a thin or abrupt \
story.
- A sentence opening with "And", "But", "So" or "Then".
- Emphatic "did" or "do" plus a plain verb: "It did nap.", "Sam did stand.", \
"The dragon did like the cave." Those are correct English.
- The tense changing from one sentence to the next, or past and present mixed \
across the story. Consistency BETWEEN sentences is style, not grammar. NEVER \
mark a sentence wrong because of the tense used in a DIFFERENT sentence. The \
child has not been taught the "-ed" ending yet, so the past-tense form of a \
verb often does not exist in the word list and the writer MUST use the present. \
"It had big wings. Its wings help it." is well-formed; do not ask for "helped". \
"Sam sat in a rocket. Sam and Bob go up fast." is well-formed too. A modal — \
"can", "will", "may" — is always correct as written; never ask for "could", \
"would" or "might". \
(Inside ONE sentence, verbs joined by "and" must still match: "Dan sat and \
admits it." is wrong.)
- Reported speech that is not backshifted: "He said he will go." is fine.
- A sentence that is grammatical but strange, dull or illogical. Judge the \
grammar, not the sense.

If you cannot quote the broken sentence and give the exact replacement word, \
the story is well-formed. When in doubt, well_formed = true: a wrongly rejected \
story is thrown away and the child gets nothing in its place."""


#: What `moderate()` prefixes a reason with when the story was judged SAFE and
#: failed only on grammar. Named rather than inlined because it is the one thing
#: that distinguishes "rejected on content" from "rejected on grammar" after the
#: two halves are folded into a single `approved` — see `warm.run_grammar_backfill`.
UNGRAMMATICAL_PREFIX = "ungrammatical: "


class ModerationVerdict(BaseModel):
    approved: bool = Field(description="True only if the story is safe for ages 5-10.")
    reason: str = Field(description="One sentence. If rejecting, name the specific problem.")
    well_formed: bool = Field(
        description=(
            "True if every sentence is grammatical English. Plain, short and "
            "repetitive prose is well-formed; only real grammar errors are not."
        )
    )
    grammar_note: str = Field(
        description=(
            "Empty string when well_formed. Otherwise quote the broken sentence "
            "and name the error, in under fifteen words."
        )
    )


class ModerationResult(BaseModel):
    approved: bool
    reason: str
    layer: str  # "blocklist" | "model"
    blocked_words: list[str] = Field(default_factory=list)
    usage: Usage | None = None
    # §13's three layers cover safety; nothing covered GRAMMAR until now
    # (docs/DEVIATIONS.md 4.12). `well_formed` is folded into `approved` by
    # `moderate()`, so an ungrammatical story is rejected exactly like an unsafe
    # one and the warm pool absorbs it. Defaults describe "no grammar defect
    # found", which is the honest reading of the blocklist short-circuit — that
    # story died at layer 1 and was never judged for grammar.
    well_formed: bool = True
    grammar_note: str = ""


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
        # unparseable moderation result must never read as approval, and that
        # applies to the grammar half of the verdict too.
        return ModerationResult(
            approved=False,
            reason=f"moderation response unparseable (stop_reason={response.stop_reason})",
            layer="model",
            usage=usage_from_response(model, response.usage),
            well_formed=False,
        )

    # An ungrammatical story is rejected the same way an unsafe one is. Folding
    # it in here rather than asking the model to also flip `approved` keeps the
    # two judgements independent in the prompt (so grammar cannot leak into the
    # safety call) while the gate stays a single deterministic boolean.
    reason = verdict.reason
    if verdict.approved and not verdict.well_formed:
        note = verdict.grammar_note.strip() or "sentence is not well-formed English"
        reason = f"{UNGRAMMATICAL_PREFIX}{note}"

    return ModerationResult(
        approved=verdict.approved and verdict.well_formed,
        reason=reason,
        layer="model",
        usage=usage_from_response(model, response.usage),
        well_formed=verdict.well_formed,
        grammar_note=verdict.grammar_note.strip(),
    )
