"""Question-generation node (§7 ENRICHING, §11.3).

§7 fans QUESTION_GEN out in parallel with SCENE_TAGGING once the validator has
passed. §11.3 fixes the inventory at exactly two types — `tap_word` ("tap the
word that says /sh/ip") and `mcq2` (a two-choice story question). Everything a
child has to READ to answer is gated by the decoder exactly as the story is, and
that gate is never lowered (§0): an OPTION of either type, and the whole of an
`mcq2` prompt, containing an undecodable word is a bug and not a stylistic
wobble.

**The one exception is the tap-a-word INSTRUCTION, and it is the spec's
exception, not a convenience** (DEVIATIONS 11.60). §11.3 attaches "validated
decodable" to `mcq2`'s *options* and to nothing else, and §10 separately spells
the same requirement out for choice labels — so the doc states it in both places
it wants it and states it for no prompt. Its own example instruction, "tap the
word that says /sh/ip", fails the gate at every stage 1-12 (`word` and `says`
are undecodable throughout), so prompt-decodability cannot be what it meant. And
the constraint bought nothing measurable: §8 credits the option the child TAPS
(`api/readings.py` reads `answer["correct"]` alone), so not one grapheme of any
prompt has ever moved `gpc_stats`. What it cost was 71 of 125 live rows whose
instruction never named the task. An instruction is not reading practice; it is
the scaffolding that says what the practice IS, it is spoken aloud on mount
(DEVIATIONS 6.27) and every word of it is individually tappable for TTS.

So the frame is validated against `INSTRUCTION_WORDS` as well as the stage's
sight words — the same parameter, the same mechanism §4 already has for
irregular function words. The gate still runs, and it still proves the one thing
that matters: **the CUE WORD is decodable at the child's stage**, because the cue
is not in the lexicon. `mcq2`'s prompt is NOT exempt — it is the comprehension
content, which the child must read to answer, so there decodability is the task
rather than the scaffolding.

The architecture mirrors `llm/generate.py`: the model is an untrusted generator
and a pure function is the judge (STATE invariant 2). Nothing the model claims
is taken on trust.

    tap_word  Options are drawn from a CLOSED list computed from the story
              itself. The claimed answer must genuinely begin with the target
              sound — proved from the decoder's own GPC alignment, not from the
              model's say-so — and every distractor must miss it, so exactly one
              option *can* be correct. Four proofs, two about LETTERS and two
              about SOUND, because the instruction is about sound and the answer
              key is about letters (DEVIATIONS 11.110): the answer's first
              grapheme is the target grapheme and its first phoneme is the CUE's;
              no distractor reuses the target grapheme anywhere in its alignment
              and none starts with the answer's phoneme. The two are not
              interchangeable — the inventory has 12 graphemes with more than one
              phoneme and 26 phonemes with more than one grapheme — and proving
              only the first pair shipped a live question with no correct answer.
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

**The instruction is one sentence, and it is the same one at every stage.** A
phonics prompt normally names the sound ("/sh/"), but a bare grapheme is not a
word: 61 of the inventory's 78 graphemes are OOV or a violation when tokenised.
More importantly the grapheme is the ANSWER KEY — exactly one option starts with
it — so printing it would leak worse than shipping it in `payload` did (4.9,
7.14). The target is therefore carried as a decodable CUE WORD, and §11.3's own
shape is kept with the cue where the spec writes the grapheme:

    "Tap the word that starts with the same sound as {cue}."

`PROMPT_FRAMES` is still tried richest-first and the first frame that passes the
gate still wins, because the mechanism is what makes `None` — "this cue cannot be
asked about at this stage" — a real answer that `sound_targets` and
`check_tap_word` both rely on. But there are now two rungs rather than six, and
the reason is DEVIATIONS 11.60/11.62: the ladder had six rungs because the gate
made a frame's readability depend on the stage, and it no longer does. Six
per-stage rungs would be dead code, which is precisely how this shipped broken
the first time.

*The history is worth keeping, because it is what proves the constraint was the
problem rather than the wording.* The first version was written from English
rather than from the inventory: every rung above the floor contained "word" or
"one" — "word" is undecodable at every stage 1-12 and "one" is not a sight word
until stage 12 — so three of four rungs were unreachable and all 28 live prompts
were the bare cue, `Tap: get.` The second version derived each rung from the
stage's own cumulative inventory and did pass the gate at 18/18. It was still
unusable, and the live pool is the evidence: 38 of 125 rows said `Tap it: X.` or
`Tap it. Hint: X.`, which name nothing to compare on a screen holding three
options, and 33 more said `Tap it if it begins like X.`, a conditional with no
identified subject. 57% of live instructions did not say what the task was, and
that is what the user reported. Every similarity word a stage-2 child can read —
`as`, `like`, `same`, `alike` — is stage 4 or later; the only stage-3 candidate,
`twin`, claims the options are IDENTICAL to the cue, which is a false
instruction. So the sentence could not get clearer while the constraint held.
The constraint was the wrong one, and 11.60 retires it.

The floor `"Tap: {cue}."` is retained for the cue that breaks the rung above it —
not for a stage — which is the one slot a rung is allowed to be unreachable in.

**`target_grapheme` is answer-key material and lives in `answer`, never in
`payload`.** `check_tap_word` guarantees that exactly one option starts with the
target grapheme and that no distractor uses it anywhere, which is precisely what
makes the question checkable — and precisely what makes the grapheme a complete
giveaway. Shipped in the payload it reduced the question to
`[o for o in options if o.startswith(payload["target_grapheme"])][0]`, so §9's
"the answer key never leaves the server" (DEVIATIONS 4.9) was false for every
tap-a-word question.

**`cue_word` IS served, so it must carry no orthographic signal beyond the
target sound** (DEVIATIONS 7.14). "Drawn from the allowed list and excluded from
the options" was not enough, and live row 4392 is the proof: `Tap it. Hint: the.`
over `["sat", "in", "then"]`. `the` is a prefix of exactly one option, so the
child taps the only word that starts with those three letters and is right
without segmenting anything — and §8 then credits `th` for the guess. Row 696 is
the same defect with the opposite sign: cue `win` over `["in", "had", "went"]`,
where `in` is a *suffix* of the cue, so the shape match points at a WRONG option
and steers the child away from the word they were meant to sound out.

The rule is therefore a property, not a list of cases: *the cue may share the
target grapheme with an option and nothing else.* Operationally —

    the cue must stand in NO whole-word containment relation with ANY option
    (`cue in option` or `option in cue`, which subsumes identity, prefix and
    suffix in both directions).

Sharing the grapheme is exactly what the question asks the child to notice, and
`check_tap_word` already proves the grapheme singles out one option; what a
whole-word match adds is a way to answer *without* segmenting anything, since
spotting the chunk `the` inside `then` needs no knowledge that `th` says /θ/.
So the boundary is the word: the grapheme is the question, the whole cue is the
answer key. `cue_conflict` decides it, `sound_targets` picks a cue that satisfies
it against the sound's whole closed offer list, and `check_tap_word` re-proves it
against the three options the model actually chose — a guarantee that only lives
in the prompt is a suggestion.

Determinism: option order is a pure function of (seed, question type, option
set). `random.Random` is seeded from a blake2b digest — stable across processes
and independent of PYTHONHASHSEED — and the option list is canonically sorted
before shuffling, so the model's emission order cannot leak the answer either.

**Every sound this module OFFERS is provably answerable, and when the model
cannot pick a legal answer out of the closed list, this module picks it**
(DEVIATIONS 8.53, 8.54). A full `make warm` left six servable stories with no
questions at all, every one of them dropping `tap_word` for "the three words must
be close to the same length" — and the measurement that matters is that a legal,
length-balanced triple existed anyway in five of the six, 21 of them in one case.
The length rule was not the binding constraint and is unchanged. Two things were:

    the menu was dishonest    `_prefer_current_stage` returned the first NON-EMPTY
                              tier. At stage 8 the `current_stage_onset` tier is
                              routinely one sound with one onset word — stage 8
                              teaches `ou` and the only `ou`-onset word a
                              decodable story holds is `outside`, seven letters
                              against a story of four-letter words. Non-empty, so
                              it fired, so the model was handed a single sound
                              that admits NO legal question and three attempts
                              later both types were gone. A tier now fires only
                              when it holds a sound that can actually be answered,
                              which preserves §8's preference exactly (the ORDER
                              does not move) while letting stage 8 fall through to
                              `current_stage_word` — a tier whose definition is
                              "the correct word uses a sound this stage teaches",
                              so those stories earn MORE §8 evidence, not less.

    the proposer was the      `tap_word` is the one type no model writes: the
    weakest link              prompt is a frame, every option is a story word that
                              already cleared the gate and §13 with the body, and
                              `LAYER3_EXEMPT_QTYPES` has said so since Phase 4. The
                              model's only job is CHOOSING three words from a
                              closed list this module composed — and it chose badly
                              on 12 of 113 stories while a legal choice sat in the
                              list. So when the retry budget is spent,
                              `tap_word_options` composes the triple and
                              `check_tap_word` judges it unchanged. The proposer
                              changes; the judge does not. Zero tokens.

`tap_word_options` is the one pure function all of that rests on: it enumerates
the closed list and returns the best legal option set, or `None` when the sound
cannot be asked about at all. `sound_targets` drops those, so a sound in the menu
is a promise rather than a suggestion.
"""

from __future__ import annotations

import hashlib
import itertools
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
from storyforge.mastery import MIN_GPC_OBSERVATIONS, STORIES_REQUIRED_AT_STAGE
from storyforge.mastery import word_gpc_ids as graded_gpc_ids
from storyforge.pipeline import passes_gate

# §7: one generation plus two retries. Never raised to make a question pass.
MAX_QUESTION_ATTEMPTS = 3
# §11.3.
TAP_WORD_OPTIONS = 3
MCQ2_OPTIONS = 2
# How many phonics questions one story may carry (DEVIATIONS 11.67). §11.3 fixes
# the number of question TYPES at two, not the number of questions, and the
# second one is what makes `mastery.MIN_GPC_OBSERVATIONS` reachable inside §8's
# own five stories: measured over the live pool, one `tap_word` credits 1.08
# current-stage GPC instances per story (9.2 stories to reach the floor of 10)
# and two credit 1.95 (5.1 stories). Raising this further is not free — each
# question is another screen for a five-year-old — so it is a named constant
# rather than a loop bound.
MAX_TAP_WORDS = 2

# How many §8 observations one story has to carry for the promotion floor to be
# reachable in the number of stories §8 itself asks for. DERIVED, not chosen:
# `MIN_GPC_OBSERVATIONS / STORIES_REQUIRED_AT_STAGE` = 10 / 5 = 2, so a story
# whose phonics question already credits 2 current-stage GPC instances is on
# track and gets no second question — the extra screen would buy nothing and
# every screen costs a five-year-old's attention. Measured: this is why stage 1
# (2.92 observations from one question) is left alone while stages 3-11 (~1.0)
# are not. A test pins it against the two thresholds so raising either one here
# cannot silently stop being consistent with §8.
EVIDENCE_PER_STORY_TARGET = -(-MIN_GPC_OBSERVATIONS // STORIES_REQUIRED_AT_STAGE)
# Keep the closed sound list short enough that the model reads all of it.
MAX_SOUND_TARGETS = 6
MAX_OFFERED_WORDS = 8
# How many cue words to keep per grapheme before the first safe one is chosen
# (DEVIATIONS 7.14). One was the old behaviour and is what shipped the leak:
# when the single best cue collided with an option there was nothing to fall
# back to but dropping the sound entirely.
MAX_CUE_CANDIDATES = 24
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

# The tap-a-word instruction's own vocabulary, exempt from the decodability gate
# (DEVIATIONS 11.60). Deliberately a CLOSED, committed list rather than a rule:
# it is auditable in one place, it is human-authored and human-reviewed, and it
# cannot grow silently — `test_questions.py` pins its contents and asserts every
# member is a function or instruction word.
#
# What must never enter it: any word that could be a CUE or an OPTION. The cue is
# the phonics content and stays gated at the child's stage; the options are what
# §8 credits. Every word here is either a determiner, a preposition, a pronoun or
# part of the fixed verb phrase "tap ... starts with the same sound as".
INSTRUCTION_WORDS = frozenset(
    {"tap", "the", "word", "that", "starts", "with", "same", "sound", "as"}
)

# Tried in order; the first one that clears the gate at this stage is used. Two
# rungs, not six: under 11.60 a frame's readability no longer depends on the
# stage, so per-stage rungs would be dead code (11.62) — and `test_questions.py`
# still asserts no rung is dead, which is exactly how this shipped broken once.
# The mechanism is kept because `None` ("this cue cannot be asked about here") is
# load-bearing for `sound_targets` and `check_tap_word`.
PROMPT_FRAMES = (
    # §11.3's own shape, with the decodable cue where the spec writes /sh/.
    "Tap the word that starts with the same sound as {cue}.",
    # Last resort. Kept because a cue word is chosen per story and a frame that
    # fails for one cue must not cost the child the whole phonics screen.
    "Tap: {cue}.",
)


def prompt_is_current(prompt: str | None, cue: str | None) -> bool:
    """Could the ladder ABOVE have written this instruction for this cue word?

    The read side of `tap_word_prompt`, and it lives here — next to the frames,
    spelled out of them — for the reason DEVIATIONS 11.84 gives: a pool gate that
    restated the shape as its own `LIKE 'Tap the word that %'` would be free to
    stop agreeing with the ladder it is guarding, silently, the next time the
    ladder moves. That is exactly the defect it exists to catch.

    A membership test rather than a re-derivation, deliberately. It needs no
    stage vocabulary and no database, so a caller can ask it of every row in the
    pool in one pass, and "no rung of `PROMPT_FRAMES` produces this string" is
    the proposition. The stricter question — *is this the rung `tap_word_prompt`
    would pick today* — is `warm.refresh_tap_word_prompts` returning `(0, [])`,
    which needs the stage's vocabulary and is asserted separately.

    The cue is the row's OWN `payload.cue_word`, so a prompt naming some other
    word is not current either: the instruction and the hint the child is shown
    have to be about the same sound. No cue, nothing to check it against —
    `False`, which is the answer for a legacy row that predates 4.9's payload.
    """
    if not prompt or not cue:
        return False
    return prompt in {frame.format(cue=cue) for frame in PROMPT_FRAMES}

# Frozen. Per-request material lives in `messages`, so this prefix (plus the
# stage vocabulary) stays cacheable across every story at a stage.
#
# The worked examples below deliberately use a theme-free micro-vocabulary (`pup`,
# `pot`, `bun`, `nut`, `shed`) rather than the live rows they were derived from,
# which named dragons and burrows. `test_question_rules_are_frozen_of_request_detail`
# is the tripwire and it is right to be: a frozen example naming a THEME entity is
# indistinguishable, by inspection, from story material that leaked into the cached
# prefix, and the next editor should not have to make that judgement. Keep examples
# theme-free.
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
   - The three words must be within two letters of the same length. A word that \
     is longer or shorter than the other two can be picked out by its shape, so \
     the child never has to sound anything out. Choose the three lengths first, \
     then the words.

2. TWO-CHOICE QUESTION. Write the two answers FIRST, then the question. Doing it \
in that order is what makes the question askable, because the question has to be \
about the part the two answers agree on.
   - Build the true answer by copying a sentence from the story, or by \
     shortening one. Keep the story's words in the story's own order, and add \
     nothing the story does not say.
   - Build the false answer from the story's words too, then put them in an \
     order the story never uses — swap who did what, or swap what happened to \
     what. Read your false answer back and check you cannot find its words in \
     the story one after another in that same order; if you can, it is not a \
     wrong answer.
   - Do NOT make the false answer by adding or removing the word "not". Every \
     other word stays in the story's own order, so the story still reads as \
     saying it, and it does not count as a wrong answer.
   - The two answers must have the same number of words and be within about \
     three letters of the same length. A longer or shorter answer tells the \
     child which one to pick without reading it.
   - Now find the words that are in ONE answer and not the other. Those words \
     are the whole question, so they must NOT appear in the question you write. \
     Ask about what the two answers SHARE. This is the rule most questions get \
     wrong, so check it word by word before you answer.
     Good — "What did the pup get from the pot?" for "The pup got a bun from \
     the pot." and "The pup got a nut from the pot.": both answers say the pup \
     got something from the pot, so the question may say that, and only "bun" \
     and "nut" are held back.
     Good with only yes-or-no opening words — "Did the pup dig?" for "It dug \
     in the mud." and "It dug in the sand.": both answers agree that it dug, so \
     the question asks just that, and "mud" and "sand" stay on the answers. \
     Keep the question SHORT for this reason: every extra word you add is \
     another chance to name the difference.
     Bad — "Did the pup dig in the mud?" for "It dug in the mud." and "It dug \
     in the sand.": the question already says "mud", so the child picks the \
     answer with the matching word and never reads the story. Words like "in", \
     "on", "up" and "back" count too if they are what differs.
     Bad the other way — "Was the pup glad?" for "A man was glad." and "The \
     pup was glad.": the question names the pup, which points at the FALSE \
     answer.
   - Never begin an answer with Yes or No. Prefer a question that asks WHICH \
     or WHERE or WHAT happened over one answered by yes or no, using only the \
     opening words you are given.

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
    #: Question types this module composed itself after the model's retry budget
    #: was spent (DEVIATIONS 8.54). Reported for the same reason `dropped` is: a
    #: recovered question is not a normal one. `warm.enrichment_problems` fails a
    #: run in which EVERY surviving question is derived — that is the signature of
    #: a model returning nothing usable at all, and before this field existed the
    #: fallback would have turned that from a non-zero exit into a quiet success.
    derived: list[str] = Field(default_factory=list)
    #: The ADDITIONAL phonics questions this module composed by pure function
    #: (DEVIATIONS 11.67), described the same way `tap_word_target` describes the
    #: first. Deliberately NOT folded into `derived`: that field means "the model's
    #: retries were spent and this had to be recovered", it is read as a SET of
    #: qtypes by `warm.enrichment_problems`, and adding a second `tap_word` to it
    #: would raise 8.58's outage alarm on any story whose `mcq2` was merely
    #: dropped — a false alarm in exactly the case the model did contribute.
    #: An extra question is a designed one, not a recovery.
    extra_tap_words: list[dict] = Field(default_factory=list)
    #: §8/§14: which sound the tap-a-word question ended up practising, and which
    #: tier of `_prefer_current_stage` produced it. Surfaced (rather than logged)
    #: so the eval harness can measure how often the fallback fires — a high
    #: `older_stage_fallback` rate at some stage is a finding about the ALLOWLIST,
    #: not about this node.
    tap_word_target: dict = Field(default_factory=dict)


# --- pure helpers ------------------------------------------------------------
def _norm(word: str) -> str:
    return " ".join(word.strip().split())


def _quoted_words(words: Iterable[str]) -> str:
    return ", ".join(f'"{w}"' for w in words)


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


def word_gpcs(word: str, mastered: frozenset[str]) -> list[str] | None:
    """The GPC ids the decoder actually used to sound `word` out, in order.

    ``None`` when the word is not decodable at this stage — which is also the
    answer to "may this word be a tap-a-word option?".

    A GPC id is ``"sh|SH"``: grapheme before the pipe, phonemes after it. Both
    halves are load-bearing and they are NOT interchangeable (DEVIATIONS 11.110).
    The inventory holds 12 graphemes carrying more than one phoneme and 26
    phonemes spelled more than one way, so `c` alone does not name a sound —
    `c|K` in `can` and `c|S` in `city` are different sounds sharing a letter.
    """
    report = validate_word(word, mastered)
    if report.verdict is not WordVerdict.DECODABLE or not report.alignment:
        return None
    return list(report.alignment)


def word_graphemes(word: str, mastered: frozenset[str]) -> list[str] | None:
    """The graphemes the decoder actually used to sound `word` out, in order.

    ``None`` when the word is not decodable at this stage. Spelled out of
    `word_gpcs` so the two cannot disagree about what "decodable" means.
    """
    found = word_gpcs(word, mastered)
    return None if found is None else [gpc_grapheme(gid) for gid in found]


def gpc_grapheme(gpc_id: str) -> str:
    """The letters of a GPC id — ``"sh|SH"`` -> ``"sh"``."""
    return gpc_id.split("|", 1)[0]


def gpc_phoneme(gpc_id: str) -> str:
    """The SOUND of a GPC id — ``"sh|SH"`` -> ``"SH"``, ``"x|K.S"`` -> ``"K.S"``.

    The whole tail, not the first phoneme of it: a two-phoneme grapheme like
    `x` says /ks/ as one indivisible onset, and comparing only `K` would call
    `x` and `c` the same starting sound.
    """
    return gpc_id.split("|", 1)[1] if "|" in gpc_id else ""


def onset_gpc(word: str, mastered: frozenset[str]) -> str | None:
    """The GPC this word STARTS with — the whole id, letters and sound.

    ``None`` when the word cannot be sounded out at this stage, which is the
    same answer `word_gpcs` gives and for the same reason.
    """
    found = word_gpcs(word, mastered)
    return found[0] if found else None


def onset_phoneme(word: str, mastered: frozenset[str]) -> str | None:
    """The SOUND this word starts with — what the child is actually asked for.

    `PROMPT_FRAMES[0]` says "starts with the same **sound** as {cue}", and the
    web speaks "Which word starts **like** {cue}?". This is the function that
    proposition is proved with (DEVIATIONS 11.110-11.111); the grapheme proofs in
    `check_tap_word` are kept beside it, not replaced by it.
    """
    found = onset_gpc(word, mastered)
    return None if found is None else gpc_phoneme(found)


@lru_cache(maxsize=16)
def stage_gpc_ids(stage_id: int) -> frozenset[str]:
    """The GPC ids INTRODUCED at this stage — §8's promotion denominator.

    `mastered_ids` is cumulative (everything up to and including the stage);
    this is the thin slice the stage actually teaches, which is what promotion
    is measured over.
    """
    return frozenset(g.id for g in load_gpcs() if g.stage == stage_id)


def stage_evidence(question: GeneratedQuestion, stage_id: int) -> int:
    """How many §8 observations this question will actually credit.

    The GPC instances in the credited word that `stage_id` INTRODUCES — which is
    exactly `evaluate_promotion`'s denominator, not the cumulative set. Computed
    from the answer key rather than from the target, so it measures what the
    grader will count.
    """
    introduced = stage_gpc_ids(stage_id)
    word = str(question.answer.get("correct", ""))
    return sum(1 for gid in graded_gpc_ids(word, stage_id) if gid in introduced)


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


#: Words that turn an answer into its own negation. `check_mcq2` refuses a pair
#: that differs only by one of these (DEVIATIONS 11.64).
NEGATION_WORDS = frozenset({"not", "no", "never"})


def discriminating_words(correct: str, incorrect: str, sight: frozenset[str]) -> set[str]:
    """The content words that appear in exactly ONE of the two answers.

    These are what the child has to read the story to choose between. A word in
    BOTH answers is the shared premise and carries no information about which
    card is right — which is why the question SHOULD restate it ("What did he
    take from the moon?" over "a rock" / "a star").

    Sight words are excluded for the same reason `check_mcq2` already excludes
    them from its grounding test: they are shared by every English sentence, so
    treating "a" or "the" as discriminating would refuse almost every question.
    """
    a = {w for w in story_word_tokens(correct) if w not in sight}
    b = {w for w in story_word_tokens(incorrect) if w not in sight}
    return (a - b) | (b - a)


def prompt_leaks_answer(
    prompt: str, correct: str, incorrect: str, sight: frozenset[str]
) -> list[str]:
    """Every word by which the QUESTION gives one of the two answers away.

    DEVIATIONS 11.64, and the same defect class as `cue_leaks`: a question that
    contains the word distinguishing the two cards can be answered by matching
    letters against the cards, with the story unread. 40 of 74 live `mcq2` rows
    did exactly that — "Did the rabbit run into the burrow?" over "ran fast into
    the burrow" / "ran fast past the burrow" needs no comprehension at all.

    It is symmetric on purpose. A question that echoes the FALSE answer is the
    same defect with a worse sign, because letter-matching then points at the
    wrong card: live row "Was the dragon glad?" has the true answer "A man was
    glad."
    """
    leaked = discriminating_words(correct, incorrect, sight) & set(story_word_tokens(prompt))
    return sorted(leaked)


def mcq2_pair(payload: object, answer: object) -> tuple[str, str] | None:
    """The (true, false) answers a PERSISTED `mcq2` row is asking about.

    `check_mcq2` judges a draft, which carries the pair as two named fields; a
    row in the pool carries the same pair as `payload.options` (shuffled, so the
    order says nothing) plus `answer.correct`. This is the one place that
    reverses the shuffle, so a sweep over the pool can hand `prompt_leaks_answer`
    exactly what the generator hands it and the two cannot end up judging
    different strings.

    ``None`` for a row this cannot be read out of — no `correct`, or no second
    option distinct from it. A caller must treat that as "unknown", never as
    "clean": the row is malformed, not proven innocent.
    """
    options = payload.get("options") if isinstance(payload, dict) else None
    correct = str((answer or {}).get("correct") or "").strip() if isinstance(answer, dict) else ""
    if not correct or not isinstance(options, list):
        return None
    incorrect = next(
        (o for option in options if (o := str(option).strip()) and o.lower() != correct.lower()),
        "",
    )
    return (correct, incorrect) if incorrect else None


def negation_pair(correct: str, incorrect: str) -> bool:
    """Is one answer just the other with `not` added or removed?

    `QUESTION_RULES` has forbidden this since 8.55 and 3 live rows do it anyway,
    because until now the only thing enforcing it was `story_supports` — which
    catches a negated distractor only when the negation happens to break the
    subsequence. "The moon was white and high." / "The moon was white and not
    high." slipped through on exactly that accident.
    """
    a = [w for w in story_word_tokens(correct) if w not in NEGATION_WORDS]
    b = [w for w in story_word_tokens(incorrect) if w not in NEGATION_WORDS]
    return a == b and story_word_tokens(correct) != story_word_tokens(incorrect)


def cue_conflict(cue: str, option: str) -> str | None:
    """How the cue gives this option away by SHAPE, or ``None`` if it does not.

    DEVIATIONS 7.14. Containment in either direction is the whole rule, and it
    subsumes identity, prefix and suffix — those are the four cases a child can
    settle by laying one word against another without reading either. Sharing
    only the target grapheme is deliberately NOT a conflict: that is the signal
    the question exists to test.

    Returns a phrase naming the relation, so the caller's problem string can say
    what is wrong rather than that something is.
    """
    a, b = cue.strip().lower(), option.strip().lower()
    if not a or not b:
        return None
    if a == b:
        return f'the hint word "{a}" IS the option "{b}"'
    if b.startswith(a):
        return f'the option "{b}" starts with the whole hint word "{a}"'
    if b.endswith(a):
        return f'the option "{b}" ends with the whole hint word "{a}"'
    if a in b:
        return f'the option "{b}" contains the whole hint word "{a}"'
    if a.startswith(b) or a.endswith(b) or b in a:
        return f'the hint word "{a}" contains the whole option "{b}"'
    return None


def cue_leaks(cue: str, options: Iterable[str]) -> list[str]:
    """Every way this cue points at one of these options by shape (7.14)."""
    return [why for option in options if (why := cue_conflict(cue, option)) is not None]


def choose_cue(candidates: Sequence[str], options: Iterable[str]) -> str | None:
    """The best candidate cue that gives none of `options` away, or None.

    `candidates` arrives already ranked (shortest, then most frequent), so this
    is "the first safe one". None means this sound cannot be asked about with
    this exact option set — the sound goes, never the rule. Used where the
    options are already FIXED: `warm.repair_leaky_cues` re-cues a row that a
    child may have answered, so the options are the one thing it must not move.
    """
    words = list(options)
    return next((c for c in candidates if not cue_leaks(c, words)), None)


def cue_candidates(
    allowed_words: Sequence[str], stage_id: int, exclude: Iterable[str] = ()
) -> dict[str, list[str]]:
    """grapheme -> the cue words this stage could use for it, best first.

    The public face of `_cue_index`, for callers that hold an option set rather
    than a story: `warm.repair_leaky_cues` re-cues rows already in the pool and
    must reach the same candidate list the node would have drawn from, or a
    repaired row is not the row the node would write.
    """
    return _cue_index(allowed_words, mastered_ids(stage_id), {w.strip().lower() for w in exclude})


def _cue_and_offer(
    candidates: Sequence[str], onset: Sequence[str], clean: Sequence[str]
) -> tuple[str | None, list[str], list[str]]:
    """The best cue for this sound, with the offer list narrowed to fit it.

    A conflict with one offered word is not a reason to lose the sound: the
    offer list is a CLOSED list this module composes, so the word can simply
    leave it — the same narrowing `_prefer_current_stage` already does, and
    narrowing can only shrink what the model may pick. The sound is dropped
    only when no candidate leaves an answerable question behind (one onset word
    and two clean ones).

    Measured over the 52 servable stories in the live pool, this costs nothing:
    all 52 still offer a sound and the total offered targets is unchanged at
    223, so the rule in 7.14 buys its safety for free at production vocabulary
    sizes.
    """
    for cue in candidates:
        keep_onset = [w for w in onset if cue_conflict(cue, w) is None]
        keep_clean = [w for w in clean if cue_conflict(cue, w) is None]
        if keep_onset and len(keep_clean) >= TAP_WORD_OPTIONS - 1:
            return cue, keep_onset, keep_clean
    return None, [], []


def tap_word_options(target: SoundTarget) -> list[str] | None:
    """The best legal option set for this sound — `[correct, other, other]`.

    ``None`` when the sound admits no legal question at all, which is the case
    `sound_targets` used to offer anyway and the model then spent three attempts
    failing to satisfy (DEVIATIONS 8.53). Stage 8 is the clean example: the
    `current_stage_onset` tier is `ou`, the only `ou`-onset word a decodable
    story holds is `outside`, and no two of that story's other words are within
    two letters of seven — so every triple the closed list admits is one
    `check_tap_word` must reject, and offering the sound could only ever cost the
    child the whole phonics screen.

    Exhaustive over the closed list, which is small by construction
    (`MAX_OFFERED_WORDS` each side). Every constraint applied here is one
    `check_tap_word` applies, so a triple this returns is a triple that passes —
    which is what lets the same function serve as the tier test (8.53), the menu
    filter, and the fallback proposer (8.54) without three definitions of legal.
    The one constraint it cannot see is whether `PROMPT_FRAMES` can spell a
    readable sentence for the cue; that is per-stage rather than per-triple, so
    `sound_targets` checks it alongside.

    "Best" is the least guessable: smallest spread in length first, then the
    shortest words, then alphabetical so the choice is reproducible.
    """
    best: tuple[int, int, list[str]] | None = None
    for correct in target.onset_words:
        pool = [w for w in target.clean_words if w != correct]
        for pair in itertools.combinations(pool, 2):
            options = [correct, *pair]
            if len(set(options)) != len(options):
                continue
            if not _length_balanced(options, single_word=True):
                continue
            lengths = [len(o) for o in options]
            key = (max(lengths) - min(lengths), sum(lengths), options)
            if best is None or key < best:
                best = key
    return list(best[2]) if best is not None else None


def _offer(target: SoundTarget) -> SoundTarget:
    """The target as the MODEL will see it — option lists cut to `MAX_OFFERED_WORDS`.

    The cut used to be the last thing `sound_targets` did, and it could remove
    exactly the words that made the sound answerable: `clean_words` arrives in
    reading order, so whether a legal partner for a seven-letter onset word
    survived the first eight was luck. The proven triple therefore floats to the
    front, and the rest of each list keeps reading order behind it — a list whose
    first entries are a legal triple still contains a legal triple after any
    truncation, so the answerability this module promises survives the cut.

    Nothing about that order is visible to a child. Both lists are prompt-side
    only, every word in `onset_words` is a legal correct answer and every word in
    `clean_words` is a legal distractor, so promoting one carries no signal.
    """
    options = tap_word_options(target)
    onset, clean = list(target.onset_words), list(target.clean_words)
    if options is not None:
        correct, *others = options
        onset = [correct, *(w for w in onset if w != correct)]
        clean = [*others, *(w for w in clean if w not in others)]
    return target.model_copy(
        update={
            "onset_words": onset[:MAX_OFFERED_WORDS],
            "clean_words": clean[:MAX_OFFERED_WORDS],
        }
    )


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
    answer" cannot be guaranteed.

    **A sound is a GPC, not a grapheme** (DEVIATIONS 11.110). Grouping by the
    first LETTER put `can` (`c|K`) and `city` (`c|S`) in one target, and the
    live consequence was question 11294: *"the same sound as can"* over
    `['city','in','had']`, where no option starts with /k/ and a child applying
    phonics correctly cannot answer. The candidate cue is required to share the
    target's whole first GPC id for the same reason — the cue is the only sound
    the child is given, so it is what states the question's proposition.

    **A word is excluded only when the VALIDATOR cannot sound it out at this
    stage** (DEVIATIONS 11.112). This used to drop every sight and story-bank
    word on the grounds that the child had not sounded them out — but `out` is a
    stage-7 sight word and `ou|AW` is the GPC stage 7 introduces, so the
    validator decodes it at that very stage, and `mastery.word_gpc_ids` (the
    function that actually awards the credit) passes no sight set at all. Over
    the servable pool that exclusion was refusing 3,555 of 4,681 sight/story
    token occurrences the aligner decodes at the story's own stage — 76% of them
    — and stage 7's own new sound with them. The surviving half of the old rule
    is the whole of `word_gpcs` returning `None`: a sight word the child's stage
    genuinely cannot spell is still refused, which is the case the sentence was
    about. §0 is untouched — this narrows nothing the gate judges.

    "Best" is §8's evidence rule, not recency alone — see `_prefer_current_stage`.

    **Qualifying now means answerable, not merely non-degenerate** (DEVIATIONS
    8.53). A sound survives only if `tap_word_options` can compose a legal triple
    from its closed list AND `tap_word_prompt` can spell a readable sentence for
    its cue. Both were previously left to `check_tap_word` to discover per
    attempt, which is the wrong place: by then the sound is already the only thing
    on the menu and the retry budget is being spent proving it impossible. This is
    pure narrowing — the same move `_cue_and_offer` and `_prefer_current_stage`
    make — so nothing `check_tap_word` proves is affected.
    """
    mastered = mastered_ids(stage_id)

    # Insertion-ordered, so the offered lists follow reading order.
    gpcs_by_word: dict[str, list[str]] = {}
    for token in tokenize(story_text):
        if not token.is_word or token.text in gpcs_by_word:
            continue
        found = word_gpcs(token.text, mastered)
        # `None` is the only exclusion, and it already covers the sight and
        # story-bank words this stage cannot sound out (11.112). A word the
        # validator DOES align at the story's own stage is evidence, exactly as
        # `mastery.word_gpc_ids` treats it when the credit is awarded.
        if found:
            gpcs_by_word[token.text] = found

    # Keyed per GPC ID, never `min()` over a grapheme (11.110). `c|K` is stage 1
    # and `c|S` is stage 8, so keying by the letter dated soft `c` to stage 1 and
    # put the only stage-8 onset those stories carry permanently out of reach of
    # the `current_stage_onset` tier. GPC ids are unique across the inventory.
    stage_of = {gpc.id: gpc.stage for gpc in load_gpcs() if gpc.stage <= stage_id}

    cues = _cue_index(allowed_words, mastered, exclude=set(gpcs_by_word))

    targets: list[SoundTarget] = []
    for gpc_id in sorted({ids[0] for ids in gpcs_by_word.values()}):
        grapheme, phoneme = gpc_grapheme(gpc_id), gpc_phoneme(gpc_id)
        onset = [w for w, ids in gpcs_by_word.items() if ids[0] == gpc_id]
        # A distractor must be wrong BOTH ways: it may not reuse the target's
        # letters anywhere (what `check_tap_word` proves about the answer key)
        # and it may not start with the target's sound (what the child is asked).
        # `kid` shares no letter with `c` and starts with /k/ — a child who taps
        # it has done exactly as instructed and is marked wrong.
        clean = [
            w
            for w, ids in gpcs_by_word.items()
            if grapheme not in {gpc_grapheme(i) for i in ids}
            and gpc_phoneme(ids[0]) != phoneme
        ]
        if not onset or len(clean) < TAP_WORD_OPTIONS - 1:
            continue
        # 7.14: the cue is proved against EVERY word this sound may still offer,
        # and any word it would give away leaves the offer list. Later steps
        # (`_prefer_current_stage`, the `MAX_OFFERED_WORDS` cut) only narrow
        # further, so a cue proved here stays proved.
        #
        # 11.110: and the candidates are first narrowed to words that genuinely
        # start with THIS GPC. `_cue_index` is keyed by grapheme and stays that
        # way — `warm.repair_leaky_cues` reads it through `cue_candidates` — so
        # the sound half of the key is applied here.
        candidates = [c for c in cues.get(grapheme, ()) if onset_gpc(c, mastered) == gpc_id]
        cue, onset, clean = _cue_and_offer(candidates, onset, clean)
        if cue is None:
            continue
        targets.append(
            SoundTarget(
                grapheme=grapheme,
                cue=cue,
                onset_words=onset,
                clean_words=clean,
                stage=stage_of.get(gpc_id, 0),
            )
        )

    # Most recently taught sound first — that is the one worth practising —
    # with an alphabetical tiebreak so the list is reproducible. The cue is part
    # of the tiebreak because two sounds can now share a grapheme (11.110), and
    # the cue is the only thing that tells them apart on the page.
    targets.sort(key=lambda t: (-t.stage, t.grapheme, t.cue))

    # Memoised per cue: `tap_word_prompt` walks `PROMPT_FRAMES` against the gate,
    # and the same cue is re-tested once per tier otherwise.
    frames: dict[str, bool] = {}

    def askable(target: SoundTarget) -> bool:
        if target.cue not in frames:
            frames[target.cue] = (
                tap_word_prompt(target.cue, stage_id, sight_words, story_words) is not None
            )
        return frames[target.cue]

    return _prefer_current_stage(targets, stage_id, askable=askable)[:limit]


def _prefer_current_stage(
    targets: Sequence[SoundTarget],
    stage_id: int,
    askable: object = None,
) -> list[SoundTarget]:
    """§8: prefer a question that practises a sound taught AT this stage.

    Three tiers, best first (see the module docstring). The tiers NARROW the
    closed list rather than adding anything to it, so every guarantee
    `check_tap_word` proves is untouched — a narrower menu cannot make a wrong
    question right.

    **A tier fires when it holds an ANSWERABLE sound, not merely a sound**
    (DEVIATIONS 8.53). "First non-empty tier wins" is what cost six live stories
    their whole question screen: stage 8's `current_stage_onset` tier is one sound
    (`ou`) with one onset word (`outside`) whose length no other word in the story
    comes within two letters of, so the tier was non-empty, fired, and offered a
    menu on which every possible answer is one `check_tap_word` must reject.
    Falling through to `current_stage_word` costs §8 nothing — that tier's
    definition is that the correct word uses a sound this stage teaches — and the
    tier ORDER is unchanged, so a story that could earn onset-tier evidence still
    does. Measured over the live pool: stage 8 goes from 4 of 8 answerable stories
    to 8 of 8, and stage-evidence-earning stories rise from 86 to 90 of 113.

    The last-resort tier is deliberate. A stage whose story simply contains no
    current-stage sound still gets a question about the newest sound available;
    dropping the only phonics question would cost the child the whole screen and
    §8 nothing it did not already lack. Returning nothing at all is reserved for
    the story where no sound anywhere is answerable — and that is an honest empty,
    not a silent one: the caller stops asking for a `tap_word`, says so in
    `dropped`, and spends the retry budget on the question it can still get.
    """
    onset_tier = [
        t.model_copy(update={"scope": "current_stage_onset"})
        for t in targets
        if t.stage == stage_id
    ]

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

    old_tier = [t.model_copy(update={"scope": "older_stage_fallback"}) for t in targets]

    for tier in (onset_tier, word_tier, old_tier):
        offered = [_offer(t) for t in tier]
        answerable = [
            t
            for t in offered
            if tap_word_options(t) is not None and (askable is None or askable(t))
        ]
        if answerable:
            return answerable
    return []


def _cue_index(
    allowed_words: Sequence[str],
    mastered: frozenset[str],
    exclude: set[str],
    limit: int = MAX_CUE_CANDIDATES,
) -> dict[str, list[str]]:
    """grapheme -> allowed words starting with it, shortest and commonest first.

    `allowed_words` arrives frequency-ordered from the precompute table, so rank
    is just the index. Shortest first because a cue is read aloud and compared
    by ear: "Tap: hat." is a cleaner model of /h/ than "Tap: having.", which is
    what pure frequency picked live. Words already in the story are excluded so
    the cue can never also be one of the options.

    A RANKED LIST rather than one word (DEVIATIONS 7.14): the best cue for a
    sound may be the one that gives an option away, and in the live pool it
    repeatedly was — `the` is the shortest, commonest stage-3 word starting with
    `th`, and seven warm stories offering the option `then` all took it. The
    caller picks the first candidate that is safe for its own word list.

    The dict is keyed by GRAPHEME and that is deliberate — `warm.repair_leaky_
    cues` reads it through `cue_candidates` and holds a grapheme, not a GPC id.
    The `limit` is applied per SOUND before the merge, though (DEVIATIONS
    11.110): one grapheme can spell two sounds, `sound_targets` then keeps only
    the candidates whose whole first GPC matches its target, and truncating the
    merged list first would cut the minority sound away entirely. Stage 8's `c`
    is the live case — its 24 shortest, commonest words are all `c|K`, so the
    soft-`c` question had no cue left to be re-asked with. The merged list is a
    SUPERSET ordered by the same comparator, so the first `limit` entries are
    exactly what this returned before and no existing caller's choice moves.
    """
    ranked: dict[str, list[tuple[int, int, str]]] = {}
    for rank, raw in enumerate(allowed_words):
        word = raw.strip().lower()
        if not word or word in exclude:
            continue
        found = word_gpcs(word, mastered)
        if not found:
            continue
        ranked.setdefault(found[0], []).append((len(word), rank, word))

    merged: dict[str, list[tuple[int, int, str]]] = {}
    for gpc_id, keys in ranked.items():
        merged.setdefault(gpc_grapheme(gpc_id), []).extend(sorted(keys)[:limit])
    return {grapheme: [key[2] for key in sorted(keys)] for grapheme, keys in merged.items()}


def tap_word_prompt(
    cue: str,
    stage_id: int,
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
) -> str | None:
    """The richest frame that clears the gate at this stage — or None.

    Deterministic: the same (cue, stage) always yields the same sentence.

    The frame is validated against `INSTRUCTION_WORDS` alongside the stage's own
    sight words (DEVIATIONS 11.60). That is a narrowing of what the gate JUDGES,
    not a lowering of the gate: the cue is not in the lexicon, so this still
    returns `None` for a cue the child cannot decode at this stage — which is the
    answer `sound_targets` filters its menu on and `check_tap_word` refuses on.
    """
    exempt = [*sight_words, *INSTRUCTION_WORDS]
    for frame in PROMPT_FRAMES:
        text = frame.format(cue=cue)
        if passes_gate(readable(text, stage_id, exempt, story_words)):
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
    keep_answers: tuple[str, str] | None = None,
) -> str:
    """Per-request half of the prompt (after the cached prefix).

    On a retry this carries the SPECIFIC reason each question was rejected, the
    same way §6's rewrite node carries the violating words — "try again" tells
    the model nothing it can act on.

    `keep_answers` narrows a retry to the one thing that was wrong (DEVIATIONS
    11.68). An `mcq2` has to satisfy eight constraints at once — the true answer
    a subsequence of the story in its own order, the false answer not one but
    still built from the story's words, equal word counts, a length ratio, a
    readable opener, no leading Yes/No, no `not`-negation, and no word that gives
    the answer away — and measured live, the commonest failure by far is the LAST
    one alone, with a perfectly good answer pair attached. Asking for a whole new
    question in that case throws away the seven constraints already satisfied and
    re-rolls all eight; asking only for a new question keeps them.
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
        block = (
            "Your two-choice question MUST begin with one of these words — every "
            f"other question word is unreadable at this stage: {', '.join(openers)}."
        )
        # DEVIATIONS 11.68. Which openers exist is a fact about the STAGE, and the
        # first version of the "ask about what they share" rule was taught with a
        # `What did ...` example — a shape stages 1-3 cannot write, because their
        # only readable openers are `Did` and `Can`. So the model had no in-
        # inventory model of a good question at exactly the stages where the leak
        # was worst, and it wrote the yes/no question that restates one answer.
        # Measured: `mcq2` survival at stage 3 was 1 of 8 before this paragraph
        # existed. Naming the constraint is the same move `readable_openers` makes
        # for the openers themselves — tell the model what it CAN write, not only
        # what it cannot.
        if not {o.lower() for o in openers} & {"what", "where", "why", "how", "who"}:
            block += (
                " None of them can ask WHAT or WHERE something happened, so ask a "
                "yes-or-no question about the part BOTH answers agree on and keep "
                "it short — see the 'Good with only yes-or-no opening words' "
                "example. Do not name the thing that differs."
            )
        parts.append(block)
    if keep_answers is not None:
        parts.append(
            "Your two answers were ACCEPTED. Send these two back exactly as they are, "
            "word for word, and change nothing about them:\n"
            f"- true answer: {keep_answers[0]}\n"
            f"- false answer: {keep_answers[1]}\n"
            "Only the question was wrong. Write a different question about what these "
            "two answers AGREE on, using none of the words that differ between them."
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
    mastered = mastered_ids(stage_id)
    matching = [t for t in targets if t.grapheme == grapheme]
    if not matching:
        return None, [
            f'"{draft.target_grapheme}" is not one of the sounds offered; pick one from the list'
        ]
    # One grapheme can now name two offered sounds (11.110): `c` is `c|K` in `can`
    # and `c|S` in `city`, and the model names the target by its LETTERS. Resolve
    # by the sound the claimed answer actually starts with, and fall back to the
    # first — a wrong resolution can only cost the attempt, because every proof
    # below still has to pass against whichever target is chosen.
    target = matching[0]
    if len(matching) > 1:
        claimed = [t.text for t in tokenize(draft.correct_word) if t.is_word]
        want = onset_gpc(claimed[0], mastered) if len(claimed) == 1 else None
        target = next((t for t in matching if onset_gpc(t.cue, mastered) == want), matching[0])

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
    # DEVIATIONS 7.14. `sound_targets` already picked a cue that is safe for
    # this sound's whole closed offer list, so this fires only when the model
    # reached outside that list — which is exactly when a menu stops being a
    # guarantee. The cue is fixed per sound, so the actionable instruction is to
    # ask about a different sound, not to reword the hint.
    for why in cue_leaks(target.cue, options):
        problems.append(
            f"{why}, so the hint points at an option by its letters instead of its "
            f"sound — ask about a different sound"
        )

    correct = options[0]
    in_story = set(story_word_tokens(story_text))

    # 11.110: the SENTENCE says "starts with the same sound as {cue}", so the cue
    # is what states the proposition and the cue is what it is proved against.
    # Derived here rather than carried on the target, so a `SoundTarget` built
    # anywhere — including in a test — is subject to it.
    cue_sound = onset_phoneme(target.cue, mastered)
    correct_sound = onset_phoneme(correct, mastered)

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
            # The claim the child is asked to check. Live row 11294 satisfied the
            # letters clause above and failed this one: cue `can` (`c|K`, /k/),
            # answer `city` (`c|S`, /s/), so the question it asked had NO correct
            # answer and a 1-in-3 guess was credited as mastery evidence.
            if cue_sound is None:
                problems.append(
                    f'"{target.cue}" cannot be sounded out at this stage, so it cannot '
                    f"name a sound — ask about a different sound"
                )
            elif correct_sound != cue_sound:
                problems.append(
                    f'"{correct}" does not start with the same sound as "{target.cue}" '
                    f"({correct_sound or '?'} against {cue_sound}) — the question asks "
                    f"about a sound, not about a letter"
                )
        elif grapheme in graphemes:
            # Two options carrying the sound means two defensible answers.
            problems.append(f'"{option}" also uses {grapheme}, so it is not a wrong answer')
        elif correct_sound is not None and onset_phoneme(option, mastered) == correct_sound:
            # The mirror of the clause above, and the one the grapheme rule
            # cannot see (11.110): `kid` reuses no letter of `c` and still starts
            # with /k/. A child who taps it has done exactly what the sentence
            # asked and is marked WRONG, with `gpc_stats` debited for it.
            problems.append(
                f'"{option}" also starts with the sound {correct_sound}, so it is a '
                f'defensible answer to "the same sound as {target.cue}"'
            )

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


def check_mcq2_answers(
    correct: str,
    incorrect: str,
    *,
    stage_id: int,
    story_text: str,
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
) -> list[str]:
    """Everything provable about the ANSWER PAIR alone, without the question.

    Split out so it has exactly one definition and two callers (DEVIATIONS
    11.68): `check_mcq2` runs it first, and `generate_questions` runs it on a
    refused draft to learn whether the pair was salvageable. An `mcq2` must
    satisfy eight constraints at once and the commonest live failure is the
    question leaking the answer while the pair is perfectly good — re-rolling all
    eight to fix one is what was costing the comprehension question.
    """
    problems: list[str] = []
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

    if negation_pair(correct, incorrect):
        problems.append(
            'the false answer is only the true answer with "not" added or removed, so the '
            "story still reads as saying it — change WHAT happened, not whether it happened"
        )

    if not _length_balanced([correct, incorrect], single_word=False):
        problems.append("the two answers must be close to the same length")
    return problems


def check_mcq2(
    draft: Mcq2Draft,
    *,
    stage_id: int,
    story_text: str,
    sight_words: Iterable[str] = (),
    story_words: Iterable[str] = (),
    seed: int = 0,
) -> tuple[GeneratedQuestion | None, list[str]]:
    prompt = _norm(draft.prompt)
    correct = _norm(draft.correct)
    incorrect = _norm(draft.incorrect)

    problems = check_mcq2_answers(
        correct,
        incorrect,
        stage_id=stage_id,
        story_text=story_text,
        sight_words=sight_words,
        story_words=story_words,
    )

    if not prompt.endswith("?"):
        problems.append("the question must end with a question mark")
    report = readable(prompt, stage_id, sight_words, story_words)
    if report.word_count == 0:
        # The answer pair has had this guard since 11.68 (`the {label} is empty`)
        # and the question never did. `"?"` ends with a question mark and used to
        # clear `passes_gate` on an empty denominator, so a question with no words
        # in it was judged decodable; now the gate refuses it and this is what
        # keeps the refusal from being reported as "uses words this child cannot
        # read: " with nothing after the colon.
        problems.append("the question is empty")
    elif not passes_gate(report):
        problems.append(
            f'the question "{prompt}" uses words this child cannot read: '
            + ", ".join(unreadable_words(report))
        )

    # DEVIATIONS 11.64. The question must not contain the word that tells the
    # child which card is right — 40 of 74 live rows did, which made a
    # comprehension question answerable by letter-matching with the story unread.
    # Same shape as `cue_leaks` for `tap_word`: the actionable instruction names
    # the offending word, because "rewrite it" tells the model nothing.
    sight, _ = _sets(sight_words, ())
    leaked = prompt_leaks_answer(prompt, correct, incorrect, sight)
    if leaked:
        problems.append(
            f"the question already contains {_quoted_words(leaked)}, which is what makes the "
            "two answers different — so the child can pick the matching answer without "
            "reading the story. Ask about what the two answers SHARE and leave the "
            "difference to the answers"
        )

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
        dropped["tap_word"] = [
            "no sound in this story can be asked about: none has one onset word and "
            "two clean words of a similar length that this stage can also spell a "
            "readable prompt for"
        ]

    total = Usage(model=fast_model())
    problems: list[str] = []
    attempts = 0
    # DEVIATIONS 11.68. An `mcq2` answer pair that satisfied every constraint but
    # whose question gave the answer away is carried into the next attempt, so the
    # retry re-rolls one constraint instead of eight. `check_mcq2` still judges
    # whatever comes back, in full — this changes what the model is ASKED for, not
    # what is accepted.
    keep_answers: tuple[str, str] | None = None

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
                keep_answers=keep_answers,
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
        keep_answers = None
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
                if qtype == "mcq2":
                    # Salvage the pair only if it is independently sound. Asked
                    # again rather than inferred from `why`: matching on the
                    # refusal strings would silently rot the moment one is reworded.
                    pair = (_norm(draft.correct), _norm(draft.incorrect))
                    if not check_mcq2_answers(
                        *pair,
                        stage_id=stage_id,
                        story_text=story_text,
                        sight_words=sight_words,
                        story_words=story_words,
                    ):
                        keep_answers = pair

    for qtype in needed:
        dropped.setdefault(qtype, problems or ["retry budget exhausted"])

    # DEVIATIONS 8.54. The retry budget is spent and the phonics question — the
    # only §8-bearing type — is still missing, while the closed list this module
    # composed provably admits a legal answer (`sound_targets` offers no sound for
    # which it does not). So compose the answer here and submit it to the SAME
    # judge: `check_tap_word`, unmodified, with the same cue rule, the same
    # per-option gate, the same "exactly one option can be correct" proof and the
    # same length rule. A draft that fails is discarded exactly as the model's is.
    #
    # The proposer is the only thing that changes, and this is the one type where
    # that is not a trust decision at all: `LAYER3_EXEMPT_QTYPES` already records
    # that no model writes a tap-a-word string — the prompt is a frame, and every
    # option is a story word that cleared the gate and §13 with the body. §13
    # layer 1 still reads every string of it in `_moderate_questions`.
    # Read BEFORE the fallback runs, and not inferred from `dropped` afterwards:
    # a model that failed attempt 1 and succeeded on attempt 2 leaves a `dropped`
    # entry behind, so "there are recorded problems" is not the same question as
    # "who wrote the question that survived".
    model_wrote_it = "tap_word" in accepted
    derived_problems = dropped.get("tap_word")
    derived: list[str] = []
    if "tap_word" in needed:
        for target in targets:
            options = tap_word_options(target)
            if options is None:  # pragma: no cover — `sound_targets` filters these
                continue
            question, why = check_tap_word(
                TapWordDraft(
                    target_grapheme=target.grapheme,
                    correct_word=options[0],
                    other_words=list(options[1:]),
                ),
                targets=targets,
                **common,
            )
            if question is not None:
                accepted["tap_word"] = question
                needed.remove("tap_word")
                derived.append("tap_word")
                break
            derived_problems = (derived_problems or []) + why

    # DEVIATIONS 11.67. §8's promotion rule needs ≥10 graded observations on the
    # GPCs the CHILD'S STAGE teaches, and one phonics question per story credits
    # 1.08 of them — so `MIN_GPC_OBSERVATIONS` silently doubled §8's own "5
    # stories" to nine. A second `tap_word` on a different sound brings it to 1.95
    # (5.1 stories), which makes the floor consistent with §8 rather than moving
    # either number.
    #
    # Nothing about guessability changes: still one of three options, still §8's
    # named type (`mastery.MASTERY_QTYPES`), still judged by the UNCHANGED
    # `check_tap_word`. Letting the 50%-guessable `mcq2` move a counter instead is
    # refused in 11.66 — that is DEVIATIONS 6.7's failure class, an unearned
    # signal advancing a child's stage.
    #
    # Zero tokens and zero new prompt surface: `sound_targets` already returns up
    # to `MAX_SOUND_TARGETS` ranked targets, every one of which `tap_word_options`
    # can provably answer, and until now all but the first were discarded.
    extra: list[GeneratedQuestion] = []
    first = accepted.get("tap_word")
    if first is not None and stage_evidence(first, stage_id) < EVIDENCE_PER_STORY_TARGET:
        # Guards against counting one word's GPCs twice, and against re-showing a
        # word the child just judged: the second question's answer must be a new
        # word that appeared nowhere in the first question's options.
        used_graphemes = {first.answer.get("target_grapheme")}
        used_words = set(first.payload.get("options", ())) | {first.answer.get("correct")}
        for target in targets:
            if len(extra) >= MAX_TAP_WORDS - 1:
                break
            if target.grapheme in used_graphemes:
                continue
            options = tap_word_options(target)
            if options is None or options[0] in used_words:
                continue
            question, _why = check_tap_word(
                TapWordDraft(
                    target_grapheme=target.grapheme,
                    correct_word=options[0],
                    other_words=list(options[1:]),
                ),
                targets=targets,
                **common,
            )
            if question is None:
                continue
            extra.append(question)
            used_graphemes.add(target.grapheme)
            used_words |= set(question.payload["options"])

    # Stable order: the phonics questions first, comprehension last (§11.3).
    questions = [
        *([first] if first is not None else []),
        *extra,
        *([accepted["mcq2"]] if "mcq2" in accepted else []),
    ]
    for idx, question in enumerate(questions):
        question.idx = idx

    return QuestionSet(
        questions=questions,
        usage=total,
        attempts=attempts,
        dropped={k: v for k, v in dropped.items() if k not in accepted},
        derived=derived,
        extra_tap_words=[
            describe_target(q, targets, stage_id, source="derived") for q in extra
        ],
        tap_word_target=describe_target(
            accepted.get("tap_word"),
            targets,
            stage_id,
            source="model" if model_wrote_it else "derived",
            model_dropped=derived_problems,
        ),
    )


def describe_target(
    question: GeneratedQuestion | None,
    targets: Sequence[SoundTarget],
    stage_id: int,
    source: str | None = "model",
    model_dropped: Sequence[str] | None = None,
) -> dict:
    """What the tap-a-word question ended up practising (§8/§14).

    Reported for every outcome, including the ones with no question, because
    "this story could offer nothing at this stage" is the measurement that tells
    an operator the allowlist — not the node — is the problem.

    `source` distinguishes the model's own choice from the one this module
    composed after the retries were spent (DEVIATIONS 8.54), and `model_dropped`
    keeps the reasons the model's attempts were refused even when the question
    was recovered. Both are persisted into `gen_meta.enrichment`, so "how often
    does the model need catching" is a query rather than a re-run — a rising
    `derived` rate at some stage is a finding about the PROMPT, not about the
    fallback, and would be invisible if the recovery overwrote the evidence.
    """
    if question is None:
        return {
            "scope": "none_available" if not targets else "dropped",
            "offered": len(targets),
            "stage_id": stage_id,
            # No question survived, so nothing wrote one. `None` rather than
            # `"derived"`: the fallback ran and also failed, which is a different
            # fact from "the fallback was never reached".
            "source": None,
            "model_dropped": list(model_dropped or []),
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
        "source": source,
        "model_dropped": list(model_dropped or []),
    }
