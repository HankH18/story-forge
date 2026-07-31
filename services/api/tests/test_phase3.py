"""Phase 3: moderation layering, segment splitting, warm-pool integrity.

The live generation path is proved by `make eval-smoke` and `make warm` — these
cover the pure logic and the DB invariants around them.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge.llm.moderate import blocklist_check
from storyforge.warm import _split_segments


# --- moderation layer 1: deterministic, free, cannot be argued with ----------
def test_blocklist_catches_violence_without_an_api_call():
    hits = blocklist_check("The man had a gun. He shot it and the dragon was dead.")
    assert set(hits) >= {"gun", "shot", "dead"}


def test_blocklist_is_case_and_punctuation_insensitive():
    """Tokenised like the validator, so "Kill." and "kill" are the same word."""
    assert "kill" in blocklist_check("Kill! The man will Kill.")


def test_blocklist_does_not_substring_match():
    """docs/DEVIATIONS.md 2.4 — Scunthorpe. "assassin" must not flag via "ass"."""
    assert blocklist_check("Sam ran to the class and sat on the grass.") == []


def test_clean_story_passes_layer_one_free():
    assert blocklist_check("A dragon sat in a cave. Sam was glad.") == []


# --- segment splitting (§10: max 7 screens) ----------------------------------
def test_segments_split_on_sentences():
    segs = _split_segments("Sam sat. A cat ran. The dog was glad.")
    assert segs == ["Sam sat.", "A cat ran.", "The dog was glad."]


def test_segments_respect_the_seven_screen_cap():
    body = " ".join(f"Sentence {i} here." for i in range(20))
    segs = _split_segments(body)
    assert len(segs) == 7


def test_over_cap_merges_the_tail_rather_than_truncating():
    """Truncating would silently drop story text — the child would hit a
    non-ending. The tail is merged into the last screen instead."""
    body = " ".join(f"S{i}." for i in range(12))
    segs = _split_segments(body)
    assert "S11" in segs[-1]
    assert all(f"S{i}" in " ".join(segs) for i in range(12))


def test_single_sentence_is_one_segment():
    assert _split_segments("Sam sat.") == ["Sam sat."]


# --- warm pool DB invariants -------------------------------------------------
@pytest.fixture
async def session():
    from storyforge.db import SessionLocal

    async with SessionLocal() as s:
        yield s


async def test_pool_has_warm_stories(session):
    n = (
        await session.execute(text("SELECT count(*) FROM stories WHERE status='warm'"))
    ).scalar_one()
    assert n > 0, "run `make warm`"


async def test_every_warm_story_meets_the_gate(session):
    """§7: nothing below the gate may sit in the pool waiting to be served."""
    rows = await session.execute(
        text("SELECT id, decodability FROM stories WHERE status='warm' AND decodability < 0.95")
    )
    assert not rows.all()


async def test_every_warm_story_passed_moderation(session):
    """A story can clear decodability and still be unfit for a child."""
    rows = await session.execute(
        text(
            "SELECT id FROM stories WHERE status='warm' "
            "AND gen_meta->'moderation'->>'approved' <> 'true'"
        )
    )
    assert not rows.all()


async def test_warm_stories_respect_the_story_word_cap(session):
    rows = await session.execute(
        text(
            "SELECT id, story_words FROM stories "
            "WHERE status='warm' AND array_length(story_words, 1) > 3"
        )
    )
    assert not rows.all()


async def test_every_warm_story_has_segments(session):
    """A story with no segments would render as a blank screen in kid mode."""
    rows = await session.execute(
        text(
            "SELECT s.id FROM stories s LEFT JOIN story_segments g ON g.story_id = s.id "
            "WHERE s.status='warm' GROUP BY s.id HAVING count(g.id) = 0"
        )
    )
    assert not rows.all()


# Every pull path, as `(does this row carry the record, when does it say)`. One
# table, because the comment above this used to promise that "a new path is one
# entry here" while assertion 4 separately re-spelled the same key names — so a
# fourth path was really two edits, and the one an author would forget is the
# assertion that a record must be DATED. Now both expressions are derived from
# this, and forgetting is not available.
#
# Kept as SQL rather than Python because assertions 1 and 2 must ask the DATABASE
# — a Python-side coalesce would only see rows a query had already selected.
#
# The presence test differs by shape and that is not an oversight: `moderation`
# exists on every story, so its pull record is the FIELD `pulled_at`; a quarantine
# is a top-level object whose very presence means "pulled" (DEVIATIONS 11.117 —
# such a key must never live under `enrichment`, which `_record_enrichment`
# replaces wholesale).
_PULL_PATHS = (
    ("gen_meta->'moderation' ? 'pulled_at'", "gen_meta->'moderation'->>'pulled_at'"),
    ("gen_meta ? 'vocabulary_quarantine'", "gen_meta->'vocabulary_quarantine'->>'at'"),
    # DEVIATIONS 31.3 — `storyforge.quarantine_story_words`, the far-chip half of
    # §5.5 that `--story-word-repair` structurally cannot fix.
    ("gen_meta ? 'story_word_quarantine'", "gen_meta->'story_word_quarantine'->>'at'"),
    # DEVIATIONS 19.6 — §10's fit quarantine. A body over STORY_CHAR_BUDGET
    # cannot be shown on 7 screens at ANY pagination, so `--refit-segments`
    # structurally cannot repair it and the row is pulled for regeneration.
    ("gen_meta ? 'fit_quarantine'", "gen_meta->'fit_quarantine'->>'at'"),
)

#: When was this row pulled from the pool, under ANY path.
_PULLED_AT = f"coalesce({', '.join(value for _, value in _PULL_PATHS)})"

#: A recognised pull record that carries no time. See assertion 4.
_UNDATED = " OR ".join(f"({present} AND {value} IS NULL)" for present, value in _PULL_PATHS)


async def test_rejected_stories_are_kept_but_never_warm(session):
    """Rejects are eval evidence for why a pair is hard — kept, never served.

    `justify-test-edit`, run 2026-07-31 (THIRD time, FOURTH pull path). VERDICT:
    THE TEST IS RIGHT AND THE NEW CODE WAS WRONG — which is the opposite of the
    two verdicts below and is why the note is worth keeping. DEVIATIONS 31.3 added
    `storyforge.quarantine_story_words`, and its first version wrote a pull record
    with a reason and NO `at`. Assertion 1 went red on twelve rows. The remedy was
    entirely in the new code: it now writes `at`, and the twelve rows already
    written were backfilled. Only THEN was the key registered in `_PULL_PATHS`,
    and registering it without the timestamp is still red — verified by sabotage,
    at assertion 4's line, which is the whole reason assertion 4 exists.

    The one structural change: `_PULL_PATHS` now drives both `_PULLED_AT` and
    assertion 4's `_UNDATED`, because the comment promising "a new path is one
    entry here" was false — assertion 4 re-spelled the key names, so a new path
    was two edits and the forgettable one was the dated check. Nothing is
    weakened: the same three questions are asked of a strictly larger set of pull
    paths.

    `justify-test-edit`, run 2026-07-30 (SECOND time, third pull path). VERDICT:
    THE TEST IS AMBIGUOUS AGAIN, for the same structural reason and not a new
    one. `--check-vocabulary`'s quarantine (DEVIATIONS 22.x) pulls stories whose
    words left `allowed_words` after they were generated, and records
    `gen_meta.vocabulary_quarantine.at`. Thirteen rows were legitimately served
    while warm and then pulled — indistinguishable, under the old assertion,
    from `_SERVE_SQL` leaking a rejected story to a child.

    Checked before editing, because the alternative reading was a live safety
    failure: 0 rejected+served rows lack a pull time under ANY path, and 0 were
    served AFTER their pull time. So this is the ambiguity, not the leak.

    The 2026-07-29 note below fixed this once by making the sweep record
    `pulled_at`. That fix does not generalise, because it named ONE producer;
    the third producer reintroduced the ambiguity by writing a different key.
    So the shape of the fix changes: the invariant now asks "did SOMETHING
    record pulling this row", via `_PULLED_AT`, which is what it always meant.
    Assertion 4 is the price of that generalisation and is strictly new.

    `justify-test-edit`, run 2026-07-29. VERDICT: CODE IS WRONG (the sweep was
    not recording *when* it pulled a story); the assertion below is then narrowed
    to the population it can still speak about and a strictly stronger one is
    added beside it.

    ORIGIN: commit e0f3267 (Phase 3), where `warm.persist()` was the ONLY
    producer of `status='rejected'`. Under that closed world "a rejected story
    was never served" and "`last_served_at IS NULL`" were the same statement, so
    the cheap data check stood in for the child-safety invariant.

    They are no longer the same statement. `--grammar-backfill` (DEVIATIONS
    6.25-6.26) re-runs §13 over bodies already in the pool and pulls the
    ungrammatical ones, so `rejected` is now reachable from `warm`/`complete` as
    well as at birth — which `models.Story`'s own docstring already allowed
    ("failed the gate **or moderation**") and which `_remoderate_questions` had
    already established for question rows. Those four stories were served,
    legitimately, while they were still warm.

    So the old assertion was not too strict; it was ambiguous — it could no
    longer distinguish "`_SERVE_SQL` leaked a rejected story" (a child-safety
    failure) from "a served story was later pulled" (this feature working). The
    fix is in the CODE: the sweep records `pulled_at`, and the question becomes
    answerable again. Both halves are asserted below, and neither is weaker than
    what was here before.
    """
    rows = await session.execute(
        text("SELECT count(*) FROM stories WHERE status='rejected'")
    )
    assert rows.scalar_one() >= 0  # presence is fine; the invariant is below

    # 1. Rejected at GENERATION — never in the pool, so a serve stamp with NO
    #    pull record under any path still means `_SERVE_SQL` leaked a rejected
    #    row. Same question as the original; it just no longer assumes that
    #    moderation is the only thing that can pull.
    bad = await session.execute(
        text(
            "SELECT id FROM stories WHERE status='rejected' "
            f"AND last_served_at IS NOT NULL AND {_PULLED_AT} IS NULL"
        )
    )
    assert not bad.all()

    # 2. Pulled from the pool later — may carry a serve stamp, but only from
    #    BEFORE it was pulled. A serve after the pull is the same leak.
    served_after = await session.execute(
        text(
            "SELECT id FROM stories WHERE status='rejected' "
            f"AND {_PULLED_AT} IS NOT NULL "
            f"AND last_served_at > ({_PULLED_AT})::timestamptz"
        )
    )
    assert not served_after.all()

    # 4. STRICTLY NEW, and the reason generalising 1 and 2 is not a relaxation.
    #    `_PULLED_AT` coalesces, so a FOURTH pull path that records a reason but
    #    no timestamp reads as NULL — and on an unserved row that NULL is
    #    invisible to both assertions above. This sees it: any recognised pull
    #    record must carry its own time, served or not.
    undated = await session.execute(
        text(f"SELECT id FROM stories WHERE status='rejected' AND ({_UNDATED})")
    )
    assert not undated.all()

    # 3. Every pulled row names why, so a story is never out of rotation for a
    #    reason nobody recorded.
    unexplained = await session.execute(
        text(
            "SELECT id FROM stories WHERE gen_meta->'moderation'->'pulled_at' IS NOT NULL "
            "AND (status <> 'rejected' "
            "     OR coalesce(gen_meta->'moderation'->>'reason', '') = '')"
        )
    )
    assert not unexplained.all()
