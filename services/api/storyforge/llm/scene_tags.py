"""Scene-tagging node and asset selection (§12).

§12 is specific about the mechanism, and the specificity is the point:

    SCENE_TAGGING picks tags per segment from the *closed tag vocabulary*
    (enum in the JSON schema — the LLM cannot invent tags); segment renders
    the best-matching asset (most tag overlap, deterministic tiebreak by
    asset id).

An open vocabulary would look like it worked. The model would happily emit
`"dragon_in_a_cave_at_night"`, every asset would score zero overlap, and every
segment would silently fall back to the placeholder — a failure that never
raises and never shows up in a metric. So the vocabulary is an enum **in the
JSON schema**, which constrains decoding itself, and `_enforce_vocabulary`
re-checks the result afterwards. The second check is not redundant: the closed
vocabulary is a §12 guarantee, and a guarantee that depends on the model
honouring its schema is not a guarantee.

`select_asset` is a pure function for the same reason the validator is: given
the same tags and the same pool it must always pick the same picture, or two
readers of the same story see different books.

Style guide §06 governs what the pool contains — 4:3 segment illustrations,
1:1 theme covers, 1:1 transparent story-word cutouts, `alt_text` mandatory on
every row, and when tag matching finds nothing the UI renders the striped
placeholder. `select_asset` returning `None` is how the UI is told to do that;
it is a normal outcome, not an error.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from storyforge.llm.client import FAST_TIMEOUT_S, fast_model, get_client
from storyforge.llm.pricing import Usage, usage_from_response


class SceneTag(StrEnum):
    """The CLOSED tag vocabulary (§12).

    This enum is rendered directly into the JSON schema, so it is simultaneously
    the model's only legal output and the only thing an asset row may be tagged
    with. Adding a tag here without adding assets tagged with it is harmless
    (it just never matches); tagging an asset with a string that is NOT here is
    a seed-time error, because that asset becomes permanently unreachable.

    Kept deliberately small and concrete. Tags describe what is *visible in the
    picture*, not how the story feels — "a dragon asleep in a cave" is
    `dragon, sleeping, cave`, and an illustrator can draw exactly that.
    """

    # --- where it happens -----------------------------------------------
    CAVE = "cave"
    FOREST = "forest"
    MOUNTAIN = "mountain"
    CASTLE = "castle"
    VILLAGE = "village"
    MEADOW = "meadow"
    WATER = "water"
    PATH = "path"
    SKY = "sky"
    SPACE = "space"

    # --- what is in the sky ---------------------------------------------
    PLANET = "planet"
    MOON = "moon"
    STARS = "stars"
    SPACESHIP = "spaceship"

    # --- when / weather --------------------------------------------------
    NIGHT = "night"
    DAY = "day"
    STORM = "storm"
    SNOW = "snow"

    # --- who is in it ----------------------------------------------------
    DRAGON = "dragon"
    KNIGHT = "knight"
    ASTRONAUT = "astronaut"
    FRIENDLY_CREATURE = "friendly_creature"
    BIRD = "bird"
    FISH = "fish"
    CAT = "cat"
    DOG = "dog"
    FOX = "fox"
    RABBIT = "rabbit"

    # --- what they are doing ---------------------------------------------
    FLYING = "flying"
    RUNNING = "running"
    SLEEPING = "sleeping"
    HIDING = "hiding"
    SWIMMING = "swimming"
    CLIMBING = "climbing"
    DIGGING = "digging"
    EATING = "eating"
    HELPING = "helping"

    # --- story beats ------------------------------------------------------
    FIRE = "fire"
    TREASURE = "treasure"
    LOST = "lost"
    FOUND = "found"
    CELEBRATING = "celebrating"


#: Membership test for seeding and for the post-hoc check. Plain strings, so
#: callers (the CSV seeder, the DB) never need to import the enum.
TAG_VOCABULARY: frozenset[str] = frozenset(t.value for t in SceneTag)

#: More than a few tags per segment stops discriminating between assets — every
#: asset overlaps a little and the ranking flattens.
MAX_TAGS_PER_SEGMENT = 4

SCENE_TAG_SYSTEM = f"""You label illustrations for a children's storybook.

You are given the screens of one short story. For each screen, choose the tags \
that describe what an illustrator would DRAW for that screen — the setting, who \
is present, and what they are doing.

Rules:
1. Choose tags ONLY from the provided list. There are no other options.
2. Choose at most {MAX_TAGS_PER_SEGMENT} tags per screen, most important first.
3. Tag what is actually on THIS screen, not what happened earlier in the story.
4. Prefer fewer, accurate tags over more, loose ones. If a screen is just \
dialogue or feeling with nothing to draw, return an empty list.
5. Return one entry per screen, with the screen's index, in order.

Do not explain your choices."""


class SegmentTagging(BaseModel):
    """Tags for one screen. `idx` is echoed back so a short or reordered
    response can be realigned to the input instead of silently shifting every
    picture by one."""

    idx: int = Field(description="The index of the screen these tags describe.")
    tags: list[SceneTag] = Field(
        description="Tags from the closed vocabulary, most important first."
    )


class StoryTagging(BaseModel):
    segments: list[SegmentTagging] = Field(description="One entry per screen, in order.")


class SceneTagResult(BaseModel):
    """Per-segment tags, aligned 1:1 with the segments that were passed in."""

    tags: list[list[str]]
    usage: Usage | None = None
    #: True when the model call produced nothing usable. Every segment then has
    #: no tags, `select_asset` returns None, and the UI renders the striped
    #: placeholder (style guide §06). Illustration is decorative — a tagging
    #: failure must not fail the story.
    degraded: bool = False


def build_tagging_request(theme: str, segments: Sequence[str]) -> str:
    numbered = "\n".join(f"[{i}] {body}" for i, body in enumerate(segments))
    return (
        f"Theme: {theme}\n\n"
        f"TAGS you may use ({len(TAG_VOCABULARY)}):\n"
        + ", ".join(sorted(TAG_VOCABULARY))
        + f"\n\nSTORY ({len(segments)} screens):\n{numbered}"
    )


def _enforce_vocabulary(
    tagging: StoryTagging, n_segments: int
) -> list[list[str]]:
    """Realign to the input and drop anything outside the vocabulary (§12).

    The JSON schema already constrains this. Checking again costs nothing and
    means the closed-vocabulary guarantee holds even if the schema is bypassed,
    the model is swapped, or a future caller hands us hand-written tags.
    """
    out: list[list[str]] = [[] for _ in range(n_segments)]
    for entry in tagging.segments:
        if not 0 <= entry.idx < n_segments:
            continue  # a hallucinated index would shift every later picture
        seen: list[str] = []
        for tag in entry.tags:
            value = str(tag)
            if value in TAG_VOCABULARY and value not in seen:
                seen.append(value)
        out[entry.idx] = seen[:MAX_TAGS_PER_SEGMENT]
    return out


async def tag_segments(*, segments: Sequence[str], theme: str) -> SceneTagResult:
    """Tag every screen of one story in a single call (§12).

    Whole-story rather than per-screen: the model needs the neighbouring screens
    to tell "the dragon flew away" (sky) from "the dragon slept" (cave), and one
    call per story is one Haiku request instead of seven.
    """
    if not segments:
        return SceneTagResult(tags=[])

    client = get_client().with_options(timeout=FAST_TIMEOUT_S)
    model = fast_model()
    response = await client.messages.parse(
        model=model,
        max_tokens=1000,
        system=SCENE_TAG_SYSTEM,
        messages=[
            {"role": "user", "content": build_tagging_request(theme, segments)}
        ],
        output_format=StoryTagging,
    )

    usage = usage_from_response(model, response.usage)
    if response.parsed_output is None:
        # §7 counts a schema-parse failure as api_fail. Here it degrades to the
        # placeholder rather than raising — a missing picture is not a reason to
        # throw away a story that passed the decodability gate.
        return SceneTagResult(
            tags=[[] for _ in segments], usage=usage, degraded=True
        )

    return SceneTagResult(
        tags=_enforce_vocabulary(response.parsed_output, len(segments)),
        usage=usage,
    )


class TaggedAsset(Protocol):
    """What `select_asset` needs from an asset.

    Structural rather than importing `storyforge.models.Asset`, so the selection
    logic stays a pure function testable without a database.
    """

    id: int
    kind: str
    tags: Sequence[str]


def select_asset(
    segment_tags: Iterable[str],
    assets: Iterable[TaggedAsset],
    *,
    kind: str | None = "segment",
    avoid: Collection[int] = (),
) -> TaggedAsset | None:
    """Pick the best-matching asset: most tag overlap, then a NOVELTY tiebreak,
    then asset id (§12).

    Pure. Same tags + same pool + same `avoid` => same asset, always. Two
    children reading the same story must see the same pictures, and the eval
    harness must be able to reproduce a run.

    **`avoid` is the ids already spent on this story, and it is a TIEBREAK, not
    a penalty.** §12's rule is "most tag overlap, deterministic tiebreak by
    asset id", and the second half of that sentence is what went wrong: when two
    assets overlap equally, lowest-id-always-wins is not a tiebreak between
    pictures, it is a fixed preference that repeats down the whole story. The
    animals pool holds exactly two rabbit pictures (`forest-02`, `burrow-01`),
    so every screen tagged `rabbit` and nothing else that matched scored 1 on
    both, and asset 24 — the lower id — took all seven pages. Measured on the
    live pool before this changed: **4 servable stories showed ONE picture on
    every page and 40 more showed two**, and a story whose seven screens carry
    one repeated picture is what the owner meant by "there are no images in the
    stories".

    Overlap still comes first and is never traded away. Demoting a better match
    because it has been seen would put a *wrong* picture on the page — a cat in
    a story about a dragon — and §12's promise is that the segment renders the
    best-matching asset. Novelty only decides between assets that match the
    segment EQUALLY WELL, where §12 was choosing arbitrarily anyway. Once every
    equally-good match is spent, the lowest id wins again, so a story with more
    screens than pictures repeats instead of falling back to the placeholder.

    `kind` defaults to `"segment"` because the pool also holds 1:1 theme covers
    and 1:1 transparent story-word cutouts (style guide §06). Dropping a square
    cover into the 4:3 segment frame is a real bug, not a cosmetic one. Pass
    `kind=None` to rank across every kind.

    Returns None when nothing overlaps. That is a normal outcome: §06 requires
    the UI to render the striped placeholder rather than an empty box or a
    broken-image icon, and it can only do that if it is told there is no match.
    """
    wanted = {t for t in segment_tags if t}
    if not wanted:
        return None

    spent = set(avoid)
    best: TaggedAsset | None = None
    best_key: tuple[int, int] | None = None
    # Ascending id + strict `>` on the key is the deterministic tiebreak of last
    # resort: the first asset to reach a given (overlap, unseen) keeps it, and
    # the first is the lowest id.
    for asset in sorted(assets, key=lambda a: a.id):
        if kind is not None and asset.kind != kind:
            continue
        overlap = len(wanted & set(asset.tags))
        if overlap == 0:
            continue
        key = (overlap, 0 if asset.id in spent else 1)
        if best_key is None or key > best_key:
            best_key = key
            best = asset
    return best


def select_story_assets(
    story_tags: Sequence[Sequence[str]],
    assets: Iterable[TaggedAsset],
    *,
    kind: str | None = "segment",
) -> list[TaggedAsset | None]:
    """Pick every picture in ONE story, in reading order (§12).

    This is where `select_asset`'s `avoid` set is threaded, and it exists as a
    named pure function because the story — not the segment — is the unit the
    repeat is visible in. A per-segment selector cannot see that it has already
    used this picture six times; nothing else in the pipeline was looking at the
    story as a whole, which is how three stories came to carry asset 24 on all
    seven pages.

    Order is significant and must be READING order (`story_segments.idx`): page
    1 gets first claim on the best match, later pages take the best of what is
    left. Deterministic for a given order, which is what §12's reproducibility
    promise actually needs — the same story always renders the same book.

    Returns one entry per input, `None` where §06's placeholder is the answer.
    """
    pool = list(assets)
    used: set[int] = set()
    picks: list[TaggedAsset | None] = []
    for tags in story_tags:
        chosen = select_asset(tags, pool, kind=kind, avoid=used)
        if chosen is not None:
            used.add(chosen.id)
        picks.append(chosen)
    return picks
