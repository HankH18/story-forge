"""Phase 4: §7 ENRICHING — the node that had no test at all.

Two live failures are the reason this file exists.

1. **Silent degradation.** `enrich` fans QUESTION_GEN and SCENE_TAGGING out with
   `asyncio.gather(..., return_exceptions=True)`, which cannot tell an API blip
   from an `AttributeError`. Both nodes were deliberately gutted and `make warm`
   still printed `warm #17 88% 0 rewrite(s)` and exited 0. Nine servable stories
   reached the pool with no questions and nothing said so.
2. **Unmoderated child-facing text.** §13 requires every string a child reads to
   pass blocklist → allowlist-constrained generation → moderation. `moderate()`
   ran once, on the story body, inside `run_pipeline` — which returns BEFORE
   enrichment starts. Every question prompt and every option, free text from a
   second model rendered full-screen to a five-year-old, passed none of the
   three. Live: the option "The man ran back stop." and the *correct* answer
   "He did not came back." both shipped.

The nodes are stubbed here. That is the point: the live path is proved by
`make warm`, and what these tests pin down is what happens when the live path
misbehaves — which is exactly the part a live run cannot demonstrate on demand.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge import warm
from storyforge.db import SessionLocal
from storyforge.llm.moderate import ModerationResult
from storyforge.llm.pricing import Usage
from storyforge.llm.questions import GeneratedQuestion, QuestionSet
from storyforge.llm.scene_tags import SceneTagResult
from storyforge.models import Story, StorySegment

STAGE = 1
THEME = "dragons"
# Stage-1 decodable: the enrichment nodes are stubbed, but `load_vocabulary` and
# the questions the stubs return still have to be real shapes.
BODIES = ["Sam sat.", "The cat sat.", "Sam and the cat nap."]

Q_USAGE = Usage(model="claude-haiku-4-5", input_tokens=400, output_tokens=80)
TAG_USAGE = Usage(model="claude-haiku-4-5", input_tokens=300, output_tokens=40)
MOD_USAGE = Usage(model="claude-haiku-4-5", input_tokens=90, output_tokens=12)


def questions() -> list[GeneratedQuestion]:
    return [
        GeneratedQuestion(
            qtype="tap_word",
            idx=0,
            prompt="Tap: sit.",
            payload={"options": ["sam", "cat", "sat"], "cue_word": "sit"},
            answer={"correct": "sat", "index": 2, "target_grapheme": "s"},
        ),
        GeneratedQuestion(
            qtype="mcq2",
            idx=1,
            prompt="Did the cat sit?",
            payload={"options": ["The cat sat.", "The cat ran."]},
            answer={"correct": "The cat sat.", "index": 0},
        ),
    ]


def qset(**over) -> QuestionSet:
    return QuestionSet(**{"questions": questions(), "usage": Q_USAGE, "attempts": 1, **over})


def tags() -> SceneTagResult:
    return SceneTagResult(tags=[["cat"], ["cat", "sleeping"], ["cat"]], usage=TAG_USAGE)


APPROVED = ModerationResult(approved=True, reason="fine", layer="model", usage=MOD_USAGE)
REJECTED = ModerationResult(
    approved=False,
    reason='ungrammatical: "The man ran back stop." (stop -> stopped)',
    layer="model",
    usage=MOD_USAGE,
    well_formed=False,
    grammar_note='"The man ran back stop." (stop -> stopped)',
)


# --- fixtures ----------------------------------------------------------------
@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
async def story(session):
    """A real, throwaway story row with real segments. Deleted afterwards."""
    theme_id = (
        await session.execute(text("SELECT id FROM themes WHERE slug = :s"), {"s": THEME})
    ).scalar_one()
    row = Story(
        stage_id=STAGE,
        theme_id=theme_id,
        status="warm",
        title="Sam And The Cat",
        decodability=1.0,
        story_words=["cat"],
        gen_meta={"cost_usd": 0.01},
    )
    session.add(row)
    await session.flush()
    for i, body in enumerate(BODIES):
        session.add(StorySegment(story_id=row.id, idx=i, body=body))
    await session.commit()
    story_id = row.id  # `rollback()` expires the instance; reading it then does IO
    yield row
    # Discard anything a test left pending before deleting the parent row, or
    # the flush on commit inserts children for a story that no longer exists.
    await session.rollback()
    await session.execute(text("DELETE FROM stories WHERE id = :i"), {"i": story_id})
    await session.commit()


@pytest.fixture
def nodes(monkeypatch):
    """Stub the three API calls enrichment makes. Returns a call log."""
    calls: dict = {"moderated": []}

    async def fake_tags(*, segments, theme):
        calls["tag_segments"] = {"segments": list(segments), "theme": theme}
        result = calls.get("tag_result", tags())
        if isinstance(result, BaseException):
            raise result
        return result

    async def fake_questions(**kwargs):
        calls["generate_questions"] = kwargs
        result = calls.get("question_result", qset())
        if isinstance(result, BaseException):
            raise result
        return result

    async def fake_moderate(body):
        calls["moderated"].append(body)
        return calls.get("verdict", APPROVED)

    monkeypatch.setattr(warm, "tag_segments", fake_tags)
    monkeypatch.setattr(warm, "generate_questions", fake_questions)
    monkeypatch.setattr(warm, "moderate", fake_moderate)
    return calls


async def _questions_of(session, story_id: int) -> list[dict]:
    rows = await session.execute(
        text(
            "SELECT qtype, prompt, payload, answer FROM questions "
            "WHERE story_id = :i ORDER BY idx"
        ),
        {"i": story_id},
    )
    return [dict(r._mapping) for r in rows]


async def _tags_of(session, story_id: int) -> list[list[str]]:
    rows = await session.execute(
        text("SELECT scene_tags FROM story_segments WHERE story_id = :i ORDER BY idx"),
        {"i": story_id},
    )
    return [list(r[0] or []) for r in rows]


# --- the happy path ----------------------------------------------------------
async def test_enrich_writes_questions_and_tags(session, story, nodes):
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()

    assert [q["qtype"] for q in await _questions_of(session, story.id)] == ["tap_word", "mcq2"]
    assert await _tags_of(session, story.id) == [["cat"], ["cat", "sleeping"], ["cat"]]
    assert meta["questions"] == 2
    assert meta["segments_tagged"] == 3
    assert meta["segments"] == 3
    assert warm.enrichment_problems(meta) == []


async def test_the_node_gets_the_validated_text_not_a_model_claim(session, story, nodes):
    """STATE invariant 2: what the questions are checked against is the text the
    validator passed, and the DISTINCT story words the report actually found."""
    await warm.enrich(
        session, story, THEME, story_text="Sam sat on the mat.", story_words=["cat"]
    )
    sent = nodes["generate_questions"]
    assert sent["story_text"] == "Sam sat on the mat."
    assert sent["story_words"] == ["cat"]
    assert sent["seed"] == story.id  # reproducible: same story, same questions


# --- §13: the question text is moderated -------------------------------------
async def test_the_model_authored_question_text_goes_to_the_judge(session, story, nodes):
    """Layer 3 sees every string a MODEL wrote, before any row is written.

    Not the tap-a-word text: that prompt is a fixed frame plus a cue word chosen
    by a pure function, and `check_tap_word` rejects any option that is not
    already a word of the story — which `run_pipeline` moderated. Handing the
    judge those bare words instead rejects 16 of 16 live question sets as "not a
    coherent story" (measured), which would strip the whole pool. Layer 1 still
    reads them; see the blocklist tests below.
    """
    await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    assert len(nodes["moderated"]) == 1
    body = nodes["moderated"][0]
    for shown in ("Did the cat sit?", "The cat sat.", "The cat ran."):
        assert shown in body, f"{shown!r} reaches a child unjudged"
    assert "Tap: sit." not in body


async def test_layer_one_reads_every_string_including_the_exempt_ones(
    session, story, nodes, monkeypatch
):
    """§13 layer 1 is deterministic, free and has no opinion about prose, so it
    covers the strings layer 3 is deliberately not shown. A blocklisted word in
    a tap-a-word option must still stop the whole set — with no API call."""
    from storyforge.llm.moderate import moderate as real_moderate

    nodes["question_result"] = qset(
        questions=[
            GeneratedQuestion(
                qtype="tap_word",
                idx=0,
                prompt="Tap: sit.",
                payload={"options": ["sam", "kill", "sat"], "cue_word": "sit"},
                answer={"correct": "sat", "index": 2, "target_grapheme": "s"},
            )
        ]
    )
    called: list[str] = []

    async def counting_moderate(body):
        called.append(body)
        return await real_moderate(body)

    monkeypatch.setattr(warm, "moderate", counting_moderate)
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()

    assert meta["questions_moderation"]["layer"] == "blocklist"
    assert meta["questions_moderation"]["blocked_words"] == ["kill"]
    assert called == [], "layer 1 must short-circuit before any API call"
    assert await _questions_of(session, story.id) == []


async def test_rejected_questions_are_dropped_and_the_story_survives(
    session, story, nodes, capsys
):
    """The story already passed the gate AND its own moderation pass. Only the
    second model's free text is in question, so only that goes.

    The tap-a-word question survives a layer-3 rejection on purpose: its text
    was never in the judged body, and it is the only question type that carries
    §8 mastery evidence. Live case — story #9's mcq2 asked "Did the man ran
    off?" while its phonics question was perfectly fine.
    """
    nodes["verdict"] = REJECTED
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()

    kept = await _questions_of(session, story.id)
    assert [q["qtype"] for q in kept] == ["tap_word"]
    assert kept[0]["prompt"] == "Tap: sit."
    assert meta["questions"] == 1
    assert meta["questions_moderation"]["approved"] is False
    assert "ungrammatical" in meta["questions_moderation"]["reason"]
    assert meta["questions_rejected"] == ["mcq2"]

    survived = (
        await session.execute(text("SELECT status FROM stories WHERE id = :i"), {"i": story.id})
    ).scalar_one()
    assert survived == "warm"
    # The pictures are unaffected: they were never in question.
    assert await _tags_of(session, story.id) == [["cat"], ["cat", "sleeping"], ["cat"]]
    # A partial drop is not a failed run — the story is still servable and still
    # earns mastery evidence — but the operator is told what was thrown away.
    assert warm.enrichment_problems(meta) == []
    warm._report_enrichment(story.id, meta)
    assert "questions REJECTED by moderation" in capsys.readouterr().out


async def test_a_blocklisted_word_in_an_option_never_reaches_the_database(
    session, story, nodes, monkeypatch
):
    """§13 layer 1 through the REAL `moderate()`: deterministic, free, and it
    cannot be talked out of it. No stubbed verdict — the blocklist decides."""
    from storyforge.llm.moderate import moderate as real_moderate

    nodes["question_result"] = qset(
        questions=[
            GeneratedQuestion(
                qtype="mcq2",
                idx=0,
                prompt="Did the man run?",
                payload={"options": ["The man was hit.", "The man will kill the cat."]},
                answer={"correct": "The man was hit.", "index": 0},
            )
        ]
    )
    monkeypatch.setattr(warm, "moderate", real_moderate)

    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()

    assert meta["questions_moderation"]["layer"] == "blocklist"
    assert "kill" in meta["questions_moderation"]["blocked_words"]
    assert await _questions_of(session, story.id) == []


async def test_moderation_is_not_called_when_there_are_no_questions(session, story, nodes):
    """§7: never spend a Haiku call on nothing."""
    nodes["question_result"] = qset(questions=[], dropped={"tap_word": ["no sound"]})
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    assert nodes["moderated"] == []
    assert "questions_moderation" not in meta


# --- degraded paths ----------------------------------------------------------
async def test_a_real_code_bug_is_reported_rather_than_swallowed(session, story, nodes):
    """The live failure, reproduced: `return_exceptions=True` cannot tell an
    `AttributeError` from a timeout, so nothing downstream may treat the two the
    same. The exception TYPE has to survive into `gen_meta`."""
    nodes["question_result"] = AttributeError("'NoneType' object has no attribute 'questions'")
    nodes["tag_result"] = AttributeError("'NoneType' object has no attribute 'tags'")

    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()

    assert meta["questions_error"].startswith("AttributeError: ")
    assert meta["scene_tags_error"].startswith("AttributeError: ")
    assert meta["questions"] == 0 and meta["segments_tagged"] == 0
    assert await _questions_of(session, story.id) == []

    problems = warm.enrichment_problems(meta)
    assert len(problems) == 2
    assert all("AttributeError" in p for p in problems)


async def test_a_tagging_failure_does_not_cost_the_story_its_questions(session, story, nodes):
    """§7/DEVIATIONS 4.4: illustration is decorative. The two nodes are
    independent and one falling over must not take the other with it."""
    nodes["tag_result"] = TimeoutError("read timed out")
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()

    assert len(await _questions_of(session, story.id)) == 2
    assert meta["scene_tags_error"].startswith("TimeoutError: ")
    assert await _tags_of(session, story.id) == [[], [], []]


async def test_a_degraded_tagging_response_is_reported_as_a_problem(session, story, nodes):
    """No exception, no error key — and still nothing on screen. `degraded` is
    the §12 placeholder path and it must not read as success."""
    nodes["tag_result"] = SceneTagResult(tags=[[], [], []], usage=TAG_USAGE, degraded=True)
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))

    assert meta["scene_tags_degraded"] is True
    assert meta["segments_tagged"] == 0
    assert any("0 tagged segments" in p for p in warm.enrichment_problems(meta))


async def test_a_story_that_ends_with_no_questions_is_a_problem(session, story, nodes):
    nodes["question_result"] = qset(questions=[], dropped={"mcq2": ["retry budget exhausted"]})
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    assert any("0 questions" in p for p in warm.enrichment_problems(meta))


# --- §15 / §14: cost and tracing ---------------------------------------------
async def test_every_enrichment_call_is_counted_in_the_cost(session, story, nodes):
    """`QuestionSet.usage` and `SceneTagResult.usage` were populated and then
    never read, so every reported cost was roughly half of what was spent."""
    from datetime import date

    on = date.today()
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES), on=on)
    expected = Q_USAGE.cost_usd(on) + TAG_USAGE.cost_usd(on) + MOD_USAGE.cost_usd(on)

    assert meta["cost_usd"] == pytest.approx(round(expected, 6))
    assert meta["cost_usd"] > 0

    await warm._record_enrichment(session, story, meta, 0.01)
    assert story.gen_meta["cost_usd"] == pytest.approx(round(0.01 + expected, 6))
    assert story.gen_meta["enrichment"] == meta


async def test_the_enrichment_nodes_are_traced_under_the_story_trace(
    session, story, nodes, monkeypatch
):
    """§15: five nodes, one trace. QUESTION_GEN and SCENE_TAGGING were two of the
    five and neither had a span, so half the run's cost was invisible."""
    from storyforge import obs
    from tests.test_obs import RecordingClient

    client = RecordingClient()
    monkeypatch.setattr(obs, "_client", client)
    story.langfuse_trace_id = "abc123"
    await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))

    names = client.names()
    for node in ("ENRICHING", "QUESTION_GEN", "SCENE_TAGGING", "MODERATING"):
        assert node in names
    # The nodes belong to the trace that produced the story, not to traces of
    # their own — `run_pipeline`'s trace context is already closed by now.
    assert client.span("ENRICHING")["trace_context"] == {"trace_id": "abc123"}
    for node in ("QUESTION_GEN", "SCENE_TAGGING", "MODERATING"):
        span = client.span(node)
        assert span["as_type"] == "generation"
        update = span["updates"][-1]
        # §15 wants tokens AND cost on the span, not just a name.
        assert update["usage_details"]["output"] > 0
        assert update["cost_details"]["total"] > 0


# --- run_warm: the exit code -------------------------------------------------
async def test_run_warm_exits_non_zero_when_a_story_lands_unenriched(monkeypatch, nodes, capsys):
    """The audit's headline: both nodes gutted, `warm #17 88% 0 rewrite(s)`
    printed, exit 0. Never again."""
    from storyforge.decoder import mastered_ids, validate_text
    from storyforge.pipeline import Attempt, PipelineResult

    body = " ".join(BODIES)
    report = validate_text(body, mastered_ids(STAGE), frozenset(["the", "and"]), frozenset())
    usage = Usage(model="claude-sonnet-5", input_tokens=100, output_tokens=50)

    async def fake_pipeline(session, stage_id, theme_slug, effort="medium"):
        return PipelineResult(
            stage_id=stage_id,
            theme=theme_slug,
            passed=True,
            attempts=[
                Attempt(
                    n=0,
                    kind="generate",
                    text=body,
                    decodability=1.0,
                    violations=[],
                    usage=usage,
                )
            ],
            final_text=body,
            final_report=report,
            title="Sam And The Cat",
            story_words=[],
            moderation=ModerationResult(approved=True, reason="ok", layer="model"),
        )

    async def full_pool(session, stage_id, theme_id):
        return 0

    monkeypatch.setattr(warm, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(warm, "pool_depth", full_pool)
    nodes["question_result"] = AttributeError("'NoneType' object has no attribute 'questions'")

    async with SessionLocal() as s:
        before = (await s.execute(text("SELECT coalesce(max(id), 0) FROM stories"))).scalar_one()
    try:
        with pytest.raises(SystemExit) as exit_info:
            await warm.run_warm([STAGE], [THEME], 1, "medium")
    finally:
        async with SessionLocal() as s:
            await s.execute(text("DELETE FROM stories WHERE id > :i"), {"i": before})
            await s.commit()

    message = str(exit_info.value)
    assert "unenriched" in message
    assert "AttributeError" in message
    # And it is visible on the way past, not only in the exit code.
    assert "AttributeError" in capsys.readouterr().out


async def test_run_warm_is_silent_about_problems_when_there_are_none(monkeypatch, nodes, capsys):
    """The other direction — a green run must stay green, or the new exit code
    is just a broken build."""
    from storyforge.decoder import mastered_ids, validate_text
    from storyforge.pipeline import Attempt, PipelineResult

    body = " ".join(BODIES)
    report = validate_text(body, mastered_ids(STAGE), frozenset(["the", "and"]), frozenset())
    usage = Usage(model="claude-sonnet-5", input_tokens=100, output_tokens=50)

    async def fake_pipeline(session, stage_id, theme_slug, effort="medium"):
        return PipelineResult(
            stage_id=stage_id,
            theme=theme_slug,
            passed=True,
            attempts=[
                Attempt(
                    n=0,
                    kind="generate",
                    text=body,
                    decodability=1.0,
                    violations=[],
                    usage=usage,
                )
            ],
            final_text=body,
            final_report=report,
            title="Sam And The Cat",
            story_words=[],
            moderation=ModerationResult(approved=True, reason="ok", layer="model"),
        )

    async def empty_pool(session, stage_id, theme_id):
        return 0

    monkeypatch.setattr(warm, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(warm, "pool_depth", empty_pool)

    async with SessionLocal() as s:
        before = (await s.execute(text("SELECT coalesce(max(id), 0) FROM stories"))).scalar_one()
    try:
        await warm.run_warm([STAGE], [THEME], 1, "medium")
        out = capsys.readouterr().out
        assert "1 warm, 0 rejected" in out
        assert "2 question(s)" in out
        async with SessionLocal() as s:
            made = (
                await s.execute(text("SELECT id FROM stories WHERE id > :i"), {"i": before})
            ).scalars().all()
            assert len(made) == 1
            rows = await _questions_of(s, made[0])
            assert [q["qtype"] for q in rows] == ["tap_word", "mcq2"]
            # The answer key is written to `answer`, never to the served payload.
            assert "target_grapheme" not in rows[0]["payload"]
            assert rows[0]["answer"]["target_grapheme"] == "s"
    finally:
        async with SessionLocal() as s:
            await s.execute(text("DELETE FROM stories WHERE id > :i"), {"i": before})
            await s.commit()


# --- the backfill ------------------------------------------------------------
async def test_backfill_enriches_a_servable_story_that_has_no_questions(
    session, story, nodes, capsys
):
    """Nine such stories existed in the live pool. `--backfill` is how they were
    repaired, through the same code path a fresh warm run uses."""
    assert await _questions_of(session, story.id) == []

    await warm.run_backfill(only=[story.id])

    async with SessionLocal() as s:
        assert [q["qtype"] for q in await _questions_of(s, story.id)] == ["tap_word", "mcq2"]
        assert await _tags_of(s, story.id) == [["cat"], ["cat", "sleeping"], ["cat"]]
        meta = (
            await s.execute(text("SELECT gen_meta FROM stories WHERE id = :i"), {"i": story.id})
        ).scalar_one()
    assert meta["enrichment"]["questions"] == 2
    # The pipeline's own cost is preserved and enrichment is ADDED to it.
    assert meta["cost_usd"] > 0.01
    assert "1 of 1 backfilled" in capsys.readouterr().out


async def test_backfill_moves_a_legacy_answer_key_out_of_the_served_payload(
    session, story, nodes
):
    """DEVIATIONS 4.9 for rows already in the database: `target_grapheme` used to
    be written into `payload`, from which the answer is
    `[o for o in options if o.startswith(g)][0]`."""
    await session.execute(
        text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', 'Tap: sit.', "
            """CAST('{"options": ["sat", "cat"], "target_grapheme": "s"}' AS jsonb), """
            """CAST('{"correct": "sat", "index": 0}' AS jsonb))"""
        ),
        {"s": story.id},
    )
    await session.commit()

    await warm.run_backfill(only=[story.id])

    async with SessionLocal() as s:
        row = (await _questions_of(s, story.id))[0]
    assert "target_grapheme" not in row["payload"]
    assert row["answer"]["target_grapheme"] == "s"
    assert row["payload"]["options"] == ["sat", "cat"]
