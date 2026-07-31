"""`storyforge.quarantine_vocabulary` — the re-status half of `--check-vocabulary`
(DEVIATIONS 32.5).

The risk in this module is not selecting the wrong class of offence — every class
has the same remedy, which is why `offending_stories` deliberately does not read
`classify_offence`. The risk is **what a re-status COSTS**, and it has two halves:

* a DELETE would cascade a real child's recorded history (`answers.question_id` is
  `ON DELETE CASCADE`), so nothing may be deleted;
* pulling a story empties a (stage, theme) pair, and §7's ladder answers an empty
  pair SILENTLY. On the pool this was written against, nine of the eleven offending
  stories were every servable story at stage 5 in all three themes.

Every test below is built around a positive control rather than reading whatever the
live pool happens to hold, because a test that only reads the pool passes vacuously
the moment the pool is clean — the lesson `tests/test_story_words.py::owned_story`
was written for.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge import quarantine_vocabulary as q
from storyforge.db import SessionLocal


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


async def _story(session, *, stage: int, theme: str, body: str, title: str = "A Fixture"):
    """One servable story, plus the question that makes it servable at all (STATE
    invariant 14: no questions row, not servable, and `vocabulary_offences` would
    never see it) and the one segment its body lives in."""
    theme_id = (
        await session.execute(text("SELECT id FROM themes WHERE slug = :t"), {"t": theme})
    ).scalar_one()
    story_id = (
        await session.execute(
            text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (:st, :th, 'warm', :ti, 1.0, "
                "CAST(:w AS text[]), '{}'::jsonb) RETURNING id"
            ),
            {"st": stage, "th": theme_id, "ti": title, "w": []},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
            "VALUES (:s, 0, :b, CAST(:tags AS text[]))"
        ),
        {"s": story_id, "b": body, "tags": []},
    )
    await session.execute(
        text(
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
    return story_id


@pytest.fixture
async def pair(session):
    """Two stage-1 stories: one using a word stage 1 does not permit, one clean.

    `amid` is the REGISTER class's own worked example (11.8) — decodable from stage
    1, so §1's gate does not own it, and refused at every stage by the child-
    vocabulary filter. The clean story is the control: the module must take exactly
    the first, and a test that only had the offender would pass while quarantining
    the whole pool.
    """
    dirty = await _story(session, stage=1, theme="animals", body="A cat sat amid a mat.")
    clean = await _story(session, stage=1, theme="animals", body="A cat sat on a mat.")
    try:
        yield dirty, clean
    finally:
        await session.rollback()
        await session.execute(
            text("DELETE FROM stories WHERE id = ANY(:i)"), {"i": [dirty, clean]}
        )
        await session.commit()


async def test_only_the_offending_story_is_selected(pair, session):
    """The negative half is the one that protects a servable story."""
    dirty, clean = pair
    selected = await q.offending_stories(session)
    assert dirty in selected, "a story using a word no stage permits must be selected"
    assert selected[dirty] == ["amid"]
    assert clean not in selected, (
        "a story every word of which the current vocabulary permits must NOT be "
        "quarantined — pulling it throws away a servable story for nothing"
    )


async def test_only_restricts_the_selection(pair, session):
    dirty, clean = pair
    assert await q.offending_stories(session, only=[clean]) == {}
    assert list(await q.offending_stories(session, only=[dirty])) == [dirty]


async def test_the_quarantine_never_deletes_and_keeps_a_childs_history(pair, session):
    """`answers.question_id` is ON DELETE CASCADE and `questions.story_id` cascades
    from `stories`, so a DELETE here erases a real child's recorded history to fix
    OUR defect. A status change does not."""
    dirty, _ = pair
    kid_id = (
        await session.execute(
            text(
                "INSERT INTO kids (display_name, stage_id) VALUES ('Vocab Kid', 1) "
                "RETURNING id"
            )
        )
    ).scalar_one()
    reading_id = (
        await session.execute(
            text(
                "INSERT INTO readings (kid_id, story_id, stage_id, path, started_at) "
                "VALUES (:k, :s, 1, CAST(:p AS jsonb), now()) RETURNING id"
            ),
            {"k": kid_id, "s": dirty, "p": "[]"},
        )
    ).scalar_one()
    question_id = (
        await session.execute(
            text("SELECT id FROM questions WHERE story_id = :s"), {"s": dirty}
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO answers (reading_id, question_id, correct, answered_at) "
            "VALUES (:r, :q, true, now())"
        ),
        {"r": reading_id, "q": question_id},
    )
    await session.commit()

    try:
        await q.quarantine(session, {dirty: ["amid"]})
        await session.commit()

        assert (
            await session.execute(
                text("SELECT count(*) FROM stories WHERE id = :i"), {"i": dirty}
            )
        ).scalar_one() == 1, "the story row was DELETED"
        assert (
            await session.execute(
                text("SELECT count(*) FROM readings WHERE id = :i"), {"i": reading_id}
            )
        ).scalar_one() == 1, "a real reading was cascaded away"
        assert (
            await session.execute(
                text("SELECT count(*) FROM answers WHERE reading_id = :i"),
                {"i": reading_id},
            )
        ).scalar_one() == 1, "a real recorded ANSWER was cascaded away"
    finally:
        await session.rollback()
        await session.execute(text("DELETE FROM answers WHERE reading_id = :i"), {"i": reading_id})
        await session.execute(text("DELETE FROM readings WHERE id = :i"), {"i": reading_id})
        await session.execute(text("DELETE FROM kids WHERE id = :i"), {"i": kid_id})
        await session.commit()


async def test_the_pull_record_is_dated_and_top_level(pair, session):
    """Two properties one assertion each, and both were paid for elsewhere.

    `at` — `tests/test_phase3.py::_PULLED_AT` coalesces every pull path so that "was
    this rejected row served AFTER it was pulled" stays answerable, and its fourth
    assertion fails any recognised record with no timestamp (31.6: the first version
    of the story-word record had none and twelve rows went red).

    TOP LEVEL — a key under `gen_meta.enrichment` is erased by the next
    `_record_enrichment`, which destroyed four rows of two children's history once
    (11.117).
    """
    dirty, _ = pair
    await q.quarantine(session, {dirty: ["amid"]})
    await session.commit()

    meta = (
        await session.execute(
            text("SELECT gen_meta FROM stories WHERE id = :i"), {"i": dirty}
        )
    ).scalar_one()
    record = meta[q.QUARANTINE_KEY]
    assert record["at"], "the pull record carries no timestamp"
    assert record["previous_status"] == "warm"
    assert record["words"] == ["amid"]
    assert record["reason"]
    assert "enrichment" not in meta or q.QUARANTINE_KEY not in meta.get("enrichment", {})


async def test_the_quarantine_is_idempotent(pair, session):
    """The write removes the row from `SERVABLE_STATUSES` and the selector reads
    servable stories only, so a second run must select nothing."""
    dirty, _ = pair
    await q.quarantine(session, await q.offending_stories(session, only=[dirty]))
    await session.commit()
    assert (
        await session.execute(text("SELECT status FROM stories WHERE id = :i"), {"i": dirty})
    ).scalar_one() == "rejected"
    assert await q.offending_stories(session, only=[dirty]) == {}
