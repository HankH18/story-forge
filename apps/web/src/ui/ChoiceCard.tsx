/**
 * ChoiceCard — the one card shape behind every kid decision: a §10 branch arm,
 * an mcq2 option, a tap_word option.
 *
 * Two controls, never nested. §07 asks for choice text that is "tappable per
 * word too", which cannot be done inside a card that is itself a button —
 * nested `<button>`s are invalid HTML, break keyboard navigation, and would
 * make hearing a word indistinguishable from committing to an answer. So the
 * card carries a big choose button and a separate 48px speak button. The child
 * can still hear the words; hearing them can no longer accidentally answer for
 * them. (Logged in DEVIATIONS as the one §07 concession.)
 *
 * State, per §07 "MCQ2 · gentle retry":
 *   idle     paper card, 3px ink border, hard offset shadow
 *   wrong    dims and goes DASHED, plus a hand-lettered "try again"
 *   correct  leaf fill, solid border, and a check glyph
 *
 * No red anywhere (§02: berry is the violation colour and violations never
 * reach kid mode — a red wrong-answer state would import the one meaning the
 * palette reserves). No buzzer, no score, no lockout: a wrong card stops being
 * tappable so the child cannot re-hit the same dead end, and the others stay
 * live until one of them is right.
 */

import SpeakerIcon from './SpeakerIcon'
import { SIZE_CLASS, type ReadingSize } from './StoryText'

export type ChoiceState = 'idle' | 'wrong' | 'correct'

export interface ChoiceCardProps {
  label: string
  size?: ReadingSize
  state?: ChoiceState
  onChoose: () => void
  onSpeak: () => void
  /** Overrides the reading size for short one-word options. */
  textClassName?: string
}

const FRAME: Record<ChoiceState, string> = {
  idle: 'sf-lift-kid border-[3px] border-ink-900 bg-paper-card',
  wrong: 'border-[3px] border-dashed border-ink-300 bg-paper-warm opacity-60',
  correct: 'sf-lift-kid border-[3px] border-leaf-500 bg-leaf-100',
}

export default function ChoiceCard({
  label,
  size = 'default',
  state = 'idle',
  onChoose,
  onSpeak,
  textClassName,
}: ChoiceCardProps) {
  const spent = state === 'wrong'
  return (
    <div
      className={`flex items-stretch gap-3 rounded-card p-3 transition-transform
                  duration-[90ms] ease-out ${FRAME[state]}`}
    >
      <button
        type="button"
        onClick={onChoose}
        disabled={spent}
        aria-label={`choose ${label}`}
        className="flex min-h-tap-kid flex-1 flex-col items-start justify-center gap-1
                   rounded-btn px-3 text-left disabled:cursor-default"
      >
        <span className="flex items-center gap-3">
          {state === 'correct' ? (
            <svg viewBox="0 0 24 24" aria-hidden="true" className="h-8 w-8 shrink-0">
              <path
                d="M4 12.5l5 5 11-11"
                fill="none"
                stroke="var(--sf-leaf-700)"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : null}
          <span
            className={[
              'font-read text-left',
              textClassName ?? SIZE_CLASS[size],
              spent ? 'text-ink-500' : 'text-ink-900',
            ].join(' ')}
          >
            {label}
          </span>
        </span>
        {/* Under the word, never beside it: at tap_word width there is no room
            next to the label and the note used to run under the speak button. */}
        {spent ? (
          <span className="font-hand text-[20px] text-ink-500">try again</span>
        ) : null}
      </button>

      <button
        type="button"
        onClick={onSpeak}
        aria-label={`hear ${label}`}
        className="inline-flex min-h-tap min-w-tap shrink-0 items-center justify-center
                   self-center rounded-btn border-2 border-ink-900 bg-paper-warm text-ink-900"
      >
        <SpeakerIcon />
      </button>
    </div>
  )
}
