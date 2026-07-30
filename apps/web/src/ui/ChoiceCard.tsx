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
 *
 * **`wrong` dims by INK, never by `opacity`** (DEVIATIONS 13.9). The frame used
 * to end `opacity-60`, and sampled from the rendered PNG that put the option
 * label at `rgb(167,152,133)` on `rgb(248,238,218)` — **2.44:1**, where an idle
 * option is 15.23:1 and §11 puts a *non-negotiable* 7:1 floor under story text.
 * The label is Andika at `text-read`, the same class as the reading view, and the
 * "try again" caption — the only instruction on the screen — was inside the same
 * dimmed box at the same ratio. A container `opacity` multiplies through to every
 * descendant, including text that passed a contrast audit on its own, and no
 * audit of the palette can see it. §07's "dim and go dashed" is delivered by the
 * dashed ink-300 border, the paper-warm ground and an ink-700 label, all of which
 * clear the floor.
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
  wrong: 'border-[3px] border-dashed border-ink-300 bg-paper-warm',
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
              // ink-700, not ink-500: a visible step down from ink-900 that is
              // still over §11's 7:1 story-text floor. ink-500 lands at 5.9:1.
              spent ? 'text-ink-700' : 'text-ink-900',
            ].join(' ')}
          >
            {label}
          </span>
        </span>
        {/* Under the word, never beside it: at tap_word width there is no room
            next to the label and the note used to run under the speak button. */}
        {/* The only instruction on this screen, so it is held to the story-text
            floor rather than to §11's 4.5:1 body-text one. */}
        {spent ? (
          <span className="font-hand text-[20px] text-ink-700">try again</span>
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
