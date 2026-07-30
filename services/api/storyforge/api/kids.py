"""Kid CRUD, scoped to the parent that owns them (§9, §13).

`POST /api/kids` and `GET /api/kids` moved here from `api/readings.py` when
Clerk landed, because they stopped being "write a row / read the rows" and
became the place where ownership is established and enforced. Everything else
about a kid — `GET /api/kids/{id}/progress` — is a mastery read and stays with
the readings it summarises.

Two properties, and they are the Phase 6 Definition of Done.

**The owner comes from the token, never from the body.** `KidCreate` has exactly
two fields and neither of them is `parent_id`. A client-supplied owner id is not
an owner claim; the only thing that decides who owns a new kid is the verified
Clerk session (`auth.require_parent`). There is no request this endpoint accepts
that creates a kid for somebody else.

**The list is a filter, not a projection.** `GET /api/kids` selects
`WHERE parent_id = :caller` rather than fetching every kid and hiding the rest,
so there is no code path on which another family's row is loaded into this
process at all. This is the single line DEVIATIONS 5.9 said would change.

Kids still have no auth identity of their own (§13). A kid is not a principal;
it is a row a principal owns. That is why every kid-scoped route takes its
authorization from `kids.parent_id` and why a kid id is never a credential.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storyforge.auth import Principal, require_parent
from storyforge.db import get_session
from storyforge.decoder import MAX_STAGE, MIN_STAGE

router = APIRouter(tags=["kids"])

#: A child's first name (§13). Long enough for a hyphenated name, short enough
#: that the column is not a free-text field by accident.
MAX_DISPLAY_NAME = 40
#: The only non-letters a first name contains: `Mary Jane`, `O'Brien`,
#: `Ana-Sofia`, `J.`. Everything else — digits, slashes, angle brackets, the
#: whole punctuation table — belongs to addresses, phone numbers and markup.
NAME_PUNCTUATION = frozenset(" '-.")
#: `Mary Jane` yes; `Bob 555-123-4567 12 Oak St` no.
MAX_NAME_WORDS = 2

DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_DISPLAY_NAME)
]


class KidCreate(BaseModel):
    """§9 `POST /api/kids {display_name, stage_id}`.

    There is no third field, and that is the §13 posture in the type system: no
    email, no date of birth, no year group, no nickname the child types in. A
    parent supplies a first name and where the child is in the scope & sequence.

    There is no `parent_id` either, and that is the §9 posture in the type
    system: ownership is taken from the verified session, so a hostile body
    cannot assign a kid to another account.
    """

    display_name: DisplayName
    # DEVIATIONS 17.2: derived from the decoder, which owns stage semantics.
    stage_id: int = Field(ge=MIN_STAGE, le=MAX_STAGE)

    @field_validator("display_name")
    @classmethod
    def _is_a_first_name(cls, value: str) -> str:
        """§13, enforced as "a first name" rather than as "not an email".

        This field is not metadata. The theme picker renders it full-screen to
        a child as "hello {name}", and unlike every other child-facing string in
        the system it has been through none of §13's three layers — no
        blocklist, no allowlist-constrained generation, no moderation. It is
        free text typed by an adult and shown to a six-year-old. That is
        structurally the Phase 4 question hole (DEVIATIONS 5.12) arriving
        through a different door.

        The original rule rejected exactly one character, `@` (DEVIATIONS 5.9),
        so `"Bob 555-123-4567 12 Oak St"` was a 201 — a phone number and a home
        address, stored against a child, in a system whose §13 claim is that it
        holds a first name and nothing else. An allowlist is the right shape
        here precisely because the legitimate input is so narrow.

        `str.isalpha()` is Unicode-aware, so `Sofía`, `Zoë` and `李` are names
        and `9`, `😀` and `<` are not. `models.DISPLAY_NAME_IS_A_FIRST_NAME`
        enforces the same rule on the column, because a rule that lives only in
        one endpoint is a rule for one endpoint — this database already held
        `kid@example.com`, and it did not arrive through here.
        """
        if "@" in value:
            raise ValueError("display_name is a first name — no email addresses (§13)")

        rejected = sorted({c for c in value if not (c.isalpha() or c in NAME_PUNCTUATION)})
        if rejected:
            raise ValueError(
                "display_name is a child's first name (§13) and is rendered to the "
                f"child; it may not contain {''.join(rejected)!r}"
            )
        if len(value.split(" ")) > MAX_NAME_WORDS or "  " in value:
            raise ValueError(
                f"display_name is a first name (§13) — at most {MAX_NAME_WORDS} words"
            )
        return value


class KidDTO(BaseModel):
    id: int
    display_name: str
    stage_id: int
    created_at: datetime


_INSERT_KID_SQL = text(
    "INSERT INTO kids (parent_id, display_name, stage_id) "
    "VALUES (:parent_id, :name, :stage) "
    "RETURNING id, display_name, stage_id, created_at"
)

# The `WHERE` is the authorization. §9: "parent → own kids only".
_LIST_KIDS_SQL = text(
    "SELECT id, display_name, stage_id, created_at FROM kids "
    "WHERE parent_id = :parent_id ORDER BY id"
)


@router.post("/api/kids", response_model=KidDTO, status_code=201)
async def create_kid(
    body: KidCreate,
    principal: Principal = Depends(require_parent),
    session: AsyncSession = Depends(get_session),
) -> KidDTO:
    """Create a reader owned by the calling parent (§9, §13)."""
    if (
        await session.execute(text("SELECT 1 FROM stages WHERE id = :s"), {"s": body.stage_id})
    ).first() is None:
        raise HTTPException(
            status_code=404, detail=f"stage {body.stage_id} not seeded — run `make seed`"
        )

    row = (
        await session.execute(
            _INSERT_KID_SQL,
            {
                "parent_id": principal.user_id,
                "name": body.display_name,
                "stage": body.stage_id,
            },
        )
    ).one()
    await session.commit()
    return KidDTO(
        id=row.id,
        display_name=row.display_name,
        stage_id=row.stage_id,
        created_at=row.created_at,
    )


@router.get("/api/kids", response_model=list[KidDTO])
async def list_kids(
    principal: Principal = Depends(require_parent),
    session: AsyncSession = Depends(get_session),
) -> list[KidDTO]:
    """This parent's kids, and only this parent's kids (§9).

    Admins are not exempt. §9 gives admins their own routes under `/api/admin/*`
    for the operational views; this one answers "my kids" for whoever is asking,
    so an admin account sees its own children here like any other parent. An
    admin who could list every child by calling a parent route would make the
    ownership boundary a matter of who is asking rather than what is being asked.
    """
    rows = await session.execute(_LIST_KIDS_SQL, {"parent_id": principal.user_id})
    return [
        KidDTO(
            id=r.id, display_name=r.display_name, stage_id=r.stage_id, created_at=r.created_at
        )
        for r in rows
    ]


__all__ = ["KidCreate", "KidDTO", "MAX_DISPLAY_NAME", "router"]
