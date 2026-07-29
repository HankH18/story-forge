"""Phase 3: moderation layering, segment splitting, warm-pool integrity.

The live generation path is proved by `make eval-smoke` and `make warm` — these
cover the pure logic and the DB invariants around them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge.llm.moderate import blocklist_check
from storyforge.warm import _split_segments


# --- moderation layer 1: deterministic, free, cannot be argued with ----------
def test_blocklist_catches_violence_without_an_api_call():
    hits = blocklist_check("The man had a gun. He shot it and the dragon was dead.")
    assert set(hits) >= {"gun", "shot", "dead"}


def test_blocklist_is_case_and_punctuation_insensitive():
    """Tokenised like the validator, so "Kill." and "kill" are the same word."""
    assert "kill" in blocklist_check("Kill! The man will Kill.")


def test_blocklist_does_not_substring_match():
    """docs/DEVIATIONS.md 2.4 — Scunthorpe. "assassin" must not flag via "ass"."""
    assert blocklist_check("Sam ran to the class and sat on the grass.") == []


def test_clean_story_passes_layer_one_free():
    assert blocklist_check("A dragon sat in a cave. Sam was glad.") == []


# --- segment splitting (§10: max 7 screens) ----------------------------------
def test_segments_split_on_sentences():
    segs = _split_segments("Sam sat. A cat ran. The dog was glad.")
    assert segs == ["Sam sat.", "A cat ran.", "The dog was glad."]


def test_segments_respect_the_seven_screen_cap():
    body = " ".join(f"Sentence {i} here." for i in range(20))
    segs = _split_segments(body)
    assert len(segs) == 7


def test_over_cap_merges_the_tail_rather_than_truncating():
    """Truncating would silently drop story text — the child would hit a
    non-ending. The tail is merged into the last screen instead."""
    body = " ".join(f"S{i}." for i in range(12))
    segs = _split_segments(body)
    assert "S11" in segs[-1]
    assert all(f"S{i}" in " ".join(segs) for i in range(12))


def test_single_sentence_is_one_segment():
    assert _split_segments("Sam sat.") == ["Sam sat."]


# --- warm pool DB invariants -------------------------------------------------
@pytest.fixture
async def session():
    from storyforge.db import SessionLocal

    async with SessionLocal() as s:
        yield s


async def test_pool_has_warm_stories(session):
    n = (
        await session.execute(text("SELECT count(*) FROM stories WHERE status='warm'"))
    ).scalar_one()
    assert n > 0, "run `make warm`"


async def test_every_warm_story_meets_the_gate(session):
    """§7: nothing below the gate may sit in the pool waiting to be served."""
    rows = await session.execute(
        text("SELECT id, decodability FROM stories WHERE status='warm' AND decodability < 0.95")
    )
    assert not rows.all()


async def test_every_warm_story_passed_moderation(session):
    """A story can clear decodability and still be unfit for a child."""
    rows = await session.execute(
        text(
            "SELECT id FROM stories WHERE status='warm' "
            "AND gen_meta->'moderation'->>'approved' <> 'true'"
        )
    )
    assert not rows.all()


async def test_warm_stories_respect_the_story_word_cap(session):
    rows = await session.execute(
        text(
            "SELECT id, story_words FROM stories "
            "WHERE status='warm' AND array_length(story_words, 1) > 3"
        )
    )
    assert not rows.all()


async def test_every_warm_story_has_segments(session):
    """A story with no segments would render as a blank screen in kid mode."""
    rows = await session.execute(
        text(
            "SELECT s.id FROM stories s LEFT JOIN story_segments g ON g.story_id = s.id "
            "WHERE s.status='warm' GROUP BY s.id HAVING count(g.id) = 0"
        )
    )
    assert not rows.all()


async def test_rejected_stories_are_kept_but_never_warm(session):
    """Rejects are eval evidence for why a pair is hard — kept, never served."""
    rows = await session.execute(
        text("SELECT count(*) FROM stories WHERE status='rejected'")
    )
    assert rows.scalar_one() >= 0  # presence is fine; the invariant is below
    bad = await session.execute(
        text("SELECT id FROM stories WHERE status='rejected' AND last_served_at IS NOT NULL")
    )
    assert not bad.all()
