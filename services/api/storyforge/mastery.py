"""Grading and stage promotion (§8) — the mastery signal, as a pure function.

§8's whole instruction about this module is one sentence: *"Interpretable by
design — no BKT."* So there is no latent knowledge state, no decay curve, no
tuned weights. There are two integers per grapheme-phoneme correspondence and
three published thresholds, and a parent can recompute every promotion decision
on paper from what the dashboard already shows them.

Pure, in the same sense as ``storyforge.decoder``: no database, no clock, no
framework. ``api/readings.py`` reads rows, calls in here, and writes rows back.
That split is what makes the interesting parts unit-testable — a promotion test
should not need Postgres, and a grading test should not need HTTP.

Three properties this module exists to hold.

**Grading is server-side and total.** The answer key never leaves the server
(`api/stories.py` withholds it from the StoryDTO deliberately), so
:func:`grade_answer` is the only thing that knows the truth. It takes ``object``
rather than a typed response and is total over every Python value — a hostile
client that posts a dict, a nested list, a negative index or a bool must get a
verdict or a 4xx, never a 500. It returns ``None`` for *"this key cannot be
graded"*, which is a different thing from *"the child was wrong"* and must never
be collapsed into one.

**Evidence comes from the decoder, never from a guess.** A word's GPCs are
``validate_word(word, mastered_ids(stage)).alignment`` — the exact edges the
aligner used to sound the word out. Nothing here re-implements spelling.

**Only tap-a-word questions move the counters.** §8's rule is "GPC instances in
words the kid answered questions about (tap-word correct → +1 correct for that
word's GPCs)". An `mcq2` is a *comprehension* question about the story; getting
it right is evidence the child understood the plot and no evidence at all that
they can decode ``sh``. Crediting every grapheme in a two-sentence option would
swamp the honest signal with dozens of free correct marks per reading and make
80% trivially reachable without decoding anything. ``MASTERY_QTYPES`` names the
types that count, so widening it is a visible decision.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from storyforge.decoder import WordVerdict, mastered_ids, validate_word
from storyforge.decoder.tokenizer import tokenize

# --- thresholds (§8: "thresholds in config, shown on dashboard") -------------
# Named constants, surfaced verbatim by GET /api/kids/{id}/progress so the
# dashboard shows the rule rather than just its verdict.

#: §8: "≥5 stories completed at current stage".
STORIES_REQUIRED_AT_STAGE = 5
#: §8: "aggregate accuracy over current-stage GPCs ≥ 80%".
ACCURACY_THRESHOLD = 0.80
#: Additive to §8, and the conservative direction. A ratio over two observations
#: is not an accuracy, it is a coin flip: one lucky tap on a two-GPC word would
#: read as 100% and promote a child who has demonstrated nothing. §8 gives no
#: floor, so promotion would otherwise be decidable from a single answer.
MIN_GPC_OBSERVATIONS = 10
#: Appendix A's scope & sequence. Stage 12 is the end of the ladder.
MIN_STAGE = 1
MAX_STAGE = 12

#: The question types that carry evidence about grapheme-phoneme knowledge.
MASTERY_QTYPES = ("tap_word",)


@dataclass(frozen=True)
class Thresholds:
    """The promotion rule, as data — so the dashboard can print it (§8)."""

    stories_required: int = STORIES_REQUIRED_AT_STAGE
    accuracy_threshold: float = ACCURACY_THRESHOLD
    min_observations: int = MIN_GPC_OBSERVATIONS
    max_stage: int = MAX_STAGE


THRESHOLDS = Thresholds()


# --- grading -----------------------------------------------------------------
def _norm(value: str) -> str:
    """Compare answers the way a child's tap means them: text, not whitespace."""
    return " ".join(value.split()).casefold()


def _options(payload: object) -> list[str]:
    """The offered options, or an empty list. Never raises."""
    if isinstance(payload, dict) and isinstance(payload.get("options"), list):
        return [o for o in payload["options"] if isinstance(o, str)]
    return []


def answer_truth(payload: object, answer: object) -> str | None:
    """The one correct option text, or ``None`` when the key cannot be trusted.

    A stored key is two redundant claims — ``{"correct": "fast", "index": 0}``.
    Redundancy is only useful if disagreement is treated as a fault: if the text
    and the index name different options the key is corrupt, and guessing which
    half to believe would silently grade a child against the wrong word. It
    returns ``None`` and the caller refuses the answer instead.
    """
    key = answer if isinstance(answer, dict) else {}
    options = _options(payload)

    raw_text = key.get("correct")
    by_text = raw_text if isinstance(raw_text, str) and raw_text.strip() else None

    raw_index = key.get("index")
    by_index: str | None = None
    # `bool` is an `int` in Python; `{"index": true}` is a corrupt key, not 1.
    if isinstance(raw_index, int) and not isinstance(raw_index, bool):
        if 0 <= raw_index < len(options):
            by_index = options[raw_index]

    if by_text is not None and by_index is not None and _norm(by_text) != _norm(by_index):
        return None  # internally inconsistent — refuse to pick a winner

    truth = by_text if by_text is not None else by_index
    if truth is None:
        return None
    # A key naming a word that was never offered makes the question unanswerable,
    # so it is a bad key rather than a wrong child.
    if options and _norm(truth) not in {_norm(o) for o in options}:
        return None
    return truth


def grade_answer(*, response: object, payload: object, answer: object) -> bool | None:
    """Grade one response. ``None`` means the stored key is unusable.

    Total over every Python value of ``response``:

    ``str``   compared to the correct option after whitespace/case normalisation.
    ``int``   treated as a 0-based index into ``payload["options"]``; out of
              range is simply wrong, not an error.
    ``bool``  never an index (``True`` is not "option 1") — always wrong.
    anything else, including ``None``, dicts and lists — always wrong.

    The generous reading would be to accept ``"0"`` as an index too. It is not
    accepted: the options are words, so a numeric string is genuinely ambiguous,
    and quietly widening what counts as correct is exactly how a mastery signal
    stops meaning anything.
    """
    truth = answer_truth(payload, answer)
    if truth is None:
        return None

    if isinstance(response, bool):
        return False
    if isinstance(response, int):
        options = _options(payload)
        if 0 <= response < len(options):
            return _norm(options[response]) == _norm(truth)
        return False
    if isinstance(response, str):
        return _norm(response) == _norm(truth)
    return False


# --- evidence ----------------------------------------------------------------
def single_word(raw: object) -> str | None:
    """The one word in ``raw``, tokenised the way the decoder tokenises text.

    ``None`` for anything that is not exactly one word — a tap-a-word key that
    holds a phrase is a bug, and attributing GPCs to a phrase would be worse.
    """
    if not isinstance(raw, str):
        return None
    words = [t.text for t in tokenize(raw) if t.is_word]
    return words[0] if len(words) == 1 else None


def word_gpc_ids(word: str, stage_id: int) -> list[str]:
    """The GPC instances the decoder used to sound ``word`` out at ``stage_id``.

    In alignment order, **with repeats** — §8 counts GPC *instances*, so "dad"
    contributes ``d|D`` twice. An undecodable word yields ``[]`` and therefore
    contributes no evidence at all, which is the honest answer: a sight word was
    recognised, not decoded.

    ``stage_id`` is the *story's* stage, not the kid's. The story is proved
    decodable at its own stage, so aligning there attributes credit to the
    correspondences the text actually required. Aligning at a higher stage would
    let the aligner reach for a later-taught GPC and credit the child for a
    correspondence the word never needed — and would make the same story yield
    different evidence for two different children.
    """
    report = validate_word(word, mastered_ids(stage_id))
    if report.verdict is not WordVerdict.DECODABLE:
        return []
    return list(report.alignment)


@dataclass(frozen=True)
class WordEvidence:
    """One graded word: what was asked about, where it was proved, how it went."""

    word: str
    stage_id: int
    correct: bool


@dataclass(frozen=True)
class GpcDelta:
    """What to add to one ``gpc_stats`` row."""

    gpc_id: str
    n_seen: int
    n_correct: int


def gpc_increments(evidence: Iterable[WordEvidence]) -> list[GpcDelta]:
    """Fold graded words into per-GPC counter deltas (§8).

    Every GPC instance in the word is *seen*; it is *correct* only if the child
    got that word right. Deterministically ordered by GPC id so two runs over
    the same reading produce byte-identical updates.
    """
    seen: dict[str, int] = {}
    correct: dict[str, int] = {}
    for item in evidence:
        for gpc_id in word_gpc_ids(item.word, item.stage_id):
            seen[gpc_id] = seen.get(gpc_id, 0) + 1
            if item.correct:
                correct[gpc_id] = correct.get(gpc_id, 0) + 1
    return [
        GpcDelta(gpc_id=gpc_id, n_seen=seen[gpc_id], n_correct=correct.get(gpc_id, 0))
        for gpc_id in sorted(seen)
    ]


# --- promotion ---------------------------------------------------------------
@dataclass(frozen=True)
class Criterion:
    """One clause of §8's rule, with the numbers that decided it.

    The dashboard renders these directly. "Not yet" is useless to a parent;
    "3 of 5 stories at stage 2" tells them what happens next.
    """

    name: str
    met: bool
    detail: str


@dataclass(frozen=True)
class PromotionDecision:
    promote: bool
    from_stage: int
    to_stage: int | None
    stories_completed: int
    stories_required: int
    gpc_seen: int
    gpc_correct: int
    #: ``None`` when nothing has been observed — distinct from 0.0, which means
    #: observed and wrong every time.
    accuracy: float | None
    accuracy_threshold: float
    min_observations: int
    criteria: list[Criterion] = field(default_factory=list)


def accuracy_of(n_seen: int, n_correct: int) -> float | None:
    """``n_correct / n_seen``, or ``None`` when there is no evidence."""
    if n_seen <= 0:
        return None
    return n_correct / n_seen


def evaluate_promotion(
    *,
    stage_id: int,
    stories_completed: int,
    gpc_seen: int,
    gpc_correct: int,
    thresholds: Thresholds = THRESHOLDS,
) -> PromotionDecision:
    """§8's promotion rule, and nothing else.

    Promote when **all** of:

    1. ``stories_completed`` at the current stage ≥ 5;
    2. at least ``MIN_GPC_OBSERVATIONS`` graded GPC instances on the GPCs this
       stage teaches;
    3. accuracy over those ≥ 80%;
    4. there is a stage above this one.

    ``gpc_seen``/``gpc_correct`` are aggregated over the GPCs **introduced at**
    ``stage_id``, not the cumulative set a stage-N reader has been taught.
    Promotion has to mean "learned what this stage teaches"; a cumulative
    denominator is dominated by stage-1 correspondences the child mastered
    months earlier, so a reader who never once met the new graphemes would still
    show 95% and advance. The strict reading costs a real thing — it can stall a
    child whose questions never happen to target a new grapheme — which is why
    criterion 2 reports the shortfall by name instead of hiding behind "not yet".
    """
    accuracy = accuracy_of(gpc_seen, gpc_correct)
    at_top = stage_id >= thresholds.max_stage

    criteria = [
        Criterion(
            name="stories_completed",
            met=stories_completed >= thresholds.stories_required,
            detail=(
                f"{stories_completed} of {thresholds.stories_required} stories "
                f"completed at stage {stage_id}"
            ),
        ),
        Criterion(
            name="evidence",
            met=gpc_seen >= thresholds.min_observations,
            detail=(
                f"{gpc_seen} of {thresholds.min_observations} answered GPC "
                f"instances needed on stage-{stage_id} sounds"
            ),
        ),
        Criterion(
            name="accuracy",
            met=accuracy is not None and accuracy >= thresholds.accuracy_threshold,
            detail=(
                f"{gpc_correct}/{gpc_seen} = {accuracy:.0%} of stage-{stage_id} sounds "
                f"correct (need {thresholds.accuracy_threshold:.0%})"
                if accuracy is not None
                else f"no stage-{stage_id} sounds answered yet"
            ),
        ),
        Criterion(
            name="stage_available",
            met=not at_top,
            detail=(
                f"stage {thresholds.max_stage} is the last stage in the scope & sequence"
                if at_top
                else f"stage {stage_id + 1} is next"
            ),
        ),
    ]

    return PromotionDecision(
        promote=all(c.met for c in criteria),
        from_stage=stage_id,
        to_stage=None if at_top else stage_id + 1,
        stories_completed=stories_completed,
        stories_required=thresholds.stories_required,
        gpc_seen=gpc_seen,
        gpc_correct=gpc_correct,
        accuracy=accuracy,
        accuracy_threshold=thresholds.accuracy_threshold,
        min_observations=thresholds.min_observations,
        criteria=criteria,
    )
