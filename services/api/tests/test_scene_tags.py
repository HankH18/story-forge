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


def test_each_seeded_theme_has_eight_to_twelve_assets():
    counts: dict[str, int] = {}
    for row in ASSET_ROWS:
        counts[row["theme"]] = counts.get(row["theme"], 0) + 1
    assert set(counts) == {"dragons", "space", "animals"}
    for theme, n in counts.items():
        assert 8 <= n <= 12, f"{theme} has {n} assets, §12 wants 8-12"


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
