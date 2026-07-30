/**
 * Reading view — "the whole product" (style guide §10), §11.2 step 2.
 *
 * Layout is the 1024×768 landscape tablet reference: art left 50%, text right
 * 50%; portrait stacks art on top at 4:3 with text below. One segment per
 * screen and no scrolling — §10 is blunt about the failure mode: "if text
 * overflows, generation length is wrong, not the layout."
 *
 * The two columns align to the TOP, not the middle. §10 asks for vertically
 * centred, and that is what this was — but centring an overflowing grid row
 * gives it a negative top offset, which put the art behind the story text at
 * every portrait viewport (§06: "Never behind text") and pushed the tail of a
 * long segment under the Next button with no scrollbar to reach it. Centred
 * text that is unreadable is not the more faithful reading of §10.
 *
 * Chrome is exactly three things: back, progress dots, font-size toggle. No
 * settings gear, no share, no account, no home — §13's kid-mode surface is a
 * closed room. `Next` is bottom-right, 64px, and is the only way forward: no
 * swipe-only navigation, because a swipe is invisible to a child who has not
 * been told about it and unusable with a keyboard. §11 also asks for `→` to
 * advance, which is wired below without stealing Enter (Enter speaks the
 * focused word).
 *
 * Everything decodable on this screen is Andika and tappable (see StoryText).
 *
 * **When a segment does not fit, the way forward says so** (DEVIATIONS 13.1).
 * §10's sentence is about generation length and this file does not argue with
 * it — but a too-long segment is in the pool today, the clip lands BETWEEN lines
 * so nothing is visibly sliced, and the button underneath said `Finish`. A child
 * pressed it having never seen the ending. So while any of the segment is below
 * the clip edge the primary button is `More words ↓` and scrolls; only the last
 * line turns it back into `Next`/`Finish`. `make fit-check` measures the same box
 * over the whole pool so the generator's half of this is not invisible.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Segment, Story } from '../lib/api'
import { cutoutFor } from '../lib/art'
import Button from '../ui/Button'
import ProgressDots from '../ui/ProgressDots'
import SegmentArt from '../ui/SegmentArt'
import StoryText, { type ReadingSize } from '../ui/StoryText'

/**
 * The §10 reading box, as one string, because `scripts/fit-check.mjs` imports it
 * and measures the pool against exactly the box this renders (DEVIATIONS 13.2).
 * A gate that re-spells the layout it is checking measures a different layout the
 * first time either side is edited.
 *
 * Written as a literal, never composed: Tailwind's extractor scans source text,
 * and a class assembled at runtime is a class purged from the production bundle
 * (STATE invariant 7).
 */
export const READING_MAIN_CLASS =
  'sf-page-in mx-auto flex w-full min-h-0 max-w-[1024px] flex-1 flex-col gap-8 overflow-y-auto py-4 pb-8 [scrollbar-gutter:stable] [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-ink-300 [&::-webkit-scrollbar]:w-3 lg:grid lg:grid-cols-2 lg:content-start lg:items-start'

/**
 * The clip-edge lip, painted only while there is more of the page below.
 *
 * An INSET shadow, and that is the whole reason it is allowed to exist: an inset
 * shadow paints above the element's background and below its content, so it
 * shades the paper at the bottom of the reading box and never lands on a glyph.
 * The obvious alternative — a gradient fade over the last visible line — buys the
 * same cue by taking contrast off the exact words the child is reading, and §11
 * puts a 7:1 floor under story text with the word "non-negotiable" over it.
 */
const EDGE_CLASS: Record<'more' | 'end', string> = {
  more: 'shadow-[inset_0_-10px_10px_-10px_rgba(43,33,24,0.35)]',
  end: '',
}

/** Overlap kept between screenfuls, so a press never jumps a whole line away. */
const SCROLL_OVERLAP_PX = 72

/** Sub-pixel line heights make an exact bottom comparison flap; 2px of slack. */
const AT_END_SLACK_PX = 2

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** §07's chevron idiom, pointing at the words the clip edge is hiding. */
function MoreBelowIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className="h-7 w-7 shrink-0">
      <path
        d="M6 9l6 6 6-6"
        fill="none"
        stroke="var(--sf-gold-700)"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export interface ReadingViewProps {
  story: Story
  segment: Segment
  /** Reading screens in this run (branches included), for the dots. */
  totalSegments: number
  segmentOrdinal: number
  size: ReadingSize
  onToggleSize: () => void
  onBack: () => void
  onNext: () => void
  /** Last reading screen — the button says so rather than lying with "Next". */
  isLast: boolean
  shouldAnnounce: (word: string) => boolean
  onAnnounced: (word: string) => void
}

export default function ReadingView({
  story,
  segment,
  totalSegments,
  segmentOrdinal,
  size,
  onToggleSize,
  onBack,
  onNext,
  isLast,
  shouldAnnounce,
  onAnnounced,
}: ReadingViewProps) {
  const box = useRef<HTMLElement | null>(null)
  /**
   * Has the child reached the bottom of this page? `true` for every segment that
   * fits, which is the overwhelming majority — the whole affordance below is
   * invisible until the pool hands over a page that does not.
   */
  const [atEnd, setAtEnd] = useState(true)

  /**
   * "Is anything a child could READ still below the clip edge?" — not "does this
   * box scroll", which is a different and wronger question.
   *
   * `pb-8` is 32px of deliberate blank space at the end of the scroll (it keeps
   * the last line off the footer), and `.sf-word` carries 12px of padding and 5px
   * of margin under its glyph box. Nine segments in the live pool overflow by
   * exactly 6px at 40px portrait: no word is hidden, only the tail of that
   * blankness. Measuring raw `scrollHeight` would put `More words ↓` on a page
   * whose every word is on screen — a false alarm on the one control the child
   * has, which is how an affordance stops being believed. So the content bottom
   * is `scrollHeight` less the container's own bottom padding. A genuinely hidden
   * line is 62-78px tall and clears this by a wide margin.
   */
  const measure = useCallback(() => {
    const el = box.current
    if (!el) return
    const padBottom = Number.parseFloat(window.getComputedStyle(el).paddingBottom) || 0
    const contentBottom = el.scrollHeight - padBottom
    setAtEnd(el.scrollTop + el.clientHeight >= contentBottom - AT_END_SLACK_PX)
  }, [])

  // A new page starts at the top, and it starts by asking whether it fits.
  // Layout effect: measuring after paint would flash `Finish` on a clipped page.
  useLayoutEffect(() => {
    const el = box.current
    if (el) el.scrollTop = 0
    measure()
  }, [measure, segment.id, size])

  /**
   * Re-measure on anything that can change the answer: the viewport (a tablet
   * rotating), the paragraph reflowing, and the WEB FONT arriving — Andika loads
   * after first paint and a paragraph set in the fallback face is a different
   * height, which is exactly the kind of "measured once, wrongly" this file has
   * already been bitten by.
   */
  useEffect(() => {
    const el = box.current
    if (!el) return
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    for (const child of Array.from(el.children)) observer.observe(child)
    window.addEventListener('resize', measure)
    if (typeof document !== 'undefined' && document.fonts) {
      void document.fonts.ready.then(measure).catch(() => {})
    }
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [measure, segment.id])

  /**
   * The one way forward, and it does the honest thing with the page it is on:
   * more of this segment first, the next screen only after the last line.
   */
  const advance = useCallback(() => {
    const el = box.current
    if (el && !atEnd) {
      const step = Math.max(el.clientHeight - SCROLL_OVERLAP_PX, Math.round(el.clientHeight * 0.6))
      el.scrollBy({ top: step, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
      // `scrollBy` fires `scroll`, which re-measures; this covers the case where
      // it does not move at all because we were already there.
      window.setTimeout(measure, 0)
      return
    }
    onNext()
  }, [atEnd, measure, onNext])

  // §11: "Tab through words, Enter to speak, → for next." `→` goes through
  // `advance`, so the keyboard cannot skip what the button will not.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' && !e.metaKey && !e.ctrlKey && !e.altKey) advance()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [advance])

  return (
    // `h-screen` + a scrollable middle, not a scrollable page. §10 wants one
    // segment per screen and treats overflow as a generation-length bug — but
    // when a too-long segment does arrive (the warm pool has 200-character
    // ones), the chrome and the Next button must stay put. A page that scrolls
    // takes the only way forward off the bottom of the screen.
    <div className="flex h-screen flex-col gap-4 px-6 py-5">
      {/* §10 chrome. Nothing else may be added here. */}
      <header className="flex items-center justify-between gap-4">
        <Button
          variant="kid-quiet"
          size="md"
          onClick={onBack}
          aria-label="go back to choosing a story"
          className="px-4 text-[22px]"
        >
          <span aria-hidden="true">←</span>
        </Button>

        <ProgressDots total={totalSegments} current={segmentOrdinal} />

        <Button
          variant="kid-quiet"
          size="md"
          onClick={onToggleSize}
          aria-pressed={size === 'large'}
          aria-label={size === 'large' ? 'smaller words' : 'bigger words'}
          className="px-4"
        >
          <span aria-hidden="true" className="font-read text-[22px]">
            Aa
          </span>
        </Button>
      </header>

      {/* `key` on the segment id so the §05 page-in animation replays and the
          word-tap state resets when the page turns. */}
      {/* PORTRAIT IS A FLEX COLUMN, LANDSCAPE IS THE GRID. It was a
          one-column grid in portrait, and that is what put the illustration
          behind the story text at every portrait viewport — §06 is explicit:
          "Never behind text."

          `SegmentArt` sizes itself with `aspect-ratio: 4/3` off a full-width
          box. An `auto` grid row does NOT size to an aspect-ratio item's block
          size — the inline size it would be derived from is what the track is
          still resolving — so the art's row collapsed to its (empty) content
          height and the 531px picture spilled out of a 251px track. Whichever
          way `align-items` pointed, it spilled onto its neighbour: `items-center`
          pushed it UP (measured artBox.y = -93.3 at 768x1024, off the top of a
          scroll container and unreachable), `items-start` pushes it DOWN, onto
          the first six lines of the paragraph. Alignment was never the fix.
          Flex sizes the aspect-ratio box correctly, so art and text stack.

          `min-h-0` — a flex child's `min-height` defaults to `auto`, so `flex-1`
          alone cannot shrink `main` below its content and `overflow-y-auto`
          never engages; the tail of a long segment then grows past the viewport
          instead of scrolling inside it (DEVIATIONS 5.25).

          `pb-8` plus the parent's `gap-4` keep the last line clear of the
          footer, at the bottom of the scroll AND at the clip edge.

          The scrollbar is styled rather than left to the platform: macOS paints
          OVERLAY scrollbars, invisible until something is already scrolling
          (measured `offsetWidth - clientWidth === 0`), so the only signal that
          more words existed was a chip sliced in half at the bottom edge. */}
      <main
        key={segment.id}
        ref={box}
        onScroll={measure}
        className={`${READING_MAIN_CLASS} ${EDGE_CLASS[atEnd ? 'end' : 'more']}`}
      >
        {/* `shrink-0`: the 4:3 frame is §06's contract and must not be squashed
            to make a long segment fit — that is what the scroll is for. */}
        <SegmentArt
          asset={segment.asset}
          themeSlug={story.theme_slug}
          className="w-full shrink-0"
        />
        {/* `assetFor` was never passed, which is why every story-word chip in
            the live app showed a grey striped square instead of a picture —
            §07's "picture cutout on the left" had no picture to render
            (DEVIATIONS 11.24, 11.25). */}
        <StoryText
          text={segment.body}
          size={size}
          storyWords={story.story_words}
          assetFor={cutoutFor}
          shouldAnnounce={shouldAnnounce}
          onAnnounced={onAnnounced}
        />
      </main>

      {/* The cue lives IN the footer row, not in a strip above it: a strip would
          cost `main` its own height plus a flex gap and push more segments over
          the clip edge — the fix would have widened the defect (13.1). */}
      <footer className="flex items-center justify-between gap-4">
        {atEnd ? (
          <span />
        ) : (
          <span className="flex items-center gap-2">
            <MoreBelowIcon />
            {/* §12: one Kalam phrase per screen. This is the reading view's. */}
            <span className="font-hand text-[20px] text-ink-700">keep reading</span>
          </span>
        )}
        <Button
          variant="kid-primary"
          size="kid"
          onClick={advance}
          aria-label={atEnd ? undefined : 'more words on this page'}
        >
          {atEnd ? (
            <>
              {isLast ? 'Finish' : 'Next'} <span aria-hidden="true">→</span>
            </>
          ) : (
            <>
              More words <span aria-hidden="true">↓</span>
            </>
          )}
        </Button>
      </footer>
    </div>
  )
}
