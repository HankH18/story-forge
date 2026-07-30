"""Phase 6: Clerk JWT verification, roles and the §18 bypass — as attacks.

There are no Clerk credentials on this machine. That does not weaken this suite,
because a Clerk session token is nothing more exotic than an RS256 JWT whose
public key is published as a JWKS document: `tests/conftest.py` generates a real
2048-bit RSA keypair, serves a real JWKS over a real socket, and mints real
tokens. Every test below drives `storyforge.auth` through the same code path a
production request takes. Swapping in Clerk is `CLERK_JWKS_URL`.

The suite is written the way an attacker would write it. A happy-path test tells
you the door opens; these tell you what the door refuses, which is the only
property that matters here. Nine forgeries get their own named test — expired,
premature, wrong audience, missing audience, wrong issuer, `alg: none`,
`alg: HS256` signed with the downloadable public key, signed by a different RSA
key, edited payload, absent `kid`, unknown `kid` — and one parametrised sweep
asserts the thing behind all of them: **every rejection is a 401, never a 500.**
A malformed-token crash is not a safe failure; it is an unhandled exception with
a stack trace, and 500s are how "invalid" quietly becomes "unhandled".

`GET /api/kids` is the probe for most of them. It is the cheapest authenticated
route and it returns a list, so a leak would be visible rather than inferred.
"""

from __future__ import annotations

import asyncio
import time

import jwt
import pytest
from sqlalchemy import text

from storyforge.auth import (
    ALGORITHM,
    DEMO_CLERK_ID,
    JwksCache,
    UnknownSigningKey,
    assert_demo_bypass_is_local,
    auth_config,
    deployment_signals,
    reset_auth_config,
    role_from_claims,
    verify_token,
)
from storyforge.db import SessionLocal


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


@pytest.fixture(autouse=True)
async def _clean_users():
    """Delete the users these tests mint. `kids` cascades from `users`."""
    yield
    async with SessionLocal() as s:
        await s.execute(
            text("DELETE FROM users WHERE clerk_id LIKE 'user_local%' OR clerk_id = :demo"),
            {"demo": DEMO_CLERK_ID},
        )
        await s.commit()


async def _kids(client, token: str | None):
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return await client.get("/api/kids", headers=headers)


# --- the door opens for a good token -----------------------------------------
async def test_a_valid_token_is_accepted_and_creates_the_user(client, local_clerk, session):
    """The baseline. Without this, every rejection below could be vacuous.

    §9: the dependency "loads/creates `users` row". A first request from a Clerk
    user this database has never seen must produce the row rather than 401 — a
    parent's first login is not an error condition.
    """
    token = local_clerk.mint(sub="user_local_a", role="parent", email="a@example.com")
    resp = await _kids(client, token)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    row = (
        await session.execute(
            text("SELECT clerk_id, role, email FROM users WHERE clerk_id = 'user_local_a'")
        )
    ).one()
    assert (row.clerk_id, row.role, row.email) == ("user_local_a", "parent", "a@example.com")


async def test_the_user_row_is_created_once_not_once_per_request(client, local_clerk, session):
    """Load-OR-create. Ten calls, one row — and no duplicate-key crash."""
    token = local_clerk.mint(sub="user_local_a")
    for _ in range(10):
        assert (await _kids(client, token)).status_code == 200
    count = (
        await session.execute(
            text("SELECT count(*) FROM users WHERE clerk_id = 'user_local_a'")
        )
    ).scalar_one()
    assert count == 1


async def test_two_concurrent_first_requests_do_not_double_insert(client, local_clerk, session):
    """The upsert's reason for existing, exercised rather than asserted.

    A parent opening two tabs is two simultaneous first requests. A
    check-then-insert would race here; `ON CONFLICT (clerk_id)` cannot.
    """
    token = local_clerk.mint(sub="user_local_race")
    results = await asyncio.gather(*(_kids(client, token) for _ in range(8)))
    assert {r.status_code for r in results} == {200}
    count = (
        await session.execute(
            text("SELECT count(*) FROM users WHERE clerk_id = 'user_local_race'")
        )
    ).scalar_one()
    assert count == 1


# --- forgeries ---------------------------------------------------------------
async def test_an_expired_token_is_rejected(client, local_clerk):
    resp = await _kids(client, local_clerk.mint(expires_in=-60))
    assert resp.status_code == 401
    assert "Expired" in resp.json()["detail"]


async def test_a_token_with_no_expiry_is_rejected(client, local_clerk):
    """A token that never expires is a permanent credential in a bearer header.

    PyJWT will happily accept one unless `exp` is in `require`, so this asserts
    the option is actually set rather than assumed.
    """
    resp = await _kids(client, local_clerk.mint(expires_in=None))
    assert resp.status_code == 401
    assert "exp" in resp.json()["detail"]


async def test_a_token_not_yet_valid_is_rejected(client, local_clerk):
    """`nbf` in the future. Rejected because clock skew tolerance defaults to 0."""
    resp = await _kids(client, local_clerk.mint(not_before_in=600))
    assert resp.status_code == 401
    assert "Immature" in resp.json()["detail"]


async def test_the_wrong_audience_is_rejected(client, local_clerk):
    resp = await _kids(client, local_clerk.mint(audience="some-other-app"))
    assert resp.status_code == 401
    assert "Audience" in resp.json()["detail"]


async def test_a_missing_audience_is_rejected_when_one_is_configured(client, local_clerk):
    """Dropping a claim must not be a way around checking it."""
    resp = await _kids(client, local_clerk.mint(audience=None))
    assert resp.status_code == 401
    assert "aud" in resp.json()["detail"]


async def test_the_wrong_issuer_is_rejected(client, local_clerk):
    """The issuer here was derived from CLERK_JWKS_URL, not configured.

    So this also proves the derivation produced something real: if it had
    yielded None, an attacker's issuer would sail straight through.
    """
    resp = await _kids(client, local_clerk.mint(issuer="https://evil.example.com"))
    assert resp.status_code == 401
    assert "Issuer" in resp.json()["detail"]


async def test_a_missing_issuer_is_rejected(client, local_clerk):
    resp = await _kids(client, local_clerk.mint(issuer=None))
    assert resp.status_code == 401


async def test_an_unsigned_token_is_rejected(client, local_clerk):
    """`alg: none` — the oldest JWT bug there is, and still worth a test."""
    resp = await _kids(client, local_clerk.unsigned())
    assert resp.status_code == 401
    assert "none" in resp.json()["detail"]


async def test_algorithm_confusion_hs256_with_the_public_key_is_rejected(client, local_clerk):
    """The public key is *published*. Anyone can HMAC with it.

    If this service accepted HS256, downloading the JWKS would be enough to mint
    an admin token for any user id. The rejection happens at the header, before
    a key is even looked up, so the forgery never reaches a verifier that might
    be talked into using an RSA public key as an HMAC secret.
    """
    forged = local_clerk.hs256_with_the_public_key(role="admin", sub="user_local_attacker")
    resp = await _kids(client, forged)
    assert resp.status_code == 401
    assert "HS256" in resp.json()["detail"]


async def test_a_token_signed_by_a_different_key_is_rejected(client, local_clerk):
    """Right shape, right `kid`, wrong private key. Only the signature disagrees."""
    forged = local_clerk.mint(key=local_clerk.rogue.private_pem.decode())
    resp = await _kids(client, forged)
    assert resp.status_code == 401
    assert "Signature" in resp.json()["detail"]


async def test_a_tampered_payload_is_rejected(client, local_clerk):
    """A real signed token whose claims were edited to `role: admin` afterwards."""
    resp = await _kids(client, local_clerk.tampered(sub="user_local_a"))
    assert resp.status_code == 401
    assert "Signature" in resp.json()["detail"]


async def test_a_token_with_no_kid_is_rejected(client, local_clerk):
    """No `kid` means no way to pick a key — and "try them all" is a bug."""
    resp = await _kids(client, local_clerk.mint(kid=None))
    assert resp.status_code == 401
    assert "kid" in resp.json()["detail"]


async def test_an_unknown_kid_is_rejected(client, local_clerk):
    resp = await _kids(client, local_clerk.mint(kid="not-a-published-key"))
    assert resp.status_code == 401
    assert "unknown signing key" in resp.json()["detail"]


async def test_a_token_with_no_subject_is_rejected(client, local_clerk):
    """No `sub` means no identity to own anything."""
    resp = await _kids(client, local_clerk.mint(extra_claims={"sub": ""}))
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "bearer",
        "Token abc.def.ghi",
        "Bearer notajwt",
        "Bearer a.b",
        "Bearer ...",
        "Bearer " + "A" * 5000,
        "Bearer \x00\x01\x02",
        "Bearer eyJhbGciOiJSUzI1NiJ9..",
    ],
)
async def test_no_malformed_authorization_header_produces_a_500(client, local_clerk, header):
    """The property behind every test above: malformed input is 401, not a crash.

    A 500 here would mean an unhandled exception somewhere in parsing — which is
    both a stack trace on the wire and an invitation to keep poking. The
    assertion is on the *class* of status, so a future refactor that starts
    raising from a new place fails here rather than in production.
    """
    resp = await client.get("/api/kids", headers={"Authorization": header} if header else {})
    assert resp.status_code == 401, f"{header!r} → {resp.status_code}: {resp.text}"


async def test_no_token_at_all_is_401_with_a_challenge(client, local_clerk):
    resp = await _kids(client, None)
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


# --- roles (§13) -------------------------------------------------------------
async def test_an_admin_token_reaches_the_admin_route(client, local_clerk):
    """§9 marks `POST /api/validate` admin-only."""
    token = local_clerk.mint(sub="user_local_admin", role="admin")
    resp = await client.post(
        "/api/validate",
        json={"text": "Sam sat.", "stage_id": 1},
        headers=local_clerk.bearer(token),
    )
    assert resp.status_code == 200, resp.text


async def test_a_parent_token_does_not_reach_the_admin_route(client, local_clerk):
    """Role escalation, the direct attempt."""
    token = local_clerk.mint(sub="user_local_a", role="parent")
    resp = await client.post(
        "/api/validate",
        json={"text": "Sam sat.", "stage_id": 1},
        headers=local_clerk.bearer(token),
    )
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"]


async def test_an_unauthenticated_caller_does_not_reach_the_admin_route(client, local_clerk):
    resp = await client.post("/api/validate", json={"text": "Sam sat.", "stage_id": 1})
    assert resp.status_code == 401


async def test_a_role_the_database_holds_cannot_outlive_the_token(client, local_clerk, session):
    """Revoking admin in Clerk takes effect on the next request, not the next login.

    The `users.role` column is a cache of the token's claim, so a demoted admin
    whose row still says `admin` must be a parent the moment their token says so.
    """
    admin = local_clerk.mint(sub="user_local_demote", role="admin")
    assert (
        await client.post(
            "/api/validate",
            json={"text": "Sam sat.", "stage_id": 1},
            headers=local_clerk.bearer(admin),
        )
    ).status_code == 200

    demoted = local_clerk.mint(sub="user_local_demote", role="parent")
    assert (
        await client.post(
            "/api/validate",
            json={"text": "Sam sat.", "stage_id": 1},
            headers=local_clerk.bearer(demoted),
        )
    ).status_code == 403

    role = (
        await session.execute(
            text("SELECT role FROM users WHERE clerk_id = 'user_local_demote'")
        )
    ).scalar_one()
    assert role == "parent", "the column follows the token, not the other way round"


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        ({"public_metadata": {"role": "admin"}}, "admin"),
        ({"public_metadata": {"role": "parent"}}, "parent"),
        ({"public_metadata": {"role": "ADMIN"}}, "admin"),
        ({"public_metadata": {"role": " admin "}}, "admin"),
        ({"user_public_metadata": {"role": "admin"}}, "admin"),
        ({"metadata": {"role": "admin"}}, "admin"),
        ({"role": "admin"}, "admin"),
        # Absent is the normal case for a parent who just signed up.
        ({}, "parent"),
        ({"public_metadata": {}}, "parent"),
        ({"public_metadata": None}, "parent"),
        # Anything unrecognised falls to LEAST privilege, never to admin.
        ({"public_metadata": {"role": "superuser"}}, "parent"),
        ({"public_metadata": {"role": "admin!"}}, "parent"),
        ({"public_metadata": {"role": ""}}, "parent"),
        ({"public_metadata": {"role": True}}, "parent"),
        ({"public_metadata": {"role": ["admin"]}}, "parent"),
        ({"public_metadata": "admin"}, "parent"),
        # A junk value in the primary location is not overridden by a friendlier
        # one further down the list: first path that names a role wins.
        ({"public_metadata": {"role": "wizard"}, "role": "admin"}, "parent"),
    ],
)
def test_role_resolution_never_escalates(claims, expected):
    assert role_from_claims(claims) == expected


# --- the JWKS cache ----------------------------------------------------------
async def test_the_jwks_is_fetched_once_across_many_requests(client, local_clerk):
    """§9's "never fetch per request", measured at the JWKS server itself.

    Counting hits on the stub rather than trusting the cache's own counter is
    the point: a cache that reports hits while still making calls would pass the
    self-reported version of this test.
    """
    token = local_clerk.mint(sub="user_local_a")
    for _ in range(25):
        assert (await _kids(client, token)).status_code == 200
    assert len(local_clerk.hits) == 1, f"{len(local_clerk.hits)} JWKS fetches for 25 requests"
    assert auth_config().jwks.stats()["cache_hits"] == 24


async def test_concurrent_cold_requests_collapse_into_one_fetch(client, local_clerk):
    """A cold start under load is where a TTL cache without a lock stampedes."""
    token = local_clerk.mint(sub="user_local_a")
    results = await asyncio.gather(*(_kids(client, token) for _ in range(20)))
    assert {r.status_code for r in results} == {200}
    assert len(local_clerk.hits) == 1


async def test_the_cache_refetches_after_its_ttl(client, local_clerk, monkeypatch):
    monkeypatch.setenv("CLERK_JWKS_TTL_S", "1")
    reset_auth_config()

    token = local_clerk.mint(sub="user_local_a")
    assert (await _kids(client, token)).status_code == 200
    assert len(local_clerk.hits) == 1

    # Real elapsed time: the cache reads the monotonic clock, and a fake one
    # would test the fake.
    time.sleep(1.1)
    assert (await _kids(client, token)).status_code == 200
    assert len(local_clerk.hits) == 2


async def test_an_unknown_kid_cannot_be_used_to_hammer_the_jwks_endpoint(client, local_clerk):
    """Key rotation must be supported; being an amplifier must not be.

    Fifty unauthenticated requests naming fifty `kid`s that do not exist produce
    **zero** extra fetches: the document was refreshed moments ago, so a key
    absent from it is absent, and refetching on demand would let any stranger
    point this service at Clerk and hold the button down. Rotation is still
    picked up — by the TTL, or by one refetch once the cooldown has elapsed,
    which is the next test.
    """
    assert (await _kids(client, local_clerk.mint(sub="user_local_a"))).status_code == 200
    baseline = len(local_clerk.hits)

    for i in range(50):
        resp = await _kids(client, local_clerk.mint(kid=f"rotated-{i}"))
        assert resp.status_code == 401
    assert len(local_clerk.hits) == baseline


async def test_the_cache_picks_up_a_rotated_key_after_the_cooldown(local_clerk):
    """The other half of the same rule, at the unit level."""
    cache = JwksCache(
        local_clerk.jwks_url, ttl_s=3600, timeout_s=5, rotation_cooldown_s=0.0
    )
    assert await cache.key_for(local_clerk.live.kid) is not None
    with pytest.raises(UnknownSigningKey):
        await cache.key_for("nope")
    assert cache.fetches == 2, "a fresh cache with an unknown kid refetches once"


async def test_a_jwks_outage_is_503_not_401(client, local_clerk, monkeypatch):
    """An unreachable identity provider is not a bad credential.

    401 would tell a legitimate parent their login is wrong. The distinction
    matters at 3am: 503 points the operator at Clerk, 401 points them at users.
    """
    monkeypatch.setenv("CLERK_JWKS_URL", "http://127.0.0.1:1/.well-known/jwks.json")
    monkeypatch.setenv("CLERK_ISSUER", local_clerk.origin)
    reset_auth_config()
    resp = await _kids(client, local_clerk.mint(sub="user_local_a"))
    assert resp.status_code == 503
    assert "JWKS" in resp.json()["detail"]


async def test_verification_is_impossible_without_a_jwks_url(client, local_clerk, monkeypatch):
    """Unconfigured must fail closed. A missing URL is not "skip verification".

    Blanked rather than deleted: `Settings` falls back to `.env`, so unsetting
    the variable in a process whose `.env` names a real Clerk instance would
    quietly test the configured path instead of the unconfigured one.
    """
    monkeypatch.setenv("CLERK_JWKS_URL", "")
    reset_auth_config()
    resp = await _kids(client, local_clerk.mint(sub="user_local_a"))
    assert resp.status_code == 503
    assert "CLERK_JWKS_URL" in resp.json()["detail"]


async def test_only_rs256_is_ever_offered_to_the_verifier(local_clerk):
    """Belt and braces: the library is pinned too, not just the header check.

    The header check is the first line. This asserts the second — that even if
    a token reached `jwt.decode`, the allowed-algorithm list is a single entry.
    """
    assert ALGORITHM == "RS256"
    token = local_clerk.mint(sub="user_local_a")
    claims = await verify_token(token)
    assert claims.sub == "user_local_a"
    assert claims.raw["iss"] == local_clerk.origin

    with pytest.raises(jwt.InvalidAlgorithmError):
        jwt.decode(
            token,
            key=local_clerk.live.public_pem.decode(),
            algorithms=["HS256"],
            options={"verify_aud": False},
        )


# --- §18 DEMO_BYPASS_AUTH ----------------------------------------------------
def test_deployment_signals_notice_a_deployed_environment(monkeypatch):
    assert deployment_signals("postgresql+psycopg://u:p@localhost:5433/db") == []

    monkeypatch.setenv("ENVIRONMENT", "production")
    assert any("ENVIRONMENT" in s for s in deployment_signals(""))
    monkeypatch.delenv("ENVIRONMENT")

    monkeypatch.setenv("DIGITALOCEAN_APP_ID", "abc-123")
    assert any("DIGITALOCEAN_APP_ID" in s for s in deployment_signals(""))
    monkeypatch.delenv("DIGITALOCEAN_APP_ID")

    remote = "postgresql+psycopg://u:p@db-postgresql-nyc3-1.b.ondigitalocean.com:25060/sf"
    assert any("non-local host" in s for s in deployment_signals(remote))

    assert any("live key" in s for s in deployment_signals("", "sk_live_deadbeef"))


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ENV", "production"),
        ("ENVIRONMENT", "prod"),
        ("APP_ENV", "staging"),
        ("NODE_ENV", "production"),
        ("DIGITALOCEAN_APP_ID", "1234"),
        ("KUBERNETES_SERVICE_HOST", "10.0.0.1"),
        ("FLY_APP_NAME", "storyforge"),
        ("APP_URL", "https://storyforge.ondigitalocean.app"),
    ],
)
def test_the_bypass_refuses_to_start_in_anything_that_looks_deployed(monkeypatch, name, value):
    """§18: "Never enabled in prod deploy" — enforced, not documented.

    A comment in `.env.example` is not a control. This is: the process refuses
    to run, by name, saying which signal it saw.
    """
    monkeypatch.setenv("DEMO_BYPASS_AUTH", "true")
    monkeypatch.setenv(name, value)
    reset_auth_config()
    with pytest.raises(RuntimeError) as caught:
        assert_demo_bypass_is_local()
    assert name in str(caught.value)
    monkeypatch.undo()
    reset_auth_config()


def test_the_bypass_is_permitted_on_a_laptop(monkeypatch):
    """The guard has to allow the thing it exists to allow."""
    monkeypatch.setenv("DEMO_BYPASS_AUTH", "true")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://storyforge:storyforge@localhost:5433/storyforge"
    )
    reset_auth_config()
    assert_demo_bypass_is_local()
    monkeypatch.undo()
    reset_auth_config()


async def test_the_bypass_yields_a_fixed_local_session(client, local_clerk, monkeypatch, caplog):
    """§18's fallback: no token, and the request still works — loudly."""
    monkeypatch.setenv("DEMO_BYPASS_AUTH", "true")
    monkeypatch.setenv("DEMO_BYPASS_ROLE", "parent")
    reset_auth_config()

    with caplog.at_level("WARNING", logger="storyforge.auth"):
        resp = await _kids(client, None)
    assert resp.status_code == 200
    assert any(DEMO_CLERK_ID in r.getMessage() for r in caplog.records), (
        "an auth bypass that does not say so is how one ships"
    )

    # And it is genuinely a parent: §9's admin route stays shut.
    denied = await client.post("/api/validate", json={"text": "Sam sat.", "stage_id": 1})
    assert denied.status_code == 403


async def test_with_the_bypass_off_a_token_is_mandatory(client, local_clerk):
    """The inverse, so the suite cannot be passing because of the bypass."""
    assert (await _kids(client, None)).status_code == 401
    assert (await _kids(client, local_clerk.mint(sub="user_local_a"))).status_code == 200


# --- against the REAL Clerk instance -----------------------------------------
# Everything above runs against a locally generated keypair, and that is not a
# compromise — it is the only way to test forgeries. A real Clerk instance will
# not mint an expired token, an `alg: none` token, or one signed by the wrong
# key, so the attack matrix is necessarily local. What a local stub CANNOT prove
# is that Clerk's actual JWKS document parses, that its `kid` shape selects a
# key, and that the cache behaves against a real remote endpoint over TLS. That
# is what these three add, and they skip cleanly when nobody has configured an
# instance, so the default `uv run pytest` needs neither network nor credentials.


def _real_jwks_url() -> str | None:
    """The configured Clerk JWKS URL, read past any fixture's monkeypatching."""
    from storyforge.config import Settings

    url = (Settings().clerk_jwks_url or "").strip()
    return url or None


@pytest.fixture
def real_clerk():
    url = _real_jwks_url()
    if url is None:
        pytest.skip("CLERK_JWKS_URL is not configured — local keypair covers the rest")
    reset_auth_config()
    try:
        yield url
    finally:
        reset_auth_config()


async def _fetch_real(url: str) -> JwksCache:
    """Fetch the live document, skipping only on a TRANSPORT failure.

    The split is deliberate. Being offline is an environment fact and must not
    redden the suite. A document that arrives and does not parse is a defect in
    this code or a breaking change at Clerk, and must.
    """
    cache = JwksCache(url, ttl_s=600, timeout_s=10)
    try:
        await cache.key_for("probe-for-a-key-that-does-not-exist")
    except UnknownSigningKey:
        pass  # expected: the document loaded, this kid is simply not in it
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Clerk JWKS unreachable ({type(exc).__name__}) — network-dependent test")
    return cache


async def test_the_real_clerk_jwks_parses_into_usable_signing_keys(real_clerk):
    """Clerk's actual response shape, through the real fetch and parse path.

    Live-observed at the time of writing: one key, `kty=RSA`, `use=sig`,
    `alg=RS256`, `kid=ins_…`. `_parse_jwks` filters on exactly those fields, so
    a mismatch between what it expects and what Clerk sends would have shown up
    as "no usable RSA signing keys" — silently, at the first login.
    """
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

    cache = await _fetch_real(real_clerk)
    keys = cache.stats()["keys"]
    assert keys, "Clerk's JWKS yielded no usable RSA signing key"
    for kid in keys:
        assert isinstance(await cache.key_for(kid), RSAPublicKey)


async def test_kid_selection_picks_the_right_key_from_the_real_key_set(real_clerk):
    """`kid` → key, against the ids Clerk actually issues (`ins_…`).

    The expected `kid` is read from the live document rather than pinned as a
    literal. Pinning it would make this test fail the day Clerk rotates the
    instance key — which is a thing `JwksCache` is specifically built to
    survive, so a test that broke on it would be testing the wrong property.
    """
    import httpx as _httpx

    cache = await _fetch_real(real_clerk)
    document = (await _httpx.AsyncClient(timeout=10).get(real_clerk)).json()
    published = [k["kid"] for k in document["keys"] if k.get("kty") == "RSA"]

    assert published, "Clerk published no RSA key"
    assert sorted(cache.stats()["keys"]) == sorted(published), (
        "the cache selected a different key set than Clerk published"
    )
    for kid in published:
        assert await cache.key_for(kid) is not None

    with pytest.raises(UnknownSigningKey):
        await cache.key_for("ins_not_a_real_key")


async def test_the_real_jwks_is_fetched_once_and_then_cached(real_clerk):
    """§9's "never fetch per request", against a remote endpoint over TLS."""
    cache = JwksCache(real_clerk, ttl_s=600, timeout_s=10)
    try:
        kid = None
        for _ in range(25):
            if kid is None:
                await cache.key_for("bootstrap")
    except UnknownSigningKey:
        pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Clerk JWKS unreachable ({type(exc).__name__})")

    kid = cache.stats()["keys"][0]
    for _ in range(25):
        assert await cache.key_for(kid) is not None
    assert cache.fetches == 1, f"{cache.fetches} fetches for 26 key lookups"
    assert cache.stats()["cache_hits"] == 25


async def test_the_issuer_is_derived_from_the_real_jwks_url(real_clerk):
    """A real deployment sets CLERK_JWKS_URL and nothing else (§9's iss check).

    If the derivation did not handle Clerk's actual URL shape, `issuer` would be
    None and issuer validation would be silently skipped in production while
    every local test still passed.
    """
    config = auth_config()
    assert config.jwks_url == real_clerk
    assert config.issuer, "no issuer derived — issuer validation would be OFF"
    assert real_clerk.startswith(config.issuer + "/")
    assert config.issuer.startswith("https://")
    assert config.issuer.endswith(".clerk.accounts.dev") or config.issuer


async def test_the_real_config_rejects_a_token_this_process_minted(
    client, real_clerk, _keypairs, monkeypatch
):
    """The local keypair is not a back door into the real configuration.

    With Clerk configured, a token signed by the key this test suite generates
    is refused — its `kid` is not in Clerk's document. This is the join between
    the two halves of the suite: the forgeries are exercised locally, and here
    the real configuration is shown not to accept any of them.
    """
    monkeypatch.setenv("DEMO_BYPASS_AUTH", "false")
    reset_auth_config()
    if _real_jwks_url() is None:  # pragma: no cover — guarded by the fixture
        pytest.skip("unconfigured")

    live, _rogue = _keypairs
    token = jwt.encode(
        {"sub": "user_local_forger", "iat": int(time.time()), "exp": int(time.time()) + 300},
        live.private_pem.decode(),
        algorithm="RS256",
        headers={"kid": live.kid},
    )
    resp = await _kids(client, token)
    if resp.status_code == 503:  # offline
        pytest.skip("Clerk JWKS unreachable")
    assert resp.status_code == 401

    # And with the bypass off, no token at all is refused too — the §18
    # fallback must not be silently carrying requests in a configured process.
    assert (await _kids(client, None)).status_code == 401


# --- a Clerk outage (DEVIATIONS 7.34) ----------------------------------------
# The audit's C8: verification fails CLOSED, which is correct, but with no stale
# tolerance a Clerk blip lasting longer than the 600 s TTL logged EVERY user out —
# including a child mid-story — while the cache was still holding keys that were
# valid and would have verified the token correctly.
#
# The tests below are the shape of that fix and, more importantly, the shape of
# its limits. Two of them exist specifically to prove it is not a fail-open: an
# unknown `kid` is still refused during the outage, and an expired token is still
# expired while stale keys are being served. If either of those ever goes green in
# the other direction, the fallback has become a bypass.
#
# The outage is simulated by repointing `cache.url` at a closed port after a
# successful fetch. That is a public attribute and the failure it produces
# (ConnectError) is the same one the audit drove with a real stub server.
DEAD_JWKS_URL = "http://127.0.0.1:1/.well-known/jwks.json"


async def test_a_jwks_outage_serves_the_stale_key_rather_than_locking_everyone_out(local_clerk):
    from storyforge.auth import JwksUnavailable

    cache = JwksCache(
        local_clerk.jwks_url,
        ttl_s=0.05,
        timeout_s=1,
        stale_grace_s=3600,
        failure_backoff_s=0,
    )
    assert await cache.key_for(local_clerk.live.kid) is not None
    assert cache.fetches == 1
    assert cache.stale_serves == 0

    time.sleep(0.1)  # real elapsed time: the cache reads the monotonic clock
    cache.url = DEAD_JWKS_URL

    assert await cache.key_for(local_clerk.live.kid) is not None
    assert cache.stale_serves == 1, "the still-valid cached key was not served"

    # NOT a fail-open. A stale document cannot contain a key it never contained,
    # so an unknown kid during an outage is the same 503 it always was — never a
    # pass, and never a 401 that blames the user for Clerk being down.
    with pytest.raises(JwksUnavailable):
        await cache.key_for("rotated-tomorrow")


async def test_the_stale_window_is_bounded(local_clerk):
    """Past the grace period the cache refuses again.

    "Clerk is unreachable" must not decay into "this API trusts a key set from
    last week". `stale_grace_s=0` is the configuration for an operator who would
    rather take the outage, and it is exercised here as the boundary case.
    """
    from storyforge.auth import JwksUnavailable

    cache = JwksCache(
        local_clerk.jwks_url, ttl_s=0.05, timeout_s=1, stale_grace_s=0.05, failure_backoff_s=0
    )
    assert await cache.key_for(local_clerk.live.kid) is not None
    time.sleep(0.25)  # past ttl + grace
    cache.url = DEAD_JWKS_URL

    with pytest.raises(JwksUnavailable):
        await cache.key_for(local_clerk.live.kid)
    assert cache.stale_serves == 0


async def test_serving_stale_does_not_retry_the_dead_endpoint_on_every_request(local_clerk):
    """Otherwise an outage becomes a queue.

    Every request would re-attempt a fetch, each holding the cache lock for the
    full connect timeout. Ten lookups, one failed attempt.
    """
    cache = JwksCache(
        local_clerk.jwks_url, ttl_s=0.05, timeout_s=1, stale_grace_s=3600, failure_backoff_s=60
    )
    assert await cache.key_for(local_clerk.live.kid) is not None
    time.sleep(0.1)
    cache.url = DEAD_JWKS_URL

    for _ in range(10):
        assert await cache.key_for(local_clerk.live.kid) is not None
    assert cache.fetches == 2, f"{cache.fetches} fetches — the backoff is not holding"
    assert cache.stale_serves == 10


async def test_a_clerk_outage_no_longer_401s_a_signed_in_parent_end_to_end(
    client, local_clerk, monkeypatch
):
    """The same property through the real route, which is where it matters.

    Three statuses in one test on purpose, because the fix is only correct if all
    three hold at once: a valid token still works, an EXPIRED token is still
    refused, and a token naming a key nobody ever published is still a 503.
    """
    monkeypatch.setenv("CLERK_JWKS_TTL_S", "1")
    reset_auth_config()

    token = local_clerk.mint(sub="user_local_a")
    assert (await _kids(client, token)).status_code == 200

    time.sleep(1.1)  # the TTL lapses
    auth_config().jwks.url = DEAD_JWKS_URL  # and Clerk goes away

    assert (await _kids(client, token)).status_code == 200
    assert auth_config().jwks.stats()["stale_serves"] >= 1

    # Availability bought nothing that verification used to refuse.
    assert (await _kids(client, local_clerk.mint(expires_in=-60))).status_code == 401
    assert (await _kids(client, local_clerk.mint(kid="rotated-tomorrow"))).status_code == 503


# --- §18: the signals the audit proved were missing (DEVIATIONS 7.36) ---------
@pytest.mark.parametrize(
    ("name", "value"),
    [
        # Environment values the exact-set test missed. `prod-us` is a real
        # spelling and it booted an unauthenticated API.
        ("ENVIRONMENT", "prod-us"),
        ("ENVIRONMENT", "production-eu"),
        ("APP_ENV", "staging2"),
        ("DEPLOY_ENV", "live-1"),
        # Platform variables. Each of these was measured BOOTING before the fix.
        ("K_SERVICE", "api"),
        ("K_REVISION", "api-00001"),
        ("CONTAINER_APP_NAME", "storyforge"),
        ("WEBSITE_SITE_NAME", "storyforge"),
        ("AWS_LAMBDA_FUNCTION_NAME", "storyforge"),
        ("LAMBDA_TASK_ROOT", "/var/task"),
        ("HEROKU_APP_NAME", "storyforge"),
        ("NOMAD_ALLOC_ID", "8f2c"),
        ("OPENSHIFT_BUILD_NAME", "storyforge-1"),
        ("CF_PAGES", "1"),
        # "A platform is telling this process where to listen."
        ("PORT", "8080"),
    ],
)
def test_the_bypass_also_refuses_the_signals_the_audit_found_missing(monkeypatch, name, value):
    monkeypatch.setenv("DEMO_BYPASS_AUTH", "true")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://storyforge:storyforge@localhost:5433/storyforge"
    )
    monkeypatch.setenv(name, value)
    reset_auth_config()
    with pytest.raises(RuntimeError) as caught:
        assert_demo_bypass_is_local()
    assert name in str(caught.value), "the refusal must name the signal it saw"
    monkeypatch.undo()
    reset_auth_config()


@pytest.mark.parametrize(
    ("label", "url"),
    [
        # THE one that mattered: `db` is the service name in this repo's own
        # docker-compose.yml, so `docker compose up` on a public VPS with the
        # bypass on used to serve a fully unauthenticated API.
        ("this repo's compose service name", "postgresql+psycopg://u:p@db:5432/storyforge"),
        ("a generic container alias", "postgresql+psycopg://u:p@postgres:5432/storyforge"),
        # A unix socket: the hostname parses to nothing, which used to be on the
        # LOCAL list. `?host=/cloudsql/…` is the shape that matters.
        ("a unix socket", "postgresql+psycopg://u:p@/storyforge?host=/var/run/postgresql"),
        ("a Cloud SQL socket", "postgresql+psycopg://u:p@/sf?host=/cloudsql/proj:region:inst"),
    ],
)
def test_a_container_or_socket_database_url_is_a_deployment_signal(label, url):
    signals = deployment_signals(url)
    assert signals, f"{label} produced no signal: {url}"


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://storyforge:storyforge@localhost:5433/storyforge",
        "postgresql+psycopg://u:p@127.0.0.1:5433/sf",
        "postgresql+psycopg://u:p@host.docker.internal:5433/sf",
    ],
)
def test_a_laptop_database_url_is_still_not_a_signal(url):
    """The guard has to keep allowing the thing it exists to allow.

    `make dev` runs the API on the host against `localhost:5433` — which is
    exactly why docker-compose publishes `5433:5432` — so widening the DB
    hostname rule must not touch it.
    """
    assert deployment_signals(url) == []


@pytest.mark.parametrize(
    ("value", "deployed"),
    [
        ("production", True),
        ("prod", True),
        ("prod-us", True),
        ("production-eu", True),
        ("staging", True),
        ("stage", True),
        ("staging2", True),
        ("live", True),
        ("live-1", True),
        ("preview", True),
        ("preview-42", True),
        # Everything a laptop actually says. A false positive here costs a
        # developer their demo, so the prefixes are checked in both directions.
        ("development", False),
        ("dev", False),
        ("local", False),
        ("test", False),
        ("ci", False),
        ("sandbox", False),
        ("", False),
        ("   ", False),
    ],
)
def test_the_environment_value_prefixes_cut_where_they_are_meant_to(value, deployed):
    from storyforge.auth import is_deployed_env_value

    assert is_deployed_env_value(value) is deployed


# --- §18: the two defeats AUDIT-PHASE7 F11 measured on the real image ---------
# Defeat 1 was `docker run -e DEMO_BYPASS_AUTH=true -e PORT= …`: blanking the one
# variable the image supplies to itself (the Dockerfile's `ENV PORT=8080`) removed
# the only signal and a fully unauthenticated API stayed up. The guard tested the
# VALUE; the fact that matters is the PRESENCE (DEVIATIONS 8.64).
@pytest.mark.parametrize(
    "name",
    ["PORT", "K_SERVICE", "DIGITALOCEAN_APP_ID", "AWS_LAMBDA_FUNCTION_NAME"],
)
def test_a_platform_variable_that_is_present_but_BLANK_is_still_a_signal(monkeypatch, name):
    """`-e PORT=` is not "no platform variable". It is someone emptying one.

    No platform sets one of these to the empty string and no laptop has a reason
    to, so blank is if anything a stronger signal than set.
    """
    monkeypatch.setenv("DEMO_BYPASS_AUTH", "true")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://storyforge:storyforge@localhost:5433/storyforge"
    )
    monkeypatch.setenv(name, "")
    reset_auth_config()
    signals = deployment_signals("postgresql+psycopg://u:p@localhost:5433/db")
    assert any(name in s for s in signals), f"{name}= produced no signal: {signals}"
    with pytest.raises(RuntimeError) as caught:
        assert_demo_bypass_is_local()
    assert name in str(caught.value), "the refusal must name the variable it saw"
    monkeypatch.undo()
    reset_auth_config()


@pytest.mark.parametrize(
    ("value", "deployed"),
    [
        # F11 defeats 2 and 3: both BOOTED bypassed, against a code comment that
        # claimed the prefix list had closed this family after `prod-us`.
        ("prd", True),
        ("pre-production", True),
        # The same family, spelled the other ways teams spell it.
        ("preprod", True),
        ("eu-stg", True),
        ("k8s-prod", True),
        ("uat", True),
        ("canary", True),
        ("release-candidate", False),  # not a deployment word on its own
        # And the false-positive direction, which is the reason this is a
        # parametrised pair and not a one-way sweep: a guard that refuses a
        # laptop gets disabled, which is worse than the gap.
        ("dev", False),
        ("development", False),
        ("local", False),
        ("localhost", False),
        ("test", False),
        ("testing", False),
        ("ci", False),
        ("sandbox", False),
        ("demo", False),
        ("laptop", False),
        ("", False),
        ("   ", False),
    ],
)
def test_the_deployed_environment_markers_catch_prd_and_pre_production(value, deployed):
    from storyforge.auth import is_deployed_env_value

    assert is_deployed_env_value(value) is deployed, value


def test_a_container_is_a_deployment_signal_and_no_env_flag_can_blank_it(monkeypatch, tmp_path):
    """§18 says "laptop-only". A container is not a laptop.

    This is the first signal in the guard that lives on the filesystem rather
    than in the environment, which is the whole point: `docker run` has no flag
    that removes `/.dockerenv` (DEVIATIONS 8.66).
    """
    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    monkeypatch.setattr("storyforge.auth.CONTAINER_MARKER_FILES", (str(marker),))
    monkeypatch.setenv("DEMO_BYPASS_AUTH", "true")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://storyforge:storyforge@localhost:5433/storyforge"
    )
    reset_auth_config()

    signals = deployment_signals("postgresql+psycopg://u:p@localhost:5433/db")
    assert any("container" in s for s in signals), signals
    with pytest.raises(RuntimeError) as caught:
        assert_demo_bypass_is_local()
    assert "container" in str(caught.value)
    monkeypatch.undo()
    reset_auth_config()


def test_a_container_cgroup_marker_is_enough_on_its_own(monkeypatch, tmp_path):
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("11:name=systemd:/kubepods/besteffort/pod1234/abcdef\n")
    monkeypatch.setattr("storyforge.auth.CONTAINER_MARKER_FILES", ())
    monkeypatch.setattr("storyforge.auth.CONTAINER_CGROUP_PATH", str(cgroup))
    assert any("container" in s for s in deployment_signals(""))


def test_the_container_override_cancels_the_container_signal_and_nothing_else(
    monkeypatch, tmp_path
):
    """The one documented way past it, and it is not a master key.

    A dev container and a containerised CI runner are real workflows, and a guard
    that fails them at import gets deleted rather than configured. The escape is a
    positive assertion spelled out in full — and every OTHER signal still refuses
    while it is set, which is what stops it being a bypass for the bypass.
    """
    from storyforge.auth import CONTAINER_OVERRIDE_VALUE, CONTAINER_OVERRIDE_VAR

    marker = tmp_path / ".dockerenv"
    marker.write_text("")
    monkeypatch.setattr("storyforge.auth.CONTAINER_MARKER_FILES", (str(marker),))
    monkeypatch.setattr("storyforge.auth.CONTAINER_CGROUP_PATH", str(tmp_path / "absent"))

    monkeypatch.setenv(CONTAINER_OVERRIDE_VAR, CONTAINER_OVERRIDE_VALUE)
    assert deployment_signals("postgresql+psycopg://u:p@localhost:5433/db") == []

    # A near miss is not the assertion. The value has to be typed.
    monkeypatch.setenv(CONTAINER_OVERRIDE_VAR, "true")
    assert any("container" in s for s in deployment_signals(""))

    # And it cancels ONE signal, not the guard.
    monkeypatch.setenv(CONTAINER_OVERRIDE_VAR, CONTAINER_OVERRIDE_VALUE)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert any("ENVIRONMENT" in s for s in deployment_signals(""))


def test_the_bypass_is_still_permitted_on_this_developer_machine(monkeypatch):
    """The whole suite runs under the bypass, so this is the canary for it.

    If widening the signals above ever makes a laptop look deployed, this fails
    here rather than in `make dev` on somebody else's afternoon.
    """
    monkeypatch.setenv("DEMO_BYPASS_AUTH", "true")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://storyforge:storyforge@localhost:5433/storyforge"
    )
    for leaked in ("PORT", "K_SERVICE", "DYNO"):
        monkeypatch.delenv(leaked, raising=False)
    reset_auth_config()
    assert_demo_bypass_is_local()
    monkeypatch.undo()
    reset_auth_config()
