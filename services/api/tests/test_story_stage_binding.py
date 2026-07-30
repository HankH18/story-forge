"""The stage a story is served at comes from the CHILD (§9, DEVIATIONS 7.21–7.23).

This file is the regression suite for the Phase 6 audit's C1 and C2, which were
one defect wearing two faces: *nothing in the serving path ever asked the
decoder about the right child*.

- C1: kid mode computed the stage client-side and fell back to a constant
  (`DEFAULT_STAGE = 4`). After the Phase 6 migration orphaned every kid row that
  fallback was the state of every account in the database, so a stage-1 reader
  was served text measuring **17.6% decodable at their own stage**. §1 requires
  ≥95%.
- C2: the server took `stage_id` from the request body and used it as the
  ceiling. `{"stage_id": 12}` returned a stage-5 story measuring **13.0%
  decodable** for that same reader, reachable from the address bar with no
  tooling, and `POST /api/readings` then filed it against the child with a 201.

So these run with the **bypass OFF**, against real Postgres, with real RS256
tokens from `conftest.py`'s local JWKS server. That is not incidental: the
audit's own finding 0 was that a whole phase of verification had been done
against an API whose auth was disabled, and every assertion below is about who
the caller is and which child they own. Proving it against a fixed demo
principal would prove nothing.

The suite that runs under the bypass (`test_stories_api.py`) covers the §7
ladder and is the reason `stage_id` still exists at all: it is an admin preview
knob (DEVIATIONS 7.21), and `test_a_parent_may_not_choose_the_stage` below is
the line between the two.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge.db import SessionLocal
from tests.owned_pool import hide_servable_pool, owned_transaction

DRAGONS = "dragons"

#: The stage the serve-gate fixture plants its poison row at. A CONSTANT with one
#: requirement — a stage below it inside §9's 1..12 — because the gate test needs a
#: readable story one rung down for §7's ladder to descend to once the poison is
#: refused. Any interior stage is as good as any other.
#:
#: This replaces `EMPTY_STAGE_BAND = 12`, whose comment read *"Nothing in the seeded
#: pool lives above stage 5, so a story placed here is the unique winner of
#: `_SERVE_SQL`'s `ORDER BY stage_id DESC` … That is what makes the serve-gate test
#: deterministic rather than dependent on whatever `make warm` last produced."*
#: Stage 12 holds **nine** servable, answerable stories today and the conclusion was
#: inverted: the test had become entirely dependent on what `make warm` last
#: produced. It survived on `last_served_at ASC NULLS FIRST` alone — all nine
#: stage-12 rows happen to be stamped, so the fixture's NULL won; unstamped, which
#: is the state immediately after `make warm`, they tie and the fixture loses the
#: `id ASC` tiebreak because it holds the highest id. PROVED in both directions with
#: the serve-time gate deleted: 1 failed as the database sits, **1 passed** after
#: `UPDATE stories SET last_served_at = NULL WHERE status IN ('warm','complete')`
#: (AUDIT-PHASE7 F3, DEVIATIONS 9.65). The pool is no longer consulted at all — the
#: fixture below owns it, the way DEVIATIONS 8.90-8.94 rewrote `Pool`.
POISON_STAGE = 6

#: Real English, entirely undecodable at any stage of §4's cumulative inventory:
#: every word needs GPCs no stage teaches, and none of them are sight words.
#: Measured below rather than asserted from here.
UNDECODABLE_BODY = (
    "The knight thought the enormous creature might be dangerous, "
    "although its enthusiasm suggested otherwise entirely."
)

#: Stage-1 decodable text, so a story carrying it clears §1's gate at every stage
#: (§4 cumulative inventory, §5.7c monotonicity) with no story words. This is the
#: rung the ladder must descend to when the poison row above it is refused, and the
#: reason the gate test can still assert what it always asserted — that what WAS
#: served is genuinely readable by this child — instead of settling for a 503.
#: Measured below rather than asserted from here.
DECODABLE_BODY = "Sam sat on the mat. The cat did nap. Sam can pat the cat."


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
async def sandbox(session):
    """Track and remove everything this file creates.

    `users` cascades to `kids` → `readings` → `answers`, and `stories` cascades
    to `story_segments`, so the two lists below are the whole cleanup. Nothing
    pre-existing is deleted: the audit lost a row carrying mastery evidence that
    way, and a test file is not the place to find out.
    """
    created_stories: list[int] = []
    yield created_stories
    await session.execute(text("DELETE FROM users WHERE clerk_id LIKE 'user_local%'"))
    if created_stories:
        await session.execute(
            text("DELETE FROM stories WHERE id = ANY(CAST(:ids AS int[]))"),
            {"ids": created_stories},
        )
    await session.commit()


@pytest.fixture
async def owned_pool():
    """A session the API shares, on a transaction that owns the visible pool.

    DEVIATIONS 8.91's mechanism, shared from `tests/owned_pool.py` (9.62), for the
    one test in this file whose subject is which story `_SERVE_SQL` picks FIRST. The
    other tests here assert about the stage boundary and are indifferent to pool
    shape, so they keep `session` + `sandbox` and their real rows.

    Nothing is committed, so this needs no `sandbox`: no story, no theme, no kid and
    no `last_served_at` stamp survives the test.
    """
    async with owned_transaction() as session:
        await hide_servable_pool(session)
        yield session


@pytest.fixture
def parent(local_clerk):
    return local_clerk.bearer(
        local_clerk.mint(sub="user_local_parent", role="parent", email="p@example.com")
    )


@pytest.fixture
def other_parent(local_clerk):
    return local_clerk.bearer(
        local_clerk.mint(sub="user_local_other", role="parent", email="o@example.com")
    )


@pytest.fixture
def admin(local_clerk):
    return local_clerk.bearer(
        local_clerk.mint(sub="user_local_admin", role="admin", email="a@example.com")
    )


async def _kid(client, headers, stage_id: int, name: str = "Ana") -> dict:
    resp = await client.post(
        "/api/kids", json={"display_name": name, "stage_id": stage_id}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _insert_story(session, sandbox, *, stage_id: int, body: str, theme=DRAGONS) -> int:
    """A servable story with `last_served_at IS NULL`, so the ladder prefers it.

    `sandbox=None` means the row needs no cleanup because it is being written inside
    `owned_pool`'s rolled-back transaction and will never be committed
    (DEVIATIONS 9.62, 9.65).

    **It carries a question, and that is load-bearing rather than tidy**
    (DEVIATIONS 8.50). A story with no `questions` row is no longer servable at
    all, so a fixture without one is excluded by `_SERVE_SQL` before any of the
    gates these tests are about ever runs — which would have made
    `test_an_undecodable_story_in_the_pool_is_never_served` pass VACUOUSLY: green,
    still carrying its sabotage note, and proving nothing. That is the exact shape
    of the defect its own docstring is about. Verified rather than assumed: with
    this row present, deleting the `_is_decodable_for` branch in `api/stories.py`
    turns that test red again; without it, the test stays green.

    The question is deliberately minimal — these tests are about the stage and
    decodability boundaries, not §11.3 — but it is a real `tap_word` row shaped
    the way the node writes one.
    """
    theme_id = (
        await session.execute(text("SELECT id FROM themes WHERE slug = :s"), {"s": theme})
    ).scalar_one()
    story_id = (
        await session.execute(
            text(
                "INSERT INTO stories (stage_id, theme_id, status, title, decodability, "
                "story_words, gen_meta) VALUES (:stage, :theme, 'warm', :title, 1.0, "
                "CAST('{}' AS text[]), CAST('{}' AS jsonb)) RETURNING id"
            ),
            {"stage": stage_id, "theme": theme_id, "title": "Fixture"},
        )
    ).scalar_one()
    if sandbox is not None:
        sandbox.append(story_id)
    await session.execute(
        text(
            "INSERT INTO story_segments (story_id, idx, body, scene_tags) "
            "VALUES (:id, 0, :body, CAST('{}' AS text[]))"
        ),
        {"id": story_id, "body": body},
    )
    await session.execute(
        text(
            "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
            "VALUES (:id, 0, 'tap_word', 'Tap: sit.', CAST(:p AS jsonb), CAST(:a AS jsonb))"
        ),
        {
            "id": story_id,
            "p": '{"options": ["sat", "did", "nap"], "cue_word": "sit"}',
            "a": '{"correct": "sat", "index": 0, "target_grapheme": "s"}',
        },
    )
    await session.commit()
    return story_id


# --- the anchor is the child -------------------------------------------------
async def test_the_served_stage_is_the_kids_stage_not_the_callers_wish(
    client, local_clerk, parent, sandbox
):
    """C1, inverted. Five readers, five different stages, five correct answers.

    This is the assertion the product is: a child is served material written for
    where that child actually is. Before the fix every one of these returned a
    stage-4 story.
    """
    # §13 puts a first-name CHECK on the column, so these are names, not `Kid1`.
    for stage, name in ((1, "Ana"), (2, "Bo"), (3, "Cara"), (4, "Dev"), (5, "Eve")):
        kid = await _kid(client, parent, stage, name=name)
        resp = await client.post(
            "/api/stories/request",
            json={"kid_id": kid["id"], "theme_id_or_slug": DRAGONS},
            headers=parent,
        )
        assert resp.status_code == 200, resp.text
        served = resp.json()
        # Never ABOVE — STATE invariant 6. A lower stage is safe (§4 cumulative
        # inventory, §5.7c monotonicity); a higher one can contain GPCs this
        # child has never been taught, which is the whole defect.
        assert served["stage_id"] <= stage, (
            f"stage-{stage} reader was served a stage-{served['stage_id']} story"
        )


async def test_a_parent_may_not_choose_the_stage(client, local_clerk, parent, sandbox):
    """C2. The client is untrusted exactly as §0 says the LLM is."""
    # Any stage inside §9's 1..12. The value is irrelevant to what this asserts:
    # `_resolve_stage` raises the 422 before the pool, the stage table or the theme
    # is read at all, which is why this test never needed the pool to have a shape.
    resp = await client.post(
        "/api/stories/request",
        json={"stage_id": 12, "theme_id_or_slug": DRAGONS},
        headers=parent,
    )
    assert resp.status_code == 422, resp.text
    assert "kid_id" in resp.json()["detail"]


async def test_kid_id_is_required(client, local_clerk, parent, sandbox):
    """A request that names no reader is not a reading request."""
    resp = await client.post(
        "/api/stories/request", json={"theme_id_or_slug": DRAGONS}, headers=parent
    )
    assert resp.status_code == 422, resp.text


async def test_a_kid_and_a_stage_together_are_refused(client, local_clerk, admin, sandbox):
    """Not "the kid wins" — refused.

    Silently ignoring one of two contradictory inputs is how `DEFAULT_STAGE`
    survived: the substitution was invisible. A caller that sends both is
    confused about which one decides, and the answer they need is the error.
    """
    kid = await _kid(client, admin, 1)
    resp = await client.post(
        "/api/stories/request",
        json={"kid_id": kid["id"], "stage_id": 5, "theme_id_or_slug": DRAGONS},
        headers=admin,
    )
    assert resp.status_code == 422, resp.text


async def test_another_parents_kid_is_a_404_here_too(
    client, local_clerk, parent, other_parent, sandbox
):
    """The new anchor must not become a new enumeration oracle.

    `kid_id` in this body reaches `auth.owned_kid`, so "not yours" and "not a
    thing" are answered identically — the same posture `POST /api/readings` has
    (DEVIATIONS 6.9), now on the route that decides what gets read.
    """
    kid = await _kid(client, parent, 1)
    mine = await client.post(
        "/api/stories/request",
        json={"kid_id": kid["id"], "theme_id_or_slug": DRAGONS},
        headers=parent,
    )
    assert mine.status_code == 200, mine.text

    theirs = await client.post(
        "/api/stories/request",
        json={"kid_id": kid["id"], "theme_id_or_slug": DRAGONS},
        headers=other_parent,
    )
    nobodys = await client.post(
        "/api/stories/request",
        json={"kid_id": 99_999_999, "theme_id_or_slug": DRAGONS},
        headers=other_parent,
    )
    assert theirs.status_code == 404
    assert nobodys.status_code == 404
    # Byte-identical but for the id, so the pair carries no information.
    assert theirs.json()["detail"] == f"kid {kid['id']} not found"
    assert nobodys.json()["detail"] == "kid 99999999 not found"


async def test_an_admin_keeps_the_stage_preview_knob(client, local_clerk, admin, sandbox):
    """DEVIATIONS 7.21, stated as a test so the exception cannot spread quietly.

    §11's admin portal and the eval path both ask for a stage with no child in
    the room. It grants nothing new — `GET /api/stories/{id}` already serves any
    servable story to any authenticated parent — and it is the reason the
    bypass-run suite in `test_stories_api.py` still speaks the old dialect.
    """
    resp = await client.post(
        "/api/stories/request", json={"stage_id": 5, "theme_id_or_slug": DRAGONS}, headers=admin
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage_id"] <= 5


# --- the second gate, on the write path --------------------------------------
async def test_a_reading_cannot_pair_a_story_above_the_readers_stage(
    client, local_clerk, parent, sandbox, session
):
    """C2's other half (DEVIATIONS 7.22).

    `api/stories.py` argued ownership binds here. It binds the *kid*; it never
    compared the two rows, so a stage-1 kid and a stage-5 story filed a 201 —
    and §8 counted that row as a story "completed at current stage", because
    `readings.stage_id` is stamped from the kid.
    """
    kid = await _kid(client, parent, 1)
    high = await _insert_story(session, sandbox, stage_id=5, body="The dragon came home.")

    resp = await client.post(
        "/api/readings", json={"kid_id": kid["id"], "story_id": high}, headers=parent
    )
    assert resp.status_code == 422, resp.text
    assert "stage 5" in resp.json()["detail"] and "stage 1" in resp.json()["detail"]

    # The legitimate pairing is untouched: §7's ladder serves at or below, and
    # a gate that also refused those would break every fallback serve.
    low = await _insert_story(session, sandbox, stage_id=1, body="Sam sat.")
    ok = await client.post(
        "/api/readings", json={"kid_id": kid["id"], "story_id": low}, headers=parent
    )
    assert ok.status_code == 201, ok.text


# --- defence in depth: the gate re-runs at serve time ------------------------
async def test_an_undecodable_story_in_the_pool_is_never_served(
    client, local_clerk, parent, owned_pool
):
    """DEVIATIONS 7.23 — the property C2 lacked.

    A story that is 13% decodable *for the child receiving it* should be
    inexpressible, not merely unlikely. The row below claims `decodability 1.0`
    in its own column, exactly as a mis-persisted story would (the grammar
    backfill found four stories whose stored verdict was a fiction), and sits
    alone at the top of the ladder where `_SERVE_SQL` must pick it first.

    justify-test-edit — VERDICT: TEST IS WRONG, and it is the FIXTURE PRECONDITION
    that is wrong. Every assertion below is byte-for-byte what it was. What was
    wrong is `EMPTY_STAGE_BAND = 12` and its claim that "nothing in the seeded pool
    lives above stage 5" — stage 12 holds nine servable, answerable stories, so
    "sits alone at the top of the ladder" was false and the test's own stated goal
    ("deterministic rather than dependent on whatever `make warm` last produced")
    was inverted. ORIGIN: commit `559f102`, DEVIATIONS 7.23. REVERT CHECK: nothing
    to revert — this file is unchanged since `538a08b` and the defect is caused by
    `make warm` filling stage 12, which is what `make warm-check` demands. EVIDENCE
    the code is right: the auditor swept all 119 servable stories through
    `_load_story` + `_is_decodable_for` at every reader stage `_SERVE_SQL` can hand
    them to — 0 failures, minimum 95.12%. The gate works; the test could not see it.
    BLAST RADIUS: `EMPTY_STAGE_BAND`'s only other reader asserts a 422 raised before
    the pool is read (see `test_a_parent_may_not_choose_the_stage`).

    PROVED both ways, with `if decodability < DECODABILITY_GATE:` replaced by
    `if False:` in a throwaway copy: **1 failed** as the database sits, **1 passed**
    — green with the gate DELETED — after `UPDATE stories SET last_served_at = NULL
    WHERE status IN ('warm','complete')`, which is the state every `make warm`
    leaves behind. The nine stage-12 rows are all stamped today, so the fixture's
    NULL won `last_served_at ASC NULLS FIRST` by luck; unstamped they tie and the
    fixture loses `id ASC` (AUDIT-PHASE7 F3, DEVIATIONS 9.65).

    The pool is now MANUFACTURED rather than hoped for (DEVIATIONS 8.90-8.94's
    pattern): `owned_pool` hides every real servable story inside a rolled-back
    REPEATABLE READ transaction, so the poison row is the row `_SERVE_SQL` picks
    first whatever the database contains — full or empty — and the readable story
    one rung below it is what §7's ladder must descend to. Sabotage-checked in both
    states: deleting the `_is_decodable_for` branch in `api/stories.py` turns this
    red, because the endpoint returns the poison id.
    """
    from storyforge.decoder import mastered_ids, validate_text

    session = owned_pool

    # The premise, measured rather than assumed — both halves of it.
    sight = frozenset(
        r[0]
        for r in await session.execute(
            text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": POISON_STAGE}
        )
    )
    measured = validate_text(
        UNDECODABLE_BODY, mastered_ids(POISON_STAGE), sight, frozenset()
    ).decodability
    assert measured < 0.95, f"the fixture text is decodable ({measured:.3f}); it proves nothing"
    readable = validate_text(
        DECODABLE_BODY, mastered_ids(POISON_STAGE - 1), sight, frozenset()
    ).decodability
    assert readable >= 0.95, (
        f"the rung below measures {readable:.3f}; the gate would refuse it too and "
        "this test would pass on a 503 instead of on the ladder"
    )

    kid = await _kid(client, parent, POISON_STAGE)
    poison = await _insert_story(session, None, stage_id=POISON_STAGE, body=UNDECODABLE_BODY)
    below = await _insert_story(session, None, stage_id=POISON_STAGE - 1, body=DECODABLE_BODY)

    resp = await client.post(
        "/api/stories/request",
        json={"kid_id": kid["id"], "theme_id_or_slug": DRAGONS},
        headers=parent,
    )
    assert resp.status_code == 200, resp.text
    served = resp.json()
    assert served["id"] != poison, (
        "the serve-time gate let through a story measuring "
        f"{measured:.1%} decodable for this reader"
    )
    assert served["id"] == below, (
        "the poison row was refused but the ladder did not descend to the readable "
        "story one stage down (§7, AUDIT-PHASE7 F4)"
    )

    # And what was served is genuinely readable by this child — the claim, not a
    # proxy for it.
    body = " ".join(s["body"] for s in served["segments"])
    actual = validate_text(
        body,
        mastered_ids(kid["stage_id"]),
        sight,
        frozenset(w.lower() for w in served["story_words"]),
    ).decodability
    assert actual >= 0.95, (
        f"served a story measuring {actual:.1%} for a stage-{kid['stage_id']} reader"
    )
