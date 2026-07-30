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

**Rule 7 is a child-safety control, and it sits in the prompt because that is the
layer that can hold it** (DEVIATIONS 8.29). The vocabulary work removed `fat` from
the blocklist (8.16) and was right to: a blocklist hit SHORT-CIRCUITS
`moderate()`, so an innocuous entry does not merely narrow vocabulary, it stops
§13's hazard-versus-harm judgement from ever running on that story. But `fat` is
the one removal whose hazard is a DESCRIPTION rather than an action, and layer 3's
REJECT list is all actions — hurt, cruelty, weapons used against a character. So
"Sam is fat. Sam is sad." can clear all three layers. Prevention here is the
compensating control, on the same argument DEVIATIONS 4.12 made for grammar.

Ranked BELOW the vocabulary and grammar rules and ABOVE length: a story must never
describe a body in order to reach a word count.

**The story also has to contain the material the stage teaches** (DEVIATIONS
5.17). §8 promotes on accuracy over the GPCs introduced AT the child's stage,
and the only evidence source is a tap-a-word question — which can only ask about
a grapheme the STORY contains. Stage 4 teaches nothing but word-final `-s -es
-ed -ing`, so a story that happens to use no suffixed word leaves promotion
unreachable however well the child reads it. The allowlist already holds the
words that would fix that; the prompt simply never pointed at them.
`practice_words` names them, inside the cached half of the prompt.

**And it names them for COVERAGE, not for frequency** (DEVIATIONS 11.86-11.87).
§8's question is which of the stage's new sounds a story can be ASKED about, so
the twenty-four most common words that happen to use one of them is the wrong
selection: it spent sixteen of stage 3's slots on one GPC of six, and it never
once named stage 2's `sh` — the stage's headline digraph, §5.7's golden case and
§17's demo sentence — while `ship` sat in the same 400 words it was drawing from.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field

from storyforge.decoder import (
    MAX_STAGE,
    MIN_STAGE,
    WordVerdict,
    load_gpcs,
    mastered_ids,
    validate_word,
)
from storyforge.llm.client import generation_model, generation_timeout, get_client
from storyforge.llm.pricing import Usage, usage_from_response

# Imported, never reimplemented: the prompt must point at the words the GRADER
# credits. A second copy of "which GPCs does this word use" would drift, and the
# drift would be invisible — stories would look right and earn nothing.
from storyforge.mastery import word_gpc_ids

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
7. Never describe what a person's body or looks are like. Do not write that
   someone is fat, thin, big, small, tall or short, and never compare one
   person's body with another's. A word list this small turns such a sentence
   into a verdict, and a child reading it may take it as one about themselves.
   Write what a person DOES and how they FEEL instead. Creatures, places and
   objects can still be described freely: a cave can be dark, a rock can be big,
   a wing can be wide.
8. Meet the length the request gives you. That word count is a requirement, not
   an approximation, and finishing under it is a failed answer however well the
   sentences read. Get to a longer count by telling MORE of the story — more
   happens, in more small steps — and never by writing longer or more elaborate
   sentences, which is the one way length can cost a child their story. If the
   story ends before the count does, do not send the character off to do
   something else: go back and give them more trouble with the SAME problem — a
   second try, a setback, someone who helps — so the extra words are still the
   story you started. Count the words you have written before you answer.

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

#: Output-token ceiling for a generation. Named rather than inlined because
#: `pipeline._call` — the node that actually runs in `warm` and `eval` — carries
#: its own copy of this number, and the two must not drift (DEVIATIONS 8.25).
#:
#: Raised from 8000. On Sonnet 5 adaptive thinking is ON when `thinking` is
#: omitted (STATE invariant 9) and thinking counts against `max_tokens`, so the
#: story competes with the reasoning for the same budget. A stage-9 generation in
#: the before-measurement returned `stop_reason="max_tokens"` and no parseable
#: story at all — §7 counts that as `api_fail`, so it is a whole cell lost, not a
#: short story. Asking for the full specified length makes the story half of that
#: budget larger, which makes the collision likelier, so the ceiling moves with
#: it. 16000 is the largest value that stays comfortably inside a non-streaming
#: request; `max_tokens` is a cap, not a reservation, so an unused ceiling costs
#: nothing and the model is never told the number.
MAX_OUTPUT_TOKENS = 16000


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

    One honest limitation. The top stage (11, review) introduces no GPCs at all
    — it lifts the syllable cap and reviews — so its allowlist looks like stage
    10's and this returns 10. That is the right answer to the question the
    caller is really asking ("what are the newest sounds in here?"), and §8
    cannot promote out of the top stage anyway, so no evidence is owed there.
    """
    stages = [s for s in (_first_decodable_stage(w) for w in allowed_words) if s is not None]
    return max(stages) if stages else None


def _fill_by_coverage(
    picked: list[str],
    covers: dict[str, frozenset[str]],
) -> tuple[str, ...]:
    """`PRACTICE_WORD_LIMIT` words chosen for COVERAGE, printed in frequency order.

    `picked` is every candidate word, in allowlist-frequency order; `covers[word]`
    is the introduced GPCs that word is credited with. The old rule was
    `picked[:PRACTICE_WORD_LIMIT]`, and DEVIATIONS 11.86 measured what that costs:
    the cut fell on whole SOUNDS, not on spare words. Four stages lost a GPC the
    allowlist could express — stage 2 lost `sh` and `qu`, stage 4 `u_e`, stage 5
    `oa`, stage 7 `au` and `oy` — because sixteen commoner words used the stage's
    commonest new sound and got there first. A `tap_word` can only ask about a
    grapheme the STORY contains, so a sound the prompt never names is a sound §8
    can never collect evidence on, however well the child reads.

    Three passes, in this order:

    1. **coverage** — one word per introduced GPC, its most frequent, skipping a
       GPC an earlier pick already covers (a word can name two of the stage's
       sounds, and spending a second slot on the same sound is the defect in
       miniature);
    2. **balance** — further rounds, one more word per GPC per round, so the
       slots left over spread across the stage's sounds instead of pouring into
       its commonest one;
    3. **frequency** — top up with whatever is left, which only bites if the
       rounds run dry first.

    **The ORDER of the offer is unchanged**: the result is emitted in
    allowlist-frequency order, so the head of the list is still the part a
    thirty-word story can actually carry, and a list that fits inside the limit
    comes back exactly as it was handed in. The defect was which twenty-four words
    survive the cut, never the order they are printed in.
    """
    rank = {word: i for i, word in enumerate(picked)}
    by_gpc: dict[str, list[str]] = {}
    for word in picked:
        for gpc in covers[word]:
            by_gpc.setdefault(gpc, []).append(word)
    # Deterministic, and it puts the commonest sounds at the front of round 1,
    # where the limit can never cut them off.
    order = sorted(by_gpc, key=lambda gpc: rank[by_gpc[gpc][0]])

    chosen: set[str] = set()
    covered: set[str] = set()
    for gpc in order:  # 1. coverage
        if len(chosen) >= PRACTICE_WORD_LIMIT:
            break
        if gpc in covered:
            continue
        # `gpc not in covered` means no chosen word names it, so its own list is
        # entirely unchosen and the first entry is its most frequent word.
        word = by_gpc[gpc][0]
        chosen.add(word)
        covered |= covers[word]

    while len(chosen) < PRACTICE_WORD_LIMIT:  # 2. balance
        before = len(chosen)
        for gpc in order:
            if len(chosen) >= PRACTICE_WORD_LIMIT:
                break
            spare = next((w for w in by_gpc[gpc] if w not in chosen), None)
            if spare is not None:
                chosen.add(spare)
        if len(chosen) == before:  # every candidate is already offered
            break

    for word in picked:  # 3. frequency
        if len(chosen) >= PRACTICE_WORD_LIMIT:
            break
        chosen.add(word)

    return tuple(word for word in picked if word in chosen)


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

    Which words survive `PRACTICE_WORD_LIMIT` is decided by COVERAGE of the
    stage's introduced GPCs, not by frequency — see `_fill_by_coverage`, which
    also explains why the printed order is still frequency's.

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
    # What each candidate is CREDITED with, kept from the same call that admitted
    # it. The selection needs the alignment, not just the yes/no, and re-deriving
    # it in the picker would be a second copy of "which GPCs does this word use" —
    # the drift this module imports `word_gpc_ids` to avoid.
    covers: dict[str, frozenset[str]] = {}
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
        hit = introduced & frozenset(word_gpc_ids(word, stage))
        if hit:
            picked.append(word)
            covers[word] = hit

    # The saturation ratio is measured over the FULL candidate list, before any
    # truncation: whether a stage's sounds are scarce is a fact about the stage,
    # never about how the twenty-four slots are filled.
    if not picked or len(picked) > candidates * PRACTICE_SATURATION:
        return ()
    return _fill_by_coverage(picked, covers)


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


#: The narrative section's header. Named for the same reason `PRACTICE_HEADER` is:
#: the request block does not know the stage and so cannot reference this section,
#: and the tests need one string to assert on.
NARRATIVE_HEADER = "STORY SHAPE"

#: Stage bands for the narrative ask, taken from §10 rather than invented
#: (DEVIATIONS 11.44). §10: "linear spine with exactly 0-2 branch points
#: (stage-scaled: 0 for stages 1-2, up to 2 for 5+)" — in the spec's TWELVE-stage
#: numbering. Under the eleven-stage ladder (DEVIATIONS 12.10) each band covers
#: the same inventory range one number lower: 0 decisions at stage 1, one at 2-3,
#: up to two from 4 up.
NARRATIVE_BAND_EDGES = (1, 3, 7)

#: What every band says, whatever else it says. Three separate hazards, each one a
#: thing a previous prompt change actually did:
#:
#: * 9.40 — a prompt change that made the model resolve a conflict by deleting the
#:   theme, so 6 of 15 dragons stories shipped with no dragon in them. Nothing here
#:   may read as a reason to reach for a fourth story word.
#: * 8.22 v2 — a permissive paragraph read as an instruction and took the 120-200
#:   band to a 193.5-word mean. Nothing here may read as a licence to write more.
#: * §0 / invariant 1 — the gate is decodability and is never weakened for this.
#:   The last sentence of every band subordinates the whole section to rule 1.
_NARRATIVE_FLOOR = (
    "\n\nNone of this needs a new story word or any word that is not on your "
    "lists: a want can be about a nap, a cave or getting home, and the characters "
    "you already have are enough. Do not add sentences that EXPLAIN the shape — "
    "the shape is what happens. And none of this outranks rule 1: if you cannot "
    "say the next thing with the words you have, change what happens next. A plain "
    "story a child can read beats an interesting one they cannot."
)

#: The failure this whole section exists to prevent, quoted so the model can
#: recognise it. Both halves matter: the first is the observed defect, the second
#: keeps it from being read as an attack on repetition, which is CORRECT
#: decodable-reader behaviour (§5.5, STATE invariant 3) and which 9.41 already had
#: to defend once.
_NARRATIVE_ANTIPATTERN = (
    "\n\nThis is what to avoid, and it is the most common way these stories go "
    'wrong: "Dan sat. Dan ran. Dan can nap." Every sentence is correct and '
    "nothing happens — the sentences could be put in any order and nothing would "
    "be lost. Repeating the same WORDS is right and expected here. Repeating the "
    "same sentence shape while nothing changes is the thing to avoid."
)

_NARRATIVE_BANDS = {
    # Stage 1 (the entry stage). §18: "Sam sat. A cat sat." is authentic
    # decodable-reader register, not a defect. So this band asks for LESS, and
    # says out loud how small a want is allowed to be — the failure mode to avoid
    # here is a prompt that pushes a 30-word story into breaking the gate reaching
    # for something it cannot say (DEVIATIONS 11.46).
    0: (
        "One character, and ONE thing: something they want, or something that is "
        "wrong. The last sentence tells us they got it, or that it is fixed. "
        "Nothing else has to happen.\n\n"
        "Keep the want as small as your words are. Wanting to nap is a want. A cat "
        "in the way is a problem. A cat that sits on Sam's map until Sam taps it is "
        "a whole story at this stage, and six short sentences is enough to tell it."
    ),
    # Stages 2-3 (Appendix A's 3-4). §10's implied middle: one branch point.
    1: (
        "One character, one thing they want or one thing that is wrong, and ONE "
        "thing that gets in the way before it is settled.\n\n"
        "Say the want early — in the first two or three sentences — so the rest of "
        "the story is about getting it. The thing in the way is what a list of "
        "actions never has, and it is the difference between a story and a list. "
        "The last sentences tell us how it came out."
    ),
    # Stages 4-7 (Appendix A's 5-8). §10: up to 2 branch points.
    2: (
        "A story is one situation that CHANGES. Settle these before you write a "
        "word:\n"
        "  WHO — one main character.\n"
        "  WANT — what they want, or what is wrong. Put it in the story in the "
        "first two or three sentences, not just in your head.\n"
        "  IN THE WAY — one thing that stops them getting it.\n"
        "  A CHOICE — somewhere in the middle the character has two things they "
        "could do and picks one. Write the moment of deciding, not just the "
        "result.\n"
        "  THE END — the last sentences answer the want, and the situation is not "
        "the same as it was at the start.\n\n"
        "You have enough words now to do all five, so do all five."
    ),
    # Stages 8-11 (Appendix A's 9-12). Same shape, twice the words — and the
    # extra words are where the
    # padding failure lives (DEVIATIONS 11.41): story 7309's arc was finished at
    # sentence 14 and four flat sentences were bolted on after it to reach 120.
    3: (
        "A story is one situation that CHANGES. Settle these before you write a "
        "word:\n"
        "  WHO — one main character.\n"
        "  WANT — what they want, or what is wrong. Put it in the story in the "
        "first two or three sentences, not just in your head.\n"
        "  IN THE WAY — one thing that stops them getting it, and a second thing "
        "if the story needs it.\n"
        "  A CHOICE — up to two places where the character has two things they "
        "could do and picks one. Write the moment of deciding.\n"
        "  THE END — the last sentences answer the want, and the situation is not "
        "the same as it was at the start.\n\n"
        "This story is long, and a long story is the hard case: the words have to "
        "come from ONE story and not from two. When you reach the middle and still "
        "have half the words left, the answer is more trouble with the SAME "
        "problem — a first try that fails, a setback, someone who helps — and never "
        "a new errand once the first one is finished. A story that ends and then "
        "keeps going is the exact failure this section is here to stop."
    ),
}


# The stage-1 negation clause (DEVIATIONS 11.57) lived here until the ladder was
# renumbered (12.9). It stated an inventory FACT about the retired stage 1 — no
# word with the letter `o`, so `not`/`no`/`cannot`/`never` were unwritable and a
# story could not state an obstacle. The new stage 1 (the old stage 2 inventory)
# has `o`, `not` is decodable and `no`/`go`/`so`/`do` are its sight words, so the
# clause's premise is false and keeping it would tell the model a lie that removes
# its cheapest way to state a problem. Its guard test carried an explicit kill
# switch for exactly this condition and was removed with it.


def narrative_band(stage: int | None) -> int:
    """Which §10 band a stage falls in. `None` (unknown stage) gets the gentlest.

    Unknown-stage defaults DOWN rather than up, because the cost of asking a
    stage-1 story for a decision it has no words for is a story the child cannot
    read, and the cost of under-asking at stage 9 is a duller story they can.
    """
    if stage is None:
        return 0
    for band, edge in enumerate(NARRATIVE_BAND_EDGES):
        if stage <= edge:
            return band
    return len(NARRATIVE_BAND_EDGES)


def build_narrative_block(allowed_words: list[str]) -> str:
    """The stage-scaled narrative ask. Cached, per stage (DEVIATIONS 11.43).

    **Why this is in `system` and not in `messages`.** Two reasons, and the second
    is the one that decides it:

    1. It is byte-identical for every story at a stage, so in `system` it is
       written to the prompt cache once and read back at 0.1x. In `messages` it
       would be re-sent as fresh input on every request — 5.17's argument for
       putting the practice block here.
    2. **The rewrite block REPLACES `build_request_block`** in a single-message
       conversation (9.40). Narrative guidance in `messages` would therefore vanish
       the moment a first draft missed the decodability gate, which is precisely
       the mechanism that was deleting the theme from rewritten stories. In
       `system` it survives every rewrite.

    It is NOT in `SYSTEM_RULES`, because it is per-stage and that prefix is frozen
    against exactly that (STATE invariant 10).
    """
    stage = allowlist_stage(allowed_words)
    band = _NARRATIVE_BANDS[narrative_band(stage)]
    return (
        f"{NARRATIVE_HEADER}\n\n{band}"
        f"{_NARRATIVE_ANTIPATTERN}{_NARRATIVE_FLOOR}"
    )


def generation_system_blocks(
    stage_name: str,
    allowed_words: list[str],
    sight_words: list[str],
) -> list[dict]:
    """The whole `system` half of a generation request, assembled in ONE place.

    Two callers assemble a generation request — `generate_story` here and
    `pipeline._call`, which is the one that actually runs in `warm` and `eval`.
    They have drifted before: `_call` held `max_tokens=8000` while this module had
    moved to 16000, and because `_call` is the live node the production path kept
    the old ceiling and lost whole cells to `stop_reason="max_tokens"`
    (DEVIATIONS 8.7/8.25). A second prompt block is a second chance to drift the
    same way, so the assembly is shared rather than copied.

    The cache breakpoint goes at the END of the last stable block, so the whole
    prefix — rules, vocabulary, practice, narrative — is one cached prefix keyed
    per stage. That is the same economics as before this block existed: one
    breakpoint, one cache entry per stage, everything per-request still in
    `messages`.
    """
    narrative = build_narrative_block(allowed_words)
    blocks: list[dict] = [
        {"type": "text", "text": SYSTEM_RULES},
        {
            "type": "text",
            "text": build_vocabulary_block(stage_name, allowed_words, sight_words),
        },
    ]
    if narrative:
        blocks.append({"type": "text", "text": narrative})
    # On the last block whatever that turns out to be: an empty text block is an
    # API error, so the breakpoint travels with the content rather than assuming a
    # fixed index.
    blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return blocks


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


#: Words per sentence to plan against. Decodable-reader sentences are short by
#: construction, so a word target has to be translated into a number of THINGS
#: THAT HAPPEN before the model can plan for it — "120 words in short sentences"
#: is not a plan, "about 20 short sentences" is. Eight is the middle of the
#: observed register: `docs/EVAL.md`'s lowest-stage output runs 5-7 words a
#: sentence and the top stage runs nearer 10.
WORDS_PER_SENTENCE = 8


def build_request_block(
    theme: str,
    story_words: dict[str, str],
    target_min: int,
    target_max: int,
) -> str:
    """The per-request half of the prompt. After the cache breakpoint, by design.

    The length section is the fix for `docs/EVAL.md` section 12 (DEVIATIONS 8.22):
    52.2% length compliance overall and 6.7-13.3% at the 120-200 word stages, where
    the measured mean was 89-102. The old wording was "a story of roughly
    {min}-{max} words", and "roughly" was read as a licence — per-stage means sat
    just above the floor at stages 1-4 and below it from stage 5 up.

    Three things changed, each aimed at an observed behaviour rather than at the
    abstraction "be longer":

    * the floor is named as a floor and a midpoint is given to aim at, because a
      range invites its cheapest end;
    * the count is restated as a number of short sentences, because "short
      sentences" and "120 words" are instructions the model has to reconcile and
      it was resolving the conflict by stopping early;
    * the ceiling is named too. Over-length is also non-compliant, and one
      stage-4 draft in the before-measurement came back at 67 words against a
      30-60 target, so pressure in one direction only would trade one miss for
      the other.

    The `{min}-{max}` hyphenated form is preserved verbatim: `tests/test_llm.py`
    asserts on it, and that assertion is about the range reaching the model.

    **The story-word line is part of the same fix, because the first version of the
    length section regressed a GATE criterion.** Measured: with the length section
    alone, 5 of 45 first drafts used MORE than 3 distinct story words — one
    stage-8 draft used 8 — where the before-measurement had none at all. A longer
    story reaches further into the offered bank. `passes_gate` caps DISTINCT story
    words and leaves occurrences uncapped (STATE invariant 3); the old line said
    "at most 3" without saying "different" or that repeating is free, so "write
    more" and "at most 3" read as a contradiction and the model resolved it the
    wrong way.

    **The SECOND version overshot, and the ordering below is what fixes that.**
    Stating the repetition allowance as its own permissive paragraph AFTER the
    length block took the 120-200 band to a mean of 193.5 words with 7 of 11 drafts
    OVER 200 — compliance 36.4%, worse than the 33.3% it started at. Two lessons,
    both encoded here rather than described: a permission is read as an
    instruction, so the allowance is now one clause on the cap and not a paragraph
    of its own; and the LAST thing in the block is what the model weights most, so
    the length bounds go last and name both ends symmetrically. Over-length is a
    miss exactly as under-length is, and only the second version made that visible.
    """
    offered = (
        "\n".join(f"- {w}: {gloss}" for w, gloss in sorted(story_words.items()))
        or "- (none)"
    )
    aim = (target_min + target_max) // 2
    sentences = max(4, round(aim / WORDS_PER_SENTENCE))
    return (
        f"Write a {theme} story.\n\n"
        f"STORY WORDS — at most 3 DIFFERENT ones, only from this list:\n{offered}\n\n"
        "Repeat those freely; only the number of DIFFERENT ones is capped, never how "
        "often each appears. Needing more words is never a reason to reach for a "
        "fourth.\n\n"
        "Every other word must come from the permitted or sight lists above.\n\n"
        f"LENGTH — {target_min}-{target_max} words. Both ends are real:\n"
        f"- Under {target_min} does not count. {target_min} is the FLOOR, not the goal.\n"
        f"- Over {target_max} does not count either. Do not run past {target_max}.\n"
        f"- Aim at about {aim} words, which in this register is roughly {sentences} "
        f"short sentences. Decide that many things to happen BEFORE you start "
        f"writing, so you neither stop early nor pad late.\n"
        f"- Count the words in the finished story before you answer, and fix it if "
        f"the total is outside {target_min}-{target_max}.\n"
        f"- Every one of those words still has to come from the lists above. A "
        f"longer story is not a licence to reach outside them: if you cannot say "
        f"the next thing with the words you have, say a different next thing."
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
    # `generation_timeout` returns an `httpx.Timeout` with `connect` split out
    # (DEVIATIONS 8.2). Passing the bare float instead reaches httpx as
    # connect == read, so a 240s read budget would also spend 240s trying to open
    # a socket — six times the SDK default, on a host that is simply unreachable.
    # It takes `MAX_OUTPUT_TOKENS` because the budget is derived from it: raising
    # the ceiling (8.25) must raise the time allowed to fill it, or the two
    # changes fight.
    client = get_client().with_options(timeout=generation_timeout(MAX_OUTPUT_TOKENS))
    model = generation_model()

    response = await client.messages.parse(
        model=model,
        # Generous: on Sonnet 5 adaptive thinking is ON when `thinking` is
        # omitted, and thinking counts against max_tokens. A tight budget here
        # yields a response that is mostly thinking plus a truncated story
        # (stop_reason "max_tokens"), which looks like a bad model rather than a
        # bad parameter. Observed live at stage 9 — see MAX_OUTPUT_TOKENS.
        max_tokens=MAX_OUTPUT_TOKENS,
        output_config={"effort": effort},
        # Breakpoint at the end of the stable prefix: every story for this stage
        # reads rules, vocabulary and story shape from cache. Assembled by the
        # shared helper so this and `pipeline._call` cannot drift (8.25).
        system=generation_system_blocks(stage_name, allowed_words, sight_words),
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
