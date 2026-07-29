/**
 * PrinterWorking — style guide §07, "Loading, the only 'wait' a kid ever sees".
 *
 * "Friendly sentence, gold pen-stroke bar, no percentage, no spinner. After 12s
 * the copy changes to 'almost there…' — it never becomes an error."
 *
 * The warm pool (§7) makes this screen rare: a story is normally served from
 * the pool in one query. It exists for the cold path and for a slow network,
 * and its entire job is to be *not alarming*. No percentage, because a
 * percentage that stalls at 40% is a broken promise a child can see. No
 * spinner, because a spinner over an empty page is the universal shape of "this
 * app is stuck".
 *
 * `aria-live="polite"` so the copy change is announced once, and `role=status`
 * rather than `alert` — this is news, not a problem.
 */

import { useEffect, useState } from 'react'

/** §07: the copy softens at 12s. It never hardens into an error. */
const ALMOST_THERE_MS = 12_000

export interface PrinterWorkingProps {
  /** Overrides the first line; the 12s line is fixed. */
  message?: string
  className?: string
}

export default function PrinterWorking({
  message = 'Writing your story…',
  className = '',
}: PrinterWorkingProps) {
  const [almost, setAlmost] = useState(false)

  useEffect(() => {
    const t = window.setTimeout(() => setAlmost(true), ALMOST_THERE_MS)
    return () => window.clearTimeout(t)
  }, [])

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex flex-col items-center gap-6 text-center ${className}`}
    >
      <p className="font-display text-[40px] leading-[1.15] text-ink-900">
        {almost ? 'Almost there…' : message}
      </p>
      {/* The gold pen-stroke: one line drawing and erasing itself. */}
      <span aria-hidden="true" className="sf-pen block h-[8px] w-[280px] rounded-full" />
      <p className="font-hand text-[24px] text-ink-500">the printer is working</p>
    </div>
  )
}
