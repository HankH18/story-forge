"""`storyforge.quarantine_story_words` — the far half of §5.5 (DEVIATIONS 31.3).

`--check-story-words` names two populations with opposite remedies, and the whole
risk in this module is confusing them:

* a **wasted** chip (the reader can already decode the word) is FREE to drop —
  `--story-word-repair` owns it, the story stays servable;
* a **far** chip (more than `STORY_WORD_MAX_LOOKAHEAD` stages ahead) cannot be
  dropped at all, because the word is in the text either way. The story leaves the
  pool.

So the assertion that matters most here is a NEGATIVE one: quarantining a story
over a *wasted* chip would throw away a servable story to fix a bookkeeping error
— the "data fix disguised as a purge" DEVIATIONS 22.9 refused. Every test below is
built around a positive control, because a test that only reads whatever the live
pool happens to hold passes vacuously the moment the pool is clean (the lesson
`tests/test_story_words.py::owned_story` was written for, after a sabotage run
made `chip_offences` report nothing and every assertion still passed).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge import quarantine_story_words as q
from storyforge.db import SessionLocal


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


async def _story(session, *, stage: int, theme: str, words: list[str], status: str = "warm"):
    """One servable story with `words` as its chips, plus the question that makes
    it servable at all (STATE invariant 14: no questions row, not servable, and
    `chip_offences` would never see it)."""
    theme_id = (
        await session.execute(text("SELECT id FROM themes WHERE slug = :t"), {"t": theme})
    ).scalar_one()
    story_id = (
        await session.execute(
            text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (:st, :th, :status, 'Quarantine Fixture', "
                "1.0, CAST(:w AS text[]), '{}'::jsonb) RETURNING id"
            ),
            {"st": stage, "th": theme_id, "status": status, "w": words},
        )
    ).scalar_one()
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
    """Two stage-1 stories: one carrying ONLY a far chip, one ONLY a wasted chip.

    `giraffe` is decodable at stage 8 — seven stages ahead of a stage-1 reader, so
    far. `rabbit` is decodable at stage 1 — wasted. One of each is the point: the
    module must take exactly the first.
    """
    far = await _story(session, stage=1, theme="animals", words=["giraffe"])
    wasted = await _story(session, stage=1, theme="animals", words=["rabbit"])
    try:
        yield far, wasted
    finally:
        await session.rollback()
        await session.execute(
            text("DELETE FROM stories WHERE id = ANY(:i)"), {"i": [far, wasted]}
        )
        await session.commit()


async def test_only_the_far_half_is_selected(pair, session):
    """The negative half is the one that protects a servable story."""
    far, wasted = pair
    selected = await q.far_chip_stories(session)
    assert far in selected, "a chip 7 stages ahead of the reader must be selected"
    assert selected[far] == ["giraffe"]
    assert wasted not in selected, (
        "a story whose only offence is a chip the reader can ALREADY decode must "
        "NOT be quarantined — that is `--story-word-repair`'s free remedy, and "
        "pulling the story instead throws away a servable story to fix a "
        "bookkeeping error (DEVIATIONS 22.9)"
    )


async def test_only_restricts_the_selection(pair, session):
    far, wasted = pair
    assert await q.far_chip_stories(session, only=[wasted]) == {}
    assert list(await q.far_chip_stories(session, only=[far])) == [far]


async def test_the_quarantine_never_deletes_and_keeps_a_childs_history(pair, session):
    """`answers.question_id` is ON DELETE CASCADE and `questions.story_id` cascades
    from `stories`, so a DELETE here erases a real child's recorded history to fix
    OUR defect. A status change does not."""
    far, _ = pair
    kid_id = (
        await session.execute(
            text(
                "INSERT INTO kids (display_name, stage_id) VALUES ('Quarantine Kid', 1) "
                "RETURNING id"
            )
        )
    ).scalar_one()
    reading_id = (
        await session.execute(
            text(
                "INSERT INTO readings (kid_id, story_id, stage_id, path) "
                "VALUES (:k, :s, 1, '[]'::jsonb) RETURNING id"
            ),
            {"k": kid_id, "s": far},
        )
    ).scalar_one()
    question_id = (
        await session.execute(
            text("SELECT id FROM questions WHERE story_id = :s"), {"s": far}
        )
    ).scalar_one()
    await session.execute(
        text("INSERT INTO answers (reading_id, question_id, correct) VALUES (:r, :q, true)"),
        {"r": reading_id, "q": question_id},
    )
    await session.commit()

    await q.quarantine(session, await q.far_chip_stories(session, only=[far]))
    await session.commit()

    row = (
        await session.execute(
            text("SELECT status FROM stories WHERE id = :i"), {"i": far}
        )
    ).first()
    assert row is not None, "the story row was DELETED — a child's history went with it"
    assert row.status == "rejected"
    assert (
        await session.execute(
            text("SELECT count(*) FROM readings WHERE id = :r"), {"r": reading_id}
        )
    ).scalar_one() == 1, "the reading was cascaded away"
    assert (
        await session.execute(
            text("SELECT count(*) FROM answers WHERE reading_id = :r"), {"r": reading_id}
        )
    ).scalar_one() == 1, "the child's answer was cascaded away"

    await session.execute(text("DELETE FROM answers WHERE reading_id = :r"), {"r": reading_id})
    await session.execute(text("DELETE FROM readings WHERE id = :r"), {"r": reading_id})
    await session.execute(text("DELETE FROM kids WHERE id = :k"), {"k": kid_id})
    await session.commit()


async def test_the_pull_record_carries_its_own_time(pair, session):
    """The defect the suite caught on the first version of this module.

    `tests/test_phase3.py::_PULL_PATHS` coalesces every pull path into one
    expression so "was this rejected row served AFTER it was pulled" stays
    answerable; a record with a reason and no `at` reads as NULL there and is
    invisible on an unserved row. Asserted here at the producer too, so the
    failure is attributed to the writer rather than to the pool.
    """
    far, _ = pair
    await q.quarantine(session, await q.far_chip_stories(session, only=[far]))
    await session.commit()
    record = (
        await session.execute(
            text("SELECT gen_meta -> :k FROM stories WHERE id = :i"),
            {"k": q.QUARANTINE_KEY, "i": far},
        )
    ).scalar_one()
    assert record is not None, "no pull record was written at all"
    assert record.get("at"), (
        "the pull record carries no `at`, so `_PULLED_AT` reads NULL for this row "
        "and the story is indistinguishable from one `_SERVE_SQL` leaked"
    )
    assert record["previous_status"] == "warm"
    assert record["words"] == ["giraffe"]
    assert "31.3" in record["deviation"]


async def test_the_record_is_at_the_top_level_never_under_enrichment(pair, session):
    """DEVIATIONS 11.117: `_record_enrichment` replaces `gen_meta.enrichment`
    wholesale by design, which erased four rows of two real children's history. A
    key that must outlive an enrichment must not live inside one."""
    far, _ = pair
    await q.quarantine(session, await q.far_chip_stories(session, only=[far]))
    await session.commit()
    meta = (
        await session.execute(text("SELECT gen_meta FROM stories WHERE id = :i"), {"i": far})
    ).scalar_one()
    assert q.QUARANTINE_KEY in meta
    assert q.QUARANTINE_KEY not in (meta.get("enrichment") or {})


async def test_it_is_idempotent(pair, session):
    """The write removes the row from `SERVABLE_STATUSES` and `chip_offences` only
    reads servable stories, so a second run has nothing to select."""
    far, _ = pair
    first = await q.quarantine(session, await q.far_chip_stories(session, only=[far]))
    await session.commit()
    assert len(first) == 1
    second = await q.quarantine(session, await q.far_chip_stories(session, only=[far]))
    await session.commit()
    assert second == []
