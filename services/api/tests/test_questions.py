"""Phase 4: the question-generation node (§11.3).

Everything here is pure logic — prompt assembly, the closed sound list, the
decoder-backed verification, the deterministic shuffle. The live path is proved
by `make eval-smoke`; what these tests protect is the property that makes the
node safe to ship: **a question the decoder rejects never becomes a question**,
and the answer is never guessable from position or shape.
"""

from __future__ import annotations

import pytest

from storyforge.llm.pricing import Usage
from storyforge.llm.questions import (
    PROMPT_FRAMES,
    QUESTION_RULES,
    GeneratedQuestion,
    Mcq2Draft,
    Mcq2Only,
    QuestionDraft,
    SoundTarget,
    TapWordDraft,
    TapWordOnly,
    build_question_request,
    build_sound_block,
    check_mcq2,
    check_tap_word,
    cue_leaks,
    generate_questions,
    readable,
    readable_openers,
    shuffle_options,
    sound_targets,
    story_supports,
    tap_word_prompt,
    uses_stage_gpc,
    word_graphemes,
)
from storyforge.pipeline import passes_gate

# A real warm-pool story (stage 3, dragons theme) — the node's actual input.
STORY = (
    "A dragon was in a cave. It went up fast. Fire hit the hill. "
    "A man ran back. He was glad. The dragon went into the cave."
)
TITLE = "The Dragon And Fire"
STAGE = 3
SIGHT = ["a", "go", "he", "i", "is", "me", "no", "she", "the", "to", "was", "we"]
STORY_WORDS = ["cave", "dragon", "fire"]
# Frequency-ordered, as `allowed_words` arrives from the precompute table.
ALLOWED = [
    "the", "and", "had", "but", "for", "get", "with", "not", "him", "top",
    "sit", "run", "big", "red", "dad", "van", "job", "let", "must", "camp",
]


def targets():
    return sound_targets(
        story_text=STORY,
        stage_id=STAGE,
        allowed_words=ALLOWED,
        sight_words=SIGHT,
        story_words=STORY_WORDS,
    )


def check_tap(draft, ts=None, stage_id=STAGE, story_text=STORY, seed=7):
    return check_tap_word(
        draft,
        targets=targets() if ts is None else ts,
        stage_id=stage_id,
        story_text=story_text,
        sight_words=SIGHT,
        story_words=STORY_WORDS,
        seed=seed,
    )


def check_two(draft, stage_id=STAGE, story_text=STORY, seed=7):
    return check_mcq2(
        draft,
        stage_id=stage_id,
        story_text=story_text,
        sight_words=SIGHT,
        story_words=STORY_WORDS,
        seed=seed,
    )


# /w/ ("went") is what stage 3 TEACHES, so it is the only sound this story is
# offered — see `test_the_offered_sound_is_the_one_this_stage_teaches`. The
# fixture used to be /h/, which is a stage-2 sound: a perfectly valid question
# that earns the child no evidence toward promotion (§8), which is the defect
# `_prefer_current_stage` exists to fix.
GOOD_TAP = TapWordDraft(target_grapheme="w", correct_word="went", other_words=["ran", "man"])

# The rule tests below check `check_tap_word` as a pure function of the list it
# is HANDED — "is a distractor carrying the sound rejected?" is a different
# question from "which sound should be offered?". They hand it this one: /h/,
# older than stage 3, exactly as the fallback tier would produce.
H_TARGET = SoundTarget(
    grapheme="h",
    cue="had",
    onset_words=["hit", "hill"],
    clean_words=["ran", "man", "fast", "up", "back", "glad", "into", "it"],
    stage=2,
    scope="older_stage_fallback",
)
GOOD_MCQ = Mcq2Draft(
    prompt="Did the man run?",
    correct="The man ran back.",
    incorrect="The man ran up.",
)
# The real per-stage list — "one" only becomes a sight word at stage 6.
SIGHT_12 = [
    "a", "again", "all", "are", "because", "come", "go", "he", "her", "i", "is",
    "little", "me", "no", "oh", "one", "out", "people", "said", "she", "some",
    "the", "there", "they", "thought", "through", "to", "was", "we", "what",
    "when", "you", "your",
]


# --- prompt assembly ---------------------------------------------------------
def test_question_rules_are_frozen_of_request_detail():
    """Anything per-request in the rules invalidates the cached prefix on every
    call, silently destroying the per-stage cache (STATE invariant 9)."""
    for leak in ("dragon", "Stage 3", "hit", "{", "}"):
        assert leak not in QUESTION_RULES


def test_sound_block_offers_the_words_and_the_cue():
    block = build_sound_block(targets())
    target = targets()[0]
    assert f"as in {target.cue}" in block
    for word in target.onset_words + target.clean_words:
        assert word in block


def test_sound_block_says_so_when_no_sound_qualifies():
    """An empty list must read as "none", never as an empty menu the model
    might fill in itself."""
    assert "none" in build_sound_block([]).lower()


def test_request_block_carries_the_story_and_the_title():
    block = build_question_request(
        story_title=TITLE, story_text=STORY, targets=targets(), needed=["tap_word", "mcq2"]
    )
    assert TITLE in block and "A dragon was in a cave." in block
    assert "tap-a-word" in block and "two-choice" in block


def test_request_block_asks_only_for_what_is_still_missing():
    block = build_question_request(
        story_title=TITLE, story_text=STORY, targets=targets(), needed=["mcq2"]
    )
    assert "two-choice" in block and "tap-a-word" not in block.split("Write ")[1]


def test_only_readable_question_words_are_offered():
    """Live-observed: Haiku burns the whole retry budget on "What did ...?"
    because "what" is undecodable until stage 12. §0 says fix the prompt, not
    the gate — so the openers are filtered by the gate itself."""
    early = readable_openers(STAGE, SIGHT, STORY_WORDS)
    assert "Did" in early and "What" not in early
    assert "What" in readable_openers(12, SIGHT_12, STORY_WORDS)


def test_the_request_block_lists_the_readable_openers():
    block = build_question_request(
        story_title=TITLE,
        story_text=STORY,
        targets=targets(),
        needed=["mcq2"],
        openers=readable_openers(STAGE, SIGHT, STORY_WORDS),
    )
    assert "Did" in block and "What" not in block


def test_openers_are_omitted_when_only_tap_word_is_wanted():
    block = build_question_request(
        story_title=TITLE,
        story_text=STORY,
        targets=targets(),
        needed=["tap_word"],
        openers=["Did", "Was"],
    )
    assert "unreadable at this stage" not in block


def test_a_rejection_names_the_word_that_failed():
    """"try again" tells the model nothing it can act on (§6)."""
    _, problems = check_two(
        Mcq2Draft(
            prompt="What did the man do?",
            correct="The man ran back.",
            incorrect="The man ran up.",
        )
    )
    assert any("cannot read: " in p and "what" in p.split("cannot read: ")[1] for p in problems)


def test_retry_block_names_the_specific_problem():
    """§6's rewrite lesson: the model gets the violating detail, not "try again"."""
    block = build_question_request(
        story_title=TITLE,
        story_text=STORY,
        targets=targets(),
        needed=["mcq2"],
        problems=['"the sun was hot" is not what the story says'],
    )
    assert "is not what the story says" in block


# --- the closed sound list ---------------------------------------------------
def test_targets_are_derived_from_the_story():
    found = {t.grapheme for t in targets()}
    assert "w" in found  # "went" — the sound stage 3 teaches (§8, see below)
    for target in targets():
        assert target.onset_words
        assert len(target.clean_words) >= 2


def test_every_onset_word_really_starts_with_the_sound():
    from storyforge.decoder import mastered_ids

    for target in targets():
        for word in target.onset_words:
            assert word_graphemes(word, mastered_ids(STAGE))[0] == target.grapheme


def test_every_clean_word_really_avoids_the_sound():
    """This is the property that makes "exactly one correct answer" true."""
    from storyforge.decoder import mastered_ids

    for target in targets():
        for word in target.clean_words:
            assert target.grapheme not in word_graphemes(word, mastered_ids(STAGE))


def test_sight_and_story_words_are_never_offered():
    """The child has not sounded them out, so they carry no evidence about a
    grapheme — and a story word is not decodable by design (§5.5)."""
    offered = {w for t in targets() for w in t.onset_words + t.clean_words}
    assert not offered & set(SIGHT)
    assert not offered & set(STORY_WORDS)


def test_the_cue_word_is_never_one_of_the_story_words():
    """If the cue could also be an option, the question answers itself."""
    story_tokens = set(STORY.lower().replace(".", "").split())
    for target in targets():
        assert target.cue not in story_tokens


# --- the cue may name the sound and nothing else (DEVIATIONS 7.14) -----------
# Live row 4392, reproduced exactly: story 1671, stage 3, the `the`/`then`
# prefix. `the` is the shortest and commonest stage-3 word starting with `th`,
# so the old one-cue-per-grapheme index picked it for every one of the seven
# warm stories that offered `then` — and the question became "tap the word that
# starts with these three letters", answerable with no phonics at all, while §8
# credited `th` for the tap. `win`/`in` below is live row 696, the same defect
# pointing at a WRONG option.
LEAKY_STORY = (
    "A dragon sat in a cave. It had fire. Sam went to the cave. The dragon ran at Sam. "
    "Sam ran back fast. Then the dragon went into the cave. Sam did not go back."
)
# Frequency order as `allowed_words` really arrives for stage 3 — `the` first,
# which is what made this the DEFAULT outcome rather than an unlucky one.
STAGE3_ALLOWED = [
    "the", "to", "and", "a", "in", "for", "that", "it", "on", "with",
    "this", "at", "not", "but", "or", "an", "if", "can", "will", "just",
    "up", "had", "get", "them", "its", "than", "him", "into", "think", "then",
]


def leaky_targets():
    return sound_targets(
        story_text=LEAKY_STORY,
        stage_id=3,
        allowed_words=STAGE3_ALLOWED,
        sight_words=SIGHT,
        story_words=STORY_WORDS,
    )


def test_the_cue_word_never_points_at_an_option_by_shape():
    """The cue may share the target grapheme with an option and nothing else.

    Whole-word containment in either direction is a match a child settles by
    laying one word against the other, with no idea what `th` says — so it is
    an answer key, not a hint. Sharing the grapheme is the question itself.
    """
    offered = leaky_targets()
    assert offered, "story 1671 must still be able to ask something"
    for target in offered:
        assert cue_leaks(target.cue, target.onset_words + target.clean_words) == [], (
            f"cue {target.cue!r} for /{target.grapheme}/ gives an option away"
        )

    # And specifically the live row: `th` is still askable, and `the` is not how.
    th = next((t for t in offered if t.grapheme == "th"), None)
    assert th is not None, "dropping the sound is not the fix — re-cueing it is"
    assert "then" in th.onset_words
    assert th.cue != "the"


def test_a_cue_that_contains_an_option_is_rejected_even_if_it_was_offered():
    """Live row 696: cue `win`, options `in`/`had`/`went`, answer `went`.

    `in` is a suffix of the cue, so the shape match points at a WRONG option and
    steers the child away from the word they were meant to sound out. A cue that
    identifies the wrong option is the same defect with the opposite sign, and
    `check_tap_word` must refuse it however the target was built — the narrowing
    in `sound_targets` is a menu, and a guarantee that only lives in the prompt
    is a suggestion.
    """
    story = "Sam had a hat. It went in. The man ran back."
    target = SoundTarget(
        grapheme="w",
        cue="win",
        onset_words=["went"],
        clean_words=["in", "had", "hat", "man", "ran", "back"],
        stage=3,
        scope="current_stage_onset",
    )
    question, problems = check_tap_word(
        TapWordDraft(target_grapheme="w", correct_word="went", other_words=["in", "had"]),
        targets=[target],
        stage_id=3,
        story_text=story,
        sight_words=SIGHT,
        story_words=STORY_WORDS,
        seed=7,
    )
    assert question is None
    assert any('contains the whole option "in"' in p for p in problems)

    # The same question with a cue that names only the sound is fine, which is
    # what makes the rejection above about the CUE and not about the words.
    clean = target.model_copy(update={"cue": "wet"})
    question, problems = check_tap_word(
        TapWordDraft(target_grapheme="w", correct_word="went", other_words=["had", "man"]),
        targets=[clean],
        stage_id=3,
        story_text=story,
        sight_words=SIGHT,
        story_words=STORY_WORDS,
        seed=7,
    )
    assert problems == [] and question is not None
    assert question.payload["cue_word"] == "wet"


def test_a_conflicting_cue_costs_a_WORD_and_not_the_whole_SOUND():
    """The offer list is ours to narrow, so one collision is not a lost sound.

    Measured over the 52 servable stories in the live pool: every one still
    offers at least one sound after 7.14, and the total offered targets moves
    223 -> 222. The rule is free at production vocabulary sizes; dropping the
    sound outright would not have been.
    """
    # `with` collides with the clean word `it` and with nothing else, so /w/
    # survives with `it` off the menu rather than disappearing.
    w = next(t for t in targets() if t.grapheme == "w")
    assert w.cue == "with"
    assert "it" not in w.clean_words
    assert len(w.clean_words) >= 2
    assert "went" in w.onset_words


def test_targets_are_deterministic():
    assert [t.model_dump() for t in targets()] == [t.model_dump() for t in targets()]


def test_a_story_with_no_usable_sound_offers_nothing():
    """Then the node returns fewer questions rather than inventing one."""
    assert (
        sound_targets(
            story_text="The dragon was in the cave.",
            stage_id=STAGE,
            allowed_words=ALLOWED,
            sight_words=SIGHT,
            story_words=STORY_WORDS,
        )
        == []
    )


# --- §8: the target must be a sound this stage teaches -----------------------
# Live failure this section exists for: stage-4 and stage-5 warm stories asked
# about `fast`, `ran` and `held`. Every one is a valid question and none of them
# contains a stage-4 or stage-5 GPC, so a reader could complete every story in
# the pool and accumulate zero evidence toward promotion (§8 measures accuracy
# over the GPCs introduced AT the current stage, and only `tap_word` counts).

# Stage 4 teaches ONLY final suffixes — ed, es, ing, s|Z — so no word can start
# with one and the onset tier is structurally impossible there.
STAGE4_STORY = "The man went up. He jumped fast. A big dog runs and digs."


def stage4_targets():
    return sound_targets(
        story_text=STAGE4_STORY, stage_id=4, allowed_words=ALLOWED, sight_words=SIGHT
    )


def test_the_offered_sound_is_the_one_this_stage_teaches():
    """Tier 1: a grapheme introduced at the story's own stage, or nothing."""
    offered = targets()
    assert offered, "stage 3 teaches /w/ and the story says 'went'"
    for target in offered:
        assert target.stage == STAGE
        assert target.scope == "current_stage_onset"
    # And the older sounds the story could ask about are no longer on the menu.
    assert "h" not in {t.grapheme for t in offered}


def test_the_correct_word_earns_evidence_at_the_stage_that_teaches_suffixes():
    """Tier 2, and the only tier that can ever fire at stage 4.

    No word STARTS with `ed`/`ing`/`s|Z`, so the onset tier is empty by
    construction. The correct word must therefore be one that uses a stage-4
    sound somewhere — `jumped`, `runs` — or the child reads a question that
    teaches nothing new.
    """
    offered = stage4_targets()
    assert offered, "the stage-4 story must still offer a question"
    for target in offered:
        assert target.scope == "current_stage_word"
        assert target.onset_words
        for word in target.onset_words:
            assert uses_stage_gpc(word, 4), word


def test_a_correct_word_that_earns_no_evidence_is_rejected_at_that_tier():
    """The scope is ENFORCED, not merely offered. `went` is a fine tap-a-word
    answer and contains no stage-4 GPC, so at this tier it is not one."""
    target = next(t for t in stage4_targets() if t.grapheme == "j")
    question, problems = check_tap_word(
        TapWordDraft(target_grapheme="j", correct_word="jumped", other_words=["went", "fast"]),
        targets=[target],
        stage_id=4,
        story_text=STAGE4_STORY,
        sight_words=SIGHT,
    )
    assert problems == [] and question is not None

    # Same sound, a word that earns nothing.
    swapped = target.model_copy(update={"onset_words": ["jumped", "went"]})
    question, problems = check_tap_word(
        TapWordDraft(target_grapheme="j", correct_word="went", other_words=["up", "man"]),
        targets=[swapped],
        stage_id=4,
        story_text=STAGE4_STORY,
        sight_words=SIGHT,
    )
    assert question is None
    assert any("teaches nothing new" in p for p in problems)


def test_the_fallback_still_asks_a_question_and_says_that_it_did():
    """Tier 3. A story that supports neither is not left with no question —
    §14 is told the fallback fired instead."""
    offered = sound_targets(
        story_text=STORY, stage_id=5, allowed_words=ALLOWED, sight_words=SIGHT,
        story_words=STORY_WORDS,
    )
    assert offered, "stage 5 must still get a phonics question"
    for target in offered:
        assert target.scope == "older_stage_fallback"
        assert target.stage < 5


async def test_the_node_reports_which_tier_produced_the_target(monkeypatch):
    result, _ = await _run(monkeypatch, [QuestionDraft(tap_word=GOOD_TAP, mcq2=GOOD_MCQ)])
    # Still exact equality rather than a subset check: the expected literal gained
    # `source` and `model_dropped` because DEVIATIONS 8.54 requires that "the model
    # wrote this" and "we composed it after the model failed" be distinguishable in
    # `gen_meta`. So the assertion is widened in COVERAGE, not loosened in form —
    # and `source == "model"` is the fact this case is about on the new axis: the
    # first attempt passed, so nothing was composed locally.
    assert result.tap_word_target == {
        "grapheme": "w",
        "stage": 3,
        "scope": "current_stage_onset",
        "offered": 1,
        "stage_id": 3,
        "earns_stage_evidence": True,
        "source": "model",
        "model_dropped": [],
    }
    assert result.derived == []


async def test_a_story_with_no_target_reports_none_available(monkeypatch):
    only = Mcq2Only(
        mcq2=Mcq2Draft(
            prompt="Was it in the cave?",
            correct="The dragon was in the cave.",
            incorrect="The cave was in the dragon.",
        )
    )
    result, _ = await _run(monkeypatch, [only], story_text="The dragon was in the cave.")
    assert result.tap_word_target["scope"] == "none_available"
    assert result.tap_word_target["offered"] == 0


# --- the prompt frame ladder -------------------------------------------------
def test_the_richest_readable_frame_wins():
    prompt = tap_word_prompt("had", 12, SIGHT_12, STORY_WORDS)
    assert prompt is not None
    assert prompt != PROMPT_FRAMES[-1].format(cue="had")
    assert prompt in [f.format(cue="had") for f in PROMPT_FRAMES]


def test_early_stages_fall_back_to_the_terse_frame():
    """No English instruction is decodable at stage 1. The gate holds and the
    sentence shortens — never the other way round.

    The literal used to be `"Tap: sat."` — `PROMPT_FRAMES[-1]`. That pinned the
    ladder's CONTENTS, which the §11.3 fix necessarily changes, and it never
    asserted the docstring's actual claim: that the shortening is FORCED by the
    decoder rather than chosen. Both are asserted now, and the second one is
    what a future edit must not be able to sneak past — lowering the gate to let
    a richer frame through at stage 1 would fail here even if the floor string
    were updated to match.
    """
    stage_one = ["a", "i", "the"]
    assert tap_word_prompt("sat", 1, stage_one) == "Tap it: sat."

    chosen = tap_word_prompt("sat", 1, stage_one)
    richer = PROMPT_FRAMES[: [f.format(cue="sat") for f in PROMPT_FRAMES].index(chosen)]
    assert richer, "the stage-1 floor must not be the richest frame"
    for frame in richer:
        text = frame.format(cue="sat")
        assert not passes_gate(readable(text, 1, stage_one)), (
            f"{text!r} is claimed unreadable at stage 1 but passes the gate — "
            "either the ladder or the gate moved"
        )


def test_every_frame_in_the_ladder_is_reachable_at_some_stage():
    """A frame no stage can read is dead code, and dead code is how all 28 live
    prompts ended up as the bare cue `Tap: get.` — three of the four rungs
    contained "word" or "one", which are undecodable/not-yet-sight-words at
    every stage the ladder is used at, so only the floor ever fired.

    Reachability is checked against the REAL sight-word ladder, cumulative per
    stage, with a cue that is decodable everywhere.
    """
    sight_by_stage = {
        1: ["a", "i", "the"], 2: ["go", "is", "no", "to"], 3: ["he", "me", "she", "was", "we"],
        4: ["said", "they", "you"], 5: ["all", "are", "her"], 6: ["come", "some", "there"],
        7: ["little", "one", "when"], 8: ["oh", "out", "what"], 9: ["people", "your"],
        10: ["again", "because"], 11: ["thought", "through"], 12: [],
    }
    # `sat` is decodable at stage 1 and, the inventory being cumulative, at every
    # stage above it — so a rung that never fires here is unreachable for a
    # reason that has nothing to do with the cue.
    cue = "sat"
    reached = set()
    cumulative: list[str] = []
    for stage in range(1, 13):
        cumulative += sight_by_stage[stage]
        prompt = tap_word_prompt(cue, stage, list(cumulative))
        assert prompt is not None, f"stage {stage} can ask nothing at all"
        reached.add(next(f for f in PROMPT_FRAMES if f.format(cue=cue) == prompt))

    unreachable = [f for f in PROMPT_FRAMES if f not in reached]
    # The floor is allowed to be unreachable: it exists for the cue word that
    # breaks the rung above it, not for a stage.
    assert unreachable in ([], [PROMPT_FRAMES[-1]]), (
        f"dead rungs — no stage 1-12 can read {unreachable}"
    )
    # And the ladder must really be a ladder: more than one rung actually fires.
    assert len(reached) >= 4, f"only {len(reached)} distinct prompt(s) across 12 stages"


def test_the_ladder_names_the_cue_and_never_the_answer():
    """Every rung is a fixed frame plus the cue word, which `_cue_index` draws
    from the ALLOWED list and `check_tap_word` forbids as an option. A rung that
    mentioned the target grapheme would ship the answer key (DEVIATIONS 4.9)."""
    for frame in PROMPT_FRAMES:
        assert frame.count("{cue}") >= 1
        assert "{" not in frame.replace("{cue}", ""), f"{frame!r} interpolates something else"


def test_an_unreadable_cue_yields_no_prompt_at_all():
    assert tap_word_prompt("beautiful", 1, ["a", "i", "the"]) is None


# --- tap_word verification ---------------------------------------------------
def test_a_correct_tap_word_question_is_accepted():
    question, problems = check_tap(GOOD_TAP)
    assert problems == []
    assert question.qtype == "tap_word"
    assert sorted(question.payload["options"]) == ["man", "ran", "went"]
    assert question.answer["correct"] == "went"
    assert question.payload["options"][question.answer["index"]] == "went"
    # The grapheme is ANSWER-KEY material, not payload. This assertion used to
    # read `question.payload["target_grapheme"] == "h"`, which encoded the defect
    # as the contract: `check_tap_word` proves exactly one option starts with the
    # target grapheme, so a served grapheme IS the answer — solving 9/9 live rows
    # by `[o for o in options if o.startswith(g)][0]` — and §9 / DEVIATIONS 4.9
    # say the key never reaches the client.
    assert question.answer["target_grapheme"] == "w"
    assert "target_grapheme" not in question.payload
    # `cue_word` stays: it comes from the ALLOWED list and is rejected as an
    # option, so it names the sound without naming the answer.
    assert question.payload["cue_word"] not in question.payload["options"]


def test_a_claimed_answer_that_does_not_start_with_the_sound_is_rejected():
    """The model saying so is not evidence — the GPC alignment is."""
    question, problems = check_tap(
        TapWordDraft(target_grapheme="h", correct_word="ran", other_words=["man", "it"]),
        ts=[H_TARGET],
    )
    assert question is None
    assert any("does not start with h" in p for p in problems)


def test_a_distractor_carrying_the_sound_is_rejected():
    """Two options with /h/ means two defensible answers."""
    question, problems = check_tap(
        TapWordDraft(target_grapheme="h", correct_word="hit", other_words=["hill", "man"]),
        ts=[H_TARGET],
    )
    assert question is None
    assert any("also uses h" in p for p in problems)


def test_a_word_that_is_not_in_the_story_is_rejected():
    """§11.3: the options are drawn from the story the child just read."""
    question, problems = check_tap(
        TapWordDraft(target_grapheme="h", correct_word="hit", other_words=["ran", "camp"]),
        ts=[H_TARGET],
    )
    assert question is None
    assert any("not in the story" in p for p in problems)


def test_repeated_options_are_rejected():
    question, problems = check_tap(
        TapWordDraft(target_grapheme="h", correct_word="hit", other_words=["ran", "ran"]),
        ts=[H_TARGET],
    )
    assert question is None
    assert any("different" in p for p in problems)


def test_an_undecodable_option_is_rejected():
    """The gate is never lowered for a question (§0). "night" needs `igh`,
    which is not taught at stage 3."""
    story = STORY + " The man saw a night."
    question, problems = check_tap(
        TapWordDraft(target_grapheme="h", correct_word="hit", other_words=["ran", "night"]),
        ts=[H_TARGET],
        story_text=story,
    )
    assert question is None
    assert any("not decodable" in p for p in problems)


def test_a_sound_that_was_not_offered_is_rejected():
    question, problems = check_tap(
        TapWordDraft(target_grapheme="sh", correct_word="hit", other_words=["ran", "man"])
    )
    assert question is None
    assert any("not one of the sounds offered" in p for p in problems)


def test_the_cue_word_cannot_also_be_an_option():
    story = STORY + f" The man ran to a {H_TARGET.cue}."
    question, problems = check_tap(
        TapWordDraft(
            target_grapheme="h", correct_word="hit", other_words=["ran", H_TARGET.cue]
        ),
        ts=[H_TARGET],
        story_text=story,
    )
    assert question is None
    assert any("example word" in p for p in problems)


def test_an_option_that_stands_out_by_length_is_rejected():
    """A child who spots the odd one out by shape has not read anything."""
    story = "The man ran up the hill. He went to stand on it."
    question, problems = check_tap(
        TapWordDraft(target_grapheme="s", correct_word="stand", other_words=["up", "man"]),
        ts=[
            SoundTarget(
                grapheme="s",
                cue="sit",
                onset_words=["stand"],
                clean_words=["up", "man", "ran", "hill"],
                stage=1,
                scope="older_stage_fallback",
            )
        ],
        story_text=story,
    )
    assert question is None
    assert any("same length" in p for p in problems)


def test_a_multi_word_option_is_rejected():
    question, problems = check_tap(
        TapWordDraft(target_grapheme="h", correct_word="hit", other_words=["ran", "the man"]),
        ts=[H_TARGET],
    )
    assert question is None
    assert any("single word" in p for p in problems)


# --- mcq2 verification -------------------------------------------------------
def test_a_correct_mcq2_is_accepted():
    question, problems = check_two(GOOD_MCQ)
    assert problems == []
    assert question.qtype == "mcq2"
    assert len(question.payload["options"]) == 2
    assert question.answer["correct"] == "The man ran back."
    assert question.payload["options"][question.answer["index"]] == "The man ran back."


def test_story_supports_requires_every_word():
    assert not story_supports("The man ran up the tree.", STORY)


def test_story_supports_requires_the_story_order():
    """Same words, different claim. This is what makes "correct" decidable."""
    story = "Sam sat on the cat."
    assert story_supports("Sam sat.", story)
    assert not story_supports("The cat sat on Sam.", story)


def test_an_unsupported_true_answer_is_rejected():
    question, problems = check_two(
        Mcq2Draft(
            prompt="Did the man run?",
            correct="The man ran up the hill.",
            incorrect="The man ran into it.",
        )
    )
    assert question is None
    assert any("is not what the story says" in p for p in problems)


def test_a_false_answer_the_story_actually_says_is_rejected():
    """If both options are supported, there is no wrong answer to pick."""
    question, problems = check_two(
        Mcq2Draft(
            prompt="Did the man run?",
            correct="The man ran back.",
            incorrect="The dragon went into the cave.",
        )
    )
    assert question is None
    assert any("also true in the story" in p for p in problems)


def test_an_undecodable_option_fails_the_same_gate_as_the_story():
    question, problems = check_two(
        Mcq2Draft(
            prompt="Did the man run?",
            correct="The man ran back.",
            incorrect="The man ran at night.",
        )
    )
    assert question is None
    assert any("cannot read" in p for p in problems)


def test_an_undecodable_prompt_is_rejected():
    """The prompt is child-facing text too — it is validated like story text."""
    question, problems = check_two(
        Mcq2Draft(
            prompt="What happened at night?",
            correct="The man ran back.",
            incorrect="The man ran into it.",
        )
    )
    assert question is None
    assert any("cannot read" in p for p in problems)


def test_a_prompt_that_is_not_a_question_is_rejected():
    question, problems = check_two(
        Mcq2Draft(
            prompt="Did the man run",
            correct="The man ran back.",
            incorrect="The man ran into it.",
        )
    )
    assert question is None
    assert any("question mark" in p for p in problems)


def test_identical_options_are_rejected():
    question, problems = check_two(
        Mcq2Draft(
            prompt="Did the man run?",
            correct="The man ran back.",
            incorrect="The man ran back.",
        )
    )
    assert question is None
    assert any("different" in p for p in problems)


def test_an_mcq2_option_that_stands_out_by_length_is_rejected():
    question, problems = check_two(
        Mcq2Draft(
            prompt="Did the man run?",
            correct="The man ran back.",
            incorrect="It went up.",
        )
    )
    assert question is None
    assert any("same length" in p for p in problems)


def test_a_false_answer_with_no_story_words_is_rejected():
    """An option built from alien vocabulary is guessable without reading."""
    question, problems = check_two(
        Mcq2Draft(
            prompt="Did the man run?",
            correct="The man ran back.",
            incorrect="Sam had a big red van.",
        )
    )
    assert question is None
    assert any("shares no words" in p for p in problems)


# --- deterministic shuffling -------------------------------------------------
def test_shuffle_is_stable_for_a_seed():
    a = shuffle_options(["hit", "ran", "man"], "hit", 42, "tap_word:h")
    b = shuffle_options(["hit", "ran", "man"], "hit", 42, "tap_word:h")
    assert a == b


def test_shuffle_ignores_the_order_the_model_emitted():
    """The model always lists the answer first. Sorting before the shuffle is
    what stops that from surviving into the payload."""
    a, _ = shuffle_options(["hit", "ran", "man"], "hit", 42, "tap_word:h")
    b, _ = shuffle_options(["man", "hit", "ran"], "hit", 42, "tap_word:h")
    assert a == b


def test_shuffle_index_follows_the_correct_answer():
    for seed in range(20):
        options, index = shuffle_options(["hit", "ran", "man"], "hit", seed, "tap_word:h")
        assert options[index] == "hit"


def test_the_answer_does_not_sit_in_one_position():
    """Position must carry no information across stories."""
    positions = {
        shuffle_options(["hit", "ran", "man"], "hit", seed, "tap_word:h")[1]
        for seed in range(30)
    }
    assert len(positions) == 3


def test_the_two_question_types_shuffle_independently():
    """A shared seed must not correlate the two answers' positions."""
    tap = {shuffle_options(["a1", "b1", "c1"], "a1", s, "tap_word:h")[1] for s in range(30)}
    mcq = {shuffle_options(["a1", "b1"], "a1", s, "mcq2")[1] for s in range(30)}
    assert len(tap) == 3 and len(mcq) == 2


# --- the node's retry and drop policy ----------------------------------------
def _stub(responses):
    """Replace the API call with a scripted sequence of parsed outputs."""
    calls = []

    async def fake_call(**kwargs):
        calls.append(kwargs)
        parsed = responses[min(len(calls) - 1, len(responses) - 1)]
        return parsed, Usage(model="claude-haiku-4-5", input_tokens=10, output_tokens=5), "end_turn"

    return fake_call, calls


async def _run(monkeypatch, responses, **overrides):
    from storyforge.llm import questions as mod

    fake_call, calls = _stub(responses)
    monkeypatch.setattr(mod, "_call", fake_call)
    kwargs = {
        "stage_id": STAGE,
        "stage_name": "Stage 3",
        "story_title": TITLE,
        "story_text": STORY,
        "allowed_words": ALLOWED,
        "sight_words": SIGHT,
        "story_words": STORY_WORDS,
        "seed": 7,
    }
    kwargs.update(overrides)
    return await generate_questions(**kwargs), calls


BAD_TAP = TapWordDraft(target_grapheme="w", correct_word="ran", other_words=["man", "it"])


async def test_both_questions_pass_on_the_first_attempt(monkeypatch):
    result, calls = await _run(monkeypatch, [QuestionDraft(tap_word=GOOD_TAP, mcq2=GOOD_MCQ)])
    assert result.attempts == 1 and len(calls) == 1
    assert [q.qtype for q in result.questions] == ["tap_word", "mcq2"]
    assert [q.idx for q in result.questions] == [0, 1]
    assert result.dropped == {}


async def test_a_tap_word_the_model_never_gets_right_is_composed_here_instead(monkeypatch):
    """Retries are bounded (§7), and the model's wrong draft is never accepted.

    This test used to be `test_a_question_that_never_validates_is_dropped`, and it
    asserted `[q.qtype for q in result.questions] == ["mcq2"]` — that a model which
    never writes a valid `tap_word` leaves the story without one. **That contract
    was deliberately replaced** (DEVIATIONS 8.54), because it is what six live
    `warm` stories were: a child reading to the end and getting no question screen,
    and so no §8 evidence at all. `tap_word` is the only mastery-bearing type and
    the one type whose text no model writes — the prompt is a frame from
    `PROMPT_FRAMES`, every option is a story word already through the gate and §13
    with the body, and `LAYER3_EXEMPT_QTYPES` has recorded that since Phase 4. So
    when the budget is spent, this module composes the triple from the same closed
    list and submits it to the same judge.

    The two propositions the old assertion carried are both kept and one is
    strengthened. "Retries are bounded" is unchanged. "Fewer questions beats a
    wrong one" is now asserted *directly* — the surviving question is checked not
    to be the model's rejected draft — rather than inferred from the absence of a
    question, which is the weaker form. What is NOT relaxed anywhere:
    `check_tap_word` judges the composed draft exactly as it judged the model's,
    and `test_a_never_valid_mcq2_is_still_dropped` below holds the original
    proposition on the type that has no fallback.
    """
    result, calls = await _run(monkeypatch, [QuestionDraft(tap_word=BAD_TAP, mcq2=GOOD_MCQ)])
    assert len(calls) == 3  # one generation plus two retries, then stop

    assert [q.qtype for q in result.questions] == ["tap_word", "mcq2"]
    assert [q.idx for q in result.questions] == [0, 1]

    # The model's draft was refused, and what survived is not it. BAD_TAP claims
    # `ran` starts with `w`; the accepted question's answer must be a word that
    # genuinely does.
    tap = result.questions[0]
    assert tap.answer["correct"] != BAD_TAP.correct_word
    assert tap.answer["correct"].startswith(tap.answer["target_grapheme"])
    assert set(BAD_TAP.other_words).isdisjoint(tap.payload["options"])

    # Composed locally, and the model's failure is preserved rather than erased by
    # the recovery — §14 measures the prompt from this, not from the outcome.
    assert result.derived == ["tap_word"]
    assert result.tap_word_target["source"] == "derived"
    assert any("does not start with w" in p for p in result.tap_word_target["model_dropped"])
    # `dropped` reports what the story ENDED UP without, and it did not end up
    # without a tap-a-word question.
    assert "tap_word" not in result.dropped


async def test_a_never_valid_mcq2_is_still_dropped(monkeypatch):
    """"Fewer questions beats a wrong one", on the type with no fallback.

    The proposition the previous test used to carry, kept where it is still
    reachable. `mcq2` is model-authored prose — there is no closed list to compose
    it from and no pure function that could prove a synthesised one true — so a
    draft that never validates is dropped, exactly as before, and the story is
    served with one question rather than two.
    """
    bad_mcq = Mcq2Draft(
        prompt="Did the dragon nap?",
        # Not supported by the story in the story's own word order, so
        # `story_supports` refuses it however many times it is offered.
        correct="The cave sat on the dragon.",
        incorrect="The dragon sat in the cave.",
    )
    result, calls = await _run(monkeypatch, [QuestionDraft(tap_word=GOOD_TAP, mcq2=bad_mcq)])
    assert len(calls) == 3
    assert [q.qtype for q in result.questions] == ["tap_word"]
    assert "mcq2" in result.dropped
    assert result.derived == []


async def test_an_accepted_question_survives_the_retry(monkeypatch):
    """The retry asks only for the failing type, and does not re-litigate the
    one that already passed."""
    result, calls = await _run(
        monkeypatch,
        [
            QuestionDraft(tap_word=BAD_TAP, mcq2=GOOD_MCQ),
            TapWordOnly(tap_word=GOOD_TAP),
        ],
    )
    assert len(calls) == 2
    assert calls[1]["schema"] is TapWordOnly
    assert [q.qtype for q in result.questions] == ["tap_word", "mcq2"]


async def test_the_retry_prompt_carries_the_rejection_reason(monkeypatch):
    _, calls = await _run(
        monkeypatch,
        [QuestionDraft(tap_word=BAD_TAP, mcq2=GOOD_MCQ), TapWordOnly(tap_word=GOOD_TAP)],
    )
    assert "does not start with w" in calls[1]["user_block"]


async def test_a_story_with_no_usable_sound_skips_tap_word_entirely(monkeypatch):
    """No wasted API attempt on a question that cannot exist."""
    plain = "The dragon was in the cave."
    only = Mcq2Only(
        mcq2=Mcq2Draft(
            prompt="Was it in the cave?",
            correct="The dragon was in the cave.",
            incorrect="The cave was in the dragon.",
        )
    )
    result, calls = await _run(monkeypatch, [only], story_text=plain)
    assert calls[0]["schema"] is Mcq2Only
    assert "tap_word" in result.dropped
    assert [q.qtype for q in result.questions] == ["mcq2"]


async def test_usage_is_summed_across_attempts(monkeypatch):
    result, _ = await _run(monkeypatch, [QuestionDraft(tap_word=BAD_TAP, mcq2=GOOD_MCQ)])
    assert result.usage.input_tokens == 30  # 3 attempts x 10
    assert result.usage.output_tokens == 15


async def test_an_unparseable_response_fails_closed(monkeypatch):
    """§7: a schema-parse failure is an api_fail, never a silent pass.

    `assert result.questions == []` was the old form. Nothing about "fails closed"
    has been relaxed — no byte of the model's output is used, on any attempt — but
    the `tap_word` is now composed locally (DEVIATIONS 8.54), so an empty list is
    no longer the way to say it. The assertions below say the thing itself: the
    parse failure is recorded for BOTH types, the model authored nothing that
    survived, and the one question that exists was written here.

    The loudness this could have cost is closed in `warm.enrichment_problems`, not
    here: a story whose every surviving question was composed locally still fails a
    `make warm` run, because that is the signature of a model returning nothing
    usable. See `test_dead_end_stories.py::
    test_a_story_whose_every_question_was_composed_locally_still_fails_the_run`.
    """
    result, _ = await _run(monkeypatch, [None])

    # Both types recorded the parse failure — that is the "fails closed" claim.
    assert any(
        "could not be read" in p for p in result.tap_word_target["model_dropped"]
    ), result.tap_word_target
    assert "mcq2" in result.dropped
    assert any("could not be read" in p for p in result.dropped["mcq2"])

    # Nothing the model returned became a question, and the survivor is ours.
    assert result.derived == ["tap_word"]
    assert result.tap_word_target["source"] == "derived"
    assert [q.qtype for q in result.questions] == ["tap_word"]


async def test_the_node_is_reproducible_for_a_seed(monkeypatch):
    first, _ = await _run(monkeypatch, [QuestionDraft(tap_word=GOOD_TAP, mcq2=GOOD_MCQ)])
    second, _ = await _run(monkeypatch, [QuestionDraft(tap_word=GOOD_TAP, mcq2=GOOD_MCQ)])
    assert [q.model_dump() for q in first.questions] == [q.model_dump() for q in second.questions]


async def test_a_different_seed_reorders_the_options(monkeypatch):
    seen = set()
    for seed in range(12):
        result, _ = await _run(
            monkeypatch, [QuestionDraft(tap_word=GOOD_TAP, mcq2=GOOD_MCQ)], seed=seed
        )
        seen.add(tuple(result.questions[0].payload["options"]))
    assert len(seen) > 1


def test_generated_question_shape_matches_the_questions_table():
    """models.Question stores prompt / payload / answer as written here.

    The split is the child-safety line: `payload` is served verbatim to kid mode,
    `answer` never leaves the server (§9, DEVIATIONS 4.9). This assertion
    previously expected `target_grapheme` in `payload` — see
    `test_a_correct_tap_word_question_is_accepted` for why that was the defect.
    """
    question, _ = check_tap(GOOD_TAP)
    assert isinstance(question, GeneratedQuestion)
    assert set(question.payload) == {"options", "cue_word"}
    assert set(question.answer) == {"correct", "index", "target_grapheme"}


def test_no_served_payload_field_identifies_the_correct_option():
    """The property, not the field list: nothing in `payload` picks the answer.

    A field-name assertion only catches the grapheme by name. This catches any
    future payload field that happens to single out the correct option.
    """
    question, _ = check_tap(GOOD_TAP)
    correct = question.answer["correct"]
    others = [o for o in question.payload["options"] if o != correct]
    for key, value in question.payload.items():
        if key == "options" or not isinstance(value, str):
            continue
        singles_out = correct.startswith(value) and not any(o.startswith(value) for o in others)
        assert not singles_out, f"payload[{key!r}]={value!r} identifies the answer"


@pytest.mark.parametrize("qtype", ["tap_word", "mcq2"])
def test_every_option_would_pass_the_pipeline_gate(qtype):
    """The invariant in one assertion: nothing ships that the story validator
    would have rejected."""
    from storyforge.llm.questions import readable
    from storyforge.pipeline import passes_gate

    question, _ = check_tap(GOOD_TAP) if qtype == "tap_word" else check_two(GOOD_MCQ)
    for text in [question.prompt, *question.payload["options"]]:
        assert passes_gate(readable(text, STAGE, SIGHT, STORY_WORDS))
