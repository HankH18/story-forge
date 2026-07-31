"""The two vocabulary filters, proved in BOTH directions (DEVIATIONS 11.1-11.11).

Two defects are guarded here, and the pattern they share is the reason this file
exists: **both passed every previous check by measuring the wrong thing.**

`precompute.py` filtered with ``if word in blocked`` against a list of BASE forms,
so the exact-match hit count against the live `allowed_words` read **0** while 50
morphological variants of blocked entries sat in the table — `fucks`, `dicks`,
`asses`, `murders`, `killings`, `corpses`, `punching`, `attacking`, `addicts`,
`freaking`, `morons`, `hostages`. A test that asserts "no blocklist entry is in the
allowlist" is satisfied by that; only a test that asserts "no INFLECTION of a
blocklist entry is in the allowlist" catches it.

And §5.4's filter was adult film-subtitle frequency, which certifies that a word is
common in adult speech and says nothing about whether a five-year-old knows it.
`amid` (rank 7,528) was offered at stage 1. A row count cannot see that either.

Every test here is NEW. No existing assertion is modified — see 11.12.

The pure-function half needs no database. The table half is marked and needs
`make precompute` to have run, for the reason `test_phase2.py` gives: a precompute
test that passes against an empty table is evidence of nothing.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.decoder import MAX_STAGE
from storyforge.pipeline import decodable_from_stage
from storyforge.precompute import (
    DATA,
    FRAGMENT_SECTION,
    REGISTER_FILTER_FROM_STAGE,
    _read_word_list,
    blocked_forms,
    candidate_pool,
    child_additions,
    child_forms,
    common_forms,
    inflect,
    load_blocklist,
    load_blocklist_sections,
)

DALE_CHALL_LITERALS = frozenset(_read_word_list("child-vocabulary.txt"))

#: DEVIATIONS 31.1-31.5. The RATCHET on `data/child-vocabulary-additions.txt`,
#: written out literally rather than read from the file, because a guard that
#: takes its expectation from the thing it guards cannot notice the thing
#: changing (the same construction `test_the_register_filter_is_applied_where_it
#: _is_configured` uses for `REGISTER_FILTER_FROM_STAGE`).
#:
#: Widening the register filter is the one direction 11.7-11.11 spent a whole
#: phase narrowing, so it costs an edit HERE as well as an edit to the data file —
#: and this line is where a reviewer is forced to look at the whole set at once
#: instead of at one added line in a diff.
LOCAL_ADDITIONS = frozenset({"dragon", "planet"})

# The 50 variants the sweep found in the live `allowed_words`, minus the four the
# mandate flagged as false alarms of its own crude suffixing (11.4). Every one of
# these is a genuine inflection of an entry on `data/blocklist.txt`.
BLOCKED_VARIANTS = (
    "fucks",
    "fuckers",
    "dicks",
    "cocks",
    "asses",
    "butts",
    "murders",
    "murderers",
    "murdering",
    "killers",
    "killings",
    "corpses",
    "pisses",
    "pissing",
    "punches",
    "punching",
    "attacks",
    "attackers",
    "attacking",
    "addicts",
    "addicted",
    "freaks",
    "freaking",
    "freaked",
    "morons",
    "hostages",
    # `mock` and `mocked` are both entries, so `mocking` is the missing third form
    # of a blocked verb, not a false alarm.
    "mocking",
    # `beat` is KEPT as a hazard by 8.74 and `die`/`died`/`dying` are all entries.
    # Blocking their siblings costs vocabulary; it cannot kill a story, because
    # `moderate.blocklist_check` is deliberately untouched (11.6).
    "beats",
    "beating",
    "dies",
)

# The words substring matching would destroy, which is why `data/blocklist.txt` has
# always refused it, PLUS the three false alarms the mandate's crude suffixing
# produced (11.3): `los` + es = `loses`, `san` + s = `sans`, `butt` + er = `butter`.
INNOCENT_WORDS = (
    "class",
    "classic",
    "classes",
    "grass",
    "grasses",
    "bass",
    "assess",
    "assessment",
    "cocktail",
    "cocktails",
    "scunthorpe",
    "penistone",
    "butter",
    "buttercup",
    "loss",
    "losses",
    "loses",
    "sans",
    "corps",
    "passes",
    "glasses",
    "masses",
    "shell",
    "shelter",
    "pillow",
    "assassin",
    "therapist",
    "analysis",
    "dictionary",
    "mockingbird",
)


# --- the inflection generator ------------------------------------------------
@pytest.mark.parametrize(
    "word,expected",
    [
        ("fuck", {"fucks", "fucked", "fucking"}),
        ("kill", {"kills", "killed", "killing"}),
        ("ass", {"asses", "assed", "assing"}),
        ("piss", {"pisses", "pissed", "pissing"}),
        ("punch", {"punches", "punched", "punching"}),
        ("murder", {"murders", "murdered", "murdering"}),
        ("die", {"dies", "died", "diing"}),
        ("bully", {"bullies", "bullied", "bullying"}),
    ],
)
def test_inflect_generates_the_regular_forms(word, expected):
    assert expected <= inflect(word)


def test_inflect_never_returns_the_word_itself():
    """Otherwise `_expand` looks like it works while adding nothing."""
    for word in ("cat", "fuck", "bully", "die", "leaf", "box"):
        assert word not in inflect(word)


def test_inflect_generates_no_derivational_suffix():
    """11.2: `-er`/`-est` are where the collisions live.

    `butt` + `er` is `butter` and `hot` + `est` is `hottest`. The four derivational
    forms that carry real hazard are explicit LINES in `data/blocklist.txt`, so the
    generator does not have to guess.
    """
    assert "butter" not in inflect("butt")
    assert "hottest" not in inflect("hot")
    assert "killer" not in inflect("kill")
    assert "attacker" not in inflect("attack")
    # ...and the explicit lines are there instead.
    literal = load_blocklist()
    for word in ("killer", "attacker", "fuckin", "freaky", "pimp"):
        assert word in literal, f"{word} must be an explicit blocklist line (11.5)"


def test_the_derivational_lines_carry_their_own_plurals():
    """`killers`/`attackers` fall out of the plural rule once `killer` is a line."""
    forms = blocked_forms()
    assert {"killer", "killers", "attacker", "attackers"} <= forms


# --- the blocklist expansion, both directions --------------------------------
def test_load_blocklist_reads_one_word_per_line():
    """6.15's bug: a space-separated rewrite loaded 22 entries instead of 240.

    The only symptom was a row count moving the wrong way, so 8.17 made lines and
    entries equal to make the list verifiable by counting it. This asserts that
    equality rather than a magic number, so adding an entry does not need a test
    edit — but a word with a space in it, or a duplicated line, fails.
    """
    raw = (DATA / "blocklist.txt").read_text(encoding="utf-8").splitlines()
    lines = [ln.strip() for ln in raw if ln.strip() and not ln.lstrip().startswith("#")]
    assert lines, "blocklist is empty"
    assert not [ln for ln in lines if " " in ln], "an entry has a space in it (6.15)"
    assert len(lines) == len(set(lines)), "a duplicate line (8.17)"
    assert len(load_blocklist()) == len(lines)


def test_the_expansion_is_a_strict_superset_of_the_literal_entries():
    literal = load_blocklist()
    assert literal < blocked_forms()


@pytest.mark.parametrize("variant", BLOCKED_VARIANTS)
def test_every_variant_the_sweep_found_is_now_blocked(variant):
    """Direction 1: the inflections that walked straight through (11.1)."""
    assert variant in blocked_forms()


def test_the_variants_were_invisible_to_the_literal_list():
    """What makes 11.1 a defect and not a restatement of an existing test.

    Not one of these is on `data/blocklist.txt`, which is why
    `test_phase2.py::test_no_blocklisted_word_reaches_the_allowlist` was green
    while all 30 were in the table. If a future pass adds one of them as an explicit
    line, this fails and the redundancy gets removed from `BLOCKED_VARIANTS`
    deliberately rather than by accident.
    """
    literal = load_blocklist()
    assert not [v for v in BLOCKED_VARIANTS if v in literal]


@pytest.mark.parametrize("word", INNOCENT_WORDS)
def test_no_innocent_word_is_blocked(word):
    """Direction 2: the Scunthorpe problem, which is why substring matching is out.

    `data/blocklist.txt`'s header has always said this; nothing asserted it.
    """
    assert word not in blocked_forms()


def test_the_uninflected_section_header_still_exists():
    """11.3's carve-out is matched on the header text, so it must not go silent.

    If the section is renamed, the fragments get inflected again and `loses`,
    `sans` and `corps` come back. This is the test that makes that loud.
    """
    sections = load_blocklist_sections()
    assert FRAGMENT_SECTION in sections, (
        f"the section header {FRAGMENT_SECTION!r} is gone from data/blocklist.txt — "
        "precompute.blocked_forms() is now inflecting `los`, `san` and `corp`"
    )
    assert "los" in sections[FRAGMENT_SECTION]


def test_the_fragment_section_contributes_no_inflections():
    forms = blocked_forms()
    for fragment in load_blocklist_sections()[FRAGMENT_SECTION]:
        assert fragment in forms, "the fragment itself must still be blocked"
        assert not (inflect(fragment) & forms), (
            f"{fragment!r} was inflected — 11.3 says it must not be"
        )


def test_the_expansion_blocks_no_dale_chall_word_that_is_not_a_blocked_inflection():
    """The false-alarm audit, as a test rather than a one-off script (11.4).

    Any child-vocabulary word appearing in the expansion is either a genuine
    inflection of a deliberately-blocked hazard or a defect. Two are genuine and
    they are named; a third arrival fails here and has to be judged, which is the
    point.
    """
    # Only the LISTED child words, not their generated inflections: comparing two
    # expansions against each other would drown the signal.
    generated = blocked_forms() - load_blocklist()
    assert generated & DALE_CHALL_LITERALS == {"beating", "dies"}, (
        "a new child-vocabulary word entered the blocklist expansion — judge it "
        "against 8.14's test (is the hazard in the WORD?) and record it, per 11.4"
    )


# --- the register and proper-noun filters ------------------------------------
def test_the_child_list_admits_inflections_of_its_own_entries():
    """Dale-Chall's own convention (11.8): `cat` listed means `cats` is familiar."""
    forms = child_forms()
    for word in ("cats", "maps", "sits", "taps", "stamps", "camps", "picnics", "acts"):
        assert word in forms


def test_the_child_list_does_not_admit_comparatives():
    """`-est` is not generated, which is what keeps `hottest` out (11.2)."""
    assert "hot" in child_forms()
    assert "hottest" not in child_forms()


@pytest.mark.parametrize(
    "word",
    ["nixon", "gibson", "ronald", "adam", "sam", "santa", "tampa", "nasa", "napa", "sid"],
)
def test_capitalised_only_hunspell_entries_are_rejected_as_proper_nouns(word):
    """11.9: the signal is capitalisation in the source `.dic`.

    `frequency-en.csv` is lowercased, so it cannot tell `Nixon` from `nixon`.
    """
    assert word not in common_forms()


@pytest.mark.parametrize("word", ["lebron", "aca", "cas", "das", "sas", "nic", "mina", "im"])
def test_words_absent_from_the_dictionary_are_rejected(word):
    assert word not in common_forms()


@pytest.mark.parametrize(
    "word", ["cat", "class", "grass", "cocktail", "assess", "panda", "nip", "amid", "griffin"]
)
def test_real_lowercase_words_survive_the_proper_noun_filter(word):
    """Direction 2 for 11.9. `amid` and `griffin` are real words — they are the
    REGISTER filter's problem, not this one, and conflating the two filters would
    hide which one is doing the work."""
    assert word in common_forms()


def test_the_child_list_is_a_subset_of_the_common_list():
    """11.9's union. Without it, a gap in the Hunspell list would silently delete
    vocabulary Dale-Chall vouches for — e.g. generated forms like `picnics`."""
    assert child_forms() <= common_forms()


def test_the_candidate_pool_is_wider_than_the_frequency_list():
    """11.10: 316 Dale-Chall words are outside the top 20k entirely."""
    from storyforge.precompute import _UNRANKED_RANK_BASE, load_frequency

    ranked = load_frequency()
    pool = candidate_pool()
    assert len(pool) > len(ranked)
    synthetic = {word for rank, word in pool if rank >= _UNRANKED_RANK_BASE}
    assert {"acorn", "beehive", "blackbird", "bandage"} <= synthetic
    # Nothing from the real list is given a synthetic rank.
    assert not (synthetic & {word for _, word in ranked})


# --- the table itself --------------------------------------------------------
@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


async def test_no_blocklisted_variant_reaches_the_allowlist(session):
    """The defect this whole file exists for, asserted against the real table.

    `test_phase2.py::test_no_blocklisted_word_reaches_the_allowlist` checks the 228
    literal entries and passed throughout — with 50 variants in the table.
    """
    rows = await session.execute(
        text("SELECT DISTINCT word FROM allowed_words WHERE word = ANY(:b)"),
        {"b": sorted(blocked_forms())},
    )
    assert not [r[0] for r in rows]


async def test_no_innocent_word_was_collateral(session):
    """Both directions against the real table: the words substring matching kills
    are still offered wherever the inventory can spell them."""
    rows = await session.execute(
        text("SELECT DISTINCT word FROM allowed_words WHERE word = ANY(:w)"),
        {"w": ["class", "grass", "assess", "cocktail", "butter", "loss", "classic"]},
    )
    survivors = {r[0] for r in rows}
    assert {"class", "grass", "butter", "loss"} <= survivors, (
        f"innocent vocabulary was lost: only {sorted(survivors)} survived"
    )


async def test_no_proper_noun_reaches_the_allowlist_at_any_stage(session):
    """11.9. `lebron` and `nixon` were in the live table at the top stages."""
    rows = await session.execute(
        text("SELECT DISTINCT stage_id, word FROM allowed_words WHERE word = ANY(:w)"),
        {
            "w": [
                "lebron",
                "nixon",
                "ronald",
                "gibson",
                "nasa",
                "sam",
                "adam",
                "santa",
                "tampa",
                "napa",
                "im",
                "ip",
                "aca",
            ]
        },
    )
    assert not rows.all()


async def test_the_register_filter_is_applied_where_it_is_configured(session):
    """11.8/11.11, in the form that keeps the escalation honest.

    `amid` is decodable from stage 1 up. It must be gone from every stage the
    register filter reaches, and this test says exactly where that starts — so if
    `REGISTER_FILTER_FROM_STAGE` is set to 1, this test tightens with it instead of
    needing an edit.
    """
    # Pinned, not read off the constant. A guard that takes its threshold from the
    # thing it guards cannot notice the filter being switched off — sabotage-checked:
    # setting the constant to 99 left the rest of this test green.
    assert REGISTER_FILTER_FROM_STAGE == 1, (
        "the register filter no longer applies at every stage. Raising this is the "
        "documented escape hatch if the stage-1 ladder work is deferred (11.11), and "
        "it is a REGRESSION in word quality at the one stage §7's fallback ladder can "
        "serve to any child — so it turns the suite red rather than passing quietly."
    )
    rows = await session.execute(
        text("SELECT DISTINCT stage_id FROM allowed_words WHERE word = ANY(:w) ORDER BY 1"),
        {"w": ["amid", "drafted", "classics", "blunt", "hottest", "mod", "griffin", "pimp"]},
    )
    stages = [r[0] for r in rows]
    assert all(s < REGISTER_FILTER_FROM_STAGE for s in stages), (
        f"adult-register words survive at stages {stages}, but the register filter "
        f"is configured from stage {REGISTER_FILTER_FROM_STAGE}"
    )
    # `pimp` is a blocklist entry now (11.5), so it is gone at EVERY stage,
    # including the ones the register filter does not reach.
    gone = await session.execute(
        text("SELECT count(*) FROM allowed_words WHERE word = 'pimp'"),
    )
    assert gone.scalar_one() == 0


async def test_every_stage_holds_only_child_vocabulary_where_the_filter_applies(session):
    """The assertion a row count cannot make (11.7).

    §5.4's own metric was "~150-2,500 words/stage", and 157 rows at stage 1 met it
    while 27 of them were proper nouns. This checks the words, not the count.
    """
    child = child_forms()
    rows = await session.execute(
        text("SELECT stage_id, word FROM allowed_words WHERE stage_id >= :s"),
        {"s": REGISTER_FILTER_FROM_STAGE},
    )
    strays = [(stage, word) for stage, word in rows if word not in child]
    assert not strays, f"non-child vocabulary above the filter boundary: {strays[:10]}"


# --- the LOCAL additions to the register list (DEVIATIONS 31.1-31.5) ---------
# Dale-Chall is a 1948 list and it is silent about `dragon` and `planet`. Two of
# this app's three §8 themes are named after them. The measurement that made this
# a defect rather than a preference: 38 of 38 servable DRAGONS stories contained
# the word `dragon` while it was in NO stage's `allowed_words`, so the exclusion
# prevented zero occurrences and instead converted every one into either a §5.5
# picture chip spent on a word decodable from stage 1 or a `--check-vocabulary`
# REGISTER offence.
#
# Widening a filter is the dangerous direction — 11.7-11.11 exists because §5.4's
# original filter was too WIDE — so these guards are deliberately about what the
# widening must NOT do, not about the two words being nice to have.


def test_every_local_addition_is_decodable_by_the_unchanged_inventory():
    """A word being THEMATIC is not an argument for it being DECODABLE.

    Nothing in 31.1 touches §1's gate: `precompute.compute_rows` still refuses any
    candidate the 93-GPC inventory cannot align (`if first is None: continue`), so
    an undecodable addition would be silently ignored rather than loudly wrong —
    which is the shape a filter change should never have. This asserts it at the
    word, so the file cannot accumulate lines that do nothing.
    """
    undecodable = sorted(w for w in child_additions() if decodable_from_stage(w) is None)
    assert not undecodable, (
        f"{undecodable} cannot be aligned by the 93-GPC inventory at any stage, so "
        "adding them to the register list permits a word no child here can read. "
        "The decodability gate is never weakened to admit a theme word (STATE "
        "invariant 1)."
    )


def test_no_local_addition_is_already_a_dale_chall_word():
    """The file must be a true DELTA, or it stops recording a decision.

    A line that duplicates Dale-Chall changes nothing and makes the additions list
    look bigger than the judgement actually was — and the next re-vendor could
    quietly move a word from one file to the other with no diff anywhere.
    """
    duplicates = sorted(child_additions() & DALE_CHALL_LITERALS)
    assert not duplicates, (
        f"{duplicates} are already in the vendored Dale-Chall list, so the addition "
        "records a judgement that was never made"
    )


def test_the_local_additions_are_a_closed_deliberate_set():
    """The ratchet. A new register word costs an edit HERE too.

    `LOCAL_ADDITIONS` is written out literally rather than read from the file it
    guards — a guard sourced from its own subject cannot see the subject move.
    """
    assert child_additions() == LOCAL_ADDITIONS, (
        "data/child-vocabulary-additions.txt changed. Every line there widens the "
        "REGISTER filter that DEVIATIONS 11.7-11.11 narrowed on purpose (`amid` was "
        "reaching stage-1 five-year-olds), so it is ratcheted: state the measurement "
        "in the file, then update LOCAL_ADDITIONS here."
    )


def test_the_vendored_dale_chall_file_is_not_where_additions_go():
    """The provenance half, and the reason for a second file at all.

    `data/child-vocabulary.txt` is a verbatim vendored copy, SHA-256 pinned in
    `decoder/data/PINS.md` and asserted by
    `tests/decoder/test_inventory.py::test_vendored_artifacts_match_their_pins`.
    Patching a local judgement into it would break that pin and be reverted by the
    next `scripts/vendor-word-lists.py` run.
    """
    assert not (LOCAL_ADDITIONS & DALE_CHALL_LITERALS), (
        "a local addition was written into the PINNED vendored list — that breaks "
        "its digest and the next re-vendor silently drops it"
    )
    assert LOCAL_ADDITIONS <= child_forms(), (
        "the additions file is no longer reaching the register filter, so the two "
        "words are permitted nowhere again"
    )


def test_the_additions_pass_every_other_filter_on_their_own_merits():
    """Widening ONE filter must not tunnel under the other two.

    `child_forms()` is unioned into `common_forms()` (11.9), so a word added here
    is exempted from the proper-noun filter by construction. That is safe only
    while the addition is a real lowercase dictionary word and not on the
    blocklist — asserted, not assumed.
    """
    assert not (child_additions() & blocked_forms()), (
        "an addition is a blocklist entry or an inflection of one — the register "
        "list must never be a route around §5.4 layer 1"
    )
    for word in sorted(child_additions()):
        assert word in _read_word_list("common-words-en.txt"), (
            f"{word!r} is not a lowercase Hunspell headword, so it is only reaching "
            "`common_forms()` through the child-list union — which is exactly how a "
            "proper noun would get in (11.9)"
        )


async def test_a_local_addition_reaches_every_stage_that_can_decode_it(session):
    """The table, not the set. `child_forms()` being right proves nothing alone.

    `compute_rows` applies the syllable cap and the alignment search AFTER the
    register test, so a word can pass the filter and still reach no stage. `dragon`
    and `planet` are two syllables against a stage-1 cap of two; if that cap ever
    moves, this fails rather than the pool quietly going back to having no word for
    its own theme.
    """
    for word in sorted(child_additions()):
        first = decodable_from_stage(word)
        assert first is not None
        rows = await session.execute(
            text("SELECT stage_id FROM allowed_words WHERE word = :w ORDER BY 1"),
            {"w": word},
        )
        stages = [r[0] for r in rows]
        assert stages == list(range(first, MAX_STAGE + 1)), (
            f"{word!r} is decodable from stage {first} but `allowed_words` offers it "
            f"at {stages}. Run `make precompute`; if that does not fix it, the "
            "syllable cap or the register filter is dropping it again and a "
            f"{word}-themed reader has no licensed way to say the word."
        )
