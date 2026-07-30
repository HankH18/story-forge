"""A servable story with no comprehension question is a dead-end session (8.50-8.54).

The defect these tests exist for. A full `make warm` over §7's whole matrix left
six `warm` stories carrying zero `questions` rows — 7291/7297/7299 at stage 7,
7302 at stage 8, 7332/7334 at stage 10. A child served one of them reads the
story and the session just stops: no question screen, so no `tap_word` answer, so
no `gpc_stats` movement and no §8 promotion signal at all. `run_warm` printed
`! 0 questions — dropped: {...}` per story and exited non-zero, exactly as the
Phase 4 audit arranged — and it made no difference, because **nothing anywhere
asserted that such a story must not be SERVED.** Every check in the codebase
looked at `status`, and `status` was still `warm`. `warm.py`'s own docstring
called it "a defined, servable state (§7)".

Two propositions, tested separately, because either one alone leaves the child
somewhere bad:

1. **The serving path refuses it** — `ANSWERABLE_SQL` in `_SERVE_SQL`, in
   `GET /api/stories/{id}`, in `POST /api/readings`, and again on the loaded DTO
   as defence in depth (`test_serving_*`, `test_the_serve_loop_*`).
2. **The coverage gate and the refill count agree with the serving path**
   (`test_the_coverage_gate_*`, `test_the_refill_count_*`). Without this, 1 turns
   one visible defect into an invisible one: a pair reports itself at target while
   every child at it is served a silent §7 fallback to a lower stage — which is
   AUDIT-PHASE7 F4 reached by a second route.

Then the yield half (8.53, 8.54), which is why the pool held six of them:
`test_every_offered_sound_*` and `test_a_tier_falls_through_*`. Those are pure
functions; nothing here calls Anthropic.

SABOTAGE CHECKED, and the record below is what actually went red rather than what
was expected to. Against the pre-fix tree — `ANSWERABLE_SQL` removed from
`_SERVE_SQL`, `_COVERAGE_SQL`, `_DEPTH_SQL` and `readings.py`, both
`not dto.questions` checks removed, and `_prefer_current_stage` restored to
returning its first NON-EMPTY tier — **9 of these 11 tests fail**:

    test_serving_refuses_a_story_with_no_questions                FAILED
    test_serving_refuses_a_dead_end_story_by_id                   FAILED
    test_a_reading_cannot_start_against_a_dead_end_story          FAILED
    test_the_serve_loop_refuses_a_dead_end_story_it_is_handed     FAILED
    test_the_coverage_gate_ignores_a_story_that_cannot_be_served  FAILED
    test_the_refill_count_ignores_a_dead_end_story                FAILED
    test_the_answerable_predicate_is_defined_once                 FAILED
    test_the_live_pool_holds_no_dead_end_servable_story  FAILED  6 dead ends, by id
    test_every_offered_sound_can_be_answered   FAILED  (7302, 'ou', no legal option set)

That last line is the tier defect caught end to end, on the live story it cost:
stage 8's `current_stage_onset` tier is `ou`, `sound_targets` offered it, and no
option set the closed list admits could ever pass `check_tap_word`.

The two that stay green under that sabotage are
`test_a_tier_falls_through_when_its_only_sound_is_unanswerable` and
`test_the_length_rule_is_not_relaxed_to_manufacture_a_question`, and the reason is
worth stating rather than glossing: both assert about `tap_word_options` directly,
which the sabotage does not touch (it reverts the CALLER). They are unit tests of
the predicate the tier decision keys off, not a second guard on the tier decision
— `test_every_offered_sound_can_be_answered` is that guard.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from storyforge import warm
from storyforge.api import stories as stories_api
from storyforge.api.stories import ANSWERABLE_SQL, SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.llm.questions import (
    SoundTarget,
    TapWordDraft,
    check_tap_word,
    sound_targets,
    tap_word_options,
)
from storyforge.main import app
from storyforge.mastery import MASTERY_QTYPES
from storyforge.pipeline import load_vocabulary

#: Stage-1 decodable text (1.000 with these story words), so a fixture carrying
#: it clears §1's gate at EVERY stage — the inventory is cumulative (§4, §5.7c
#: monotonicity) and the request path re-checks decodability at serve time. Same
#: reasoning, and deliberately the same sentences, as `test_stories_api.py`.
SEGMENTS = (
    "A dragon sat in a cave.",
    "Sam did pat the dragon.",
    "The dragon did nap in the sand.",
)
STORY_WORDS = ["cave", "dragon"]

#: The fixture lives at stage 1 so that `stage_id <= :stage` admits only stage-1
#: stories, which is the smallest set the serve test has to exclude.
STAGE = 1


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


class DeadEnd:
    """One servable story with no `questions` row, and its own theme.

    Its own theme (DEVIATIONS 7.16) so nothing here asserts about a pair some
    other run filled, and so the teardown can delete by id rather than by
    predicate. `questions`, `story_segments` and `readings` all cascade off
    `stories`, so two deletes undo the whole fixture.
    """

    def __init__(self, session) -> None:
        self.session = session
        self.theme_id = 0
        self.story_ids: list[int] = []
        self.kid_ids: list[int] = []

    async def start(self) -> DeadEnd:
        self.theme_id = (
            await self.session.execute(
                text(
                    "INSERT INTO themes (slug, display_name, emoji) "
                    "VALUES ('zz-dead-end', 'Dead End Fixture', '🧪') RETURNING id"
                )
            )
        ).scalar_one()
        await self.session.commit()
        return self

    async def story(
        self,
        *,
        questions: bool,
        stage: int = STAGE,
        qtype: str = "tap_word",
        enrichment: str | None = None,
    ) -> int:
        story_id = (
            await self.session.execute(
                text(
                    "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                    "story_words, gen_meta) VALUES (:st, :th, 'warm', 'Dead End Fixture', "
                    "1.0, CAST(:words AS text[]), CAST(:meta AS jsonb)) RETURNING id"
                ),
                {
                    "st": stage,
                    "th": self.theme_id,
                    "words": list(STORY_WORDS),
                    "meta": enrichment or "{}",
                },
            )
        ).scalar_one()
        for idx, body in enumerate(SEGMENTS):
            await self.session.execute(
                text(
                    "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
                    "VALUES (:s, :i, :b, ARRAY[]::text[])"
                ),
                {"s": story_id, "i": idx, "b": body},
            )
        if questions:
            # Written the way the node writes one, so the row this test calls
            # "answerable" is a row the product would actually serve.
            await self.session.execute(
                text(
                    "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                    "VALUES (:s, 0, :qtype, 'Tap: sit.', CAST(:p AS jsonb), "
                    "CAST(:a AS jsonb))"
                ),
                {
                    "s": story_id,
                    "qtype": qtype,
                    "p": '{"options": ["sat", "did", "nap"], "cue_word": "sit"}',
                    "a": '{"correct": "sat", "index": 0, "target_grapheme": "s"}',
                },
            )
        await self.session.commit()
        self.story_ids.append(story_id)
        return story_id

    async def stop(self) -> None:
        await self.session.rollback()
        if self.kid_ids:
            await self.session.execute(
                text("DELETE FROM kids WHERE id = ANY(CAST(:ids AS int[]))"),
                {"ids": self.kid_ids},
            )
        if self.story_ids:
            await self.session.execute(
                text("DELETE FROM stories WHERE id = ANY(:ids)"), {"ids": self.story_ids}
            )
        await self.session.execute(
            text("DELETE FROM themes WHERE id = :id"), {"id": self.theme_id}
        )
        await self.session.commit()


@pytest.fixture
async def dead_end(session):
    fixture = await DeadEnd(session).start()
    try:
        yield fixture
    finally:
        await fixture.stop()


async def _serve(session, dead_end: DeadEnd, story_id: int):
    """Run `_SERVE_SQL` with every OTHER candidate excluded.

    The pick is ordered on `stage_id DESC`, then theme, then `last_served_at`,
    then id — none of which a fixture can win against a full pool. So rather than
    trying to out-rank the pool, this narrows the candidate set to exactly one
    story using the endpoint's own `:excluded` parameter: whatever comes back,
    comes back because `_SERVE_SQL`'s WHERE clause admitted it. Deterministic, and
    it tests the production statement rather than a restatement of it.
    """
    others = [
        r.id
        for r in await session.execute(
            text(
                "SELECT id FROM stories WHERE status = ANY(:statuses) "
                "AND stage_id <= :stage AND id <> :keep"
            ),
            {"statuses": list(SERVABLE_STATUSES), "stage": STAGE, "keep": story_id},
        )
    ]
    row = (
        await session.execute(
            stories_api._SERVE_SQL,
            {
                "statuses": list(SERVABLE_STATUSES),
                "stage": STAGE,
                "theme": dead_end.theme_id,
                "excluded": [0, *others],
            },
        )
    ).first()
    await session.commit()
    return row


# --- proposition 1: the serving path refuses it ------------------------------
async def test_serving_refuses_a_story_with_no_questions(session, dead_end):
    """`_SERVE_SQL` will not pick it even when it is the only candidate left.

    The control in the same test is the point: the identical fixture WITH a
    question is served, so what the first half proves is the predicate and not a
    broken exclusion list.
    """
    dead = await dead_end.story(questions=False)
    assert await _serve(session, dead_end, dead) is None, (
        f"story {dead} has no questions and `_SERVE_SQL` handed it to a child "
        "anyway — the session would read and then stop (§11.3, §8)"
    )

    alive = await dead_end.story(questions=True)
    served = await _serve(session, dead_end, alive)
    assert served is not None and served.id == alive


async def test_serving_refuses_a_dead_end_story_by_id(client, dead_end):
    """`GET /api/stories/{id}` is a second door into the same room.

    Kid mode re-fetches by id (`apps/web/src/lib/api.ts`), so a content rule that
    only `POST /api/stories/request` enforced would be one GET away from
    irrelevant — the same argument that already puts the `rejected` filter here.
    """
    dead = await dead_end.story(questions=False)
    alive = await dead_end.story(questions=True)

    assert (await client.get(f"/api/stories/{dead}")).status_code == 404
    ok = await client.get(f"/api/stories/{alive}")
    assert ok.status_code == 200, ok.text
    assert ok.json()["questions"], "the control fixture must carry its question"


async def test_a_reading_cannot_start_against_a_dead_end_story(client, dead_end, session):
    """A `reading` is the row §8 counts toward "≥5 stories at current stage".

    Opening one against a story that can never yield a `tap_word` answer spends
    one of the five on a session that cannot contribute to the other half of the
    same criterion (accuracy over current-stage GPCs).
    """
    dead = await dead_end.story(questions=False)
    alive = await dead_end.story(questions=True)

    created = await client.post("/api/kids", json={"display_name": "DeadEnd", "stage_id": STAGE})
    assert created.status_code == 201, created.text
    kid_id = created.json()["id"]
    dead_end.kid_ids.append(kid_id)

    refused = await client.post("/api/readings", json={"kid_id": kid_id, "story_id": dead})
    assert refused.status_code == 404, refused.text

    allowed = await client.post("/api/readings", json={"kid_id": kid_id, "story_id": alive})
    assert allowed.status_code == 201, allowed.text


async def test_the_serve_loop_refuses_a_dead_end_story_it_is_handed(client, dead_end, monkeypatch):
    """Defence in depth, in the shape `_is_decodable_for` established (7.23).

    The SQL predicate cannot see a question row deleted between the
    `UPDATE … RETURNING` and the `_load_story` read, so the loaded DTO is checked
    too. Provoked by making `_load_story` return a question-less story for
    whatever the query picked — which is exactly the race, expressed as a stub.
    The endpoint must 503 and name the repair, never 200 with an empty question
    list.
    """
    real = stories_api._load_story

    async def strip_questions(session, story_id: int):
        dto = await real(session, story_id)
        if dto is not None:
            dto.questions = []
        return dto

    monkeypatch.setattr(stories_api, "_load_story", strip_questions)

    resp = await client.post(
        "/api/stories/request", json={"stage_id": STAGE, "theme_id_or_slug": "zz-dead-end"}
    )
    assert resp.status_code == 503, resp.text
    assert "comprehension question" in resp.json()["detail"]
    assert "--backfill" in resp.json()["detail"]


# --- proposition 2: the gate and the refill count agree ----------------------
async def test_the_coverage_gate_ignores_a_story_that_cannot_be_served(session, dead_end):
    """Otherwise the fix hides the defect instead of removing it.

    A pair counted at target that a child cannot be served from is answered by
    §7's ladder with a lower stage — correctly, and reporting to nobody. That is
    the AUDIT-PHASE7 F4 failure mode, and it survives the 8.50 fix unless this
    query and `_SERVE_SQL` mean the same thing by "servable" (8.51).
    """
    dead = await dead_end.story(questions=False)
    slug = "zz-dead-end"

    rows = await warm.pool_coverage(session)
    depth = {(r.stage_id, r.theme_slug): r.servable for r in rows}
    assert depth[(STAGE, slug)] == 0, (
        f"story {dead} has no questions, so it counted toward coverage for a pair "
        "no child can be served from"
    )

    await dead_end.story(questions=True)
    rows = await warm.pool_coverage(session)
    depth = {(r.stage_id, r.theme_slug): r.servable for r in rows}
    assert depth[(STAGE, slug)] == 1


async def test_the_refill_count_ignores_a_dead_end_story(session, dead_end):
    """`pool_depth` decides how many stories to GENERATE (8.42).

    Counting a dead end here is worse than counting it in coverage: three of them
    in a pair would make `make warm` skip that pair forever, so the operator's
    remedy for a red gate ("re-run `make warm`") would do nothing at all.
    """
    await dead_end.story(questions=False)
    assert await warm.pool_depth(session, STAGE, dead_end.theme_id) == 0

    await dead_end.story(questions=True)
    assert await warm.pool_depth(session, STAGE, dead_end.theme_id) == 1


def test_the_answerable_predicate_is_defined_once():
    """One definition, formatted by every caller — never re-spelled (8.50).

    The same discipline as `SERVABLE_STATUSES`: the gate and the serving path
    disagreeing about which stories exist is the whole failure mode, and two
    copies of a SQL fragment is how they come to disagree.
    """
    assert "{story}" in ANSWERABLE_SQL
    assert ANSWERABLE_SQL.format(story="s") == (
        "EXISTS (SELECT 1 FROM questions q WHERE q.story_id = s.id)"
    )
    for statement in (
        str(stories_api._SERVE_SQL),
        str(warm._COVERAGE_SQL),
        str(warm._DEPTH_SQL),
    ):
        assert ANSWERABLE_SQL.format(story="stories") in statement or ANSWERABLE_SQL.format(
            story="s"
        ) in statement


async def test_the_live_pool_holds_no_dead_end_servable_story(session):
    """The defect's own query, as an invariant over the real pool.

    Not "reading an expected value out of the pool" (7.16): the expected value is
    zero by construction after 8.50, so this asserts the property rather than a
    count that some other run happens to produce. It is also the check that would
    have caught the six stories on the day they were written — the earlier version
    of it found 0 of 28 only because the pool was entirely stages 1-6, where the
    yield is ~100%.
    """
    dead = [
        (r.id, r.stage_id)
        for r in await session.execute(
            text(
                "SELECT st.id, st.stage_id FROM stories st "
                "WHERE st.status = ANY(:statuses) "
                "AND NOT EXISTS (SELECT 1 FROM questions q WHERE q.story_id = st.id) "
                "ORDER BY st.id"
            ),
            {"statuses": list(SERVABLE_STATUSES)},
        )
    ]
    assert dead == [], (
        f"{len(dead)} servable story/stories carry no question and are therefore "
        f"unservable dead weight in the pool: {dead}. Repair with "
        "`python -m storyforge.warm --backfill --only "
        f"{','.join(str(i) for i, _ in dead)}`"
    )


async def test_the_backfill_reaches_a_story_that_cannot_move_mastery(session, dead_end):
    """The forward-only trap, third time (DEVIATIONS 8.56).

    A servable story with an `mcq2` and no `tap_word` is not a dead-end session —
    the child gets a question screen — but `MASTERY_QTYPES` is `("tap_word",)`, so
    it moves no `gpc_stats` counter and earns nothing toward promotion. Six live
    stories were in that state and **none was reachable**: they had questions and a
    §13 receipt, so both of `_UNENRICHED_SQL`'s first two clauses were satisfied.
    Five of the six had dropped `tap_word` for the same "close to the same length"
    reason 8.53 is about.
    """

    async def selected() -> set[int]:
        rows = await session.execute(
            warm._UNENRICHED_SQL,
            {
                "statuses": list(SERVABLE_STATUSES),
                "only": dead_end.story_ids,
                "mastery_qtypes": list(MASTERY_QTYPES),
            },
        )
        return {r.id for r in rows}

    moderated = '{"enrichment": {"questions_moderation": {"approved": true}}}'
    mcq_only = await dead_end.story(questions=True, qtype="mcq2", enrichment=moderated)
    assert mcq_only in await selected(), (
        "a story §8 can never count is invisible to the one command that repairs it"
    )

    # A story with the mastery-bearing type is done, and is not swept again.
    complete = await dead_end.story(questions=True, qtype="tap_word", enrichment=moderated)
    assert complete not in await selected()


async def test_the_mastery_backfill_is_idempotent_by_receipt(session, dead_end):
    """A story that CANNOT have a tap_word must not be re-swept forever.

    Idempotence by receipt rather than by hope, the same way `_UNGRADED_BODY_SQL`
    does it (6.25): `describe_target` writes `source: null` when nothing wrote a
    tap-a-word question, so a story this sweep has already tried and failed on
    carries the key with a null value and is excluded. Without this the sweep would
    pay for the same impossible story on every run.
    """
    tried = (
        '{"enrichment": {"questions_moderation": {"approved": true}, '
        '"tap_word_target": {"scope": "none_available", "source": null}}}'
    )
    story_id = await dead_end.story(questions=True, qtype="mcq2", enrichment=tried)
    rows = await session.execute(
        warm._UNENRICHED_SQL,
        {
            "statuses": list(SERVABLE_STATUSES),
            "only": [story_id],
            "mastery_qtypes": list(MASTERY_QTYPES),
        },
    )
    assert {r.id for r in rows} == set(), (
        "a story already proved unable to carry a tap-a-word question was selected "
        "again — the sweep would spend on it on every run"
    )


async def test_the_live_pool_holds_no_servable_story_that_cannot_move_mastery(session):
    """§8's own precondition, over the real pool.

    A story with a question screen but no `tap_word` is servable and still earns a
    child nothing: `MASTERY_QTYPES` is what `POST /api/readings/{id}/answers`
    credits, so `gpc_stats` cannot move and §8's accuracy criterion can never be
    met from it. Six stories were in that state after the zero-question six were
    repaired — which is why this is a separate assertion and not a corollary.
    """
    blind = [
        (r.id, r.stage_id)
        for r in await session.execute(
            text(
                "SELECT s.id, s.stage_id FROM stories s WHERE s.status = ANY(:statuses) "
                "AND NOT EXISTS (SELECT 1 FROM questions q WHERE q.story_id = s.id "
                "AND q.qtype = ANY(:qtypes)) ORDER BY s.id"
            ),
            {"statuses": list(SERVABLE_STATUSES), "qtypes": list(MASTERY_QTYPES)},
        )
    ]
    assert blind == [], (
        f"{len(blind)} servable story/stories carry no {'/'.join(MASTERY_QTYPES)} "
        f"question, so a child can read them and earn nothing toward promotion: "
        f"{blind}. Repair with `python -m storyforge.warm --backfill --only "
        f"{','.join(str(i) for i, _ in blind)}`"
    )


# --- the yield half (8.53, 8.54) ---------------------------------------------
async def test_every_offered_sound_can_be_answered(session):
    """`sound_targets` offers no sound `check_tap_word` must always reject (8.53).

    This is the property the deterministic fallback rests on (8.54): if the menu
    can contain an unanswerable sound, then "compose the triple ourselves" is not
    a guarantee, and the six live stories are what that costs. Swept over the real
    pool, and asserted per (story, sound) rather than per story, because one
    answerable sound hiding four impossible ones is exactly the menu the model was
    handed at stage 8.
    """
    rows = (
        await session.execute(
            text(
                "SELECT s.id, s.stage_id, s.story_words, t.slug AS theme_slug, "
                "coalesce(string_agg(sg.body, ' ' ORDER BY sg.idx), '') AS body "
                "FROM stories s JOIN themes t ON t.id = s.theme_id "
                "LEFT JOIN story_segments sg ON sg.story_id = s.id "
                "WHERE s.status = ANY(:statuses) AND s.stage_id >= 6 "
                "GROUP BY s.id, s.stage_id, s.story_words, t.slug ORDER BY s.id"
            ),
            {"statuses": list(SERVABLE_STATUSES)},
        )
    ).all()
    assert rows, "no servable story at stage 6+ — run `make seed && make warm`"

    cache: dict[tuple[int, str], object] = {}
    unanswerable, storyless = [], []
    for row in rows:
        key = (row.stage_id, row.theme_slug)
        if key not in cache:
            cache[key] = await load_vocabulary(session, row.stage_id, row.theme_slug)
        vocab = cache[key]
        words = list(row.story_words or [])
        targets = sound_targets(
            story_text=row.body,
            stage_id=row.stage_id,
            allowed_words=vocab.allowed_words,
            sight_words=vocab.sight_words,
            story_words=words,
        )
        if not targets:
            storyless.append(row.id)
            continue
        for target in targets:
            options = tap_word_options(target)
            if options is None:
                unanswerable.append((row.id, target.grapheme, "no legal option set"))
                continue
            # The judge is unchanged, which is the whole claim of 8.54.
            question, why = check_tap_word(
                TapWordDraft(
                    target_grapheme=target.grapheme,
                    correct_word=options[0],
                    other_words=list(options[1:]),
                ),
                targets=targets,
                stage_id=row.stage_id,
                story_text=row.body,
                sight_words=vocab.sight_words,
                story_words=words,
                seed=row.id,
            )
            if question is None:
                unanswerable.append((row.id, target.grapheme, "; ".join(why)))

    assert unanswerable == [], (
        "`sound_targets` offered sound(s) whose every legal answer `check_tap_word` "
        f"refuses, so the model's three attempts cannot succeed: {unanswerable[:10]}"
    )
    assert storyless == [], (
        f"story/stories {storyless} can offer no answerable sound at all, so they "
        "will reach the pool without the one question type §8 counts"
    )


def test_a_tier_falls_through_when_its_only_sound_is_unanswerable():
    """§8's tier preference is a ranking, not a veto (8.53).

    The live stage-8 shape, as a pure function. `ou` is what stage 8 teaches and
    `outside` is the only `ou`-onset word a decodable story holds — seven letters
    against a story of four-letter words, so no triple is within
    `TAP_LEN_SPREAD`. "First NON-EMPTY tier wins" fired on it anyway and handed
    the model a menu of one impossible sound; three attempts later the story had
    no questions at all. The tier ORDER is untouched, so this is not a §8
    relaxation: the `current_stage_word` tier it falls through to is defined as
    "the correct word uses a sound this stage teaches", so the fallback still
    earns promotion evidence.
    """
    impossible = SoundTarget(
        grapheme="ou",
        cue="our",
        onset_words=["outside"],
        clean_words=["had", "in", "came", "look", "for", "food", "it", "saw"],
        stage=8,
    )
    assert tap_word_options(impossible) is None

    # `food` uses stage 8's `oo` without starting with it — the `current_stage_word`
    # tier, and answerable against four-letter distractors.
    answerable = SoundTarget(
        grapheme="f",
        cue="fun",
        onset_words=["food"],
        clean_words=["came", "look", "saw", "it"],
        stage=2,
    )
    options = tap_word_options(answerable)
    assert options is not None
    assert options[0] == "food"
    assert len({len(o) for o in options}) <= 2
    assert max(len(o) for o in options) - min(len(o) for o in options) <= 2


def test_a_story_whose_every_question_was_composed_locally_still_fails_the_run():
    """8.54's own downside, closed — and the reason the threshold is where it is.

    The derived `tap_word` keeps a story out of the dead-end state, and that is
    exactly what makes it capable of hiding an outage: three unparseable responses
    used to leave 0 questions and fail the run loudly, and would now leave 1 and
    pass it quietly. That would be a real loss of the alarm the Phase 4 audit
    installed.

    So the test is not "was anything derived" — the measured rate at which the
    model needs catching is ~11% of stories, and failing every run on that would
    make the exit code worthless — but "did the model contribute NOTHING that
    survived", which is the §7 api_fail signature. A story where the model wrote a
    usable `mcq2` and only the phonics question was composed is reported (see
    `_report_enrichment`) and counted in the run summary, but does not fail the run.
    """
    outage = {
        "questions": 1,
        "questions_written": ["tap_word"],
        "questions_derived": ["tap_word"],
        "questions_dropped": {"mcq2": ["the answer could not be read (stop_reason=None)"]},
        "segments_tagged": 3,
    }
    problems = warm.enrichment_problems(outage)
    assert any("composed locally" in p for p in problems), problems
    assert any("contributed nothing usable" in p for p in problems), problems

    # The model wrote the comprehension question, so this is a degraded story and
    # not a broken run.
    partial = {**outage, "questions": 2, "questions_written": ["tap_word", "mcq2"]}
    assert warm.enrichment_problems(partial) == []

    # And the pre-existing rule is untouched: zero questions is still a failure.
    none_at_all = {"questions": 0, "questions_dropped": {}, "segments_tagged": 3}
    assert any("0 questions" in p for p in warm.enrichment_problems(none_at_all))


def test_the_length_rule_is_not_relaxed_to_manufacture_a_question():
    """The rule blamed for the six stories is unchanged, and it still refuses.

    Measured before anything was changed: a legal triple existed under
    `spread <= 2` for 109 of 113 servable stories, and for five of the six that
    dropped their question — 21 of them for story 7291. So the length rule was
    never the binding constraint, relaxing it would have bought two stories out of
    four, and a ratio is not what fixed this (DEVIATIONS 8.53). A question a child
    can answer by shape is worse than no question, which is the whole reason 7.14
    exists.
    """
    from storyforge.llm.questions import TAP_LEN_SPREAD, _length_balanced

    assert TAP_LEN_SPREAD == 2
    assert _length_balanced(["want", "far", "big"], single_word=True)
    assert not _length_balanced(["outside", "food", "came"], single_word=True)
    # And the composer will not propose one either — same predicate, one source.
    assert (
        tap_word_options(
            SoundTarget(
                grapheme="o",
                cue="on",
                onset_words=["outside"],
                clean_words=["food", "came", "it"],
                stage=8,
            )
        )
        is None
    )
