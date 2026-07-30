/**
 * The two art lookups the SPA resolves itself: story-word cutouts and theme
 * covers (DEVIATIONS 11.24, 11.29).
 *
 * Everything here is a LITERAL `import`, not a URL assembled at runtime.
 * `StoryWordChip`'s contract is "never a broken image, never an invented URL",
 * and `` `/assets/cutouts/${word}.svg` `` invents one for every word that has no
 * drawing — fifteen hits and an unbounded tail of 404s that only `onError`
 * hides. A literal map answers "no picture" with `null`, which is a fact about
 * the pool rather than a network failure.
 *
 * Vite turns each `import` into a content-hashed URL under `dist/assets/`, which
 * also means the immutable `Cache-Control` the API serves `/assets` with is
 * honest for these files — unlike the segment pool, which is copied from
 * `public/` and therefore carries its version in the path (11.23).
 *
 * The segment illustrations are deliberately NOT here. Those come from the
 * `assets` table via `select_asset` (§12: most tag overlap, deterministic
 * tiebreak by asset id) and the DTO hands the URL over. Duplicating that
 * decision in the client would give two readers of the same story two different
 * books the moment the two lists drifted.
 */

import astronaut from '../assets/cutouts/astronaut.svg'
import burrow from '../assets/cutouts/burrow.svg'
import cave from '../assets/cutouts/cave.svg'
import dragon from '../assets/cutouts/dragon.svg'
import fire from '../assets/cutouts/fire.svg'
import forest from '../assets/cutouts/forest.svg'
import fox from '../assets/cutouts/fox.svg'
import giraffe from '../assets/cutouts/giraffe.svg'
import moon from '../assets/cutouts/moon.svg'
import planet from '../assets/cutouts/planet.svg'
import rabbit from '../assets/cutouts/rabbit.svg'
import rocket from '../assets/cutouts/rocket.svg'
import stars from '../assets/cutouts/stars.svg'
import treasure from '../assets/cutouts/treasure.svg'
import wings from '../assets/cutouts/wings.svg'

/**
 * §06's third ratio: "one object on transparency, no scene, no shadow — it sits
 * inside a chip beside the word."
 *
 * The keys are the words `SELECT unnest(story_words)` returns over the servable
 * pool, not a guess at English. A word outside this set renders as a word with
 * no picture, which is a fine outcome and better than a grey striped square
 * standing in for one (11.25).
 */
const CUTOUTS: Record<string, string> = {
  astronaut,
  burrow,
  cave,
  dragon,
  fire,
  forest,
  fox,
  giraffe,
  moon,
  planet,
  rabbit,
  rocket,
  stars,
  treasure,
  wings,
}

/** The cutout for a story word, or `null` when the pool has none. */
export function cutoutFor(word: string): string | null {
  return CUTOUTS[word.trim().toLowerCase()] ?? null
}

/**
 * §07 theme covers, keyed by `themes.slug`.
 *
 * `ThemePicker` used `themes.emoji`, and its own comment explained why: "cover
 * art is not shipped yet ... If a cover ever arrives on the theme row it takes
 * over and the emoji retires." It has arrived. A slug with no cover still falls
 * back to the emoji, so a fourth theme degrades to the documented fallback
 * rather than to a blank card.
 *
 * These are the same three `kind='cover'` rows `assets.csv` seeds; the picker
 * resolves them by slug instead of by an API field because `GET /api/themes`
 * returns no asset and adding one is backend work this does not need (11.29).
 */
const COVERS: Record<string, string> = {
  dragons: '/assets/art/v1/dragons/cover.svg',
  space: '/assets/art/v1/space/cover.svg',
  animals: '/assets/art/v1/animals/cover.svg',
}

export function coverFor(slug: string): string | null {
  return COVERS[slug.trim().toLowerCase()] ?? null
}
