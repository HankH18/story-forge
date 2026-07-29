"""Clerk JWT verification, roles and owner scoping (§9, §13).

Everything under `/api/*` except `/api/health` requires a Clerk session token
(§9). This module is the single place that decides who the caller is and what
they may reach, so the properties below hold for every route by construction
rather than by each handler remembering.

**Verification is standard RS256 over JWKS, and nothing else.** The signing
algorithm is pinned to `RS256` *before* the key is fetched, so the two classic
JWT forgeries — `alg: none` and `alg: HS256` signed with the public key everyone
can download — are refused at the header, not deep inside a library. `kid` is
mandatory: a token that does not name its key cannot be matched against one, and
"try every key" is how a rotated-out key stays valid forever.

**The JWKS is cached with a TTL and fetched under a lock**, so N concurrent
requests cause at most one HTTP call and the steady state is zero. An unknown
`kid` is the one thing that can force an out-of-band refetch (key rotation is
real), so that path is rate-limited by `_ROTATION_COOLDOWN_S` — otherwise a
stranger with a random `kid` is a free amplifier against Clerk's endpoint.

**Roles come from the token, never from the database.** `public_metadata.role`
is set in the Clerk dashboard (§13) and is re-read on every request, so revoking
admin takes effect on the next call rather than at the next login. A role string
this code does not recognise resolves to `parent`; an unknown value must never
be able to escalate.

**Ownership is enforced here, not in the handlers.** `owned_kid` and
`owned_reading` are the only ways a route reaches a kid or a reading, and both
filter on `kids.parent_id = <this caller>`. A row belonging to another parent is
**404, not 403** — a 403 would confirm that the id exists, which is exactly the
enumeration oracle §13's posture is trying not to hand out. That distinction is
the Phase 6 Definition of Done: two parents cannot see each other's kids, on
every route that takes a kid or a reading id.

**`DEMO_BYPASS_AUTH` (§18) cannot follow the code to production.** It mints a
fixed local parent so a Clerk outage cannot take the demo down. `assert_demo_bypass_is_local`
runs at import and again on every bypassed request, and refuses to start if
anything at all suggests a deployed environment — an env-name variable, a
platform-injected variable, a non-local database host, or a live Clerk key. When
it is active it says so, loudly, on every single request.

Configuration note: §9's issuer and audience have no home in `config.Settings`
yet (its Clerk fields are `CLERK_JWKS_URL` and `CLERK_SECRET_KEY`), so the extra
knobs live in `AuthSettings` below, reading the same `.env` through the same
pydantic-settings machinery. `CLERK_ISSUER` defaults to the origin of the JWKS
URL, which is exactly how Clerk publishes it, so a real deployment needs to set
`CLERK_JWKS_URL` and nothing else. Swapping the local test JWKS for Clerk's is
configuration, not code.
"""

from __future__ import annotations

import json
import logging
import os
import time
from asyncio import Lock
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.config import get_settings
from storyforge.db import get_session

log = logging.getLogger("storyforge.auth")

#: The only signature algorithm this service will ever accept. Clerk signs with
#: RS256; every other value in a token header is an attack or a misconfiguration,
#: and there is no honest reason to widen this list.
ALGORITHM: Final = "RS256"

Role = Literal["parent", "admin"]
#: §13: two roles, set in the Clerk dashboard. Anything else is not a role.
ROLES: Final[frozenset[str]] = frozenset({"parent", "admin"})
DEFAULT_ROLE: Final[Role] = "parent"

#: Where `public_metadata.role` (§3, §13) can legitimately appear. Clerk's JWT
#: templates let the operator name the claim, and the three spellings below are
#: the ones its own docs produce. The bare top-level `role` is last and is still
#: safe: the token is RS256-signed by Clerk, so only Clerk can put it there.
#: Order matters — the first path that yields a string wins, so a nonsense value
#: in `public_metadata` cannot be overridden by a friendlier one further down.
ROLE_CLAIM_PATHS: Final[tuple[tuple[str, ...], ...]] = (
    ("public_metadata", "role"),
    ("user_public_metadata", "role"),
    ("metadata", "role"),
    ("role",),
)

#: Clerk templates spell the address differently depending on which shortcut the
#: operator pasted. `users.email` is a convenience for the parent dashboard, not
#: an identity — `clerk_id` is the identity.
EMAIL_CLAIM_KEYS: Final[tuple[str, ...]] = (
    "email",
    "primary_email_address",
    "email_address",
)
MAX_EMAIL_CHARS: Final = 320

#: The clerk_id of the §18 fallback session. It is a real `users` row, so kids
#: created during a demo are owned exactly like any other kid and the ownership
#: tests are not special-cased.
DEMO_CLERK_ID: Final = "demo_local_parent"

_ROTATION_COOLDOWN_S: Final = 60.0


# --- errors ------------------------------------------------------------------
def _unauthorized(detail: str) -> HTTPException:
    """401 with the challenge header. Every rejection below is one of these.

    The detail names the reason. That is deliberate: these are all *malformed
    or unacceptable tokens*, not "wrong password" — telling a client that its
    clock is off or its key rotated is diagnosis, not disclosure. Nothing here
    reveals whether a user, kid or reading exists.
    """
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


class JwksUnavailable(RuntimeError):
    """The JWKS endpoint could not be reached or did not parse."""


class UnknownSigningKey(LookupError):
    """The token names a `kid` the JWKS does not contain, even after a refresh."""


# --- settings ----------------------------------------------------------------
class AuthSettings(BaseSettings):
    """Clerk knobs beyond the two `config.Settings` already carries.

    Same `.env`, same mechanism — a second model rather than new fields on
    `Settings` only because `config.py` is owned elsewhere this phase. Folding
    these in later is a copy-paste of four lines and no behaviour change.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    #: Defaults to the origin of `CLERK_JWKS_URL`, which is where Clerk serves it.
    clerk_issuer: str | None = None
    #: Clerk's default session token carries no `aud`. Set this only if the JWT
    #: template adds one — when set it is REQUIRED, and matched strictly.
    clerk_audience: str | None = None
    #: §9 never wants a fetch per request. Ten minutes is Clerk's own guidance.
    clerk_jwks_ttl_s: int = 600
    clerk_jwks_timeout_s: float = 5.0
    #: Clock skew tolerance in seconds. Zero by default: a token that is not yet
    #: valid is not valid, and a fudge factor here silently widens `exp` too.
    clerk_clock_skew_s: int = 0
    #: The role §18's bypass session carries. `parent` per §18's own wording.
    #:
    #: It is a knob because §17 and §18 disagree: the demo *opens* on the admin
    #: playground and the allowed-words browser, both `(admin)` in §9, and a
    #: "fixed parent session" cannot reach either — so the outage fallback would
    #: not survive the outage. `DEMO_BYPASS_ROLE=admin` is the escape hatch, and
    #: it grants nothing the bypass had not already granted: this flag is a total
    #: auth bypass either way, which is why it refuses to run anywhere deployed.
    demo_bypass_role: str = DEFAULT_ROLE


# --- JWKS cache --------------------------------------------------------------
class JwksCache:
    """One JWKS document, fetched rarely and shared by every request.

    The lock is what makes "never fetch per request" true under load: it is held
    across the HTTP call, so a hundred concurrent cold requests produce one
    fetch and ninety-nine waits, not a hundred fetches.

    `fetches` is a real counter rather than a log line, so the caching claim is
    testable — `tests/test_auth.py` asserts it stays at 1 across N verifications,
    and the live transcript reads it back from the stub JWKS server's own access
    log. A cache nobody has measured is a cache nobody knows is working.
    """

    def __init__(
        self,
        url: str,
        *,
        ttl_s: float,
        timeout_s: float,
        rotation_cooldown_s: float = _ROTATION_COOLDOWN_S,
    ) -> None:
        self.url = url
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._rotation_cooldown_s = rotation_cooldown_s
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._last_rotation_fetch = float("-inf")
        self._lock = Lock()
        #: HTTP requests actually made to the JWKS endpoint.
        self.fetches = 0
        #: Keys served without touching the network.
        self.cache_hits = 0

    def stats(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "fetches": self.fetches,
            "cache_hits": self.cache_hits,
            "keys": sorted(self._keys),
            "ttl_s": self._ttl_s,
            "age_s": (
                None if self._fetched_at is None else round(time.monotonic() - self._fetched_at, 3)
            ),
        }

    def clear(self) -> None:
        """Forget everything, counters included. Tests and key-rotation drills."""
        self._keys = {}
        self._fetched_at = None
        self._last_rotation_fetch = float("-inf")
        self.fetches = 0
        self.cache_hits = 0

    async def key_for(self, kid: str) -> Any:
        """The public key for `kid`, from cache when possible.

        Three outcomes, in order of how often they happen: a cache hit (no IO),
        a TTL-expired refetch, and — rarely — a fetch triggered by a `kid` that
        is not in a still-fresh document, which is what key rotation looks like
        from here. The last one is deliberately rate-limited: without the
        cooldown, an unauthenticated stranger sending random `kid`s would turn
        this service into a load generator pointed at Clerk.
        """
        async with self._lock:
            now = time.monotonic()
            fresh = self._fetched_at is not None and now - self._fetched_at < self._ttl_s

            if fresh and kid in self._keys:
                self.cache_hits += 1
                return self._keys[kid]

            if not fresh:
                await self._fetch(now)
                if kid in self._keys:
                    return self._keys[kid]

            if now - self._last_rotation_fetch >= self._rotation_cooldown_s:
                log.info(
                    "jwks: kid %r absent from a fresh document, refetching (rotation?)", kid
                )
                await self._fetch(now)
                if kid in self._keys:
                    return self._keys[kid]

            raise UnknownSigningKey(kid)

    async def _fetch(self, now: float) -> None:
        """Replace the cached document. Callers hold the lock."""
        self.fetches += 1
        self._last_rotation_fetch = now
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(self.url)
                response.raise_for_status()
                document = response.json()
        except Exception as exc:  # noqa: BLE001 — every transport failure is one thing here
            raise JwksUnavailable(f"{type(exc).__name__}: {exc}") from exc

        self._keys = _parse_jwks(document)
        self._fetched_at = now
        log.info(
            "jwks: fetch #%d from %s → %d key(s) %s",
            self.fetches,
            self.url,
            len(self._keys),
            sorted(self._keys),
        )


def _parse_jwks(document: Any) -> dict[str, Any]:
    """Public keys by `kid`, ignoring anything this service cannot use.

    Only RSA keys usable for signature verification are kept, and a key with no
    `kid` is dropped rather than given a synthetic one — an unnamed key can
    never be selected by a token, so keeping it could only ever widen what
    verifies.
    """
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise JwksUnavailable("JWKS document has no 'keys' array")

    keys: dict[str, Any] = {}
    for entry in document["keys"]:
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str) or not kid:
            continue
        if entry.get("kty") != "RSA":
            continue
        if entry.get("use") not in (None, "sig"):
            continue
        if entry.get("alg") not in (None, ALGORITHM):
            continue
        try:
            keys[kid] = RSAAlgorithm.from_jwk(json.dumps(entry))
        except Exception as exc:  # noqa: BLE001 — a malformed key is a skipped key
            log.warning("jwks: skipping unusable key %r: %s", kid, exc)

    if not keys:
        raise JwksUnavailable("JWKS document contains no usable RSA signing keys")
    return keys


# --- resolved configuration --------------------------------------------------
@dataclass(frozen=True)
class AuthConfig:
    jwks_url: str | None
    issuer: str | None
    audience: str | None
    clock_skew_s: int
    demo_bypass: bool
    demo_role: Role
    jwks: JwksCache | None


def _issuer_from_jwks_url(url: str) -> str | None:
    """`https://x.clerk.accounts.dev/.well-known/jwks.json` → `https://x.clerk.accounts.dev`.

    Clerk's issuer is the origin of its Frontend API, and its JWKS lives at the
    well-known path underneath. Deriving it means a deployment configures one
    URL and still gets issuer checking; a JWKS URL of some other shape yields
    None, and the operator sets `CLERK_ISSUER` explicitly.
    """
    parts = urlsplit(url)
    marker = "/.well-known/jwks.json"
    if not parts.scheme or not parts.netloc or not parts.path.endswith(marker):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path[: -len(marker)], "", ""))


@lru_cache
def auth_config() -> AuthConfig:
    """The verification parameters, resolved once per process.

    Cached because it owns the JWKS cache — building a new one per request would
    defeat the entire point of the TTL. `reset_auth_config()` is the way out,
    and exists for tests and for a key-rotation drill.
    """
    settings = get_settings()
    extra = AuthSettings()

    jwks_url = (settings.clerk_jwks_url or "").strip() or None
    issuer = (extra.clerk_issuer or "").strip() or None
    if issuer is None and jwks_url is not None:
        issuer = _issuer_from_jwks_url(jwks_url)

    demo_role = extra.demo_bypass_role.strip().lower()
    if demo_role not in ROLES:
        log.warning("DEMO_BYPASS_ROLE=%r is not a role; using %s", demo_role, DEFAULT_ROLE)
        demo_role = DEFAULT_ROLE

    return AuthConfig(
        jwks_url=jwks_url,
        issuer=issuer,
        audience=(extra.clerk_audience or "").strip() or None,
        clock_skew_s=max(0, extra.clerk_clock_skew_s),
        demo_bypass=settings.demo_bypass_auth,
        demo_role=demo_role,  # type: ignore[arg-type]
        jwks=(
            None
            if jwks_url is None
            else JwksCache(
                jwks_url,
                ttl_s=max(1, extra.clerk_jwks_ttl_s),
                timeout_s=extra.clerk_jwks_timeout_s,
            )
        ),
    )


def reset_auth_config() -> None:
    """Drop the resolved configuration and its JWKS cache."""
    get_settings.cache_clear()
    auth_config.cache_clear()


# --- §18 DEMO_BYPASS_AUTH ----------------------------------------------------
#: Values of an environment-name variable that mean "this is not a laptop".
DEPLOYED_ENV_VALUES: Final[frozenset[str]] = frozenset(
    {"production", "prod", "staging", "stage", "live", "preview"}
)
#: Variables that name an environment.
ENV_NAME_VARS: Final[tuple[str, ...]] = (
    "ENV",
    "ENVIRONMENT",
    "APP_ENV",
    "PYTHON_ENV",
    "NODE_ENV",
    "DEPLOY_ENV",
    "STORYFORGE_ENV",
)
#: Variables no laptop sets, injected by the platforms this could plausibly be
#: deployed to. DigitalOcean App Platform (§3's target) sets the first three.
PLATFORM_VARS: Final[tuple[str, ...]] = (
    "DIGITALOCEAN_APP_ID",
    "APP_URL",
    "APP_DOMAIN",
    "DYNO",
    "KUBERNETES_SERVICE_HOST",
    "FLY_APP_NAME",
    "RENDER",
    "RAILWAY_ENVIRONMENT",
    "AWS_EXECUTION_ENV",
    "ECS_CONTAINER_METADATA_URI",
    "VERCEL",
    "GAE_ENV",
    "WEBSITE_INSTANCE_ID",
)
#: Database hosts that can only be a developer machine.
LOCAL_DB_HOSTS: Final[frozenset[str]] = frozenset(
    {"", "localhost", "127.0.0.1", "::1", "db", "postgres", "host.docker.internal"}
)

_BYPASS_BANNER = (
    "DEMO_BYPASS_AUTH=true — Clerk verification is DISABLED and every request "
    "runs as the fixed local session %r (§18). This is a laptop-only fallback; "
    "it refuses to start anywhere that looks deployed."
)


def deployment_signals(database_url: str = "", clerk_secret_key: str | None = None) -> list[str]:
    """Everything about this process that suggests it is not a laptop.

    Each entry is a human-readable reason, because the failure this feeds is a
    refusal to boot and "something looked like production" is not an actionable
    message at 3am. The list is deliberately over-eager: a false positive costs
    one env var during a demo, a false negative costs an unauthenticated
    production API.
    """
    signals: list[str] = []

    for name in ENV_NAME_VARS:
        value = (os.environ.get(name) or "").strip().lower()
        if value in DEPLOYED_ENV_VALUES and value:
            signals.append(f"{name}={value}")

    for name in PLATFORM_VARS:
        if (os.environ.get(name) or "").strip():
            signals.append(f"{name} is set (platform-injected)")

    host = (urlsplit(database_url).hostname or "").strip().lower()
    if host and host not in LOCAL_DB_HOSTS:
        signals.append(f"DATABASE_URL points at a non-local host ({host})")

    if (clerk_secret_key or "").startswith("sk_live_"):
        signals.append("CLERK_SECRET_KEY is a live key (sk_live_…)")

    return signals


def assert_demo_bypass_is_local() -> None:
    """Refuse to run the §18 bypass anywhere that looks deployed.

    Called at import — so a container with this flag set dies at startup rather
    than serving unauthenticated traffic — and again inside the bypass branch of
    `current_principal`, because the environment can be mutated after import and
    the branch that grants access is the one that has to be sure.
    """
    settings = get_settings()
    if not settings.demo_bypass_auth:
        return

    signals = deployment_signals(settings.database_url, settings.clerk_secret_key)
    if signals:
        raise RuntimeError(
            "DEMO_BYPASS_AUTH=true is refused: this process looks deployed "
            f"({'; '.join(signals)}). §18 permits the bypass on a laptop only. "
            "Unset DEMO_BYPASS_AUTH and configure CLERK_JWKS_URL."
        )
    log.warning(_BYPASS_BANNER, DEMO_CLERK_ID)


# --- claims ------------------------------------------------------------------
class ClerkClaims(BaseModel):
    """The verified payload, narrowed to what authorization actually uses."""

    #: Clerk's user id (`user_…`). The stable identity; `users.clerk_id` mirrors it.
    sub: str
    role: Role = DEFAULT_ROLE
    email: str | None = None
    issuer: str | None = None
    expires_at: int | None = None
    #: The whole verified payload, for debugging and for claims added later.
    raw: dict[str, Any] = {}


def role_from_claims(claims: dict[str, Any]) -> Role:
    """§13's role, or `parent`.

    Two rules, both in the safe direction. An absent role is `parent`, because a
    parent who just signed up has no `public_metadata` at all. A *present* role
    this code does not recognise is also `parent` — "admin " with a typo, or a
    value from a future scheme, must never be treated as more than the least
    privilege on offer.
    """
    for path in ROLE_CLAIM_PATHS:
        node: Any = claims
        for part in path[:-1]:
            node = node.get(part) if isinstance(node, dict) else None
        value = node.get(path[-1]) if isinstance(node, dict) else None
        if isinstance(value, str):
            normalised = value.strip().lower()
            if normalised in ROLES:
                return normalised  # type: ignore[return-value]
            log.warning(
                "auth: unrecognised role %r at %s — treating as %s",
                value,
                ".".join(path),
                DEFAULT_ROLE,
            )
            return DEFAULT_ROLE
    return DEFAULT_ROLE


def _email_from_claims(claims: dict[str, Any]) -> str | None:
    for key in EMAIL_CLAIM_KEYS:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_EMAIL_CHARS]
    return None


def bearer_token(request: Request) -> str:
    """The token from `Authorization: Bearer …`, or a 401.

    Header only. Clerk's React SDK hands the app a token via `getToken()` and
    the app sends it here; accepting a cookie as well would add a CSRF surface
    for no capability the SPA does not already have.
    """
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("missing bearer token")
    return token.strip()


async def verify_token(token: str, config: AuthConfig | None = None) -> ClerkClaims:
    """Verify a Clerk session token (RS256 over JWKS) or raise 401.

    The order is the security argument. `alg` is pinned before a key is even
    looked up, so `alg: none` and the HS256-signed-with-the-public-key confusion
    both die at the header — the library's own algorithm check is a second line
    of defence, not the first. `kid` is required, so key selection is exact.
    Only then is a signature checked, and only then are `exp`, `nbf`, `iss` and
    `aud` enforced by PyJWT with `require` set, so a token that simply omits
    `exp` is not silently eternal.
    """
    config = config or auth_config()
    if config.jwks is None:
        raise HTTPException(
            status_code=503,
            detail="CLERK_JWKS_URL is not configured — this API cannot verify sessions",
        )

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise _unauthorized(f"malformed token header: {exc}") from exc

    algorithm = header.get("alg")
    if algorithm != ALGORITHM:
        raise _unauthorized(f"unsupported signing algorithm {algorithm!r}; only {ALGORITHM}")

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        raise _unauthorized("token header names no signing key (kid)")

    try:
        key = await config.jwks.key_for(kid.strip())
    except UnknownSigningKey:
        raise _unauthorized(f"unknown signing key {kid!r}") from None
    except JwksUnavailable as exc:
        # Not the caller's fault and not a credential problem: the identity
        # provider is unreachable. Saying 401 here would tell a legitimate
        # parent their login is bad when it is not.
        raise HTTPException(status_code=503, detail=f"JWKS unavailable: {exc}") from exc

    try:
        payload = jwt.decode(
            token,
            key=key,
            algorithms=[ALGORITHM],
            issuer=config.issuer,
            audience=config.audience,
            leeway=config.clock_skew_s,
            options={
                "require": ["exp", "sub"],
                # PyJWT rejects a token that carries `aud` when none is expected,
                # which would break Clerk's default template. Enforced strictly
                # when an audience IS configured: exact string, no list matching.
                "verify_aud": config.audience is not None,
                "strict_aud": config.audience is not None,
            },
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized(f"{type(exc).__name__}: {exc}") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise _unauthorized("token has no usable subject")

    return ClerkClaims(
        sub=subject.strip(),
        role=role_from_claims(payload),
        email=_email_from_claims(payload),
        issuer=payload.get("iss") if isinstance(payload.get("iss"), str) else None,
        expires_at=payload.get("exp") if isinstance(payload.get("exp"), int) else None,
        raw=payload,
    )


# --- the caller --------------------------------------------------------------
class Principal(BaseModel):
    """Who is calling: a `users` row plus the role the token asserted."""

    #: `users.id` — the FK `kids.parent_id` points at (DEVIATIONS 5.1).
    user_id: int
    clerk_id: str
    role: Role
    email: str | None = None
    #: True only under §18's bypass. Present so a handler or a log can tell.
    demo: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


_SELECT_USER_SQL = text("SELECT id, clerk_id, role, email FROM users WHERE clerk_id = :clerk_id")

# One statement, so two concurrent first-requests from the same new parent
# cannot both insert. Clerk owns `role`: it is overwritten on every change so
# revoking admin in the dashboard takes effect on the next request, not the next
# login. `email` is only ever filled in — a token minted from a template without
# the email claim must not blank a column the dashboard displays.
_UPSERT_USER_SQL = text(
    """
    INSERT INTO users (clerk_id, role, email)
    VALUES (:clerk_id, :role, :email)
    ON CONFLICT (clerk_id) DO UPDATE
        SET role = EXCLUDED.role,
            email = COALESCE(EXCLUDED.email, users.email)
    RETURNING id, clerk_id, role, email
    """
)


async def load_or_create_user(
    session: AsyncSession,
    *,
    clerk_id: str,
    role: Role,
    email: str | None,
) -> Principal:
    """§9: "loads/creates `users` row, enforces `role`".

    Read first, write only when something actually changed. The common request
    is a returning parent whose role and email already match, and that path
    costs one indexed SELECT and no write — an unconditional upsert would put a
    row write in front of every authenticated call in the system.
    """
    row = (await session.execute(_SELECT_USER_SQL, {"clerk_id": clerk_id})).first()
    stale = row is None or row.role != role or (email is not None and row.email != email)

    if stale:
        row = (
            await session.execute(
                _UPSERT_USER_SQL, {"clerk_id": clerk_id, "role": role, "email": email}
            )
        ).one()
        await session.commit()

    assert row is not None  # noqa: S101 — narrowing; the upsert always returns
    return Principal(
        user_id=row.id,
        clerk_id=row.clerk_id,
        role=row.role if row.role in ROLES else DEFAULT_ROLE,
        email=row.email,
    )


async def current_principal(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Principal:
    """The authenticated caller (§9), or 401.

    The §18 bypass is checked first and re-validates its own safety before it
    grants anything, so the flag cannot be flipped on in a deployed process by
    mutating the environment after import.
    """
    config = auth_config()

    if config.demo_bypass:
        assert_demo_bypass_is_local()
        # Loud on every request, on purpose. This is an auth bypass; a single
        # startup banner scrolls away and a quiet bypass is how one ships.
        log.warning(
            "DEMO_BYPASS_AUTH: serving %s %s as the fixed local %s %r (§18)",
            request.method,
            request.url.path,
            config.demo_role,
            DEMO_CLERK_ID,
        )
        principal = await load_or_create_user(
            session, clerk_id=DEMO_CLERK_ID, role=config.demo_role, email=None
        )
        return principal.model_copy(update={"demo": True})

    claims = await verify_token(bearer_token(request), config)
    return await load_or_create_user(
        session, clerk_id=claims.sub, role=claims.role, email=claims.email
    )


async def require_parent(
    principal: Principal = Depends(current_principal),
) -> Principal:
    """§9's `(parent)` gate.

    An admin passes too — the roles are a ladder, and Hank's own account (§13)
    is an admin who also has kids. That is not a hole: *ownership is enforced
    separately and unconditionally*, so an admin reaching a kid route still
    reaches only their own kids. Admin-only *data* lives behind `require_admin`
    on `/api/admin/*` and `/api/validate`.
    """
    return principal


async def require_admin(
    principal: Principal = Depends(current_principal),
) -> Principal:
    """§9's `(admin)` gate — `public_metadata.role == "admin"` in Clerk (§13).

    403 rather than 404: the route's existence is public (it is in the OpenAPI
    schema), so there is nothing to conceal, and an honest "you are not an
    admin" is what an operator needs. Ownership 404s are a different question
    with a different answer.
    """
    if not principal.is_admin:
        raise HTTPException(
            status_code=403,
            detail="admin role required (§9); set public_metadata.role in Clerk",
        )
    return principal


# --- owner scoping (§9: "parent → own kids only") ----------------------------
_OWNED_KID_SQL = text(
    """
    SELECT id, parent_id, display_name, stage_id, created_at
    FROM kids
    WHERE id = :id AND parent_id = :parent_id
    """
)

# The join is the whole authorization decision for every reading route. A
# reading is reachable exactly when the kid it belongs to belongs to the caller,
# so `/choice`, `/answers` and `/complete` cannot be reached by guessing ids.
_OWNED_READING_SQL = text(
    """
    SELECT r.id, r.kid_id, r.story_id, r.stage_id, r.path, r.started_at, r.completed_at
    FROM readings r
    JOIN kids k ON k.id = r.kid_id
    WHERE r.id = :id AND k.parent_id = :parent_id
    """
)


async def owned_kid(session: AsyncSession, principal: Principal, kid_id: int) -> Any:
    """This caller's kid, or 404 — including when it is someone else's kid.

    **404, never 403.** A 403 would confirm the id exists, which turns any kid
    route into an enumeration oracle over other families' children. The response
    for "no such kid" and "not your kid" is byte-identical, so there is nothing
    to compare. Same for a kid with a NULL `parent_id` — pre-auth rows are owned
    by nobody and are therefore visible to nobody.
    """
    row = (
        await session.execute(_OWNED_KID_SQL, {"id": kid_id, "parent_id": principal.user_id})
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"kid {kid_id} not found")
    return row


async def owned_reading(session: AsyncSession, principal: Principal, reading_id: int) -> Any:
    """This caller's reading, or 404. Ownership travels through the kid."""
    row = (
        await session.execute(
            _OWNED_READING_SQL, {"id": reading_id, "parent_id": principal.user_id}
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"reading {reading_id} not found")
    return row


# Startup gate: a deployed process with DEMO_BYPASS_AUTH set never gets as far
# as serving a request. Importing this module is unavoidable for the API, the
# CLIs and the tests, so there is no entry point that skips the check.
assert_demo_bypass_is_local()


__all__ = [
    "ALGORITHM",
    "DEMO_CLERK_ID",
    "AuthConfig",
    "AuthSettings",
    "ClerkClaims",
    "JwksCache",
    "JwksUnavailable",
    "Principal",
    "Role",
    "UnknownSigningKey",
    "assert_demo_bypass_is_local",
    "auth_config",
    "bearer_token",
    "current_principal",
    "deployment_signals",
    "load_or_create_user",
    "owned_kid",
    "owned_reading",
    "require_admin",
    "require_parent",
    "reset_auth_config",
    "role_from_claims",
    "verify_token",
]
