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

**Do not generalise the sentence above to SESSIONS. Revoking a session does not
invalidate a token that has already been issued** — `verify_token` checks the
signature, `exp`, `nbf`, `iss` and optionally `aud`, and asks Clerk nothing, so a
revoked session's token is accepted for the rest of its own lifetime. That is the
ordinary stateless-JWT trade and it is deliberate (AUDIT-PHASE6 C11, DEVIATIONS
8.63). **The bound is the token's remaining lifetime, and Clerk's browser session
token measures `exp - iat = 60`** — `getToken()` re-mints it roughly every 53 s
and the re-mint is what fails after a sign-out — so the exposure for anything a
browser holds is ≤60 s, reaching that same parent's own family data and no other
family's (ownership is the `kids.parent_id` filter below). The audit observed 30
minutes because it used a **Backend-API-minted 1800 s** token, which only a holder
of `CLERK_SECRET_KEY` can create and which no browser in this product produces.

Three closures were considered and are recorded in 8.63 rather than built.
Checking `sts != "active"` closes nothing: the claim is frozen at mint time, so a
revoked session's issued token still reads `active`. Asking Clerk per request puts
a third-party round trip on every authenticated call, including every call in a
child's reading session, and turns a Clerk blip into a total outage — which is the
thing the JWKS cache and its stale-key window (7.34) exist to avoid. Capping
`exp - iat` would bound the window for every token, and would also make this API
refuse every request in the product the day an operator raises the session-token
lifetime in a dashboard this code cannot see.

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

import ipaddress
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

#: How long a cached JWKS document may be served BEYOND its TTL when the JWKS
#: endpoint cannot be reached (DEVIATIONS 7.34). One hour: long enough to ride out
#: a Clerk incident, short enough that "the identity provider has been gone for a
#: day" is not answered with keys from yesterday. RSA signing keys outlive this by
#: design — Clerk rotates on the order of months and publishes the old and new
#: keys together while it does.
_DEFAULT_STALE_GRACE_S: Final = 3600.0

#: While the endpoint is failing and a usable stale document is in hand, do not
#: re-attempt the fetch more often than this. Without it, every request retries a
#: 5 s connect while holding the cache lock, and an identity-provider outage
#: becomes a queue.
_FAILURE_BACKOFF_S: Final = 30.0


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
    """The JWKS endpoint could not be reached or did not parse.

    `document_rejected` separates the two, because they are different operator
    problems with the same HTTP status. False means nothing usable came back
    over the wire — Clerk is down, DNS is wrong, the connect timed out. True
    means the endpoint **answered** and what it said could not be used: an empty
    `keys` array, an HTML error page served as 200, a key set this service has
    no algorithm for. The second is nearly always a misconfigured
    `CLERK_JWKS_URL` or a proxy in the way, and telling an operator "the JWKS
    endpoint is unreachable" while it is answering every request sends them to
    look at the wrong thing (DEVIATIONS 26.5).
    """

    document_rejected: bool = False


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
    #: How long past the TTL a cached document may still be used when the JWKS
    #: endpoint is unreachable (DEVIATIONS 7.34). Set to 0 to refuse all stale
    #: keys and take the outage instead. This is availability only: an unknown
    #: `kid` is still refused, and every signature and claim check is unchanged.
    clerk_jwks_stale_grace_s: float = _DEFAULT_STALE_GRACE_S
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
        stale_grace_s: float = _DEFAULT_STALE_GRACE_S,
        failure_backoff_s: float = _FAILURE_BACKOFF_S,
    ) -> None:
        self.url = url
        self._ttl_s = ttl_s
        self._timeout_s = timeout_s
        self._rotation_cooldown_s = rotation_cooldown_s
        self._stale_grace_s = max(0.0, stale_grace_s)
        self._failure_backoff_s = max(0.0, failure_backoff_s)
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._failed_at: float | None = None
        self._last_rotation_fetch = float("-inf")
        self._lock = Lock()
        #: HTTP requests actually made to the JWKS endpoint.
        self.fetches = 0
        #: Keys served without touching the network.
        self.cache_hits = 0
        #: Keys served from a document past its TTL because the endpoint was down.
        #: A counter rather than only a log line, for the same reason `fetches` is
        #: one: "did this ever happen in production?" should be answerable.
        self.stale_serves = 0

    def stats(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "fetches": self.fetches,
            "cache_hits": self.cache_hits,
            "stale_serves": self.stale_serves,
            "keys": sorted(self._keys),
            "ttl_s": self._ttl_s,
            "stale_grace_s": self._stale_grace_s,
            "age_s": (
                None if self._fetched_at is None else round(time.monotonic() - self._fetched_at, 3)
            ),
        }

    def clear(self) -> None:
        """Forget everything, counters included. Tests and key-rotation drills."""
        self._keys = {}
        self._fetched_at = None
        self._failed_at = None
        self._last_rotation_fetch = float("-inf")
        self.fetches = 0
        self.cache_hits = 0
        self.stale_serves = 0

    async def key_for(self, kid: str) -> Any:
        """The public key for `kid`, from cache when possible.

        Four outcomes, in order of how often they happen: a cache hit (no IO), a
        TTL-expired refetch, a **stale** serve when that refetch cannot reach
        Clerk, and — rarely — a fetch triggered by a `kid` that is not in a
        still-fresh document, which is what key rotation looks like from here.
        The last one is deliberately rate-limited: without the cooldown, an
        unauthenticated stranger sending random `kid`s would turn this service
        into a load generator pointed at Clerk.

        **The stale serve is not a fail-open** (DEVIATIONS 7.34). Three things
        make it safe, and each is the reason the dangerous version was not
        written. A stale document cannot contain a key it never contained, so an
        unknown `kid` is still refused and the 503 still happens for it. Nothing
        about signature, `exp`, `nbf`, `iss` or `aud` changes — the key is a real
        Clerk public key that Clerk published, and an expired token is still
        expired. And the window is bounded by `stale_grace_s`, so an identity
        provider that never comes back does not leave this API trusting a key set
        indefinitely. What it buys is that a Clerk blip past the TTL stops taking
        the whole product down, including a child mid-story.
        """
        async with self._lock:
            now = time.monotonic()
            fresh = self._fetched_at is not None and now - self._fetched_at < self._ttl_s

            if fresh and kid in self._keys:
                self.cache_hits += 1
                return self._keys[kid]

            fetched_now = False
            if not fresh:
                if self._backing_off(now) and self._stale_key_usable(kid, now):
                    return self._serve_stale(kid, now, "the last fetch failed moments ago")
                try:
                    await self._fetch(now)
                except JwksUnavailable as exc:
                    if self._stale_key_usable(kid, now):
                        return self._serve_stale(kid, now, _stale_reason(exc))
                    raise
                fetched_now = True
                if kid in self._keys:
                    return self._keys[kid]

            if not fetched_now and now - self._last_rotation_fetch >= self._rotation_cooldown_s:
                log.info(
                    "jwks: kid %r absent from a fresh document, refetching (rotation?)", kid
                )
                # No stale fallback on this path, and none is possible: the
                # document in hand is FRESH and does not contain this kid, so
                # there is no older key to fall back to. A failure here stays a
                # 503, which is what an unreachable provider is.
                await self._fetch(now)
                if kid in self._keys:
                    return self._keys[kid]

            raise UnknownSigningKey(kid)

    def _backing_off(self, now: float) -> bool:
        """Did a fetch fail recently enough that retrying is just queueing?"""
        return self._failed_at is not None and now - self._failed_at < self._failure_backoff_s

    def _stale_key_usable(self, kid: str, now: float) -> bool:
        """Is this `kid` in a document that is past its TTL but inside the grace?"""
        if self._fetched_at is None or kid not in self._keys:
            return False
        return now - self._fetched_at <= self._ttl_s + self._stale_grace_s

    def _serve_stale(self, kid: str, now: float, why: str) -> Any:
        self.stale_serves += 1
        age = 0.0 if self._fetched_at is None else now - self._fetched_at
        log.warning(
            "jwks: serving STALE key %r — %s and the cached document is %.0fs old "
            "(ttl %.0fs + grace %.0fs). Signature, exp, nbf, iss and aud are all "
            "still enforced and an unknown kid is still refused; this only avoids "
            "locking every user out while Clerk is unreachable (§18, 7.34).",
            kid,
            why,
            age,
            self._ttl_s,
            self._stale_grace_s,
        )
        return self._keys[kid]

    async def _fetch(self, now: float) -> None:
        """Replace the cached document. Callers hold the lock."""
        self.fetches += 1
        self._last_rotation_fetch = now

        # Split in two so the failure can say WHICH half failed. Both arm the
        # backoff and both are `JwksUnavailable`; only the second one means the
        # endpoint answered (DEVIATIONS 26.5). `raise_for_status` stays on the
        # transport side: a 5xx from a JWKS host is a broken host or proxy, not
        # a statement about the key set.
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(self.url)
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — every transport failure is one thing here
            self._failed_at = now
            raise JwksUnavailable(f"{type(exc).__name__}: {exc}") from exc

        try:
            keys = _parse_jwks(response.json())
        except JwksUnavailable as exc:
            self._failed_at = now
            exc.document_rejected = True
            raise
        except Exception as exc:  # noqa: BLE001 — a body that is not JSON is a bad document
            self._failed_at = now
            rejected = JwksUnavailable(f"{type(exc).__name__}: {exc}")
            rejected.document_rejected = True
            raise rejected from exc

        self._keys = keys
        self._fetched_at = now
        self._failed_at = None
        log.info(
            "jwks: fetch #%d from %s → %d key(s) %s",
            self.fetches,
            self.url,
            len(self._keys),
            sorted(self._keys),
        )


def _stale_reason(exc: JwksUnavailable) -> str:
    """Why a stale key is being served, in the operator's terms (26.5).

    The two are one HTTP status and two completely different things to go and
    look at, so the log line has to tell them apart.
    """
    if exc.document_rejected:
        return (
            "the JWKS endpoint ANSWERED but its document carries no key this service "
            f"can use ({exc}) — check CLERK_JWKS_URL and anything proxying it"
        )
    return "the JWKS endpoint is unreachable"


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
                stale_grace_s=max(0.0, extra.clerk_jwks_stale_grace_s),
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
#: …and the PREFIXES of such values, because the exact-set test missed
#: `ENVIRONMENT=prod-us` — a real spelling that boots an unauthenticated API on a
#: production cluster (DEVIATIONS 7.36). Deliberately not `dev`, `development`,
#: `local` or `test`: those must keep working.
DEPLOYED_ENV_PREFIXES: Final[tuple[str, ...]] = ("prod", "stag", "live", "preview")
#: …and the MARKERS, looked for anywhere in the value, because the prefix test
#: above missed `prd` and `pre-production` and both booted a bypassed API
#: (AUDIT-PHASE7 F11, DEVIATIONS 8.65) — under a comment claiming the prefixes had
#: closed the family. A prefix list only catches the spellings someone thought of;
#: this family is spelled `preprod`, `pre-production`, `k8s-prod`, `eu-stg`, `prd`.
#:
#: Every one of these is checked in BOTH directions in `tests/test_auth.py`: none
#: of `dev`, `development`, `local`, `localhost`, `test`, `testing`, `ci`,
#: `sandbox`, `demo`, `laptop` contains any of them, and a false positive here
#: costs a developer their demo.
DEPLOYED_ENV_MARKERS: Final[tuple[str, ...]] = (
    "prod",
    "prd",
    "stag",
    "stg",
    "live",
    "preview",
    "uat",
    "canary",
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
    # Added after the Phase 6 audit booted a bypassed API with each of these set
    # (7.36). `AWS_EXECUTION_ENV` above is not enough on its own: the al2023
    # Lambda runtimes stopped setting it, so `AWS_LAMBDA_FUNCTION_NAME` and
    # `LAMBDA_TASK_ROOT` are the ones that are actually always there.
    "K_SERVICE",  # Cloud Run / Knative
    "K_REVISION",
    "CONTAINER_APP_NAME",  # Azure Container Apps
    "WEBSITE_SITE_NAME",  # Azure App Service / Functions
    "FUNCTIONS_WORKER_RUNTIME",
    "AWS_LAMBDA_FUNCTION_NAME",
    "LAMBDA_TASK_ROOT",
    "ECS_CONTAINER_METADATA_URI_V4",
    "HEROKU_APP_NAME",
    "NOMAD_ALLOC_ID",
    "OPENSHIFT_BUILD_NAME",
    "CF_PAGES",
    "KOYEB_APP_NAME",
)
#: Variables that mean "a platform is telling this process how to serve".
#:
#: `PORT` is the archetype and it is the one addition here with a real
#: false-positive risk — a developer may have it exported for something else. It
#: is taken anyway: every PaaS on earth sets it, a laptop demo of THIS repo does
#: not (`make dev` passes `--port 8000` as a flag), and §18's trade is already
#: "a false positive costs one env var during a demo, a false negative costs an
#: unauthenticated production API". The refusal names the variable.
SERVING_HINT_VARS: Final[tuple[str, ...]] = ("PORT",)
#: Database hosts that can only be a developer machine.
#:
#: The empty string is NOT in here any more. A `DATABASE_URL` whose host parses
#: to nothing is a unix socket, and `?host=/cloudsql/…` is a deployment, not a
#: laptop (7.36). An *empty* `DATABASE_URL` is handled separately, because that
#: is "unconfigured" rather than "deployed".
LOCAL_DB_HOSTS: Final[frozenset[str]] = frozenset(
    {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
)
#: Hostnames that only resolve inside a container network — which is to say, this
#: process is in a container. `db` is the service name in this repo's own
#: `docker-compose.yml`, and it used to be on the LOCAL list: stand that compose
#: file up on a rented VPS with the bypass on and the guard saw a local-looking
#: database, no environment variable and a test Clerk key, and booted a fully
#: unauthenticated API on a public address. `make dev` is unaffected — it runs the
#: API on the host against `localhost:5433`, which is exactly why compose
#: publishes `5433:5432`.
CONTAINER_DB_HOSTS: Final[frozenset[str]] = frozenset(
    {"db", "database", "postgres", "postgresql", "pg", "pgbouncer"}
)

#: Evidence of containerisation that lives on the FILESYSTEM rather than in the
#: environment — which is the entire reason it is here (DEVIATIONS 8.66).
#:
#: Every other signal in this guard can be removed by whoever starts the process.
#: AUDIT-PHASE7 F11 proved the cheapest version of that: `docker run
#: -e DEMO_BYPASS_AUTH=true -e PORT=` blanked the one variable the image supplies
#: to itself and served a fully unauthenticated API. There is no `docker run` flag
#: that removes `/.dockerenv`.
#:
#: §18's own words are "a laptop-only fallback". A container is not a laptop. On
#: macOS — where `make dev` and the test suite run — none of these paths exists,
#: so the laptop path is untouched by their presence in this list.
CONTAINER_MARKER_FILES: Final[tuple[str, ...]] = ("/.dockerenv", "/run/.containerenv")
CONTAINER_CGROUP_PATH: Final = "/proc/1/cgroup"
CONTAINER_CGROUP_MARKERS: Final[tuple[str, ...]] = (
    "docker",
    "containerd",
    "kubepods",
    "lxc",
    "podman",
)
#: The one documented way past the container signal, and it cancels **that signal
#: only** — every other signal still refuses while it is set, so it is not a key
#: to the guard.
#:
#: It exists because the failure mode is asymmetric: a dev container or a
#: containerised CI runner would otherwise fail every test at import with a
#: message about production, and a guard that blocks a legitimate local workflow
#: gets deleted rather than configured. The value has to be typed in full, so
#: setting it is a decision rather than a shrug — and a careless operator putting
#: the demo on a VPS will not set a variable they have never heard of.
CONTAINER_OVERRIDE_VAR: Final = "STORYFORGE_BYPASS_IN_CONTAINER"
CONTAINER_OVERRIDE_VALUE: Final = "i-understand-this-disables-auth"

#: `localhost` as a peer name. Every other loopback form is decided numerically
#: by `ipaddress`, which covers all of 127.0.0.0/8, `::1` and `::ffff:127.0.0.1`
#: without a list of spellings to keep in step.
LOOPBACK_PEER_NAMES: Final[frozenset[str]] = frozenset({"localhost"})

_BYPASS_BANNER = (
    "DEMO_BYPASS_AUTH=true — Clerk verification is DISABLED and every request "
    "runs as the fixed local session %r (§18). This is a laptop-only fallback; "
    "it refuses to start anywhere that looks deployed."
)


def is_deployed_env_value(value: str) -> bool:
    """Does an environment-name variable's value mean "not a laptop"?

    Exact membership, a prefix match, OR a marker anywhere in the value. Each
    widening is a measured defeat of the previous one: the exact-only version
    booted a bypassed API under `ENVIRONMENT=prod-us` (DEVIATIONS 7.36), and the
    prefix version booted under `ENVIRONMENT=prd` and `ENVIRONMENT=pre-production`
    (AUDIT-PHASE7 F11, DEVIATIONS 8.65).

    `tests/conftest.py` scrubs these values before importing this module and so
    duplicates the rule rather than calling this function — importing
    `storyforge.auth` is what runs the guard, which is the thing the scrub exists
    to avoid. Keep the two in step.
    """
    normalised = value.strip().lower()
    if not normalised:
        return False
    if normalised in DEPLOYED_ENV_VALUES or normalised.startswith(DEPLOYED_ENV_PREFIXES):
        return True
    return any(marker in normalised for marker in DEPLOYED_ENV_MARKERS)


def container_signals() -> list[str]:
    """Is this process inside a container? Read off the filesystem, not the env.

    Returns at most one signal, and none at all when `CONTAINER_OVERRIDE_VAR`
    carries `CONTAINER_OVERRIDE_VALUE` exactly. Any read error is treated as "no
    evidence": this function must never be the reason the API fails to start.
    """
    if (os.environ.get(CONTAINER_OVERRIDE_VAR) or "").strip() == CONTAINER_OVERRIDE_VALUE:
        return []

    for path in CONTAINER_MARKER_FILES:
        try:
            if os.path.exists(path):
                return [
                    f"{path} exists, so this process is in a container — §18 permits "
                    f"the bypass on a laptop only. Set "
                    f"{CONTAINER_OVERRIDE_VAR}={CONTAINER_OVERRIDE_VALUE} only if this "
                    f"container really is a development machine."
                ]
        except OSError:  # pragma: no cover — a stat that raises is not evidence
            continue

    try:
        with open(CONTAINER_CGROUP_PATH, encoding="utf-8", errors="replace") as handle:
            cgroup = handle.read().lower()
    except OSError:
        return []  # no /proc at all (macOS), or unreadable — not evidence
    for marker in CONTAINER_CGROUP_MARKERS:
        if marker in cgroup:
            return [
                f"{CONTAINER_CGROUP_PATH} names {marker!r}, so this process is in a "
                f"container — §18 permits the bypass on a laptop only. Set "
                f"{CONTAINER_OVERRIDE_VAR}={CONTAINER_OVERRIDE_VALUE} only if this "
                f"container really is a development machine."
            ]
    return []


def deployment_signals(database_url: str = "", clerk_secret_key: str | None = None) -> list[str]:
    """Everything about this process that suggests it is not a laptop.

    Each entry is a human-readable reason, because the failure this feeds is a
    refusal to boot and "something looked like production" is not an actionable
    message at 3am. The list is deliberately over-eager: a false positive costs
    one env var during a demo, a false negative costs an unauthenticated
    production API.

    **This is mostly a list of negative signals, and that is its weakness.** A
    bare `docker run -e DEMO_BYPASS_AUTH=true` on a public host sets few of them,
    and AUDIT-PHASE7 F11 proved that blanking one variable removed the last one.
    `container_signals()` is the answer to that specific defeat and the one entry
    here that no `-e` flag can erase (DEVIATIONS 8.66). The remaining half of the
    inverse control — refuse unless the process is bound to `127.0.0.1` — is still
    not taken, because the bind address does not exist yet when this runs (this
    module is imported before uvicorn has a socket); see 8.66.
    """
    signals: list[str] = []

    for name in ENV_NAME_VARS:
        value = (os.environ.get(name) or "").strip().lower()
        if is_deployed_env_value(value):
            signals.append(f"{name}={value}")

    # PRESENCE, not value, for both loops below. `-e PORT=` booted a fully
    # unauthenticated API on the real image (AUDIT-PHASE7 F11, DEVIATIONS 8.64):
    # `(os.environ.get(name) or "").strip()` is empty for a blanked variable, so
    # emptying it removed the signal while `--port ${PORT:-8080}` served on. No
    # platform sets one of these to "" and no laptop has a reason to, so a blank
    # value is if anything the stronger signal — someone had to do it on purpose.
    for name in PLATFORM_VARS:
        if name in os.environ:
            value = (os.environ.get(name) or "").strip()
            signals.append(
                f"{name} is set (platform-injected)"
                if value
                else f"{name} is present but BLANK — a platform variable was emptied"
            )

    for name in SERVING_HINT_VARS:
        if name in os.environ:
            value = (os.environ.get(name) or "").strip()
            signals.append(
                f"{name}={value} is set — a platform is telling this process where "
                f"to listen. Unset it (not blank it) to run the §18 bypass locally."
                if value
                else f"{name} is present but BLANK — a platform variable was emptied. "
                f"Unset it entirely to run the §18 bypass locally."
            )

    signals.extend(container_signals())

    url = (database_url or "").strip()
    host = (urlsplit(url).hostname or "").strip().lower()
    if host and host in CONTAINER_DB_HOSTS:
        signals.append(
            f"DATABASE_URL host {host!r} only resolves inside a container network, "
            f"so this process is containerised"
        )
    elif host and host not in LOCAL_DB_HOSTS:
        signals.append(f"DATABASE_URL points at a non-local host ({host})")
    elif url and not host:
        # A unix socket. Could be Postgres.app on a laptop; could equally be
        # `?host=/cloudsql/project:region:instance`. The hostname is the thing
        # this function checks and there is none, so it cannot be verified local.
        signals.append(
            "DATABASE_URL has no host (unix socket), so it cannot be verified as local"
        )

    if (clerk_secret_key or "").startswith("sk_live_"):
        signals.append("CLERK_SECRET_KEY is a live key (sk_live_…)")

    return signals


def peer_is_loopback(request: Request) -> bool:
    """Did this request arrive from this machine? (DEVIATIONS 26.7)

    Every other signal in the §18 guard is read at IMPORT, and 8.66 recorded the
    consequence in its own words: "The remaining half of the inverse control —
    refuse unless the process is bound to `127.0.0.1` — is still not taken,
    because the bind address does not exist yet when this runs". Measured, that
    gap is real: a rented VPS running `uvicorn --host 0.0.0.0` directly on the
    host, with Postgres on `localhost` and a `sk_test_…` key, produces **zero**
    deployment signals and boots a fully unauthenticated children's-data API.

    `assert_demo_bypass_is_local` is already called a second time from inside
    `current_principal`, which *does* hold the request — so the peer address is
    available exactly where the grant is made, and this is that half of the
    control, taken at request time instead of at import.

    **What it does not close, stated rather than implied**: a reverse proxy on
    the same host (nginx public, uvicorn on 127.0.0.1) makes every peer look
    like loopback, and nothing inside this process can see nginx. The bound
    address would catch that and is not usable here — under an ASGI test
    transport `scope["server"]` is not an address at all — so this narrows the
    hole rather than closing it. It costs nothing on the sanctioned path: `make
    dev` serves on the host and both the browser and the Vite proxy reach it
    over loopback.

    No peer address is **not** evidence of a laptop, so it returns False.
    """
    client = getattr(request, "client", None)
    host = (getattr(client, "host", "") or "").strip().lower()
    if not host:
        return False
    if host in LOOPBACK_PEER_NAMES:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


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
        if not peer_is_loopback(request):
            # The half of the guard that could not be taken at import (8.66,
            # DEVIATIONS 26.7). A refusal rather than a fall-through to real
            # verification: `demo_bypass_auth` is one bit on `/api/health` and
            # a bypass that applied to some callers and not others would put
            # this API back where AUDIT-PHASE6's finding 0 found it — two
            # readers, opposite conclusions about whether auth was on.
            peer = getattr(request.client, "host", None) if request.client else None
            log.error(
                "DEMO_BYPASS_AUTH: REFUSING %s %s from non-loopback peer %r — §18's "
                "bypass is a laptop-only fallback and this request came from the "
                "network. Unset DEMO_BYPASS_AUTH and configure CLERK_JWKS_URL.",
                request.method,
                request.url.path,
                peer,
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    "DEMO_BYPASS_AUTH is active and this request did not come from "
                    f"this machine (peer {peer!r}). §18 permits the bypass on a "
                    "laptop only; it is never a way to serve remote traffic without "
                    "authentication."
                ),
            )
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
    "CONTAINER_OVERRIDE_VALUE",
    "CONTAINER_OVERRIDE_VAR",
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
    "container_signals",
    "current_principal",
    "deployment_signals",
    "is_deployed_env_value",
    "load_or_create_user",
    "peer_is_loopback",
    "owned_kid",
    "owned_reading",
    "require_admin",
    "require_parent",
    "reset_auth_config",
    "role_from_claims",
    "verify_token",
]
