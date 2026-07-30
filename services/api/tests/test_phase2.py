"""Phase 2: seed, precompute and the validation endpoint.

These hit real Postgres. They require `make seed && make precompute` to have run —
which is the point: a precompute test that passes against an empty table is
evidence of nothing.
"""

from __future__ import annotations

import csv
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.decoder import load_gpcs, load_lexicon, mastered_ids, validate_word
from storyforge.decoder.lexicon_util import aligned_syllables, min_syllables, syllable_count
from storyforge.decoder.types import WordVerdict
from storyforge.main import app
from storyforge.precompute import load_blocklist


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


async def _scalar(session, sql: str, **params):
    return (await session.execute(text(sql), params or None)).scalar_one()


# --- seed --------------------------------------------------------------------
async def test_gpc_table_mirrors_the_decoder_inventory(session):
    """The database and the validator must never disagree about the inventory."""
    db_ids = {r[0] for r in await session.execute(text("SELECT gpc_id FROM gpcs"))}
    assert db_ids == {g.id for g in load_gpcs()}


async def test_all_eleven_stages_seeded(session):
    # Eleven since the ladder renumber (docs/DECISION-stage-ladder.md, 12.1-12.3).
    assert await _scalar(session, "SELECT count(*) FROM stages") == 11


async def test_sight_words_are_cumulative_by_stage(session):
    """Monotonicity of decodability (§5.7c) depends on this."""
    prev: set[str] = set()
    for stage in range(1, 12):
        rows = await session.execute(
            text("SELECT word FROM sight_words WHERE stage_id <= :s"), {"s": stage}
        )
        words = {r[0] for r in rows}
        assert prev <= words, f"stage {stage} lost a sight word"
        prev = words


# --- precompute --------------------------------------------------------------
async def test_allowed_words_is_populated(session):
    n = await _scalar(session, "SELECT count(*) FROM allowed_words")
    assert n > 10_000, f"only {n} rows — run `make precompute`"


async def test_every_allowed_word_revalidates_as_decodable(session):
    """§5.7 property (a), in the form the spec intended once the table exists.

    This is the load-bearing test of the whole phase: it proves the precomputed
    allowlist and the live validator cannot disagree.
    """
    rows = await session.execute(
        text("SELECT stage_id, word FROM allowed_words ORDER BY random() LIMIT 300")
    )
    failures = [
        (stage, word)
        for stage, word in rows
        if validate_word(word, mastered_ids(stage)).verdict is not WordVerdict.DECODABLE
    ]
    assert not failures, f"allowed words that do not validate: {failures[:10]}"


async def test_no_blocklisted_word_reaches_the_allowlist(session):
    rows = await session.execute(
        text("SELECT DISTINCT word FROM allowed_words WHERE word = ANY(:b)"),
        {"b": list(load_blocklist())},
    )
    assert not [r[0] for r in rows]


async def test_syllable_cap_is_honoured(session):
    rows = await session.execute(
        text(
            "SELECT a.stage_id, a.word, a.syllables, s.max_syllables "
            "FROM allowed_words a JOIN stages s ON s.id = a.stage_id "
            "WHERE a.syllables > s.max_syllables LIMIT 5"
        )
    )
    assert not rows.all()


async def test_the_live_syllable_cap_holds_for_the_reading_the_child_produces(session):
    """The same claim as `test_syllable_cap_is_honoured`, asked of the ALIGNMENT.

    DEVIATIONS 21.2. That test reads `allowed_words.syllables` and compares it to
    `stages.max_syllables`, so it proves precompute agrees with itself and nothing
    more — it was green over 67 rows whose stored count came from a pronunciation
    the aligner could not use at that stage (`every`, `family`, `different`,
    `several`, `towards` at stages 5-7, all counted 2 under a cap of 2 and all
    three syllables long in the only reading available there).

    Red means the table is stale against the code, not that the code is wrong:
    the remedy is `make precompute`, which is free.

    EVERY row, not `ORDER BY random() LIMIT n`. 208 of 34,288 rows carried a wrong
    count and 67 of those broke the cap — 0.6% of the table, which a 600-row sample
    misses outright about one run in forty. A tripwire with a 2.6% false-green rate
    against the defect it was written for is not a tripwire. The full scan costs
    ~2s because the aligner's lattice cache does the heavy half once per word.
    """
    rows = (
        await session.execute(
            text(
                "SELECT a.stage_id, a.word, a.syllables, s.max_syllables "
                "FROM allowed_words a JOIN stages s ON s.id = a.stage_id "
                "ORDER BY a.word, a.stage_id"
            )
        )
    ).all()
    assert rows, "allowed_words is empty — run `make precompute`"

    lexicon = load_lexicon()
    bad = []
    for stage, word, stored, cap in rows:
        real = aligned_syllables(word, lexicon.get(word, ()), mastered_ids(stage))
        if real is None or real != stored or real > cap:
            bad.append((stage, word, stored, real, cap))
    assert not bad, (
        f"{len(bad)} of {len(rows)} rows carry a syllable count from a "
        f"pronunciation the reader cannot use (stage, word, stored, aligned, cap): "
        f"{bad[:10]} — run `make precompute`"
    )


async def test_allowlist_grows_monotonically_with_stage(session):
    """A later stage must never allow fewer words than an earlier one."""
    rows = await session.execute(
        text("SELECT stage_id, count(*) FROM allowed_words GROUP BY 1 ORDER BY 1")
    )
    counts = [n for _, n in rows]
    assert counts == sorted(counts), f"allowlist shrinks at some stage: {counts}"


async def test_no_stage_is_starved(session):
    """§18: a stage with almost no vocabulary starves generation.

    The stage-1 exception 11.11 carried here is GONE, exactly the way its own
    tripwire said it should go (justify-test-edit verdict in DEVIATIONS 12.7):
    the exception asserted `counts[1] < 100` so that retiring stage 1 would turn
    it into a loud failure instead of a permanent loophole, the user-authorised
    renumber landed (docs/DECISION-stage-ladder.md), and the entry stage is now
    the old stage-2 inventory at 771 words. The plain assertion is restored for
    EVERY stage, and the floor is §18's own 150 rather than the softened 100 the
    exception era used — no stage needs the discount any more, and a fresh
    starvation anywhere would mean the FILTER broke, which renumbering cannot
    fix (11.11).
    """
    counts = dict(
        (await session.execute(text("SELECT stage_id, count(*) FROM allowed_words GROUP BY 1")))
        .tuples()
        .all()
    )
    starved = {stage: n for stage, n in counts.items() if n < 150}
    assert not starved, f"stages under §18's 150-word floor: {starved}"


# --- syllable counting -------------------------------------------------------
@pytest.mark.parametrize(
    "pron,expected",
    [
        (("K", "AE1", "T"), 1),
        (("D", "R", "AE1", "G", "AH0", "N"), 2),
        (("L", "IH1", "T", "AH0", "L"), 2),
        (("HH", "M"), 1),  # no vowel at all still counts as one
    ],
)
def test_syllable_count(pron, expected):
    assert syllable_count(pron) == expected


def test_min_syllables_takes_the_kindest_reading():
    assert min_syllables((("K", "AE1", "T"), ("K", "AE1", "T", "AH0"))) == 1


def _seeded_syllable_caps() -> dict[int, int]:
    """`stages.max_syllables`, from the CSV `make seed` loads. No database, no
    hand-copied ladder — `data/seed/stages.csv` is the same file the table comes
    from, so this cannot disagree with what is seeded."""
    path = Path(__file__).resolve().parents[3] / "data/seed/stages.csv"
    body = [
        ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and ln[0] != "#"
    ]
    return {int(r["id"]): int(r["max_syllables"]) for r in csv.DictReader(body)}


# --- the syllable cap measures the reading the child produces (21.2) --------
# `test_syllable_cap_is_honoured` above compares the STORED number against the
# stage cap, so it can only ever prove precompute is consistent with itself. It
# was green while the stored number came from `min_syllables(prons)` — the fewest
# syllables in any CMUdict entry for the word, including entries the aligner could
# not use at that stage. These ask the alignment instead of the row.
@pytest.mark.parametrize(
    "word,stage,counted,aligned,why",
    [
        # only /EH1 V ER0 IY0/ aligns; the 2-syllable /EH1 V R IY0/ never does
        ("every", 5, 2, 3, "over stage 5's cap of 2 — the row should not exist"),
        ("every", 8, 2, 3, "under stage 8's cap of 3, but still not 2"),
        ("family", 5, 2, 3, "same shape as `every`"),
        ("several", 6, 2, 3, "same shape as `every`"),
        ("towards", 6, 1, 2, "/T AO1 R D Z/ needs `ow`->/AO/, which is not taught"),
        # the stage-DEPENDENT case: silent-e is stage 4, so before it the only
        # reading of `fine` is the Italian /F IH1 N AH0/, and the word gets
        # SHORTER as the child learns more.
        ("fine", 1, 1, 2, "silent-e is stage 4; the 1-syllable reading is unavailable"),
        ("fine", 4, 1, 1, "silent-e taught — the kind reading is now reachable"),
    ],
)
def test_the_syllable_count_comes_from_a_pronunciation_that_aligns(
    word, stage, counted, aligned, why
):
    prons = load_lexicon()[word]
    assert min_syllables(prons) == counted, f"{word}: the OLD number moved"
    assert aligned_syllables(word, prons, mastered_ids(stage)) == aligned, why


def test_aligned_syllables_is_none_when_no_pronunciation_aligns():
    """`None`, never a number. "Cannot be read here at all" and "can be read here
    in two syllables" are different answers, and a cap comparison must not be able
    to accept the first by accident."""
    prons = load_lexicon()["beautiful"]
    for stage in range(1, 12):
        assert aligned_syllables("beautiful", prons, mastered_ids(stage)) is None
    # ...and the word it replaces answers 3 at every stage, which is <= the top
    # stage's cap of 9. That is the accident.
    assert min_syllables(prons) == 3


def test_no_precomputed_row_is_capped_against_an_unusable_pronunciation():
    """The whole allowlist, re-derived — the test that would have caught 21.2.

    Pure: `compute_rows` takes no database, so this covers all ~34k rows in about
    two seconds and cannot be green against a stale table. Two claims, and the
    second is the one that was false: every row is under its stage's cap, AND the
    number it is under the cap on is the one the reader actually produces.

    The caps are READ FROM THE SEED CSV rather than written out here, so this test
    covers whatever ladder is seeded — a hand-written `{1: 2, ..., 11: 9}` raises
    `KeyError` the moment the ladder is renumbered again, which is a test failing
    for a reason that has nothing to do with what it measures.
    """
    from storyforge.precompute import compute_rows

    caps = _seeded_syllable_caps()
    lexicon = load_lexicon()
    rows = compute_rows(caps)
    assert len(rows) > 30_000, "compute_rows produced almost nothing — check the data files"

    bad = []
    for row in rows:
        stage, word = row["stage_id"], row["word"]
        real = aligned_syllables(word, lexicon[word], mastered_ids(stage))
        if real != row["syllables"] or real is None or real > caps[stage]:
            bad.append((stage, word, row["syllables"], real, caps[stage]))
    assert not bad, (
        f"{len(bad)} rows are capped on a pronunciation the reader cannot use at "
        f"that stage (stage, word, stored, aligned, cap): {bad[:10]}"
    )


# --- POST /api/validate (§5.6) ----------------------------------------------
async def test_validate_endpoint_reproduces_the_demo_sentence(client):
    """§17 item 1 over HTTP — the thesis, as a grader would see it."""
    resp = await client.post(
        "/api/validate",
        json={
            # Stage 2 since the renumber — the same cumulative inventory the
            # §17 demo always ran against (DEVIATIONS 12.5, 12.12).
            "text": "The knight sees a beautiful dragon",
            "stage_id": 2,
            "story_words": ["dragon"],
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["decodability"] == 0.0
    assert len(body["violations"]) == 3
    assert body["story_word_count"] == 1
    assert body["counts"]["oov"] == 0

    verdicts = {w["word"]: w["verdict"] for w in body["words"]}
    assert verdicts["knight"] == "violation"
    assert verdicts["the"] == "sight"
    assert verdicts["dragon"] == "story"

    missing = {w["word"]: w["missing_gpcs"] for w in body["words"]}
    assert missing["knight"] == ["igh|AY", "kn|N"]
    assert missing["sees"] == ["ee|IY", "s|Z"]


async def test_validate_endpoint_accepts_extra_sight_words(client):
    """A kid's own name joins their sight allowlist (§4)."""
    payload = {"text": "Maya sat", "stage_id": 1, "extra_sight_words": ["Maya"]}
    body = (await client.post("/api/validate", json=payload)).json()
    verdicts = {w["word"]: w["verdict"] for w in body["words"]}
    assert verdicts["maya"] == "sight"
    assert verdicts["sat"] == "decodable"
    assert body["decodability"] == 1.0


async def test_validate_endpoint_rejects_an_unseeded_stage(client):
    resp = await client.post("/api/validate", json={"text": "cat", "stage_id": 99})
    assert resp.status_code == 422  # outside the ge=1, le=11 bound


async def test_validate_endpoint_rejects_overlong_text(client):
    resp = await client.post("/api/validate", json={"text": "a " * 3000, "stage_id": 1})
    assert resp.status_code == 422


# --- pipeline integrity ------------------------------------------------------
async def test_invented_story_words_cannot_shrink_the_denominator(session):
    """§0: the LLM is an untrusted generator.

    Story words are excluded from the decodability denominator, so a model that
    declares ordinary words as "story words" inflates its own score. The claim
    must be intersected with the words actually offered.
    """
    from storyforge.pipeline import load_vocabulary, validate_draft

    vocab = await load_vocabulary(session, 3, "dragons")
    text_body = "The king set a big dragon. The big lot got and the class red ran last."

    honest = validate_draft(vocab, text_body, ["dragon"])
    inflated = validate_draft(
        vocab, text_body, ["dragon", "king", "big", "class", "red"]
    )

    # Only the genuinely-offered word survives the intersection.
    assert honest.distinct_story_words == ["dragon"]
    assert inflated.distinct_story_words == ["dragon"]
    # And the denominator is therefore identical — the claim buys nothing.
    assert inflated.scored_word_count == honest.scored_word_count
