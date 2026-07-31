"""§12's illustration, per STORY — the eighth gate (DEVIATIONS 26.1-26.6).

The owner's first complaint about this app was "there are no images in the
stories". The art is real: 33 asset rows, 33 files on disk, HTTP 200 in
production. The defect was in SELECTION, and it had two halves.

1. §12's tiebreak. "Most tag overlap, deterministic tiebreak by asset id" —
   and lowest-id-always-wins is not a tiebreak between two pictures, it is a
   standing preference for one of them, re-applied on every screen. The animals
   pool holds exactly two rabbit pictures, so every screen tagged `rabbit` and
   nothing else that matched scored 1 on both and took the lower id. Measured on
   the live pool: **4 servable stories rendered ONE picture on all seven pages,
   40 more rendered two**, and asset 24 alone was on 160 of 763 segments.

2. Nothing could see it. Every count in the pipeline is per-SEGMENT, and a
   story whose seven pages all carry asset 24 records `segments_with_art: 7` —
   full marks. **The repeat does not exist at the segment.** That is why the
   unit here is the story, and it is the general lesson: a metric denominated in
   the wrong unit is not a weak signal, it is a green light.

The gate's floor is `MIN_DISTINCT_PICTURES`, and it is deliberately 2 rather
than "one per page": 10 segment illustrations per theme against 7-page stories
and a 42-tag vocabulary is a LIBRARY that is too small (DEVIATIONS 26.5), and a
gate that demands what the library cannot supply is a gate nobody can act on.

Nothing here calls Anthropic.
"""

from __future__ import annotations

import logging

import pytest

from storyforge import warm
from storyforge.db import SessionLocal
from storyforge.llm.scene_tags import SceneTagResult
from storyforge.models import Story, StorySegment


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


#: The live rows this whole finding is about. `forest-02` (24) and `burrow-01`
#: (25) are the only two rabbit pictures in the animals theme.
RABBIT_SCREENS = [
    ["rabbit", "forest"],
    ["rabbit"],
    ["rabbit", "friendly_creature"],
]


@pytest.fixture
async def repeated_picture_story(session):
    """An animals story stored exactly as the live pool stored story 7291:
    every screen tagged `rabbit`, every screen carrying asset 24."""
    theme_id = (
        await session.execute(warm.text("SELECT id FROM themes WHERE slug = 'animals'"))
    ).scalar_one()
    asset_id = (
        await session.execute(
            warm.text(
                "SELECT id FROM assets WHERE theme_id = :th AND kind = 'segment' "
                "AND 'rabbit' = ANY(tags) ORDER BY id LIMIT 1"
            ),
            {"th": theme_id},
        )
    ).scalar_one()
    story_id = (
        await session.execute(
            warm.text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (1, :th, 'warm', 'Art Fixture', 1.0, "
                "ARRAY[]::text[], '{}'::jsonb) RETURNING id"
            ),
            {"th": theme_id},
        )
    ).scalar_one()
    for idx, tags in enumerate(RABBIT_SCREENS):
        await session.execute(
            warm.text(
                "INSERT INTO story_segments (story_id, idx, body, scene_tags, asset_id) "
                "VALUES (:s, :i, :b, CAST(:t AS text[]), :a)"
            ),
            {"s": story_id, "i": idx, "b": f"The rabbit ran. Screen {idx}.", "t": tags,
             "a": asset_id},
        )
    await session.execute(
        warm.text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:s, 0, 'tap_word', 'Tap: ran.', CAST(:p AS jsonb), CAST(:a AS jsonb))"
        ),
        {
            "s": story_id,
            "p": '{"options": ["ran", "did", "nap"], "cue_word": "run"}',
            "a": '{"correct": "ran", "index": 0, "target_grapheme": "r"}',
        },
    )
    await session.commit()
    try:
        yield story_id, asset_id
    finally:
        await session.rollback()
        await session.execute(warm.text("DELETE FROM stories WHERE id = :i"), {"i": story_id})
        await session.commit()


def _one(census, story_id):
    return next(a for a in census if a.story_id == story_id)


# --- the census: the unit is the STORY ----------------------------------------
async def test_the_census_sees_the_repeat_that_every_segment_count_calls_full(
    repeated_picture_story, session
):
    story_id, asset_id = repeated_picture_story
    art = _one(await warm.art_census(session), story_id)

    # What the old per-segment metric saw: every screen has a picture.
    assert all(stored is not None for _, _, stored, _ in art.screens)
    assert art.tagged_screens == len(RABBIT_SCREENS)
    # What the story-level unit sees: it is the SAME picture every time.
    assert art.stored_distinct == 1
    assert {stored for _, _, stored, _ in art.screens} == {asset_id}
    # And what §12 picks today.
    assert art.chosen_distinct > 1


async def test_the_census_separates_no_tags_from_no_asset(session):
    """Two causes of §06's placeholder, two different remedies. An untagged
    screen is SCENE_TAGGING's sanctioned answer; an unmatched one is a hole in
    the library, and its tags are the shopping list."""
    shortfall = warm.art_shortfall(
        [["dragon"], [], ["celebrating"]], [object(), None, None]
    )
    assert shortfall["untagged"] == [1]
    assert shortfall["unmatched"] == [{"idx": 2, "tags": ["celebrating"]}]


# --- the gate ------------------------------------------------------------------
async def test_the_gate_refuses_a_story_that_shows_one_picture(
    monkeypatch, repeated_picture_story
):
    story_id, _ = repeated_picture_story

    async def census(_session):
        return [a for a in await _real(_session) if a.story_id == story_id]

    _real = warm.art_census
    monkeypatch.setattr(warm, "art_census", census)
    with pytest.raises(SystemExit) as exc:
        await warm.run_art_check()
    assert "fewer than" in str(exc.value)
    assert "--reselect-art" in str(exc.value)


async def test_the_gate_passes_once_the_story_shows_more_than_one(
    monkeypatch, repeated_picture_story, session
):
    story_id, _ = repeated_picture_story
    await warm.reselect_art(session, only=[story_id])
    await session.commit()

    _real = warm.art_census

    async def census(_session):
        return [a for a in await _real(_session) if a.story_id == story_id]

    monkeypatch.setattr(warm, "art_census", census)
    await warm.run_art_check()  # must not raise


async def test_the_gate_refuses_to_call_an_empty_pool_clean(monkeypatch):
    """8.44's vacuity rule. A fresh deploy is exactly when a clean art report
    would be believed, and it is exactly when it means nothing."""

    async def empty(_session):
        return []

    monkeypatch.setattr(warm, "art_census", empty)
    with pytest.raises(SystemExit) as exc:
        await warm.run_art_check()
    assert "EMPTY pool" in str(exc.value)


async def test_a_single_screen_story_is_not_an_offence(monkeypatch, repeated_picture_story):
    """A story with one tagged screen cannot show two pictures and is not what
    the owner saw. The gate must not manufacture an offence out of arithmetic."""
    story_id, asset_id = repeated_picture_story
    one_screen = warm.StoryArt(
        story_id=story_id,
        stage_id=1,
        theme_slug="animals",
        screens=((0, ("rabbit",), asset_id, asset_id),),
    )

    async def census(_session):
        return [one_screen]

    monkeypatch.setattr(warm, "art_census", census)
    await warm.run_art_check()  # must not raise


# --- the free repair -----------------------------------------------------------
async def test_the_repair_gives_the_story_back_its_pictures(
    repeated_picture_story, session
):
    story_id, asset_id = repeated_picture_story
    changed = await warm.reselect_art(session, only=[story_id])
    await session.commit()

    assert [a.story_id for a in changed] == [story_id]
    rows = (
        await session.execute(
            warm.text(
                "SELECT idx, asset_id, scene_tags, body FROM story_segments "
                "WHERE story_id = :i ORDER BY idx"
            ),
            {"i": story_id},
        )
    ).all()
    assert len({r.asset_id for r in rows}) > 1, "still one picture on every page"
    assert rows[0].asset_id == asset_id, "page 1 keeps the best match"
    # The tags are the MODEL's answer and re-deriving them costs a Haiku call.
    # This repair is free precisely because it does not touch them.
    assert [list(r.scene_tags) for r in rows] == RABBIT_SCREENS
    assert all(r.body.startswith("The rabbit ran.") for r in rows)
    # And no §8 evidence can be cascaded away, because no question is read.
    assert (
        await session.execute(
            warm.text("SELECT count(*) FROM questions WHERE story_id = :i"), {"i": story_id}
        )
    ).scalar_one() == 1


async def test_the_repair_is_idempotent(repeated_picture_story, session):
    """It writes the output of a pure function of rows it does not change."""
    story_id, _ = repeated_picture_story
    first = await warm.reselect_art(session, only=[story_id])
    await session.commit()
    second = await warm.reselect_art(session, only=[story_id])
    await session.commit()
    assert len(first) == 1 and second == []


async def test_the_repair_leaves_every_other_story_alone(repeated_picture_story, session):
    """`--only` is a restriction, not a hint — the live pool is 110 stories and
    an operator repairing one must not rewrite the other 109."""
    story_id, _ = repeated_picture_story
    before = {
        (r.story_id, r.idx): r.asset_id
        for r in (
            await session.execute(
                warm.text("SELECT story_id, idx, asset_id FROM story_segments")
            )
        ).all()
    }
    await warm.reselect_art(session, only=[story_id])
    await session.commit()
    after = {
        (r.story_id, r.idx): r.asset_id
        for r in (
            await session.execute(
                warm.text("SELECT story_id, idx, asset_id FROM story_segments")
            )
        ).all()
    }
    moved = {k for k in before if before[k] != after.get(k)}
    assert {k[0] for k in moved} == {story_id}


# --- the silence: an unillustrated screen now leaves a record ------------------
async def test_apply_tags_records_and_logs_the_screens_that_lost_their_picture(
    repeated_picture_story, session, caplog
):
    """A segment whose tags come back empty was demoted to the placeholder with
    no record of WHICH screen or WHY. `segments_with_art: 5` of 6 said how many
    and never said which, and never distinguished "nothing to draw" from "we do
    not own this picture"."""
    story_id, _ = repeated_picture_story
    story = (
        await session.execute(warm.select(Story).where(Story.id == story_id))
    ).scalar_one()
    segments = list(
        (
            await session.execute(
                warm.select(StorySegment)
                .where(StorySegment.story_id == story_id)
                .order_by(StorySegment.idx)
            )
        ).scalars()
    )
    # A tag the ANIMALS theme owns no picture for — ASSERTED, not assumed. This
    # was `celebrating`, and `animals/celebrate-01` (DEVIATIONS 28.3) gave the
    # theme a celebrating picture, which turned the fixture's premise into a
    # silent lie: the screen matched, so the test was no longer exercising the
    # unmatched branch at all. A test whose subject is a hole in the library has
    # to check the hole is still there.
    unowned = "castle"
    assert not (
        await session.execute(
            warm.text(
                "SELECT 1 FROM assets a JOIN themes t ON t.id = a.theme_id "
                "WHERE t.slug = 'animals' AND a.kind = 'segment' "
                "AND :tag = ANY(a.tags)"
            ),
            {"tag": unowned},
        )
    ).all(), f"animals now owns a {unowned!r} picture — pick another library hole"

    meta: dict = {}
    tagging = SceneTagResult(tags=[["rabbit", "forest"], [], [unowned]])
    with caplog.at_level(logging.WARNING, logger="storyforge.warm"):
        await warm._apply_tags(session, story, segments, tagging, meta)
    await session.commit()

    assert meta["art_untagged_idx"] == [1]
    assert meta["art_unmatched"] == [{"idx": 2, "tags": [unowned]}]
    assert meta["art_distinct"] == 1
    logged = [r.getMessage() for r in caplog.records]
    assert any("placeholder" in m and "untagged idx [1]" in m for m in logged), logged


async def test_apply_tags_no_longer_repeats_one_picture(repeated_picture_story, session):
    """The fix at its source: a freshly enriched story does not need the repair."""
    story_id, _ = repeated_picture_story
    story = (
        await session.execute(warm.select(Story).where(Story.id == story_id))
    ).scalar_one()
    segments = list(
        (
            await session.execute(
                warm.select(StorySegment)
                .where(StorySegment.story_id == story_id)
                .order_by(StorySegment.idx)
            )
        ).scalars()
    )
    meta: dict = {}
    await warm._apply_tags(
        session, story, segments, SceneTagResult(tags=RABBIT_SCREENS), meta
    )
    await session.commit()
    assert meta["art_distinct"] > 1
    assert len({s.asset_id for s in segments}) > 1
