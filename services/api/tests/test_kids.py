"""Phase 6 Definition of Done: two parents cannot see each other's kids.

§16's gate for this phase is one sentence and this file is the whole of it,
proved against real Postgres with real RS256 tokens minted by the local JWKS
server in `conftest.py` — not with dependency overrides, which would test a
fixture instead of the system.

The shape of every test is the same. Parent A does something legitimate and it
works. Parent B — a different verified Clerk user with a valid, unexpired,
correctly signed token — tries the identical request against A's ids and is
refused. Both halves matter: without A's success, B's 404 could be a broken
route rather than an enforced boundary.

**The refusal is 404, never 403.** A 403 says "this exists and is not yours",
which turns `/api/kids/{id}/progress` into an oracle for enumerating other
families' children — and the thing being enumerated is a list of six-year-olds
with a name and a reading level attached (§13). "Not yours" and "not a thing"
are answered identically, down to the response body, and there is a test for
exactly that.

The matrix covers every route that takes a kid or a reading id: list, create,
progress, start a reading, record a choice, answer a question, and complete.
Wherever a route validates something else too — that a segment belongs to the
story, that a question belongs to the story — B's attempt uses ids that are
genuinely valid for A's story, so the only thing left to refuse it is ownership.
Otherwise this would pass while proving nothing.

Three attacks beyond the two-parent case are here as well: an admin does not
inherit a parent's children; a kid with no owner at all is visible to nobody;
and every route rejects an unauthenticated caller before it does anything else.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge.db import SessionLocal

TAP_STAGE = 1


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture(autouse=True)
async def _sandbox(session):
    """Undo the users (kids cascade) and any story a completion flipped."""
    statuses = {
        r.id: r.status for r in await session.execute(text("SELECT id, status FROM stories"))
    }
    yield
    await session.execute(text("DELETE FROM users WHERE clerk_id LIKE 'user_local%'"))
    await session.execute(
        text(
            "UPDATE stories s SET status = v.status "
            "FROM unnest(CAST(:ids AS int[]), CAST(:statuses AS text[])) AS v(id, status) "
            "WHERE s.id = v.id AND s.status <> v.status"
        ),
        {"ids": list(statuses), "statuses": list(statuses.values())},
    )
    await session.commit()


@pytest.fixture
def parent_a(local_clerk):
    return local_clerk.bearer(
        local_clerk.mint(sub="user_local_alice", role="parent", email="alice@example.com")
    )


@pytest.fixture
def parent_b(local_clerk):
    return local_clerk.bearer(
        local_clerk.mint(sub="user_local_bob", role="parent", email="bob@example.com")
    )


@pytest.fixture
def admin(local_clerk):
    return local_clerk.bearer(local_clerk.mint(sub="user_local_root", role="admin"))


@pytest.fixture
async def story(session):
    """A real servable story from the warm pool, with a segment and a question."""
    row = (
        await session.execute(
            text(
                "SELECT st.id, sg.id AS segment_id FROM stories st "
                "JOIN story_segments sg ON sg.story_id = st.id "
                "WHERE st.stage_id = :s AND st.status IN ('warm', 'complete') "
                "ORDER BY st.id, sg.idx LIMIT 1"
            ),
            {"s": TAP_STAGE},
        )
    ).first()
    assert row is not None, "no stage-1 story with segments — run `make seed && make warm`"

    qid = (
        await session.execute(
            text(
                "INSERT INTO questions (story_id, idx, qtype, prompt, payload, answer) "
                "VALUES (:st, 98, 'tap_word', 'Tap: sit.', CAST(:p AS jsonb), CAST(:a AS jsonb)) "
                "RETURNING id"
            ),
            {
                "st": row.id,
                "p": '{"options": ["sit", "map", "bed"], "target_grapheme": "s"}',
                "a": '{"index": 0, "correct": "sit"}',
            },
        )
    ).scalar_one()
    await session.commit()
    try:
        yield {"id": row.id, "segment_id": row.segment_id, "question_id": qid}
    finally:
        await session.execute(text("DELETE FROM questions WHERE id = :q"), {"q": qid})
        await session.commit()


async def _create_kid(client, headers, name="Maya", stage_id=TAP_STAGE) -> int:
    resp = await client.post(
        "/api/kids", json={"display_name": name, "stage_id": stage_id}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _start_reading(client, headers, kid_id: int, story_id: int) -> int:
    resp = await client.post(
        "/api/readings", json={"kid_id": kid_id, "story_id": story_id}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- ownership is established from the token ---------------------------------
async def test_a_new_kid_is_owned_by_the_calling_parent(client, parent_a, session):
    """§9: the owner comes from the verified session, not from the body."""
    kid_id = await _create_kid(client, parent_a)
    owner = (
        await session.execute(
            text(
                "SELECT u.clerk_id FROM kids k JOIN users u ON u.id = k.parent_id "
                "WHERE k.id = :id"
            ),
            {"id": kid_id},
        )
    ).scalar_one()
    assert owner == "user_local_alice"


async def test_a_parent_id_in_the_body_is_ignored(client, parent_a, parent_b, session):
    """The obvious attempt: name someone else as the owner.

    `KidCreate` has no `parent_id` field, so this is silently dropped rather
    than honoured — asserted here because "the model does not have that field"
    is a fact that a later convenience could quietly undo.
    """
    victim = await _create_kid(client, parent_b, name="Bkid")
    victim_parent = (
        await session.execute(text("SELECT parent_id FROM kids WHERE id = :i"), {"i": victim})
    ).scalar_one()

    resp = await client.post(
        "/api/kids",
        json={"display_name": "Sneaky", "stage_id": 1, "parent_id": victim_parent},
        headers=parent_a,
    )
    assert resp.status_code == 201
    owner = (
        await session.execute(
            text(
                "SELECT u.clerk_id FROM kids k JOIN users u ON u.id = k.parent_id "
                "WHERE k.id = :id"
            ),
            {"id": resp.json()["id"]},
        )
    ).scalar_one()
    assert owner == "user_local_alice"


# --- the Definition of Done, route by route ----------------------------------
async def test_list_a_parent_sees_only_their_own_kids(client, parent_a, parent_b):
    a_kid = await _create_kid(client, parent_a, name="Amy")
    b_kid = await _create_kid(client, parent_b, name="Ben")

    a_list = (await client.get("/api/kids", headers=parent_a)).json()
    b_list = (await client.get("/api/kids", headers=parent_b)).json()

    assert [k["id"] for k in a_list] == [a_kid]
    assert [k["id"] for k in b_list] == [b_kid]
    assert "Ben" not in {k["display_name"] for k in a_list}
    assert "Amy" not in {k["display_name"] for k in b_list}


async def test_progress_another_parents_kid_is_404(client, parent_a, parent_b):
    a_kid = await _create_kid(client, parent_a)
    assert (await client.get(f"/api/kids/{a_kid}/progress", headers=parent_a)).status_code == 200
    assert (await client.get(f"/api/kids/{a_kid}/progress", headers=parent_b)).status_code == 404


async def test_start_a_reading_for_another_parents_kid_is_404(
    client, parent_a, parent_b, story, session
):
    a_kid = await _create_kid(client, parent_a)

    resp = await client.post(
        "/api/readings",
        json={"kid_id": a_kid, "story_id": story["id"]},
        headers=parent_b,
    )
    assert resp.status_code == 404

    # And nothing was written. A 404 that still inserted a row would be a leak
    # of a different kind: B's activity landing in A's child's record.
    count = (
        await session.execute(
            text("SELECT count(*) FROM readings WHERE kid_id = :k"), {"k": a_kid}
        )
    ).scalar_one()
    assert count == 0


async def test_record_a_choice_on_another_parents_reading_is_404(
    client, parent_a, parent_b, story
):
    a_kid = await _create_kid(client, parent_a)
    reading = await _start_reading(client, parent_a, a_kid, story["id"])

    # A genuinely valid segment for this reading's story: ownership is the only
    # thing left that can refuse it.
    body = {"segment_id": story["segment_id"], "choice": "left"}
    assert (
        await client.post(f"/api/readings/{reading}/choice", json=body, headers=parent_b)
    ).status_code == 404
    assert (
        await client.post(f"/api/readings/{reading}/choice", json=body, headers=parent_a)
    ).status_code == 200


async def test_answer_a_question_on_another_parents_reading_is_404(
    client, parent_a, parent_b, story, session
):
    """The one that would corrupt §8's mastery signal, not merely read it."""
    a_kid = await _create_kid(client, parent_a)
    reading = await _start_reading(client, parent_a, a_kid, story["id"])
    body = {"question_id": story["question_id"], "response": "map"}  # a wrong answer

    assert (
        await client.post(f"/api/readings/{reading}/answers", json=body, headers=parent_b)
    ).status_code == 404
    recorded = (
        await session.execute(
            text("SELECT count(*) FROM answers WHERE reading_id = :r"), {"r": reading}
        )
    ).scalar_one()
    assert recorded == 0, "B must not be able to push a wrong answer into A's child's record"

    assert (
        await client.post(f"/api/readings/{reading}/answers", json=body, headers=parent_a)
    ).status_code == 200


async def test_complete_another_parents_reading_is_404_and_changes_nothing(
    client, parent_a, parent_b, story, session
):
    """Ownership is checked BEFORE the completion latch, not after.

    Completing writes `gpc_stats`, flips the story's status and can promote the
    child a stage. A check that ran after the latch would return 404 to the
    attacker with all of that already done.

    **The status code alone cannot see that, and for two phases this test could
    not either** (DEVIATIONS 26.3). Remove `owned_reading` from the handler and
    every assertion below still passes: the request runs on to
    `owned_kid(latched.kid_id)` further down, *that* raises 404, and the
    transaction rolls back unread — so the 404 and the untouched row are both
    still there, produced by a different mechanism than the one this test is
    named after. The detail string is what distinguishes them, because
    `owned_reading` names a READING and the downstream `owned_kid` names a KID.
    """
    a_kid = await _create_kid(client, parent_a)
    reading = await _start_reading(client, parent_a, a_kid, story["id"])

    refused = await client.post(f"/api/readings/{reading}/complete", headers=parent_b)
    assert refused.status_code == 404
    assert refused.json()["detail"] == f"reading {reading} not found", (
        "the refusal did not come from `owned_reading` — the request reached "
        "past the ownership check and was stopped by something downstream"
    )
    still_open = (
        await session.execute(
            text("SELECT completed_at FROM readings WHERE id = :r"), {"r": reading}
        )
    ).scalar_one()
    assert still_open is None, "the latch fired for a caller who did not own the reading"

    done = await client.post(f"/api/readings/{reading}/complete", headers=parent_a)
    assert done.status_code == 200
    assert done.json()["newly_completed"] is True


async def test_a_wrong_owner_is_indistinguishable_from_a_nonexistent_id(
    client, parent_a, parent_b, story
):
    """404 bodies must match, or the 404 is a 403 wearing a disguise."""
    a_kid = await _create_kid(client, parent_a)
    reading = await _start_reading(client, parent_a, a_kid, story["id"])

    theirs = await client.get(f"/api/kids/{a_kid}/progress", headers=parent_b)
    nothing = await client.get("/api/kids/2147483000/progress", headers=parent_b)
    assert theirs.status_code == nothing.status_code == 404
    assert theirs.json()["detail"].replace(str(a_kid), "N") == nothing.json()[
        "detail"
    ].replace("2147483000", "N")

    theirs_r = await client.post(f"/api/readings/{reading}/complete", headers=parent_b)
    nothing_r = await client.post("/api/readings/2147483000/complete", headers=parent_b)
    assert theirs_r.status_code == nothing_r.status_code == 404
    assert theirs_r.json()["detail"].replace(str(reading), "N") == nothing_r.json()[
        "detail"
    ].replace("2147483000", "N")


# --- adjacent ownership attacks ----------------------------------------------
async def test_an_admin_does_not_inherit_other_parents_kids(client, parent_a, admin):
    """Roles are a ladder for *capabilities*, not a skeleton key for *rows*.

    §9 gives admins their own routes. On a parent route an admin is a parent,
    and ownership is enforced identically — otherwise the boundary would depend
    on who is asking rather than on what is being asked.
    """
    a_kid = await _create_kid(client, parent_a)

    assert (await client.get("/api/kids", headers=admin)).json() == []
    assert (await client.get(f"/api/kids/{a_kid}/progress", headers=admin)).status_code == 404


async def test_a_kid_with_no_owner_is_visible_to_nobody(client, parent_a, parent_b, session):
    """Pre-auth rows have `parent_id IS NULL` (DEVIATIONS 5.1, migration ab5a866f52fc).

    `NULL = anything` is never true, so the filter excludes them from every
    parent. That is the correct reading of "owned by nobody" — the alternative,
    backfilling them to some parent, would hand one family another's children.
    """
    orphan = (
        await session.execute(
            text(
                "INSERT INTO kids (parent_id, display_name, stage_id) "
                "VALUES (NULL, 'Orphan', 1) RETURNING id"
            )
        )
    ).scalar_one()
    await session.commit()
    try:
        for headers in (parent_a, parent_b):
            listing = (await client.get("/api/kids", headers=headers)).json()
            assert orphan not in [k["id"] for k in listing]
            assert (
                await client.get(f"/api/kids/{orphan}/progress", headers=headers)
            ).status_code == 404
    finally:
        await session.execute(text("DELETE FROM kids WHERE id = :i"), {"i": orphan})
        await session.commit()


async def test_a_kid_id_is_not_a_credential(client, parent_a, parent_b, story):
    """Knowing the id is the whole of what B has, and it buys nothing.

    Kids have no auth identity (§13), so a kid id is an ordinary integer that
    appears in URLs. Every route that takes one must therefore treat it as
    untrusted input rather than as evidence of anything.
    """
    a_kid = await _create_kid(client, parent_a)
    reading = await _start_reading(client, parent_a, a_kid, story["id"])

    attacks = [
        ("get", f"/api/kids/{a_kid}/progress", None),
        ("post", "/api/readings", {"kid_id": a_kid, "story_id": story["id"]}),
        (
            "post",
            f"/api/readings/{reading}/choice",
            {"segment_id": story["segment_id"], "choice": "left"},
        ),
        (
            "post",
            f"/api/readings/{reading}/answers",
            {"question_id": story["question_id"], "response": 0},
        ),
        ("post", f"/api/readings/{reading}/complete", None),
    ]
    for method, url, body in attacks:
        call = getattr(client, method)
        resp = await (
            call(url, json=body, headers=parent_b) if body else call(url, headers=parent_b)
        )
        assert resp.status_code == 404, f"{method.upper()} {url} → {resp.status_code}"


# --- §13: display_name is a first name, at the route AND at the column -------
@pytest.mark.parametrize(
    "name",
    [
        "Bob 555-123-4567 12 Oak St",  # the live 201 that started this
        "kid@example.com",
        "Sam1",
        "12 Oak Street",
        "Zo<script>alert(1)</script>",
        "Bob Smith Jones",
        "Ann  Lee",
        "Maya\nEve",
        "call me at 5551234567",
        "https://x.example/y",
        "Maya 😀",
        "{{name}}",
    ],
)
async def test_a_name_that_is_not_a_first_name_is_refused(client, parent_a, name):
    """§13 is "display-name only", and this string is shown to the child.

    It is the only child-facing text in the system that passes through none of
    §13's three layers — no blocklist, no allowlist-constrained generation, no
    moderation — because an adult types it directly. So the shape of the input
    is the whole defence.
    """
    resp = await client.post(
        "/api/kids", json={"display_name": name, "stage_id": 1}, headers=parent_a
    )
    assert resp.status_code == 422, f"{name!r} was accepted"


@pytest.mark.parametrize("name", ["Maya", "O'Brien", "Ana-Sofia", "Sofía", "Zoë", "Mary Jane"])
async def test_real_first_names_are_still_accepted(client, parent_a, name):
    """The rule has to allow the names it exists to allow.

    Non-ASCII letters included: a validator that rejects `Sofía` is not a child
    safety control, it is a bug with a rationale.
    """
    resp = await client.post(
        "/api/kids", json={"display_name": name, "stage_id": 1}, headers=parent_a
    )
    assert resp.status_code == 201, resp.text


async def test_the_column_refuses_a_bad_name_even_without_the_api(session):
    """The constraint is on the COLUMN, because the API is not the only writer.

    This database already contained `kid@example.com`, and it did not arrive
    through `POST /api/kids` — that endpoint has rejected `@` since Phase 5
    (DEVIATIONS 5.9). A validator only constrains the callers that run it.
    """
    with pytest.raises(Exception) as caught:
        await session.execute(
            text("INSERT INTO kids (display_name, stage_id) VALUES (:n, 1)"),
            {"n": "Bob 555-123-4567 12 Oak St"},
        )
        await session.commit()
    assert "ck_kids_display_name_is_a_first_name" in str(caught.value)
    await session.rollback()


# --- authentication is required at all ---------------------------------------
@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("get", "/api/kids", None),
        ("post", "/api/kids", {"display_name": "Ana", "stage_id": 1}),
        ("get", "/api/kids/1/progress", None),
        ("post", "/api/readings", {"kid_id": 1, "story_id": 1}),
        ("post", "/api/readings/1/choice", {"segment_id": 1, "choice": "left"}),
        ("post", "/api/readings/1/answers", {"question_id": 1, "response": 0}),
        ("post", "/api/readings/1/complete", None),
        ("post", "/api/stories/request", {"stage_id": 1, "theme_id_or_slug": "dragons"}),
        ("get", "/api/stories/1", None),
        ("post", "/api/validate", {"text": "Sam sat.", "stage_id": 1}),
    ],
)
async def test_every_route_requires_a_session(client, local_clerk, method, url, body):
    """§9: "Clerk JWT required unless noted". `/api/health` is the only exception.

    Parametrised over the route table rather than written once per handler. The
    list is hand-maintained and that is exactly its weakness, which is why
    `test_every_route_declares_its_auth_posture` below reads the app's own
    inventory and refuses to pass while any route is missing from these tables.
    """
    call = getattr(client, method)
    resp = await (call(url, json=body) if body else call(url))
    assert resp.status_code == 401, f"{method.upper()} {url} → {resp.status_code}"


# --- the route inventory, read off the app (DEVIATIONS 26.1-26.4) ------------
#
# The version of this that stood here for two phases walked `app.routes` looking
# for `route.dependant`, and **inspected zero routes**: FastAPI wraps every
# `include_router` call in a `_IncludedRouter`, which holds the real `APIRoute`s
# under `.original_router.routes` and carries no `.dependant` of its own, so
# every entry took the `continue`. `open_routes` was `set()` on every run and
# `set() <= anything` is True. The proof it should have been red is in the
# repository: `/api/themes` and `/api/stages` have been unauthenticated since
# DEVIATIONS 8.60 and were not in its allowlist.
#
# So the walk below recurses, and — because a recursion that silently finds
# nothing is the same defect in a new place — `test_the_route_inventory_is_
# readable` is the anti-vacuity control for every test that consumes it.

#: §9's own division: the only routes in the service that take no principal.
#: `api/catalog.py`'s docstring states it — "This and `/api/health` are the only
#: routes in the service that take no principal, which is §9's own division."
#: Spelled out here rather than derived from the routers, because the point is
#: that this is a DECISION list: a public route added to `catalog.py` tomorrow
#: must turn this file red, not update it.
PUBLIC_API_ROUTES = frozenset({"/api/health", "/api/themes", "/api/stages"})

#: FastAPI's own schema and documentation routes. Not `/api/*` and not ours.
FRAMEWORK_ROUTES = frozenset(
    {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
)

#: Routes that resolve a kid or a reading id through `auth.owned_kid` /
#: `auth.owned_reading`. Each is DRIVEN with a second parent below, so adding a
#: route here without the check is a failing test rather than a review comment.
#: The template values are substituted from ids parent A really owns.
OWNER_SCOPED_ROUTES: dict[str, tuple[str, dict | None]] = {
    "/api/kids/{kid_id}/progress": ("get", None),
    "/api/readings": ("post", {"kid_id": "{kid_id}", "story_id": "{story_id}"}),
    "/api/readings/{reading_id}/choice": (
        "post",
        {"segment_id": "{segment_id}", "choice": "left"},
    ),
    "/api/readings/{reading_id}/answers": (
        "post",
        {"question_id": "{question_id}", "response": 0},
    ),
    "/api/readings/{reading_id}/complete": ("post", None),
    "/api/stories/request": ("post", {"kid_id": "{kid_id}", "theme_id_or_slug": "dragons"}),
}

#: §9's `(admin)` block. Covered by `tests/test_admin.py`'s own matrix.
ADMIN_ONLY_ROUTES = frozenset(
    {
        "/api/validate",
        "/api/admin/eval-runs",
        "/api/admin/eval-runs/{run_id}",
        "/api/admin/allowed-words",
        "/api/admin/cold-generate",
        "/api/admin/cold-generate/{job_id}",
    }
)

#: A verified session is required and there is no per-family owner to scope to.
#: `GET /api/kids` is a FILTER on the caller rather than an id lookup; `POST
#: /api/kids` creates under the caller; `GET /api/stories/{id}` serves a warm
#: pool row, which is not a per-family artifact (`api/stories.py` docstring).
SESSION_ONLY_ROUTES = frozenset(
    {"/api/kids", "/api/stories/{story_id}"}
)


def _flatten_dependencies(dependant):
    if dependant is None:
        return
    yield dependant
    for sub in dependant.dependencies:
        yield from _flatten_dependencies(sub)


def _api_routes():
    """Every real route in the app, through the `_IncludedRouter` wrappers."""
    from storyforge.main import app

    found = []

    def walk(routes):
        for route in routes:
            included = getattr(route, "original_router", None)
            if included is not None:
                walk(included.routes)
            elif getattr(route, "methods", None):
                found.append(route)

    walk(app.routes)
    return found


def test_the_route_inventory_is_readable():
    """The anti-vacuity control for every test that walks the app.

    This is the assertion the old guard needed and did not have. It fails when
    FastAPI changes how `include_router` stores routes — which is precisely what
    happened, and which the old guard absorbed as silence instead of as a
    failure. Every path below is one this file or `test_admin.py` drives with a
    real request, so "the walk found them" and "they exist" are the same claim.
    """
    paths = {r.path for r in _api_routes()}
    assert paths >= (
        PUBLIC_API_ROUTES
        | FRAMEWORK_ROUTES
        | frozenset(OWNER_SCOPED_ROUTES)
        | ADMIN_ONLY_ROUTES
        | SESSION_ONLY_ROUTES
    ), f"the route walk lost routes: {sorted(paths)}"


def test_every_route_declares_its_auth_posture():
    """A route this file has never heard of is a failure, not a default.

    §9 gives every route one of four postures. The tables above are the whole
    list; a new router mounted in `main.py` lands in none of them and turns this
    red, which is the property the old `app.routes` walk was written to have.
    """
    declared = (
        PUBLIC_API_ROUTES
        | FRAMEWORK_ROUTES
        | frozenset(OWNER_SCOPED_ROUTES)
        | ADMIN_ONLY_ROUTES
        | SESSION_ONLY_ROUTES
    )
    undeclared = {r.path for r in _api_routes()} - declared
    assert not undeclared, (
        f"route(s) with no declared auth posture: {sorted(undeclared)} — add each "
        "to PUBLIC_API_ROUTES, OWNER_SCOPED_ROUTES, ADMIN_ONLY_ROUTES or "
        "SESSION_ONLY_ROUTES, and to the matrix that drives it"
    )


async def test_no_route_ships_without_a_session(client, local_clerk):
    """§9: "Clerk JWT required unless noted", read off the app rather than a list.

    A new router registered without a dependency shows up here as a failure
    naming the path, instead of quietly shipping open. `PUBLIC_API_ROUTES` is
    §9's own division and nothing else is allowed to join it silently.
    """
    from storyforge.auth import current_principal

    # A route with no `dependant` at all is FastAPI's own (`/docs`,
    # `/openapi.json`) and is unauthenticated by definition — counted as open
    # rather than skipped, which is the exact `continue` the old walk used to
    # swallow the whole API with.
    open_routes = {
        route.path
        for route in _api_routes()
        if not any(
            d.call is current_principal
            for d in _flatten_dependencies(getattr(route, "dependant", None))
        )
    }
    assert open_routes <= PUBLIC_API_ROUTES | FRAMEWORK_ROUTES, (
        f"unauthenticated route(s): {sorted(open_routes - PUBLIC_API_ROUTES - FRAMEWORK_ROUTES)}"
    )
    assert (await client.get("/api/health")).status_code == 200


async def test_every_owner_scoped_route_refuses_another_parent(
    client, parent_a, parent_b, story
):
    """The Definition of Done, driven off the inventory instead of a prose list.

    The route-by-route tests above each say one thing well; this says the same
    thing about *every* route in `OWNER_SCOPED_ROUTES` at once, so a seventh
    owner-scoped route cannot be added without either wiring the check or
    turning this red. Parent A's ids are real and valid throughout — ownership
    is the only thing left that can refuse B.
    """
    a_kid = await _create_kid(client, parent_a)
    reading = await _start_reading(client, parent_a, a_kid, story["id"])
    subs = {
        "kid_id": a_kid,
        "reading_id": reading,
        "story_id": story["id"],
        "segment_id": story["segment_id"],
        "question_id": story["question_id"],
    }

    for template, (method, body_template) in OWNER_SCOPED_ROUTES.items():
        url = template.format(**subs)
        body = (
            None
            if body_template is None
            else {
                k: (subs[v.strip("{}")] if isinstance(v, str) and v.startswith("{") else v)
                for k, v in body_template.items()
            }
        )
        call = getattr(client, method)
        mine = await (
            call(url, json=body, headers=parent_a) if body else call(url, headers=parent_a)
        )
        assert mine.status_code < 400, (
            f"{method.upper()} {url} refused its OWNER {mine.status_code}: {mine.text} — "
            "the refusal below would then prove nothing"
        )
        theirs = await (
            call(url, json=body, headers=parent_b) if body else call(url, headers=parent_b)
        )
        assert theirs.status_code == 404, (
            f"{method.upper()} {url} → {theirs.status_code} for a parent who owns "
            f"nothing in it: {theirs.text}"
        )
