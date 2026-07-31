"""The curated CHARACTER NAMES allowlist (DEVIATIONS 32.1-32.7).

**The defect this is the evidence for was a CONTRADICTION INSIDE THE PROMPT, not a
missing word.** `SYSTEM_RULES` rule 1 says every word must come from the PERMITTED
WORDS, SIGHT WORDS or STORY WORDS list and that "there are no other options", while
rule 6's worked example of CORRECT output is `"Sam sat. A cat sat on Sam."`
`common_forms()` filters proper nouns out of `allowed_words` by design (11.9) and
nothing ever put a name back, so a name was permitted at ZERO stages while the
prompt demonstrated one. The model obeyed the example: `sam`/`tom`/`dan` were
unlicensed words on 14 of 106 servable stories, and **~10% of newly generated
servable stories carried a fresh name offence** — an unbounded source term.

Two things are being widened here and both are dangerous directions, so every test
below is about what the widening must NOT do rather than about the names being nice
to have:

* the PROPER-NOUN filter, which 11.9 exists to keep narrow (`nixon`, `nasa`,
  `lebron`);
* the PROMPT, where a name that is permitted but never shown to the model is
  DEVIATIONS 31.5's caveat repeated — and where the tempting fix, raising
  `PROMPT_WORD_LIMIT`, grows and invalidates every per-stage cached prefix
  (STATE invariant 10).

The pure half needs no database. The table half is marked and needs `make
precompute` to have run, for the reason `test_phase2.py` gives: a precompute test
that passes against an empty table is evidence of nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.decoder import MAX_STAGE, MIN_STAGE
from storyforge.llm.generate import (
    NAMES_HEADER,
    SYSTEM_RULES,
    build_names_block,
    build_vocabulary_block,
    character_names,
)
from storyforge.pipeline import PROMPT_WORD_LIMIT, decodable_from_stage
from storyforge.precompute import (
    _read_word_list,
    blocked_forms,
    child_forms,
    inflect,
    name_allowlist,
)

#: The RATCHET on `data/child-name-allowlist.txt`, written out literally rather
#: than read from the file, because a guard that takes its expectation from the
#: thing it guards cannot notice the thing changing — the same construction
#: `test_the_local_additions_are_a_closed_deliberate_set` uses for 31.1.
#:
#: These are strings printed to five-year-olds, and every one of them is a hole in
#: a filter that 11.7-11.11 spent a whole phase narrowing. So a thirteenth name
#: costs an edit HERE as well as an edit to the data file, and this line is where a
#: reviewer is made to look at the whole set at once instead of at one added line
#: in a diff.
PERMITTED_NAMES = frozenset(
    {
        "amir",
        "ann",
        "ben",
        "beth",
        "dan",
        "hana",
        "jill",
        "meg",
        "pam",
        "sam",
        "tim",
        "tom",
    }
)


# --- §1: decodability, DERIVED and never assigned ----------------------------
def test_every_permitted_name_is_decodable_by_the_unchanged_inventory():
    """**Being a name is no argument for being decodable.**

    That is exactly the reasoning that would weaken §1's guarantee, and it is the
    single most likely way this file goes wrong: a name looks like a proper noun,
    proper nouns feel exempt from spelling rules, and one line here would put a
    word no child on this ladder can sound out into `allowed_words` at stage 1.

    `ruth`, `kai`, `leo`, `mia`, `raj`, `rosa` and `ali` are all ordinary first
    names that the unchanged 93-GPC inventory cannot align at ANY stage. None of
    them is in the file, and this is what says so.
    """
    undecodable = sorted(w for w in name_allowlist() if decodable_from_stage(w) is None)
    assert not undecodable, (
        f"{undecodable} cannot be aligned by the 93-GPC inventory at any stage, so "
        "permitting them puts a word no child here can read into a story's "
        "characters. The decodability gate is never weakened to admit a name "
        "(STATE invariant 1)."
    )


def test_no_name_is_offered_before_the_stage_that_can_decode_it():
    """The per-name minimum stage is READ OFF THE VALIDATOR, not hand-assigned.

    `character_names(stage)` is what the prompt offers. It must never name a name
    whose own `decodable_from_stage` is above that stage — a five-year-old at stage
    1 handed "Beth" has been handed `th`, which stage 2 is the first to teach.
    """
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        for name in character_names(stage):
            first = decodable_from_stage(name.lower())
            assert first is not None and first <= stage, (
                f"{name!r} is offered at stage {stage} but is first decodable at "
                f"{first} — the offer was assigned rather than derived"
            )


def test_the_offer_grows_with_the_ladder_and_never_shrinks():
    """The GPC inventory is cumulative, so the name offer must be monotone.

    A name that appears at stage 4 and is gone at stage 5 would mean something
    other than decodability is deciding the offer.
    """
    for stage in range(MIN_STAGE, MAX_STAGE):
        assert set(character_names(stage)) <= set(character_names(stage + 1))
    assert set(character_names(MAX_STAGE)) == {n.capitalize() for n in name_allowlist()}


def test_every_stage_has_at_least_one_name_to_offer():
    """§18's shape: a stage with nothing to offer is a stage where the rule cannot
    be obeyed, and a rule that cannot be obeyed is worse than no rule. Rule 1 now
    says "only from the CHARACTER NAMES list", so an empty list at any stage would
    be an instruction to write no named character at all."""
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        assert character_names(stage), f"stage {stage} offers no character name"


# --- §13: the second bar ------------------------------------------------------
def test_no_permitted_name_collides_with_the_blocklist():
    """Widening ONE filter must not tunnel under §5.4 layer 1.

    Both directions: the name itself, and every form `inflect()` would generate —
    the second because `name_allowlist()` is read literally TODAY and a future
    decision to inflect it must not be able to manufacture a blocked string
    silently.
    """
    direct = sorted(name_allowlist() & blocked_forms())
    assert not direct, f"{direct} is a blocklist entry or an inflection of one"
    for name in sorted(name_allowlist()):
        collisions = sorted(f for f in inflect(name) if f in blocked_forms())
        assert not collisions, (
            f"inflecting {name!r} generates {collisions}, which the blocklist "
            "refuses — a name must not be a route around §13's deterministic layer"
        )


def test_no_permitted_name_is_already_a_child_word():
    """Selection criterion 5, in its mechanical form.

    `bob`, `pat`, `rob`, `hop` and `wes` are Dale-Chall familiar words already in
    `allowed_words` as verbs and nouns. Making one a name would let the same string
    mean two things in one story for a child who is decoding word by word — and it
    would also make `--check-vocabulary`'s NAME/WRONG-STAGE classification, which
    reads "does this token ever appear lower case", answer for two populations at
    once.
    """
    collisions = sorted(name_allowlist() & child_forms())
    assert not collisions, (
        f"{collisions} are words this curriculum already offers with another "
        "meaning, so the string would be ambiguous for a decoding reader"
    )


def test_the_names_file_is_not_the_register_file_and_not_the_pinned_one():
    """Three files, three jobs, and the provenance argument 31.1 made.

    `data/child-vocabulary.txt` is a VERBATIM vendored copy, SHA-256 pinned in
    `decoder/data/PINS.md`; an edit there breaks the digest and is reverted by the
    next `scripts/vendor-word-lists.py`. `data/child-vocabulary-additions.txt`
    widens the REGISTER filter and is guarded by
    `test_the_additions_pass_every_other_filter_on_their_own_merits`, which
    requires every line to be a lowercase Hunspell headword — written precisely so
    the register list cannot tunnel a proper noun past 11.9. A name is neither.
    """
    assert not (name_allowlist() & frozenset(_read_word_list("child-vocabulary.txt"))), (
        "a name was written into the PINNED vendored Dale-Chall list — that breaks "
        "its digest and the next re-vendor silently drops it"
    )
    assert not (
        name_allowlist() & frozenset(_read_word_list("child-vocabulary-additions.txt"))
    ), (
        "a name was written into the register-additions file, which is guarded to "
        "hold lowercase Hunspell headwords only — the two widenings must stay "
        "separately enumerated or neither can be audited"
    )


def test_the_permitted_names_are_a_closed_deliberate_set():
    """The ratchet. A thirteenth name costs an edit HERE too."""
    assert name_allowlist() == PERMITTED_NAMES, (
        "data/child-name-allowlist.txt changed. Every line there is a string shown "
        "to a five-year-old AND a hole in the proper-noun filter 11.9 exists to "
        "keep narrow, so it is ratcheted: state the §13 reasoning in the file, then "
        "update PERMITTED_NAMES here."
    )


# --- the prompt: PERMITTED is not the same as OFFERED (31.5) -------------------
def test_rule_1_names_the_list_that_rule_6_demonstrates():
    """The contradiction 32.1 exists to remove, asserted as one statement.

    Rule 6's example is `"Sam sat. A cat sat on Sam."` and rule 1 used to say the
    only sources were three lists, none of which could ever contain `Sam`. If a
    future edit deletes the CHARACTER NAMES clause from rule 1 while leaving rule
    6's example, the prompt goes back to contradicting itself and the generator
    goes back to inventing names — silently, because nothing else reads the prompt.

    **Sliced PER RULE, and that is what the sabotage run forced.** The first version
    asserted `NAMES_HEADER in SYSTEM_RULES`, which is satisfied by rule 6's own
    back-reference — so deleting the whole clause from rule 1 left this GREEN. A
    test that cannot tell which rule carries the licence cannot see the
    contradiction it exists to prevent.
    """
    rule_1 = SYSTEM_RULES.split("\n1. ", 1)[1].split("\n2. ", 1)[0]
    rule_6 = SYSTEM_RULES.split("\n6. ", 1)[1].split("\n7. ", 1)[0]

    # The ENUMERATION, not the rule as a whole: rule 1 goes on to explain itself,
    # and a mention in the explanation while the closed list of sources omits the
    # names is the contradiction wearing a different coat. The sabotage run found
    # exactly that — deleting `CHARACTER NAMES` from the enumeration left the
    # first version of this test green on the strength of the paragraph below it.
    assert "There are no other options." in rule_1, (
        "rule 1's closure clause is what makes the enumeration exhaustive; without "
        "it every list in it is a suggestion"
    )
    enumeration = rule_1.split("There are no other options.")[0]
    for source in ("PERMITTED WORDS", "SIGHT WORDS", NAMES_HEADER, "STORY WORDS"):
        assert source in enumeration, (
            f"rule 1's closed enumeration of word sources no longer includes "
            f"{source!r}, so a word from it is forbidden by the rule that is "
            "supposed to license it (32.1)"
        )
    assert "Sam sat." in rule_6, "rule 6's worked example moved"
    assert NAMES_HEADER in rule_6, (
        "RULE 6 no longer points at the list that legalises its own example, so the "
        "next reader has to rediscover why a name is allowed there"
    )
    assert "sam" in name_allowlist(), (
        "rule 6 demonstrates a name that the allowlist does not license — the exact "
        "contradiction this whole change exists to close"
    )


def test_the_names_reach_the_prompt_without_raising_the_word_limit():
    """**The half DEVIATIONS 31.5 says is missing, and the way it must NOT be fixed.**

    `load_vocabulary` sends `ORDER BY freq_rank LIMIT PROMPT_WORD_LIMIT`, so a word
    permitted at a stage is not thereby OFFERED at it: measured, the 400th-ranked
    word is rank 6,442 at stage 1 but 784 at stage 11, and no name is anywhere near
    that. The fixture below is that window — 400 common words, no name among them —
    and the block must still print the names.

    The limit itself is asserted UNCHANGED. Raising it is the obvious fix and it is
    the wrong one: `PROMPT_WORD_LIMIT` sizes every per-stage cached prefix, so
    raising it grows and invalidates all eleven of them permanently to make room
    for twelve strings (STATE invariant 10).
    """
    assert PROMPT_WORD_LIMIT == 400, (
        "PROMPT_WORD_LIMIT moved. If it moved to make room for the names, that is "
        "the fix invariant 10 forbids — the names have their own section precisely "
        "so the window does not have to grow."
    )
    window = ["cat", "sat", "mat", "run", "big", "red", "top", "hat"] * 50
    assert len(window) == PROMPT_WORD_LIMIT
    assert not (set(window) & name_allowlist()), "fixture must contain no name"

    block = build_vocabulary_block("Stage 1", window, ["the", "a"])
    assert NAMES_HEADER in block
    for name in character_names(1):
        assert name in block, f"{name} is permitted at stage 1 and never offered"


def test_a_name_is_printed_once_and_capitalised():
    """Rule 2 says "never inflect or MODIFY a permitted word" and rule 1 now
    requires the capital letter. Printing `sam` in the frequency-ordered permitted
    list AND `Sam` in the names section would put those two rules in direct
    conflict and hand the model a licensed lowercase spelling of a name."""
    block = build_vocabulary_block("Stage 1", ["cat", "sam", "sat", "tom"], ["the"])
    assert "Sam" in block and "Tom" in block
    head = block.split(NAMES_HEADER)[0]
    listed = head.split(":\n", 1)[1].strip().split(", ")
    assert listed == ["cat", "sat"], (
        f"PERMITTED WORDS printed {listed}: a name is either still being offered "
        "lowercase there, or a non-name was dropped from the offer"
    )
    assert "PERMITTED WORDS (2)" in head, "the printed count must match the offer"


def test_the_names_section_is_a_rule_and_not_a_preference():
    """The deliberate difference from `build_practice_block`, which IS a preference.

    "Prefer these names" would leave the model free to invent one, which is the
    entire defect. The section has to forbid, and it has to say that naming nobody
    is allowed — otherwise it reads as an instruction to name a character, which is
    how 9.40 deleted the theme from six stories.
    """
    block = build_names_block(character_names(1))
    assert "never" in block.lower()
    assert "optional" in block.lower() or "do not have to" in block.lower()
    assert build_names_block(()) == "", "no stage may print an empty names section"


# --- the table itself ---------------------------------------------------------
@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.mark.usefixtures("session")
async def test_a_permitted_name_reaches_every_stage_that_can_decode_it(session):
    """The TABLE, not the set. `name_allowlist()` being right proves nothing alone.

    `compute_rows` applies the blocklist, CMUdict, `align()` and the per-stage
    SYLLABLE CAP after the two filter exemptions, so a name can be exempted and
    still reach no stage — or reach one stage later than the validator says. This
    reads what `make precompute` actually wrote.
    """
    for name in sorted(name_allowlist()):
        first = decodable_from_stage(name)
        assert first is not None
        rows = await session.execute(
            text("SELECT stage_id FROM allowed_words WHERE word = :w ORDER BY 1"),
            {"w": name},
        )
        stages = [r[0] for r in rows]
        assert stages == list(range(first, MAX_STAGE + 1)), (
            f"{name!r} is decodable from stage {first} but `allowed_words` offers "
            f"it at {stages}. Run `make precompute`; if that does not fix it, the "
            "syllable cap or one of the two filters is dropping it and a story that "
            "obeys rule 1 by naming a character becomes a `--check-vocabulary` "
            "offence again."
        )


@pytest.mark.usefixtures("session")
async def test_every_offered_name_is_permitted_by_the_live_table(session):
    """**The two halves of 31.5's caveat, compared at every stage.**

    `character_names` is a pure function of the validator and cannot see the
    per-stage syllable cap; `allowed_words` is what `permitted_at` reads and what
    `--check-vocabulary` judges against. If they disagree, the prompt offers a name
    the pool will then refuse — a story generated in good faith becomes an offence.
    Asserted rather than argued, in both directions.
    """
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        rows = await session.execute(
            text(
                "SELECT word FROM allowed_words WHERE stage_id = :s AND word = ANY(:n)"
            ),
            {"s": stage, "n": sorted(name_allowlist())},
        )
        permitted = {r[0] for r in rows}
        offered = {n.lower() for n in character_names(stage)}
        assert offered == permitted, (
            f"stage {stage}: the prompt offers {sorted(offered - permitted)} that "
            f"the table does not permit, and the table permits "
            f"{sorted(permitted - offered)} that the prompt never mentions (31.5)"
        )
