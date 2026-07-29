"""Grammar (docs/DEVIATIONS.md 4.12) — prevention and detection.

The validator proves *decodability* and the moderation node judges *safety*.
Nothing looked at whether the sentence was well-formed English, and live
warm-pool output shipped "He did not came back." and "The dragon had a got
time." — both 100% decodable, both wrong, both would reach a child.

Two layers, both tested here:

  prevention  a grammar rule in `SYSTEM_RULES` (frozen, so the cache-prefix
              invariant is re-asserted alongside it — STATE.md 10)
  detection   `well_formed` + `grammar_note` on the moderation verdict, folded
              into `approved` so an ungrammatical story is rejected exactly like
              an unsafe one and the warm pool absorbs it

The decodability gate is NOT lowered to accommodate grammar; there is a test for
that below.

`test_live_moderation_fixture_both_directions` is the one that matters. It runs
the REAL Haiku node over a labelled fixture and prints a confusion matrix. It is
opt-in (`STORYFORGE_LIVE_MODERATION=1`) because it costs money, but per
DEVIATIONS 3.10 any change to the moderation prompt must be re-tested in BOTH
directions — the first version of that prompt had a 55% false-positive rate and
made a whole theme unusable.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from storyforge import warm
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.llm import moderate as mod_mod
from storyforge.llm.generate import SYSTEM_RULES
from storyforge.llm.moderate import (
    MODERATION_SYSTEM,
    ModerationResult,
    ModerationVerdict,
    blocklist_check,
    moderate,
)
from storyforge.llm.pricing import Usage
from storyforge.models import Question, Story, StorySegment
from storyforge.pipeline import DECODABILITY_GATE


# --- fakes -------------------------------------------------------------------
class _FakeClient:
    """Stands in for AsyncAnthropic. Counts calls so the blocklist short-circuit
    can be proven to cost nothing."""

    def __init__(self, parsed: ModerationVerdict | None, stop_reason: str = "end_turn"):
        self._parsed = parsed
        self._stop = stop_reason
        self.calls = 0

    def with_options(self, **_kwargs):
        return self

    @property
    def messages(self):
        return self

    async def parse(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            parsed_output=self._parsed,
            stop_reason=self._stop,
            usage=SimpleNamespace(
                input_tokens=90,
                output_tokens=12,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
        )


def _stub(monkeypatch, parsed, stop_reason: str = "end_turn") -> _FakeClient:
    client = _FakeClient(parsed, stop_reason)
    monkeypatch.setattr(mod_mod, "get_client", lambda: client)
    return client


CLEAN = "Sam sat. A cat sat on Sam. Sam is glad."


# --- prevention: the frozen prompt -------------------------------------------
def test_system_rules_demand_grammatical_english():
    low = SYSTEM_RULES.lower()
    assert "correct english" in low
    assert "verb tense" in low or "tense" in low
    # The observed failure mode is named, not merely gestured at.
    assert "did not came" in low


def test_system_rules_stay_free_of_request_detail():
    """STATE.md 10 / DEVIATIONS 3.5: `SYSTEM_RULES` is the frozen cache prefix.
    Any per-request detail smuggled in alongside the grammar rule would
    invalidate the per-stage cache on every single call — silently, and visible
    only as a cost line. Duplicated from test_llm.py deliberately: this is the
    invariant most at risk from editing this block."""
    for leak in ("dragon", "Stage 3", "30-60", "{", "}"):
        assert leak not in SYSTEM_RULES


def test_system_rules_still_defend_the_simple_register():
    """The grammar rule must not be read as "write richer prose". "Sam sat." is
    correct output; a model that starts inflating verbs to sound literate walks
    straight back through the decodability gate."""
    assert "Sam sat." in SYSTEM_RULES
    assert "simple by design" in SYSTEM_RULES


def test_grammar_rule_ranks_below_the_vocabulary_rules():
    """Priority order is load-bearing. If grammar outranked the allowlist the
    model would reach outside the permitted words to fix a sentence, and the
    story would fail the gate instead of reading badly."""
    assert SYSTEM_RULES.index("PERMITTED WORDS list") < SYSTEM_RULES.index("correct English")


# --- detection: the moderation prompt ----------------------------------------
def test_moderation_prompt_asks_for_grammar_independently_of_safety():
    assert "well_formed" in MODERATION_SYSTEM
    assert "SEPARATELY" in MODERATION_SYSTEM


def test_moderation_prompt_defends_the_simple_register():
    """DEVIATIONS 3.10 — the expensive lesson. The first moderation prompt
    rejected 5 of 9 warm stories. A grammar question is a *new* false-positive
    surface: short, repetitive, plain prose is the format working, not an error.
    The prompt must say so in the grammar section, not just the safety one."""
    grammar_half = MODERATION_SYSTEM.split("SEPARATELY", 1)[1]
    low = grammar_half.lower()
    assert "sam sat" in low
    assert "repeated" in low or "repetitive" in low
    assert "the end." in low  # a fragment beat is not an error


def test_moderation_prompt_carves_out_the_observed_false_positives():
    """Every carve-out below is empirically earned, not anticipated. The first
    draft of this prompt flagged 5 of 20 live warm stories — "It did nap.",
    "Its wings help it.", "He said he will go.", and a tense shift between two
    otherwise-fine sentences. One of its own corrections ("wings helps") was
    itself ungrammatical. Deleting any of these lines re-opens a false positive
    that has actually happened."""
    grammar_half = MODERATION_SYSTEM.split("SEPARATELY", 1)[1].lower()
    assert "it did nap." in grammar_half  # emphatic do + base verb is correct
    assert "he said he will go." in grammar_half  # no backshift required
    assert "between sentences is style" in grammar_half  # cross-sentence tense
    assert "when in doubt, well_formed = true" in grammar_half
    # The stubborn one: the rule alone did not hold, the MECHANISM did. Below
    # the stage that teaches "-ed" the past-tense form is not in the child's
    # word list at all, so present-tense narration is forced, not sloppy.
    assert '"-ed" ending' in grammar_half
    assert 'do not ask for "helped"' in grammar_half
    # Same root cause, second surface: the judge asked for "could" in a
    # past-tense story. "could" is no more available than "helped" is.
    assert 'never ask for "could"' in grammar_half


def test_moderation_prompt_requires_a_nameable_correction():
    """The self-contradictory notes ("should be 'It did nap' or 'It napped'")
    were the tell that the judge was flagging without a correction in hand.
    Requiring one is the cheapest available brake on hallucinated errors."""
    grammar_half = MODERATION_SYSTEM.split("SEPARATELY", 1)[1].lower()
    assert "on its own" in grammar_half
    assert "replacement word" in grammar_half


# --- detection: the verdict wiring -------------------------------------------
async def test_ungrammatical_story_is_rejected_like_an_unsafe_one(monkeypatch):
    """The whole point of 4.12: safe + decodable + ungrammatical must not ship."""
    _stub(
        monkeypatch,
        ModerationVerdict(
            approved=True,
            reason="storybook peril only",
            well_formed=False,
            grammar_note='"He did not came back." — wrong verb form after "did".',
        ),
    )
    result = await moderate(CLEAN)
    assert result.approved is False
    assert result.well_formed is False


async def test_the_grammar_note_reaches_the_rejection_reason(monkeypatch):
    """`warm.py` prints and persists `reason`; a bare "rejected" would leave the
    operator unable to tell a grammar reject from a safety one."""
    _stub(
        monkeypatch,
        ModerationVerdict(
            approved=True,
            reason="nothing unsafe",
            well_formed=False,
            grammar_note='"The dragon had a got time." — stray word.',
        ),
    )
    result = await moderate(CLEAN)
    assert "ungrammatical" in result.reason
    assert "had a got time" in result.reason
    assert result.grammar_note.startswith('"The dragon')


async def test_an_empty_grammar_note_still_yields_a_usable_reason(monkeypatch):
    _stub(
        monkeypatch,
        ModerationVerdict(approved=True, reason="fine", well_formed=False, grammar_note="  "),
    )
    result = await moderate(CLEAN)
    assert result.approved is False
    assert result.reason == "ungrammatical: sentence is not well-formed English"
    assert result.grammar_note == ""


async def test_the_safety_reason_wins_when_a_story_fails_both(monkeypatch):
    """Two problems, one `reason` field. Safety is the one an operator must see
    first; the grammar detail survives on `grammar_note`."""
    _stub(
        monkeypatch,
        ModerationVerdict(
            approved=False,
            reason="a character is hurt and it is never resolved",
            well_formed=False,
            grammar_note="tense error",
        ),
    )
    result = await moderate(CLEAN)
    assert result.approved is False
    assert result.reason == "a character is hurt and it is never resolved"
    assert result.grammar_note == "tense error"


async def test_safe_and_well_formed_is_approved(monkeypatch):
    """The other direction. A grammar check that rejects everything is not a
    grammar check."""
    _stub(
        monkeypatch,
        ModerationVerdict(
            approved=True, reason="gentle and resolved", well_formed=True, grammar_note=""
        ),
    )
    result = await moderate(CLEAN)
    assert result.approved is True
    assert result.well_formed is True


async def test_unparseable_verdict_fails_closed_in_both_halves(monkeypatch):
    """DEVIATIONS 3.11 extended to grammar: "we could not tell" must never read
    as "it was fine", for either question."""
    _stub(monkeypatch, None, stop_reason="max_tokens")
    result = await moderate(CLEAN)
    assert result.approved is False
    assert result.well_formed is False
    assert "unparseable" in result.reason


async def test_the_blocklist_short_circuit_still_costs_no_api_call(monkeypatch):
    """Layer 1 is free and must stay free — adding a grammar question to layer 3
    must not drag layer 3 into the layer-1 path."""
    client = _stub(
        monkeypatch,
        ModerationVerdict(approved=True, reason="", well_formed=True, grammar_note=""),
    )
    result = await moderate("The man had a gun and the dog was dead.")
    assert result.approved is False
    assert result.layer == "blocklist"
    assert client.calls == 0


def test_a_grammatical_story_is_not_a_blocklist_concern():
    """Layer 1 has no opinion about grammar and must not grow one."""
    assert blocklist_check("He did not came back. The dragon had a got time.") == []


def test_grammar_did_not_lower_the_decodability_gate():
    """§0 / STATE.md 1. The cheap way to make ungrammatical stories go away is to
    let the model use more words. That is forbidden: an ungrammatical story is
    rejected and the warm pool absorbs it."""
    assert DECODABILITY_GATE == 0.95


# --- the backfill: applying 4.12 backwards (DEVIATIONS 6.24-6.26) ------------
# The detection above was forward-only. `gen_meta->'moderation'->>'well_formed'`
# was NULL on all 28 stories already in the pool, because `warm.persist()` wrote
# three of the verdict's five fields and dropped the grammar half entirely.
#
# Everything below runs against REAL rows in the real database and drives the
# real `run_grammar_backfill`; only the Haiku call is stubbed, and it is stubbed
# per-test so the verdict under test is the input, not the assertion.

BF_KID_NAME = "Grammarfixture"
BF_BODIES = ["A dragon sat in a cave.", "He did not came back.", "The end."]
BF_USAGE = Usage(model="claude-haiku-4-5", input_tokens=120, output_tokens=20)

BF_GOOD = ModerationResult(
    approved=True, reason="fine", layer="model", usage=BF_USAGE, well_formed=True
)
BF_UNGRAMMATICAL = ModerationResult(
    approved=False,
    reason='ungrammatical: "He did not came back." (came -> come)',
    layer="model",
    usage=BF_USAGE,
    well_formed=False,
    grammar_note='"He did not came back." (came -> come)',
)


@pytest.fixture
async def db():
    async with SessionLocal() as s:
        yield s


@pytest.fixture
async def pool_story(db):
    """A throwaway servable story shaped exactly like a pre-4.12 pool row:
    segments, a `tap_word` question, an answer against it, and a Phase 3
    moderation receipt with no `well_formed` key."""
    theme_id = (
        await db.execute(text("SELECT id FROM themes WHERE slug = 'dragons'"))
    ).scalar_one()
    row = Story(
        stage_id=1,
        theme_id=theme_id,
        status="warm",
        title="The Dragon Fixture",
        decodability=1.0,
        story_words=["dragon"],
        # The Phase 3 shape: three keys, no grammar verdict. This is the state
        # every live pool row was in.
        gen_meta={"cost_usd": 0.01, "moderation": {"approved": True, "layer": "model",
                                                   "reason": "safe"}},
    )
    db.add(row)
    await db.flush()
    for i, body in enumerate(BF_BODIES):
        db.add(StorySegment(story_id=row.id, idx=i, body=body))
    question = Question(
        story_id=row.id,
        idx=0,
        qtype="tap_word",  # the only §8 mastery-bearing type
        prompt="Tap it: sat.",
        payload={"options": ["sat", "dragon", "cave"], "cue_word": "sat"},
        answer={"correct": "sat", "index": 0, "target_grapheme": "s"},
    )
    db.add(question)
    await db.flush()

    kid_id = (
        await db.execute(
            text("INSERT INTO kids (display_name, stage_id) VALUES (:n, 1) RETURNING id"),
            {"n": BF_KID_NAME},
        )
    ).scalar_one()
    reading_id = (
        await db.execute(
            text(
                "INSERT INTO readings (kid_id, story_id, stage_id, path, completed_at) "
                "VALUES (:k, :s, 1, CAST('{\"choices\": []}' AS jsonb), now()) RETURNING id"
            ),
            {"k": kid_id, "s": row.id},
        )
    ).scalar_one()
    await db.execute(
        text("INSERT INTO answers (reading_id, question_id, correct) VALUES (:r, :q, true)"),
        {"r": reading_id, "q": question.id},
    )
    await db.commit()

    story_id, question_id = row.id, question.id
    yield SimpleNamespace(id=story_id, question_id=question_id, kid_id=kid_id)

    await db.rollback()
    await db.execute(text("DELETE FROM stories WHERE id = :i"), {"i": story_id})
    await db.execute(text("DELETE FROM kids WHERE display_name = :n"), {"n": BF_KID_NAME})
    await db.commit()


def _stub_body_moderation(monkeypatch, verdict: ModerationResult) -> list[str]:
    """Replace the Haiku call `run_grammar_backfill` makes. Returns a call log,
    so "spent nothing on the second run" is counted rather than asserted."""
    seen: list[str] = []

    async def fake(body: str) -> ModerationResult:
        seen.append(body)
        return verdict

    monkeypatch.setattr(warm, "moderate", fake)
    return seen


async def _row(db, story_id: int) -> Story:
    db.expire_all()
    return await db.get(Story, story_id)


async def test_a_freshly_persisted_story_is_never_selected_by_the_backfill(db, pool_story):
    """DEVIATIONS 6.24, stated as the property that matters: once `persist()`
    records the grammar half, the sweep is a ONE-TIME repair. If the receipt were
    still three keys wide, every `make warm` would re-create the backlog."""
    selectable = lambda: db.execute(  # noqa: E731
        warm._UNGRADED_BODY_SQL,
        {"statuses": list(SERVABLE_STATUSES), "only": [pool_story.id]},
    )
    assert [r.id for r in (await selectable()).all()] == [pool_story.id]

    story = await _row(db, pool_story.id)
    story.gen_meta = {**story.gen_meta, "moderation": warm._body_verdict(BF_GOOD)}
    await db.commit()

    assert (await selectable()).all() == []


async def test_backfill_rejects_an_ungrammatical_body_and_records_the_sentence(
    db, pool_story, monkeypatch
):
    seen = _stub_body_moderation(monkeypatch, BF_UNGRAMMATICAL)
    await warm.run_grammar_backfill(only=[pool_story.id])

    # The text judged is the reconstructed body, not a fixture the test invented.
    assert seen == [" ".join(BF_BODIES)]

    story = await _row(db, pool_story.id)
    assert story.status == "rejected"
    assert story.status not in SERVABLE_STATUSES
    verdict = story.gen_meta["moderation"]
    assert verdict["well_formed"] is False
    # The offending sentence is named. Without this the rejection is unfalsifiable.
    assert "came back" in verdict["grammar_note"]
    assert verdict["source"] == "grammar-backfill"
    # The Phase 3 receipt is preserved, not overwritten.
    assert verdict["generation"] == {"approved": True, "layer": "model", "reason": "safe"}


async def test_backfill_never_deletes_mastery_evidence(db, pool_story, monkeypatch):
    """DEVIATIONS 6.26. `answers.question_id` is ON DELETE CASCADE and
    `questions.story_id` is too, so a DELETE here would take §8's only evidence
    with it. Rejecting the story must remove it from rotation and nothing else."""
    _stub_body_moderation(monkeypatch, BF_UNGRAMMATICAL)
    await warm.run_grammar_backfill(only=[pool_story.id])

    counts = (
        await db.execute(
            text(
                "SELECT (SELECT count(*) FROM questions WHERE story_id = :s), "
                "       (SELECT count(*) FROM answers WHERE question_id = :q), "
                "       (SELECT count(*) FROM readings WHERE story_id = :s)"
            ),
            {"s": pool_story.id, "q": pool_story.question_id},
        )
    ).first()
    assert tuple(counts) == (1, 1, 1)


async def test_a_rejected_body_stops_being_served(db, pool_story, monkeypatch, client):
    """The mechanism, exercised through the API rather than asserted about the
    column: `SERVABLE_STATUSES` is what `api/stories.py` filters on, so flipping
    `status` is the whole of "stop serving this story"."""
    assert (await client.get(f"/api/stories/{pool_story.id}")).status_code == 200

    _stub_body_moderation(monkeypatch, BF_UNGRAMMATICAL)
    await warm.run_grammar_backfill(only=[pool_story.id])

    assert (await client.get(f"/api/stories/{pool_story.id}")).status_code == 404
    # And a reading of it cannot be started either.
    resp = await client.post(
        "/api/readings", json={"kid_id": pool_story.kid_id, "story_id": pool_story.id}
    )
    assert resp.status_code == 404


async def test_a_well_formed_body_keeps_its_place_in_the_pool(db, pool_story, monkeypatch):
    _stub_body_moderation(monkeypatch, BF_GOOD)
    await warm.run_grammar_backfill(only=[pool_story.id])

    story = await _row(db, pool_story.id)
    assert story.status == "warm"
    assert story.gen_meta["moderation"]["well_formed"] is True


async def test_the_backfill_is_idempotent_and_re_spends_nothing(db, pool_story, monkeypatch):
    """Re-running must be a no-op. The selection predicate is `well_formed IS
    NULL`, so a judged story — approved or rejected — is out of the set."""
    first = _stub_body_moderation(monkeypatch, BF_GOOD)
    await warm.run_grammar_backfill(only=[pool_story.id])
    assert len(first) == 1

    second = _stub_body_moderation(monkeypatch, BF_GOOD)
    await warm.run_grammar_backfill(only=[pool_story.id])
    assert second == [], "the second run judged a story again — that is real money"


async def test_a_story_that_fails_both_halves_still_counts_as_a_grammar_failure(monkeypatch):
    """Observed on the live sweep: stories #11 and #20 came back with
    `approved=False` for a CONTENT reason and `well_formed=False` at the same
    time. `moderate()` folds both into one boolean and keeps the safety text in
    `reason`, so counting failures off `reason` would have reported "0
    ungrammatical" for a pool containing "He did not came back." — the exact
    sentence DEVIATIONS 4.12 exists because of."""
    both = ModerationVerdict(
        approved=False,
        reason="The man is hit with fire, which means he is injured.",
        well_formed=False,
        grammar_note='"He did not came back." (came -> come)',
    )
    _stub(monkeypatch, both)
    result = await moderate("He did not came back. The dragon gave him fire.")

    assert result.well_formed is False, "the grammar failure must survive the safety one"
    assert "came" in result.grammar_note
    assert warm._rejected_on_content(result) is True
    assert warm._grammar_note(result) == '"He did not came back." (came -> come)'


async def test_a_grammar_only_failure_is_not_counted_as_a_content_failure(monkeypatch):
    """The other direction: the model approved the CONTENT and only grammar
    failed. Reporting that as unsafe would misattribute why a story was pulled."""
    _stub(
        monkeypatch,
        ModerationVerdict(
            approved=True,
            reason="A gentle dragon story.",
            well_formed=False,
            grammar_note='"A dragon sit in a cave." (sit -> sits)',
        ),
    )
    result = await moderate("A dragon sit in a cave.")

    assert result.approved is False and result.well_formed is False
    assert warm._rejected_on_content(result) is False


async def test_story_body_round_trips_through_the_segment_split(db):
    """DEVIATIONS 6.25's losslessness claim, measured over the REAL pool rather
    than a fixture. `stories` never persisted `final_text`, so re-moderation has
    to rebuild the body from segments — and the rebuild is only trustworthy if
    re-splitting it returns the segments a child actually reads."""
    ids = (
        await db.execute(
            text("SELECT id FROM stories WHERE status = ANY(:st) ORDER BY id"),
            {"st": list(SERVABLE_STATUSES)},
        )
    ).scalars().all()
    assert ids, "no servable stories — run `make warm`"

    for story_id in ids:
        segments = (
            await db.execute(
                text("SELECT body FROM story_segments WHERE story_id = :i ORDER BY idx"),
                {"i": story_id},
            )
        ).scalars().all()
        body = await warm.story_body(db, story_id)
        assert warm._split_segments(body) == [s.strip() for s in segments], story_id


# --- the live fixture: both directions, per DEVIATIONS 3.10 ------------------
# (name, should_approve, body). Every should-approve case is a shape the format
# genuinely produces; every should-reject case is either real observed output or
# a documented failure from 3.10.
FIXTURE: list[tuple[str, bool, str]] = [
    (
        "approve/plain-register",
        True,
        "Sam sat. A cat sat. The cat sat on Sam. Sam is glad. The cat is glad. The end.",
    ),
    (
        "approve/fire-breathing-dragon",
        True,
        "The big dragon has fire in it. Puff! The fire is hot and red. "
        "The dragon can not nap in the den. Sam ran up the hill with a bag of ice. "
        "The dragon is glad. The end.",
    ),
    (
        "approve/dark-cave-scared-then-found",
        True,
        "Sam ran in the dark cave. It is black in the cave. Sam is scared. Sam hid. "
        "Then Mom came in with a lamp. Sam ran to Mom. Sam is not scared. The end.",
    ),
    (
        "approve/heavy-repetition",
        True,
        "Run, Tim, run. Run up. Run up the hill. Run down. Run down the hill. "
        "Run, run, run. Fun! Tim ran a lot. The end.",
    ),
    (
        "approve/space-abrupt-ending",
        True,
        "Meg has a red ship. The ship can go up. Up, up, up. Meg can see the sun. "
        "Meg can see a big rock in space. Meg went back down. The end.",
    ),
    # --- the five below are VERBATIM live warm-pool stories that the FIRST draft
    # of the grammar prompt wrongly rejected. They are the false-positive guard,
    # and each is genuinely well-formed English.
    (
        "approve/emphatic-did",  # story #10: "It did nap." is correct English
        True,
        "A dragon sat in a cave. It did nap. Sam sat amid sand. The dragon did spit fire. "
        "Sam did stand. Sam did pat the dragon. The dragon did sit. "
        "Sam did nap amid the sand.",
    ),
    (
        "approve/tense-shift-between-sentences",  # story #2
        True,
        "A dragon sits in a cave. It can spin. It can nap. The dragon taps its wings. "
        "Sam pats the dragon. Dan sat amid the sand. The dragon and Dan camp. "
        "Sam and Dan nap in the cave.",
    ),
    (
        "approve/plural-subject-present-verb",  # story #6: "wings help" is correct
        True,
        "A dragon sat in a cave. It had big wings. The dragon ran up a hill. It sat on top. "
        "Its wings help it. The dragon ran back to its cave.",
    ),
    (
        "approve/unbackshifted-reported-speech",  # story #26
        True,
        "The man got in the rocket. He said he will go to the moon. The rocket ran fast. "
        "Up, up, up it went. He was with the stars. The stars are fine. "
        "The rocket went to the moon. He came back home. The end.",
    ),
    (
        "approve/present-verb-among-past-sentences",  # story #22
        True,
        "Sam sat in a rocket. Sam and Bob go up fast. Up, up, to the moon. The moon is big. "
        "Sam ran on the moon. Bob got a rock. Back to a planet. Sam is glad.",
    ),
    # --- genuinely broken. All three are VERBATIM live output (DEVIATIONS 4.12).
    (
        "reject/ungrammatical-verb-form",
        False,
        "Sam went up the hill. Sam met a big man. The man had a hat. Sam ran back. "
        "He did not came back. Sam is sad. The end.",
    ),
    (
        "reject/ungrammatical-stray-word",
        False,
        "The dragon sat in the den. The dragon had a got time. It can not get up. "
        "Sam had fun with the dragon. The end.",
    ),
    (
        "reject/ungrammatical-subject-verb-agreement",  # story #9, live and servable
        False,
        "A dragon sit in a cave. It had fire. A man went in. The man ran off. "
        "The dragon had fun.",
    ),
    (
        "reject/ungrammatical-object-pronoun-as-subject",  # story #8, live and servable
        False,
        "He went to the cave. A dragon was in the cave. It had fire. He got sad. "
        "The dragon felt him. It did not spit fire. He sat with the dragon. "
        "The dragon and him ran up the hill. It was fun. He will help the dragon.",
    ),
    (
        # story #11. The mismatch is INSIDE one sentence ("sat and admits"), which
        # is the line between this and approve/present-verb-among-past-sentences.
        "reject/ungrammatical-coordinated-tense",
        False,
        "A dragon sat in a cave. It did nap. Dan can stand and tap. "
        "The dragon did spit fire. Dan sat and admits it acts mad. Dan pats the dragon. "
        "It acts sad.",
    ),
    (
        "reject/unsafe-cruelty",
        False,
        "The big kid hit Sam. Sam fell in the mud. The big kid did it again and ran off. "
        "Sam sat in the mud and cried. The end.",
    ),
    # The subtle one from DEVIATIONS 3.10 — peril left unresolved.
    (
        "reject/unsafe-unresolved-peril",
        False,
        "Sam ran in the dark. Sam ran and ran. The thing got Sam. The end.",
    ),
]


@pytest.mark.skipif(
    not os.getenv("STORYFORGE_LIVE_MODERATION"),
    reason="live Haiku calls: set STORYFORGE_LIVE_MODERATION=1",
)
async def test_live_moderation_fixture_both_directions():
    """Confirming the rejects disappeared is not a test — you have to run the
    approvals too, or you ship a 55% false-positive rate (DEVIATIONS 3.10)."""
    import asyncio

    results = await asyncio.gather(*(moderate(body) for _n, _e, body in FIXTURE))

    tp = tn = fp = fn = 0
    rows = []
    for (name, should_approve, _body), r in zip(FIXTURE, results, strict=True):
        ok = r.approved == should_approve
        if should_approve and r.approved:
            tn += 1  # correctly served
        elif should_approve and not r.approved:
            fp += 1  # false positive: a good story thrown away
        elif not should_approve and not r.approved:
            tp += 1  # correctly rejected
        else:
            fn += 1  # false negative: a bad story reaching a child
        rows.append(
            f"{'PASS' if ok else 'FAIL'}  {name:38s} "
            f"approved={str(r.approved):5s} well_formed={str(r.well_formed):5s} "
            f"layer={r.layer:9s} {r.reason}"
        )

    print("\n".join(["", *rows, ""]))
    print(
        f"correct rejects (TP)={tp}  correct approvals (TN)={tn}  "
        f"false positives (FP)={fp}  false negatives (FN)={fn}"
    )

    by_name = dict(zip((f[0] for f in FIXTURE), results, strict=True))
    # Every ungrammatical case must be caught by the GRAMMAR half specifically —
    # a lucky safety rejection would not generalise.
    for name, should_approve, _ in FIXTURE:
        if should_approve or not name.startswith("reject/ungrammatical"):
            continue
        assert by_name[name].well_formed is False, f"{name}: {by_name[name].reason}"
    # ...and no good story may be thrown away for a grammar reason.
    for name, should_approve, _ in FIXTURE:
        if should_approve:
            assert by_name[name].well_formed is True, f"{name}: {by_name[name].grammar_note}"

    assert fp == 0, "false positives — a good story was thrown away"
    assert fn == 0, "false negatives — a bad story would reach a child"
