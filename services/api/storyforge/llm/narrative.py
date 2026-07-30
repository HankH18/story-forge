"""Is it a story, or is it a word list? (DEVIATIONS 11.47-11.50)

**Nothing in this system measured narrative until this module existed.** §1 item 2
is decodability plus rewrite depth. `pipeline.passes_gate` is decodability plus OOV
plus the story-word cap. §13's judge is told in so many words that "the prose will
be extremely simple and repetitive… never a reason to reject". So a stage-6 story
that reads like a vocabulary drill clears every gate the product has, and the only
thing that noticed was a person reading the live app.

`judge_narrative` is the instrument: a Haiku judge, priced by `llm/pricing.py`,
validated in both directions against hand-labelled live stories.

`narrative_signals` is NOT an instrument, and that is a measured finding rather
than a design choice. A cheap structural proxy was the first thing tried, because
free and deterministic beats paid and stochastic — and it does not work. Over 19
stories from the live pool that I read and labelled myself, five candidate free
signals were computed (DEVIATIONS 11.53) and **none separated the labels**:

    signal                    word-list mean    story mean
    same-opening share             0.363           0.301
    late-third word novelty        0.274           0.290
    intent or adversative word     0.44            0.70
    connective share               0.087           0.103
    max repeated-bigram share      0.076           0.075

The reason is structural: the two failure modes look opposite to a surface metric.
Story 1670 repeats one frame seven times and scores as repetitive; story 7274
tours four locations with varied openings and no repetition at all, and both are
word lists. A metric that catches one misses the other. The three functions are
kept because they cost nothing and `top_opener_share` is a real diagnostic for the
padding failure specifically (story 7309, 0.571 — the highest in the pool), but
they are reported as DIAGNOSTICS beside the judge and never as a narrative score.
There is deliberately no composite: a 0-3 number computed from signals measured
not to work would read as coverage, which is the whole failure this file exists
because of.

**Neither one is a gate, and that is deliberate (11.47).** §18's register point is
correct at the bottom of the ladder: stage 1's vocabulary is `s a t p i n m d` and
a spare stage-1 story is the format working, not a defect. A hard per-story
narrative gate would reject those, and it would also hand every future prompt edit
a fourth invariant-5 retest obligation. The stage-scaled EXPECTATION lives in the
report that reads these numbers, never in a boolean that can refuse a child a
story.

The judge is stage-BLIND on purpose. One ruler for twelve stages, with the
expectation applied outside it — a judge told the stage can be talked into
leniency, and then the ladder it exists to measure is baked into the ruler.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from storyforge.llm.client import FAST_TIMEOUT_S, fast_model, get_client
from storyforge.llm.pricing import Usage, usage_from_response

# --- the pure proxy -----------------------------------------------------------

#: Sentence boundary. Deliberately crude: these texts are one to three clauses a
#: sentence with no abbreviations, decimals or ellipses in the allowlist, so the
#: failure modes of a real sentence splitter cannot occur here.
_SENTENCE_END = re.compile(r"[.!?]+")

#: Word tokens, lowercased. `decoder.tokenizer` is the canonical tokenizer for
#: anything the GATE reads; this is not that, and importing it would tie a
#: reporting proxy to the validator's contract for no gain.
_WORD = re.compile(r"[a-z']+")

#: Words of INTENT. Small and closed: a story with a want almost always says so
#: out loud in this register, because the vocabulary holds no way to imply it.
#: "will" and "going" are excluded even though they are modal-ish — they appear in
#: flat routine sentences constantly ("Every day it went up high") and firing on
#: them would make the signal true for every story, which is a signal of nothing.
INTENT_WORDS = frozenset(
    """want wants wanted wish wishes wished need needs needed must
    try tries tried hope hopes hoped""".split()
)

#: Words that mark something GETTING IN THE WAY. "not" is included and it is the
#: workhorse: this vocabulary expresses an obstacle as a negation far more often
#: than as a noun, because "did not" is available at stage 1 and "obstacle" is
#: not available at any stage.
ADVERSATIVE_WORDS = frozenset("but not no never cannot".split())

#: The live pool's own distribution, so a reported figure can be read against
#: something. NOT thresholds: no comparison in this file returns a pass or a fail,
#: because the signals were measured not to separate the labels (11.53). Recorded
#: from 120 servable stories.
POOL_OPENER_SHARE_MEDIAN = 0.286
POOL_LATE_NOVELTY_MEDIAN = 0.333


def sentences(text: str) -> list[str]:
    """Sentence strings, empties dropped."""
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _opener(sentence: str) -> str:
    """The first two words of a sentence, or the first if there is only one.

    Two words, not one: "The rabbit" and "The giraffe" are different subjects and
    a one-word signature would collapse them into a single "The". Two words is
    also where the observed failure lives — story 1670 is "The rabbit" three times
    out of seven sentences, and story 28 is "Sam ran"/"Sam sat"/"Sam had".
    """
    words = _WORD.findall(sentence.lower())
    return " ".join(words[:2])


def top_opener_share(text: str) -> float:
    """The largest share of sentences that open with the same two words.

    0.0 on an empty text — a text with no sentences has no repeated frame, and a
    ZeroDivision here would take down a reporting path.
    """
    ss = sentences(text)
    if not ss:
        return 0.0
    counts: dict[str, int] = {}
    for s in ss:
        key = _opener(s)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.values()) / len(ss)


def late_novelty(text: str) -> float:
    """Share of the final third's words that appear nowhere earlier.

    The task's own test of "does the story change its subject's state": a story
    that resolves has to name something it has not named yet, and a list of
    actions by one character does not. Measured over the last third of SENTENCES
    (not words) so a long final sentence cannot dominate.
    """
    ss = sentences(text)
    if len(ss) < 3:
        return 0.0
    cut = len(ss) - max(1, len(ss) // 3)
    earlier = {w for s in ss[:cut] for w in _WORD.findall(s.lower())}
    tail = [w for s in ss[cut:] for w in _WORD.findall(s.lower())]
    if not tail:
        return 0.0
    return sum(1 for w in tail if w not in earlier) / len(tail)


class NarrativeSignals(BaseModel):
    """Free surface diagnostics. **Not a narrative score** — see the module
    docstring for the measurement that says so. There is no composite field and no
    threshold, on purpose."""

    sentences: int
    top_opener_share: float
    late_novelty: float
    has_intent: bool
    has_adversative: bool


def narrative_signals(text: str) -> NarrativeSignals:
    """Four free surface measurements. No I/O, no cost, no verdict."""
    words = set(_WORD.findall(text.lower()))
    return NarrativeSignals(
        sentences=len(sentences(text)),
        top_opener_share=round(top_opener_share(text), 4),
        late_novelty=round(late_novelty(text), 4),
        has_intent=bool(words & INTENT_WORDS),
        has_adversative=bool(words & ADVERSATIVE_WORDS),
    )


# --- the judge ----------------------------------------------------------------

#: The lowest and highest score the judge may return. Named because the report's
#: stage-scaled expectations are stated against this scale, and a silent change to
#: the scale would silently rescale every expectation.
SCORE_MIN = 1
SCORE_MAX = 5

NARRATIVE_SYSTEM = """You judge ONE thing about a story written for a child who is \
learning to read: is it a STORY, or is it a LIST of things that happen?

A STORY has some of these:
- someone who wants something, or who has a problem;
- something that gets in the way, or a choice the character has to make;
- an ending in which the situation is different from how it started.

A LIST has none of them. It names a character and then reports actions in \
sequence: "Adam sat. Adam ran. Adam can nap." Every sentence is true, nothing \
follows from anything, and the sentences could be reordered without losing \
anything. A tour ("It went to a town. It went to a park. It went past a hill.") \
and a routine ("Every day it went up high. Then it came back.") are lists too.

NONE OF THE FOLLOWING IS EVER A REASON TO SCORE LOWER. These stories are written \
from a list of a few hundred words a five-year-old has been taught to sound out, \
and the format is doing its job:
- very short, plain sentences and a tiny vocabulary;
- the same word, name or sentence pattern repeated many times;
- no description, no figurative language, no interesting words;
- a total length of thirty words;
- an abrupt ending, or "The end."
Judge STRUCTURE only, never prose quality. A story can be a whole story in six \
five-word sentences, and at these reading levels it usually is.

SCORES:
5 - a want or a problem, something in the way, and a clear resolution.
4 - a want or a problem and a resolution, with little in the way.
3 - something real changes by the end, but there is no want and nothing in the way.
2 - a sequence of related actions in which nothing changes: a routine, a tour, a \
list of places visited.
1 - a list of actions by one character, which could be given in any order."""


class NarrativeVerdict(BaseModel):
    """The judge's structured output.

    **Field order is load-bearing and this is the third time it has been proved on
    this model.** 8.77 moved `grammar_note` ahead of `well_formed` and fixed three
    false positives; 9.50 moved `reason` ahead of `approved` and flipped two live
    false positives back. Structured output is emitted in schema order, so a
    conclusion declared first is a conclusion the judge then rationalises. `spine`
    is the derivation and `score` is the conclusion, so `spine` goes first and
    `score` goes last.
    """

    spine: str = Field(
        description=(
            "Fill this in FIRST, before anything else. In one sentence: who wants "
            "what, what got in the way, and what is different at the end. If the "
            "text has none of those, say plainly that it is a list of actions. "
            "Under thirty words."
        )
    )
    has_want: bool = Field(
        description="Does a character want something, or have a problem? Follows from spine."
    )
    has_obstacle: bool = Field(
        description="Does anything get in the way, or must a character choose? Follows from spine."
    )
    has_change: bool = Field(
        description=(
            "Is the situation at the end different from the situation at the start? "
            "Follows from spine. Visiting places and coming home unchanged is not a change."
        )
    )
    score: int = Field(
        ge=SCORE_MIN,
        le=SCORE_MAX,
        description=(
            "The CONCLUSION of spine and the three answers above, not an "
            "independent judgement. 1 to 5, on the scale given. Never lowered for "
            "plain words, short sentences, repetition or length."
        ),
    )


class NarrativeResult(BaseModel):
    score: int
    spine: str
    has_want: bool
    has_obstacle: bool
    has_change: bool
    usage: Usage | None = None
    #: The pure proxy, always populated — it costs nothing, so there is no reason
    #: for a judged story not to carry both numbers side by side.
    signals: NarrativeSignals | None = None
    #: Set when the judge could not be reached or its output would not parse. The
    #: node DEGRADES (STATE invariant 11): a measurement failure must never fail a
    #: story, and a missing measurement must never read as a good one.
    error: str | None = None


async def judge_narrative(body: str) -> NarrativeResult:
    """Score one story for narrative structure. Never raises.

    Failure returns `error` set and `score = 0` — BELOW `SCORE_MIN`, so an
    unreachable judge cannot be mistaken for a story that scored badly and cannot
    be averaged into a report as if it were a measurement. `score = 0` is
    "unmeasured", and every summary in `scripts/measure_narrative.py` drops it.
    """
    signals = narrative_signals(body)
    try:
        client = get_client().with_options(timeout=FAST_TIMEOUT_S)
        model = fast_model()
        response = await client.messages.parse(
            model=model,
            max_tokens=1000,
            system=NARRATIVE_SYSTEM,
            messages=[{"role": "user", "content": f"Story:\n\n{body}"}],
            output_format=NarrativeVerdict,
        )
        verdict = response.parsed_output
        usage = usage_from_response(model, response.usage)
        if verdict is None:
            return NarrativeResult(
                score=0,
                spine="",
                has_want=False,
                has_obstacle=False,
                has_change=False,
                usage=usage,
                signals=signals,
                error=f"narrative verdict unparseable (stop_reason={response.stop_reason})",
            )
        return NarrativeResult(
            score=verdict.score,
            spine=verdict.spine,
            has_want=verdict.has_want,
            has_obstacle=verdict.has_obstacle,
            has_change=verdict.has_change,
            usage=usage,
            signals=signals,
        )
    except Exception as exc:  # noqa: BLE001 — a measurement must not fail a story
        return NarrativeResult(
            score=0,
            spine="",
            has_want=False,
            has_obstacle=False,
            has_change=False,
            signals=signals,
            error=f"{type(exc).__name__}: {exc}",
        )
