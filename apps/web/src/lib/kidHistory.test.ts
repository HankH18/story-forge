/**
 * The back gesture's decision table, and the two facts about the guard entry
 * (DEVIATIONS 27.3).
 *
 * These are the half of the containment a pure-helper suite CAN see: what back
 * means at each phase, and whether an entry already carries the guard. What a
 * browser actually does with a history stack is `make kid-check`, which drives
 * a real one — the audit's "untestable in a pure-helper suite" was true of this
 * suite and not of the problem, and the split is the honest version of it.
 *
 * Every case below corresponds to something that goes wrong if the decision is
 * different: `reading → stay` is the child stuck on a page they wanted to leave,
 * `picker → to-picker` is a no-op that hides the fact that back did anything,
 * `loading → to-picker` yanks a child into a story they had just backed out of
 * when the in-flight request lands, and a `needsGuardEntry` that always says yes
 * deepens the stack by one on every reload.
 */

import { describe, expect, test } from 'bun:test'
import { GUARD_MARK, backOutcome, guardState, needsGuardEntry, type KidPhase } from './kidHistory'

describe('backOutcome — what the platform back gesture means inside kid mode', () => {
  test('inside a story, back is the ← button: it returns to the theme picker', () => {
    expect(backOutcome('reading')).toBe('to-picker')
    expect(backOutcome('story-end')).toBe('to-picker')
    expect(backOutcome('questions')).toBe('to-picker')
  })

  test('the celebration goes back to the picker — "read another?" is that screen’s own offer', () => {
    expect(backOutcome('celebration')).toBe('to-picker')
  })

  test('at the picker, back does nothing: leaving kid mode is not one of the answers', () => {
    expect(backOutcome('picker')).toBe('stay')
  })

  test('the printer screen stays, because the request it is waiting on will still land', () => {
    // `pickTheme` calls `beginStory` when the in-flight request resolves. Going
    // back to the picker here would show the picker and then replace it with the
    // story the child had just left.
    expect(backOutcome('loading')).toBe('stay')
  })

  test('every phase has an answer, and it is one of exactly two', () => {
    const phases: KidPhase[] = [
      'picker',
      'loading',
      'reading',
      'story-end',
      'questions',
      'celebration',
    ]
    for (const phase of phases) {
      expect(['to-picker', 'stay']).toContain(backOutcome(phase))
    }
  })
})

describe('needsGuardEntry — one guard entry, no matter how many reloads', () => {
  test('an entry already carrying the guard does not need another', () => {
    expect(needsGuardEntry({ [GUARD_MARK]: true })).toBe(false)
  })

  test("react-router's own state, unmarked, needs one", () => {
    expect(needsGuardEntry({ usr: null, key: 'abc123', idx: 1 })).toBe(true)
  })

  test('a fresh entry — `history.state` is null on a first load — needs one', () => {
    expect(needsGuardEntry(null)).toBe(true)
    expect(needsGuardEntry(undefined)).toBe(true)
  })

  test('a mark that is not literally `true` is not a mark', () => {
    // `history.state` survives a reload and is writable by anything on the page.
    expect(needsGuardEntry({ [GUARD_MARK]: 'true' })).toBe(true)
    expect(needsGuardEntry({ [GUARD_MARK]: 1 })).toBe(true)
  })
})

describe('guardState — the router keeps its bookkeeping', () => {
  test("react-router's fields survive the push", () => {
    const state = guardState({ usr: null, key: 'abc123', idx: 4 })
    expect(state.key).toBe('abc123')
    expect(state.idx).toBe(4)
    expect(state[GUARD_MARK]).toBe(true)
  })

  test('a null state becomes a state carrying only the mark', () => {
    expect(guardState(null)).toEqual({ [GUARD_MARK]: true })
  })

  test('the pushed state is a copy — the entry we are shadowing is not mutated', () => {
    const original: Record<string, unknown> = { idx: 2 }
    guardState(original)
    expect(original[GUARD_MARK]).toBeUndefined()
  })

  test('what it produces no longer needs a guard entry — mount and popstate agree', () => {
    expect(needsGuardEntry(guardState({ idx: 7 }))).toBe(false)
  })
})
