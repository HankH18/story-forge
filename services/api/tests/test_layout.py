"""§10's screen budget: the box, the splitter, the gate and the clamp.

The defect these tests are about (DEVIATIONS 19.1): `warm._split_segments` put
the first six sentences on the first six screens and *every remaining sentence*
on the seventh, so 68 of 763 servable segments overflowed the §10 box and all 68
were `idx = 6` — the screen carrying the ending. Nothing in the generator had
ever been told there was a screen, and nothing in the validator had ever checked.

Every test here was sabotage-checked: the source was broken, the suite re-run,
and the failure confirmed to land on THIS file's own assertion rather than an
earlier one. The line numbers are recorded in DEVIATIONS 19.8, because a test
that cannot fail is the thing this repo has now caught six times.
"""

from __future__ import annotations

import json

import pytest

from storyforge.layout import (
    LAYOUT_CHARS_PER_WORD,
    MAX_SEGMENTS,
    SEGMENT_CHAR_BUDGET,
    STORY_CHAR_BUDGET,
    fit_refusal,
    layout_word_ceiling,
    paginate,
    split_sentences,
)

#: A stage-9-shaped story: 24 short sentences, the length band §6 asks for.
#: Deliberately built from equal-length sentences so the optimal partition is
#: arithmetic rather than a judgement call — a test that has to be re-derived by
#: hand every time the packer changes is a test nobody will keep honest.
LONG_STORY = " ".join(f"The dragon went to the cave {i}." for i in range(24))

#: A LUMPY story: one long sentence and eight short ones. It exists because the
#: even-length `LONG_STORY` **cannot fail** the screen-count assertions — the
#: binary search happens to land on exactly the requested number of screens for
#: it, so the fill step never runs and a test using it proves nothing. Sabotage
#: caught that: deleting the fill step left the whole file green.
#:
#: Here the one long sentence sets a floor on the page cap that lets all eight
#: short ones share a single screen, so the search alone yields 2 or 3 screens
#: where 7 were asked for. Only the fill step reaches the quota.
LUMPY_STORY = (
    "The little dragon flew all the way over the dark wood to the cave by the sea. "
    + " ".join(f"He ran {i}." for i in range(8))
)


# --- the numbers close ------------------------------------------------------


def test_the_word_ceiling_is_what_seven_screens_actually_hold():
    """§6's band and §10's box have to be reconcilable, or the prompt asks for
    something the validator refuses.

    This is the arithmetic that made the old pool impossible: §6 asks for up to
    200 words at stages 8-11, and 200 words is ~1040 characters against the 875
    seven screens hold. `layout_word_ceiling` is the largest word count that
    fits, and if it ever stops being that, a generation prompt and a generation
    gate start disagreeing at the margin.
    """
    assert layout_word_ceiling() * LAYOUT_CHARS_PER_WORD <= STORY_CHAR_BUDGET
    # ...and it is the LARGEST such count, not merely a safe one. A ceiling that
    # drifted low would quietly shorten every top-stage story for no reason.
    assert (layout_word_ceiling() + 1) * LAYOUT_CHARS_PER_WORD > STORY_CHAR_BUDGET


def test_the_story_budget_is_the_screen_budget_times_the_screen_count():
    assert STORY_CHAR_BUDGET == MAX_SEGMENTS * SEGMENT_CHAR_BUDGET


def test_the_screen_budget_stays_under_what_the_box_was_measured_to_hold():
    """125 is not a preference. `apps/web/fit-check` swept the real ReadingView in
    the production bundle at every length from 100 to 209 characters over three
    word-length profiles and 0-3 story-word chips; at the binding configuration
    (1024x768 landscape, §11's 40px, a 583px box) **129 was the largest length at
    which every sample fitted and 130 the shortest that overflowed**.

    Raising the constant past that measurement does not make the box bigger — it
    only stops this file from agreeing with `make fit-check`, which renders the
    box for real and would go red. Locked here so the temptation is a red test
    rather than a quiet edit.
    """
    measured_ceiling = 129
    assert SEGMENT_CHAR_BUDGET <= measured_ceiling


# --- the splitter -----------------------------------------------------------


def test_short_stories_are_still_one_sentence_per_screen():
    """The fix must not move anything that was never broken. A story of
    `MAX_SEGMENTS` sentences or fewer already has the smallest possible longest
    page, and stages 1-3 are entirely made of those."""
    body = "Sam sat. A cat ran. The dog was glad."
    assert paginate(body) == ["Sam sat.", "A cat ran.", "The dog was glad."]


def test_pagination_never_drops_a_word():
    """The old splitter's one correct instinct, kept. Truncating would hand a
    child a story with no ending; this asserts the partition is exactly the
    sentences, in order."""
    pages = paginate(LONG_STORY)
    assert " ".join(pages) == " ".join(split_sentences(LONG_STORY))


def test_pagination_respects_the_seven_screen_cap():
    assert len(paginate(LONG_STORY)) <= MAX_SEGMENTS


def test_the_last_screen_is_not_where_the_rest_of_the_story_goes():
    """**This is the regression test for the whole defect.**

    Under the old tail-merge the first six screens held one sentence each (~31
    chars) and the seventh held the other eighteen (~560 chars) — an 18x spread
    that the fit gate saw as 68 clipped segments, every one of them idx 6. The
    assertion is on the SHAPE, not on a golden partition: no screen may be more
    than twice the mean, which the tail-merge fails by a factor of nine and any
    honest balanced split passes comfortably.
    """
    pages = paginate(LONG_STORY)
    widest, mean = max(len(p) for p in pages), len(LONG_STORY) / len(pages)
    assert widest <= 2 * mean, f"one screen carries {widest} chars against a mean of {mean:.0f}"


def test_pagination_puts_a_long_story_inside_the_screen_budget():
    """A story at the clamped word ceiling has to actually fit, or the ceiling is
    a number nobody can hit."""
    assert max(len(p) for p in paginate(LONG_STORY)) <= SEGMENT_CHAR_BUDGET


def test_pagination_is_idempotent():
    """Re-paginating the joined pages must reproduce them. The free repair runs
    against the live pool and an operator will run it twice."""
    once = paginate(LONG_STORY)
    assert paginate(" ".join(once)) == once


def test_pagination_uses_every_screen_it_is_given():
    """**The promise the repair path depends on**, and the one the binary search
    alone does not keep.

    Minimising the longest page can be achieved with fewer pages than exist — the
    cap is set by the longest single sentence, and at that cap the rest may all
    fit on one screen. Three live stories were packed into five or six screens
    where seven were available. That is not merely a wasted screen: `refit_census`
    holds a story's existing segment count fixed (the rows are already in the
    database), so an under-filling `paginate` would make a repaired story and a
    freshly-split one disagree about the same text, which is exactly what
    `test_grammar.py::test_story_body_round_trips_through_the_segment_split`
    refuses.

    `LUMPY_STORY`, not `LONG_STORY` — see that constant's docstring: with
    equal-length sentences the search hits the quota by itself and this assertion
    would prove nothing.
    """
    for n in (2, 5, 7):
        assert len(paginate(LUMPY_STORY, max_segments=n)) == n, n


def test_pagination_never_invents_a_screen_it_cannot_fill():
    """Fewer sentences than screens is a shorter story, not a blank page. Filling
    must stop at the sentences that exist."""
    assert len(paginate("Sam sat. A cat ran.", max_segments=7)) == 2


def test_more_screens_never_makes_a_screen_bigger():
    """Filling the spare screens has to be monotone, or it is not a fix.

    The whole argument for using every available screen is that splitting the
    widest one only ever lowers the maximum. If that were false, the fill step
    would be trading the property `fit_refusal` measures for a tidier-looking
    partition — so the property is asserted rather than argued.
    """
    for story in (LONG_STORY, LUMPY_STORY):
        widths = [max(len(p) for p in paginate(story, max_segments=n)) for n in range(1, 9)]
        assert widths == sorted(widths, reverse=True), (story[:40], widths)


# --- the gate ---------------------------------------------------------------


def test_the_splitter_that_writes_the_database_is_the_one_that_was_fixed():
    """`warm._split_segments` is what `persist()` and `eval.persist_story` call, so
    it is the function that decides what a child's screens actually contain. A fix
    that lived only in `layout.paginate` while `warm` kept its own tail-merge
    would leave every new story broken and every test in this file green —
    `pipeline._call` holding `max_tokens=8000` while `llm/generate.py` had moved
    to 16000 is the same shape (DEVIATIONS 8.7/8.25)."""
    from storyforge.warm import _split_segments

    assert _split_segments(LONG_STORY) == paginate(LONG_STORY)
    assert max(len(p) for p in _split_segments(LONG_STORY)) <= SEGMENT_CHAR_BUDGET


def test_a_story_that_fits_is_not_refused():
    assert fit_refusal(LONG_STORY) is None


def test_a_story_too_long_for_seven_screens_is_refused():
    """§6's literal 200-word top band, as characters. This is the draft the old
    prompt was asking for and the old gate was accepting."""
    body = " ".join(f"The dragon went to the big dark cave number {i}." for i in range(30))
    assert len(body) > STORY_CHAR_BUDGET
    refusal = fit_refusal(body)
    assert refusal is not None


def test_the_refusal_names_the_ceiling_the_overflow_and_the_cut():
    """It is read straight into the rewrite prompt. "Too long" is not something
    a model can act on — DEVIATIONS 9.40's whole finding is that a problem list
    which does not say what to change is worse than none."""
    body = " ".join(f"The dragon went to the big dark cave number {i}." for i in range(30))
    refusal = fit_refusal(body)
    assert str(SEGMENT_CHAR_BUDGET) in refusal
    assert str(STORY_CHAR_BUDGET) in refusal
    assert str(len(body)) in refusal


def test_an_empty_story_is_not_this_gates_refusal():
    """Vacuity belongs to `passes_gate` (STATE invariant 1) and has a different
    remedy — write the story, not shorten it. Two gates saying the same thing in
    different words is how a rewrite prompt gets a false premise."""
    for empty in ("", "   ", "..."):
        assert fit_refusal(empty) is None


def test_the_fit_gate_catches_what_the_decodability_gate_cannot():
    """The two gates are independent, and this is the proof the second one earns
    its place: a story can be 100% decodable, zero OOV, zero story words — every
    `passes_gate` clause satisfied — and still be a screen a child cannot read to
    the end of. Before DEVIATIONS 19.4 that story was accepted and served."""
    from storyforge.decoder import mastered_ids, validate_text
    from storyforge.pipeline import passes_gate

    body = " ".join("Sam sat on the big red mat." for _ in range(30))
    report = validate_text(body, mastered_ids(11), frozenset(), frozenset())
    assert passes_gate(report) is True, "decodability has no opinion about length"
    assert fit_refusal(body) is not None


def test_the_rewrite_prompt_says_the_story_is_too_long_and_not_that_it_is_unreadable():
    """DEVIATIONS 9.40's finding, applied to the new refusal: a draft that failed
    on length alone must not be handed the false premise "your story used words
    this child cannot read" beside an empty problem list. It has to be told the
    thing it can act on."""
    from storyforge.decoder import mastered_ids, validate_text
    from storyforge.pipeline import Vocabulary, build_rewrite_block

    body = " ".join("Sam sat on the big red mat." for _ in range(30))
    report = validate_text(body, mastered_ids(11), frozenset(), frozenset())
    vocab = Vocabulary(
        stage_id=11,
        stage_name="Review",
        target_min=120,
        target_max=layout_word_ceiling(),
        allowed_words=["sam", "sat", "on", "the", "big", "red", "mat"],
        sight_words=[],
        story_words={},
    )
    block = build_rewrite_block(report, vocab, body, theme="dragons")
    # The REFUSAL SENTENCE, not merely the headline. The first version of this
    # test asserted only on the headline and could not fail: deleting the
    # `lines.append` that puts the refusal in PROBLEMS left the headline in place
    # and the whole file green, which is F1 exactly — a headline naming a problem
    # beside a list that does not contain it.
    assert fit_refusal(body) in block
    assert "does not fit on the screen" in block
    assert "words this child cannot read" not in block
    # ...and it must not be the generic "did not pass the reading check" filler,
    # which is what a model gets when nothing described its actual problem.
    assert "did not pass the reading check" not in block


@pytest.mark.anyio
async def test_run_pipeline_refuses_a_story_that_does_not_fit_and_never_pays_to_moderate_it(
    monkeypatch,
):
    """**The gate has to be ACTED ON, not merely computed.**

    This repo has already shipped the other version of this once: DEVIATIONS
    6.24's `well_formed` was computed, folded into a verdict, and then dropped on
    the floor by `warm.persist`, so "the check ran, passed, and was thrown away"
    for every story ever written. A `fit_refusal` that `run_pipeline` calculated
    into a span and did not put in `gate` would be that defect again, and every
    other test in this file would still be green.

    So this drives the real `run_pipeline` with a fake `_call` returning a
    perfectly decodable but far-too-long story, and asserts three things a
    discarded check could not produce: it does not pass, it spends the whole
    rewrite budget trying, and **§13 is never asked to judge it** — invariant 11,
    no Haiku call on a story that can never be served.
    """
    from storyforge import obs
    from storyforge import pipeline as pl
    from storyforge.llm.generate import StoryDraft, StorySegment
    from storyforge.llm.pricing import Usage

    monkeypatch.setattr(obs, "_client", None)
    body = " ".join("Sam sat on the big red mat." for _ in range(30))
    vocab = pl.Vocabulary(
        stage_id=11,
        stage_name="Review",
        target_min=120,
        target_max=layout_word_ceiling(),
        allowed_words=["sam", "sat", "on", "the", "big", "red", "mat"],
        sight_words=[],
        story_words={},
    )

    async def fake_load(_session, _stage_id, _theme):
        return vocab

    async def fake_call(*_a, **_kw):
        draft = StoryDraft(
            title="Sam Sat",
            page_plan=["sam sits"],
            segments=[StorySegment(text=body)],
            story_words_used=[],
        )
        return draft, Usage(model="m", input_tokens=1, output_tokens=1)

    async def fake_moderate(_body):  # pragma: no cover - must never be reached
        raise AssertionError("§13 was paid to judge a story that cannot be shown")

    monkeypatch.setattr(pl, "load_vocabulary", fake_load)
    monkeypatch.setattr(pl, "_call", fake_call)
    monkeypatch.setattr(pl, "moderate", fake_moderate)

    result = await pl.run_pipeline(None, 11, "dragons")

    assert result.passed is False
    assert result.final_report is not None
    from storyforge.pipeline import passes_gate

    assert passes_gate(result.final_report) is True, (
        "the draft is fully decodable — if this is False the test is proving the "
        "wrong gate and would pass with the fit check deleted"
    )
    assert len(result.attempts) == pl.MAX_REWRITES + 1


# --- what the model is actually told ----------------------------------------


def test_the_frozen_rules_state_the_screen_budget():
    """A rule the model is not given cannot be followed. Before DEVIATIONS 19.1
    the word "screen" appeared in the schema's field description and nowhere in
    the prompt at all."""
    from storyforge.llm.generate import SYSTEM_RULES

    assert str(SEGMENT_CHAR_BUDGET) in SYSTEM_RULES
    assert str(STORY_CHAR_BUDGET) in SYSTEM_RULES
    assert str(MAX_SEGMENTS) in SYSTEM_RULES


def test_the_request_block_states_the_screen_budget_alongside_the_word_band():
    """Both units, in the same block, so the model never has to reconcile a word
    count with a page size it was never given."""
    from storyforge.llm.generate import build_request_block

    block = build_request_block("dragons", {}, 120, layout_word_ceiling())
    assert "SCREENS" in block
    assert str(SEGMENT_CHAR_BUDGET) in block
    assert str(STORY_CHAR_BUDGET) in block


def test_the_schema_makes_the_model_plan_the_pages_before_it_writes_one():
    """**Field order is what the model thinks in.**

    A constrained decoder emits properties in schema order, so `title, segments,
    story_words_used` sent the model into `segments[0].text` with no commitment
    to how many screens there were or what was on the last one — it wrote the
    opening before it had budgeted for the ending. Same shape as `well_formed`
    before `grammar_note` (DEVIATIONS 8.70-8.82).

    Asserted on the JSON Schema rather than on `model_fields`, because the schema
    is what actually reaches the API.
    """
    from storyforge.llm.generate import StoryDraft

    order = list(StoryDraft.model_json_schema()["properties"])
    assert "page_plan" in order, order
    assert order.index("page_plan") < order.index("segments"), order
    # ...and the untrusted claim still comes last: it is a report about text that
    # by then exists (STATE invariant 2).
    assert order[-1] == "story_words_used", order


def test_the_segment_field_tells_the_model_how_big_a_screen_is():
    from storyforge.llm.generate import StorySegment

    described = json.dumps(StorySegment.model_json_schema())
    assert str(SEGMENT_CHAR_BUDGET) in described


# --- the clamp --------------------------------------------------------------


@pytest.mark.anyio
async def test_load_vocabulary_clamps_the_word_target_to_what_the_box_holds():
    """§6 says 120-200 at stages 8-11; seven screens hold 168 words. The clamp is
    on `Vocabulary` and not on the prompt, so the model, the validator and the
    §14 harness all read the SAME ceiling — a prompt-only clamp would leave
    `measure_length.py` scoring a 190-word story compliant while the gate refused
    it.
    """
    from storyforge.db import SessionLocal
    from storyforge.pipeline import load_vocabulary

    async with SessionLocal() as session:
        top = await load_vocabulary(session, 9, "dragons")
        low = await load_vocabulary(session, 1, "dragons")

    assert top.target_max == layout_word_ceiling()
    assert top.target_min == 120, "the FLOOR is §6's and is never raised to fit the box"
    # Stages whose §6 ceiling already fits are untouched — `min`, never `max`.
    assert low.target_max == 60
