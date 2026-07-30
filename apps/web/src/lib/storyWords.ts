/**
 * Which occurrences of a story word get the §07 chip — the pure half of
 * DEVIATIONS 11.26.
 *
 * Style guide §07, verbatim: "Amber ring, picture cutout on the left,
 * **auto-speaks once on first appearance then behaves like any tappable word**."
 *
 * The reading view rendered a chip for EVERY occurrence, so a stage-6 screen in
 * the live pool carried three chips for `dragon` and two more for `cave` and
 * `wings` — five boxed words on one paragraph. The chip is an *introduction*: a
 * picture and one utterance for a word the reader cannot decode yet. An
 * introduction that repeats is not an introduction; it is an annotation, and the
 * audience for a per-occurrence annotation is an adult marking a page.
 *
 * This lives in `lib/` rather than inside the component because it is the kind
 * of logic that can be wrong without being visibly wrong: "first" has three
 * different meanings here (first in the paragraph, first in the reading, first
 * ever) and only one of them is right. It is also exactly what the audio
 * sequencer needs, so the chip and the utterance are driven by ONE list and
 * cannot disagree — "a chip that never speaks" and "a word that speaks with no
 * chip" stop being reachable states.
 */

import { splitText, type TextPart } from './text'

export interface IntroductionOptions {
  /** Distinct undecodable theme words for this story (§5.5), any case. */
  storyWords: readonly string[]
  /**
   * Session-level: has this story word still to be introduced to this reader,
   * in this reading? Absent means "nothing is owed" — which is why comprehension
   * prompts and branch labels, neither of which passes it, render no chips.
   */
  shouldAnnounce?: (word: string) => boolean
}

/**
 * The `index` of every word token in `text` that is a story word's FIRST
 * appearance and is still owed an introduction, in reading order.
 *
 * Reading order matters twice: it is the order the chips appear down the
 * paragraph, and it is the order they take the microphone in (§11 allows exactly
 * one utterance at a time).
 */
export function introductionIndices(text: string, options: IntroductionOptions): number[] {
  const { storyWords, shouldAnnounce } = options
  if (!shouldAnnounce) return []

  const chipWords = new Set(storyWords.map((w) => w.toLowerCase()))
  if (chipWords.size === 0) return []

  const seen = new Set<string>()
  const out: number[] = []
  for (const part of splitText(text)) {
    if (part.kind !== 'word') continue
    const lower = part.text.toLowerCase()
    // `seen` is what makes this FIRST appearance rather than every appearance:
    // the second `dragon` in a paragraph is skipped even though the session
    // still says the word is owed, because this paragraph is already paying it.
    if (!chipWords.has(lower) || seen.has(lower)) continue
    seen.add(lower)
    if (shouldAnnounce(lower)) out.push(part.index)
  }
  return out
}

/** The lowercased story-word set, for "is this word a story word at all". */
export function storyWordSet(storyWords: readonly string[]): Set<string> {
  return new Set(storyWords.map((w) => w.toLowerCase()))
}

export type { TextPart }
