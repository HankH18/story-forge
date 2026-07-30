"""Phase 2: seed, precompute and the validation endpoint.

These hit real Postgres. They require `make seed && make precompute` to have run —
which is the point: a precompute test that passes against an empty table is
evidence of nothing.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text

from storyforge.db import SessionLocal
from storyforge.decoder import load_gpcs, mastered_ids, validate_word
from storyforge.decoder.lexicon_util import min_syllables, syllable_count
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
