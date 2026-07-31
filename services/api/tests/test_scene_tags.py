"""Phase 4: scene tagging and asset selection (§12).

The two things §12 actually promises are that the tag vocabulary is CLOSED and
that asset selection is DETERMINISTIC. Both are testable without an API call and
without a database, so they are tested that way; the live tagging path is proved
by the smoke run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from storyforge.llm.scene_tags import (
    MAX_TAGS_PER_SEGMENT,
    TAG_VOCABULARY,
    SceneTag,
    SegmentTagging,
    StoryTagging,
    _enforce_vocabulary,
    select_asset,
    select_story_assets,
)
from storyforge.seed import SEED_DIR, _rows


@dataclass
class FakeAsset:
    """Stand-in for `models.Asset` — `select_asset` is structural on purpose."""

    id: int
    tags: list[str] = field(default_factory=list)
    kind: str = "segment"


# --- the vocabulary is CLOSED (§12) ------------------------------------------
def test_vocabulary_matches_the_enum():
    assert TAG_VOCABULARY == {t.value for t in SceneTag}
    assert len(TAG_VOCABULARY) > 20, "too few tags to discriminate between assets"


def test_an_invented_tag_is_rejected_by_the_output_model():
    """§12: the LLM cannot invent tags. This is the first of two lines of
    defence — the Pydantic model the API decodes into."""
    with pytest.raises(ValidationError):
        SegmentTagging(idx=0, tags=["dragon_in_a_cave_at_night"])


def test_every_real_tag_is_accepted():
    entry = SegmentTagging(idx=0, tags=list(TAG_VOCABULARY))
    assert {str(t) for t in entry.tags} == TAG_VOCABULARY


def test_the_json_schema_carries_the_enum():
    """§12 says the vocabulary is "an enum in the JSON schema" — that is what
    constrains decoding. If it degraded to a bare string the model could emit
    anything and nothing would fail loudly."""
    schema = StoryTagging.model_json_schema()
    enums = [
        d["enum"] for d in schema.get("$defs", {}).values() if "enum" in d
    ]
    assert enums, "no enum in the generated schema — the vocabulary is not closed"
    assert set(enums[0]) == TAG_VOCABULARY


def test_a_tag_that_bypassed_the_schema_is_still_dropped():
    """Second line of defence. A guarantee that depends on the model honouring
    its schema is not a guarantee, so the result is re-checked post-hoc."""
    smuggled = StoryTagging.model_construct(
        segments=[
            SegmentTagging.model_construct(idx=0, tags=["cave", "dark_scary_hole"])
        ]
    )
    assert _enforce_vocabulary(smuggled, 1) == [["cave"]]


def test_an_out_of_range_index_is_ignored_rather_than_shifting_pictures():
    tagging = StoryTagging.model_construct(
        segments=[
            SegmentTagging(idx=7, tags=[SceneTag.CAVE]),
            SegmentTagging(idx=1, tags=[SceneTag.SKY]),
        ]
    )
    assert _enforce_vocabulary(tagging, 2) == [[], ["sky"]]


def test_a_segment_the_model_skipped_gets_no_tags():
    """No tags -> no asset -> striped placeholder (style guide §06). It must not
    inherit its neighbour's picture."""
    tagging = StoryTagging(segments=[SegmentTagging(idx=0, tags=[SceneTag.CAVE])])
    assert _enforce_vocabulary(tagging, 3) == [["cave"], [], []]


def test_tags_are_deduplicated_and_capped():
    tagging = StoryTagging.model_construct(
        segments=[
            SegmentTagging.model_construct(
                idx=0, tags=["cave", "cave", "dragon", "night", "stars", "moon"]
            )
        ]
    )
    tags = _enforce_vocabulary(tagging, 1)[0]
    assert tags == ["cave", "dragon", "night", "stars"][:MAX_TAGS_PER_SEGMENT]
    assert len(tags) == len(set(tags))


# --- selection: most overlap, deterministic tiebreak by id (§12) -------------
def test_picks_the_asset_with_the_most_tag_overlap():
    assets = [
        FakeAsset(1, ["cave"]),
        FakeAsset(2, ["cave", "dragon"]),
        FakeAsset(3, ["cave", "dragon", "sleeping"]),
        FakeAsset(4, ["sky", "flying"]),
    ]
    picked = select_asset(["cave", "dragon", "sleeping"], assets)
    assert picked.id == 3


def test_extra_asset_tags_do_not_count_against_it():
    """Overlap, not similarity — a richly tagged asset that covers the segment
    still wins over a sparse partial match."""
    assets = [
        FakeAsset(1, ["cave", "dragon"]),
        FakeAsset(2, ["cave", "dragon", "night", "stars", "moon", "castle"]),
    ]
    assert select_asset(["cave", "dragon"], assets).id == 1
    assert select_asset(["cave", "dragon", "night"], assets).id == 2


def test_ties_break_on_the_lowest_asset_id():
    assets = [
        FakeAsset(9, ["cave", "dragon"]),
        FakeAsset(4, ["cave", "dragon"]),
        FakeAsset(7, ["cave", "dragon"]),
    ]
    assert select_asset(["cave", "dragon"], assets).id == 4


def test_the_tiebreak_does_not_depend_on_pool_order():
    """Two children reading the same story must see the same picture, whatever
    order the database happened to return the rows in."""
    a, b, c = FakeAsset(9, ["cave"]), FakeAsset(4, ["cave"]), FakeAsset(7, ["cave"])
    for pool in ([a, b, c], [c, b, a], [b, c, a], [c, a, b]):
        assert select_asset(["cave"], pool).id == 4


def test_selection_is_repeatable():
    assets = [FakeAsset(i, ["cave", "dragon"]) for i in range(20, 0, -1)]
    picks = {select_asset(["cave", "dragon"], assets).id for _ in range(50)}
    assert picks == {1}


# --- the tiebreak breaks AWAY from a picture this story already used ---------
# The live defect (DEVIATIONS 26.1): lowest-id-always-wins is not a tiebreak
# between two pictures, it is a standing preference for one of them, and it
# repeats on every screen. Four servable stories rendered ONE picture on every
# page; asset 24 was on 160 segments across 32 stories.
def test_an_asset_already_used_in_this_story_loses_the_tie():
    assets = [FakeAsset(4, ["cave", "dragon"]), FakeAsset(7, ["cave", "dragon"])]
    assert select_asset(["cave", "dragon"], assets).id == 4
    assert select_asset(["cave", "dragon"], assets, avoid={4}).id == 7


def test_novelty_never_beats_overlap():
    """§12's rule is unchanged: most tag overlap wins, and novelty only decides
    between assets that match EQUALLY WELL. Demoting a better match because it
    has been seen would put a wrong picture on the page."""
    assets = [
        FakeAsset(1, ["cave", "dragon", "sleeping"]),
        FakeAsset(2, ["cave"]),
    ]
    assert select_asset(["cave", "dragon", "sleeping"], assets, avoid={1}).id == 1


def test_when_every_equal_match_is_spent_the_lowest_id_comes_back():
    """A story with more screens than pictures repeats rather than falling back
    to the placeholder — a repeat is worse than a fresh picture and far better
    than no picture."""
    assets = [FakeAsset(4, ["cave"]), FakeAsset(7, ["cave"])]
    assert select_asset(["cave"], assets, avoid={4, 7}).id == 4


def test_a_story_stops_showing_one_picture_on_every_page():
    """The live case, with the live rows: the animals pool holds exactly two
    rabbit pictures, and story 7291's seven screens were all asset 24."""
    pool = [
        FakeAsset(23, ["forest", "fox", "day"]),
        FakeAsset(24, ["forest", "rabbit", "hiding"]),
        FakeAsset(25, ["rabbit", "digging", "meadow"]),
    ]
    tags = [["rabbit", "forest"], ["rabbit"], ["rabbit", "friendly_creature"]]
    picks = select_story_assets(tags, pool)
    assert [p.id for p in picks] == [24, 25, 24]
    assert len({p.id for p in picks}) > 1


def test_select_story_assets_is_deterministic_and_keeps_the_placeholder():
    pool = [FakeAsset(1, ["cave"]), FakeAsset(2, ["cave"])]
    tags = [["cave"], [], ["water"], ["cave"]]
    first = select_story_assets(tags, pool)
    assert [p.id if p else None for p in first] == [1, None, None, 2]
    for _ in range(10):
        again = select_story_assets(tags, pool)
        assert [p.id if p else None for p in again] == [1, None, None, 2]


def test_a_cover_is_still_refused_when_the_segment_pool_is_exhausted():
    """§06's aspect-ratio rule outranks novelty: a 1:1 cover in the 4:3 frame is
    a real bug, and 'we have run out of segment art' is not a licence."""
    pool = [FakeAsset(1, ["cave"], kind="segment"), FakeAsset(2, ["cave"], kind="cover")]
    picks = select_story_assets([["cave"], ["cave"]], pool)
    assert [p.id for p in picks] == [1, 1]


# --- no match returns None so the UI can draw the placeholder (§06) ----------
def test_no_overlap_returns_none():
    assets = [FakeAsset(1, ["sky", "flying"]), FakeAsset(2, ["water", "fish"])]
    assert select_asset(["cave", "dragon"], assets) is None


def test_no_tags_returns_none():
    assert select_asset([], [FakeAsset(1, ["cave"])]) is None


def test_empty_pool_returns_none():
    assert select_asset(["cave"], []) is None


def test_an_untagged_asset_never_matches():
    assert select_asset(["cave"], [FakeAsset(1, [])]) is None


# --- kind: a 1:1 cover must not land in the 4:3 segment frame (§06) ----------
def test_covers_are_excluded_from_segment_selection():
    assets = [
        FakeAsset(1, ["dragon", "cave"], kind="cover"),
        FakeAsset(2, ["dragon"], kind="segment"),
    ]
    assert select_asset(["dragon", "cave"], assets).id == 2


def test_a_cover_only_pool_yields_the_placeholder_not_a_squashed_cover():
    assets = [FakeAsset(1, ["dragon", "cave"], kind="cover")]
    assert select_asset(["dragon", "cave"], assets) is None


def test_kind_none_ranks_across_every_kind():
    assets = [
        FakeAsset(1, ["dragon", "cave"], kind="cover"),
        FakeAsset(2, ["dragon"], kind="segment"),
    ]
    assert select_asset(["dragon", "cave"], assets, kind=None).id == 1


# --- the seeded pool (§12: ~8-12 per theme, §06: alt_text mandatory) ---------
ASSET_ROWS = _rows("assets.csv")


def test_every_seeded_tag_is_in_the_closed_vocabulary():
    """An out-of-vocabulary tag is silently unreachable, so it is a hard error."""
    for row in ASSET_ROWS:
        for tag in row["tags"].split("|"):
            assert tag in TAG_VOCABULARY, f"{row['url']}: unknown tag {tag!r}"


def test_every_seeded_asset_has_meaningful_alt_text():
    for row in ASSET_ROWS:
        alt = row["alt_text"].strip()
        assert len(alt) > 20, f"{row['url']}: alt_text too thin to help anyone"
        assert alt.endswith("."), f"{row['url']}: alt_text should be a sentence"


# ---------------------------------------------------------------------------
# §12's pool size: the FLOOR is a requirement, the ceiling was a transcription.
#
# This was one assertion, `8 <= n <= 12`, copied from §12 line 260 ("curated
# asset pool — ~8–12 images per theme"). The floor half is real and stays. The
# ceiling half was retired by the operator after DEVIATIONS 26.1-26.8 measured
# what it costs, and 28.1-28.6 paid for it in drawings.
#
# The reason it had to go is that §12 says two things that cannot both be true.
# It mandates a CLOSED 42-tag vocabulary that the scene-tagging node must emit
# from, and a pool of ~12 pictures per theme to answer them with. Measured on
# the live pool at 11 assets per theme: coverage was 22/20/15 of 42 tags, THREE
# tags (`climbing`, `eating`, `storm`) had no asset in any theme at all, 41
# servable animals screens asked for `cave` in a theme that owned no cave, and 5
# screens rendered §06's placeholder outright. The tilde in "~8–12" is on the
# pool size; there is no tilde on the vocabulary.
#
# So the ceiling is replaced, not deleted, by two assertions that bound the pool
# by what it can ANSWER instead of by how many files it is.
# ---------------------------------------------------------------------------

#: Distinct §12 tags each theme's SEGMENT pool can answer, measured after
#: DEVIATIONS 28.1-28.6. A RATCHET: curation may broaden a theme, never narrow
#: it. Lowering a number here is the change this file exists to make visible.
TAG_COVERAGE_FLOOR = {"animals": 28, "dragons": 23, "space": 15}


def _segment_tags_by_theme() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in ASSET_ROWS:
        if row["kind"].strip() != "segment":
            continue
        out.setdefault(row["theme"].strip(), set()).update(
            t.strip() for t in row["tags"].split("|") if t.strip()
        )
    return out


def test_no_theme_holds_fewer_than_section_12s_eight_pictures():
    """§12's FLOOR, unchanged. A theme below it cannot illustrate a story."""
    counts: dict[str, int] = {}
    for row in ASSET_ROWS:
        counts[row["theme"]] = counts.get(row["theme"], 0) + 1
    assert set(counts) == {"dragons", "space", "animals"}
    for theme, n in counts.items():
        assert n >= 8, f"{theme} has {n} assets, §12's floor is 8"


def test_no_theme_grows_without_broadening_what_it_can_answer():
    """The anti-sprawl bound that replaced §12's transcribed ceiling.

    A theme may hold no more SEGMENT pictures than the number of distinct tags
    it covers. That refuses the thing a raw count was standing in for — a pool
    that grows by adding pictures which answer nothing new — while permitting
    the thing the count forbade by accident, which is a SECOND picture for a tag
    the live pool asks for 41 times (the novelty tiebreak in `select_asset` has
    nothing to choose between until there are two).
    """
    tags = _segment_tags_by_theme()
    counts: dict[str, int] = {}
    for row in ASSET_ROWS:
        if row["kind"].strip() == "segment":
            counts[row["theme"]] = counts.get(row["theme"], 0) + 1
    for theme, n in counts.items():
        covered = len(tags[theme])
        assert n <= covered, (
            f"{theme} holds {n} segment pictures covering only {covered} tags — "
            "the pool is growing without broadening"
        )


def test_no_theme_covers_fewer_tags_than_it_did_when_this_was_measured():
    """The ratchet. Narrowing a theme's coverage is what the old ceiling would
    have quietly permitted, and it is the failure that actually hurts."""
    tags = _segment_tags_by_theme()
    for theme, floor in TAG_COVERAGE_FLOOR.items():
        assert len(tags[theme]) >= floor, (
            f"{theme} answers {len(tags[theme])} of the {len(TAG_VOCABULARY)} §12 "
            f"tags, down from the measured {floor}"
        )


def test_the_three_tags_no_theme_could_draw_are_drawable_now():
    """`climbing`, `eating` and `storm` were in the closed vocabulary the model
    is told to choose from and in NO asset row anywhere (DEVIATIONS 26.7).

    A tag the model may emit and the library cannot answer is not a neutral gap:
    every screen that picks it either falls to §06's placeholder or is answered
    by whichever of its OTHER tags happens to match, which is how a rabbit in a
    burrow ended up illustrating a screen about a mountain.
    """
    drawn = set().union(*_segment_tags_by_theme().values())
    for tag in ("climbing", "eating", "storm"):
        assert tag in drawn, f"{tag}: still in the vocabulary and in no drawing"


def test_each_theme_has_exactly_one_cover():
    covers = [r["theme"] for r in ASSET_ROWS if r["kind"] == "cover"]
    assert sorted(covers) == ["animals", "dragons", "space"]


def test_seeded_urls_are_unique():
    """`url` is the natural key the idempotent upsert is built on."""
    urls = [r["url"] for r in ASSET_ROWS]
    assert len(urls) == len(set(urls))


def test_every_seeded_asset_is_reachable_from_some_tag():
    """A segment asset with no tags could never be selected."""
    for row in ASSET_ROWS:
        if row["kind"] == "segment":
            assert row["tags"].strip(), f"{row['url']}: segment asset has no tags"


# --- database invariants after `make seed` -----------------------------------
@pytest.fixture
async def session():
    from storyforge.db import SessionLocal

    async with SessionLocal() as s:
        yield s


async def test_assets_are_seeded(session):
    n = (await session.execute(text("SELECT count(*) FROM assets"))).scalar_one()
    assert n == len(ASSET_ROWS), "run `make seed`"


async def test_seeding_twice_does_not_duplicate_assets(session):
    """`url` is the natural key; a second `make seed` must update, not append."""
    rows = await session.execute(
        text("SELECT url FROM assets GROUP BY url HAVING count(*) > 1")
    )
    assert not rows.all()


async def test_no_asset_row_is_missing_alt_text(session):
    rows = await session.execute(
        text("SELECT id FROM assets WHERE alt_text IS NULL OR btrim(alt_text) = ''")
    )
    assert not rows.all()


async def test_no_asset_row_carries_an_out_of_vocabulary_tag(session):
    rows = await session.execute(
        text(
            "SELECT DISTINCT unnest(tags) AS tag FROM assets "
            "WHERE NOT (tags <@ CAST(:vocab AS text[]))"
        ),
        {"vocab": list(sorted(TAG_VOCABULARY))},
    )
    assert not rows.all()


# ---------------------------------------------------------------------------
# The pool's art exists on disk (DEVIATIONS 11.20-11.23).
#
# For the whole life of this project `assets.csv` seeded 33 rows, `scene_tags.py`
# tagged 839 segments, the logs said "7 with art" — and there were ZERO image
# files in the repository. Every `<img>` 404'd, `SegmentArt.onError` demoted
# every segment to the striped placeholder, and nothing anywhere raised, logged
# or measured it. These two tests are the tripwire that was missing.
# ---------------------------------------------------------------------------

#: `SEED_DIR` is `<repo>/data/seed`, so two parents up is the repo root.
WEB_PUBLIC = SEED_DIR.parents[1] / "apps/web/public"


def test_every_asset_row_has_a_file_on_disk():
    """A url pointing at nothing renders the placeholder forever, silently."""
    missing = []
    for row in ASSET_ROWS:
        url = row["url"].strip()
        assert url.startswith("/assets/"), f"{url}: not under the /assets mount"
        candidate = WEB_PUBLIC / url.lstrip("/")
        if not candidate.is_file():
            missing.append(url)
    assert not missing, (
        "assets.csv rows with no art file: "
        + ", ".join(missing)
        + " — run `node scripts/generate-art.mjs`"
    )


def test_every_asset_url_is_version_pinned():
    """`main.py` serves /assets with `immutable, max-age=1y`.

    That is only safe for content-addressed names, and files copied from
    `public/` are not hashed by Vite. So the version lives in the path and a
    redrawn pool becomes `/assets/art/v2/...` — nothing is ever revised in place
    (DEVIATIONS 11.23).
    """
    unpinned = [r["url"] for r in ASSET_ROWS if "/art/v" not in r["url"]]
    assert not unpinned, unpinned


def test_assets_csv_has_no_duplicate_picture():
    """`(theme, kind, tags)` is the natural key `seed_assets` upserts on.

    Two rows with the same identity are not merely redundant under that key —
    the second silently overwrites the first's url (DEVIATIONS 11.22).
    """
    seen: dict[tuple[str, str, str], str] = {}
    for row in ASSET_ROWS:
        key = (
            row["theme"].strip(),
            row["kind"].strip(),
            "|".join(sorted(t.strip() for t in row["tags"].split("|") if t.strip())),
        )
        assert key not in seen, f"{seen.get(key)!r} and {row['url']!r} are the same picture"
        seen[key] = row["url"]
