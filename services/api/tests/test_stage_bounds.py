"""The stage ladder has ONE length, and everything that states it must derive it.

DEVIATIONS 17.2. When Appendix A's stage 1 was retired (12.1-12.14, migration
`f3a91c6d0e24`) the ladder went from twelve stages to eleven, and the ceiling was
edited **by hand** in five Pydantic schemas, one range check, one error message
and two CLI defaults. `MAX_STAGE` already existed and was already re-exported
into `storyforge.mastery` precisely so there would be one source of truth; the
bounds simply did not use it.

Nothing was wrong on the day — every hand edit was made correctly. What was wrong
is that **nothing could tell**. `tests/decoder/test_inventory.py` pins
`MAX_STAGE == 11` and `MAX_STAGE == max(gpc.stage) + 1`, and `test_mastery.py`
pins mastery's re-export; past that, every assertion about a request bound was
written as a literal. `test_phase2.py` probes `stage_id: 99` and expects 422,
which passes for any ceiling below 99. `test_admin.py` pins the *string*
`"within 1..11"`. A renumber to twelve would leave five schemas silently
refusing the top stage with a green suite.

The four tests here are the ratchet, and they divide on one line: whether the
thing stating the bound is able to import `MAX_STAGE`.

**Can import it** — the five request bounds, the range check and its message, and
both CLI `DEFAULT_STAGES`. Those are now derived, and the sweep below asserts the
derivation rather than the number, so a bound added tomorrow is covered by a test
written today.

**Cannot import it** — `data/seed/stages.csv` (a CSV) and
`apps/web/src/lib/api.ts`'s `SEEDED_STAGES` (TypeScript, and there is no shared
package). For those the brief is a test that asserts the two agree. The CSV is
asserted here; the TypeScript is asserted against the CSV by
`apps/web/src/lib/seeds.test.ts`, so the chain MAX_STAGE -> CSV -> SEEDED_STAGES
is closed end to end without a second copy of the number.

There is deliberately no test for a SQL `CHECK` on `stage_id`, because there is
no such constraint: the database bounds stages only through the `stages` foreign
key, so `test_the_seeded_stages_table_agrees_with_the_decoder` is the check that
the ROWS — the actual bound — match.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from sqlalchemy import text

from storyforge import eval as eval_cli
from storyforge import warm
from storyforge.db import SessionLocal
from storyforge.decoder import MAX_STAGE, MIN_STAGE
from storyforge.main import app

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
STAGES_CSV = REPO_ROOT / "data" / "seed" / "stages.csv"

#: Every bound the sweep is expected to find. Named so the sweep cannot pass
#: vacuously: a refactor that renames `stage_id` would otherwise turn this test
#: into an assertion over the empty set, which is the failure mode
#: `_UNENRICHED_SQL` had (11.99) reached by a different route.
EXPECTED_BOUNDED_SCHEMAS = {
    "ValidateRequest",  # POST /api/validate
    "KidCreate",  # POST /api/kids
    "StoryRequest",  # POST /api/stories/request (the §7.21 admin preview knob)
    "ColdGenerateRequest",  # POST /api/admin/cold-generate
}


def _bounds_over(node, path: str = ""):
    """Every `(path, title, minimum, maximum)` triple in an OpenAPI document."""
    if isinstance(node, dict):
        if "maximum" in node or "minimum" in node:
            yield path, node.get("title"), node.get("minimum"), node.get("maximum")
        for key, value in node.items():
            yield from _bounds_over(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _bounds_over(value, f"{path}[{i}]")


def _stage_bounds():
    """The bounds the API actually publishes on a stage-shaped field.

    Read out of the generated OpenAPI document rather than out of the source
    files, because that is the artefact a client is handed: it is the schema
    FastAPI will enforce, and it catches a bound declared anywhere — a new route,
    a `Query`, a nested model — without this test having to list it.
    """
    for path, title, lo, hi in _bounds_over(app.openapi()):
        if "stage" in (title or "").lower() or "stage" in path.lower():
            yield path, lo, hi


def test_every_stage_bound_the_api_publishes_is_the_decoders():
    """The five hand-edited `le=11`s, asserted as a derivation.

    A sweep, not a list of four file paths: the defect was that adding or copying
    a bound cost nothing and was invisible, so the guard has to cover bounds that
    do not exist yet.
    """
    found = list(_stage_bounds())
    wrong = [(p, lo, hi) for p, lo, hi in found if (lo, hi) != (MIN_STAGE, MAX_STAGE)]
    assert wrong == [], (
        f"{len(wrong)} stage bound(s) disagree with the decoder's "
        f"{MIN_STAGE}..{MAX_STAGE}: {wrong}. Import MIN_STAGE/MAX_STAGE from "
        "`storyforge.decoder` instead of writing the number."
    )

    schemas_seen = {
        p.split("/components/schemas/")[1].split("/")[0]
        for p, _, _ in found
        if "/components/schemas/" in p
    }
    missing = EXPECTED_BOUNDED_SCHEMAS - schemas_seen
    assert not missing, (
        f"the sweep found no stage bound on {sorted(missing)} — either the bound was "
        "dropped (a request may now name a stage that does not exist) or the field "
        "was renamed and this test is now asserting over a smaller set than it thinks."
    )


def test_the_seed_ladder_csv_agrees_with_the_decoder():
    """`data/seed/stages.csv` cannot import `MAX_STAGE`, so it is asserted here.

    The CSV is the authority the database is built from AND the authority
    `apps/web/src/lib/seeds.test.ts` holds `SEEDED_STAGES` to, so pinning it to
    `MAX_STAGE` closes the TypeScript side without duplicating the number into
    TypeScript.
    """
    rows = list(
        csv.DictReader(
            io.StringIO(
                "\n".join(
                    line
                    for line in STAGES_CSV.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")
                )
            )
        )
    )
    ids = [int(r["id"]) for r in rows]
    assert ids == list(range(MIN_STAGE, MAX_STAGE + 1)), (
        f"{STAGES_CSV} declares stages {ids} but the decoder's ladder is "
        f"{MIN_STAGE}..{MAX_STAGE}. `make seed` loads this file, so the two "
        "disagreeing means the database and the validator disagree."
    )


async def test_the_seeded_stages_table_agrees_with_the_decoder():
    """And the ROWS, because there is no SQL CHECK to pin.

    `stages` has no bound constraint on `id`; every other table reaches the ladder
    through a foreign key to this one. So the real database-side ceiling is
    `max(stages.id)`, and that is what a stage bound in a request schema is
    ultimately protecting.
    """
    async with SessionLocal() as session:
        ids = [
            r[0] for r in (await session.execute(text("SELECT id FROM stages ORDER BY id"))).all()
        ]
    assert ids == list(range(MIN_STAGE, MAX_STAGE + 1)), (
        f"the `stages` table holds {ids}, the decoder's ladder is "
        f"{MIN_STAGE}..{MAX_STAGE}. Re-run `make seed`."
    )


def test_both_cli_default_matrices_cover_the_whole_ladder():
    """§7's "full matrix" and §14's acceptance matrix are the SAME ladder.

    `DEFAULT_STAGES` was `"1,2,3"` in `warm.py` for five phases and a fresh deploy
    got nine empty stages that nothing could see (8.40) — a stale ceiling here
    does not raise, it silently stops covering the top of the ladder. Both are now
    spelled from `MIN_STAGE`/`MAX_STAGE`; this asserts they still PARSE to every
    stage, which is the property the operator actually depends on.
    """
    ladder = list(range(MIN_STAGE, MAX_STAGE + 1))
    assert warm.parse_stages(warm.DEFAULT_STAGES) == ladder
    assert eval_cli.parse_stages(eval_cli.DEFAULT_STAGES) == ladder
    assert warm.parse_stages("all") == ladder
