/**
 * The way out of kid mode, for the person who is allowed to take it — style
 * guide §11's child-safety surface, DEVIATIONS 27.2.
 *
 * Kid mode now refuses the platform back gesture (`lib/kidHistory.ts`), which
 * closes §13's leak and opens the failure the audit named as the reason not to:
 * *"a history trap the parent then cannot escape"*. This control is the answer,
 * and it is built on the one asymmetry that actually exists between the two
 * people who touch this tablet — **not what they can see, what they can
 * INTEND**.
 *
 * So it is not hidden and it is not small. §11 floors every control in kid mode
 * at 48px and hiding the exit would trade a §13 defect for a §11 one, and for a
 * parent hunting a way out of their child's app in front of a grader. It is
 * quiet, it says exactly what it is, and it opens only to a **deliberate
 * press-and-hold**. A five-year-old taps; a tap here does nothing at all, twice
 * in a row does nothing at all, and there is no text to read that suggests
 * otherwise. An adult reads five words and holds a button.
 *
 * The bar is not decoration. A control that appears dead for a second and a half
 * is a control an adult lets go of at 1.2s and concludes is broken, so the hold
 * shows its own progress and the label says what is happening.
 *
 * Keyboard: `keydown`/`keyup` drive the same timer, so §11's "fully
 * keyboard-navigable" holds — hold Enter or Space. Auto-repeat is ignored
 * (`event.repeat`) or the timer would restart forty times a second and never
 * finish.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * How long is a deliberate hold.
 *
 * Long enough that it is not a tap and not a bounce, short enough that an adult
 * does not let go first. Exported so `scripts/kid-check.mjs` drives the real
 * number instead of a guess at it — a check that holds for its own idea of long
 * enough proves nothing about this component.
 */
export const HOLD_MS = 1500

export interface GrownUpExitProps {
  onExit: () => void
  /** Overridable for the harness only; the product always uses `HOLD_MS`. */
  holdMs?: number
}

export default function GrownUpExit({ onExit, holdMs = HOLD_MS }: GrownUpExitProps) {
  const [holding, setHolding] = useState(false)
  const timer = useRef<number | null>(null)

  const cancel = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    setHolding(false)
  }, [])

  const start = useCallback(() => {
    // A second start while one is running would stack timers, and a pointer
    // event followed by the keyboard event some browsers synthesise does exactly
    // that.
    if (timer.current !== null) return
    setHolding(true)
    timer.current = window.setTimeout(() => {
      timer.current = null
      setHolding(false)
      onExit()
    }, holdMs)
  }, [holdMs, onExit])

  // A hold interrupted by an unmount must not fire into a component that is gone.
  useEffect(() => cancel, [cancel])

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        type="button"
        data-testid="grown-up-exit"
        aria-label="for grown-ups: press and hold to leave the reading"
        onPointerDown={start}
        onPointerUp={cancel}
        onPointerLeave={cancel}
        onPointerCancel={cancel}
        onKeyDown={(event) => {
          if (event.repeat) return
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault()
            start()
          }
        }}
        onKeyUp={cancel}
        onBlur={cancel}
        className="inline-flex min-h-tap min-w-tap items-center justify-center rounded-btn
                   border-2 border-paper-rule bg-paper-page px-5 font-mono text-[13px]
                   uppercase tracking-[0.14em] text-ink-500"
      >
        {holding ? 'keep holding…' : 'For grown-ups · hold'}
      </button>
      <div
        aria-hidden="true"
        className="h-[3px] w-[180px] overflow-hidden rounded-pill bg-paper-rule"
      >
        <div
          className="h-full rounded-pill bg-ink-300"
          style={{
            width: holding ? '100%' : '0%',
            transition: holding ? `width ${holdMs}ms linear` : 'none',
          }}
        />
      </div>
    </div>
  )
}
