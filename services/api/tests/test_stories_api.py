"""Phase 4: the story request API (§9), served from the warm pool (§7).

Like the Phase 2 tests these run against real Postgres and the real seeded pool.
A story-serving test that passes against an empty `stories` table would be
evidence of nothing, so these require `make seed && make warm` to have run.

The tests mutate `last_served_at` — that column exists to be mutated, and the
rotation tests are written to be order-independent so a second run of the suite
behaves identically to the first.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from storyforge.api.stories import RATE_LIMIT_PER_MINUTE, limiter
from storyforge.db import SessionLocal
from storyforge.main import app

DRAGONS = "dragons"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter keys on client address, which every test shares."""
    limiter.clear()
    yield
    limiter.clear()


async def _pool(session, stage_id: int, theme_slug: str = DRAGONS) -> set[int]:
    rows = await session.execute(
        text(
            "SELECT s.id FROM stories s JOIN themes t ON t.id = s.theme_id "
            "WHERE s.stage_id = :stage AND t.slug = :slug AND s.status = 'warm'"
        ),
        {"stage": stage_id, "slug": theme_slug},
    )
    return {r[0] for r in rows}


async def _request(client, stage_id: int, theme=DRAGONS) -> dict:
    resp = await client.post(
        "/api/stories/request", json={"stage_id": stage_id, "theme_id_or_slug": theme}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- happy path --------------------------------------------------------------
async def test_request_returns_a_full_dto(client, session):
    """§11.2: kid mode renders from this response alone, with no second call."""
    body = await _request(client, 1)

    assert body["id"] in await _pool(session, 1)
    assert body["status"] == "warm"
    assert body["stage_id"] == 1
    assert body["theme_slug"] == DRAGONS
    assert body["title"]
    assert 0.0 <= body["decodability"] <= 1.0
    assert body["fallback"] is None

    # Picture chips (§4) — distinct story words, never more than three (§7 gate).
    assert body["story_words"]
    assert len(body["story_words"]) == len(set(body["story_words"])) <= 3

    assert body["segments"], "a story with no segments is not renderable"
    for seg in body["segments"]:
        assert seg["body"].strip()
        assert set(seg) >= {"id", "idx", "body", "scene_tags", "asset", "choice_label"}


async def test_segments_come_back_in_idx_order(client):
    """The reading view pages one segment per screen (§11.2); order is the story."""
    for stage in (1, 2, 3):
        body = await _request(client, stage)
        idxs = [s["idx"] for s in body["segments"]]
        assert idxs == sorted(idxs) == list(range(len(idxs)))


async def test_dto_degrades_when_questions_and_assets_are_absent(client):
    """The QUESTION_GEN and SCENE_TAGGING nodes are being built in parallel.

    Missing rows must produce an empty list and a null asset — never a 500 and
    never an invented URL (the UI draws a striped placeholder instead).
    """
    body = await _request(client, 2)
    assert isinstance(body["questions"], list)
    for seg in body["segments"]:
        assert seg["asset"] is None or seg["asset"]["url"]


async def test_dto_carries_assets_and_questions_when_present(client, session):
    """The other half of the same contract, exercised with real rows."""
    story_id = min(await _pool(session, 1))
    seg_id = (
        await session.execute(
            text("SELECT id FROM story_segments WHERE story_id = :s ORDER BY idx LIMIT 1"),
            {"s": story_id},
        )
    ).scalar_one()

    asset_id = (
        await session.execute(
            text(
                "INSERT INTO assets (theme_id, kind, tags, url, alt_text) "
                "VALUES (1, 'segment', ARRAY['cave','dragon_friendly'], "
                "'/assets/test-cave.png', 'A friendly dragon in a cave') RETURNING id"
            )
        )
    ).scalar_one()
    question_id = (
        await session.execute(
            text(
                "INSERT INTO questions (story_id, segment_id, idx, qtype, prompt, payload, answer) "
                "VALUES (:st, :sg, 0, 'tap_word', 'Tap the word that says /d/og.', "
                """CAST('{"options": ["dog", "cat"]}' AS jsonb), """
                """CAST('{"correct": "dog"}' AS jsonb)) RETURNING id"""
            ),
            {"st": story_id, "sg": seg_id},
        )
    ).scalar_one()
    await session.execute(
        text("UPDATE story_segments SET asset_id = :a WHERE id = :s"),
        {"a": asset_id, "s": seg_id},
    )
    await session.commit()

    try:
        body = (await client.get(f"/api/stories/{story_id}")).json()

        seg = next(s for s in body["segments"] if s["id"] == seg_id)
        assert seg["asset"] == {
            "id": asset_id,
            "url": "/assets/test-cave.png",
            "alt_text": "A friendly dragon in a cave",
            "tags": ["cave", "dragon_friendly"],
        }

        q = next(q for q in body["questions"] if q["id"] == question_id)
        assert q["qtype"] == "tap_word"
        assert q["options"] == ["dog", "cat"]
        assert q["segment_id"] == seg_id
        # The answer key is graded server-side (§9) and never shipped to a kid.
        assert "answer" not in q and "correct" not in q["payload"]
    finally:
        await session.execute(
            text("UPDATE story_segments SET asset_id = NULL WHERE id = :s"), {"s": seg_id}
        )
        await session.execute(text("DELETE FROM questions WHERE id = :q"), {"q": question_id})
        await session.execute(text("DELETE FROM assets WHERE id = :a"), {"a": asset_id})
        await session.commit()


# --- warm-pool mechanics (§7) ------------------------------------------------
async def test_request_stamps_last_served_at(client, session):
    before = (await session.execute(text("SELECT max(last_served_at) FROM stories"))).scalar_one()

    body = await _request(client, 3)

    served = (
        await session.execute(
            text("SELECT last_served_at FROM stories WHERE id = :i"), {"i": body["id"]}
        )
    ).scalar_one()
    assert served is not None
    assert before is None or served >= before


async def test_least_recently_served_rotation(client, session):
    """§7: the pool rotates, so a kid does not get the same story twice."""
    pool = await _pool(session, 1)
    assert len(pool) >= 2, "needs a warm pool of at least 2 — run `make warm`"

    served = [(await _request(client, 1))["id"] for _ in range(len(pool))]
    assert set(served) == pool, "rotation skipped part of the pool"
    assert len(set(served)) == len(served), "the same story was served twice in one cycle"

    # One more turn of the wheel comes back to the head of the queue.
    assert (await _request(client, 1))["id"] == served[0]


async def test_rejected_stories_are_never_served(client, session):
    """Child-safety invariant: rejected failed the gate or moderation (§7)."""
    rejected = {
        r[0]
        for r in await session.execute(text("SELECT id FROM stories WHERE status = 'rejected'"))
    }
    assert rejected, "no rejected stories in the pool — this test would be vacuous"

    for stage in (1, 2, 3):
        for _ in range(3):
            body = await _request(client, stage)
            assert body["id"] not in rejected
            assert body["status"] != "rejected"
            limiter.clear()

    # Nor by id: a rejected story is an eval artifact, not content.
    assert (await client.get(f"/api/stories/{min(rejected)}")).status_code == 404


# --- fallback ladder ---------------------------------------------------------
async def test_empty_pair_falls_back_to_the_same_stage_and_another_theme(client, session):
    """§7: the kid never sees an error. `space` has no stories at all."""
    body = await _request(client, 3, theme="space")

    assert body["stage_id"] == 3, "stage fidelity outranks theme preference"
    assert body["theme_slug"] == DRAGONS
    assert body["status"] != "rejected"
    assert body["fallback"]["kind"] == "other_theme"
    assert body["fallback"]["requested_theme_slug"] == "space"
    assert body["segments"]


async def test_fallback_never_serves_a_story_above_the_requested_stage(client):
    """A higher-stage story may contain GPCs this reader has never been taught."""
    for stage in (1, 2, 3):
        for theme in ("space", "animals", DRAGONS):
            body = await _request(client, stage, theme=theme)
            assert body["stage_id"] <= stage
            limiter.clear()


async def test_a_stage_with_no_pool_falls_back_to_the_nearest_lower_stage(client, session):
    """Stages 4–12 have never been warmed; stage 3 is the nearest safe rung."""
    highest = (
        await session.execute(
            text("SELECT max(stage_id) FROM stories WHERE status IN ('warm', 'complete')")
        )
    ).scalar_one()

    body = await _request(client, 12, theme="animals")
    assert body["stage_id"] == highest
    assert body["fallback"]["kind"] == "lower_stage_other_theme"
    assert body["fallback"]["requested_stage_id"] == 12

    body = await _request(client, 12)
    assert body["stage_id"] == highest
    assert body["fallback"]["kind"] == "lower_stage"


async def test_theme_may_be_given_as_an_id_or_a_slug(client):
    by_slug = await _request(client, 1, theme=DRAGONS)
    by_id = await _request(client, 1, theme=1)
    assert by_slug["theme_id"] == by_id["theme_id"] == 1


# --- unknown stage / theme ---------------------------------------------------
async def test_unknown_theme_is_a_404(client):
    """Pool exhaustion is not an error; naming a theme that does not exist is."""
    resp = await client.post(
        "/api/stories/request", json={"stage_id": 1, "theme_id_or_slug": "unicorns"}
    )
    assert resp.status_code == 404
    assert "unicorns" in resp.json()["detail"]

    resp = await client.post("/api/stories/request", json={"stage_id": 1, "theme_id_or_slug": 999})
    assert resp.status_code == 404


async def test_stage_outside_the_scope_and_sequence_is_a_422(client):
    for stage in (0, 13, 99):
        resp = await client.post(
            "/api/stories/request", json={"stage_id": stage, "theme_id_or_slug": DRAGONS}
        )
        assert resp.status_code == 422, stage


# --- GET /api/stories/{id} ---------------------------------------------------
async def test_get_story_returns_the_same_dto(client):
    served = await _request(client, 2)
    resp = await client.get(f"/api/stories/{served['id']}")
    assert resp.status_code == 200

    fetched = resp.json()
    # `fallback` describes one request, not the story, so it is the one field
    # that legitimately differs.
    assert fetched | {"fallback": served["fallback"]} == served


async def test_get_unknown_story_is_a_404(client):
    assert (await client.get("/api/stories/99999999")).status_code == 404


# --- rate limit (§9) ---------------------------------------------------------
async def test_rate_limit_triggers_at_ten_per_minute(client):
    for i in range(RATE_LIMIT_PER_MINUTE):
        resp = await client.post(
            "/api/stories/request", json={"stage_id": 1, "theme_id_or_slug": DRAGONS}
        )
        assert resp.status_code == 200, f"request {i + 1} should be allowed"

    resp = await client.post(
        "/api/stories/request", json={"stage_id": 1, "theme_id_or_slug": DRAGONS}
    )
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1

    # The limiter is scoped to this endpoint, not the whole app.
    assert (await client.get("/api/stories/2")).status_code in (200, 404)
