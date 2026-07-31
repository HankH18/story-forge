"""§13 over the story TITLE (DEVIATIONS 17.1).

The README promises "Every child-facing string passes three independent layers —
a deterministic blocklist, allowlist-constrained generation, and a moderation
pass." The title passed **none** of them.

    layer 1  `blocklist_check` is called from inside `moderate()`, on whatever
             `moderate()` was handed — and `run_pipeline` handed it `body`.
    layer 2  `validate_draft` is called on `draft_text(draft)`, and
             `draft_text` joins `segment.text`. The title is not in it. The
             schema's "from the permitted vocabulary" (llm/generate.py) is an
             instruction to an untrusted generator (§0), not a constraint —
             measured over the live pool, 28 of 119 servable titles use a word
             that would fail the very gate their own body had to clear.
    layer 3  `moderate(body)`. Same string, same omission.

Nothing rendered `story.title` in kid mode on the day this was found, which is
what made it latent rather than live — but it is persisted to `stories.title`,
returned on every `StoryDTO` (`api/stories.py`), cached into the reader's own
`localStorage` (`apps/web/src/lib/lastStory.ts`), and rendered on the parent
dashboard. It was one `<h1>{story.title}</h1>` away from being unmoderated model
text in front of a five-year-old, and the README's claim was false either way.

The fix is `child_facing_text()` — the title joins the ONE existing moderation
call. Every test below fails if that call goes back to taking `body`.

**What is deliberately NOT done here: the title is not gated on decodability.**
§10 is the only line in the build doc that requires a rendered string to pass the
validator, and it names one thing: "Choice labels must themselves pass validation
(they're rendered text)." The doc never mentions titles at all. Inventing that
requirement would reject roughly a quarter of all generation to enforce a rule
nobody wrote — `test_the_title_is_deliberately_not_gated_on_decodability` pins
the non-requirement so the next reader does not have to re-derive it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from storyforge import pipeline as pl
from storyforge.api.stories import SERVABLE_STATUSES
from storyforge.db import SessionLocal
from storyforge.llm.generate import StoryDraft, StorySegment
from storyforge.llm.moderate import TITLE_LABEL, blocklist_check, child_facing_text
from storyforge.llm.moderate import moderate as real_moderate
from storyforge.llm.pricing import Usage

pytestmark = pytest.mark.anyio


@pytest.fixture
async def session():
    async with SessionLocal() as s:
        yield s


#: 100% decodable at stage 1 with "the" as a sight word — the same fixture
#: `tests/test_obs.py` uses, so the gate is real and only the title varies.
GOOD = "Sam sat. The cat sat."

GEN_USAGE = Usage(model="claude-sonnet-5", input_tokens=120, output_tokens=60)


def _draft(title: str, body: str = GOOD) -> StoryDraft:
    return StoryDraft(
        title=title,
        page_plan=["sam sits", "the cat sits"],
        segments=[
            StorySegment(text=part.strip() + ".") for part in body.split(".") if part.strip()
        ],
        story_words_used=[],
    )


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Real validator, real gate, real `child_facing_text`; fake generation.

    Returns a one-element list holding the title the fake generator will emit, so
    a test can set it. Moderation is left alone deliberately — each test decides
    whether to capture it or run the real one.
    """
    vocab = pl.Vocabulary(
        stage_id=1,
        stage_name="Stage 1",
        target_min=10,
        target_max=40,
        allowed_words=["sam", "sat", "cat", "mat"],
        sight_words=["the"],
        story_words={},
    )
    title = ["Sam And The Cat"]

    async def fake_load(_session, _stage_id, _theme_slug):
        return vocab

    async def fake_call(*_args, **_kwargs):
        return _draft(title[0]), GEN_USAGE

    monkeypatch.setattr(pl, "load_vocabulary", fake_load)
    monkeypatch.setattr(pl, "_call", fake_call)
    return title


# --- the pure function -------------------------------------------------------
def test_the_title_is_in_the_string_section_13_judges():
    judged = child_facing_text("Sam And The Cat", GOOD)
    assert "Sam And The Cat" in judged
    assert GOOD in judged
    # Labelled, not bare: the same call carries the grammar half of the verdict
    # (4.12), and a bare noun phrase read as prose invites a `well_formed = false`
    # that would cost the story.
    assert judged.startswith(TITLE_LABEL)


def test_a_story_with_no_title_is_judged_byte_for_byte_as_before():
    """The no-regression half. `moderate()`'s input is unchanged for a titleless
    story, so nothing this commit does can move an existing verdict by accident —
    which matters because STATE invariant 5 says every past change to this judge
    broke a case that had been passing."""
    assert child_facing_text("", GOOD) == GOOD
    assert child_facing_text("   \n  ", GOOD) == GOOD


def test_the_title_label_is_not_itself_blocklisted():
    """The label is our word, not the model's. If it were a layer-1 entry, every
    story would die at layer 1 and the blocklist would be reporting on us."""
    assert blocklist_check(TITLE_LABEL) == []
    assert blocklist_check(child_facing_text("Sam And The Cat", GOOD)) == []


# --- the wiring: layer 3 ------------------------------------------------------
async def test_the_pipeline_hands_the_title_to_the_moderation_node(stub_pipeline, monkeypatch):
    """The test that would have caught it. `run_pipeline` must moderate the
    title, not just the prose."""
    seen: list[str] = []

    async def capture(text_: str):
        seen.append(text_)
        return pl.ModerationResult(approved=True, reason="fine", layer="model")

    stub_pipeline[0] = "Sam And The Big Cat"
    monkeypatch.setattr(pl, "moderate", capture)

    result = await pl.run_pipeline(None, 1, "dragons")

    assert result.passed is True
    assert len(seen) == 1
    assert "Sam And The Big Cat" in seen[0], "the title never reached §13's layer 3"
    assert GOOD in seen[0], "the body must still be judged"


# --- the wiring: layer 1 ------------------------------------------------------
async def test_a_blocklisted_word_in_the_title_alone_kills_the_story(stub_pipeline, monkeypatch):
    """§13 layer 1 through the REAL `moderate()`: deterministic, free, and it
    cannot be talked out of it. The body here is spotless; only the title is not,
    and before 17.1 this story was persisted `warm` with that title intact."""

    def no_client():  # pragma: no cover - called only if layer 1 fails to fire
        raise AssertionError("layer 1 must short-circuit before any API call")

    stub_pipeline[0] = "The Cat Will Kill"
    monkeypatch.setattr(pl, "moderate", real_moderate)
    monkeypatch.setattr("storyforge.llm.moderate.get_client", no_client)

    result = await pl.run_pipeline(None, 1, "dragons")

    assert result.passed is False
    assert result.moderation is not None
    assert result.moderation.layer == "blocklist"
    assert "kill" in result.moderation.blocked_words
    # The body was and remains clean — the rejection is entirely about the title.
    assert blocklist_check(GOOD) == []


async def test_a_clean_title_still_costs_no_api_call_at_layer_1(stub_pipeline, monkeypatch):
    """The inverse direction, so the test above cannot pass by rejecting
    everything: a clean title must fall THROUGH layer 1 to the judge."""
    stub_pipeline[0] = "Sam And The Cat"
    monkeypatch.setattr(pl, "moderate", real_moderate)
    monkeypatch.setattr(
        "storyforge.llm.moderate.get_client",
        lambda: (_ for _ in ()).throw(RuntimeError("reached layer 3")),
    )

    with pytest.raises(RuntimeError, match="reached layer 3"):
        await pl.run_pipeline(None, 1, "dragons")


# --- the non-requirement ------------------------------------------------------
async def test_the_title_is_deliberately_not_gated_on_decodability(stub_pipeline, monkeypatch):
    """§10: "Choice labels must themselves pass validation (they're rendered
    text)." That is the build doc's ONLY validation requirement on a rendered
    string, and it names choice labels. The doc never mentions a title.

    So an undecodable title is not a defect and must not reject the story. "Goes"
    needs `oe`, which no stage teaches at 1; the live pool holds nine titles
    exactly like this one. If someone later decides titles must be decodable, this
    test is the place to argue it — not a silent tightening of `passes_gate`,
    which reports a §1 acceptance metric over the BODY.
    """
    stub_pipeline[0] = "Sam Goes To The Moon"

    async def approve(_text):
        return pl.ModerationResult(approved=True, reason="fine", layer="model")

    monkeypatch.setattr(pl, "moderate", approve)

    result = await pl.run_pipeline(None, 1, "dragons")

    assert result.passed is True
    assert result.title == "Sam Goes To The Moon"
    # And the story's reported decodability is still a fact about the BODY alone.
    assert result.final_report.decodability == 1.0


# --- the BACKLOG --------------------------------------------------------------
async def test_the_live_pool_holds_no_servable_story_with_a_blocklisted_title(session):
    """Fixing the source does not fix the rows already written.

    This project has been burned by that four times — the grammar verdict that was
    computed and discarded on write (6.24), the retired prompt frames nothing
    re-derived (11.82-11.85), the mcq2 repair that was written and never run
    (11.99-11.103), the archive erased by its own backfill (11.117). Every servable
    title in the pool today was generated before §13 could see one, so the property
    has to be asserted over the ROWS, not only over the next generation.

    Layer 1 only, and that is the honest boundary: it is a pure function over
    `stories.title`, so this costs one query and no API call and can run on every
    commit. **Layer 3 over the backlog is NOT covered here** — re-judging 119
    titles is a Haiku spend, and an unrun sweep asserted as done is the exact
    failure this docstring lists four instances of. Measured at the time of
    writing: 0 of 119 servable titles carry a layer-1 hit.
    """
    rows = await session.execute(
        text("SELECT id, stage_id, title FROM stories WHERE status = ANY(:statuses) ORDER BY id"),
        {"statuses": list(SERVABLE_STATUSES)},
    )
    hits = [(r.id, r.title, blocklist_check(r.title)) for r in rows]
    blocked = [h for h in hits if h[2]]
    assert blocked == [], (
        f"{len(blocked)} servable story/stories carry a blocklisted TITLE, which no "
        f"layer looked at before 17.1: {blocked}. These rows predate the fix — reject "
        "them by id rather than editing the title in place, because the title is the "
        "model's output and a hand-edited one has passed no layer either."
    )
