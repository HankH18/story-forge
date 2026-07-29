"""Suite-wide wiring. Exists only because Phase 6 put auth in front of §9.

Every route except `/api/health` now requires a verified Clerk session, and the
Phase 2–5 suites were written before that was true. Rather than thread a token
through fifty tests that are about mastery arithmetic and the fallback ladder,
the suite runs with §18's `DEMO_BYPASS_AUTH`, which makes every caller the one
fixed local parent. Those tests then assert exactly what they always asserted,
about one authenticated family.

**This is set here, not in `.env`.** A green suite that depended on a value in
an untracked local file would be a false green on any other machine — and the
opposite failure (a developer whose `.env` says `true` never discovering they
had disabled auth) is worse. Environment variables beat `env_file` in
pydantic-settings, so this assignment is what the API sees regardless.

`tests/test_auth.py` and `tests/test_kids.py` turn the bypass **off** and mint
real RS256 tokens against a real local JWKS server. That is deliberate division:
the bypass keeps the old suites honest about their own subject, and the two new
suites are the ones that actually exercise verification, roles and ownership. If
the bypass were the only thing ever tested, the auth code would be untested.

The `local_clerk` fixture below is that JWKS server. There are no Clerk
credentials on this machine and there do not need to be: a Clerk session token
is an RS256 JWT whose key is published at a JWKS URL, so a locally generated RSA
keypair served over real HTTP from a real socket exercises *the same code path
by the same protocol*. What it proves is the part that can be got wrong —
algorithm pinning, key selection, claim validation, cache behaviour. What it
cannot prove is Clerk-specific claim spelling; that is stated in the report
rather than faked here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Before anything imports storyforge.config — pydantic-settings caches its read.
os.environ["DEMO_BYPASS_AUTH"] = "true"

# The demo session runs as an admin here, for exactly one reason: `POST
# /api/validate` predates auth (Phase 2, §5.6) and §9 makes it admin-only, so
# four decoder tests that assert what the endpoint *computes* would otherwise
# assert 403 instead. Nothing about their subject changes — and the role gate
# itself is not taken on trust, because `test_auth.py` proves with real tokens
# that a parent gets 403 there and an admin does not.
os.environ["DEMO_BYPASS_ROLE"] = "admin"

# The §18 guard refuses the bypass when the environment looks deployed. A CI
# runner that happens to export CI-ish platform variables would otherwise fail
# every test with a startup error, so the ones this suite can safely disclaim
# are cleared here. DATABASE_URL is NOT touched: pointing the tests at a remote
# database really should stop the bypass dead.
#
# The prefixes mirror `auth.DEPLOYED_ENV_PREFIXES` / `auth.is_deployed_env_value`
# (DEVIATIONS 7.36) and are duplicated rather than imported ON PURPOSE: importing
# `storyforge.auth` is what runs the guard, which is the thing this scrub exists
# to get in front of. Both sites cross-reference each other.
_DEPLOYED_VALUES = {"production", "prod", "staging", "stage", "live", "preview"}
_DEPLOYED_PREFIXES = ("prod", "stag", "live", "preview")
for _leaked in ("ENV", "ENVIRONMENT", "APP_ENV", "PYTHON_ENV", "NODE_ENV", "DEPLOY_ENV"):
    _value = os.environ.get(_leaked, "").strip().lower()
    if _value and (_value in _DEPLOYED_VALUES or _value.startswith(_DEPLOYED_PREFIXES)):
        del os.environ[_leaked]

# Imported only after the environment above is settled.
import httpx  # noqa: E402
import jwt  # noqa: E402
import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from jwt.algorithms import RSAAlgorithm  # noqa: E402

from storyforge.auth import reset_auth_config  # noqa: E402
from storyforge.main import app  # noqa: E402

#: The `kid` the stub JWKS publishes. Tokens naming anything else are the
#: "unknown kid" attack.
LIVE_KID = "storyforge-local-1"
#: A key that exists but is NOT published — the "signed by a different key" case.
ROGUE_KID = "storyforge-rogue"

JWKS_PATH = "/.well-known/jwks.json"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64url_json(value: Any) -> str:
    return b64url(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


@dataclass
class _Keypair:
    private: Any
    kid: str

    @property
    def private_pem(self) -> bytes:
        return self.private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    @property
    def public_pem(self) -> bytes:
        return self.private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def jwk(self) -> dict[str, Any]:
        entry = RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        entry.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return entry


@dataclass
class LocalClerk:
    """A stand-in identity provider: real RSA, real HTTP, real JWTs."""

    origin: str
    live: _Keypair
    rogue: _Keypair
    audience: str
    #: One entry per HTTP request the API made for the JWKS. This is the
    #: evidence for "never fetch per request" — counted at the server, so it
    #: cannot be satisfied by a counter the cache increments about itself.
    hits: list[float] = field(default_factory=list)

    @property
    def jwks_url(self) -> str:
        return f"{self.origin}{JWKS_PATH}"

    def mint(
        self,
        *,
        sub: str = "user_local_a",
        role: str | None = "parent",
        role_path: str = "public_metadata",
        email: str | None = None,
        issuer: str | None = ...,  # type: ignore[assignment]
        audience: str | None = ...,  # type: ignore[assignment]
        expires_in: int | None = 300,
        not_before_in: int | None = None,
        kid: str | None = ...,  # type: ignore[assignment]
        key: Any = None,
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """A Clerk-shaped RS256 session token. Every knob is an attack knob."""
        now = int(time.time())
        claims: dict[str, Any] = {"sub": sub, "iat": now}
        if expires_in is not None:
            claims["exp"] = now + expires_in
        if not_before_in is not None:
            claims["nbf"] = now + not_before_in
        if issuer is not ...:
            if issuer is not None:
                claims["iss"] = issuer
        else:
            claims["iss"] = self.origin
        if audience is not ...:
            if audience is not None:
                claims["aud"] = audience
        else:
            claims["aud"] = self.audience
        if role is not None:
            if role_path == "":
                claims["role"] = role
            else:
                claims[role_path] = {"role": role}
        if email is not None:
            claims["email"] = email
        claims.update(extra_claims or {})

        headers: dict[str, Any] = {}
        if kid is not ...:
            if kid is not None:
                headers["kid"] = kid
        else:
            headers["kid"] = self.live.kid

        return jwt.encode(
            claims,
            key or self.live.private_pem.decode(),
            algorithm="RS256",
            headers=headers,
        )

    def unsigned(self, **kwargs: Any) -> str:
        """`alg: none` — the classic. PyJWT will not mint one, so this is hand-built."""
        signed = self.mint(**kwargs)
        _, payload, _ = signed.split(".")
        header = b64url_json({"alg": "none", "typ": "JWT", "kid": self.live.kid})
        return f"{header}.{payload}."

    def hs256_with_the_public_key(self, **kwargs: Any) -> str:
        """Algorithm confusion: HMAC the token with the key everyone can download.

        Hand-built for the same reason — PyJWT refuses to *create* this, which
        is not the same as this service refusing to *accept* it.
        """
        signed = self.mint(**kwargs)
        _, payload, _ = signed.split(".")
        header = b64url_json({"alg": "HS256", "typ": "JWT", "kid": self.live.kid})
        signing_input = f"{header}.{payload}".encode()
        signature = hmac.new(self.live.public_pem, signing_input, hashlib.sha256).digest()
        return f"{header}.{payload}.{b64url(signature)}"

    def tampered(self, **kwargs: Any) -> str:
        """A genuine token whose payload was edited to say `role: admin`."""
        header, _, signature = self.mint(**kwargs).split(".")
        claims = json.loads(base64.urlsafe_b64decode(_ + "=" * (-len(_) % 4)))
        claims["public_metadata"] = {"role": "admin"}
        claims["sub"] = "user_attacker"
        return f"{header}.{b64url_json(claims)}.{signature}"

    @staticmethod
    def bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def _keypairs() -> tuple[_Keypair, _Keypair]:
    return (
        _Keypair(rsa.generate_private_key(public_exponent=65537, key_size=2048), LIVE_KID),
        _Keypair(rsa.generate_private_key(public_exponent=65537, key_size=2048), ROGUE_KID),
    )


@pytest.fixture
def local_clerk(_keypairs, monkeypatch):
    """A real JWKS endpoint on a real socket, with the bypass turned OFF.

    Session-scoped keys (RSA generation is not free) but a per-test server and a
    per-test `auth_config`, so the JWKS cache starts empty and the fetch counts
    each test asserts are its own.
    """
    live, rogue = _keypairs
    clerk = LocalClerk(origin="", live=live, rogue=rogue, audience="storyforge-test")
    document = json.dumps({"keys": [live.jwk()]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's name
            if self.path != JWKS_PATH:
                self.send_error(404)
                return
            clerk.hits.append(time.monotonic())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(document)))
            self.end_headers()
            self.wfile.write(document)

        def log_message(self, *_args: Any) -> None:
            """Silence — the hit list is the record that matters."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    clerk.origin = f"http://127.0.0.1:{server.server_address[1]}"

    monkeypatch.setenv("DEMO_BYPASS_AUTH", "false")
    monkeypatch.setenv("CLERK_JWKS_URL", clerk.jwks_url)
    monkeypatch.setenv("CLERK_AUDIENCE", clerk.audience)
    # Issuer is deliberately NOT set: leaving it out exercises the derivation
    # from the JWKS URL, which is the path a real deployment takes.
    monkeypatch.delenv("CLERK_ISSUER", raising=False)
    reset_auth_config()

    try:
        yield clerk
    finally:
        server.shutdown()
        server.server_close()
        monkeypatch.undo()
        reset_auth_config()


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
