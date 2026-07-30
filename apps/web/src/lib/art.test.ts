import { describe, expect, it } from 'bun:test'
import { coverFor, cutoutFor } from './art'

/**
 * The 14 words `SELECT unnest(story_words) FROM stories WHERE status IN
 * ('warm','served','accepted')` returns over the live pool, plus `fox` (the
 * animals cover subject, and the next story word the generator is likely to
 * reach for).
 */
const LIVE_STORY_WORDS = [
  'rocket', 'moon', 'rabbit', 'dragon', 'cave', 'stars', 'forest',
  'burrow', 'wings', 'fire', 'planet', 'astronaut', 'giraffe', 'treasure',
]

describe('cutoutFor', () => {
  it('has a cutout for every story word the live pool actually uses', () => {
    // This is the whole point of the map: the words that occur, not a guess at
    // English. A miss here is a chip with no picture on a real story.
    for (const word of LIVE_STORY_WORDS) {
      expect(cutoutFor(word), `no cutout for "${word}"`).toBeTruthy()
    }
  })

  it('returns null for a word with no drawing, rather than an invented URL', () => {
    // `/assets/cutouts/${word}.svg` would 404 and be hidden by `onError`. Null
    // is a fact about the pool and renders as a word with no picture.
    expect(cutoutFor('velociraptor')).toBeNull()
  })

  it('is case- and whitespace-insensitive, because story text is arbitrary case', () => {
    expect(cutoutFor('Dragon')).toBe(cutoutFor('dragon'))
    expect(cutoutFor('  CAVE ')).toBe(cutoutFor('cave'))
  })

  it('resolves to a real URL string, not a module object', () => {
    // Vite's SVG import is a URL. If this ever became `{ default: ... }` every
    // chip would render `[object Object]` as a src and silently degrade.
    expect(typeof cutoutFor('dragon')).toBe('string')
  })
})

describe('coverFor', () => {
  it('covers all three seeded themes', () => {
    for (const slug of ['dragons', 'space', 'animals']) {
      expect(coverFor(slug), `no cover for "${slug}"`).toContain('/assets/art/v1/')
    }
  })

  it('points at the VERSIONED path, because /assets is served immutable', () => {
    // These are copied from `public/` and are not content-hashed by Vite, so the
    // version has to be in the URL or a redrawn cover is invisible for a year
    // (DEVIATIONS 11.23).
    expect(coverFor('dragons')).toBe('/assets/art/v1/dragons/cover.svg')
  })

  it('returns null for an unknown theme, so the card falls back to its emoji', () => {
    expect(coverFor('pirates')).toBeNull()
  })
})
