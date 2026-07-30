"""One rolled-back transaction the test and the API share (DEVIATIONS 8.91, 9.62).

Extracted from `test_stories_api.py`, where it was written for §7's ladder, because
three files now need the same mechanism and a third hand-rolled copy is how the two
halves of one mechanism drift apart:

- `test_stories_api.py` — §7's four rungs, and STATE invariant 6 (the ladder never
  climbs). Both need a requested stage that holds nothing.
- `test_story_stage_binding.py` — the serve-time decodability gate. Its poison row
  has to be the row `_SERVE_SQL` picks FIRST or the gate is never reached
  (AUDIT-PHASE7 F3).
- `test_eval.py` — the `eval` status must be the only thing that can refuse the
  fixture, which means the fixture has to be servable in every other respect and
  the only candidate on the ladder (AUDIT-PHASE7 F2).

**Why a transaction and not an empty stage band.** Every one of those needs a pool
whose shape it controls, and the previous answer was to go looking for a stage the
real pool had not reached — `base = max(servable stage_id) + 1` in
`test_stories_api.py` (DEVIATIONS 8.90), `EMPTY_STAGE_BAND = 12` with a comment
claiming nothing lives above stage 5 in `test_story_stage_binding.py` (9.65),
`stage = min(12, max(stage_id) + 1)` in `test_eval.py` (9.63). All three are the
same bet: that the product is incomplete. `make warm-check` demands >=3 servable
stories in all 36 (stage,theme) pairs, stage 12 included, so the bet is against
the product working and a full `make warm` settles it — loudly in the first case
(four fixture ERRORs) and **silently** in the other two, which is worse.

The empty pool is therefore MANUFACTURED, inside a transaction that is thrown
away.

Three pieces, and each is load-bearing:

1. **A connection out of the API's own engine, with one transaction on it.** Not
   `SessionLocal()`: the fixture's rows have to be visible to the endpoint without
   being committed, and the only way two parties see the same uncommitted rows is
   if they are the same connection.
2. **`get_session` overridden**, so the endpoint runs on that connection.
   `join_transaction_mode="create_savepoint"` is what makes the handler's own
   `await session.commit()` a SAVEPOINT release instead of a real commit — the
   request path commits mid-handler (it must; `_SERVE_SQL` is an `UPDATE …
   RETURNING` under `FOR UPDATE SKIP LOCKED`) and that must not escape.
3. **REPEATABLE READ.** The snapshot is fixed at the fixture's first statement, so
   a story `make warm` commits *while the test runs* cannot appear in the ladder
   underneath it. Under READ COMMITTED it could, and STATE.md is explicit that a
   concurrent writer makes this suite lie. The cost is stated in DEVIATIONS 8.91:
   a genuine write/write race surfaces as a loud serialization failure rather than
   as a silent flake.

Teardown rolls back, so a test using this leaves the database exactly as it found
it — including `last_served_at`, which these tests used to stamp on real rows, and
including the `questions` row `test_eval.py` needs but must not commit (9.63).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import engine, get_session
from storyforge.main import app


@asynccontextmanager
async def owned_transaction() -> AsyncIterator[AsyncSession]:
    """A session the API shares, on a transaction that is always rolled back."""
    conn = await engine.connect()
    conn = await conn.execution_options(isolation_level="REPEATABLE READ")
    try:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )

        async def _shared_session() -> AsyncIterator[AsyncSession]:
            # No `finally: close()` — FastAPI resumes generator dependencies after
            # the response, and closing here would end the transaction the rest of
            # the test still runs in.
            yield session

        app.dependency_overrides[get_session] = _shared_session
        try:
            yield session
        finally:
            app.dependency_overrides.pop(get_session, None)
            await session.close()
            await outer.rollback()
    finally:
        await conn.close()


async def hide_servable_pool(session: AsyncSession) -> int:
    """Move every real servable story out of `SERVABLE_STATUSES`, in-transaction.

    Returns how many rows were hidden — a fact about the operator's database,
    reported rather than asserted about, because that is the thing these fixtures
    no longer bet on.

    `rejected` because it is the one existing status `SERVABLE_STATUSES` excludes;
    DELETE would cascade through `story_segments`, `questions`, `readings` and
    `answers` for no gain, since what `_SERVE_SQL` reads is `status` (DEVIATIONS
    8.93). Inside the doomed transaction either way.

    LOCK COST, named because the audit named it (F15, DEVIATIONS 9.69): this holds
    row locks on the whole servable pool until rollback, and `_SERVE_SQL`'s `FOR
    UPDATE SKIP LOCKED` makes a concurrent request on another connection skip every
    locked row and answer 503. Sub-second per test, and the alternative — committing
    the hide and restoring in a `finally` — leaves every story `rejected` for every
    reader if the process dies between the two commits.
    """
    hidden = (
        await session.execute(
            text("UPDATE stories SET status = 'rejected' WHERE status = ANY(:s)"),
            {"s": list(SERVABLE_STATUSES)},
        )
    ).rowcount

    # Measured, not assumed, and it catches the one failure mode that would be
    # SILENT: an `UPDATE` that did not cover everything the serving path counts as
    # servable. The tests could still pass in that state — against the live pool,
    # which is the defect this helper exists to end. (An override that stopped
    # taking effect is the LOUD failure mode: the endpoint then cannot see the
    # fixture's uncommitted rows at all and every test using it fails.)
    left = (
        await session.execute(
            text("SELECT count(*) FROM stories WHERE status = ANY(:s)"),
            {"s": list(SERVABLE_STATUSES)},
        )
    ).scalar_one()
    assert left == 0, (
        f"the fixture does not own the pool: {left} servable stories are still "
        f"visible after hiding {hidden} — the tests below would be measuring the "
        "live pool instead of their own rows"
    )
    return hidden
