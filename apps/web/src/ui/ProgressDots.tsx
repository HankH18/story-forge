/**
 * ProgressDots — style guide §07 / §12 contract: `total(≤7), current, visited[]`.
 *
 * "Filled gold = read, larger ring = current, outline = ahead. Max 7. Never
 * numeric — '3 of 7' is a countdown, and kids read it as work remaining."
 *
 * So the visible row carries no digits at all. The *accessible* name does carry
 * them, because a screen-reader user needs the position and cannot see the row;
 * that is a different audience from the six-year-old the no-numbers rule
 * protects. The dots are `aria-hidden` inside a single labelled `role="img"`,
 * which also stops a screen reader from reading seven meaningless bullets.
 *
 * Not interactive: there is no tap-to-jump, so the 48px target floor does not
 * apply. §10 gives exactly one way forward — the Next button.
 */

import { MAX_DOTS } from '../lib/plan'

export interface ProgressDotsProps {
  total: number
  /** Zero-based index of the segment on screen. */
  current: number
  /** Zero-based indices already read. */
  visited?: number[]
  className?: string
}

export default function ProgressDots({
  total,
  current,
  visited = [],
  className = '',
}: ProgressDotsProps) {
  const count = Math.max(1, Math.min(MAX_DOTS, total))
  const seen = new Set(visited)

  return (
    <div
      role="img"
      aria-label={`story progress: part ${Math.min(current + 1, count)} of ${count}`}
      className={`flex items-center gap-3 ${className}`}
    >
      {Array.from({ length: count }, (_, i) => {
        const isCurrent = i === current
        const isRead = seen.has(i) || i < current
        return (
          <span
            key={i}
            aria-hidden="true"
            className={[
              'block rounded-full border-2 border-ink-900 transition-transform duration-[160ms]',
              isCurrent
                ? 'h-5 w-5 bg-gold-500 ring-4 ring-gold-300'
                : isRead
                  ? 'h-4 w-4 bg-gold-500'
                  : 'h-4 w-4 border-ink-300 bg-transparent',
            ].join(' ')}
          />
        )
      })}
    </div>
  )
}
