"""§9's `(public, cached)` pair — `GET /api/stages` and `GET /api/themes`.

AUDIT-PHASE6 C12: both were specified in §9 and neither was mounted, so every kid
session load 404'd on `GET /api/themes` (twice, under StrictMode) and the SPA fell
back to a hand-copy of `data/seed/*.csv` that nothing could tell had drifted.

These tests hit the real app over ASGI against the real seeded database. Two of
them are the ones that matter, and neither is about a 200:

- **every slug this route returns must resolve for `POST /api/stories/request`**.
  That is the C12 harm in one assertion — an unresolvable slug is a 404 the kid
  client cannot retry, so it burns three attempts and parks on the printer (C10).
- **`servable_stories` is reported and the route is not filtered by it**, because
  a filter that emptied the picker would be a screen with nothing to tap.
"""

import httpx
import pytest
from sqlalchemy import text

from storyforge.api.catalog import CACHE_CONTROL
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.main import app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_themes_returns_every_seeded_theme_in_id_order(client):
    resp = await client.get("/api/themes")
    assert resp.status_code == 200
    body = resp.json()

    async with SessionLocal() as s:
        rows = (await s.execute(text("SELECT id, slug, display_name, emoji FROM themes"))).all()
    expected = {int(r.id): (r.slug, r.display_name, r.emoji) for r in rows}

    assert [t["id"] for t in body] == sorted(expected), "ids must be seed order"
    for theme in body:
        slug, display_name, emoji = expected[theme["id"]]
        assert (theme["slug"], theme["display_name"], theme["emoji"]) == (
            slug,
            display_name,
            emoji,
        )


async def test_stages_returns_all_eleven_stages_in_curriculum_order(client):
    body = (await client.get("/api/stages")).json()
    assert [s["id"] for s in body] == list(range(1, 12))
    assert all(s["name"] and s["description"] for s in body)
    # Generation parameters are not a public payload (DEVIATIONS 8.60).
    assert set(body[0]) == {"id", "name", "description"}


def _dependency_names(route) -> set[str]:
    """Every callable in a route's dependency tree, by name."""
    seen: set[str] = set()
    stack = list(getattr(route.dependant, "dependencies", []))
    while stack:
        dep = stack.pop()
        call = getattr(dep, "call", None)
        if call is not None:
            seen.add(getattr(call, "__name__", repr(call)))
        stack.extend(getattr(dep, "dependencies", []))
    return seen


async def test_both_routes_are_public_and_need_no_token(client):
    """§9 lists them as `(public, cached)`; only `/api/health` shares that.

    The suite runs under §18's bypass, so "no Authorization header" is not
    evidence on its own — `tests/test_auth.py` is where enforcement is proved.
    What this pins is that neither route grew an auth dependency, which would make
    the theme picker's first paint wait on Clerk having loaded.

    The `/api/kids` half is the anti-vacuity control: if `_dependency_names` ever
    stops seeing dependencies at all, the first two assertions would pass on
    nothing and this one goes red.
    """
    from storyforge.api import catalog, kids

    for route in catalog.router.routes:
        assert route.path in ("/api/themes", "/api/stages"), route.path
        names = _dependency_names(route)
        assert "require_parent" not in names and "current_principal" not in names, names
        assert (await client.get(route.path)).status_code == 200

    guarded = {n for r in kids.router.routes for n in _dependency_names(r)}
    assert "require_parent" in guarded, "the control failed: no auth dependency was seen anywhere"


async def test_both_routes_are_cacheable(client):
    """§9's "cached" is a property of the response, not a note about the server."""
    for path in ("/api/themes", "/api/stages"):
        assert (await client.get(path)).headers["cache-control"] == CACHE_CONTROL
    assert "max-age" in CACHE_CONTROL


async def test_every_theme_this_route_offers_resolves_for_a_story_request(client):
    """The C12 harm, as one assertion.

    A slug the picker shows and `_resolve_theme` refuses is a 404 on
    `POST /api/stories/request`, and a 404 is not retryable — kid mode spends its
    three attempts and lands on the printer screen for good (C10). This route
    reads the same `themes` table `_resolve_theme` reads, so the property is
    structural; the test is here because the SPA's fallback list is NOT, and it
    fails loudly if either side ever stops using that table.
    """
    themes = (await client.get("/api/themes")).json()
    assert themes, "an empty theme list is a picker with nothing to tap"

    async with SessionLocal() as s:
        for theme in themes:
            row = (
                await s.execute(
                    text(
                        "SELECT id, slug FROM themes WHERE slug = :slug OR id = :id "
                        "ORDER BY (slug = :slug) DESC LIMIT 1"
                    ),
                    {"slug": theme["slug"], "id": theme["id"]},
                )
            ).first()
            assert row is not None, f"offered theme {theme['slug']!r} does not resolve"
            assert int(row.id) == theme["id"]


async def test_servable_depth_is_reported_and_is_the_serving_paths_own_count(client):
    themes = (await client.get("/api/themes")).json()
    async with SessionLocal() as s:
        counts = {
            int(r.theme_id): int(r.n)
            for r in (
                await s.execute(
                    text(
                        "SELECT theme_id, count(*) AS n FROM stories "
                        "WHERE status = ANY(:statuses) GROUP BY theme_id"
                    ),
                    {"statuses": list(SERVABLE_STATUSES)},
                )
            ).all()
        }
    for theme in themes:
        assert theme["servable_stories"] == counts.get(theme["id"], 0), theme["slug"]


async def test_a_theme_with_no_servable_story_is_still_offered(client):
    """Not filtered — on purpose, and this is the test that says so.

    §7's ladder answers an empty (stage, theme) cell with a lower stage or another
    theme, so a themeless pool is a preference miss and not a dead end. Hiding the
    card would turn it into one: §07 fixes the picker at 3-6 cards and an empty
    picker is a screen a child cannot leave (DEVIATIONS 8.68).
    """
    async with SessionLocal() as s:
        theme_id = (
            await s.execute(
                text(
                    "INSERT INTO themes (slug, display_name, emoji) "
                    "VALUES ('zz-empty-catalog-test', 'ZZ Empty', '🧪') RETURNING id"
                )
            )
        ).scalar_one()
        await s.commit()
    try:
        themes = (await client.get("/api/themes")).json()
        planted = next((t for t in themes if t["id"] == theme_id), None)
        assert planted is not None, "a theme with zero servable stories was hidden"
        assert planted["servable_stories"] == 0
    finally:
        async with SessionLocal() as s:
            await s.execute(text("DELETE FROM themes WHERE id = :id"), {"id": theme_id})
            await s.commit()
