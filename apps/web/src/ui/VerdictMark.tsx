/**
 * VerdictMark — style guide §12's contract, verbatim: "the ONLY place verdict
 * colors appear".
 *
 * One word, one verdict, rendered per §09's playground: the verdict tint as a
 * background, the word in ink-900, and an underline STYLE that differs per
 * verdict so colour is never the sole signal (§11 — ~8% of boys are
 * colour-vision deficient, and this screen's whole job is "find the red ones").
 *
 * **The class names are literals in the `Record` below and nowhere else.** An
 * interpolated `sf-tok-${verdict}` is invisible to Tailwind's scanner, and
 * although the rules themselves live outside `@layer utilities` for exactly that
 * reason (styles/index.css), two independent defences is the standard the parent
 * surface already holds itself to (GpcBar.tsx) and the price is one object
 * literal. STATE invariant 7; DEVIATIONS 9.27.
 */

import type { Verdict } from '../lib/validatorView'

const TOKEN_CLASS: Record<Verdict, string> = {
  decodable: 'sf-tok sf-tok-decodable',
  sight: 'sf-tok sf-tok-sight',
  story: 'sf-tok sf-tok-story',
  violation: 'sf-tok sf-tok-violation',
  oov: 'sf-tok sf-tok-oov',
  punct: 'sf-tok sf-tok-punct',
}

/** §11 pairs every hue with a word. These are the words. */
export const VERDICT_LABEL: Record<Verdict, string> = {
  decodable: 'decodable',
  sight: 'sight word',
  story: 'story word',
  violation: 'violation',
  oov: 'not in dictionary',
  punct: 'punctuation',
}

/** The legend's order — the four §5.6 names first, then the two edge verdicts. */
export const LEGEND_ORDER: Verdict[] = [
  'decodable',
  'sight',
  'story',
  'violation',
  'oov',
  'punct',
]

export interface VerdictMarkProps {
  word: string
  verdict: Verdict
  /**
   * The one-line reason (`missing igh /aɪ/, kn /n/`). Rendered as `title` and as
   * the accessible name, so the highlighting is not the only way to read this
   * screen — the reasons are also printed in full below the line, because a
   * tooltip is not a demo artifact.
   */
  reason?: string
}

export default function VerdictMark({ word, verdict, reason }: VerdictMarkProps) {
  // Punctuation is never highlighted (§02) and never announced as a verdict.
  if (verdict === 'punct') return <span className={TOKEN_CLASS.punct}>{word}</span>

  return (
    <span
      className={TOKEN_CLASS[verdict]}
      title={reason ? `${word} — ${VERDICT_LABEL[verdict]}: ${reason}` : VERDICT_LABEL[verdict]}
    >
      {word}
      {/* The verdict in words, for a screen reader and for a copy-paste. Visually
          hidden rather than absent: the four tints ARE the design, and a label
          beside every word would make the line unreadable. */}
      <span className="sr-only"> ({VERDICT_LABEL[verdict]})</span>
    </span>
  )
}

/**
 * The legend. §11: "Verdicts pair color with underline style + legend text."
 *
 * `counts` is the report's own `counts` map, so the legend doubles as the census
 * §09 draws — and a verdict with zero occurrences still appears, because the
 * legend is a key to the mapping and not a summary of this one sentence.
 */
export function VerdictLegend({ counts }: { counts?: Partial<Record<Verdict, number>> }) {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-2">
      {LEGEND_ORDER.map((verdict) => (
        <li key={verdict} className="flex items-center gap-2">
          <span className={`font-read text-[15px] ${TOKEN_CLASS[verdict]}`}>Aa</span>
          <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-ink-500">
            {VERDICT_LABEL[verdict]}
            {counts && counts[verdict] !== undefined ? ` ${counts[verdict]}` : ''}
          </span>
        </li>
      ))}
    </ul>
  )
}
