"""Phase 0 Definition of Done: the health endpoint is green.

These hit the real app over ASGI. The database assertion requires Postgres to be
up (`make db`), which is exactly the point — a health check that passes without a
database is not evidence of anything.
"""

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


async def test_health_declares_unbuilt_components_as_pending(client):
    """Phase 0 must not overstate what exists: decoder and Anthropic are pending."""
    components = (await client.get("/api/health")).json()["components"]
    assert components["decoder"]["status"] == "pending"
    assert components["anthropic"]["status"] == "pending"
