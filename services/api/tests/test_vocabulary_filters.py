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
from storyforge.precompute import (
    DATA,
    FRAGMENT_SECTION,
    REGISTER_FILTER_FROM_STAGE,
    _read_word_list,
    blocked_forms,
    candidate_pool,
    child_forms,
    common_forms,
    inflect,
    load_blocklist,
    load_blocklist_sections,
)

DALE_CHALL_LITERALS = frozenset(_read_word_list("child-vocabulary.txt"))

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
