"""adopt the kids orphaned by ab5a866f52fc

Revision ID: c4f1a2d7be03
Revises: ab5a866f52fc
Create Date: 2026-07-29 19:05:00.000000

`ab5a866f52fc` detaches every pre-auth kid (`UPDATE kids SET parent_id = NULL`)
before it can add the FK, and its own note defends that as the confidentiality-
safe direction: `auth.owned_kid` matches `parent_id = :caller`, so a NULL owner
is visible to nobody, whereas guessing an owner hands one family another
family's children. That is right, and this migration does not undo it.

What it did not account for is *availability*. After it ran, all five readers in
the development database were owned by nobody: `GET /api/kids` returned `[]` for
every real Clerk account, the parent home said "No readers yet", and every
reading, `gpc_stats` row and promotion decision accumulated in Phase 5 sat
behind a row no principal could reach. Kid mode then fell back to a hard-coded
stage for every child (DEVIATIONS 7.20/7.24), which is how a stage-1 reader came
to be served text measuring 17.6% decodable at their own stage.

So the detach needs a companion, and the companion will not guess. It resolves
an owner in exactly one of two ways:

1. `STORYFORGE_ADOPT_PARENT_CLERK_ID` names the account explicitly, or
2. there is exactly ONE candidate row in `users`.

`demo_local_parent` — §18's fixed bypass principal, which is not a person — is
never a candidate. Anything else (no candidate, or two real accounts and no
override) raises. A migration that cannot tell whose children these are must
stop and say so: silently re-orphaning them is the defect it exists to fix, and
silently misassigning them is worse than the defect.

On a fresh database `kids` is empty, so this is a no-op and a first deploy never
sees any of it. It is idempotent: a second run finds no orphans and does
nothing.

The downgrade is deliberately a no-op. Ownership cannot be un-inferred, and
re-NULLing every `parent_id` on the way down would destroy real data that a
later upgrade could not reconstruct.
"""
from typing import Sequence, Union

import os

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4f1a2d7be03'
down_revision: Union[str, Sequence[str], None] = 'ab5a866f52fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The §18 bypass principal (`storyforge.auth.DEMO_CLERK_ID`). Spelled out
#: rather than imported: a migration is a historical record and must keep
#: applying identically after the constant is renamed or deleted.
DEMO_CLERK_ID = "demo_local_parent"

#: Names the owner when the database holds more than one real account.
OWNER_ENV_VAR = "STORYFORGE_ADOPT_PARENT_CLERK_ID"


def upgrade() -> None:
    """Give every ownerless kid an owner, or refuse to proceed."""
    bind = op.get_bind()

    orphans = bind.execute(
        sa.text("SELECT id, display_name FROM kids WHERE parent_id IS NULL ORDER BY id")
    ).all()
    if not orphans:
        return

    requested = (os.environ.get(OWNER_ENV_VAR) or "").strip()
    if requested:
        owner = bind.execute(
            sa.text("SELECT id, clerk_id FROM users WHERE clerk_id = :c"), {"c": requested}
        ).first()
        if owner is None:
            raise RuntimeError(
                f"{OWNER_ENV_VAR}={requested!r} names no row in `users`. "
                f"{len(orphans)} kid(s) have no owner and this migration will not guess."
            )
    else:
        candidates = bind.execute(
            sa.text("SELECT id, clerk_id FROM users WHERE clerk_id <> :demo ORDER BY id"),
            {"demo": DEMO_CLERK_ID},
        ).all()
        if len(candidates) != 1:
            named = ", ".join(repr(c.clerk_id) for c in candidates) or "none"
            raise RuntimeError(
                f"{len(orphans)} kid(s) have no owner and there "
                f"{'is' if len(candidates) == 1 else 'are'} {len(candidates)} candidate "
                f"account(s) ({named}), so the owner is ambiguous. Set "
                f"{OWNER_ENV_VAR}=<clerk_id> and re-run. Refusing to guess: an ownerless "
                "kid is invisible to everyone, and a misassigned one shows one family "
                "another family's child."
            )
        owner = candidates[0]

    bind.execute(
        sa.text("UPDATE kids SET parent_id = :owner WHERE parent_id IS NULL"),
        {"owner": owner.id},
    )

    # The whole point is that these rows become reachable. Assert it rather than
    # assume it — the failure this migration repairs was itself silent.
    remaining = bind.execute(
        sa.text("SELECT count(*) FROM kids WHERE parent_id IS NULL")
    ).scalar_one()
    if remaining:
        raise RuntimeError(f"{remaining} kid(s) still have no owner after the adoption")

    print(
        f"adopted {len(orphans)} ownerless kid(s) "
        f"({', '.join(str(o.id) for o in orphans)}) into users.id={owner.id} "
        f"({owner.clerk_id!r})"
    )


def downgrade() -> None:
    """No-op, on purpose — see the module docstring."""
