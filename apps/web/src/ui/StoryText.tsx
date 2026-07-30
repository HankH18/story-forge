/**
 * StoryText — the assembly the §12 build order points at: "Button, Card,
 * WordToken, StoryWordChip, ProgressDots → the whole reading view is then
 * assembly."
 *
 * Renders one run of validated story text as tap targets: word buttons, story
 * word chips, and inert punctuation. Used for segment bodies, branch choice
 * labels, and question prompts — everything a child has to decode is Andika and
 * everything Andika is tappable.
 *
 * Typography here is §03, and the two sizes are the whole "Aa" toggle: 34px /
 * line-height 1.65, or 40px / 1.9. 28px is the absolute floor and nothing in
 * this component can go below it. Left-aligned always; no justification, no
 * hyphenation, no italics.
 *
 * Speech state is owned here rather than by the session, because "which word is
 * lit" is per-paragraph and dies with it. What the session *does* own is which
 * story words have already announced themselves (`shouldAnnounce`), since that
 * is a fact about the reading, not about this paragraph.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { cancelSpeech, speak, HOLD_AFTER_START_MS } from '../lib/speech'
import { introductionIndices, storyWordSet } from '../lib/storyWords'
import { splitText } from '../lib/text'
import StoryWordChip from './StoryWordChip'
import WordToken from './WordToken'

export type ReadingSize = 'default' | 'large'

/** §03: 34px default, 40px toggle. Never below the 28px floor. */
export const SIZE_CLASS: Record<ReadingSize, string> = {
  default: 'text-read',
  large: 'text-read-lg',
}

/**
 * The paragraph's own classes, exported because `scripts/fit-check.mjs` measures
 * the pool against this exact paragraph (DEVIATIONS 13.2). Every fragment is a
 * literal so Tailwind's extractor still finds them (STATE invariant 7).
 */
export function storyTextClass(size: ReadingSize = 'default'): string {
  // §03: left-aligned, no hyphenation, no justification.
  return `font-read text-left text-ink-900 [hyphens:none] ${SIZE_CLASS[size]}`
}

/**
 * The four props the §07 chip needs, and it needs ALL of them.
 *
 * They used to be four independent optionals, and `Comprehension` supplied two:
 * `storyWords` + `assetFor` with no `shouldAnnounce`, which `introductionIndices`
 * answers with an empty list — so both question prompts carried two props that
 * could not render anything, under a comment claiming they did (DEVIATIONS
 * 13.5, 13.6). A union makes "half-wired chips" unrepresentable: a caller is
 * either introducing story words or it is not.
 */
type ChipProps =
  | {
      storyWords?: undefined
      assetFor?: undefined
      shouldAnnounce?: undefined
      onAnnounced?: undefined
    }
  | {
      /** Distinct undecodable theme words for this story (§5.5), any case. */
      storyWords: string[]
      /** Curated cutout for a story word, when the pool has one (§06). */
      assetFor: (word: string) => string | null
      /** Session-level: has this story word already announced itself once? */
      shouldAnnounce: (word: string) => boolean
      onAnnounced: (word: string) => void
    }

export type StoryTextProps = ChipProps & {
  text: string
  size?: ReadingSize
  glossFor?: (word: string) => string | null
  className?: string
}

export default function StoryText({
  text,
  size = 'default',
  storyWords = [],
  assetFor,
  glossFor,
  shouldAnnounce,
  onAnnounced,
  className = '',
}: StoryTextProps) {
  const [speaking, setSpeaking] = useState<number | null>(null)
  const [heard, setHeard] = useState<Set<number>>(() => new Set())
  const tail = useRef<number | null>(null)

  const chipWords = useMemo(() => storyWordSet(storyWords), [storyWords])
  const parts = useMemo(() => splitText(text), [text])

  /**
   * Story words on this run that still owe the child an introduction, in
   * reading order — frozen at mount, because `shouldAnnounce` answers about the
   * whole reading and would start returning false as we consume it.
   *
   * They announce ONE AT A TIME. Two new story words in one sentence used to
   * mean the second cancelled the first (§11: one utterance at a time), so
   * "dragon" was introduced silently and only "cave" was ever heard. Sequencing
   * keeps the rule — never two voices — while still honouring §07's "auto-speaks
   * once on first appearance" for every chip.
   *
   * **This is also the chip list** (DEVIATIONS 11.26). §07 ends the chip's
   * sentence with "then behaves like any tappable word", and driving the picture
   * and the utterance off one array is what makes that true by construction: a
   * chip that never speaks, and a word that speaks with no chip, are no longer
   * reachable states.
   */
  const pending = useRef<number[] | null>(null)
  if (pending.current === null) {
    pending.current = introductionIndices(text, { storyWords, shouldAnnounce })
  }
  const introducing = useMemo(() => new Set(pending.current ?? []), [])
  const [announceAt, setAnnounceAt] = useState(0)
  const announcing = pending.current[announceAt] ?? null

  // A paragraph that leaves the screen must not keep talking about itself.
  useEffect(
    () => () => {
      if (tail.current !== null) window.clearTimeout(tail.current)
      cancelSpeech()
    },
    [],
  )

  const say = useCallback((index: number, word: string) => {
    if (tail.current !== null) window.clearTimeout(tail.current)
    setSpeaking(index)
    speak(word, {
      onEnd: () => {
        // §05: the highlight is held past the end of speech, then settles into
        // "already heard".
        tail.current = window.setTimeout(() => {
          setSpeaking((s) => (s === index ? null : s))
          setHeard((prev) => {
            const next = new Set(prev)
            next.add(index)
            return next
          })
          // Hand the microphone to the next chip owed an introduction.
          setAnnounceAt((at) => ((pending.current ?? [])[at] === index ? at + 1 : at))
        }, HOLD_AFTER_START_MS)
      },
    })
  }, [])

  return (
    <p className={`${storyTextClass(size)} ${className}`}>
      {parts.map((part, i) => {
        if (part.kind === 'gap') {
          // Punctuation and spaces live outside every button (§07).
          return <span key={`g${i}`}>{part.text}</span>
        }
        const lower = part.text.toLowerCase()
        const state = speaking === part.index ? 'speaking' : heard.has(part.index) ? 'heard' : 'rest'
        // §07: the chip is the FIRST appearance only. `chipWords.has(lower)` is
        // still the test for "is this a story word", but it no longer decides
        // how the word is drawn (DEVIATIONS 11.26).
        if (chipWords.has(lower) && introducing.has(part.index)) {
          const announce = announcing === part.index
          return (
            <StoryWordChip
              key={`w${i}`}
              word={part.text}
              state={state}
              assetUrl={assetFor?.(lower) ?? null}
              gloss={glossFor?.(lower) ?? null}
              autoSpeakOnFirstView={announce}
              onSpeak={(w) => {
                // Any chip that speaks HAS been introduced. This used to fire
                // only for the chip holding the microphone, so a child who
                // tapped the second of two new words consumed the audio without
                // the word being recorded — and the next screen introduced it
                // all over again.
                onAnnounced?.(lower)
                say(part.index, w)
              }}
            />
          )
        }
        return (
          <WordToken
            key={`w${i}`}
            word={part.text}
            state={state}
            onSpeak={(w) => say(part.index, w)}
          />
        )
      })}
    </p>
  )
}
