/**
 * The §10 fit-check harness — the browser half of `bun run fit-check`
 * (DEVIATIONS 13.2).
 *
 * §10: *"One segment per screen, no scrolling on tablet — if text overflows,
 * generation length is wrong, not the layout."* Nothing in this repo has ever
 * checked that. `warm-check` counts servable stories, `promote-check` counts §8
 * evidence, `prompt-check` reads what a question says, and 8-10% of the pages in
 * the pool did not fit the screen while all three were green.
 *
 * **It renders the real `ReadingView`.** Not a replica of it: the actual
 * component, in the actual production bundle, against the actual purged CSS and
 * the actual bundled Andika. A harness that re-spells the layout measures a
 * layout the app does not have, and STATE invariant 7 is a whole hard-won line
 * about how far dev CSS and built CSS can drift.
 *
 * Two deliberate choices about what is measured:
 *
 * - **Every segment is measured as if it were the first screen of the reading**
 *   (`shouldAnnounce` always true), so every story word gets its `.sf-chip` —
 *   28px of cutout, 7px of margin and 6px of padding wider than a plain token.
 *   A segment that passes has passed its own worst case.
 * - **`asset` is null**, so the frame renders §06's placeholder. The frame is
 *   `aspect-ratio: 4/3` in both cases, so the geometry is identical and the
 *   sweep does not depend on 800 image loads finishing.
 *
 * Nothing here is reachable from the app: it is its own entry, built only when
 * SF_FIT_HARNESS is set.
 */

import { createRoot } from 'react-dom/client'
import { flushSync } from 'react-dom'
import type { Segment, Story } from '../src/lib/api'
import ReadingView from '../src/routes/ReadingView'
import type { ReadingSize } from '../src/ui/StoryText'
import '../src/styles/index.css'

/** One row of `fit-segments.json`, written by `scripts/fit-check.mjs`. */
interface Row {
  segment_id: number
  story_id: number
  stage_id: number
  theme_slug: string
  idx: number
  last_idx: number
  body: string
  story_words: string[]
}

interface Result {
  segment_id: number
  story_id: number
  stage_id: number
  idx: number
  chars: number
  /**
   * Pixels of TEXT below the clip edge — not `scrollHeight - clientHeight`.
   *
   * The box's own `pb-8` is 32px of deliberate blank space and `.sf-word` carries
   * 12px of padding and 5px of margin under its glyph box, so a segment can make
   * the box scrollable by 6px with every word on screen. Nine live segments do
   * exactly that at 40px portrait. A gate that called those failures would be
   * permanently red for a reason no length budget can fix, and a gate nobody can
   * make green is a gate nobody reads. What is measured is what a child loses.
   */
  over: number
  /** Word tokens entirely below the clip edge — the ones a child never sees. */
  hidden: number
  box: number
}

const root = createRoot(document.getElementById('root') as HTMLElement)
const noop = () => {}

function measure(row: Row, size: ReadingSize): Result {
  const segment: Segment = {
    id: row.segment_id,
    idx: row.idx,
    body: row.body,
    scene_tags: [],
    parent_segment_id: null,
    choice_label: null,
    asset: null,
  }
  const story = {
    id: row.story_id,
    stage_id: row.stage_id,
    theme_id: 0,
    theme_slug: row.theme_slug,
    theme_display_name: row.theme_slug,
    status: 'warm',
    title: '',
    decodability: 1,
    story_words: row.story_words,
    segments: [segment],
    questions: [],
    fallback: null,
  } satisfies Story

  // `flushSync` so the DOM is laid out before the next line reads it. The
  // component's own `key={segment.id}` remounts `StoryText`, which is what makes
  // each row's story-word introductions independent of the row before it.
  flushSync(() => {
    root.render(
      <ReadingView
        story={story}
        segment={segment}
        totalSegments={row.last_idx + 1}
        segmentOrdinal={row.idx}
        size={size}
        onToggleSize={noop}
        onBack={noop}
        onNext={noop}
        isLast={row.idx === row.last_idx}
        shouldAnnounce={() => true}
        onAnnounced={noop}
      />,
    )
  })

  const box = document.querySelector('main')
  const paragraph = box?.querySelector('p')
  if (!box || !paragraph) throw new Error('fit-check: no §10 box rendered')

  const edge = box.getBoundingClientRect().bottom
  let deepest = edge
  let hidden = 0
  for (const part of Array.from(paragraph.children)) {
    const r = part.getBoundingClientRect()
    if (r.bottom > deepest) deepest = r.bottom
    // `.sf-word` puts 12px of padding under the glyph box, so "the top of the
    // box is past the edge" is the honest test for "this word is not on screen".
    if (r.top >= edge) hidden += 1
  }

  return {
    segment_id: row.segment_id,
    story_id: row.story_id,
    stage_id: row.stage_id,
    idx: row.idx,
    chars: row.body.length,
    over: Math.round(deepest - edge),
    hidden,
    box: box.clientHeight,
  }
}

declare global {
  interface Window {
    /** Called once per (viewport, size) by `scripts/fit-check.mjs`. */
    __fitSweep?: (size: ReadingSize) => Promise<Result[]>
    __fitReady?: boolean
  }
}

async function rows(): Promise<Row[]> {
  const res = await fetch('./fit-segments.json', { cache: 'no-store' })
  if (!res.ok) throw new Error(`fit-check: fit-segments.json → ${res.status}`)
  return (await res.json()) as Row[]
}

window.__fitSweep = async (size) => {
  const all = await rows()
  return all.map((row) => measure(row, size))
}
window.__fitReady = true
