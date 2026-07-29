/**
 * StoryWordChip — style guide §07 / §12 contract:
 * `word, assetUrl, gloss, autoSpeakOnFirstView`.
 *
 * A story word is the ≤3 theme words per story a reader at this stage cannot
 * decode (§5.5) — "dragon" at stage 1. It is excluded from the decodability
 * denominator precisely because it is *given* to the child rather than sounded
 * out, and the chip is how it is given: a picture, an amber ring, and audio the
 * first time it appears.
 *
 * Three constraints, all from §07:
 *
 * - **It sits inline, in the sentence.** Not a footnote, not a margin note. It
 *   shares `.sf-word`'s geometry with every other tappable word so it cannot
 *   break the reading line's baseline or the 48px target grid.
 * - **It auto-speaks once, on first appearance.** The only autoplay §11 allows.
 *   Interruptible by construction: `speak()` cancels whatever is talking, so a
 *   tap during the auto-speak wins. Whether a word has already been announced
 *   is session state and belongs to the caller — this component only obeys the
 *   flag, so a word repeated three times in a story announces itself once.
 * - **The picture is real art or the §06 placeholder.** Never a broken image,
 *   never an invented URL. `onError` demotes to the placeholder, so an asset row
 *   pointing at a file that is not deployed yet degrades quietly.
 */

import { useEffect, useRef, useState } from 'react'
import Placeholder from './Placeholder'
import type { WordState } from './WordToken'

export interface StoryWordChipProps {
  word: string
  assetUrl?: string | null
  /** Plain-language definition from `story_word_bank.gloss`, when the API has one. */
  gloss?: string | null
  autoSpeakOnFirstView?: boolean
  state?: WordState
  onSpeak: (word: string) => void
}

export default function StoryWordChip({
  word,
  assetUrl,
  gloss,
  autoSpeakOnFirstView = false,
  state = 'rest',
  onSpeak,
}: StoryWordChipProps) {
  const [artFailed, setArtFailed] = useState(false)
  const announced = useRef(false)

  /**
   * DEV NOTE. Under React 18 StrictMode the effect runs, is torn down, and runs
   * again on the same instance. The teardown cancels the utterance and the ref
   * guard (correctly) refuses the second one, so in `bun run dev` a chip often
   * shows the highlight without audible audio. The production bundle does not
   * double-invoke and announces normally — verified on `vite preview`. Do not
   * "fix" this by dropping the guard: that trades a dev-only quirk for a real
   * double-announcement of every story word.
   */
  useEffect(() => {
    if (!autoSpeakOnFirstView || announced.current) return
    announced.current = true
    onSpeak(word)
    // Fires once per mount of a first appearance; `onSpeak` identity is stable
    // enough that re-running on it would re-announce mid-sentence.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoSpeakOnFirstView, word])

  const showArt = Boolean(assetUrl) && !artFailed
  const glossId = gloss ? `gloss-${word}` : undefined

  return (
    <>
      <button
        type="button"
        data-state={state}
        aria-label={`say the word ${word}`}
        aria-describedby={glossId}
        onClick={() => onSpeak(word)}
        className="sf-word sf-chip"
      >
        <span className="sf-chip-art" aria-hidden="true">
          {showArt ? (
            <img
              src={assetUrl ?? undefined}
              alt=""
              onError={() => setArtFailed(true)}
              className="h-full w-full object-contain"
            />
          ) : (
            <Placeholder ratio="1:1" showLabel={false} label="" className="h-full w-full" />
          )}
        </span>
        <span>{word}</span>
      </button>
      {/* §11: the gloss is for assistive tech; the picture is the child's gloss. */}
      {gloss ? (
        <span id={glossId} className="sr-only">
          {gloss}
        </span>
      ) : null}
    </>
  )
}
