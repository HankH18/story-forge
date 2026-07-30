"""The narrative measurement, and the guards that keep it from becoming a gate.

`docs/DEVIATIONS.md` 11.40-11.59. The defect these tests exist because of: a
stage-6 story that reads like a vocabulary drill cleared every gate the product
had, and the only thing that noticed was a person reading the live app.

Three things are pinned here:

1. **The pure signals**, which are diagnostics and NOT a narrative score — the
   docstring in `llm/narrative.py` carries the measurement that says so, and the
   test at the bottom of this file asserts there is no composite field to mistake
   for one.
2. **The judge never gates.** `run_pipeline` computes `passed` before the judge is
   asked, and a story with the worst possible verdict still passes. §18's register
   point is correct at the bottom of the ladder and a hard narrative gate would
   reject legitimately spare stage-1 stories.
3. **The judge separates hand-labelled stories from hand-labelled word lists**,
   live, in both directions, over multiple runs — STATE invariant 5's discipline
   applied to a new model-judged check on the day it is introduced rather than
   after the fourth pass at it.
"""

from __future__ import annotations

import os
import statistics

import pytest

from storyforge.llm.narrative import (
    ADVERSATIVE_WORDS,
    INTENT_WORDS,
    NARRATIVE_SYSTEM,
    SCORE_MAX,
    SCORE_MIN,
    NarrativeResult,
    NarrativeSignals,
    NarrativeVerdict,
    late_novelty,
    narrative_signals,
    sentences,
    top_opener_share,
)

# The user's own example of the defect, verbatim from the report.
WORD_LIST = (
    "Dan sat in a camp. Dan can nap. A cat sat at Dan. "
    "Dan can pat the cat. The cat can nap. Dan did nap."
)

# Live pool story 7316 (stage 9, animals) — a real story, with a want, a setback
# and a resolution, in the same tiny register.
REAL_STORY = (
    "A rabbit lived in the forest. She was happy in her place. One day the rabbit "
    "went out. She wanted to play in the forest. She saw a bird in a tree. The "
    "rabbit and the bird played all day. Then it got dark. The rabbit did not know "
    "the way back. She said, I want my place. The bird said, I can help you. They "
    "went across the forest. Then the rabbit saw her tree. She ran to it fast. She "
    "was so happy."
)


# --- the pure signals ---------------------------------------------------------
def test_sentences_splits_and_drops_empties():
    assert sentences("A. B! C?  ") == ["A", "B", "C"]
    assert sentences("") == []
    assert sentences("   ...   ") == []


def test_top_opener_share_catches_a_repeated_frame():
    """The observed failure: the same two words open sentence after sentence.

    And the measured limit of this signal, pinned so nobody reads more into it than
    it says. The user's own worst example scores only **0.33** — the repeated
    opening is "Dan can", twice out of six sentences, because "Dan sat"/"Dan can"/
    "Dan did" are three different two-word openings even though every sentence
    starts with the same character. The live pool's MEDIAN is 0.286. So this number
    is a diagnostic that ranks the same text against itself before and after a
    change; it is not a classifier, which is DEVIATIONS 11.53's finding.
    """
    assert top_opener_share(WORD_LIST) == pytest.approx(2 / 6)
    assert top_opener_share(REAL_STORY) < top_opener_share(WORD_LIST)


def test_the_signals_never_divide_by_zero():
    """These run on a reporting path, and a ZeroDivision in a metric would take
    down a run that had already been paid for."""
    for text in ("", "   ", "One.", "One. Two."):
        s = narrative_signals(text)
        assert 0.0 <= s.top_opener_share <= 1.0
        assert 0.0 <= s.late_novelty <= 1.0


def test_late_novelty_needs_three_sentences_to_mean_anything():
    """Under three sentences there is no "final third" to compare, so the honest
    answer is 0.0 rather than a number computed from one sentence."""
    assert late_novelty("A cat sat. A cat ran.") == 0.0
    assert late_novelty(REAL_STORY) > 0.0


def test_intent_and_adversative_are_read_off_the_text():
    s = narrative_signals(REAL_STORY)
    assert s.has_intent  # "wanted", "want"
    assert s.has_adversative  # "did not"
    bare = narrative_signals("A cat sat. A cat ran. A cat sat.")
    assert not bare.has_intent
    assert not bare.has_adversative


def test_the_marker_lists_exclude_the_words_that_fire_on_everything():
    """"will" and "going" appear in flat routine sentences constantly ("Every day
    it went up high"), so a marker list containing them is true of every story and
    is therefore a signal of nothing."""
    for word in ("will", "would", "going", "went", "then", "so"):
        assert word not in INTENT_WORDS
    for word in ("then", "so", "and"):
        assert word not in ADVERSATIVE_WORDS


def test_there_is_no_composite_narrative_score_on_the_pure_signals():
    """DEVIATIONS 11.53: five free structural signals were computed over 19
    hand-labelled pool stories and NONE separated the labels — same-opening share
    0.363 vs 0.301, late novelty 0.274 vs 0.290, connective share 0.087 vs 0.103,
    repeated-bigram share 0.076 vs 0.075. A composite built out of those would be a
    number that looks like coverage and measures nothing, which is precisely the
    failure this module exists because of. The absence is the design."""
    fields = set(NarrativeSignals.model_fields)
    assert "score" not in fields
    assert not any("score" in f for f in fields)


# --- the judge's shape --------------------------------------------------------
def test_the_judge_derives_before_it_concludes():
    """Third time this has been proved on this model: 8.77 (`grammar_note` before
    `well_formed`, fixed three false positives) and 9.50 (`reason` before
    `approved`, flipped two live false positives). Structured output is emitted in
    schema order, so a conclusion declared first is one the judge rationalises."""
    order = list(NarrativeVerdict.model_fields)
    assert order[0] == "spine"
    assert order[-1] == "score"


def test_the_judge_score_is_bounded_by_the_schema():
    """The report's stage-scaled expectations are stated against this scale, so the
    scale is enforced by the schema rather than trusted."""
    with pytest.raises(ValueError):
        NarrativeVerdict(spine="x", has_want=False, has_obstacle=False, has_change=False, score=0)
    with pytest.raises(ValueError):
        NarrativeVerdict(spine="x", has_want=True, has_obstacle=True, has_change=True, score=6)
    assert SCORE_MIN == 1 and SCORE_MAX == 5


def test_the_prompt_carries_the_register_carve_out():
    """§18: the plain repetitive register is the format working. Without this the
    judge would measure prose quality, which is the one thing a decodable reader
    guarantees will look bad — and every story at every stage would score low, which
    is a ruler that cannot detect a change."""
    low = NARRATIVE_SYSTEM.lower()
    assert "is ever a reason to score lower" in low
    assert "repeated many times" in low
    assert "judge structure only" in low
    # And it must name the failure it is looking for, in the user's own words.
    assert "adam sat" in low


def test_a_failed_judge_scores_zero_which_is_outside_the_scale():
    """An unreachable judge must not be averaged into a report as a bad story.
    0 is below `SCORE_MIN`, so "unmeasured" is a different value from "measured
    badly" and every summary can drop it."""
    r = NarrativeResult(
        score=0, spine="", has_want=False, has_obstacle=False, has_change=False, error="boom"
    )
    assert r.score < SCORE_MIN
    assert r.error


# --- the judge is not a gate --------------------------------------------------
def test_the_narrative_verdict_cannot_change_whether_a_story_passed():
    """DEVIATIONS 11.47, asserted rather than asserted-in-prose.

    `PipelineResult.passed` is decided by the gate and by moderation. This plants
    the worst possible narrative verdict on a passing result and checks nothing
    moves. Sabotage-checked: making `passed` read `narrative.score` turns this red.
    """
    from storyforge.pipeline import PipelineResult

    r = PipelineResult(stage_id=6, theme="dragons", passed=True)
    r.narrative = NarrativeResult(
        score=1, spine="a list of actions", has_want=False, has_obstacle=False, has_change=False
    )
    assert r.passed is True


def test_the_judge_is_off_by_default_so_warm_runs_spend_nothing_on_it():
    """A measurement that costs money on every warm story is a measurement that
    gets turned off later and then rots. It is opt-in at the call site instead."""
    import inspect

    from storyforge.pipeline import run_pipeline

    sig = inspect.signature(run_pipeline)
    assert sig.parameters["judge_narrative_output"].default is False


def test_the_narrative_cost_is_reported_outside_the_cost_of_producing_a_story():
    """`total_cost_usd` is what a story COSTS; the judge is instrumentation. Folding
    it in would make an arm that was measured look like a cost regression against an
    arm that was not."""
    from datetime import date

    from storyforge.llm.pricing import Usage
    from storyforge.pipeline import PipelineResult

    r = PipelineResult(stage_id=6, theme="dragons", passed=True)
    r.narrative = NarrativeResult(
        score=5,
        spine="s",
        has_want=True,
        has_obstacle=True,
        has_change=True,
        usage=Usage(model="claude-haiku-4-5", input_tokens=1000, output_tokens=100),
    )
    on = date(2026, 7, 29)
    assert r.narrative_cost_usd(on) > 0
    assert r.total_cost_usd(on) == 0.0


# --- live, both directions, multiple runs (STATE invariant 5) ------------------
#: Hand-labelled, verbatim out of `story_segments` on the live database, plus the
#: user's own example. I read every one and labelled it myself before any judge saw
#: it; the labels are the criteria stated in `NARRATIVE_SYSTEM` (a want, something
#: in the way, a change by the end) applied by hand.
#:
#: **The ambiguous middle is EXCLUDED and named, not quietly folded into whichever
#: side flatters the judge.** 9.51's lesson is that a fixture drawn from cases the
#: judge already agreed with cannot detect the class of error it is blind to, so the
#: cases below are the ones whose labels I can defend from the TEXT, and the
#: residuals are recorded in DEVIATIONS 11.54 with their measured scores.
LIST_CASES = (
    ("user-example", WORD_LIST),
    (
        "1670",
        "A rabbit sat in the forest. A giraffe sat in the forest. The rabbit ran up. "
        "The giraffe ran up. The rabbit and the giraffe ran to the top. The rabbit "
        "and the giraffe sat on top. The end.",
    ),
    (
        "28",
        "Sam had a rocket. Sam sat in the rocket. The rocket ran up and up. Sam ran "
        "into the moon. Sam sat on the moon. Sam had fun. Sam ran into a planet. Sam "
        "had fun on the planet. Sam ran back into the rocket. The rocket ran back.",
    ),
    (
        "6",
        "A dragon sat in a cave. It had big wings. The dragon ran up a hill. It sat "
        "on top. Its wings help it. The dragon ran back to its cave.",
    ),
    (
        "7272",
        "A rabbit had a burrow in the forest. Every day the rabbit ran to see the "
        "forest. It came to a big rock by the river. The rabbit felt happy there. "
        "Then night came fast. The rabbit ran back to the burrow. It got in the "
        "burrow and was safe. The rabbit did not need to stay out. The forest was "
        "still. The rabbit went to sleep in the burrow. In the morning it came out "
        "again to play.",
    ),
)

STORY_CASES = (
    ("7316", REAL_STORY),
    (
        "7309",
        "A rabbit had a burrow in the forest. Every day, the rabbit went out to play. "
        "One day, the rabbit went far from the burrow. The forest was dark. The "
        "rabbit did not know the way back. The rabbit felt bad. The family went to "
        "look for the rabbit. Then the rabbit saw a light. The light was from home. "
        "The rabbit ran to the light. The rabbit found the burrow. The family was "
        "there. The rabbit was happy again.",
    ),
    (
        "7273",
        "The rabbit had a burrow in the forest. The burrow was deep and safe. A dog "
        "came by the forest. The dog did not see the rabbit. The rabbit ran into the "
        "burrow really fast. The dog did not get in. The rabbit felt safe. After the "
        "dog went away, the rabbit came out. The rabbit had fun in the forest.",
    ),
    (
        "1668",
        "A rabbit sat in the forest. A cat ran to the rabbit. The rabbit ran fast. "
        "The cat ran fast. The rabbit got to the top. The cat sat. The rabbit sat. "
        "The end.",
    ),
    (
        "6416",
        "A dragon was in a cave. The dragon had wide wings. Every day it went up "
        "high. On a day a dog came by the cave. The dog did not have wings like the "
        "dragon. The dragon let the dog stay. They played every day. The dog was on "
        "the dragon. Up they went, high into the sky. The dragon and the dog were "
        "happy.",
    ),
)

#: How much of a gap between the two labelled means counts as separation. The judge
#: is stochastic and the assertion is on SEPARATION rather than on an absolute
#: threshold per case, because an absolute threshold on a stochastic judge is a
#: flaky test and separation is the property the instrument actually needs: it is
#: used to detect a distribution shift between two arms, not to classify one story.
MIN_LABEL_GAP = 1.0

#: Runs. More than one, because STATE invariant 5 requires multiple runs and
#: because every one of the four passes at the moderation prompt broke a case that
#: had been passing without a single-run test noticing.
LIVE_RUNS = 3


@pytest.mark.skipif(
    not os.getenv("STORYFORGE_LIVE_NARRATIVE"),
    reason="live Haiku calls: set STORYFORGE_LIVE_NARRATIVE=1",
)
async def test_live_narrative_judge_separates_stories_from_word_lists():
    """Both directions, the whole fixture, three runs. ~$0.02.

    Direction 1: the hand-labelled word lists must score LOW. Direction 2: the
    hand-labelled stories — written in the SAME tiny register, with the same short
    sentences and the same repetition — must score HIGH. Running only direction 1 is
    how you ship a judge that scores everything 1 and calls it a discovery.
    """
    import asyncio

    from storyforge.llm.narrative import judge_narrative

    cases = [(n, "LIST", t) for n, t in LIST_CASES] + [(n, "STORY", t) for n, t in STORY_CASES]
    runs = [
        await asyncio.gather(*(judge_narrative(t) for _n, _lab, t in cases))
        for _ in range(LIVE_RUNS)
    ]

    by_case: dict[tuple[str, str], list[int]] = {}
    for run in runs:
        for (name, lab, _t), v in zip(cases, run, strict=True):
            assert not v.error, f"{name}: judge failed — {v.error}"
            by_case.setdefault((name, lab), []).append(v.score)

    print(f"\n{'case':>13} {'label':>6}  scores over {LIVE_RUNS} runs")
    for (name, lab), scores in by_case.items():
        print(f"{name:>13} {lab:>6}  {scores}")

    lists = [s for (_n, lab), ss in by_case.items() if lab == "LIST" for s in ss]
    stories = [s for (_n, lab), ss in by_case.items() if lab == "STORY" for s in ss]
    gap = statistics.mean(stories) - statistics.mean(lists)
    print(
        f"\nLIST mean {statistics.mean(lists):.2f} (max {max(lists)})   "
        f"STORY mean {statistics.mean(stories):.2f} (min {min(stories)})   gap {gap:.2f}"
    )

    assert gap >= MIN_LABEL_GAP, (
        f"the judge does not separate the labels: gap {gap:.2f} < {MIN_LABEL_GAP}. "
        f"It is being used to detect a distribution shift between two arms, and a "
        f"ruler that cannot tell a story from a word list cannot do that."
    )
    # Direction 2 on its own terms: a judge that scored EVERYTHING low would pass a
    # gap test only by accident, so the stories are also required to land in the top
    # half of the scale.
    assert statistics.mean(stories) > (SCORE_MIN + SCORE_MAX) / 2, (
        "the labelled stories do not score above the midpoint — the judge is "
        "measuring the register, not the structure"
    )
    assert statistics.mean(lists) < (SCORE_MIN + SCORE_MAX) / 2, (
        "the labelled word lists do not score below the midpoint"
    )
