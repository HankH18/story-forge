/**
 * `lib/plan.ts` — segments → the sequence of kid screens (§10).
 *
 * §10: "linear spine with exactly 0–2 branch points … at a branch, 2 choices;
 * each choice is one segment; branches remerge to the spine. Max 7 segments."
 *
 * Two rules here are child-facing rather than structural, and they are what
 * these tests are for. **A branch with fewer than two labelled arms is not a
 * branch** — a "choose" screen with one option is a dead end that also lies about
 * offering a choice. And **the dot row must not grow when a choice is made**
 * (§07: the row is a shape, not a countdown), which is why `expectedSegmentCount`
 * counts an unresolved branch as the one segment it is about to become.
 */

import { describe, expect, test } from 'bun:test'
import type { Segment } from './api'
import {
  buildPlan,
  expectedSegmentCount,
  readText,
  segmentCount,
  segmentOrdinal,
  withChoice,
} from './plan'

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

/** Three spine segments, with two labelled arms hanging off the middle one. */
const BRANCHED: Segment[] = [
  segment({ id: 10, idx: 0, body: 'Sam sat.' }),
  segment({ id: 11, idx: 1, body: 'A cat ran in.' }),
  segment({ id: 12, idx: 2, body: 'Sam ran home.' }),
  segment({ id: 20, idx: 0, parent_segment_id: 11, choice_label: 'Pat the cat', body: 'Sam pat it.' }),
  segment({ id: 21, idx: 1, parent_segment_id: 11, choice_label: 'Run', body: 'Sam ran.' }),
]

describe('buildPlan', () => {
  test('the spine is every parentless segment in idx order', () => {
    const plan = buildPlan([
      segment({ id: 3, idx: 2 }),
      segment({ id: 1, idx: 0 }),
      segment({ id: 2, idx: 1 }),
    ])
    expect(plan.map((s) => (s.kind === 'segment' ? s.segment.id : 'choice'))).toEqual([1, 2, 3])
  })

  test('a branch point becomes a choice screen after its segment', () => {
    const plan = buildPlan(BRANCHED)
    expect(plan.map((s) => (s.kind === 'segment' ? s.segment.id : `choice:${s.fromId}`))).toEqual([
      10,
      11,
      'choice:11',
      12,
    ])
  })

  test('one labelled arm is NOT a branch', () => {
    const plan = buildPlan([
      segment({ id: 10, idx: 0 }),
      segment({ id: 20, idx: 0, parent_segment_id: 10, choice_label: 'Only one' }),
    ])
    expect(plan.every((s) => s.kind === 'segment')).toBe(true)
    expect(segmentCount(plan)).toBe(1)
  })

  test('an unlabelled arm is not an option, so two arms with one label is not a branch', () => {
    // The label is the tappable text. An arm with none would render a button
    // with no words in it.
    const plan = buildPlan([
      segment({ id: 10, idx: 0 }),
      segment({ id: 20, idx: 0, parent_segment_id: 10, choice_label: 'Pat it' }),
      segment({ id: 21, idx: 1, parent_segment_id: 10, choice_label: '  ' }),
    ])
    expect(plan.every((s) => s.kind === 'segment')).toBe(true)
  })

  test('a branch is capped at two arms (§10: exactly two)', () => {
    const plan = buildPlan([
      segment({ id: 10, idx: 0 }),
      segment({ id: 20, idx: 0, parent_segment_id: 10, choice_label: 'A' }),
      segment({ id: 21, idx: 1, parent_segment_id: 10, choice_label: 'B' }),
      segment({ id: 22, idx: 2, parent_segment_id: 10, choice_label: 'C' }),
    ])
    const choice = plan.find((s) => s.kind === 'choice')
    expect(choice?.kind === 'choice' && choice.options.map((o) => o.id)).toEqual([20, 21])
  })

  test('no segments is no plan, not a crash', () => {
    expect(buildPlan([])).toEqual([])
  })
})

describe('withChoice', () => {
  test('the chosen arm is spliced in directly after its choice screen', () => {
    const plan = buildPlan(BRANCHED)
    const at = plan.findIndex((s) => s.kind === 'choice')
    const next = withChoice(plan, at, BRANCHED[3])
    expect(next.map((s) => (s.kind === 'segment' ? s.segment.id : 'choice'))).toEqual([
      10,
      11,
      'choice',
      20,
      12,
    ])
  })

  test('the original plan is not mutated', () => {
    const plan = buildPlan(BRANCHED)
    const before = plan.length
    withChoice(plan, 2, BRANCHED[3])
    expect(plan.length).toBe(before)
  })
})

describe('expectedSegmentCount', () => {
  test('does not change when a choice is made (§07: a shape, not a countdown)', () => {
    const plan = buildPlan(BRANCHED)
    const at = plan.findIndex((s) => s.kind === 'choice')
    const before = expectedSegmentCount(plan)
    const after = expectedSegmentCount(withChoice(plan, at, BRANCHED[3]))
    expect(before).toBe(4)
    expect(after).toBe(before)
  })

  test('a plan with no branches is just its segments', () => {
    const plan = buildPlan([segment({ id: 1, idx: 0 }), segment({ id: 2, idx: 1 })])
    expect(expectedSegmentCount(plan)).toBe(2)
    expect(segmentCount(plan)).toBe(2)
  })

  test('resolution is recognised by parentage, not by position alone', () => {
    // The unresolved branch counts as one; once the arm that belongs to THAT
    // branch point follows it, the arm is the one it was counting.
    const plan = buildPlan(BRANCHED)
    const at = plan.findIndex((s) => s.kind === 'choice')
    const wrongArm = segment({ id: 99, idx: 0, parent_segment_id: 999, choice_label: 'x' })
    expect(expectedSegmentCount(withChoice(plan, at, wrongArm))).toBe(5)
  })
})

describe('segmentOrdinal', () => {
  test('choice screens do not advance the dot row', () => {
    const plan = buildPlan(BRANCHED)
    expect(segmentOrdinal(plan, 0)).toBe(0) // on segment 10
    expect(segmentOrdinal(plan, 1)).toBe(1) // on segment 11
    expect(segmentOrdinal(plan, 2)).toBe(2) // on the choice screen
    expect(segmentOrdinal(plan, 3)).toBe(2) // on segment 12 — still the third dot
  })

  test('an index past the end does not run off the array', () => {
    const plan = buildPlan(BRANCHED)
    expect(segmentOrdinal(plan, 99)).toBe(segmentCount(plan))
  })
})

describe('readText', () => {
  test('is everything read so far, and nothing that was not', () => {
    const plan = buildPlan(BRANCHED)
    expect(readText(plan)).toBe('Sam sat. A cat ran in. Sam ran home.')
    const at = plan.findIndex((s) => s.kind === 'choice')
    expect(readText(withChoice(plan, at, BRANCHED[3]))).toContain('Sam pat it.')
    // The arm NOT taken is not in the text the mcq2 grader sees.
    expect(readText(withChoice(plan, at, BRANCHED[3]))).not.toContain('Sam ran.')
  })
})
