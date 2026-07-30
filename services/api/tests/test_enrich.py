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
# `kids.display_name` is constrained to a plain first name — no digits — so the
# throwaway reader `_answer` creates is identified by a distinctive one.
KID_NAME = "Enrichfixture"


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
    # `readings`/`answers` cascade off the story; the kid `_answer` made does not.
    await session.execute(text("DELETE FROM kids WHERE display_name = :n"), {"n": KID_NAME})
    await session.commit()


@pytest.fixture
def nodes(monkeypatch):
    """Stub the three API calls enrichment makes. Returns a call log."""
    calls: dict = {"moderated": [], "question_calls": []}

    async def fake_tags(*, segments, theme):
        calls["tag_segments"] = {"segments": list(segments), "theme": theme}
        result = calls.get("tag_result", tags())
        if isinstance(result, BaseException):
            raise result
        return result

    async def fake_questions(**kwargs):
        calls["generate_questions"] = kwargs
        calls["question_calls"].append(kwargs)
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
            "SELECT id, qtype, prompt, payload, answer FROM questions "
            "WHERE story_id = :i ORDER BY idx"
        ),
        {"i": story_id},
    )
    return [dict(r._mapping) for r in rows]


#: `--backfill` selects on the ABSENCE of a §13 receipt, so these two helpers are
#: how a test says "already judged" / "never judged" without reaching into
#: `_UNENRICHED_SQL` and restating it.
async def _mark_moderated(session, story_id: int) -> None:
    await session.execute(
        text(
            "UPDATE stories SET gen_meta = coalesce(gen_meta, '{}'::jsonb) || "
            """CAST('{"enrichment": {"questions_moderation": {"approved": true}}}' AS jsonb) """
            "WHERE id = :i"
        ),
        {"i": story_id},
    )


async def _clear_moderation(session, story_id: int) -> None:
    await session.execute(
        text("UPDATE stories SET gen_meta = gen_meta #- '{enrichment,questions_moderation}' "
             "WHERE id = :i"),
        {"i": story_id},
    )


async def _answer(session, story, question_id: int) -> int:
    """A real reading with a real answer row — the thing that must not be lost.

    `answers.question_id` is ON DELETE CASCADE, so this is exactly the state in
    which replacing a question row silently destroys §8 evidence.
    """
    kid_id = (
        await session.execute(
            text(
                "INSERT INTO kids (display_name, stage_id) VALUES (:n, :st) RETURNING id"
            ),
            {"n": KID_NAME, "st": story.stage_id},
        )
    ).scalar_one()
    reading_id = (
        await session.execute(
            text(
                "INSERT INTO readings (kid_id, story_id, stage_id, path, completed_at) "
                "VALUES (:k, :s, :st, CAST('{\"choices\": []}' AS jsonb), now()) RETURNING id"
            ),
            {"k": kid_id, "s": story.id, "st": story.stage_id},
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO answers (reading_id, question_id, correct) VALUES (:r, :q, true)"),
        {"r": reading_id, "q": question_id},
    )
    return kid_id


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


# --- run_warm: §7's api_fail / timeout fallback (DEVIATIONS 8.4) -------------
# The standing finding in docs/STATE.md and docs/RESUME.md JOB C: one
# `anthropic.APITimeoutError` out of the GENERATING node aborted the entire run
# and discarded every remaining (stage, theme) pair. §7 maps `timeout` to a
# fallback like every other member of its taxonomy.
#
# These tests raise the REAL exception class, not a stand-in, because the whole
# question is whether that specific class escapes the loop. The discriminator
# is `len(made) == 1`: before the fix the exception propagated out of the first
# iteration, so no second story could exist.
def _good_result(stage, theme):
    from storyforge.decoder import mastered_ids, validate_text
    from storyforge.pipeline import Attempt, PipelineResult

    body = " ".join(BODIES)
    report = validate_text(body, mastered_ids(stage), frozenset(["the", "and"]), frozenset())
    return PipelineResult(
        stage_id=stage,
        theme=theme,
        passed=True,
        attempts=[
            Attempt(
                n=0,
                kind="generate",
                text=body,
                decodability=1.0,
                violations=[],
                usage=Usage(model="claude-sonnet-5", input_tokens=100, output_tokens=50),
            )
        ],
        final_text=body,
        final_report=report,
        title="Sam And The Cat",
        story_words=[],
        moderation=ModerationResult(approved=True, reason="ok", layer="model"),
    )


def _timeout() -> Exception:
    import httpx
    from anthropic import APITimeoutError

    return APITimeoutError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )


async def test_a_generation_timeout_does_not_abort_the_rest_of_the_warm_run(
    monkeypatch, nodes, capsys
):
    """The fix, stated as the thing that used to be impossible: a story generated
    AFTER a timeout in the same run."""
    calls = []

    async def flaky_pipeline(session, stage_id, theme_slug, effort="medium"):
        calls.append((stage_id, theme_slug))
        if len(calls) == 1:
            raise _timeout()
        return _good_result(stage_id, theme_slug)

    async def empty_pool(session, stage_id, theme_id):
        return 0

    monkeypatch.setattr(warm, "run_pipeline", flaky_pipeline)
    monkeypatch.setattr(warm, "pool_depth", empty_pool)

    async with SessionLocal() as s:
        before = (await s.execute(text("SELECT coalesce(max(id), 0) FROM stories"))).scalar_one()
    try:
        # Non-zero, because a pair below target IS a failed warm run — but at the
        # END, having attempted everything, not from inside the first iteration.
        with pytest.raises(SystemExit) as exit_info:
            await warm.run_warm([STAGE], [THEME], 2, "medium")

        # THE discriminator, and it is process-local so no amount of concurrent
        # database traffic can move it: the loop reached its second iteration.
        # Before the fix the exception left `run_warm` from the first one.
        assert len(calls) == 2, "the run stopped at the timeout instead of continuing"

        # And the second iteration really persisted a story. Matched on the
        # fixture's own title rather than on `id > max(id)`: this suite shares a
        # live database with concurrent `make warm` runs, and a bare id window
        # counts their stories too.
        async with SessionLocal() as s:
            made = (
                await s.execute(
                    text("SELECT id FROM stories WHERE id > :i AND title = :t"),
                    {"i": before, "t": "Sam And The Cat"},
                )
            ).scalars().all()
        assert len(made) == 1, "the story after the timeout was never written"

        # The exit names the failure, its type and where it happened, and points
        # at the remedy that actually works (`pool_depth` makes warm idempotent).
        message = str(exit_info.value)
        assert "APITimeoutError" in message
        assert f"stage {STAGE}" in message and THEME in message
        assert "below target" in message and "make warm" in message

        out = capsys.readouterr().out
        # Visible as it happens, not only in the exit code.
        assert "API FAIL" in out and "APITimeoutError" in out
        # And counted in the one-line summary next to the other outcomes.
        assert "1 warm, 0 rejected, 1 api_fail" in out
    finally:
        # Only this test's own rows — a concurrent `make warm` is writing to the
        # same table and its stories are not ours to delete.
        async with SessionLocal() as s:
            await s.execute(
                text("DELETE FROM stories WHERE id > :i AND title = :t"),
                {"i": before, "t": "Sam And The Cat"},
            )
            await s.commit()


async def test_a_timeout_on_one_pair_does_not_stop_the_next_pair(monkeypatch, nodes, capsys):
    """The expensive half of the old behaviour: `make warm` walks stages x themes,
    so aborting on the first pair threw away every pair after it."""
    seen = []

    async def always_timeout(session, stage_id, theme_slug, effort="medium"):
        seen.append((stage_id, theme_slug))
        raise _timeout()

    async def empty_pool(session, stage_id, theme_id):
        return 0

    monkeypatch.setattr(warm, "run_pipeline", always_timeout)
    monkeypatch.setattr(warm, "pool_depth", empty_pool)

    with pytest.raises(SystemExit) as exit_info:
        await warm.run_warm([STAGE], ["animals", "dragons", "space"], 1, "medium")

    # Every pair was tried. Before the fix this list had one entry.
    assert [t for _, t in seen] == ["animals", "dragons", "space"]
    # Nothing was made, and the message says WHY rather than just "no stories".
    assert "produced no warm stories" in str(exit_info.value)
    assert "APITimeoutError" in str(exit_info.value)
    assert capsys.readouterr().out.count("API FAIL") == 3


async def test_a_keyboard_interrupt_still_stops_the_warm_run(monkeypatch, nodes):
    """The other direction. `except BaseException` is broad on purpose, so the
    exclusion has to be tested or the run becomes uninterruptible — and a warm
    run is a long-running job someone will ctrl-C."""
    seen = []

    async def interrupted(session, stage_id, theme_slug, effort="medium"):
        seen.append(theme_slug)
        raise KeyboardInterrupt

    async def empty_pool(session, stage_id, theme_id):
        return 0

    monkeypatch.setattr(warm, "run_pipeline", interrupted)
    monkeypatch.setattr(warm, "pool_depth", empty_pool)

    with pytest.raises(KeyboardInterrupt):
        await warm.run_warm([STAGE], ["animals", "dragons"], 1, "medium")
    assert seen == ["animals"], "a signal must not be swallowed as an api_fail"


async def test_a_clean_run_reports_zero_api_failures_and_exits_zero(monkeypatch, nodes, capsys):
    """DEVIATIONS 8.4 adds a second non-zero exit path; a green run must stay
    green or the new exit code is just a broken build."""

    async def good_pipeline(session, stage_id, theme_slug, effort="medium"):
        return _good_result(stage_id, theme_slug)

    async def empty_pool(session, stage_id, theme_id):
        return 0

    monkeypatch.setattr(warm, "run_pipeline", good_pipeline)
    monkeypatch.setattr(warm, "pool_depth", empty_pool)

    async with SessionLocal() as s:
        before = (await s.execute(text("SELECT coalesce(max(id), 0) FROM stories"))).scalar_one()
    try:
        await warm.run_warm([STAGE], [THEME], 1, "medium")
        out = capsys.readouterr().out
        assert "1 warm, 0 rejected, 0 api_fail" in out
        assert "API FAIL" not in out
        assert "never produced a story" not in out
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
    `[o for o in options if o.startswith(g)][0]`.

    The story is given a §13 receipt so `--backfill` does NOT select it for
    replacement, which isolates the one statement under test. Without that this
    went vacuous the moment the backfill learned to replace rows: the row it
    inspected was then a freshly generated one, so both `target_grapheme`
    assertions passed whether or not the repair statement had run at all.
    """
    await session.execute(
        text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', 'Tap: sit.', "
            """CAST('{"options": ["sat", "cat"], "target_grapheme": "s"}' AS jsonb), """
            """CAST('{"correct": "sat", "index": 0}' AS jsonb))"""
        ),
        {"s": story.id},
    )
    await _mark_moderated(session, story.id)
    await session.commit()

    await warm.run_backfill(only=[story.id])

    async with SessionLocal() as s:
        rows = await _questions_of(s, story.id)
    assert len(rows) == 1
    row = rows[0]
    assert "target_grapheme" not in row["payload"]
    assert row["answer"]["target_grapheme"] == "s"
    # Repaired IN PLACE — the legacy row is still the row, options untouched.
    assert row["payload"]["options"] == ["sat", "cat"]
    assert row["prompt"] == "Tap: sit."


async def test_backfill_re_derives_a_stale_tap_word_prompt_in_place(session, story, nodes):
    """§11.3: the prompt is a pure function of (cue, stage), so a rebuilt frame
    ladder reaches rows already in the pool WITHOUT regenerating them.

    That is the only way it can reach a story a child has read: `answers`
    cascades off `questions`, so those rows cannot be replaced (see below), and
    19 of 28 live rows were still reading the bare cue `Tap: get.` after the
    ladder was fixed. Nothing here calls an API.
    """
    await session.execute(
        text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', 'Tap: sat.', "
            """CAST('{"options": ["cat", "sam", "sit"], "cue_word": "sat"}' AS jsonb), """
            """CAST('{"correct": "sam", "index": 1}' AS jsonb))"""
        ),
        {"s": story.id},
    )
    await _mark_moderated(session, story.id)  # not a backfill candidate
    await session.commit()

    await warm.run_backfill(only=[story.id])

    async with SessionLocal() as s:
        rows = await _questions_of(s, story.id)
        # Idempotent: the second run changes nothing.
        again, skipped = await warm.refresh_tap_word_prompts(s, only=[story.id])
    assert len(rows) == 1
    assert rows[0]["prompt"] == "Tap it: sat.", "the stale frame survived the ladder rebuild"
    assert rows[0]["payload"]["options"] == ["cat", "sam", "sit"], "the row was replaced"
    assert (again, skipped) == (0, [])


# --- the §13 question pass is not forward-only -------------------------------
async def test_backfill_reaches_a_story_whose_questions_were_never_moderated(
    session, story, nodes, capsys
):
    """The audit finding this exists for.

    `_UNENRICHED_SQL` used to match only `NOT EXISTS (questions)`, which made
    the §13 question-moderation fix FORWARD-ONLY: the servable stories that
    already carried unmoderated questions were exactly the set every
    `--backfill` run skipped, forever, and no other code path re-moderated a
    question row. Nine of 28 were in that state; story 17 went on serving the
    option that motivated the fix, "The man ran back stop."
    """
    await session.execute(
        text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'mcq2', 'Did the man run back fast?', "
            """CAST('{"options": ["The man ran back fast.", "The man ran back stop."]}' """
            """AS jsonb), CAST('{"correct": "The man ran back fast.", "index": 0}' AS jsonb))"""
        ),
        {"s": story.id},
    )
    await session.commit()
    assert len(await _questions_of(session, story.id)) == 1

    await warm.run_backfill(only=[story.id])

    async with SessionLocal() as s:
        rows = await _questions_of(s, story.id)
        meta = (
            await s.execute(text("SELECT gen_meta FROM stories WHERE id = :i"), {"i": story.id})
        ).scalar_one()

    # Replaced, not appended: an append would have left the bad string fetchable.
    assert [q["qtype"] for q in rows] == ["tap_word", "mcq2"]
    served = [o for q in rows for o in q["payload"]["options"]]
    assert "The man ran back stop." not in served
    assert meta["enrichment"]["questions_replaced"] == 1
    assert meta["enrichment"]["questions_moderation"]["approved"] is True
    assert "1 servable story/stories with missing or unmoderated questions" in (
        capsys.readouterr().out
    )


async def test_a_story_with_a_moderation_receipt_is_left_alone(session, story, nodes):
    """Idempotence, and why the widened predicate is safe to sweep the whole
    pool with: a story that HAS been judged is not judged again, so a repeat
    `--backfill` does not spend a Haiku call per story per run."""
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await warm._record_enrichment(session, story, meta, 0.01)
    await session.commit()
    before = [q["id"] for q in await _questions_of(session, story.id)]
    judged = len(nodes["moderated"])

    await warm.run_backfill(only=[story.id])

    async with SessionLocal() as s:
        assert [q["id"] for q in await _questions_of(s, story.id)] == before
    assert len(nodes["moderated"]) == judged, "an already-judged story was judged again"


async def test_an_answered_question_is_re_moderated_instead_of_regenerated(
    session, story, nodes
):
    """§8 evidence outranks regeneration.

    `answers.question_id` is ON DELETE CASCADE, so replacing a question a child
    has already answered would silently take the answer row with it — and for a
    `tap_word`, the evidence behind a promotion decision. QUESTION_GEN is
    therefore not run at all for these stories; the text already in the table
    goes through the same §13 path instead, so the receipt still gets written.
    """
    await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()
    rows = await _questions_of(session, story.id)
    tap = next(q for q in rows if q["qtype"] == "tap_word")
    await _answer(session, story, tap["id"])
    await _clear_moderation(session, story.id)
    await session.commit()

    generated = len(nodes["question_calls"])
    await warm.run_backfill(only=[story.id])

    async with SessionLocal() as s:
        after = await _questions_of(s, story.id)
        meta = (
            await s.execute(text("SELECT gen_meta FROM stories WHERE id = :i"), {"i": story.id})
        ).scalar_one()
        kept = (
            await s.execute(
                text("SELECT count(*) FROM answers WHERE question_id = :q"), {"q": tap["id"]}
            )
        ).scalar_one()

    assert len(nodes["question_calls"]) == generated, "QUESTION_GEN ran on an answered story"
    assert [q["id"] for q in after] == [q["id"] for q in rows], "rows were replaced"
    assert kept == 1, "the answer row was destroyed"
    assert meta["enrichment"]["questions_moderation"]["approved"] is True
    assert meta["enrichment"]["questions_source"].startswith("existing rows")


async def test_a_rejected_answered_mastery_question_is_kept_and_reported(
    session, story, nodes
):
    """The one case this refuses to decide by itself.

    A `tap_word` row carrying an answer is §8 evidence (`MASTERY_QTYPES`), so a
    §13 rejection cannot remove it. The hazard is NAMED and the run fails,
    rather than either destroying the evidence or quietly going on serving text
    the judge rejected.
    """
    await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()
    rows = await _questions_of(session, story.id)
    tap = next(q for q in rows if q["qtype"] == "tap_word")
    await _answer(session, story, tap["id"])
    await _clear_moderation(session, story.id)
    await session.commit()

    # A blocklist verdict is the unattributable one: normally nothing survives it.
    nodes["verdict"] = ModerationResult(
        approved=False, reason="blocklisted", layer="blocklist", blocked_words=["x"]
    )
    meta = await warm.enrich(session, story, THEME, story_text=" ".join(BODIES))
    await session.commit()

    assert meta["questions_unremovable"] == [tap["id"]]
    assert any("cascade §8 mastery evidence" in p for p in warm.enrichment_problems(meta))
    async with SessionLocal() as s:
        left = await _questions_of(s, story.id)
    # The answered tap_word survives; the unanswered mcq2 does not.
    assert [q["id"] for q in left] == [tap["id"]]
