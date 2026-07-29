/**
 * Turning a story's segments into the sequence of kid screens — §10.
 *
 * §10: "linear spine with exactly 0–2 branch points … at a branch, 2 choices;
 * each choice is one segment; branches remerge to the spine. Max 7 segments."
 * So the spine is every segment with no parent, in `idx` order, and a branch is
 * the two segments that hang off a spine segment by `parent_segment_id`.
 *
 * The plan is built once and grows by exactly one segment step each time a
 * choice is made — the chosen arm is spliced in directly after its choice
 * screen, which is what "remerges to the spine" means concretely: the next
 * spine segment follows the arm with nothing special happening.
 *
 * A branch point with fewer than two labelled arms is *not* a branch. Rendering
 * a "choose" screen with one option would be a dead end that also lies about
 * having a choice, so it is dropped and the arm never shows.
 */

import type { Segment } from './api'

export type PlanStep =
  | { kind: 'segment'; segment: Segment }
  | { kind: 'choice'; fromId: number; options: Segment[] }

/** §07/§10: the dot row is capped at 7 and is never numeric. */
export const MAX_DOTS = 7

const byIdx = (a: Segment, b: Segment) => a.idx - b.idx

export function buildPlan(segments: Segment[]): PlanStep[] {
  const spine = segments.filter((s) => s.parent_segment_id === null).sort(byIdx)
  const steps: PlanStep[] = []
  for (const segment of spine) {
    steps.push({ kind: 'segment', segment })
    const options = segments
      .filter((s) => s.parent_segment_id === segment.id && (s.choice_label ?? '').trim() !== '')
      .sort(byIdx)
      .slice(0, 2) // §10: exactly two arms
    if (options.length === 2) steps.push({ kind: 'choice', fromId: segment.id, options })
  }
  return steps
}

/** Splice the chosen arm in immediately after the choice screen at `at`. */
export function withChoice(plan: PlanStep[], at: number, chosen: Segment): PlanStep[] {
  const next = plan.slice()
  next.splice(at + 1, 0, { kind: 'segment', segment: chosen })
  return next
}

/** How many reading screens this plan currently holds. */
export function segmentCount(plan: PlanStep[]): number {
  return plan.filter((s) => s.kind === 'segment').length
}

/**
 * How many reading screens this run will end up with — an unresolved branch
 * counts as the one segment it is about to become.
 *
 * The dots would otherwise grow by one the moment a choice is made, which reads
 * as "the story got longer because you chose". §07 wants the row to be a shape,
 * not a countdown; a shape that changes under you is worse than a number.
 */
export function expectedSegmentCount(plan: PlanStep[]): number {
  let n = 0
  plan.forEach((step, i) => {
    if (step.kind === 'segment') {
      n += 1
      return
    }
    const next = plan[i + 1]
    const resolved =
      next !== undefined &&
      next.kind === 'segment' &&
      next.segment.parent_segment_id === step.fromId
    if (!resolved) n += 1
  })
  return n
}

/** Which reading screen `stepIndex` is, ignoring choice screens. */
export function segmentOrdinal(plan: PlanStep[], stepIndex: number): number {
  let n = 0
  for (let i = 0; i < stepIndex && i < plan.length; i += 1) {
    if (plan[i].kind === 'segment') n += 1
  }
  return n
}

/** Everything the child has read so far, for the mcq2 fallback grader. */
export function readText(plan: PlanStep[]): string {
  return plan
    .filter((s): s is Extract<PlanStep, { kind: 'segment' }> => s.kind === 'segment')
    .map((s) => s.segment.body)
    .join(' ')
}
