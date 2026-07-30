"""§9's `/api/admin/*` block and §11's admin portal (DEVIATIONS 9.1-9.19).

Four things this file is careful about.

**It never spends a cent.** `POST /api/admin/eval-runs` and `POST
/api/admin/cold-generate` both reach the Anthropic API on their happy paths, so
every test here stops at a guard: an estimate, a 422, a 409, or a job object
placed in the registry by hand. `test_no_test_started_a_run` asserts at the end
that nothing was launched. The live proof that the happy paths work is in the
report for this phase, not in the suite — a test that costs $0.03 to run is a
test nobody runs.

**It owns its rows.** The `eval_runs` rows in the shared database belong to real
runs and to whatever else is running right now (STATE.md's warning about a
concurrent writer). So the tests that read a run insert their own and delete it,
and nothing asserts a count or an id that another process can move.

**The auth boundary is tested with real tokens.** `tests/conftest.py` runs the
whole suite under §18's bypass as an *admin*, which is what lets the shape tests
below be about shapes. That makes the bypass useless for testing the gate, so the
role tests take the `local_clerk` fixture, which turns the bypass OFF and mints
real RS256 tokens against a real JWKS server on a real socket.

**The recorder is tested directly.** DEVIATIONS 9.14 wraps `obs.observation` to
read §7's node transitions without touching `pipeline.py`. Both directions are
asserted here: with no job in context the wrapper must be invisible, and with a
job it must record the node chain — neither of which needs an API call, because
`obs` is the seam.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest
from sqlalchemy import text

from storyforge import obs
from storyforge.api import admin
from storyforge.db import SessionLocal
from storyforge.llm.pricing import Usage
from storyforge.pipeline import Attempt, PipelineResult

# `asyncio_mode = "auto"` (pyproject) runs the async tests; a module-level
# `pytestmark` would also mark the pure ones and warn on every single run.

ADMIN_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/admin/eval-runs"),
    ("POST", "/api/admin/eval-runs"),
    ("GET", "/api/admin/eval-runs/1"),
    ("GET", "/api/admin/allowed-words?stage=1"),
    ("POST", "/api/admin/cold-generate"),
    ("GET", "/api/admin/cold-generate/cg_none"),
)


# --- a run this file owns -----------------------------------------------------
def _metrics(**over: object) -> dict:
    """A §14 aggregate reduced to the keys the portal reads. Shaped exactly like
    `eval.aggregate`'s output for those keys, so the summary projection and
    `criteria_verdict` are exercised on the real shape."""
    base = {
        "cells_planned": 4,
        "attempted": 4,
        "produced": 4,
        "accepted": 4,
        "gate_pass_rate": 1.0,
        "fallback_rate": 0.0,
        "outcomes": {"accepted": 4},
        "pipeline_seconds": 88.8,
        "wall_clock_s": 90.1,
        "decodability": {"min": 0.97, "mean": 0.99, "below_gate": 0, "p50": 1.0},
        "rewrite_depth": {"p95": 1, "max": 1, "mean": 0.5},
        "cost": {"total_usd": 0.1234, "pricing_date": "2026-07-30"},
        "by_stage": {"3": {"accepted": 2}},
        "by_theme": {"dragons": {"accepted": 4}},
    }
    base.update(over)
    return base


_CONFIG = {
    "stages_spec": "3,6",
    "themes_spec": "dragons",
    "stages": [3, 6],
    "themes": ["dragons"],
    "n": 2,
    "effort": "medium",
    "cold": True,
    "concurrency": 1,
    "budget_usd": 1.0,
    "pricing_date": "2026-07-30",
    "models": {"generation": "claude-sonnet-5", "fast": "claude-haiku-4-5"},
    "gate": 0.95,
    "cells_planned": 4,
    "git_commit": "deadbee",
}


@pytest.fixture
async def owned_run():
    """One `eval_runs` row plus two items, inserted and then deleted.

    Committed rather than held in an open transaction: the endpoint runs on its
    own session, so a row nobody committed is a row the route cannot see. The
    delete cascades to `eval_run_items` (§8), and nothing else in the schema
    points at either table.
    """
    async with SessionLocal() as session:
        run_id = (
            await session.execute(
                text(
                    "INSERT INTO eval_runs (started_at, finished_at, config, metrics, status) "
                    "VALUES (now(), now(), :config, :metrics, 'completed') RETURNING id"
                ),
                {"config": json.dumps(_CONFIG),
                 "metrics": json.dumps(_metrics())},
            )
        ).scalar_one()
        for i in range(2):
            await session.execute(
                text(
                    "INSERT INTO eval_run_items (run_id, story_id, metrics) "
                    "VALUES (:run, NULL, :m)"
                ),
                {"run": run_id, "m": json.dumps({"index": i, "outcome": "accepted"})},
            )
        await session.commit()
    try:
        yield int(run_id)
    finally:
        async with SessionLocal() as session:
            await session.execute(
                text("DELETE FROM eval_runs WHERE id = :id"), {"id": run_id}
            )
            await session.commit()


@pytest.fixture(autouse=True)
def clean_registries():
    """No test may leak a job or a launch into another test's busy check."""
    admin._jobs.clear()
    admin._launches.clear()
    yield
    admin._jobs.clear()
    admin._launches.clear()


# --- the auth boundary (§9, §13, DEVIATIONS 9.19) -----------------------------
@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
async def test_no_token_reaches_no_admin_route(client, local_clerk, method, path):
    """An absent token is 401 on every route, with the challenge header (§9)."""
    resp = await client.request(method, path, json={})
    assert resp.status_code == 401, (method, path, resp.text)
    assert resp.headers.get("www-authenticate") == "Bearer"
    # And the disclosure header proves which server answered (DEVIATIONS 7.25):
    # a bypassed one would have served this as an admin.
    assert resp.headers.get("X-Storyforge-Auth") == "enforced"


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
async def test_a_garbage_token_is_401_and_never_500(client, local_clerk, method, path):
    """The check that has to come first.

    A forged or malformed token must be refused by *verification*, not by a
    handler falling over — a 500 here would mean the token got far enough to run
    code. Five shapes: not a JWT at all, a JWT signed with a key the JWKS does not
    publish, `alg: none`, HS256 signed with the public key everyone can download,
    and a real token whose payload was edited to say `role: admin`.

    **The positive control at the end is what stops this test being vacuous**
    (AUDIT-PHASE7-FIXES F2's class of defect: a guard that passes because the
    fixture never exercised it). Without it, every assertion above would also pass
    if verification refused *everything* — a broken JWKS URL, a fixture that never
    started its server, a route that 401s unconditionally. So the same route, on
    the same fixture, over the same transport, is then handed a VALID admin token
    and must not answer 401 or 403. Both halves have to hold or neither means
    anything.
    """
    forgeries = {
        "not-a-jwt": "garbage.garbage.garbage",
        "unpublished key": local_clerk.mint(
            role="admin", key=local_clerk.rogue.private_pem.decode()
        ),
        "alg:none": local_clerk.unsigned(role="admin"),
        "HS256 with the public key": local_clerk.hs256_with_the_public_key(role="admin"),
        "tampered payload": local_clerk.tampered(role="parent"),
    }
    for label, token in forgeries.items():
        resp = await client.request(
            method, path, json={}, headers=local_clerk.bearer(token)
        )
        assert resp.status_code == 401, f"{label} on {method} {path}: {resp.status_code}"

    # The positive control. `json={}` is deliberately the *default* body for both
    # POSTs and neither spends anything: `/eval-runs` answers with an estimate
    # (DEVIATIONS 9.4) and `/cold-generate` answers 422 for its missing fields.
    valid = local_clerk.mint(sub="user_local_admin_control", role="admin")
    resp = await client.request(method, path, json={}, headers=local_clerk.bearer(valid))
    assert resp.status_code not in (401, 403), (
        f"a VALID admin token was refused {resp.status_code} on {method} {path} — "
        "the forgery assertions above therefore prove nothing"
    )
    assert admin._launches == {}, "the positive control started a run"


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
async def test_a_parent_token_reaches_no_admin_route(client, local_clerk, method, path):
    """§9 marks every one of these `(admin)`. A verified parent is 403, not 404:
    the route's existence is public (it is in the OpenAPI schema), so there is
    nothing to conceal, and 6.9's 404 rule is about ownership (DEVIATIONS 9.19)."""
    token = local_clerk.mint(sub="user_local_parent_admin_probe", role="parent")
    resp = await client.request(method, path, json={}, headers=local_clerk.bearer(token))
    assert resp.status_code == 403, (method, path, resp.text)
    assert "admin" in resp.json()["detail"]


async def test_an_admin_token_reaches_the_admin_routes(client, local_clerk):
    """The other direction, or the test above proves only that the routes are shut.

    Read-only routes only: the two POSTs spend money on their happy paths.
    """
    token = local_clerk.mint(sub="user_local_admin_probe", role="admin")
    for path in ("/api/admin/eval-runs", "/api/admin/allowed-words?stage=1"):
        resp = await client.get(path, headers=local_clerk.bearer(token))
        assert resp.status_code == 200, (path, resp.text)


async def test_the_admin_role_comes_from_the_token_not_the_row(client, local_clerk):
    """Demoting an admin in Clerk shuts these routes on the next request.

    `users.role` is a cache of the claim (`auth.load_or_create_user`), so this is
    the property that makes the gate meaningful rather than a stored flag.
    """
    sub = "user_local_admin_demoted"
    admin_token = local_clerk.mint(sub=sub, role="admin")
    assert (
        await client.get("/api/admin/eval-runs", headers=local_clerk.bearer(admin_token))
    ).status_code == 200
    parent_token = local_clerk.mint(sub=sub, role="parent")
    assert (
        await client.get("/api/admin/eval-runs", headers=local_clerk.bearer(parent_token))
    ).status_code == 403


#: SQL verbs, used to pick the statements out of the module's string constants.
_SQL_VERBS = ("select ", "insert into", "update ", "delete from")
#: The tables a child's record lives in (§8, §13).
_CHILD_TABLES = ("kids", "readings", "answers", "gpc_stats")


def _sql_constants(module_path: str) -> list[str]:
    """Every string constant in a module that contains a SQL verb.

    An AST walk rather than a substring scan of the file, so the module's own
    prose — which names these tables on purpose, to say it does not read them —
    cannot make the assertion pass or fail.
    """
    tree = ast.parse(pathlib.Path(module_path).read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if any(verb in low for verb in _SQL_VERBS):
                out.append(node.value)
    return out


def test_no_admin_route_reads_a_child_table():
    """DEVIATIONS 9.11/9.16: an admin is not an owner, and no route here is a way
    round that.

    The honest form of this claim is about the SQL the module can execute, not
    about the calls one test happened to make — so every SQL string constant in
    `admin.py` is read out of its AST and checked.
    """
    statements = _sql_constants(admin.__file__)
    assert statements, "no SQL found — the extraction is broken, not the module"
    for sql in statements:
        low = sql.lower()
        for table in _CHILD_TABLES:
            for shape in (f"from {table}", f"join {table}", f"into {table}", f"update {table}"):
                assert shape not in low, f"admin.py can execute `{shape}`:\n{sql}"
        assert "kid_id" not in low, f"admin SQL names a kid id (DEVIATIONS 9.16):\n{sql}"


def test_the_child_table_guard_can_fail():
    """The guard above is only worth having if it goes red. Sabotage-checked here
    rather than by hand, against a module-shaped string with one bad statement."""
    bad = pathlib.Path(admin.__file__).parent / "_guard_probe_not_real.py"
    bad.write_text('X = "SELECT display_name FROM kids WHERE id = 1"\n', encoding="utf-8")
    try:
        statements = _sql_constants(str(bad))
        assert statements == ["SELECT display_name FROM kids WHERE id = 1"]
        assert any("from kids" in s.lower() for s in statements)
    finally:
        bad.unlink()


# --- GET /api/admin/eval-runs (DEVIATIONS 9.2, 9.3) --------------------------
async def test_the_list_is_an_envelope_with_the_summary_and_the_budget(client, owned_run):
    resp = await client.get("/api/admin/eval-runs", params={"limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"total", "limit", "offset", "runs", "launches", "budget"}
    assert body["total"] >= 1
    assert body["limit"] == 5
    # Newest first, so the row this test inserted is the first one.
    assert body["runs"][0]["id"] == owned_run
    row = body["runs"][0]
    assert row["status"] == "completed"
    assert row["item_count"] == 2
    assert row["stages"] == [3, 6]
    assert row["themes"] == ["dragons"]
    assert row["cost_usd"] == pytest.approx(0.1234)
    assert row["decodability_mean"] == pytest.approx(0.99)
    assert row["rewrite_depth_p95"] == 1
    assert row["criteria_passed"] is True
    assert [c["name"] for c in row["criteria"]][0].startswith("every story reaches")
    # DEVIATIONS 9.3: the aggregate itself is NOT in the list.
    assert "metrics" not in row and "by_stage" not in row


async def test_the_list_paginates(client, owned_run):
    first = (await client.get("/api/admin/eval-runs", params={"limit": 1})).json()
    second = (
        await client.get("/api/admin/eval-runs", params={"limit": 1, "offset": 1})
    ).json()
    assert first["runs"][0]["id"] == owned_run
    assert len(first["runs"]) == 1
    assert second["offset"] == 1
    if second["runs"]:
        assert second["runs"][0]["id"] != owned_run


async def test_the_budget_block_reports_ceilings_and_stale_running_rows(client):
    body = (await client.get("/api/admin/eval-runs")).json()["budget"]
    assert body["per_request_ceiling_usd"] == admin.MAX_BUDGET_USD
    assert body["process_ceiling_usd"] == admin.PROCESS_BUDGET_USD
    # DEVIATIONS 9.5: the button's ceiling is BELOW the CLI's own default, or the
    # mirror of `make eval --budget` would be a mirror of its number too.
    assert admin.MAX_BUDGET_USD < body["cli_default_budget_usd"]
    assert body["process_committed_usd"] == 0
    assert body["in_flight_launch_id"] is None
    # 9.7: reported, never blocking.
    assert isinstance(body["stale_running_run_ids"], list)


async def test_a_run_with_no_metrics_has_no_verdict(client):
    """A `running` row cannot be judged, and an invented verdict about it would be
    worse than an absent one (DEVIATIONS 9.10)."""
    async with SessionLocal() as session:
        run_id = (
            await session.execute(
                text(
                    "INSERT INTO eval_runs (config, metrics, status) "
                    "VALUES ('{}'::jsonb, '{}'::jsonb, 'running') RETURNING id"
                )
            )
        ).scalar_one()
        await session.commit()
    try:
        row = (await client.get("/api/admin/eval-runs", params={"limit": 1})).json()["runs"][0]
        assert row["id"] == run_id
        assert row["criteria"] is None
        assert row["criteria_passed"] is None
        assert row["cost_usd"] is None
        detail = (await client.get(f"/api/admin/eval-runs/{run_id}")).json()
        assert detail["criteria"] is None and detail["status"] == "running"
        # And it shows up in the budget block's stale list (9.7).
        budget = (await client.get("/api/admin/eval-runs")).json()["budget"]
        assert run_id in budget["stale_running_run_ids"]
    finally:
        async with SessionLocal() as session:
            await session.execute(text("DELETE FROM eval_runs WHERE id = :i"), {"i": run_id})
            await session.commit()


# --- GET /api/admin/eval-runs/{id} (DEVIATIONS 9.10, 9.11) -------------------
async def test_the_detail_returns_the_aggregate_verbatim_and_paginates_items(
    client, owned_run
):
    resp = await client.get(f"/api/admin/eval-runs/{owned_run}", params={"items_limit": 1})
    assert resp.status_code == 200
    body = resp.json()
    # §14's aggregate, unmodified — the portal is a view (9.10).
    assert body["metrics"] == _metrics()
    assert body["config"] == _CONFIG
    assert body["criteria_passed"] is True
    assert body["items_total"] == 2
    assert body["items_limit"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["metrics"]["index"] == 0
    assert body["items"][0]["story_id"] is None
    later = (
        await client.get(
            f"/api/admin/eval-runs/{owned_run}",
            params={"items_limit": 1, "items_offset": 1},
        )
    ).json()
    assert later["items"][0]["metrics"]["index"] == 1
    # items_limit=0 is the "just the tables" request.
    none = (
        await client.get(f"/api/admin/eval-runs/{owned_run}", params={"items_limit": 0})
    ).json()
    assert none["items"] == [] and none["items_total"] == 2


async def test_the_detail_verdict_is_the_harness_own_function(client, owned_run):
    """§14 makes the CLI canonical. The portal's PASS/FAIL must be the same
    computation `make eval`'s exit code is, so it is asserted against the function
    rather than against a transcription of its output."""
    from storyforge.eval import criteria_verdict

    body = (await client.get(f"/api/admin/eval-runs/{owned_run}")).json()
    expected = [
        {"name": n, "ok": ok, "evidence": ev} for n, ok, ev in criteria_verdict(_metrics())
    ]
    assert body["criteria"] == expected


async def test_a_failing_run_reports_as_failing(client):
    """A bad pass rate is reported as bad (§14's own words about a flattering eval)."""
    async with SessionLocal() as session:
        run_id = (
            await session.execute(
                text(
                    "INSERT INTO eval_runs (started_at, finished_at, config, metrics, status) "
                    "VALUES (now(), now(), :c, :m, 'completed') RETURNING id"
                ),
                {
                    "c": json.dumps(_CONFIG),
                    "m": json.dumps(
                        _metrics(
                            accepted=1,
                            gate_pass_rate=0.25,
                            decodability={"min": 0.4, "mean": 0.7, "below_gate": 3},
                            rewrite_depth={"p95": 3, "max": 3},
                        )
                    ),
                },
            )
        ).scalar_one()
        await session.commit()
    try:
        body = (await client.get(f"/api/admin/eval-runs/{run_id}")).json()
        assert body["criteria_passed"] is False
        failed = [c for c in body["criteria"] if not c["ok"]]
        assert len(failed) == 2
        assert "3 below" in failed[0]["evidence"]
    finally:
        async with SessionLocal() as session:
            await session.execute(text("DELETE FROM eval_runs WHERE id = :i"), {"i": run_id})
            await session.commit()


async def test_an_unknown_run_id_is_404(client):
    resp = await client.get("/api/admin/eval-runs/2147483000")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "eval run 2147483000 not found"


# --- GET /api/admin/allowed-words (§5.4, DEVIATIONS 9.12) --------------------
async def test_allowed_words_is_paginated_and_counts_the_whole_stage(client):
    """§17 step 2's screen. `total` is the real count; the page is 100 by default."""
    first = (await client.get("/api/admin/allowed-words", params={"stage": 3})).json()
    assert first["limit"] == admin.WORDS_DEFAULT_LIMIT
    assert len(first["words"]) == admin.WORDS_DEFAULT_LIMIT
    # §5.4 expects 150-2,500 per stage; the table holds far more at the top stages,
    # which is exactly why this route may not be unpaginated (9.12).
    assert first["total"] > admin.WORDS_DEFAULT_LIMIT
    assert first["total"] == first["stage_total"]
    assert first["has_more"] is True
    assert first["next_offset"] == admin.WORDS_DEFAULT_LIMIT
    assert first["stage"]["id"] == 3 and first["stage"]["name"]
    # Frequency order, which is the order §6 draws the prompt allowlist in.
    ranks = [w["freq_rank"] for w in first["words"]]
    assert ranks == sorted(ranks)
    second = (
        await client.get(
            "/api/admin/allowed-words", params={"stage": 3, "offset": admin.WORDS_DEFAULT_LIMIT}
        )
    ).json()
    assert {w["word"] for w in first["words"]}.isdisjoint({w["word"] for w in second["words"]})


async def test_allowed_words_marks_what_the_prompt_actually_sees(client):
    """"Every word a stage-3 reader may see" and "the 400 the model is shown" are
    different claims, and this is the screen where they can be compared (9.12)."""
    from storyforge.pipeline import PROMPT_WORD_LIMIT

    body = (await client.get("/api/admin/allowed-words", params={"stage": 3})).json()
    assert body["prompt_word_limit"] == PROMPT_WORD_LIMIT
    cutoff = body["prompt_freq_rank_cutoff"]
    assert cutoff is not None
    for w in body["words"]:
        assert w["in_prompt"] == (w["freq_rank"] <= cutoff)
    # The first page is inside the prompt at every stage: it is the same ORDER BY.
    assert all(w["in_prompt"] for w in body["words"])
    # A page BEYOND the prompt window must report `in_prompt: False`. The offset is
    # DERIVED from the limit rather than hard-coded: it was 8000, which only held
    # while the top stage carried the most rows, and DEVIATIONS 11.8's register filter takes
    # that to 4,397. The claim under test is "past the window", not "row 8000".
    deep = (
        await client.get(
            "/api/admin/allowed-words",
            params={"stage": 11, "offset": PROMPT_WORD_LIMIT + 100},
        )
    ).json()
    assert deep["words"] and not any(w["in_prompt"] for w in deep["words"])


async def test_allowed_words_search_is_a_literal_substring(client):
    """A `%` typed into the demo's search box is a character, not a wildcard (9.12)."""
    hits = (
        await client.get("/api/admin/allowed-words", params={"stage": 3, "q": "sh"})
    ).json()
    assert hits["q"] == "sh"
    assert hits["total"] > 0
    assert all("sh" in w["word"] for w in hits["words"])
    # `total` narrows to the filter while `stage_total` does not.
    assert hits["total"] < hits["stage_total"]
    wild = (
        await client.get("/api/admin/allowed-words", params={"stage": 3, "q": "%"})
    ).json()
    assert wild["total"] == 0, "`%` was treated as a wildcard"
    under = (
        await client.get("/api/admin/allowed-words", params={"stage": 3, "q": "_"})
    ).json()
    assert under["total"] == 0, "`_` was treated as a wildcard"


async def test_allowed_words_alpha_order(client):
    body = (
        await client.get(
            "/api/admin/allowed-words", params={"stage": 3, "order": "alpha", "limit": 20}
        )
    ).json()
    words = [w["word"] for w in body["words"]]
    assert words == sorted(words)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, 422),  # `stage` is required
        ({"stage": 0}, 422),
        ({"stage": 13}, 422),
        ({"stage": 3, "limit": 0}, 422),
        ({"stage": 3, "limit": admin.WORDS_MAX_LIMIT + 1}, 422),
        ({"stage": 3, "offset": -1}, 422),
        ({"stage": 3, "order": "random"}, 422),
    ],
)
async def test_allowed_words_refuses_a_bad_page(client, params, expected):
    assert (
        await client.get("/api/admin/allowed-words", params=params)
    ).status_code == expected


# --- POST /api/admin/eval-runs — the spend gate (DEVIATIONS 9.4-9.7) ---------
async def test_the_default_post_is_an_estimate_and_spends_nothing(client):
    """§14's own matrix as the default body, and no API call unless told twice."""
    resp = await client.post("/api/admin/eval-runs", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "estimate"
    assert body["launched"] is False
    assert body["cells_planned"] == 165  # 11 stages x 3 themes x 5
    assert body["projection"]["with_one_rewrite_each_usd"] > 0
    assert body["requires"]["confirm"] is True
    assert "No API call was made" in body["detail"]
    assert admin._launches == {}


async def test_the_estimate_quotes_a_comparable_real_run(client):
    """A projection is a token model, not a promise (DEVIATIONS 7.44). The last
    completed run of the same size is what makes it readable."""
    body = (await client.post("/api/admin/eval-runs", json={})).json()
    reference = body["reference_run"]
    if reference is not None:  # the shared database may hold no 165-cell run
        assert reference["id"] > 0
        assert reference["cost_usd"] > 0


async def test_confirm_without_a_budget_is_refused(client):
    resp = await client.post("/api/admin/eval-runs", json={"confirm": True})
    assert resp.status_code == 422
    assert "requires an explicit budget_usd" in resp.json()["detail"]
    assert admin._launches == {}


async def test_a_budget_over_the_ceiling_is_refused(client):
    resp = await client.post(
        "/api/admin/eval-runs",
        json={"stages": "3", "themes": "dragons", "n": 1,
              "confirm": True, "budget_usd": admin.MAX_BUDGET_USD + 0.01},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert f"${admin.MAX_BUDGET_USD:.2f} ceiling" in detail
    # The escape hatch is named, and it is a shell rather than a bigger number here.
    assert "make eval" in detail
    assert admin._launches == {}


async def test_a_budget_under_the_projection_is_refused_before_any_call(client):
    """`run_full` would exit 2 here; a caller deserves the HTTP error instead."""
    resp = await client.post(
        "/api/admin/eval-runs",
        json={"confirm": True, "budget_usd": 0.01},
    )
    assert resp.status_code == 422
    assert "before any" in resp.json()["detail"]
    assert admin._launches == {}


async def test_a_matrix_bigger_than_14s_is_refused(client):
    resp = await client.post("/api/admin/eval-runs", json={"stages": "1..11", "n": 5})
    assert resp.status_code == 200  # 165 cells is §14's matrix exactly
    assert resp.json()["cells_planned"] == admin.MAX_CELLS
    assert (
        await client.post("/api/admin/eval-runs", json={"n": admin.MAX_N + 1})
    ).status_code == 422


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"stages": "nope"}, "bad stages spec"),
        ({"stages": "1..40"}, "within 1..11"),
        ({"stages": "0"}, "within 1..11"),
        ({"themes": "unicorns"}, "unknown theme"),
        ({"themes": ","}, "no themes"),
    ],
)
async def test_a_bad_matrix_is_422_and_not_a_dead_worker(client, body, fragment):
    """`eval.parse_stages` and `pipeline.load_vocabulary` raise `SystemExit` — a
    `BaseException` that would leave the handler — so both are converted here."""
    resp = await client.post("/api/admin/eval-runs", json=body)
    assert resp.status_code == 422, resp.text
    assert fragment in resp.json()["detail"]


async def test_a_launch_in_flight_blocks_another(client):
    """One at a time per process (DEVIATIONS 9.7). Staged with a registry entry
    rather than a real run, so this test costs nothing."""
    admin._launches["el_test"] = admin.EvalLaunch(
        launch_id="el_test",
        stages_spec="3",
        themes_spec="dragons",
        n=1,
        effort="medium",
        concurrency=1,
        budget_usd=0.5,
        cells_planned=1,
        projection={},
        baseline_run_id=0,
        started_by="user_test",
    )
    resp = await client.post(
        "/api/admin/eval-runs",
        json={"stages": "3", "themes": "dragons", "n": 1,
              "confirm": True, "budget_usd": 1.0},
    )
    assert resp.status_code == 409
    assert "still running" in resp.json()["detail"]
    # …and the in-flight launch is visible on the list's own poll (9.2).
    listed = (await client.get("/api/admin/eval-runs")).json()
    assert listed["budget"]["in_flight_launch_id"] == "el_test"
    assert listed["launches"][0]["launch_id"] == "el_test"
    assert listed["launches"][0]["ephemeral"] is True


async def test_the_process_ceiling_blocks_a_repeated_click(client):
    """A per-run cap bounds one run; ten sequential clicks are ten runs (9.6)."""
    for i in range(2):
        launch = admin.EvalLaunch(
            launch_id=f"el_done{i}",
            stages_spec="1..11",
            themes_spec="all",
            n=5,
            effort="medium",
            concurrency=1,
            budget_usd=admin.MAX_BUDGET_USD,
            cells_planned=180,
            projection={},
            baseline_run_id=0,
            started_by="user_test",
        )
        launch.state = "done"
        admin._launches[launch.launch_id] = launch
    assert admin._committed_usd() == pytest.approx(2 * admin.MAX_BUDGET_USD)
    resp = await client.post(
        "/api/admin/eval-runs",
        json={"stages": "3", "themes": "dragons", "n": 1,
              "confirm": True, "budget_usd": 0.5},
    )
    assert resp.status_code == 409
    assert "cumulative ceiling" in resp.json()["detail"]


# --- POST /api/admin/cold-generate (DEVIATIONS 9.13-9.18) --------------------
async def test_cold_generate_refuses_an_unknown_stage_or_theme(client):
    assert (
        await client.post("/api/admin/cold-generate", json={"stage_id": 13, "theme": "dragons"})
    ).status_code == 422  # outside §4's 1..11 at the schema
    resp = await client.post(
        "/api/admin/cold-generate", json={"stage_id": 3, "theme": "unicorns"}
    )
    assert resp.status_code == 404
    assert "unknown theme" in resp.json()["detail"]
    assert admin._jobs == {}


async def test_cold_generate_takes_no_kid_id(client):
    """DEVIATIONS 9.16. Extra fields are ignored by the model, so the assertion is
    about the schema: there is no field a caller could aim at a child."""
    assert "kid_id" not in admin.ColdGenerateRequest.model_fields
    assert set(admin.ColdGenerateRequest.model_fields) == {
        "stage_id",
        "theme",
        "effort",
        "moderate",
    }


async def test_a_running_job_blocks_another_and_is_pollable(client):
    """One job at a time, which is what bounds the spend (9.17). Staged with a job
    object; no generation is started."""
    job = admin.ColdJob(
        job_id="cg_test",
        stage_id=3,
        theme_slug="dragons",
        theme_id=1,
        effort="medium",
        moderate=True,
        started_by="user_test",
    )
    job.state = "running"
    job.mark("RECEIVED", {"stage_id": 3, "theme": "dragons"})
    admin._jobs[job.job_id] = job

    resp = await client.post(
        "/api/admin/cold-generate", json={"stage_id": 3, "theme": "dragons"}
    )
    assert resp.status_code == 409
    assert "already running" in resp.json()["detail"]
    assert "cg_test" in resp.json()["detail"]

    snap = (await client.get("/api/admin/cold-generate/cg_test")).json()
    assert snap["job_id"] == "cg_test"
    assert snap["state"] == "running"
    assert snap["persisted"] is False
    assert snap["poll"] == "/api/admin/cold-generate/cg_test"
    assert snap["poll_after_ms"] == admin.POLL_AFTER_MS
    assert snap["transitions"][0]["node"] == "RECEIVED"
    assert snap["transitions"][0]["source"] == "job"
    assert "langfuse" in snap and "enabled" in snap["langfuse"]
    # 9.18: no URL is invented.
    assert snap["langfuse"]["url"] is None


async def test_an_unknown_job_is_404_and_says_why(client):
    resp = await client.get("/api/admin/cold-generate/cg_nothing")
    assert resp.status_code == 404
    assert "lost on restart" in resp.json()["detail"]


# --- the node recorder (DEVIATIONS 9.14) -------------------------------------
def test_the_recorder_is_installed_once():
    assert getattr(obs.observation, "_storyforge_admin_recorder", False) is True
    before = obs.observation
    admin._install_node_recorder()
    assert obs.observation is before, "the wrapper wrapped itself"


def test_with_no_job_in_context_the_recorder_is_invisible():
    """The half that matters for everything else in the system: every eval cell,
    every `make warm` story and every kid request goes through this wrapper."""
    assert admin._JOB.get() is None
    with obs.span("VALIDATING", input={"x": 1}) as handle:
        # `obs` is inert without Langfuse keys and yields a real `Span` either way.
        assert isinstance(handle, obs.Span)
        assert handle.trace_id is None or isinstance(handle.trace_id, str)
        handle.update(output={"decodability": 1.0})
    # Nothing was recorded, because there was nothing to record onto.
    assert admin._JOB.get() is None


def test_with_a_job_in_context_the_node_chain_is_recorded():
    """Exercised through `obs`, which is the seam §7's machine actually uses — so
    this asserts the recorder without an API call and without `pipeline.py`."""
    job = admin.ColdJob(
        job_id="cg_ctx",
        stage_id=3,
        theme_slug="dragons",
        theme_id=1,
        effort="medium",
        moderate=True,
        started_by="user_test",
    )
    token = admin._JOB.set(job)
    try:
        with obs.span(obs.TRACE_NAME, input={"stage_id": 3}) as trace:
            assert trace is not None
            with obs.generation("GENERATING", metadata={"attempt": 0}) as gen:
                gen.update(
                    output={"title": "T", "text": "a cat sat"},
                    usage=Usage(model="claude-sonnet-5", input_tokens=10, output_tokens=20),
                )
            with obs.span("VALIDATING", metadata={"attempt": 0}) as val:
                val.update(output={"decodability": 0.9, "violations": ["gleaming"]})
            with obs.generation("REWRITING", metadata={"attempt": 1}) as rw:
                rw.update(output={"text": "a cat sat"})
    finally:
        admin._JOB.reset(token)

    chain = [(t["node"], t["status"], t["attempt"]) for t in job.transitions]
    # The ROOT span is not a node and is excluded; its id is published instead.
    assert chain == [
        ("GENERATING", "done", 0),
        ("VALIDATING", "done", 0),
        ("REWRITING", "done", 1),
    ]
    assert job.transitions[0]["detail"]["output"]["text"] == "a cat sat"
    assert job.transitions[0]["detail"]["usage"]["model"] == "claude-sonnet-5"
    assert job.transitions[0]["detail"]["usage"]["output_tokens"] == 20
    # §17 step 4's evidence: the validator's own verdict, on the node.
    assert job.transitions[1]["detail"]["output"]["violations"] == ["gleaming"]
    assert all(t["duration_ms"] is not None for t in job.transitions)
    assert job.revision >= len(job.transitions)
    assert job.current_node is None


def test_a_node_that_raises_is_recorded_as_an_error_and_the_exception_survives():
    job = admin.ColdJob(
        job_id="cg_raise",
        stage_id=3,
        theme_slug="dragons",
        theme_id=1,
        effort="medium",
        moderate=True,
        started_by="user_test",
    )
    token = admin._JOB.set(job)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with obs.generation("GENERATING", metadata={"attempt": 0}):
                raise RuntimeError("boom")
    finally:
        admin._JOB.reset(token)
    assert job.transitions[0]["status"] == "error"
    assert "RuntimeError: boom" in job.transitions[0]["error"]


# --- the rewrite loop §17 step 4 shows (pure) --------------------------------
def _attempt(n: int, kind: str, text_: str, dec: float, violations: list[str]) -> Attempt:
    return Attempt(
        n=n,
        kind=kind,
        text=text_,
        decodability=dec,
        violations=violations,
        usage=Usage(model="claude-sonnet-5", input_tokens=1, output_tokens=1),
        latency_ms=1.0,
        validate_ms=0.1,
    )


def test_the_rewrite_loop_names_what_the_validator_caught_and_what_replaced_it():
    """§17 step 4, verbatim: "the model tried 'gleaming'; the validator caught it;
    the rewrite swapped it". Pure, so it is asserted without spending anything."""
    result = PipelineResult(
        stage_id=3,
        theme="dragons",
        passed=True,
        attempts=[
            _attempt(0, "generate", "The gleaming dragon sat.", 0.80, ["gleaming"]),
            _attempt(1, "rewrite", "The big dragon sat.", 1.0, []),
        ],
    )
    loop = admin._rewrite_loop(result)
    assert [r["attempt"] for r in loop] == [0, 1]
    assert loop[0]["kind"] == "generate"
    assert loop[0]["violations"] == ["gleaming"]
    assert loop[0]["caught"] == []  # nothing preceded it
    assert loop[1]["kind"] == "rewrite"
    assert loop[1]["caught"] == ["gleaming"]
    assert loop[1]["fixed"] == ["gleaming"]
    assert loop[1]["still_present"] == []
    assert loop[1]["new_words"] == ["big"]


def test_a_rewrite_that_did_not_fix_the_word_says_so():
    """The interesting failure: a rewrite that kept the violation. Reporting it as
    fixed would be the flattering-eval mistake §14 is written against."""
    result = PipelineResult(
        stage_id=3,
        theme="dragons",
        passed=False,
        attempts=[
            _attempt(0, "generate", "The gleaming dragon sat.", 0.80, ["gleaming"]),
            _attempt(1, "rewrite", "The gleaming dragon ran.", 0.80, ["gleaming"]),
        ],
    )
    loop = admin._rewrite_loop(result)
    assert loop[1]["fixed"] == []
    assert loop[1]["still_present"] == ["gleaming"]
    assert loop[1]["new_words"] == ["ran"]


def test_the_outcome_uses_7s_failure_taxonomy():
    from storyforge.llm.moderate import ModerationResult

    passed = PipelineResult(stage_id=3, theme="dragons", passed=True)
    assert admin._outcome(passed) == "accepted"
    exhausted = PipelineResult(stage_id=3, theme="dragons", passed=False)
    assert admin._outcome(exhausted) == "budget_exhausted"
    rejected = PipelineResult(
        stage_id=3,
        theme="dragons",
        passed=False,
        moderation=ModerationResult(approved=False, layer="model", reason="nope"),
    )
    assert admin._outcome(rejected) == "moderation_fail"


# --- the guard on this file itself -------------------------------------------
async def test_no_test_started_a_run(client):
    """Runs last-ish by name and by intent: if any test above launched a run or a
    generation, the registries would not be empty when it finished."""
    assert admin._launches == {}
    assert admin._jobs == {}
