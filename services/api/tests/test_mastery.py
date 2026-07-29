"""Phase 5: grading and promotion as pure functions (§8).

No database, no HTTP. Everything §8 actually decides — whether a tap was right,
which correspondences it is evidence about, and whether the child moves up a
stage — lives in `storyforge.mastery`, so it can be tested at the level it is
written at.

Two things these tests exist to hold, beyond "the arithmetic works":

- **The mastery signal is not forgeable.** `grade_answer` is the only thing that
  knows the answer key, and it is fed by an untrusted client. It is exercised
  here with dicts, lists, bools, negative and enormous indices, and keys that
  contradict themselves — a 500 on any of those is an availability bug, and a
  spurious `True` is a correctness one.
- **The promotion boundary is exact.** §8 says 80%. A test at 90% would pass
  against a threshold of 0.5, so the interesting cases are 79% and exactly 80%.
"""

from __future__ import annotations

import pytest

from storyforge.mastery import (
    ACCURACY_THRESHOLD,
    MAX_STAGE,
    MIN_GPC_OBSERVATIONS,
    STORIES_REQUIRED_AT_STAGE,
    THRESHOLDS,
    WordEvidence,
    accuracy_of,
    answer_truth,
    evaluate_promotion,
    gpc_increments,
    grade_answer,
    single_word,
    word_gpc_ids,
)

# A well-formed tap_word question, in the shape llm/questions.py emits.
TAP_PAYLOAD = {"options": ["fast", "ran", "it"], "cue_word": "for", "target_grapheme": "f"}
TAP_ANSWER = {"index": 0, "correct": "fast"}
# An mcq2, whose options are whole sentences.
MCQ_PAYLOAD = {"options": ["The man ran back fast.", "The man ran back stop."]}
MCQ_ANSWER = {"index": 0, "correct": "The man ran back fast."}


def _grade(response, payload=TAP_PAYLOAD, answer=TAP_ANSWER):
    return grade_answer(response=response, payload=payload, answer=answer)


# --- grading: the happy shapes -----------------------------------------------
def test_the_tapped_word_grades_correct():
    assert _grade("fast") is True
    assert _grade("ran") is False
    assert _grade("it") is False


def test_the_tapped_index_grades_correct():
    """Kid mode may send the option's position instead of its text."""
    assert _grade(0) is True
    assert _grade(1) is False
    assert _grade(2) is False


def test_grading_ignores_case_and_surrounding_whitespace():
    """A tap is a choice of option, not a spelling test."""
    assert _grade("  FAST ") is True
    assert _grade("Fast") is True


def test_mcq2_sentences_grade_by_text_and_by_index():
    assert grade_answer(response="The man ran back fast.", payload=MCQ_PAYLOAD, answer=MCQ_ANSWER)
    assert grade_answer(response=0, payload=MCQ_PAYLOAD, answer=MCQ_ANSWER) is True
    assert grade_answer(response=1, payload=MCQ_PAYLOAD, answer=MCQ_ANSWER) is False
    # Whitespace differences survive a round trip through some clients.
    assert grade_answer(
        response="The man  ran back  fast.", payload=MCQ_PAYLOAD, answer=MCQ_ANSWER
    )


def test_a_shuffled_key_is_respected():
    """Option order is randomised per story (llm/questions.shuffle_options), so
    the correct index is genuinely not always 0."""
    payload = {"options": ["fast", "plan", "gave"]}
    answer = {"index": 2, "correct": "gave"}
    assert grade_answer(response=2, payload=payload, answer=answer) is True
    assert grade_answer(response=0, payload=payload, answer=answer) is False
    assert grade_answer(response="gave", payload=payload, answer=answer) is True


# --- grading: hostile input --------------------------------------------------
@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"correct": "fast"},
        ["fast"],
        [0],
        3.0,
        b"fast",
        object(),
        float("nan"),
        set(),
    ],
)
def test_a_malformed_response_is_wrong_not_an_exception(response):
    """A client can post anything. The grader must answer, not raise (§9)."""
    assert _grade(response) is False


def test_a_boolean_is_never_an_index():
    """`True == 1` in Python, so an unguarded index check would grade
    `{"response": true}` as a tap on option 1."""
    assert _grade(True) is False
    assert _grade(False) is False


@pytest.mark.parametrize("response", [-1, -1000, 3, 99, 2**70, -(2**70)])
def test_an_out_of_range_index_is_wrong_not_an_exception(response):
    assert _grade(response) is False


def test_a_response_that_is_the_key_itself_does_not_pass():
    """Posting the key back — a client that guessed the storage shape."""
    assert _grade(str(TAP_ANSWER)) is False
    assert _grade("correct") is False


def test_a_numeric_string_is_not_an_index():
    """"0" is genuinely ambiguous; widening what counts as correct is how a
    mastery signal quietly stops meaning anything."""
    assert _grade("0") is False


# --- grading: an unusable stored key -----------------------------------------
def test_a_missing_key_is_ungradeable_rather_than_wrong():
    """`None` is "this question cannot be graded", which the endpoint turns into
    a 409. Collapsing it into `False` would mark a child wrong for our bug."""
    assert _grade("fast", answer={}) is None
    assert _grade("fast", answer=None) is None
    assert _grade("fast", answer=[1, 2]) is None
    assert _grade("fast", answer={"correct": ""}) is None
    assert _grade("fast", answer={"correct": 7}) is None


def test_a_self_contradictory_key_is_ungradeable():
    """`correct` and `index` name different options — one of them is a lie and
    there is no way to tell which."""
    assert _grade("fast", answer={"correct": "fast", "index": 1}) is None
    assert _grade("ran", answer={"correct": "fast", "index": 1}) is None


def test_a_key_naming_an_option_that_was_never_offered_is_ungradeable():
    """No tap could ever be right, so this is a bad question, not a wrong child."""
    assert _grade("fast", answer={"correct": "elephant"}) is None


def test_a_boolean_index_is_not_option_one():
    assert answer_truth(TAP_PAYLOAD, {"index": True}) is None


def test_an_index_only_key_and_a_text_only_key_both_work():
    assert answer_truth(TAP_PAYLOAD, {"index": 1}) == "ran"
    assert answer_truth(TAP_PAYLOAD, {"correct": "ran"}) == "ran"


def test_a_key_with_no_options_to_check_against_still_grades_by_text():
    """`payload` is JSONB and could be malformed independently of the key."""
    assert grade_answer(response="fast", payload={}, answer={"correct": "fast"}) is True
    assert grade_answer(response="ran", payload=None, answer={"correct": "fast"}) is False
    # ...but an index has nothing to index into.
    assert grade_answer(response=0, payload={}, answer={"correct": "fast"}) is False


def test_extra_fields_in_the_stored_key_are_ignored():
    """The key is JSONB written by `llm/questions.py`, which has already grown a
    third field (`target_grapheme`, moved out of `payload` because it gave the
    answer away). Grading reads `correct` and `index` and must not care what
    else rides along."""
    key = {"correct": "fast", "index": 0, "target_grapheme": "f", "notes": "whatever"}
    assert _grade("fast", answer=key) is True
    assert _grade("ran", answer=key) is False


def test_an_out_of_range_stored_index_falls_back_to_the_text():
    assert answer_truth(TAP_PAYLOAD, {"index": 99, "correct": "ran"}) == "ran"
    assert answer_truth(TAP_PAYLOAD, {"index": 99}) is None


# --- evidence: GPCs come from the decoder ------------------------------------
def test_gpcs_come_from_the_decoders_alignment():
    """§8's evidence is the correspondences the aligner actually used — never a
    guess from the spelling."""
    assert word_gpc_ids("cat", 1) == ["c|K", "a|AE", "t|T"]
    assert word_gpc_ids("ship", 3) == ["sh|SH", "i|IH", "p|P"]
    # A split digraph is two edges over three letters (DEVIATIONS 1.2).
    assert word_gpc_ids("gave", 5) == ["g|G", "a_e|EY", "v|V"]


def test_a_repeated_gpc_counts_once_per_instance():
    """§8 says "every GPC instance", so "dad" is evidence about /d/ twice."""
    assert word_gpc_ids("dad", 1) == ["d|D", "a|AE", "d|D"]


def test_a_word_not_decodable_at_the_storys_stage_yields_no_evidence():
    """"ran" needs `r|R`, which is taught at stage 2. A stage-1 story cannot be
    evidence about a correspondence its reader has not met."""
    assert word_gpc_ids("ran", 1) == []
    assert word_gpc_ids("ran", 2) == ["r|R", "a|AE", "n|N"]
    # A word the inventory cannot spell at any stage (DEVIATIONS 1.6), and a
    # word CMUdict has never heard of, are both simply no evidence.
    assert word_gpc_ids("beautiful", 12) == []
    assert word_gpc_ids("zzzz", 12) == []


def test_a_word_that_is_a_sight_word_but_also_decodable_still_counts():
    """`word_gpc_ids` asks the aligner, not the sight list, and that is
    deliberate. "the" is a stage-1 sight word, but by stage 3 `th|DH` and `e|EH`
    are both taught and the aligner can genuinely sound it out — at which point
    a correct tap IS evidence. This never arises for a real tap_word option
    (llm/questions.sound_targets excludes sight words from the option pool), so
    the choice only shows up if a hand-written question reaches the grader."""
    assert word_gpc_ids("the", 1) == []
    assert word_gpc_ids("the", 12) == ["th|DH", "e|EH"]


def test_single_word_rejects_anything_that_is_not_one_word():
    assert single_word("fast") == "fast"
    assert single_word("Fast!") == "fast"
    assert single_word("the man ran") is None
    assert single_word("") is None
    assert single_word(None) is None
    assert single_word(42) is None


# --- evidence: folding into counters -----------------------------------------
def test_a_correct_answer_credits_every_gpc_in_the_word():
    deltas = gpc_increments([WordEvidence(word="cat", stage_id=1, correct=True)])
    assert [(d.gpc_id, d.n_seen, d.n_correct) for d in deltas] == [
        ("a|AE", 1, 1),
        ("c|K", 1, 1),
        ("t|T", 1, 1),
    ]


def test_a_wrong_answer_is_seen_but_not_correct():
    deltas = gpc_increments([WordEvidence(word="cat", stage_id=1, correct=False)])
    assert all(d.n_seen == 1 and d.n_correct == 0 for d in deltas)


def test_increments_accumulate_across_words_and_instances():
    deltas = {
        d.gpc_id: (d.n_seen, d.n_correct)
        for d in gpc_increments(
            [
                WordEvidence(word="dad", stage_id=1, correct=True),
                WordEvidence(word="cat", stage_id=1, correct=False),
            ]
        )
    }
    assert deltas["d|D"] == (2, 2)  # twice in "dad", both credited
    assert deltas["a|AE"] == (2, 1)  # once in each word, credited once
    assert deltas["c|K"] == (1, 0)


def test_increments_are_deterministically_ordered():
    """Two runs over the same reading must produce identical updates."""
    evidence = [
        WordEvidence(word="ship", stage_id=3, correct=True),
        WordEvidence(word="cat", stage_id=1, correct=False),
    ]
    ids = [d.gpc_id for d in gpc_increments(evidence)]
    assert ids == sorted(ids)
    assert ids == [d.gpc_id for d in gpc_increments(list(reversed(evidence)))]


def test_no_evidence_produces_no_updates():
    assert gpc_increments([]) == []
    assert gpc_increments([WordEvidence(word="the", stage_id=1, correct=True)]) == []


# --- promotion (§8) ----------------------------------------------------------
def _decide(stories=STORIES_REQUIRED_AT_STAGE, seen=100, correct=90, stage=2):
    return evaluate_promotion(
        stage_id=stage, stories_completed=stories, gpc_seen=seen, gpc_correct=correct
    )


def test_the_thresholds_are_the_ones_section_8_specifies():
    assert STORIES_REQUIRED_AT_STAGE == 5
    assert ACCURACY_THRESHOLD == 0.80
    assert MAX_STAGE == 12
    # Published as data so the dashboard can print the rule (§8).
    assert THRESHOLDS.stories_required == STORIES_REQUIRED_AT_STAGE
    assert THRESHOLDS.accuracy_threshold == ACCURACY_THRESHOLD
    assert THRESHOLDS.min_observations == MIN_GPC_OBSERVATIONS


def test_all_four_criteria_met_promotes():
    decision = _decide()
    assert decision.promote is True
    assert decision.from_stage == 2
    assert decision.to_stage == 3
    assert all(c.met for c in decision.criteria)


def test_exactly_eighty_percent_promotes_and_seventy_nine_does_not():
    """The boundary is the whole test. §8 says ≥80%."""
    assert _decide(seen=100, correct=80).promote is True
    assert _decide(seen=100, correct=79).promote is False
    assert _decide(seen=100, correct=79).accuracy == pytest.approx(0.79)


def test_four_stories_is_not_five():
    decision = _decide(stories=4)
    assert decision.promote is False
    assert [c.detail for c in decision.criteria if c.name == "stories_completed"] == [
        "4 of 5 stories completed at stage 2"
    ]


def test_a_perfect_score_on_too_little_evidence_does_not_promote():
    """One lucky tap on a two-GPC word is 100% and proves nothing."""
    decision = _decide(seen=MIN_GPC_OBSERVATIONS - 1, correct=MIN_GPC_OBSERVATIONS - 1)
    assert decision.accuracy == 1.0
    assert decision.promote is False
    assert [c.met for c in decision.criteria if c.name == "evidence"] == [False]

    at_the_floor = _decide(seen=MIN_GPC_OBSERVATIONS, correct=MIN_GPC_OBSERVATIONS)
    assert at_the_floor.promote is True


def test_no_evidence_at_all_is_not_an_accuracy_of_zero():
    """`None` (never asked) and 0.0 (asked and always wrong) are different
    facts, and the dashboard says so."""
    decision = _decide(seen=0, correct=0)
    assert decision.accuracy is None
    assert decision.promote is False
    assert "no stage-2 sounds answered yet" in "".join(c.detail for c in decision.criteria)
    assert accuracy_of(0, 0) is None
    assert accuracy_of(4, 0) == 0.0


def test_the_last_stage_never_promotes():
    """There is no stage 13 in Appendix A's scope & sequence."""
    decision = _decide(stage=MAX_STAGE, stories=99, seen=999, correct=999)
    assert decision.promote is False
    assert decision.to_stage is None
    assert [c.met for c in decision.criteria if c.name == "stage_available"] == [False]


def test_every_criterion_explains_itself_with_numbers():
    """§11's dashboard shows "why not", not an unfilled bar."""
    decision = _decide(stories=3, seen=20, correct=10)
    assert not decision.promote
    details = {c.name: c.detail for c in decision.criteria}
    assert details["stories_completed"] == "3 of 5 stories completed at stage 2"
    assert details["evidence"] == "20 of 10 answered GPC instances needed on stage-2 sounds"
    assert details["accuracy"] == "10/20 = 50% of stage-2 sounds correct (need 80%)"
    assert details["stage_available"] == "stage 3 is next"
    # A parent can recompute the verdict from what they are shown.
    assert decision.stories_required == 5
    assert decision.accuracy_threshold == 0.80
    assert decision.min_observations == MIN_GPC_OBSERVATIONS


def test_promotion_is_a_pure_function_of_its_arguments():
    """Same inputs, same decision — no clock, no database, no hidden state."""
    first = _decide(stories=5, seen=13, correct=11)
    second = _decide(stories=5, seen=13, correct=11)
    assert first == second
