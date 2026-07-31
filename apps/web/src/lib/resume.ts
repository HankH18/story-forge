/**
 * Where this reader had got to — the reload half of kid mode's containment
 * (DEVIATIONS 27.4-27.6).
 *
 * The audit that found the back-button leak found this beside it and left it
 * open, because it needed a decision nobody had made: *"Fixing it means putting
 * the phase, the story and the step in the URL or in storage. That is a design
 * decision about whether a kid session is resumable, and §11's route list does
 * not answer it."* (DEVIATIONS 25.6.) Reproduced first, same session, same
 * enforced API: page 4 of 7, one reload, and the child is back at the theme
 * picker — the story gone, the place in it gone, the open `readings` row never
 * completed, and because §7's sort key prefers a story this kid has NOT read,
 * the next tap serves a DIFFERENT one. Nothing errors. The child simply loses
 * the thing they were doing.
 *
 * **The decision: a kid session IS resumable, and "resumable" means the story
 * and the place in it, for thirty minutes, for the same reader, on the same
 * device. Nothing else.** Each bound is a different refusal:
 *
 * 1. **Thirty minutes**, not `lastStory`'s seven days. A resume is about one
 *    sitting — a tablet that slept through a nappy change resumes; tomorrow
 *    morning is a new story, because a child who comes back the next day wants
 *    a new story and because the pool can RETRACT a story (the grammar backfill
 *    moved 4 of 28 servable stories to `rejected` while they were being served,
 *    DEVIATIONS 8.62). Thirty minutes is strictly inside that bound.
 * 2. **Keyed by kid id, and the STORY comes from `lastStory`**, which already
 *    refuses a story above the reader's stage and already shape-checks a
 *    `localStorage` value anyone with the device can hand-edit. This module
 *    stores a position and never a story, so there is exactly one place that
 *    decides whether a stored story may be shown to a child.
 * 3. **Reading, story-end and questions — never celebration.** The celebration
 *    is downstream of `POST /readings/{id}/complete`, whose latch means a replay
 *    returns empty deltas; resuming into it would re-fire a completed reading and
 *    blank the "Sounds you read" card (the triple-`Finish` shape of DEVIATIONS
 *    25.4). Questions ARE resumable and that is deliberate: the reading is
 *    otherwise orphaned — read to the end, never completed, and §8 gets no
 *    evidence at all — and re-answering costs nothing, because
 *    `POST /answers` is `ON CONFLICT DO NOTHING` on `(reading_id, question_id)`
 *    and documents itself as "first answer wins … a client that retries a
 *    question until it guesses correctly changes the feedback it sees and not
 *    the mastery signal".
 * 4. **The reading id is REUSED, not reopened.** A resume that opened a second
 *    `readings` row for the same story would file two readings for one sitting,
 *    which is the shape of the promotion counter that counted readings instead
 *    of distinct stories.
 * 5. **A branch that cannot be replayed exactly refuses the whole resume.** The
 *    live pool is linear (`choice_label` is null on every row), so this is the
 *    branch the code takes only under `?fixture=branch` today — which is exactly
 *    where an off-by-one in a step index would sit unseen until the generator
 *    starts emitting branches.
 *
 * Everything is pure and takes its storage and its clock as arguments, so the
 * refusals are testable in `bun test src`; that a real browser resumes the real
 * story on the real page is `make kid-check`.
 */

import type { Story } from './api'
import { loadLastStory, type StorageLike, defaultStorage } from './lastStory'
import { buildPlan, withChoice, type PlanStep } from './plan'

const KEY_PREFIX = 'sf.resume.v1.'

/** One sitting. See bound 1 above. */
export const RESUME_MAX_AGE_MS = 30 * 60 * 1000

/** The phases a session may be restored INTO. See bound 3 above. */
export type ResumePhase = 'reading' | 'story-end' | 'questions'
const RESUMABLE: readonly string[] = ['reading', 'story-end', 'questions']

export function isResumablePhase(phase: string): phase is ResumePhase {
  return RESUMABLE.includes(phase)
}

/** Where the child was. Never the story itself — see bound 2. */
export interface ResumePoint {
  storyId: number
  step: number
  phase: ResumePhase
  readingId: number | null
  /** Segment ids of the branch arms already chosen, in the order chosen. */
  choices: number[]
}

interface Envelope extends ResumePoint {
  savedAt: number
}

function keyFor(kidId: number): string {
  return `${KEY_PREFIX}${kidId}`
}

function isPositiveInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

/**
 * Remember where this reader is. Best effort in every direction — a full quota,
 * a serialisation failure or a missing `localStorage` are silent no-ops, because
 * a cache is never allowed to be the reason a story stops.
 */
export function saveResume(
  kidId: number,
  point: ResumePoint,
  storage: StorageLike | null = defaultStorage(),
  now: number = Date.now(),
): void {
  if (!storage || !isPositiveInt(kidId)) return
  if (!isPositiveInt(point.storyId)) return
  if (!Number.isInteger(point.step) || point.step < 0) return
  if (!isResumablePhase(point.phase)) return
  try {
    const envelope: Envelope = { ...point, savedAt: now }
    storage.setItem(keyFor(kidId), JSON.stringify(envelope))
  } catch {
    /* quota, private mode, serialisation — a lost resume costs one page turn */
  }
}

/** The child left this story on purpose (← , or the picker). Nothing to come back to. */
export function forgetResume(
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

/**
 * The stored position, if it is still fit to use for `storyId`.
 *
 * `storyId` is passed in rather than trusted from storage: the caller already
 * holds the story `lastStory` was willing to hand back, and a position that
 * belongs to a different story is a position in nothing.
 */
export function loadResume(
  kidId: number,
  storyId: number,
  storage: StorageLike | null = defaultStorage(),
  now: number = Date.now(),
): ResumePoint | null {
  if (!storage || !isPositiveInt(kidId) || !isPositiveInt(storyId)) return null

  let raw: string | null = null
  try {
    raw = storage.getItem(keyFor(kidId))
  } catch {
    return null
  }
  if (!raw) return null

  const drop = () => forgetResume(kidId, storage)

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    drop()
    return null
  }

  const envelope = parsed as Partial<Envelope>
  if (typeof envelope?.savedAt !== 'number') {
    drop()
    return null
  }
  // Expired, or saved in the future — a clock that moved backwards is not
  // evidence that this sitting is still going on.
  if (now - envelope.savedAt > RESUME_MAX_AGE_MS || envelope.savedAt > now) {
    drop()
    return null
  }
  if (!isPositiveInt(envelope.storyId) || envelope.storyId !== storyId) {
    drop()
    return null
  }
  if (typeof envelope.phase !== 'string' || !isResumablePhase(envelope.phase)) {
    drop()
    return null
  }
  if (!Number.isInteger(envelope.step) || (envelope.step as number) < 0) {
    drop()
    return null
  }
  if (envelope.readingId !== null && !isPositiveInt(envelope.readingId)) {
    drop()
    return null
  }
  if (!Array.isArray(envelope.choices) || !envelope.choices.every(isPositiveInt)) {
    drop()
    return null
  }

  return {
    storyId: envelope.storyId,
    step: envelope.step as number,
    phase: envelope.phase,
    readingId: envelope.readingId ?? null,
    choices: envelope.choices,
  }
}

/** The branch arms this plan has already taken, in plan order. */
export function chosenIds(plan: PlanStep[]): number[] {
  return plan
    .filter(
      (s): s is Extract<PlanStep, { kind: 'segment' }> =>
        s.kind === 'segment' && s.segment.parent_segment_id !== null,
    )
    .map((s) => s.segment.id)
}

/**
 * Re-splice the arms a child already chose, or refuse.
 *
 * `null` rather than a best effort: every id has to land on a real option of the
 * choice screen it is supposed to answer, in order, and any left over at the end
 * means the stored plan and this story disagree. A partially replayed plan would
 * put the stored `step` on the wrong page, which is worse than the picker.
 */
export function replayChoices(plan: PlanStep[], choices: number[]): PlanStep[] | null {
  let next = plan.slice()
  let taken = 0
  for (let i = 0; i < next.length; i += 1) {
    const step = next[i]
    if (step.kind !== 'choice') continue
    if (taken >= choices.length) break
    const option = step.options.find((o) => o.id === choices[taken])
    if (!option) return null
    taken += 1
    // The arm is spliced in at `i + 1`; the loop steps onto it next and skips it
    // for the ordinary reason — it is a segment, not a choice screen. A `i += 1`
    // here would be a line no test could ever redden.
    next = withChoice(next, i, option)
  }
  return taken === choices.length ? next : null
}

/** Everything `KidSession` needs to paint the screen the child was last on. */
export interface RestoredSession {
  story: Story
  plan: PlanStep[]
  step: number
  phase: ResumePhase
  readingId: number | null
}

/**
 * The whole restore, in one call: the story `lastStory` is willing to re-serve
 * this reader, the position stored against it, and the plan replayed onto it.
 *
 * Returns `null` for every failure — there is no half-restore, because §01 gives
 * kid mode no error state and "the story you were reading, on some other page"
 * is a lie a five-year-old cannot report.
 */
export function restoreSession(
  kidId: number,
  readerStage: number,
  storage: StorageLike | null = defaultStorage(),
  now: number = Date.now(),
): RestoredSession | null {
  if (!storage) return null
  const story = loadLastStory(kidId, readerStage, storage, now)
  if (!story) return null

  const point = loadResume(kidId, story.id, storage, now)
  if (!point) return null

  const plan = replayChoices(buildPlan(story.segments), point.choices)
  if (!plan) {
    forgetResume(kidId, storage)
    return null
  }
  if (point.step >= plan.length) {
    forgetResume(kidId, storage)
    return null
  }

  return { story, plan, step: point.step, phase: point.phase, readingId: point.readingId }
}
