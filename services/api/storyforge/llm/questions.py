"""Question-generation node (§7 ENRICHING, §11.3).

§7 fans QUESTION_GEN out in parallel with SCENE_TAGGING once the validator has
passed. §11.3 fixes the inventory at exactly two types — `tap_word` ("tap the
word that says /sh/ip") and `mcq2` (a two-choice story question) — and both
render as text a child has to read. So the same rule as the story applies: the
decoder is the gate, and it is never lowered for a question (§0). A question
whose prompt or options contain an undecodable word is a bug, not a stylistic
wobble.

The architecture mirrors `llm/generate.py`: the model is an untrusted generator
and a pure function is the judge (STATE invariant 2). Nothing the model claims
is taken on trust.

    tap_word  Options are drawn from a CLOSED list computed from the story
              itself. The claimed answer must genuinely begin with the target
              sound — proved from the decoder's own GPC alignment, not from the
              model's say-so — and every distractor must not use that grapheme
              anywhere in its alignment, so exactly one option *can* be correct.
    mcq2      The true option's words must appear in the story, in the story's
              own order; the false option's must not. That makes "which one is
              correct" a decidable property of the text rather than a claim.

Bounded retries (§7): three attempts. A type that still fails is DROPPED —
returning one good question beats returning two when one of them is wrong.

**A tap-a-word question is also the mastery signal, so it must target the sounds
this stage teaches.** §8 promotes on ≥80% accuracy over the GPCs introduced AT
the child's current stage, and only `tap_word` moves those counters
(`mastery.MASTERY_QTYPES`). Sorting the candidate sounds newest-first was not
enough: live stage-4 and stage-5 stories asked about `fast`, `ran` and `held`,
none of which contains a stage-4 or stage-5 GPC, so a reader could finish every
story in the pool and accumulate exactly zero evidence toward promotion. The loop
could not close. `sound_targets` now picks in three tiers, and reports which one
fired so §14 can measure it:

    current_stage_onset   the target grapheme is introduced at this stage
    current_stage_word    no grapheme qualifies, but the correct word USES a
                          sound taught at this stage somewhere in its alignment
                          (stage 4 teaches only final suffixes — `ed`, `ing`,
                          `es`, `s|Z` — so no word can START with one and this
                          tier is the only way stage 4 ever earns evidence)
    older_stage_fallback  the story supports neither; ask the newest sound it
                          does support, because a question about an older sound
                          is still a real question and dropping it is worse

Nothing about the gate or the "exactly one correct option" proof is relaxed to
achieve this: the tiers only NARROW the closed list the model may choose from.

**Prompt frames are a ladder, not a sentence.** A phonics prompt normally names
the sound ("/sh/"), but a bare grapheme is not a word: 61 of the inventory's 78
graphemes are OOV or a violation when tokenised, so a literal "/sh/" fails the
gate at every stage. The target is therefore carried as a decodable CUE WORD in
the payload — kid mode renders the sound chip from `cue_word`. Even then, "word"
is undecodable at every stage in this inventory, so `PROMPT_FRAMES` is tried
richest-first and the first frame that PASSES the gate wins. Below stage 7 that
is the terse frame. Honest consequence, logged in DEVIATIONS: the gate stays
where it is and the sentence gets shorter, never the reverse.

**`target_grapheme` is answer-key material and lives in `answer`, never in
`payload`.** `check_tap_word` guarantees that exactly one option starts with the
target grapheme and that no distractor uses it anywhere, which is precisely what
makes the question checkable — and precisely what makes the grapheme a complete
giveaway. Shipped in the payload it reduced the question to
`[o for o in options if o.startswith(payload["target_grapheme"])][0]`, so §9's
"the answer key never leaves the server" (DEVIATIONS 4.9) was false for every
tap-a-word question. `cue_word` carries no such signal: it is chosen by
`_cue_index` from the ALLOWED list and is explicitly excluded from the options,
so it names the sound without naming the answer.

Determinism: option order is a pure function of (seed, question type, option
set). `random.Random` is seeded from a blake2b digest — stable across processes
and independent of PYTHONHASHSEED — and the option list is canonically sorted
before shuffling, so the model's emission order cannot leak the answer either.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Iterable, Sequence
from functools import lru_cache

from pydantic import BaseModel, Field

from storyforge.decoder import (
    TextReport,
    WordVerdict,
    load_gpcs,
    mastered_ids,
    validate_text,
    validate_word,
)
from storyforge.decoder.tokenizer import tokenize
from storyforge.llm.client import FAST_TIMEOUT_S, fast_model, get_client
from storyforge.llm.generate import build_vocabulary_block
from storyforge.llm.pricing import Usage, usage_from_response

# The GPC instances §8 will actually CREDIT for a word, imported rather than
# reimplemented: the question generator has to target exactly what the grader
# counts, and two copies of that definition would drift.
from storyforge.mastery import word_gpc_ids as graded_gpc_ids
from storyforge.pipeline import passes_gate

# §7: one generation plus two retries. Never raised to make a question pass.
MAX_QUESTION_ATTEMPTS = 3
# §11.3.
TAP_WORD_OPTIONS = 3
MCQ2_OPTIONS = 2
# Keep the closed sound list short enough that the model reads all of it.
MAX_SOUND_TARGETS = 6
MAX_OFFERED_WORDS = 8
# Anti-guessability. A child who can spot the odd-one-out by shape has not read
# anything, so an option that stands out by length is as broken as a wrong one.
TAP_LEN_SPREAD = 2
MCQ_LEN_RATIO = 1.4

# Live-observed: Haiku reaches for "What did ...?" and burns the whole retry
# budget, because "what" is undecodable until it becomes a sight word at stage
# 12. §0's rule is to fix the prompt, not the gate — so the request block hands
# the model the openers that are readable AT THIS STAGE, filtered by the gate.
QUESTION_OPENERS = (
    "Did", "Was", "Is", "Can", "Will", "Who", "What", "Where", "When", "Why", "How",
)

# Tried in order; the first one that clears the gate at this stage is used.
PROMPT_FRAMES = (
    "Tap the word that starts like {cue}.",
    "Tap the one that starts like {cue}.",
    "Tap the one like {cue}.",
    "Tap: {cue}.",
)

# Frozen. Per-request material lives in `messages`, so this prefix (plus the
# stage vocabulary) stays cacheable across every story at a stage.
QUESTION_RULES = """You write two questions about a story for a child who is \
learning to read.

The child reads your questions, so every word you write must come from the \
PERMITTED WORDS list, the SIGHT WORDS list, or the story itself. A word outside \
those lists is unreadable for this child and makes the question useless.

You write exactly two questions.

1. TAP-A-WORD. You are given a closed list of sounds. Each sound comes with the \
words from the story that START with it, and the words from the story that do \
not use it at all. Pick one sound, one word that starts with it, and two words \
that do not use it. Use only the words offered under the sound you picked. Do \
not invent words and do not reuse a word from another sound.

2. TWO-CHOICE QUESTION. Ask one short question about what happens in the story, \
and write a true answer and a false answer.
   - Build the true answer by copying a sentence from the story, or by \
     shortening one. Keep the story's words in the story's own order, and add \
     nothing the story does not say.
   - Build the false answer from the story's words too, then change what it \
     claims — swap who did what, or say the opposite. It must be something the \
     story never says.
   - Never begin an answer with Yes or No.
   - The two answers must have the same number of words and be within about \
     three letters of the same length. A longer or shorter answer tells the \
     child which one to pick without reading it.

Short sentences. No markdown."""


# --- structured output shapes ------------------------------------------------
class TapWordDraft(BaseModel):
    target_grapheme: str = Field(description="The sound you picked, spelled with letters.")
    correct_word: str = Field(description="One story word that starts with that sound.")
    other_words: list[str] = Field(
        description="Exactly two story words that do not use that sound at all."
    )


class Mcq2Draft(BaseModel):
    prompt: str = Field(description="One short question about the story, ending in '?'.")
    correct: str = Field(description="The true answer, in the story's own words.")
    incorrect: str = Field(description="The false answer, about the same length.")


class QuestionDraft(BaseModel):
    tap_word: TapWordDraft
    mcq2: Mcq2Draft


class TapWordOnly(BaseModel):
    """Retry shape when only the tap-a-word question still needs writing.

    Separate models rather than `| None` fields: an optional field is one more
    thing the schema can get wrong, and the caller always knows which types are
    outstanding.
    """

    tap_word: TapWordDraft


class Mcq2Only(BaseModel):
    mcq2: Mcq2Draft


# --- returned values ---------------------------------------------------------
class SoundTarget(BaseModel):
    """One legal tap-a-word question, derived from the story by pure function.

    `onset_words` and `clean_words` are the ONLY words the model may offer for
    this sound, which is why the verification below almost always passes on the
    first attempt: the closed list is already correct by construction.
    """

    grapheme: str
    cue: str
    onset_words: list[str]
    clean_words: list[str]
    #: The stage at which this grapheme is first taught (§4's cumulative ladder).
    stage: int = 0
    #: Which tier of the §8 evidence preference produced this target. See
    #: `_prefer_current_stage`; `current_stage_word` is additionally ENFORCED in
    #: `check_tap_word`, because a scope that only lives in the prompt is a
    #: suggestion, not a guarantee.
    scope: str = "older_stage_fallback"


class GeneratedQuestion(BaseModel):
    qtype: str  # "tap_word" | "mcq2"
    idx: int = 0
    prompt: str
    payload: dict
    answer: dict


class QuestionSet(BaseModel):
    """What the node returns. `dropped` is deliberately part of the result —
    §14 needs to see which type failed and why, and a silent empty list would
    read as "the story had no questions"."""

    questions: list[GeneratedQuestion] = Field(default_factory=list)
    usage: Usage
    attempts: int = 0
    dropped: dict[str, list[str]] = Field(default_factory=dict)
    #: §8/§14: which sound the tap-a-word question ended up practising, and which
    #: tier of `_prefer_current_stage` produced it. Surfaced (rather than logged)
    #: so the eval harness can measure how often the fallback fires — a high
    #: `older_stage_fallback` rate at some stage is a finding about the ALLOWLIST,
    #: not about this node.
    tap_word_target: dict = Field(default_factory=dict)


# --- pure helpers ------------------------------------------------------------
def _norm(word: str) -> str:
    return " ".join(word.strip().split())


def _sets(sight_words: Iterable[str], story_words: Iterable[str]):
    return (
        frozenset(w.strip().lower() for w in sight_words if w and w.strip()),
        frozenset(w.strip().lower() for w in story_words if w and w.strip()),
    )


def readable(
    text: str,
    stage_id: int,
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
) -> TextReport:
    """Validate child-facing text exactly the way story text is validated."""
    sight, story = _sets(sight_words, story_words)
    return validate_text(text, mastered_ids(stage_id), sight, story)


def word_graphemes(word: str, mastered: frozenset[str]) -> list[str] | None:
    """The graphemes the decoder actually used to sound `word` out, in order.

    ``None`` when the word is not decodable at this stage — which is also the
    answer to "may this word be a tap-a-word option?". A GPC id is
    ``"sh|SH"``, so the grapheme is everything before the pipe.
    """
    report = validate_word(word, mastered)
    if report.verdict is not WordVerdict.DECODABLE or not report.alignment:
        return None
    return [gid.split("|", 1)[0] for gid in report.alignment]


@lru_cache(maxsize=16)
def stage_gpc_ids(stage_id: int) -> frozenset[str]:
    """The GPC ids INTRODUCED at this stage — §8's promotion denominator.

    `mastered_ids` is cumulative (everything up to and including the stage);
    this is the thin slice the stage actually teaches, which is what promotion
    is measured over.
    """
    return frozenset(g.id for g in load_gpcs() if g.stage == stage_id)


def uses_stage_gpc(word: str, stage_id: int) -> bool:
    """Would answering about this word earn evidence toward promotion (§8)?

    True when sounding the word out at `stage_id` requires at least one GPC that
    `stage_id` introduces. `graded_gpc_ids` is `mastery.word_gpc_ids` — the same
    function `POST /api/readings/{id}/answers` uses to award the credit, so this
    cannot target something the grader would not count.
    """
    return bool(stage_gpc_ids(stage_id).intersection(graded_gpc_ids(word, stage_id)))


def story_word_tokens(story_text: str) -> list[str]:
    return [t.text for t in tokenize(story_text) if t.is_word]


def story_supports(option: str, story_text: str) -> bool:
    """Does the story say this, in this order?

    Every word of the option must appear in the story AND the words must occur
    in the story's own sequence. That second half is what makes an mcq2 answer
    checkable: for "Sam sat on the cat", the reversal "The cat sat on Sam" has
    every word present but not in that order, so it is provably not supported.
    Greedy leftmost matching is exact for subsequence containment.
    """
    want = [t.text for t in tokenize(option) if t.is_word]
    if not want:
        return False
    have = story_word_tokens(story_text)
    i = 0
    for w in want:
        while i < len(have) and have[i] != w:
            i += 1
        if i == len(have):
            return False
        i += 1
    return True


def sound_targets(
    *,
    story_text: str,
    stage_id: int,
    allowed_words: Sequence[str],
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
    limit: int = MAX_SOUND_TARGETS,
) -> list[SoundTarget]:
    """Every tap-a-word question this story can legally ask, best sound first.

    A sound qualifies only when the story contains a word that STARTS with it
    and at least two words that never use it — otherwise "exactly one correct
    answer" cannot be guaranteed. Sight and story words are excluded: the child
    has not sounded them out, so they carry no evidence about a grapheme.

    "Best" is §8's evidence rule, not recency alone — see `_prefer_current_stage`.
    """
    mastered = mastered_ids(stage_id)
    sight, story = _sets(sight_words, story_words)

    # Insertion-ordered, so the offered lists follow reading order.
    graphemes_by_word: dict[str, list[str]] = {}
    for token in tokenize(story_text):
        if not token.is_word or token.text in graphemes_by_word:
            continue
        if token.text in sight or token.text in story:
            continue
        found = word_graphemes(token.text, mastered)
        if found:
            graphemes_by_word[token.text] = found

    stage_of = {}
    for gpc in load_gpcs():
        if gpc.stage <= stage_id:
            stage_of[gpc.grapheme] = min(stage_of.get(gpc.grapheme, gpc.stage), gpc.stage)

    cues = _cue_index(allowed_words, mastered, exclude=set(graphemes_by_word))

    targets: list[SoundTarget] = []
    for grapheme in sorted({g[0] for g in graphemes_by_word.values()}):
        onset = [w for w, g in graphemes_by_word.items() if g[0] == grapheme]
        clean = [w for w, g in graphemes_by_word.items() if grapheme not in g]
        cue = cues.get(grapheme)
        if not onset or len(clean) < TAP_WORD_OPTIONS - 1 or cue is None:
            continue
        targets.append(
            SoundTarget(
                grapheme=grapheme,
                cue=cue,
                onset_words=onset,
                clean_words=clean,
                stage=stage_of.get(grapheme, 0),
            )
        )

    # Most recently taught sound first — that is the one worth practising —
    # with an alphabetical tiebreak so the list is reproducible.
    targets.sort(key=lambda t: (-t.stage, t.grapheme))
    return [
        # Truncation is safe: verification tests the grapheme, not list
        # membership, so a shorter offer only narrows the model's menu. It
        # happens AFTER tiering so a stage-bearing word cannot be cut first.
        target.model_copy(
            update={
                "onset_words": target.onset_words[:MAX_OFFERED_WORDS],
                "clean_words": target.clean_words[:MAX_OFFERED_WORDS],
            }
        )
        for target in _prefer_current_stage(targets, stage_id)[:limit]
    ]


def _prefer_current_stage(targets: Sequence[SoundTarget], stage_id: int) -> list[SoundTarget]:
    """§8: prefer a question that practises a sound taught AT this stage.

    Three tiers, first non-empty wins (see the module docstring). The tiers
    NARROW the closed list rather than adding anything to it, so every guarantee
    `check_tap_word` proves is untouched — a narrower menu cannot make a wrong
    question right.

    The fallback is deliberate. A stage whose story simply contains no
    current-stage sound still gets a question about the newest sound available;
    dropping the only phonics question would cost the child the whole screen and
    §8 nothing it did not already lack.
    """
    onset_tier = [t for t in targets if t.stage == stage_id]
    if onset_tier:
        return [t.model_copy(update={"scope": "current_stage_onset"}) for t in onset_tier]

    word_tier = []
    for t in targets:
        # Keep only the correct-answer candidates that would actually earn
        # evidence. The distractors are irrelevant here: §8 credits the GPCs of
        # the word the question was ABOUT.
        earning = [w for w in t.onset_words if uses_stage_gpc(w, stage_id)]
        if earning:
            word_tier.append(
                t.model_copy(update={"onset_words": earning, "scope": "current_stage_word"})
            )
    if word_tier:
        return word_tier

    return [t.model_copy(update={"scope": "older_stage_fallback"}) for t in targets]


def _cue_index(
    allowed_words: Sequence[str], mastered: frozenset[str], exclude: set[str]
) -> dict[str, str]:
    """grapheme -> the shortest, then most frequent, allowed word starting with it.

    `allowed_words` arrives frequency-ordered from the precompute table, so rank
    is just the index. Shortest first because a cue is read aloud and compared
    by ear: "Tap: hat." is a cleaner model of /h/ than "Tap: having.", which is
    what pure frequency picked live. Words already in the story are excluded so
    the cue can never also be one of the options.
    """
    best: dict[str, tuple[int, int, str]] = {}
    for rank, raw in enumerate(allowed_words):
        word = raw.strip().lower()
        if not word or word in exclude:
            continue
        found = word_graphemes(word, mastered)
        if not found:
            continue
        key = (len(word), rank, word)
        if found[0] not in best or key < best[found[0]]:
            best[found[0]] = key
    return {grapheme: key[2] for grapheme, key in best.items()}


def tap_word_prompt(
    cue: str,
    stage_id: int,
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
) -> str | None:
    """The richest frame that clears the gate at this stage — or None.

    Deterministic: the same (cue, stage) always yields the same sentence.
    """
    for frame in PROMPT_FRAMES:
        text = frame.format(cue=cue)
        if passes_gate(readable(text, stage_id, sight_words, story_words)):
            return text
    return None


def readable_openers(
    stage_id: int,
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
) -> list[str]:
    """The question words a child at this stage can actually read."""
    return [
        opener
        for opener in QUESTION_OPENERS
        if passes_gate(readable(opener, stage_id, sight_words, story_words))
    ]


def unreadable_words(report: TextReport) -> list[str]:
    """The specific words that failed — §6's rewrite lesson: name them."""
    return sorted(
        {w.word for w in report.words if w.verdict in (WordVerdict.VIOLATION, WordVerdict.OOV)}
    )


def _rng(seed: int, salt: str) -> random.Random:
    digest = hashlib.blake2b(f"{seed}:{salt}".encode(), digest_size=8).digest()
    return random.Random(int.from_bytes(digest, "big"))


def shuffle_options(
    options: Sequence[str], correct: str, seed: int, salt: str
) -> tuple[list[str], int]:
    """Deterministic option order that depends on the SET, not the model.

    Sorting first is the point: without it the model's emission order (correct
    answer first, near enough always) survives into the shuffle for any given
    seed. `random.Random` seeded from a digest is stable across processes and
    unaffected by PYTHONHASHSEED, unlike `hash()` or `random.random()`.
    """
    ordered = sorted(options)
    _rng(seed, salt).shuffle(ordered)
    return ordered, ordered.index(correct)


def _length_balanced(options: Sequence[str], *, single_word: bool) -> bool:
    lengths = [len(o) for o in options]
    if single_word:
        return max(lengths) - min(lengths) <= TAP_LEN_SPREAD
    return max(lengths) <= MCQ_LEN_RATIO * min(lengths)


# --- prompt assembly ---------------------------------------------------------
def build_sound_block(targets: Sequence[SoundTarget]) -> str:
    if not targets:
        return "SOUNDS: (none available for this story)"
    lines = [
        f"- {t.grapheme} (as in {t.cue}) | starts with it: {', '.join(t.onset_words)}"
        f" | does not use it: {', '.join(t.clean_words)}"
        for t in targets
    ]
    return "SOUNDS you may ask about — pick exactly one:\n" + "\n".join(lines)


def build_question_request(
    *,
    story_title: str,
    story_text: str,
    targets: Sequence[SoundTarget],
    needed: Sequence[str],
    openers: Sequence[str] = (),
    problems: Sequence[str] = (),
) -> str:
    """Per-request half of the prompt (after the cached prefix).

    On a retry this carries the SPECIFIC reason each question was rejected, the
    same way §6's rewrite node carries the violating words — "try again" tells
    the model nothing it can act on.
    """
    wanted = " and ".join(
        {"tap_word": "the tap-a-word question", "mcq2": "the two-choice question"}[n]
        for n in needed
    )
    parts = [
        f'STORY — "{story_title}":\n{story_text}',
        build_sound_block(targets),
        f"Write {wanted}.",
    ]
    if openers and "mcq2" in needed:
        parts.append(
            "Your two-choice question MUST begin with one of these words — every "
            f"other question word is unreadable at this stage: {', '.join(openers)}."
        )
    if problems:
        parts.append(
            "Your last attempt was rejected:\n"
            + "\n".join(f"- {p}" for p in problems)
            + "\nFix exactly these things."
        )
    return "\n\n".join(parts)


# --- verification ------------------------------------------------------------
def check_tap_word(
    draft: TapWordDraft,
    *,
    targets: Sequence[SoundTarget],
    stage_id: int,
    story_text: str,
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
    seed: int = 0,
) -> tuple[GeneratedQuestion | None, list[str]]:
    """Prove the question before it can become one. Returns (question, problems)."""
    problems: list[str] = []
    grapheme = draft.target_grapheme.strip().lower()
    target = next((t for t in targets if t.grapheme == grapheme), None)
    if target is None:
        return None, [
            f'"{draft.target_grapheme}" is not one of the sounds offered; pick one from the list'
        ]

    raw_options = [draft.correct_word, *draft.other_words]
    options: list[str] = []
    for raw in raw_options:
        words = [t.text for t in tokenize(raw) if t.is_word]
        if len(words) != 1:
            problems.append(f'"{_norm(raw)}" must be a single word from the story')
            continue
        options.append(words[0])

    if len(options) != TAP_WORD_OPTIONS:
        problems.append(f"give exactly {TAP_WORD_OPTIONS} words: one correct and two others")
        return None, problems
    if len(set(options)) != len(options):
        problems.append("the three words must all be different")
    if target.cue in options:
        problems.append(f'"{target.cue}" is the example word and cannot also be an option')

    correct = options[0]
    in_story = set(story_word_tokens(story_text))
    mastered = mastered_ids(stage_id)

    for option in options:
        if option not in in_story:
            problems.append(f'"{option}" is not in the story')
            continue
        # §11.3: every option is text the child reads. Same gate as the story.
        report = readable(option, stage_id, sight_words, story_words)
        if not passes_gate(report):
            problems.append(
                f'"{option}" is not decodable at this stage'
                + (f" ({', '.join(unreadable_words(report))})" if unreadable_words(report) else "")
            )
            continue
        graphemes = word_graphemes(option, mastered)
        if graphemes is None:
            problems.append(f'"{option}" cannot be sounded out, so it cannot be an option')
            continue
        if option == correct:
            if graphemes[0] != grapheme:
                problems.append(
                    f'"{correct}" does not start with {grapheme} — it starts with {graphemes[0]}'
                )
        elif grapheme in graphemes:
            # Two options carrying the sound means two defensible answers.
            problems.append(f'"{option}" also uses {grapheme}, so it is not a wrong answer')

    # §8: when the story CAN supply a correct word that uses a sound taught at
    # this stage, it must. Otherwise the question is unimpeachable and still
    # earns the child nothing toward promotion — the live stage-4/5 failure.
    # Enforced, not merely offered: the model picks from the list, but nothing
    # made it obey until this check existed.
    if target.scope == "current_stage_word" and not uses_stage_gpc(correct, stage_id):
        problems.append(
            f'"{correct}" uses no sound taught at this stage, so answering it teaches '
            f"nothing new — pick the answer from: {', '.join(target.onset_words)}"
        )

    if not _length_balanced(options, single_word=True):
        problems.append("the three words must be close to the same length")

    prompt = tap_word_prompt(target.cue, stage_id, sight_words, story_words)
    if prompt is None:
        problems.append(
            f'no readable way to ask about "{target.cue}" at this stage — pick another sound'
        )

    if problems or prompt is None:
        return None, problems

    ordered, index = shuffle_options(options, correct, seed, f"tap_word:{grapheme}")
    return (
        GeneratedQuestion(
            qtype="tap_word",
            prompt=prompt,
            # Served to the client. `cue_word` names the sound; it is drawn from
            # the allowed list and can never be one of the options, so it gives
            # nothing away. The grapheme itself would (see the module docstring).
            payload={"options": ordered, "cue_word": target.cue},
            # Server-side only (§9, DEVIATIONS 4.9). `target_grapheme` rides with
            # the key because it IS the key: exactly one option starts with it.
            answer={"correct": correct, "index": index, "target_grapheme": grapheme},
        ),
        [],
    )


def check_mcq2(
    draft: Mcq2Draft,
    *,
    stage_id: int,
    story_text: str,
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
    seed: int = 0,
) -> tuple[GeneratedQuestion | None, list[str]]:
    problems: list[str] = []
    prompt = _norm(draft.prompt)
    correct = _norm(draft.correct)
    incorrect = _norm(draft.incorrect)

    if not prompt.endswith("?"):
        problems.append("the question must end with a question mark")
    report = readable(prompt, stage_id, sight_words, story_words)
    if not passes_gate(report):
        problems.append(
            f'the question "{prompt}" uses words this child cannot read: '
            + ", ".join(unreadable_words(report))
        )

    if correct.lower() == incorrect.lower():
        problems.append("the two answers must be different")

    for label, option in (("true answer", correct), ("false answer", incorrect)):
        if not [t for t in tokenize(option) if t.is_word]:
            problems.append(f"the {label} is empty")
            continue
        report = readable(option, stage_id, sight_words, story_words)
        if not passes_gate(report):
            problems.append(
                f'the {label} "{option}" uses words this child cannot read: '
                + ", ".join(unreadable_words(report))
            )

    if not story_supports(correct, story_text):
        problems.append(
            f'"{correct}" is not what the story says — use the story\'s words, in its order'
        )
    if story_supports(incorrect, story_text):
        problems.append(f'"{incorrect}" is also true in the story, so it is not a wrong answer')
    # Sight words are shared by every English sentence, so they prove nothing
    # about grounding — "a" and "the" would make any alien option look related.
    sight, _ = _sets(sight_words, ())
    if not (
        {w for w in story_word_tokens(incorrect) if w not in sight}
        & {w for w in story_word_tokens(story_text) if w not in sight}
    ):
        problems.append(
            f'"{incorrect}" shares no words with the story — build it from the story\'s words'
        )

    if not _length_balanced([correct, incorrect], single_word=False):
        problems.append("the two answers must be close to the same length")

    if problems:
        return None, problems

    ordered, index = shuffle_options([correct, incorrect], correct, seed, "mcq2")
    return (
        GeneratedQuestion(
            qtype="mcq2",
            prompt=prompt,
            payload={"options": ordered},
            answer={"correct": correct, "index": index},
        ),
        [],
    )


# --- the node ----------------------------------------------------------------
def _schema_for(needed: Sequence[str]):
    if len(needed) == 2:
        return QuestionDraft
    return TapWordOnly if needed[0] == "tap_word" else Mcq2Only


async def _call(
    *,
    stage_name: str,
    allowed_words: Sequence[str],
    sight_words: Sequence[str],
    user_block: str,
    schema,
) -> tuple[object | None, Usage, str | None]:
    client = get_client().with_options(timeout=FAST_TIMEOUT_S)
    model = fast_model()
    response = await client.messages.parse(
        model=model,
        max_tokens=2000,
        system=[
            {"type": "text", "text": QUESTION_RULES},
            {
                "type": "text",
                "text": build_vocabulary_block(stage_name, list(allowed_words), list(sight_words)),
                # Same breakpoint discipline as generation: every story at a
                # stage reads the vocabulary from cache.
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": user_block}],
        output_format=schema,
    )
    return response.parsed_output, usage_from_response(model, response.usage), response.stop_reason


async def generate_questions(
    *,
    stage_id: int,
    stage_name: str,
    story_title: str,
    story_text: str,
    allowed_words: Sequence[str],
    sight_words: Sequence[str],
    story_words: Sequence[str] = (),
    seed: int = 0,
    max_attempts: int = MAX_QUESTION_ATTEMPTS,
) -> QuestionSet:
    """Write the §11.3 question pair for one validated story.

    `story_words` must be the story's ACTUAL distinct story words (from the
    validated report), never a model claim — same reason as
    `pipeline.validate_draft`: an unchecked claim lets a generator shrink its
    own denominator.

    `seed` makes option order reproducible. A story replays to the same screen.
    """
    targets = sound_targets(
        story_text=story_text,
        stage_id=stage_id,
        allowed_words=allowed_words,
        sight_words=sight_words,
        story_words=story_words,
    )

    openers = readable_openers(stage_id, sight_words, story_words)

    accepted: dict[str, GeneratedQuestion] = {}
    dropped: dict[str, list[str]] = {}
    needed = ["tap_word", "mcq2"] if targets else ["mcq2"]
    if not targets:
        dropped["tap_word"] = ["no sound in this story has one onset word and two clean words"]

    total = Usage(model=fast_model())
    problems: list[str] = []
    attempts = 0

    common = {
        "stage_id": stage_id,
        "story_text": story_text,
        "sight_words": sight_words,
        "story_words": story_words,
        "seed": seed,
    }

    while needed and attempts < max_attempts:
        attempts += 1
        parsed, usage, stop_reason = await _call(
            stage_name=stage_name,
            allowed_words=allowed_words,
            sight_words=sight_words,
            user_block=build_question_request(
                story_title=story_title,
                story_text=story_text,
                targets=targets,
                needed=needed,
                openers=openers,
                problems=problems,
            ),
            schema=_schema_for(needed),
        )
        total = Usage(
            model=total.model,
            input_tokens=total.input_tokens + usage.input_tokens,
            output_tokens=total.output_tokens + usage.output_tokens,
            cache_creation_input_tokens=(
                total.cache_creation_input_tokens + usage.cache_creation_input_tokens
            ),
            cache_read_input_tokens=total.cache_read_input_tokens + usage.cache_read_input_tokens,
        )

        if parsed is None:
            # §7: a schema-parse failure is an api_fail, not a pass.
            problems = [f"the answer could not be read (stop_reason={stop_reason})"]
            for qtype in needed:
                dropped.setdefault(qtype, []).extend(problems)
            continue

        problems = []
        for qtype in list(needed):
            draft = getattr(parsed, qtype, None)
            if draft is None:
                problems.append(f"the {qtype} question was missing")
                continue
            if qtype == "tap_word":
                question, why = check_tap_word(draft, targets=targets, **common)
            else:
                question, why = check_mcq2(draft, **common)
            if question is not None:
                accepted[qtype] = question
                needed.remove(qtype)
            else:
                problems.extend(why)
                dropped[qtype] = why

    for qtype in needed:
        dropped.setdefault(qtype, problems or ["retry budget exhausted"])

    # Stable order: the phonics question first, comprehension second (§11.3).
    questions = [accepted[q] for q in ("tap_word", "mcq2") if q in accepted]
    for idx, question in enumerate(questions):
        question.idx = idx

    return QuestionSet(
        questions=questions,
        usage=total,
        attempts=attempts,
        dropped={k: v for k, v in dropped.items() if k not in accepted},
        tap_word_target=describe_target(accepted.get("tap_word"), targets, stage_id),
    )


def describe_target(
    question: GeneratedQuestion | None, targets: Sequence[SoundTarget], stage_id: int
) -> dict:
    """What the tap-a-word question ended up practising (§8/§14).

    Reported for every outcome, including the ones with no question, because
    "this story could offer nothing at this stage" is the measurement that tells
    an operator the allowlist — not the node — is the problem.
    """
    if question is None:
        return {
            "scope": "none_available" if not targets else "dropped",
            "offered": len(targets),
            "stage_id": stage_id,
        }
    grapheme = question.answer.get("target_grapheme")
    target = next((t for t in targets if t.grapheme == grapheme), None)
    return {
        "grapheme": grapheme,
        "stage": target.stage if target else None,
        "scope": target.scope if target else "unknown",
        "offered": len(targets),
        "stage_id": stage_id,
        # The decisive fact, computed from the answer rather than trusted:
        # does the credited word carry a sound this stage teaches?
        "earns_stage_evidence": uses_stage_gpc(question.answer.get("correct", ""), stage_id),
    }
