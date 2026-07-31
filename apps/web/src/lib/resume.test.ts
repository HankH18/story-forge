/**
 * The five bounds on a resumed kid session (DEVIATIONS 27.5).
 *
 * Resuming is the easy half. The refusals are the half worth testing, because
 * each one corresponds to a way this feature could put a child in front of the
 * wrong thing:
 *
 * - **a stale sitting** — the pool can retract a story, so a resume has a clock;
 * - **another story's position** — a step index is meaningless against a story it
 *   was not recorded for, and it would land the child on an arbitrary page;
 * - **the celebration** — resuming past `POST /complete` replays a latched
 *   completion and blanks the "Sounds you read" card (DEVIATIONS 25.4);
 * - **a hand-edited record** — `localStorage` is writable by anyone with the
 *   device, and §01 gives kid mode no error state to fall into;
 * - **a branch that does not replay** — the live pool is linear, so the choice
 *   path is precisely where an off-by-one would sit unseen.
 */

import { describe, expect, test } from 'bun:test'
import type { Segment, Story } from './api'
import { saveLastStory, type StorageLike } from './lastStory'
import { buildPlan } from './plan'
import {
  RESUME_MAX_AGE_MS,
  chosenIds,
  forgetResume,
  isResumablePhase,
  loadResume,
  replayChoices,
  restoreSession,
  saveResume,
} from './resume'

function memoryStorage(): StorageLike & { map: Map<string, string> } {
  const map = new Map<string, string>()
  return {
    map,
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
  }
}

function segment(over: Partial<Segment> & { id: number; idx: number }): Segment {
  return {
    body: `body ${over.id}`,
    scene_tags: [],
    parent_segment_id: null,
    choice_label: null,
    asset: null,
    ...over,
  }
}

/** Linear, like every story in the live pool. */
function linearStory(over: Partial<Story> = {}): Story {
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
    segments: [segment({ id: 1, idx: 0 }), segment({ id: 2, idx: 1 }), segment({ id: 3, idx: 2 })],
    questions: [],
    fallback: null,
    ...over,
  }
}

/** One branch point off segment 2, two labelled arms, remerging on segment 3. */
function branchingStory(): Story {
  return linearStory({
    segments: [
      segment({ id: 1, idx: 0 }),
      segment({ id: 2, idx: 1 }),
      segment({ id: 10, idx: 0, parent_segment_id: 2, choice_label: 'Run up the hill.' }),
      segment({ id: 11, idx: 1, parent_segment_id: 2, choice_label: 'Get in the ship.' }),
      segment({ id: 3, idx: 2 }),
    ],
  })
}

const NOW = 1_700_000_000_000

function primed(story: Story, kidId = 7, now = NOW) {
  const storage = memoryStorage()
  saveLastStory(kidId, story, storage, now)
  return storage
}

describe('loadResume — the refusals', () => {
  test('a position saved and read back inside the window survives', () => {
    const storage = memoryStorage()
    saveResume(7, { storyId: 42, step: 3, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)
    expect(loadResume(7, 42, storage, NOW + 60_000)).toEqual({
      storyId: 42,
      step: 3,
      phase: 'reading',
      readingId: 91,
      choices: [],
    })
  })

  test('one second past the window is a new story, not a resumed one', () => {
    const storage = memoryStorage()
    saveResume(7, { storyId: 42, step: 3, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)
    expect(loadResume(7, 42, storage, NOW + RESUME_MAX_AGE_MS + 1)).toBeNull()
  })

  test('a record from the future is a clock that moved, not a live sitting', () => {
    const storage = memoryStorage()
    saveResume(7, { storyId: 42, step: 3, phase: 'reading', readingId: 91, choices: [] }, storage, NOW + 5_000)
    expect(loadResume(7, 42, storage, NOW)).toBeNull()
  })

  test("another reader's record is not this reader's", () => {
    const storage = memoryStorage()
    saveResume(7, { storyId: 42, step: 3, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)
    expect(loadResume(8, 42, storage, NOW)).toBeNull()
  })

  test('a position recorded against a different story is a position in nothing', () => {
    const storage = memoryStorage()
    saveResume(7, { storyId: 42, step: 3, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)
    expect(loadResume(7, 43, storage, NOW)).toBeNull()
  })

  test('the celebration is never stored and never restored', () => {
    const storage = memoryStorage()
    saveResume(
      7,
      // Deliberately past the type, the way a hand-edited entry arrives.
      { storyId: 42, step: 6, phase: 'celebration' as 'reading', readingId: 91, choices: [] },
      storage,
      NOW,
    )
    expect(storage.map.size).toBe(0)
    storage.setItem(
      'sf.resume.v1.7',
      JSON.stringify({ storyId: 42, step: 6, phase: 'celebration', readingId: 91, choices: [], savedAt: NOW }),
    )
    expect(loadResume(7, 42, storage, NOW)).toBeNull()
  })

  test('a hand-edited entry resolves to null and is dropped, never to half a screen', () => {
    const storage = memoryStorage()
    storage.setItem('sf.resume.v1.7', '{not json')
    expect(loadResume(7, 42, storage, NOW)).toBeNull()
    expect(storage.getItem('sf.resume.v1.7')).toBeNull()
  })

  test('a negative step is refused', () => {
    const storage = memoryStorage()
    storage.setItem(
      'sf.resume.v1.7',
      JSON.stringify({ storyId: 42, step: -1, phase: 'reading', readingId: 91, choices: [], savedAt: NOW }),
    )
    expect(loadResume(7, 42, storage, NOW)).toBeNull()
  })

  test('a choices array that is not ids is refused', () => {
    const storage = memoryStorage()
    storage.setItem(
      'sf.resume.v1.7',
      JSON.stringify({ storyId: 42, step: 1, phase: 'reading', readingId: 91, choices: ['x'], savedAt: NOW }),
    )
    expect(loadResume(7, 42, storage, NOW)).toBeNull()
  })

  test('a null reading id is legitimate — `POST /readings` may not have landed yet', () => {
    const storage = memoryStorage()
    saveResume(7, { storyId: 42, step: 0, phase: 'reading', readingId: null, choices: [] }, storage, NOW)
    expect(loadResume(7, 42, storage, NOW)?.readingId).toBeNull()
  })

  test('forgetResume removes it', () => {
    const storage = memoryStorage()
    saveResume(7, { storyId: 42, step: 3, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)
    forgetResume(7, storage)
    expect(loadResume(7, 42, storage, NOW)).toBeNull()
  })

  test('no storage at all is a silent no-op, never a throw', () => {
    expect(() =>
      saveResume(7, { storyId: 42, step: 1, phase: 'reading', readingId: null, choices: [] }, null),
    ).not.toThrow()
    expect(loadResume(7, 42, null)).toBeNull()
  })
})

describe('isResumablePhase', () => {
  test('reading, story-end and questions are; celebration, picker and loading are not', () => {
    expect(isResumablePhase('reading')).toBe(true)
    expect(isResumablePhase('story-end')).toBe(true)
    expect(isResumablePhase('questions')).toBe(true)
    expect(isResumablePhase('celebration')).toBe(false)
    expect(isResumablePhase('picker')).toBe(false)
    expect(isResumablePhase('loading')).toBe(false)
  })
})

describe('choices — a branch replays exactly or not at all', () => {
  test('a linear plan has no chosen arms', () => {
    expect(chosenIds(buildPlan(linearStory().segments))).toEqual([])
  })

  test('a chosen arm is the segment with a parent, and it round-trips', () => {
    const plan = buildPlan(branchingStory().segments)
    const chosen = replayChoices(plan, [11])
    expect(chosen).not.toBeNull()
    expect(chosenIds(chosen!)).toEqual([11])
  })

  test('the arm lands immediately after its own choice screen', () => {
    const plan = replayChoices(buildPlan(branchingStory().segments), [10])!
    // spine 1, spine 2, choice, arm 10, spine 3
    expect(plan.map((s) => (s.kind === 'segment' ? s.segment.id : 'choice'))).toEqual([
      1,
      2,
      'choice',
      10,
      3,
    ])
  })

  test('an id that is not an option of the choice it answers refuses the whole resume', () => {
    expect(replayChoices(buildPlan(branchingStory().segments), [99])).toBeNull()
  })

  test('more choices than the story has branch points refuses', () => {
    expect(replayChoices(buildPlan(branchingStory().segments), [10, 11])).toBeNull()
  })

  test('a story with no branches and a stored choice refuses', () => {
    expect(replayChoices(buildPlan(linearStory().segments), [10])).toBeNull()
  })

  test('no choices yet is not a failure — the child is sitting on the choice screen', () => {
    const plan = replayChoices(buildPlan(branchingStory().segments), [])
    expect(plan).not.toBeNull()
    expect(plan!.some((s) => s.kind === 'choice')).toBe(true)
  })
})

describe('restoreSession — the story, the plan and the place, or nothing', () => {
  test('restores the page the child was on', () => {
    const story = linearStory()
    const storage = primed(story)
    saveResume(7, { storyId: 42, step: 2, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)

    const restored = restoreSession(7, 3, storage, NOW)
    expect(restored?.story.id).toBe(42)
    expect(restored?.step).toBe(2)
    expect(restored?.phase).toBe('reading')
    expect(restored?.readingId).toBe(91)
  })

  test('restores into the questions, so an interrupted reading can still be completed', () => {
    const story = linearStory()
    const storage = primed(story)
    saveResume(7, { storyId: 42, step: 2, phase: 'questions', readingId: 91, choices: [] }, storage, NOW)
    expect(restoreSession(7, 3, storage, NOW)?.phase).toBe('questions')
  })

  test('restores a branch it can replay, on the arm the child chose', () => {
    const story = branchingStory()
    const storage = primed(story)
    saveResume(7, { storyId: 42, step: 3, phase: 'reading', readingId: 91, choices: [11] }, storage, NOW)

    const restored = restoreSession(7, 3, storage, NOW)
    const at = restored?.plan[restored.step]
    expect(at?.kind).toBe('segment')
    expect(at?.kind === 'segment' && at.segment.id).toBe(11)
  })

  test('a step past the end of the plan is refused, not clamped', () => {
    const storage = primed(linearStory())
    saveResume(7, { storyId: 42, step: 99, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)
    expect(restoreSession(7, 3, storage, NOW)).toBeNull()
  })

  test('a story above the reader’s stage is refused — `lastStory` owns that rule', () => {
    const storage = primed(linearStory({ stage_id: 9 }))
    saveResume(7, { storyId: 42, step: 1, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)
    expect(restoreSession(7, 3, storage, NOW)).toBeNull()
  })

  test('a position with no cached story is nothing to restore', () => {
    const storage = memoryStorage()
    saveResume(7, { storyId: 42, step: 1, phase: 'reading', readingId: 91, choices: [] }, storage, NOW)
    expect(restoreSession(7, 3, storage, NOW)).toBeNull()
  })

  test('a cached story with no position is not a resume — the child gets the picker', () => {
    const storage = primed(linearStory())
    expect(restoreSession(7, 3, storage, NOW)).toBeNull()
  })
})
