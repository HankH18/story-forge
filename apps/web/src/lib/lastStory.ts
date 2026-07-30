/**
 * One story per reader, remembered, so kid mode's printer screen cannot become
 * permanent (AUDIT-PHASE6 C10, DEVIATIONS 8.62).
 *
 * C10: three failed attempts put a child on "Writing your story… the printer is
 * working / Try again", and "Try again" returned to the picker, which re-entered
 * the same loop indefinitely. §01 says a child never sees an error state **and**
 * never gets stuck; that screen honoured the first clause and broke the second,
 * while saying the printer was working when nothing was. The easiest way in is
 * §9's limiter — 10 story requests a minute, and an eleventh tap of "Read
 * another" is ordinary behaviour for a five-year-old who wants a different
 * dragon.
 *
 * **What is stored is a story the SERVER chose for THIS reader**, returned by
 * `POST /api/stories/request`, which resolves the stage from the `kids` row
 * (DEVIATIONS 7.21) and re-validates the pick against that reader's own stage
 * before returning it (7.23). Nothing here generates, picks or re-scores a story;
 * this module is a cache with a clock and two refusals.
 *
 * Why re-reading it later stays inside §0's guarantee: the GPC inventory and the
 * sight-word list are cumulative (§4), so a story decodable at stage N is
 * decodable at every stage above it (§5.7c monotonicity). A stale entry can only
 * ever be *easier* than the reader's current stage — the same direction §7's
 * fallback ladder is allowed to move, and the reason it never climbs.
 *
 * Three bounds, each for a different failure:
 *
 * 1. **Keyed by kid id.** C9's lesson (7.33) is that a substituted reader is
 *    worse than a refusal, so a shared tablet cannot serve reader A's story to
 *    reader B.
 * 2. **Seven-day expiry.** Not for decodability — that cannot expire upward — but
 *    because the pool can RETRACT a story. The Phase 6 grammar backfill moved 4 of
 *    28 servable stories to `rejected` while they were being served, and a cache
 *    with no clock would re-serve a retracted story to that child forever.
 * 3. **A stage ceiling, checked on read.** Defence in depth: the server cannot
 *    serve above the reader's stage, so this should never fire. It is one
 *    comparison, and "a story that is undecodable for the child receiving it
 *    should be inexpressible, not merely unlikely" is the same argument
 *    `_is_decodable_for` is in the serving path for (7.23).
 *
 * Every read also shape-checks the payload, because `localStorage` is writable by
 * anyone with the device: a hand-edited entry must resolve to `null`, never to a
 * half-rendered screen.
 */

import type { Story } from './api'

const KEY_PREFIX = 'sf.lastStory.v1.'

/** §14-free, deliberately: this is a retraction bound, not a decodability one. */
export const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000

interface Envelope {
  savedAt: number
  story: Story
}

/** The three methods used here, so a test can pass a plain object. */
export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

/**
 * `window.localStorage`, or nothing.
 *
 * Private-mode Safari throws on access, and a bun test has no `window` at all.
 * Either way the caller gets `null` and the session carries on — a cache is never
 * allowed to be the reason something fails.
 */
export function defaultStorage(): StorageLike | null {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function keyFor(kidId: number): string {
  return `${KEY_PREFIX}${kidId}`
}

/** Enough of a StoryDTO to render a reading session without holes. */
function isStoryShaped(value: unknown): value is Story {
  if (typeof value !== 'object' || value === null) return false
  const story = value as Partial<Story>
  if (typeof story.id !== 'number' || !Number.isInteger(story.id) || story.id <= 0) return false
  if (typeof story.stage_id !== 'number' || story.stage_id <= 0) return false
  if (typeof story.title !== 'string') return false
  if (!Array.isArray(story.segments) || story.segments.length === 0) return false
  if (!story.segments.every((s) => typeof s?.body === 'string' && s.body.length > 0)) return false
  if (!Array.isArray(story.questions)) return false
  if (!Array.isArray(story.story_words)) return false
  return true
}

/**
 * Remember the story just served to this reader. Best effort in every direction:
 * a full quota, a serialisation failure or a missing `localStorage` are all
 * silent no-ops.
 */
export function saveLastStory(
  kidId: number,
  story: Story,
  storage: StorageLike | null = defaultStorage(),
  now: number = Date.now(),
): void {
  if (!storage || !Number.isInteger(kidId) || kidId <= 0 || !isStoryShaped(story)) return
  try {
    const envelope: Envelope = { savedAt: now, story }
    storage.setItem(keyFor(kidId), JSON.stringify(envelope))
  } catch {
    /* quota, private mode, serialisation — a lost cache costs nothing */
  }
}

/**
 * The last story this reader was served, if it is still fit to re-serve.
 *
 * `readerStage` is the ceiling. Pass the reader's own `stage_id`; anything above
 * it is discarded rather than shown, and the entry is deleted so the check does
 * not have to be made twice.
 */
export function loadLastStory(
  kidId: number,
  readerStage: number,
  storage: StorageLike | null = defaultStorage(),
  now: number = Date.now(),
): Story | null {
  if (!storage || !Number.isInteger(kidId) || kidId <= 0) return null
  let raw: string | null = null
  try {
    raw = storage.getItem(keyFor(kidId))
  } catch {
    return null
  }
  if (!raw) return null

  const drop = () => {
    try {
      storage.removeItem(keyFor(kidId))
    } catch {
      /* nothing to do about it */
    }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    drop()
    return null
  }
  const envelope = parsed as Partial<Envelope>
  if (typeof envelope?.savedAt !== 'number' || !isStoryShaped(envelope.story)) {
    drop()
    return null
  }
  if (now - envelope.savedAt > MAX_AGE_MS || envelope.savedAt > now) {
    // Expired, or saved in the future — a clock that moved backwards is not
    // evidence the story is still servable.
    drop()
    return null
  }
  if (!Number.isFinite(readerStage) || envelope.story.stage_id > readerStage) {
    drop()
    return null
  }
  return envelope.story
}

/** Used when a reader resolves to nobody — nothing to remember, nothing to keep. */
export function forgetLastStory(
  kidId: number,
  storage: StorageLike | null = defaultStorage(),
): void {
  if (!storage) return
  try {
    storage.removeItem(keyFor(kidId))
  } catch {
    /* nothing to do about it */
  }
}
