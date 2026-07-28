"""Phase 0 Definition of Done: the health endpoint is green.

These hit the real app over ASGI. The database assertion requires Postgres to be
up (`make db`), which is exactly the point — a health check that passes without a
database is not evidence of anything.
"""

import re

import httpx
import pytest

from storyforge.main import app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_health_responds_ok(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_health_reports_a_live_database(client):
    """The DB component must be genuinely reachable, not merely present."""
    body = (await client.get("/api/health")).json()
    db = body["components"]["database"]
    assert db["status"] == "ok", f"database unreachable: {db['detail']}"
    assert db["detail"].startswith("postgres 16"), db["detail"]


async def test_health_reports_a_genuinely_loaded_decoder(client):
    """§15: health checks the CMUdict load, not merely that the module imports.

    Phase 0 asserted `pending` here. Phase 1 built the decoder, so the assertion
    was replaced with the stronger claim — the detail string must carry real
    counts read from the pinned artifacts.
    """
    decoder = (await client.get("/api/health")).json()["components"]["decoder"]
    assert decoder["status"] == "ok", decoder["detail"]

    entries, gpcs = re.search(r"cmudict (\d+) entries, (\d+) gpcs", decoder["detail"]).groups()
    assert int(entries) > 100_000
    assert int(gpcs) > 50


async def test_health_declares_unbuilt_components_as_pending(client):
    """Health must not overstate what exists. Generation lands in phase 3."""
    components = (await client.get("/api/health")).json()["components"]
    assert components["anthropic"]["status"] == "pending"
