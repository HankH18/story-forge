/**
 * StatePill — style guide §12's contract: `state: PipelineState, elapsedMs,
 * detail?`, and §09's four pill states:
 *
 *   pending    paper-shell + dashed ink-300
 *   active     gold-300 fill
 *   pass       leaf-100
 *   fail/retry berry-100 or amber
 *   "always with elapsed ms and, on failure, the reason"
 *
 * `state` is a STRING, not a union of §7's node names. The pipeline owns that
 * enumeration and it has grown before (§7's diagram has eight nodes; the
 * implementation splits VALIDATING per attempt). A node this file has never heard
 * of renders as itself on the neutral pill instead of vanishing from a diagram
 * whose whole purpose is to be complete — a missing pill during a demo reads as a
 * step that did not happen.
 *
 * The tone IS a closed set, because it drives colour and §11 requires a
 * non-colour signal for each: dashed vs solid border, and the tone word in the
 * accessible name.
 */

export type PillTone = 'pending' | 'active' | 'pass' | 'fail' | 'retry'

/**
 * Literal class names in a `Record` — never `bg-${tone}-100`. STATE invariant 7:
 * an interpolated name is invisible to Tailwind's scanner and the production
 * bundle drops the rule, which would leave five identical grey pills.
 */
const TONE_CLASS: Record<PillTone, string> = {
  pending: 'border border-dashed border-ink-300 bg-paper-shell text-ink-500',
  active: 'border border-gold-700 bg-gold-300 text-ink-900',
  pass: 'border border-leaf-500 bg-leaf-100 text-leaf-700',
  fail: 'border border-berry-500 bg-berry-100 text-berry-700',
  retry: 'border border-amber-500 bg-amber-100 text-amber-700',
}

/** §11: never colour alone. The tone is in the accessible name too. */
const TONE_WORD: Record<PillTone, string> = {
  pending: 'not started',
  active: 'running',
  pass: 'passed',
  fail: 'failed',
  retry: 'retrying',
}

/**
 * Map a server-reported node status onto a tone.
 *
 * The status strings are the API's, so the mapping is by meaning rather than by
 * an exhaustive list, and anything unrecognised is `pending` — the tone that
 * claims the least. A pill that guessed `pass` for an unknown status would be the
 * portal asserting a green it was never told about.
 */
export function toneFor(status: string | null | undefined): PillTone {
  const s = String(status ?? '').toLowerCase()
  if (!s) return 'pending'
  if (s.includes('fail') || s.includes('error') || s.includes('reject')) return 'fail'
  if (s.includes('retry') || s.includes('rewrit')) return 'retry'
  if (s.includes('pass') || s.includes('ok') || s.includes('done') || s.includes('complete')) {
    return 'pass'
  }
  if (s.includes('run') || s.includes('active') || s.includes('progress')) return 'active'
  return 'pending'
}

export interface StatePillProps {
  state: string
  tone: PillTone
  /** §09: "always with elapsed ms". Already formatted — see `elapsed()`. */
  elapsed?: string
  /** §09: "and, on failure, the reason". */
  detail?: string | null
}

export default function StatePill({ state, tone, elapsed, detail }: StatePillProps) {
  return (
    <span
      className={[
        'inline-flex items-center gap-2 rounded-pill px-3 py-[6px]',
        'font-mono text-[12px] leading-none',
        TONE_CLASS[tone],
      ].join(' ')}
    >
      <span className="font-medium">{state}</span>
      {detail ? <span className="opacity-90">{detail}</span> : null}
      {elapsed ? <span className="text-ink-500">{elapsed}</span> : null}
      <span className="sr-only"> — {TONE_WORD[tone]}</span>
    </span>
  )
}

/** §09 draws an ink-300 arrow between pills. Decorative, hidden from the tree. */
export function PillArrow() {
  return (
    <span aria-hidden="true" className="font-mono text-[12px] text-ink-300">
      →
    </span>
  )
}
