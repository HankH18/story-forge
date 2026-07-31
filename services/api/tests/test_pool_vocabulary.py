"""`--check-vocabulary` — the pool against the CURRENT allowlist (DEVIATIONS 22.1-22.9).

The defect this gate exists for: `allowed_words` is a PRECOMPUTE, and DEVIATIONS
11.7-11.11 rebuilt it with a child-register filter because `amid` was being offered
to five-year-olds. That changed the vocabulary the generator is *given* and changed
nothing about the stories already in the pool — `amid` is absent from
`allowed_words` at all eleven stages while nine servable stories still contain it,
three of them offering it to a stage-1 child as a tappable option.

Nothing existing could see it. The decodability gate cannot: `amid` aligns from
stage 1, so `_is_decodable_for` passes it at serve time every time. The objection
is register, not phonics.

**These tests are built around a positive control** (`offending_story`), because
this file's subject is a gate that reads the live pool: assertions over "whatever
the pool happens to hold" pass vacuously the moment the pool is clean, which is
the exact failure `tests/test_story_words.py` recorded catching by sabotage.
"""

from __future__ import annotations

import pytest

from storyforge import warm
from storyforge.api.stories import ANSWERABLE_SQL
from storyforge.db import SessionLocal
from storyforge.pipeline import PROMPT_WORD_LIMIT, decodable_from_stage, load_vocabulary
from storyforge.precompute import child_forms


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


# --- the permitted set: what the generator is actually told it may emit --------
async def test_the_permitted_set_is_the_three_lists_SYSTEM_RULES_names(session):
    """`SYSTEM_RULES` rule 1: "the PERMITTED WORDS list, the SIGHT WORDS list, or
    the STORY WORDS list. There are no other options." The gate's permitted set
    must be exactly that union — no wider (or it forgives real drift) and no
    narrower (or it invents offences)."""
    allowed, sight = await warm.stage_vocabularies(session)
    permitted = warm.permitted_at(3, allowed, sight, ["castle"])
    assert permitted == allowed[3] | sight[3] | {"castle"}
    # Sight words are CUMULATIVE, exactly as `load_vocabulary` reads them.
    assert sight[3] >= sight[1]
    assert "the" in permitted, "stage-1 sight words must still be permitted at stage 3"


async def test_the_gate_reads_the_whole_stage_and_not_the_prompt_window(session):
    """`load_vocabulary` applies `ORDER BY freq_rank LIMIT PROMPT_WORD_LIMIT` —
    a decision about how big a PROMPT may be, not about which words the stage
    permits. A gate built on the window would report every word at rank 401+ as a
    violation. The window must be a strict SUBSET of what the gate reads, so the
    gate can never be stricter than the generator's own instructions."""
    allowed, _sight = await warm.stage_vocabularies(session)
    vocab = await load_vocabulary(session, 5, "dragons")
    assert len(vocab.allowed_words) == PROMPT_WORD_LIMIT
    assert set(vocab.allowed_words) < allowed[5]
    assert len(allowed[5]) > PROMPT_WORD_LIMIT
    # And it is the WHOLE stage, not merely a superset of the window: any filter
    # the gate applies on the way out of the table narrows the permitted set and
    # manufactures offences out of words the generator was legitimately offered.
    live = (
        await session.execute(
            warm.text("SELECT stage_id, count(*) AS n FROM allowed_words GROUP BY stage_id")
        )
    ).all()
    assert {r.stage_id: r.n for r in live} == {s: len(w) for s, w in allowed.items()}


# --- the classifier: it may mis-sort, it may never forgive --------------------
def test_the_classifier_separates_register_from_a_name_from_a_stage_move():
    """Three classes with three different remedies. `amid` is adult register and
    is the child-facing defect; `Sam` is a name the allowlist filters out by
    design (11.9) while `SYSTEM_RULES` rule 6 demonstrates one; `every` is child
    register that a syllable-cap move (21.2) took off its stage."""
    assert warm.classify_offence("amid", lowercase_anywhere=True) == warm.REGISTER_CLASS
    assert warm.classify_offence("sam", lowercase_anywhere=False) == warm.NAME_CLASS
    assert warm.classify_offence("every", lowercase_anywhere=True) == warm.STAGE_CLASS
    # The classifier's own evidence, imported rather than restated (11.8).
    assert "every" in child_forms() and "amid" not in child_forms()


def test_a_misclassified_word_is_still_an_offence(monkeypatch, capsys):
    """The property that makes a heuristic safe in a gate: classification decides
    the ORDER of the report and never the exit code. Force every word into the
    most forgiving class and the gate must still exit non-zero — otherwise this
    is a check that passes because it asked the wrong question."""
    monkeypatch.setattr(warm, "classify_offence", lambda *a, **k: warm.STAGE_CLASS)
    census = warm.VocabCensus(
        offences=(
            warm.VocabOffence(
                story_id=7,
                stage_id=1,
                theme_slug="dragons",
                status="warm",
                word="amid",
                where="body",
                occurrences=3,
            ),
        ),
        stories=10,
        scored=900,
        slack=4,
        possessive=0,
        lowercase_anywhere=frozenset({"amid"}),
    )

    async def fake(session):
        return census

    monkeypatch.setattr(warm, "vocabulary_offences", fake)
    with pytest.raises(SystemExit) as exc:
        warm.asyncio.run(warm.run_vocabulary_check())
    assert "1 of 10 servable story/stories" in str(exc.value)


# --- the exit code -----------------------------------------------------------
def _census(offences=(), stories=10, **kw):
    return warm.VocabCensus(
        offences=tuple(offences),
        stories=stories,
        scored=kw.get("scored", 900),
        slack=kw.get("slack", 0),
        possessive=kw.get("possessive", 0),
        lowercase_anywhere=frozenset(kw.get("lowercase", ())),
    )


def _offence(story_id, stage, word, where="body", occurrences=1):
    return warm.VocabOffence(
        story_id=story_id,
        stage_id=stage,
        theme_slug="dragons",
        status="warm",
        word=word,
        where=where,
        occurrences=occurrences,
    )


def _patch(monkeypatch, census):
    async def fake(session):
        return census

    monkeypatch.setattr(warm, "vocabulary_offences", fake)


def test_the_gate_names_every_word_and_the_stories_that_hold_it(monkeypatch, capsys):
    """8.43's rule. The WORD is the unit a decision is made in — one `amid` over
    nine stories is one judgement — and the STORY is the unit a remedy is applied
    in, so the report must carry both, never a bare count."""
    _patch(
        monkeypatch,
        _census(
            [
                _offence(2, 1, "amid", occurrences=3),
                _offence(10, 1, "amid"),
                _offence(29, 1, "sam", where="title", occurrences=4),
            ],
            lowercase=("amid",),
        ),
    )
    with pytest.raises(SystemExit) as exc:
        warm.asyncio.run(warm.run_vocabulary_check())

    message = str(exc.value)
    assert "3 of 10 servable story/stories" in message
    assert "adult REGISTER" in message and "'amid'" in message
    # The remedy, and the honest fact that it is not free.
    assert "NO free repair" in message
    # The general principle, in the exit message rather than only in a docstring.
    assert "precomputed input changed" in message

    out = capsys.readouterr().out
    assert "'amid': 4 occurrence(s) over 2 story/stories" in out
    assert "2, 10" in out
    assert warm.REGISTER_CLASS.upper() in out and warm.NAME_CLASS.upper() in out
    # A title-only offender must say so, because NOTHING else validates a title.
    assert "offend ONLY in the TITLE: [29]" in out
    assert "validate_draft" in out


def test_the_gate_exits_zero_on_a_pool_that_matches_the_allowlist(monkeypatch, capsys):
    """A gate that cannot pass is a broken build, not a guard."""
    _patch(monkeypatch, _census([], stories=42, scored=3000, slack=7, possessive=2))
    warm.asyncio.run(warm.run_vocabulary_check())
    out = capsys.readouterr().out
    assert "OK — every word on all 42 servable" in out
    # What it declined to judge is COUNTED, so an operator can see the exclusions
    # rather than infer them from a number that got smaller.
    assert "7 token(s) not decodable" in out and "2 possessive form(s)" in out


def test_the_gate_refuses_to_report_a_clean_pool_over_an_empty_one(monkeypatch):
    """8.44's vacuity rule, one query later. A vocabulary check over zero servable
    stories finds zero offences and exits 0 — on a fresh deploy, which is exactly
    when an operator is most likely to believe it."""
    _patch(monkeypatch, _census([], stories=0))
    with pytest.raises(SystemExit) as exc:
        warm.asyncio.run(warm.run_vocabulary_check())
    assert "EMPTY pool" in str(exc.value)


def test_the_gate_counts_only_stories_a_child_can_actually_be_served():
    """Same `SERVABLE_STATUSES` + `ANSWERABLE_SQL` predicate as every other gate,
    imported and never re-spelled (8.42/8.51): a word on a story the serving path
    refuses is not a word any child is shown."""
    sql = str(warm._VOCAB_SQL)
    assert ":statuses" in sql
    assert ANSWERABLE_SQL.format(story="s") in sql


# --- the positive control ----------------------------------------------------
@pytest.fixture
async def offending_story(session):
    """One servable stage-1 dragons story whose text is a KNOWN mix.

    Four deliberate cases in one row:
      * `amid`  — decodable from stage 1, absent from `allowed_words` at every
                  stage: the offence, and the one this gate exists for.
      * `sat`   — in `allowed_words` at stage 1: must NOT be reported.
      * `could` — the inventory can never align it, so `allowed_words`
                  structurally cannot contain it: §1's 0.95 gate's business, and
                  this gate must skip it and COUNT it as slack.
      * `castle's` — possessive of the story's own story word: a tokenisation
                  artefact, skipped and counted separately.
    The TITLE carries `amid` too, so the body/title split is exercised.
    """
    theme_id = (
        await session.execute(warm.text("SELECT id FROM themes WHERE slug = 'dragons'"))
    ).scalar_one()
    story_id = (
        await session.execute(
            warm.text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (1, :th, 'warm', 'Amid The Cat', 1.0, "
                "CAST(:words AS text[]), '{}'::jsonb) RETURNING id"
            ),
            {"th": theme_id, "words": ["castle"]},
        )
    ).scalar_one()
    await session.execute(
        warm.text(
            "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
            "VALUES (:s, 0, :b, '{}')"
        ),
        {"s": story_id, "b": "The cat sat amid the castle's mat. It could nap."},
    )
    await session.execute(
        warm.text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', 'Tap: sit.', CAST(:p AS jsonb), CAST(:a AS jsonb))"
        ),
        {
            "s": story_id,
            "p": '{"options": ["sat", "did", "nap"], "cue_word": "sit"}',
            "a": '{"correct": "sat", "index": 0, "target_grapheme": "s"}',
        },
    )
    await session.commit()
    try:
        yield story_id
    finally:
        await session.rollback()
        await session.execute(warm.text("DELETE FROM stories WHERE id = :id"), {"id": story_id})
        await session.commit()


async def test_the_gate_finds_the_known_offender_and_nothing_else_on_that_story(
    offending_story, session
):
    """The positive control the rest of this file needs in order not to be vacuous."""
    census = await warm.vocabulary_offences(session)
    mine = [o for o in census.offences if o.story_id == offending_story]
    assert {o.word for o in mine} == {"amid"}, (
        "`amid` is the offence; `sat` is permitted, `could` is the decodability "
        "gate's business and `castle's` is a possessive of a story word"
    )
    assert {o.where for o in mine} == {"body", "title"}
    assert sum(o.occurrences for o in mine) == 2
    assert offending_story in census.story_ids


async def test_the_gate_skips_an_undecodable_word_and_counts_it_as_slack(
    offending_story, session
):
    """`precompute.compute_rows` drops any word the inventory cannot align, so
    `allowed_words` is STRUCTURALLY unable to contain `could`. Counting it here
    would silently make this a second decodability gate with no slack at all —
    a different proposition (§1 permits 5%) with a different remedy."""
    assert decodable_from_stage("could") is None
    census = await warm.vocabulary_offences(session)
    assert "could" not in {o.word for o in census.offences}
    assert census.slack > 0
    assert census.possessive > 0


async def test_the_gate_agrees_with_the_allowlist_it_is_auditing(session):
    """The two halves cannot drift: every word the gate reports must genuinely be
    absent from the permitted set at that story's stage, and every one must be
    DECODABLE there — otherwise the gate has wandered into §1's territory. A gate
    with its own copy of the rule is how 11.60 shipped a retired prompt to 120
    children under a green suite."""
    allowed, sight = await warm.stage_vocabularies(session)
    census = await warm.vocabulary_offences(session)
    assert census.stories > 0, "no servable story — the gate would be vacuous"
    rows = {
        r.id: r.story_words
        for r in (
            await session.execute(warm.text("SELECT id, story_words FROM stories"))
        ).all()
    }
    for o in census.offences:
        permitted = warm.permitted_at(o.stage_id, allowed, sight, rows[o.story_id])
        assert o.word not in permitted
        first = decodable_from_stage(o.word)
        assert first is not None and first <= o.stage_id
