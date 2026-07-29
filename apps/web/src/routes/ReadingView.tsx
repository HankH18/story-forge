/**
 * Reading view — "the whole product" (style guide §10), §11.2 step 2.
 *
 * Layout is the 1024×768 landscape tablet reference: art left 50%, text right
 * 50%, vertically centred; portrait stacks art on top at 4:3 with text below.
 * One segment per screen and no scrolling — §10 is blunt about the failure
 * mode: "if text overflows, generation length is wrong, not the layout."
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
    <div className="flex h-screen flex-col px-6 py-5">
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
      <main
        key={segment.id}
        className="sf-page-in mx-auto grid w-full max-w-[1024px] flex-1 grid-cols-1
                   items-center gap-8 overflow-y-auto py-4 lg:grid-cols-2"
      >
        <SegmentArt asset={segment.asset} className="w-full" />
        <StoryText
          text={segment.body}
          size={size}
          storyWords={story.story_words}
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
