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
 */

import { useEffect } from 'react'
import type { Segment, Story } from '../lib/api'
import { cutoutFor } from '../lib/art'
import Button from '../ui/Button'
import ProgressDots from '../ui/ProgressDots'
import SegmentArt from '../ui/SegmentArt'
import StoryText, { type ReadingSize } from '../ui/StoryText'

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
  // §11: "Tab through words, Enter to speak, → for next."
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' && !e.metaKey && !e.ctrlKey && !e.altKey) onNext()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onNext])

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
        className="sf-page-in mx-auto flex w-full min-h-0 max-w-[1024px] flex-1 flex-col
                   gap-8 overflow-y-auto py-4 pb-8
                   [scrollbar-gutter:stable] [&::-webkit-scrollbar-thumb]:rounded-full
                   [&::-webkit-scrollbar-thumb]:bg-ink-300 [&::-webkit-scrollbar]:w-3
                   lg:grid lg:grid-cols-2 lg:content-start lg:items-start"
      >
        {/* `shrink-0`: the 4:3 frame is §06's contract and must not be squashed
            to make a long segment fit — that is what the scroll is for. */}
        <SegmentArt asset={segment.asset} className="w-full shrink-0" />
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

      <footer className="flex justify-end">
        <Button variant="kid-primary" size="kid" onClick={onNext}>
          {isLast ? 'Finish' : 'Next'} <span aria-hidden="true">→</span>
        </Button>
      </footer>
    </div>
  )
}
