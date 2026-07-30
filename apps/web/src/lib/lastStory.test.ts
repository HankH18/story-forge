/**
 * The three bounds on the C10 fallback, as tests (DEVIATIONS 8.62).
 *
 * The cache itself is trivial. What is not trivial is that it must refuse in
 * three specific ways, each of which corresponds to a defect this repo has
 * already had:
 *
 * - **another reader's story** — audit C9 filed a whole session against the wrong
 *   child because a lookup fell back to `kids[0]`;
 * - **a retracted story** — the grammar backfill moved 4 of 28 servable stories to
 *   `rejected` while they were being served, so a cache with no clock re-serves a
 *   pulled story forever;
 * - **a story above the reader's stage** — the shape of C1/C2, where a stage-1
 *   reader was served text measuring 17.6% decodable at their own stage.
 *
 * Plus the one that is not about reading at all: `localStorage` is writable by
 * anyone holding the device, so a hand-edited entry must resolve to `null` rather
 * than to a half-rendered screen in front of a child.
 */

import { beforeEach, describe, expect, test } from 'bun:test'
import type { Story } from './api'
import {
  MAX_AGE_MS,
  forgetLastStory,
  loadLastStory,
  saveLastStory,
  type StorageLike,
} from './lastStory'

function memoryStorage(): StorageLike & { map: Map<string, string> } {
  const map = new Map<string, string>()
  return {
    map,
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
  }
}

function story(overrides: Partial<Story> = {}): Story {
  return {
    id: 42,
    stage_id: 3,
    theme_id: 1,
    theme_slug: 'dragons',
    theme_display_name: 'Dragons',
    status: 'warm',
    title: 'The dragon and the cave',
    decodability: 1,
    story_words: ['dragon'],
    segments: [
      {
        id: 1,
        idx: 0,
        body: 'The dragon sat in the cave.',
        scene_tags: [],
        parent_segment_id: null,
        choice_label: null,
        asset: null,
      },
    ],
    questions: [],
    fallback: null,
    ...overrides,
  }
}

const NOW = 1_800_000_000_000

describe('a story is remembered for the reader it was served to', () => {
  let storage: ReturnType<typeof memoryStorage>
  beforeEach(() => {
    storage = memoryStorage()
  })

  test('round trips', () => {
    saveLastStory(7, story(), storage, NOW)
    expect(loadLastStory(7, 3, storage, NOW)?.id).toBe(42)
  })

  test('a DIFFERENT reader gets nothing — never a substituted child (C9)', () => {
    saveLastStory(7, story(), storage, NOW)
    expect(loadLastStory(8, 12, storage, NOW)).toBeNull()
  })

  test('an unusable kid id is refused in both directions', () => {
    saveLastStory(0, story(), storage, NOW)
    saveLastStory(Number.NaN, story(), storage, NOW)
    expect(storage.map.size).toBe(0)
    expect(loadLastStory(Number.NaN, 3, storage, NOW)).toBeNull()
  })

  test('forgetting it is idempotent and silent', () => {
    saveLastStory(7, story(), storage, NOW)
    forgetLastStory(7, storage)
    forgetLastStory(7, storage)
    expect(loadLastStory(7, 3, storage, NOW)).toBeNull()
  })
})

describe('the clock is a retraction bound, not a decodability one', () => {
  test('inside the window it is served', () => {
    const storage = memoryStorage()
    saveLastStory(7, story(), storage, NOW)
    expect(loadLastStory(7, 3, storage, NOW + MAX_AGE_MS - 1)).not.toBeNull()
  })

  test('past the window it is dropped, not merely hidden', () => {
    const storage = memoryStorage()
    saveLastStory(7, story(), storage, NOW)
    expect(loadLastStory(7, 3, storage, NOW + MAX_AGE_MS + 1)).toBeNull()
    expect(storage.map.size).toBe(0)
  })

  test('a timestamp in the future is not evidence of anything', () => {
    const storage = memoryStorage()
    saveLastStory(7, story(), storage, NOW + 60_000)
    expect(loadLastStory(7, 3, storage, NOW)).toBeNull()
  })
})

describe('the stage ceiling', () => {
  test('a story at or below the reader stays readable — §5.7c monotonicity', () => {
    const storage = memoryStorage()
    saveLastStory(7, story({ stage_id: 3 }), storage, NOW)
    expect(loadLastStory(7, 3, storage, NOW)?.stage_id).toBe(3)
    saveLastStory(7, story({ stage_id: 3 }), storage, NOW)
    expect(loadLastStory(7, 9, storage, NOW)?.stage_id).toBe(3)
  })

  test('a story ABOVE the reader is refused — the C1/C2 shape', () => {
    const storage = memoryStorage()
    saveLastStory(7, story({ stage_id: 12 }), storage, NOW)
    expect(loadLastStory(7, 1, storage, NOW)).toBeNull()
    expect(storage.map.size).toBe(0)
  })

  test('an unknown reader stage refuses rather than guessing one', () => {
    const storage = memoryStorage()
    saveLastStory(7, story({ stage_id: 1 }), storage, NOW)
    expect(loadLastStory(7, Number.NaN, storage, NOW)).toBeNull()
  })
})

describe('a hand-edited entry never reaches a child', () => {
  const cases: Array<[string, string]> = [
    ['not JSON', '{oh no'],
    ['not an envelope', '"hello"'],
    ['no savedAt', JSON.stringify({ story: story() })],
    ['no story', JSON.stringify({ savedAt: NOW })],
    ['no segments', JSON.stringify({ savedAt: NOW, story: { ...story(), segments: [] } })],
    [
      'a segment with no body',
      JSON.stringify({
        savedAt: NOW,
        story: { ...story(), segments: [{ ...story().segments[0], body: '' }] },
      }),
    ],
    ['questions is not a list', JSON.stringify({ savedAt: NOW, story: { ...story(), questions: null } })],
    ['a negative id', JSON.stringify({ savedAt: NOW, story: { ...story(), id: -5 } })],
  ]

  for (const [label, raw] of cases) {
    test(label, () => {
      const storage = memoryStorage()
      storage.map.set('sf.lastStory.v1.7', raw)
      expect(loadLastStory(7, 12, storage, NOW)).toBeNull()
      expect(storage.map.size).toBe(0)
    })
  }

  test('the corpus is not vacuous: the same envelope shape DOES load when it is valid', () => {
    const storage = memoryStorage()
    storage.map.set('sf.lastStory.v1.7', JSON.stringify({ savedAt: NOW, story: story() }))
    expect(loadLastStory(7, 12, storage, NOW)?.id).toBe(42)
  })
})

describe('a missing or hostile Storage is never a failure', () => {
  test('no storage at all', () => {
    saveLastStory(7, story(), null, NOW)
    expect(loadLastStory(7, 3, null, NOW)).toBeNull()
    forgetLastStory(7, null)
  })

  test('a storage that throws on every call', () => {
    const hostile: StorageLike = {
      getItem() {
        throw new Error('SecurityError')
      },
      setItem() {
        throw new Error('QuotaExceededError')
      },
      removeItem() {
        throw new Error('SecurityError')
      },
    }
    expect(() => saveLastStory(7, story(), hostile, NOW)).not.toThrow()
    expect(loadLastStory(7, 3, hostile, NOW)).toBeNull()
    expect(() => forgetLastStory(7, hostile)).not.toThrow()
  })
})
