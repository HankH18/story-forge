/**
 * StatCard — style guide §08's four dashboard cards.
 *
 *   label   mono 11px, uppercase, 0.14em tracking, ink-500
 *   value   Bree Serif 40px, line-height 1
 *   detail  15px ink-700 — the sentence that makes the number mean something
 *
 * `detail` is not optional decoration. §08's whole posture on this surface is
 * "no invented scores": a bare "86%" is an invented score, and "86% · on
 * stage-3 sounds · 80% needed" is a measurement. The prop is required so a
 * future card cannot ship the first one.
 *
 * The `highlight` tone is §08's fourth card ("Next up"): paper-warm fill, no
 * lift shadow, and a smaller serif value because it holds a sentence rather
 * than a number.
 */

import type { ReactNode } from 'react'

export type StatTone = 'default' | 'good' | 'highlight'

export interface StatCardProps {
  label: string
  value: ReactNode
  detail: ReactNode
  tone?: StatTone
  /** Overrides the value's font size — a sentence cannot ride at 40px. */
  valueClassName?: string
}

export default function StatCard({
  label,
  value,
  detail,
  tone = 'default',
  valueClassName,
}: StatCardProps) {
  const highlight = tone === 'highlight'

  return (
    <div
      className={[
        'rounded-card border-2 border-paper-rule p-[22px]',
        highlight ? 'bg-paper-warm' : 'bg-paper-card shadow-lift-parent',
      ].join(' ')}
    >
      <div className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-500">{label}</div>
      <div
        className={[
          'mt-[10px] font-display leading-none',
          // §02: leaf-700 is the mastery colour. It is used here only where the
          // number is *already* labelled as meeting the threshold in `detail`,
          // so the colour repeats the sentence rather than replacing it (§11).
          tone === 'good' ? 'text-leaf-700' : 'text-ink-900',
          valueClassName ?? (highlight ? 'text-[20px] leading-[1.25]' : 'text-[40px]'),
        ].join(' ')}
      >
        {value}
      </div>
      <div className="mt-[6px] text-[14px] leading-[1.5] text-ink-700">{detail}</div>
    </div>
  )
}
