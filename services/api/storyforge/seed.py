"""Seed the scope & sequence into the database (§4, Appendix A).

Idempotent: running `make seed` twice must not duplicate rows, so every insert
is an upsert keyed on the natural key.

The GPC table is seeded by importing the decoder's own ``load_gpcs()`` rather
than re-parsing ``gpcs.csv``. The database and the validator therefore cannot
disagree about the inventory — there is one parser, not two.
"""

from __future__ import annotations

import asyncio
import csv
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from storyforge.db import SessionLocal
from storyforge.decoder import load_gpcs
from storyforge.models import AllowedWord, Asset, Gpc, SightWord, Stage, StoryWord, Theme

log = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parents[3] / "data/seed"


def _rows(name: str) -> list[dict[str, str]]:
    text = (SEED_DIR / name).read_text(encoding="utf-8")
    body = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    return list(csv.DictReader(body))


async def seed_stages(session) -> int:
    rows = [
        {
            "id": int(r["id"]),
            "name": r["name"],
            "description": r["description"],
            "target_len_min": int(r["target_len_min"]),
            "target_len_max": int(r["target_len_max"]),
            "max_syllables": int(r["max_syllables"]),
        }
        for r in _rows("stages.csv")
    ]
    stmt = insert(Stage).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Stage.id],
            set_={
                c: stmt.excluded[c]
                for c in (
                    "name", "description", "target_len_min",
                    "target_len_max", "max_syllables",
                )
            },
        )
    )
    return len(rows)


async def seed_gpcs(session) -> int:
    """Mirror the decoder's in-memory inventory into `gpcs`."""
    rows = [
        {
            "gpc_id": g.id,
            "grapheme": g.grapheme,
            "phonemes": " ".join(g.phonemes),
            "kind": g.kind,
            "position": g.position,
            "stage_id": g.stage,
            "schwa_ok": g.schwa_ok,
        }
        for g in load_gpcs()
    ]
    stmt = insert(Gpc).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Gpc.gpc_id],
            set_={
                c: stmt.excluded[c]
                for c in ("grapheme", "phonemes", "kind", "position", "stage_id", "schwa_ok")
            },
        )
    )
    return len(rows)


async def seed_sight_words(session) -> int:
    rows = [
        {"stage_id": int(r["stage"]), "word": r["word"].strip().lower()}
        for r in _rows("sight_words.csv")
    ]
    stmt = insert(SightWord).values(rows)
    await session.execute(stmt.on_conflict_do_nothing(constraint="uq_sight_stage_word"))
    return len(rows)


async def seed_themes(session) -> int:
    rows = [
        {"slug": r["slug"].strip(), "display_name": r["display_name"], "emoji": r["emoji"]}
        for r in _rows("themes.csv")
    ]
    stmt = insert(Theme).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Theme.slug],
            set_={c: stmt.excluded[c] for c in ("display_name", "emoji")},
        )
    )
    return len(rows)


async def seed_story_words(session) -> int:
    """Depends on themes, so it resolves slugs after `seed_themes` has run."""
    theme_ids = {
        slug: tid
        for tid, slug in (await session.execute(select(Theme.id, Theme.slug))).all()
    }
    rows = []
    for r in _rows("story_words.csv"):
        theme_id = theme_ids.get(r["theme"].strip())
        if theme_id is None:
            raise SystemExit(f"story_words.csv references unknown theme {r['theme']!r}")
        rows.append(
            {
                "theme_id": theme_id,
                "word": r["word"].strip().lower(),
                "gloss": r["gloss"].strip(),
                "min_stage": int(r["min_stage"]),
            }
        )
    stmt = insert(StoryWord).values(rows)
    await session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_storyword_theme_word",
            set_={c: stmt.excluded[c] for c in ("gloss", "min_stage")},
        )
    )
    return len(rows)


async def seed_assets(session) -> int:
    """Curated illustration pool (§12). Depends on themes, like story_words.

    Two things are validated loudly before anything is written:

    - **every tag is in the closed vocabulary** (§12). A typo'd tag cannot be
      produced by the scene-tagging node, so the asset would never be selected
      and every segment that should have matched it would quietly render the
      placeholder instead. That is a bug that never raises — so it is caught here.
    - **`alt_text` is present** (style guide §06: mandatory on every asset). A
      blank one passes every test and fails exactly one user.

    `assets` has no unique constraint (the Phase 4 migration mirrors §8's schema,
    which has none), so idempotency is done by reading the existing rows and
    splitting into update/insert rather than with ON CONFLICT. Deleting and
    re-inserting would renumber ids and orphan `story_segments.asset_id`.

    **The natural key is `(theme, kind, tags)` — the PICTURE — not `url`**
    (DEVIATIONS 11.22). It was `url`, and under that key this file's move from
    `.png` to `/assets/art/v1/....svg` would have inserted 33 new rows, left the
    old 33 alive, and stranded **831 live `story_segments.asset_id`s on files that
    do not exist** — every one of them silently rendering the striped placeholder,
    which is the defect the art was drawn to fix. Keying on the picture makes
    `url` a mutable column, so a re-versioned path (11.23) is a one-line CSV edit
    and a `make seed`, with ids preserved and no data migration.

    The new key is weaker than a unique index, so two guards come with it:

    - a duplicate `(theme, kind, tags)` in the CSV is a hard error. Under the old
      key two rows with identical tags were merely redundant — `select_asset`
      would deterministically prefer the lower id and the other would never be
      chosen. Under this one the second row silently overwrites the first's url.
    - an asset row in the database whose url is not in the CSV is logged as an
      orphan, with its id. That is exactly the state this change exists to
      prevent, and it is invisible otherwise: nothing raises, nothing 500s, a
      child just gets stripes.
    """
    from storyforge.llm.scene_tags import TAG_VOCABULARY

    theme_ids = {
        slug: tid
        for tid, slug in (await session.execute(select(Theme.id, Theme.slug))).all()
    }

    rows = []
    for r in _rows("assets.csv"):
        theme_id = theme_ids.get(r["theme"].strip())
        if theme_id is None:
            raise SystemExit(f"assets.csv references unknown theme {r['theme']!r}")
        url = r["url"].strip()
        tags = [t.strip() for t in r["tags"].split("|") if t.strip()]
        unknown = sorted(set(tags) - TAG_VOCABULARY)
        if unknown:
            raise SystemExit(
                f"assets.csv row {url!r} uses tags outside the closed §12 "
                f"vocabulary: {', '.join(unknown)} — the tagging node can never "
                "emit these, so the asset would be unreachable."
            )
        alt_text = r["alt_text"].strip()
        if not alt_text:
            raise SystemExit(f"assets.csv row {url!r} has no alt_text (style guide §06)")
        rows.append(
            {
                "theme_id": theme_id,
                "kind": r["kind"].strip(),
                "tags": tags,
                "url": url,
                "alt_text": alt_text,
            }
        )

    def identity(theme_id: int, kind: str, tags: list[str]) -> tuple[int, str, str]:
        # Tag ORDER is meaningful in the CSV (most important first) but not to
        # identity: the same picture with its tags re-ordered is the same row.
        return (theme_id, kind, "|".join(sorted(tags)))

    seen: dict[tuple[int, str, str], str] = {}
    for row in rows:
        key = identity(row["theme_id"], row["kind"], row["tags"])
        if key in seen:
            raise SystemExit(
                f"assets.csv rows {seen[key]!r} and {row['url']!r} have the same "
                f"(theme, kind, tags) — that is the natural key, so the second "
                "would silently overwrite the first's url."
            )
        seen[key] = row["url"]

    existing: dict[tuple[int, str, str], int] = {}
    # The orphan check compares IDENTITIES, not urls. Comparing urls read before
    # the updates reported all 33 rows as orphans on the very run that migrated
    # them correctly — the pre-update url is of course absent from the new CSV.
    unmatched: dict[int, str] = {}
    for aid, theme_id, kind, tags, url in (
        await session.execute(
            select(Asset.id, Asset.theme_id, Asset.kind, Asset.tags, Asset.url)
        )
    ).all():
        existing.setdefault(identity(theme_id, str(kind), list(tags or [])), aid)
        unmatched[aid] = str(url)

    for row in rows:
        asset_id = existing.get(identity(row["theme_id"], row["kind"], row["tags"]))
        if asset_id is None:
            session.add(Asset(**row))
        else:
            unmatched.pop(asset_id, None)
            await session.execute(
                Asset.__table__.update().where(Asset.id == asset_id).values(**row)
            )

    if unmatched:
        log.warning(
            "seed_assets: %d asset row(s) match no row in assets.csv: %s. Any "
            "story_segments still pointing at them render the striped placeholder "
            "(style guide §06) rather than art.",
            len(unmatched),
            ", ".join(f"id={aid} {url}" for aid, url in sorted(unmatched.items())),
        )
    return len(rows)


async def run() -> dict[str, int]:
    async with SessionLocal() as session:
        counts = {
            "stages": await seed_stages(session),
            "gpcs": await seed_gpcs(session),
            "sight_words": await seed_sight_words(session),
            "themes": await seed_themes(session),
        }
        # story_words and assets resolve theme slugs, so themes must flush first.
        await session.flush()
        counts["story_words"] = await seed_story_words(session)
        counts["assets"] = await seed_assets(session)
        await session.commit()
    return counts


def main() -> None:
    counts = asyncio.run(run())
    for table, n in counts.items():
        print(f"  seeded {table:<12} {n:>5} rows")
    # Loud, because a silently empty allowlist is the failure mode that would
    # otherwise surface as "the stories got boring" three phases later.
    print("\nNext: `make precompute` to build allowed_words.")


__all__ = ["AllowedWord", "main", "run"]


if __name__ == "__main__":
    main()
