/**
 * GpcBar — the accuracy cell of §08's per-GPC table, and its legend.
 *
 * §11: "never colour alone". A bar whose only difference between "mastered" and
 * "barely seen" is green-vs-grey fails that outright, so every band here carries
 * three independent signals:
 *
 *   1. hue          leaf-500 / gold-500 / ink-300      (§02's mastery colours)
 *   2. fill pattern solid / diagonal hatch / dotted    (.sf-band-* in index.css)
 *   3. a word       "mastered" / "practising" / "building", printed in the row
 *
 * Any one of the three is enough on its own. That is the point: signal 3
 * survives a monochrome screen, a colour-vision deficiency, and — because the
 * band classes are chosen from data — even a Tailwind purge that ate signal 2.
 *
 * The bar is `aria-hidden`; the row's own text carries the number and the band,
 * so a screen reader gets one clear sentence instead of a progressbar widget
 * announcing a percentage the reader has already heard.
 */

import { BAND_LABEL, bandFor, type Band } from '../lib/phonics'

/**
 * Literal class names, never `sf-band-${band}`.
 *
 * Same defence as VERDICT_CLASS in Landing.tsx: an interpolated name is
 * invisible to a CSS scanner. These particular rules live outside Tailwind's
 * layers so they would survive anyway, but the two protections are deliberately
 * independent — the failure mode they guard against (silent, production-only,
 * invisible in dev) is the one that already happened once on this codebase.
 */
const BAND_CLASS: Record<Band, string> = {
  mastered: 'sf-band-mastered',
  practising: 'sf-band-practising',
  building: 'sf-band-building',
}

export interface GpcBarProps {
  accuracy: number | null
  nSeen: number
  className?: string
}

/** A bar with no evidence behind it still needs to be visible as empty. */
export default function GpcBar({ accuracy, nSeen, className = '' }: GpcBarProps) {
  const band = bandFor(accuracy, nSeen)
  const pct = accuracy === null ? 0 : Math.round(accuracy * 100)

  return (
    <div
      aria-hidden="true"
      className={[
        'h-[12px] w-full max-w-[180px] overflow-hidden rounded-pill bg-paper-shell',
        className,
      ].join(' ')}
    >
      <div
        className={['h-full rounded-pill', BAND_CLASS[band]].join(' ')}
        /* Width is genuinely dynamic — an arbitrary percentage cannot be a
           utility class without generating 101 of them. Inline is the honest
           expression of a measured value. */
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

export interface BandLegendProps {
  /** Printed into the third band's label so "few" is a number, not a vibe. */
  fewSamplesBelow: number
  className?: string
}

/**
 * §08's three-band legend, verbatim in meaning:
 * "80%+ mastered · 50–79% practising · under 50% or few samples".
 *
 * The one addition is the sample floor as an actual integer. §08 writes "few
 * samples" and leaves it undefined; a threshold a parent cannot see is exactly
 * the invented-model problem §8 exists to avoid, so it is printed.
 */
export function BandLegend({ fewSamplesBelow, className = '' }: BandLegendProps) {
  const items: { band: Band; text: string }[] = [
    { band: 'mastered', text: '80%+ mastered' },
    { band: 'practising', text: '50–79% practising' },
    { band: 'building', text: `under 50%, or fewer than ${fewSamplesBelow} answers so far` },
  ]

  return (
    <ul className={['flex flex-wrap gap-x-6 gap-y-3', className].join(' ')}>
      {items.map(({ band, text }) => (
        <li key={band} className="flex items-center gap-[8px] text-[14px] text-ink-500">
          <span className={['sf-band-swatch', BAND_CLASS[band]].join(' ')} />
          <span>{text}</span>
        </li>
      ))}
    </ul>
  )
}

export { BAND_LABEL }
