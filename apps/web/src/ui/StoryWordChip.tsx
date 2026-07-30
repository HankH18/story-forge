/**
 * StoryWordChip — style guide §07 / §12 contract:
 * `word, assetUrl, gloss, autoSpeakOnFirstView`.
 *
 * A story word is the ≤3 theme words per story a reader at this stage cannot
 * decode (§5.5) — "dragon" at stage 1. It is excluded from the decodability
 * denominator precisely because it is *given* to the child rather than sounded
 * out, and the chip is how it is given: a picture, a warm amber ground, and
 * audio, once, the first time the word appears.
 *
 * **Rendered on the FIRST APPEARANCE ONLY** — that decision lives in
 * `lib/storyWords.ts` and `StoryText`, because it is a fact about the paragraph
 * and the session, not about this component. §07's sentence ends "...then
 * behaves like any tappable word", and every later occurrence is a plain
 * `WordToken` (DEVIATIONS 11.26).
 *
 * **There is no ring.** §07 asks for an amber ring and this deliberately
 * deviates (11.27). A 2px outline with a 10px radius around a word is, to a
 * five-year-old, a box — and the user's report was literally "boxes around
 * them ... boxes on a page mean mistakes". §02 keeps the berry violation colour
 * out of kid mode for exactly that reason; an amber box is the same mistake in a
 * warmer hue. What marks the word is the PICTURE. A word with a small drawing
 * beside it reads as help at any age, and it is the one treatment on a reading
 * line that cannot be mistaken for a correction.
 *
 * The rest is §07 unchanged:
 *
 * - **It sits inline, in the sentence.** Not a footnote, not a margin note. It
 *   shares `.sf-word`'s geometry with every other tappable word so it cannot
 *   break the reading line's baseline or the 48px target grid.
 * - **It auto-speaks once, on first appearance.** The only autoplay §11 allows.
 *   Interruptible by construction: `speak()` cancels whatever is talking, so a
 *   tap during the auto-speak wins. Whether a word has already been announced
 *   is session state and belongs to the caller — this component only obeys the
 *   flag.
 * - **The picture is real art, or there is no picture.** Never a broken image,
 *   never an invented URL. It used to fall back to the 1:1 `Placeholder`, which
 *   is how every chip in the live app came to carry a 24px grey striped square:
 *   no cutouts existed and `assetFor` was never wired up. §06's "never an empty
 *   box" governs the 4:3 SEGMENT frame — a hole in the layout that must not
 *   collapse — and a 24px square inside a word is not that hole; it is
 *   decoration that means nothing (11.25). `onError` drops the picture the same
 *   way, so an asset that is not deployed yet degrades to a plain amber word.
 */

import { useEffect, useRef, useState } from 'react'
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
        {showArt ? (
          <span className="sf-chip-art" aria-hidden="true">
            <img
              src={assetUrl ?? undefined}
              alt=""
              onError={() => setArtFailed(true)}
              className="h-full w-full object-contain"
            />
          </span>
        ) : null}
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
