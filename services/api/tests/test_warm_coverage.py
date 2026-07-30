"""§7's warm-pool coverage floor, as a refusal (`docs/AUDIT-PHASE7.md` F4).

The defect these tests exist for: `make warm` defaulted to
`--stages 1,2,3 --themes dragons`, the Makefile target passed no arguments, and
`docs/DEPLOY.md` handed a first-deploy operator exactly that command described as
"pre-generate the story pool (§7)". Nine stories, 3 of 12 stages, 1 of 3 themes —
against `warm.py`'s own docstring, `docs/STATE.md` and §7 all promising ">=3 per
(stage, theme)". §17's demo is dragons at stage 3, the one covered cell, so the
demo passed on a pool where nine stages held nothing, and the §7 ladder degraded
to a lower stage silently and *correctly*, which is why nobody noticed.

Three independent guards, tested here as three separate propositions, because the
whole finding is that one of them being right is not enough:

1. the DEFAULTS are §7's full matrix (`test_the_cli_default_matrix_*`);
2. the coverage GATE refuses — non-zero — while any pair is short
   (`test_the_coverage_gate_*`), and refuses to answer at all on an unseeded
   matrix rather than passing vacuously;
3. `run_warm` reads the POOL back at the end, so a pair whose every generation was
   REJECTED still fails the run (`test_a_full_matrix_run_*`) — which neither
   existing exit code detects.

Nothing here calls Anthropic. Guard 3 drives `run_warm` with a fake pipeline; the
rest is pure functions and one SQL query.

The FOURTH gate lives here too (DEVIATIONS 11.99-11.103), because it is the same
proposition about a different floor: `--check-mcq2` refuses a pool whose
two-choice questions can be answered without reading the story. `--check-coverage`
counts stories, `--check-promotability` counts §8 evidence, `--check-prompts`
reads a tap-a-word instruction, and this one reads what an `mcq2` SAYS — 37 of 70
servable rows were answerable by matching the question's letters against the two
cards, and 7 of them pointed the child at the WRONG card, under a green suite and
three green gates.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from storyforge import warm
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.decoder import MAX_STAGE, MIN_STAGE
from storyforge.llm.questions import mcq2_pair, negation_pair, prompt_leaks_answer
from storyforge.mastery import MASTERY_QTYPES
from storyforge.pipeline import load_vocabulary

# --- guard 1: the defaults ---------------------------------------------------
# RED against the pre-fix defaults. Sabotage check, run against a copy of the
# tree with `DEFAULT_STAGES = "1,2,3"` / `DEFAULT_THEMES = "dragons"` restored:
#   test_the_cli_default_matrix_is_every_stage       FAILED  [1,2,3] != 1..11
#   test_the_cli_default_matrix_is_every_theme       FAILED  'dragons' != 'all'
#   test_the_default_matrix_is_what_the_cli_hands_run_warm  FAILED  3 stages, 1 theme


def test_the_cli_default_matrix_is_every_stage():
    """§7 says every (stage, theme) pair. The default said stages 1-3."""
    assert warm.parse_stages(warm.DEFAULT_STAGES) == list(range(MIN_STAGE, MAX_STAGE + 1))


def test_the_cli_default_matrix_is_every_theme():
    """`all`, resolved against `themes` — not a hardcoded slug.

    Spelled `all` rather than `dragons,space,animals` on purpose: a fourth theme
    must widen the default, not be silently excluded from the pool for as long as
    it takes someone to notice their theme serves fallbacks.
    """
    assert warm.DEFAULT_THEMES == "all"
    assert warm.resolve_themes(warm.DEFAULT_THEMES, ["space", "dragons"]) == [
        "dragons",
        "space",
    ]


def test_the_default_matrix_is_what_the_cli_hands_run_warm(monkeypatch):
    """End-to-end through `main`'s own dispatch, not through a restatement of it.

    The defect was never in `run_warm`; it was in the two `add_argument` defaults
    and a Makefile target that passed nothing. So the assertion has to be about
    what an argument-free invocation actually delivers.

    Sync, because `main` owns the event loop (`asyncio.run`) — which is itself the
    reason this is the right seam to test: it is the one an operator uses.
    """
    import sys

    seen: dict = {}

    async def spy(stages, themes, target, effort):
        seen.update(stages=stages, themes=themes, target=target, effort=effort)

    monkeypatch.setattr(warm, "run_warm", spy)
    monkeypatch.setattr(sys, "argv", ["storyforge.warm"])
    warm.main()

    assert seen["stages"] == list(range(MIN_STAGE, MAX_STAGE + 1))
    # Every seeded theme, read from the database rather than named here.
    assert seen["themes"] == sorted(await_sync(_theme_slugs()))
    assert seen["target"] == warm.TARGET_PER_PAIR == 3


def test_a_stage_outside_the_scope_and_sequence_is_refused():
    """Bounded, unlike `eval.parse_stages` (DEVIATIONS 8.46): the failure
    direction for a bad matrix is a refusal, never a quieter run."""
    for bad in ("0..3", "13", "1..13", "x", ""):
        with pytest.raises(SystemExit):
            warm.parse_stages(bad)


def test_themes_all_over_an_unseeded_table_is_refused_not_empty():
    """`--themes all` matching nothing would generate nothing and exit 0."""
    with pytest.raises(SystemExit) as exc:
        warm.resolve_themes("all", [])
    assert "make seed" in str(exc.value)

    with pytest.raises(SystemExit) as exc:
        warm.resolve_themes("unicorns", ["dragons"])
    assert "unknown theme" in str(exc.value)


# --- guard 2: the gate -------------------------------------------------------
def _matrix(**overrides: int) -> list[warm.PairDepth]:
    """A full 12x3 matrix at target, minus whatever the caller shorts.

    Keyed `"stage:theme"`. Built full so a test names only its own defect.
    """
    rows = []
    for stage in range(MIN_STAGE, MAX_STAGE + 1):
        for theme in ("animals", "dragons", "space"):
            rows.append(warm.PairDepth(stage, theme, overrides.get(f"{stage}:{theme}", 3)))
    return rows


def test_the_coverage_gate_finds_the_shallow_pairs_and_names_them():
    rows = _matrix(**{"6:dragons": 0, "1:animals": 2})
    gaps = warm.coverage_gaps(rows, 3)
    # Shallowest first: an empty pair is worse than a pair one story short.
    assert [(g.stage_id, g.theme_slug, g.servable) for g in gaps] == [
        (6, "dragons", 0),
        (1, "animals", 2),
    ]
    assert warm.coverage_gaps(_matrix(), 3) == []


def test_the_coverage_gate_counts_what_the_serving_path_serves():
    """`warm|complete`, not `warm` — a read-down pool is not a broken pool.

    Imported from the endpoint rather than re-spelled, so a fourth status cannot
    make the gate and `_SERVE_SQL` disagree (DEVIATIONS 8.42).
    """
    assert SERVABLE_STATUSES == ("warm", "complete")
    async_sql = str(warm._COVERAGE_SQL)
    assert ":statuses" in async_sql
    # And a LEFT JOIN whose status filter is in the ON clause, because a pair with
    # NO stories has to come back as a row with 0 — a WHERE would drop exactly the
    # rows the gate exists to find.
    assert "LEFT JOIN" in async_sql
    # This assertion was `assert "WHERE" not in async_sql`, and that substring was
    # a PROXY for the requirement in the comment above rather than the requirement
    # itself. `ANSWERABLE_SQL` (DEVIATIONS 8.50/8.51) joined the same ON clause as
    # a correlated `EXISTS (SELECT 1 ... WHERE q.story_id = s.id)` — which
    # satisfies the requirement, because the filter is still in the ON clause,
    # while containing the word. So the proxy over-approximated and is replaced by
    # one that tests PLACEMENT, which is what the comment always claimed.
    # Subqueries are stripped innermost-first; what remains is the statement's own
    # top level.
    top_level = async_sql
    while "(" in top_level:
        collapsed = re.sub(r"\([^()]*\)", " ", top_level)
        if collapsed == top_level:  # unbalanced parentheses — stop, do not loop
            break
        top_level = collapsed
    assert "WHERE" not in top_level, (
        "the coverage gate grew a top-level WHERE, which drops exactly the empty "
        f"pairs it exists to find:\n{top_level}"
    )
    # The behavioural half of the same requirement, which no string check can stand
    # in for, is `test_dead_end_stories.py::
    # test_the_coverage_gate_ignores_a_story_that_cannot_be_served`: a pair whose
    # only story fails the ON clause comes back as a row with 0, not as no row.


def test_the_coverage_gate_refuses_to_pass_vacuously_on_an_unseeded_matrix():
    """The failure mode, not an edge case: on a fresh deploy `stages` and `themes`
    are empty until `make seed`, and a check over zero pairs finds zero gaps."""
    assert warm.coverage_gaps([], 3) == []  # which is why the assert below exists

    with pytest.raises(SystemExit) as exc:
        warm.assert_matrix_seeded([])
    assert "not seeded" in str(exc.value) and "make seed" in str(exc.value)

    # Eleven stages is also not the matrix. A stage silently dropped from `stages`
    # would otherwise take its three pairs out of the denominator.
    short = [r for r in _matrix() if r.stage_id != MAX_STAGE]
    with pytest.raises(SystemExit):
        warm.assert_matrix_seeded(short)

    # Themes are NOT pinned to a count: §7 does not fix how many themes exist.
    warm.assert_matrix_seeded([r for r in _matrix() if r.theme_slug == "dragons"])


def test_the_gate_exits_non_zero_and_says_what_a_gap_costs_a_child(monkeypatch, capsys):
    """The gate's whole value is the exit code plus the sentence explaining why
    nothing in the product will ever report this."""

    async def shallow(session):
        return _matrix(**{"9:space": 0})

    monkeypatch.setattr(warm, "pool_coverage", shallow)
    with pytest.raises(SystemExit) as exc:
        await_sync(warm.run_coverage_check(3))

    message = str(exc.value)
    assert "1 of 33" in message
    assert "fallback" in message and "reports to nobody" in message
    assert "--check-coverage" in message
    out = capsys.readouterr().out
    assert "stage 9 / space: 0" in out
    assert "32 of 33 pair(s) at target." in out


def test_the_gate_exits_zero_on_a_full_pool(monkeypatch, capsys):
    """The other direction — a gate that cannot pass is a broken build, not a
    guard, and this one is about to be wired into `make warm`."""

    async def full(session):
        return _matrix()

    monkeypatch.setattr(warm, "pool_coverage", full)
    await_sync(warm.run_coverage_check(3))
    assert "OK — all 33" in capsys.readouterr().out


def test_the_gate_honours_the_target_it_is_given(monkeypatch):
    """`-n` means "the depth I care about" in both modes, so `--check-coverage -n 1`
    is a weaker floor by request rather than by accident."""

    async def two_each(session):
        return [warm.PairDepth(r.stage_id, r.theme_slug, 2) for r in _matrix()]

    monkeypatch.setattr(warm, "pool_coverage", two_each)
    with pytest.raises(SystemExit):
        await_sync(warm.run_coverage_check(3))
    await_sync(warm.run_coverage_check(2))  # 2 >= 2: no gap


def test_the_rendered_table_marks_every_short_pair():
    """The operator reads the table, not the exit code. A short cell has to be
    visually distinct or the table is decoration."""
    lines = warm.render_coverage(_matrix(**{"7:animals": 1}), 3)
    body = "\n".join(lines)
    assert "1 !" in body
    assert "32 of 33 pair(s) at target." in body
    assert "stage 7 / animals: 1" in body
    # The FULL matrix, always: a table over only the requested slice is the
    # defect, not the report (DEVIATIONS 8.43).
    assert body.count("\n  ") >= MAX_STAGE


# --- guard 3: the pool, read back at the end of a run ------------------------
async def test_a_full_matrix_run_whose_stories_were_all_rejected_still_exits_non_zero(
    monkeypatch, capsys
):
    """The hole neither existing exit code reaches.

    `_finish`'s two categories are "a story landed unenriched" and "a generation
    produced nothing". A pair whose every generation SUCCEEDED and was then
    rejected on the gate or by moderation leaves both empty and `made` non-zero,
    so before this change `make warm` exited 0 with the pair still empty. That is a
    second, independent route to F4's outcome and it survives the default-matrix
    fix, which is why the exit code has to key off the pool.

    Shaped as the live case, not the degenerate one: the low stages produce, the
    LAST stage is rejected on content. An *all*-rejected run already exits non-zero
    on `made == 0` — the hole is a run that produced plenty and left a hole.
    """
    stages = list(range(MIN_STAGE, MAX_STAGE + 1))
    themes = sorted(await _theme_slugs())

    async def rejected_at_the_top(session, stage_id, theme_slug, effort="medium"):
        return _rejected_result(stage_id, theme_slug, passed=stage_id < MAX_STAGE)

    async def empty_pool(session, stage_id, theme_id):
        return 0

    async def one_pair_empty(session):
        return _matrix(**{f"{MAX_STAGE}:{themes[0]}": 0})

    monkeypatch.setattr(warm, "run_pipeline", rejected_at_the_top)
    monkeypatch.setattr(warm, "pool_depth", empty_pool)
    monkeypatch.setattr(warm, "persist", _fake_persist)
    monkeypatch.setattr(warm, "enrich", _fake_enrich)
    monkeypatch.setattr(warm, "_record_enrichment", _noop_record)
    monkeypatch.setattr(warm, "pool_coverage", one_pair_empty)

    with pytest.raises(SystemExit) as exc:
        await warm.run_warm(stages, themes, 3, "medium")

    message = str(exc.value)
    # Not "no stories were made" and not "a generation failed" — the pool is short.
    assert "covered §7's whole matrix" in message
    assert f"stage {MAX_STAGE} / {themes[0]}: 0" in message
    assert "REJECTED, not lost" in message
    assert "unenriched" not in message and "api_fail" not in message
    assert "POOL COVERAGE" in capsys.readouterr().out


async def test_a_sliced_run_prints_the_whole_matrix_and_still_exits_zero(monkeypatch, capsys):
    """A slice did what it was asked, so it is not a failure — but the operator
    sees the whole picture and the command that closes it, which is the half F4
    says nobody ever saw. Before this change the run printed nothing about the 33
    pairs it did not touch."""
    themes = sorted(await _theme_slugs())

    async def good(session, stage_id, theme_slug, effort="medium"):
        return _rejected_result(stage_id, theme_slug, passed=True)

    async def empty_pool(session, stage_id, theme_id):
        return 0

    async def mostly_empty(session):
        return _matrix(**{f"{s}:{t}": 0 for s in range(6, MAX_STAGE + 1) for t in themes})

    monkeypatch.setattr(warm, "run_pipeline", good)
    monkeypatch.setattr(warm, "pool_depth", empty_pool)
    monkeypatch.setattr(warm, "persist", _fake_persist)
    monkeypatch.setattr(warm, "enrich", _fake_enrich)
    monkeypatch.setattr(warm, "_record_enrichment", _noop_record)
    monkeypatch.setattr(warm, "pool_coverage", mostly_empty)

    await warm.run_warm([1], ["dragons"], 1, "medium")

    out = capsys.readouterr().out
    assert "POOL COVERAGE" in out
    assert "18 below target" in out
    assert "covered 1 of 11 stage(s)" in out
    assert "reports to nobody" in out
    assert "--check-coverage" in out


# --- guard 4: what a two-choice question SAYS (DEVIATIONS 11.99-11.103) -------
# `--mcq2-repair` shipped and had never been run. 11.64 taught `check_mcq2` to
# refuse a question naming the word that distinguishes its own two answers, and a
# false card that is the true card with `not` in it — both on DRAFTS, so both
# reached exactly zero of the rows already in the pool. `_UNENRICHED_SQL` selected
# ZERO stories, because every clause it has asks what a story is MISSING and a
# leaking question is present, moderated and countable. 37 of 70 servable rows
# were answerable with the story unread and 7 pointed at the WRONG card.
#
# The first two tests are the live-pool property, RED against the pool as it stood
# (37 removable rows) and stated two independent ways. The rest drive the gate and
# the repair over rows planted for the purpose, because a healthy pool cannot
# demonstrate a refusal — which is how 8.51's five vacuous guards went green
# without ever being executed.

#: A question that names the word distinguishing its two cards. Live row q695,
#: verbatim: `on`/`top` are in the question and in the TRUE card only.
LEAKING_MCQ2 = (
    "Did the dragon sit on top?",
    '{"options": ["It sat on top.", "It sat in a cave."]}',
    '{"correct": "It sat on top.", "index": 0}',
)
#: A pair whose only discriminating word is `not` — which the prompt does not
#: contain, so `prompt_leaks_answer` returns nothing and this row is invisible to
#: the leak half of the sweep. Live row q4414, verbatim (11.100).
NEGATED_MCQ2 = (
    "Was the rabbit glad?",
    '{"options": ["The rabbit was glad.", "The rabbit was not glad."]}',
    '{"correct": "The rabbit was glad.", "index": 0}',
)


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
async def planted(session):
    """A throwaway servable story carrying one `tap_word` and nothing else.

    The `tap_word` is not decoration: `_DELETE_LEAKY_MCQ2_SQL` refuses to remove a
    story's LAST question, so a story with only the planted `mcq2` would exercise
    the wrong refusal.
    """
    theme_id = (
        await session.execute(text("SELECT id FROM themes WHERE slug = 'dragons'"))
    ).scalar_one()
    story_id = (
        await session.execute(
            text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (1, :t, 'warm', 'Mcq2 Gate Fixture', 1.0, "
                "CAST('{}' AS text[]), CAST('{}' AS jsonb)) RETURNING id"
            ),
            {"t": theme_id},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', "
            "'Tap the word that starts with the same sound as sat.', "
            """CAST('{"options": ["cat", "sam", "sit"], "cue_word": "sat"}' AS jsonb), """
            """CAST('{"correct": "sit", "index": 2, "target_grapheme": "s"}' AS jsonb))"""
        ),
        {"s": story_id},
    )
    await session.commit()
    yield story_id
    await session.rollback()
    await session.execute(text("DELETE FROM stories WHERE id = :i"), {"i": story_id})
    await session.execute(text("DELETE FROM kids WHERE display_name = :n"), {"n": "Mcqgatekid"})
    await session.commit()


async def _plant_mcq2(session, story_id: int, row: tuple[str, str, str]) -> int:
    prompt, payload, answer = row
    qid = (
        await session.execute(
            text(
                "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                "VALUES (:s, 1, 'mcq2', :p, CAST(:pl AS jsonb), CAST(:a AS jsonb)) RETURNING id"
            ),
            {"s": story_id, "p": prompt, "pl": payload, "a": answer},
        )
    ).scalar_one()
    await session.commit()
    return qid


async def test_the_live_pool_serves_no_self_answering_mcq2(session):
    """Every servable two-choice question needs the story to answer it.

    Deliberately does NOT call `warm.leaky_mcq2`: the predicate is rebuilt here
    out of `prompt_leaks_answer` and `negation_pair` directly, so a bug in the
    sweep cannot make the sweep's own invariant pass. That is 11.85's rule for a
    live-pool test, applied to the other question type.

    Scoped to rows the free repair COULD remove, which is the gate's own scope
    (11.102): a row carrying a recorded answer or standing as its story's last
    question is an operator decision, and a suite that goes red over one teaches
    a developer to skip it. Those rows are named in the failure message anyway.
    """
    rows = (
        await session.execute(
            text(
                "SELECT q.id, q.story_id, q.prompt, q.payload, q.answer, s.stage_id, "
                "       t.slug AS theme_slug, "
                "       (SELECT count(*) FROM answers a WHERE a.question_id = q.id) AS n_answers, "
                "       (SELECT count(*) FROM questions o WHERE o.story_id = s.id "
                "         AND o.id <> q.id) AS n_other "
                "FROM questions q JOIN stories s ON s.id = q.story_id "
                "JOIN themes t ON t.id = s.theme_id "
                "WHERE q.qtype = 'mcq2' AND s.status = ANY(:statuses) ORDER BY q.id"
            ),
            {"statuses": list(SERVABLE_STATUSES)},
        )
    ).all()
    if not rows:
        # Not a pass. An empty pool cannot serve a self-answering question and
        # cannot demonstrate a sound one either — 8.44's vacuity failure, as a skip.
        pytest.skip("no mcq2 rows on servable stories — run `make warm` first")

    vocab: dict[tuple[int, str], object] = {}
    removable, blocked = [], []
    for r in rows:
        pair = mcq2_pair(r.payload, r.answer)
        if pair is None:
            continue
        correct, incorrect = pair
        key = (r.stage_id, r.theme_slug)
        if key not in vocab:
            vocab[key] = await load_vocabulary(session, r.stage_id, r.theme_slug)
        sight = frozenset(w.strip().lower() for w in vocab[key].sight_words)
        if not prompt_leaks_answer(r.prompt or "", correct, incorrect, sight) and not negation_pair(
            correct, incorrect
        ):
            continue
        (removable if r.n_answers == 0 and r.n_other > 0 else blocked).append(
            (r.id, r.story_id, r.prompt)
        )

    assert removable == [], (
        f"{len(removable)} of {len(rows)} servable two-choice question(s) can be answered "
        f"by matching the question's letters against the two cards: {removable[:5]}. "
        f"({len(blocked)} more are blocked by a recorded answer or by being their story's "
        "last question and are an operator decision.) Repair — free, no API call — with "
        "`python -m storyforge.warm --mcq2-repair`, then `--backfill`."
    )


async def test_the_live_pool_needs_no_mcq2_removal(session):
    """The same property through the shipped sweep rather than around it.

    Nothing is written: the session is rolled back, so this cannot repair the
    thing it is measuring. `kept` is deliberately NOT asserted empty — the rows
    the sweep is forbidden to touch are a standing operator decision (11.103),
    not a regression, and pinning their count here would freeze the defect into
    the suite.
    """
    try:
        deleted, kept, stripped = await warm.repair_leaky_mcq2(session)
    finally:
        await session.rollback()
    assert (deleted, stripped) == ([], []), (
        f"the sweep would remove {len(deleted)} live two-choice question(s) from "
        f"{len(stripped)} story/stories: {deleted[:5]}. Run `python -m storyforge.warm "
        f"--mcq2-repair`, then `--backfill`. ({len(kept)} further row(s) are blocked and "
        "are an operator decision.)"
    )


async def test_the_gate_refuses_a_question_that_names_its_own_answer(session, planted):
    """The gate's exit code, driven over a planted row. RED is the requirement."""
    qid = await _plant_mcq2(session, planted, LEAKING_MCQ2)

    with pytest.raises(SystemExit) as exc:
        await warm.run_mcq2_check()

    message = str(exc.value)
    assert "answerable with the story unread" in message
    assert "--mcq2-repair" in message, "the refusal must name the remedy"
    assert "--backfill" in message, "the refusal must name the way back to a question"
    async with SessionLocal() as s:
        found = await warm.leaky_mcq2(s, SERVABLE_STATUSES, [planted])
    assert [row.question_id for row in found] == [qid]
    assert found[0].leaked == ("on", "top")
    assert found[0].negated is False


async def test_the_gate_refuses_a_false_card_that_is_the_true_card_negated(session, planted):
    """The half `prompt_leaks_answer` cannot see, and the reason the sweep tests both.

    This row's only discriminating word is `not`, and `not` is not in the
    question — so the leak predicate returns nothing and a sweep built on it
    alone reports the row as clean. 3 live rows were in exactly this state
    (11.100).
    """
    qid = await _plant_mcq2(session, planted, NEGATED_MCQ2)

    async with SessionLocal() as s:
        found = await warm.leaky_mcq2(s, SERVABLE_STATUSES, [planted])
    assert [row.question_id for row in found] == [qid]
    assert found[0].leaked == (), "the prompt leaks nothing — only the PAIR is the defect"
    assert found[0].negated is True

    with pytest.raises(SystemExit):
        await warm.run_mcq2_check()


async def test_neither_the_gate_nor_the_repair_cascades_a_recorded_answer(session, planted):
    """§8's evidence outranks our defect, and the exit code says so out loud.

    `answers.question_id` is ON DELETE CASCADE. The DELETE's refusal is a SQL
    predicate rather than a Python `if`, so this asserts the outcome — the row
    survives, the answer survives, the sweep reports it as kept, and the gate
    stops setting its exit code over a row whose only remedy is a human decision
    (11.102/11.103).
    """
    qid = await _plant_mcq2(session, planted, LEAKING_MCQ2)
    kid_id = (
        await session.execute(
            text("INSERT INTO kids (display_name, stage_id) VALUES ('Mcqgatekid', 1) RETURNING id")
        )
    ).scalar_one()
    reading_id = (
        await session.execute(
            text(
                "INSERT INTO readings (kid_id, story_id, stage_id, path) VALUES "
                """(:k, :s, 1, CAST('{"choices": []}' AS jsonb)) RETURNING id"""
            ),
            {"k": kid_id, "s": planted},
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO answers (reading_id, question_id, correct) VALUES (:r, :q, true)"),
        {"r": reading_id, "q": qid},
    )
    await session.commit()

    async with SessionLocal() as s:
        deleted, kept, stripped = await warm.repair_leaky_mcq2(s, SERVABLE_STATUSES, [planted])
        await s.commit()
    assert deleted == [] and stripped == [], "the sweep cascaded a child's recorded answer"
    assert [row.question_id for row in kept] == [qid]

    async with SessionLocal() as s:
        assert (
            await s.execute(
                text("SELECT count(*) FROM answers WHERE question_id = :q"), {"q": qid}
            )
        ).scalar_one() == 1
        assert (
            await s.execute(text("SELECT count(*) FROM questions WHERE id = :q"), {"q": qid})
        ).scalar_one() == 1

    # And the gate does not refuse over it: the remedy is a human decision, and a
    # refusal an operator cannot act on is one they learn to ignore.
    await warm.run_mcq2_check()


async def test_the_delete_itself_refuses_an_answered_row_and_a_story_s_last_question(
    session, planted
):
    """The two refusals are SQL predicates, and this is the test that proves it.

    Found by sabotage: deleting `NOT EXISTS (SELECT 1 FROM answers ...)` from
    `_DELETE_LEAKY_MCQ2_SQL` left the whole suite GREEN, because
    `repair_leaky_mcq2` filters in Python first (`LeakyMcq2.removable`) and never
    hands the statement an id it should refuse. Every test that goes through the
    sweep therefore exercises the Python guard and leaves the SQL one unexecuted —
    8.51's guard that is green without ever having run.

    So this calls the statement directly with ids a caller has no business
    passing, which is the proposition its comment actually makes: *structurally
    incapable of cascading an answer away or of emptying a story, whatever the
    caller believes about the ids it passed*.
    """
    answered_q = await _plant_mcq2(session, planted, LEAKING_MCQ2)
    kid_id = (
        await session.execute(
            text("INSERT INTO kids (display_name, stage_id) VALUES ('Mcqgatekid', 1) RETURNING id")
        )
    ).scalar_one()
    reading_id = (
        await session.execute(
            text(
                "INSERT INTO readings (kid_id, story_id, stage_id, path) VALUES "
                """(:k, :s, 1, CAST('{"choices": []}' AS jsonb)) RETURNING id"""
            ),
            {"k": kid_id, "s": planted},
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO answers (reading_id, question_id, correct) VALUES (:r, :q, true)"),
        {"r": reading_id, "q": answered_q},
    )

    # A second story whose ONLY question is the leaking mcq2 — the other refusal.
    theme_id = (
        await session.execute(text("SELECT id FROM themes WHERE slug = 'dragons'"))
    ).scalar_one()
    lonely_story = (
        await session.execute(
            text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (1, :t, 'warm', 'Mcq2 Lonely Fixture', 1.0, "
                "CAST('{}' AS text[]), CAST('{}' AS jsonb)) RETURNING id"
            ),
            {"t": theme_id},
        )
    ).scalar_one()
    lonely_q = await _plant_mcq2(session, lonely_story, LEAKING_MCQ2)

    try:
        gone = (
            await session.execute(
                warm._DELETE_LEAKY_MCQ2_SQL, {"ids": [answered_q, lonely_q]}
            )
        ).all()
        assert gone == [], (
            "the DELETE removed a row it must refuse: "
            f"{[(r.id, r.story_id) for r in gone]}. `answers.question_id` is ON DELETE "
            "CASCADE, and a story with no questions is STATE invariant 14's dead end."
        )
        surviving = set(
            (
                await session.execute(
                    text("SELECT id FROM questions WHERE id = ANY(:ids)"),
                    {"ids": [answered_q, lonely_q]},
                )
            )
            .scalars()
            .all()
        )
        assert surviving == {answered_q, lonely_q}
        assert (
            await session.execute(
                text("SELECT count(*) FROM answers WHERE question_id = :q"), {"q": answered_q}
            )
        ).scalar_one() == 1
    finally:
        await session.rollback()
        await session.execute(text("DELETE FROM stories WHERE id = :i"), {"i": lonely_story})
        await session.commit()


async def test_the_repair_leaves_the_receipt_the_backfill_reads(session, planted):
    """`--mcq2-repair` on Monday and `--backfill` on Friday must compose.

    The defect this test exists for, found by running the two halves for real:
    `_RECORD_MCQ2_REMOVAL_SQL` was defined, its comment explained exactly why it
    had to exist, `_UNENRICHED_SQL`'s fourth clause read the key it writes — and
    nothing ever executed it. `--mcq2-repair` removed 37 live rows, and
    `--backfill` five minutes later selected ZERO stories and printed "nothing to
    backfill", because the only record of what had been stripped was a return
    value that died with the first process. Same shape as `_STRIPPED_STORY_SQL`
    (a symbol referenced by a caller and never written), inside the repair for
    the forward-only trap.

    So the assertion is deliberately made in a SEPARATE session from the repair:
    the in-memory `stripped` list is exactly what must NOT be what makes this
    pass.
    """
    await _plant_mcq2(session, planted, LEAKING_MCQ2)

    async with SessionLocal() as s:
        deleted, _kept, stripped = await warm.repair_leaky_mcq2(s, SERVABLE_STATUSES, [planted])
        await s.commit()
    assert len(deleted) == 1 and stripped == [planted]

    async with SessionLocal() as s:
        receipt = (
            await s.execute(
                text(
                    "SELECT gen_meta -> 'enrichment' -> 'mcq2_leak_removed' "
                    "FROM stories WHERE id = :i"
                ),
                {"i": planted},
            )
        ).scalar_one()
        selected = (
            await s.execute(
                warm._UNENRICHED_SQL,
                {
                    "statuses": list(SERVABLE_STATUSES),
                    "only": [planted],
                    "mastery_qtypes": list(MASTERY_QTYPES),
                },
            )
        ).all()

    assert receipt == deleted, "the story carries no record of what was taken from it"
    assert [row.id for row in selected] == [planted], (
        "`--backfill` cannot see a story stripped by an earlier `--mcq2-repair` run, so "
        "the two halves only compose when an operator happens to run them in one command"
    )


# --- helpers ------------------------------------------------------------------
def await_sync(coro):
    """Drive a coroutine from a SYNC test. `asyncio_mode = auto` makes every
    `async def test_` a coroutine test; these three are deliberately sync because
    their subject is a pure function plus one monkeypatched query."""
    import asyncio

    return asyncio.run(coro)


async def _theme_slugs() -> list[str]:
    async with SessionLocal() as s:
        return list((await s.execute(text("SELECT slug FROM themes"))).scalars().all())


def _rejected_result(stage: int, theme: str, *, passed: bool = False):
    from storyforge.decoder import mastered_ids, validate_text
    from storyforge.llm.moderate import ModerationResult
    from storyforge.llm.pricing import Usage
    from storyforge.pipeline import Attempt, PipelineResult

    body = "Sam sat. The cat sat."
    report = validate_text(body, mastered_ids(stage), frozenset(["the"]), frozenset())
    return PipelineResult(
        stage_id=stage,
        theme=theme,
        passed=passed,
        attempts=[
            Attempt(
                n=0,
                kind="generate",
                text=body,
                decodability=1.0,
                violations=[],
                usage=Usage(model="claude-sonnet-5", input_tokens=10, output_tokens=5),
            )
        ],
        final_text=body,
        final_report=report,
        title="Sam And The Cat",
        story_words=[],
        moderation=ModerationResult(approved=passed, reason="unresolved peril", layer="model"),
    )


class _FakeStory:
    """Enough of a `Story` for `run_warm`'s printing, with no row written.

    These two tests are about the EXIT CODE over a 36-pair matrix; writing 108
    throwaway stories into the shared development database to prove it would be
    the tail wagging the dog.
    """

    id = 999_999
    gen_meta: dict = {}


async def _fake_persist(session, result, theme_id, today):
    return _FakeStory()


async def _fake_enrich(session, story, theme, **kwargs):
    return {"questions": 2, "segments_tagged": 3, "cost_usd": 0.0}


async def _noop_record(session, story, meta, base_cost):
    return None
