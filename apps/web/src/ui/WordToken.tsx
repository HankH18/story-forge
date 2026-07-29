/**
 * WordToken — style guide §07 / §12 contract: `word, onSpeak, state`.
 *
 * Every word a child reads is a `<button>` inside the paragraph flow. Not a
 * span with a click handler: a button is in the tab order, fires on Enter and
 * Space for free, and announces itself as an action. §11 requires the reading
 * view to be fully keyboard-navigable — Tab through words, Enter to speak.
 *
 * The accessible name is `say the word ship`, not `ship`. A child using a
 * screen reader hears what the control *does*; a child using their finger sees
 * the word. Punctuation is never inside the button (see lib/text.ts).
 *
 * Geometry lives in `.sf-word` (styles/index.css) because it is load-bearing
 * and not expressible as utilities: 12px of vertical padding beyond the glyph
 * box per §07, inline-block so that padding actually grows the line box instead
 * of overlapping the line above, and 4px vertical margin so two stacked targets
 * keep §11's 8px gap. The font size and family are inherited from the story
 * paragraph, so the Aa toggle moves the targets with the text.
 *
 * `state` is the §05 motion contract, not decoration:
 *   rest      untouched
 *   speaking  gold-300 fill + scale 1.05, held 250ms past the start of speech
 *   heard     a gold underline — "you already had this one"
 */

export type WordState = 'rest' | 'speaking' | 'heard'

export interface WordTokenProps {
  word: string
  state?: WordState
  onSpeak: (word: string) => void
  /** Set on decorative repeats so only the reading copy takes tab focus. */
  tabIndex?: number
  className?: string
}

export default function WordToken({
  word,
  state = 'rest',
  onSpeak,
  tabIndex,
  className = '',
}: WordTokenProps) {
  return (
    <button
      type="button"
      data-state={state}
      aria-label={`say the word ${word}`}
      tabIndex={tabIndex}
      onClick={() => onSpeak(word)}
      className={`sf-word ${className}`}
    >
      {word}
    </button>
  )
}
