/**
 * Comprehension — §11.2 step 3, style guide §07 "Questions — two types only".
 *
 *   tap_word  "Tap the word that starts like for." — three options, one of
 *             which provably starts with the target grapheme.
 *   mcq2      a two-choice story question; both options are validated
 *             decodable text.
 *
 * The grading rule is the point of this screen, and it is §07's: **wrong
 * answers dim and go dashed — no red, no buzzer, no score. The child retries
 * until correct; the attempt is what's logged.** There is no "next" past a
 * question and no way to fail one. A child who taps the wrong word twice ends
 * on the right word and leaves having read it.
 *
 * Where the verdict comes from, in order:
 *   1. `POST /api/readings/{id}/answers` — authoritative, and the same call
 *      that writes `answers` + `gpc_stats` (§9).
 *   2. `lib/grade.ts` — a restatement of the properties the question generator
 *      already proved, used only while that endpoint does not exist.
 *   3. If neither can decide, the answer is ACCEPTED. Telling a child they are
 *      wrong on no evidence is the one outcome worth avoiding at any cost.
 */

import { useEffect, useState } from 'react'
import { postAnswer, type Question } from '../lib/api'
import { localGrade } from '../lib/grade'
import { speak } from '../lib/speech'
import Button from '../ui/Button'
import ChoiceCard, { type ChoiceState } from '../ui/ChoiceCard'
import StoryText, { type ReadingSize } from '../ui/StoryText'

/** §05-ish: long enough to see the check land, short enough not to feel stuck. */
const CORRECT_PAUSE_MS = 900

export interface ComprehensionProps {
  questions: Question[]
  /** Everything the child actually read, for the local mcq2 check. */
  storyText: string
  readingId: number | null
  size: ReadingSize
  onToggleSize: () => void
  /** Fires once, when every question has been answered correctly. */
  onDone: () => void
}

export default function Comprehension({
  questions,
  storyText,
  readingId,
  size,
  onToggleSize,
  onDone,
}: ComprehensionProps) {
  const [index, setIndex] = useState(0)
  const [wrong, setWrong] = useState<Set<string>>(() => new Set())
  const [right, setRight] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const question = questions[index]

  /**
   * §07: the prompt is read aloud when the question appears.
   *
   * Every choice card has carried a speak button since this screen existed; the
   * prompt — the one string that says what to DO — had none, so it was the only
   * text on the screen a child could not hear. That gap is widest exactly where
   * the words are weakest: below stage 4 the prompt is "Tap it. Hint: win.",
   * because every similarity word a child could decode (`as`, `like`, `same`,
   * `alike`) is stage 4 or later, so the written sentence provably cannot say
   * more (llm/questions.py). Speech is not bound by the decoder. Logged in
   * DEVIATIONS 6.27, including what this does NOT fix.
   *
   * **One live utterance per question, and never zero.** The cleanup is the
   * whole of the StrictMode handling, and it must CANCEL rather than suppress.
   * React 18 double-invokes effects in dev (mount → cleanup → mount), so the
   * engine sees speak → cancel → speak and exactly one utterance survives.
   * `StoryWordChip` takes the other approach — a ref guard that is set and
   * never cleared — and the documented cost there is that dev teardown cancels
   * the first utterance while the guard refuses the second, so the chip is
   * silent in `bun run dev`. That is tolerable for a word the child can also
   * tap; it is not tolerable for the one string that explains the screen.
   *
   * The cleanup stops OUR utterance specifically — `speak()` returns a canceller
   * that no-ops once anything else has taken the microphone — so advancing to
   * the next question cannot cut off an option the child just asked to hear.
   * Nothing is awaited: a child can tap the instant the screen paints, and if
   * `speechSynthesis` is missing `speak()` degrades to silence rather than an
   * error (§01: kid mode has no error surface).
   *
   * Measured in a headed browser against a live story, not assumed: one
   * `speak()` without StrictMode, zero on re-render, one surviving utterance
   * with it, a real `start` event 2ms in and ~1.8s of `speechSynthesis.speaking`.
   */
  const prompt = question?.prompt ?? ''
  const questionId = question?.id ?? null

  useEffect(() => {
    if (questionId === null || !prompt) return
    return speak(prompt)
  }, [questionId, prompt])

  if (!question) return null

  /**
   * §07 draws a sound chip beside a tap_word prompt ("…that starts with /sh/").
   * `QuestionDTO` redacts `target_grapheme` — the correct option is the one
   * that starts with it, so shipping it would be shipping the answer key the
   * DTO exists to withhold. The chip therefore renders only if the API ever
   * offers the grapheme; the prompt's decodable cue word ("Tap: for.") is what
   * carries the sound today. Logged in DEVIATIONS.
   */
  const grapheme = String(question.payload?.target_grapheme ?? '')
  const isTapWord = question.qtype === 'tap_word'

  async function answer(option: string) {
    if (busy || right) return
    setBusy(true)
    let verdict: boolean | null = null
    if (readingId !== null) verdict = await postAnswer(readingId, question.id, option)
    if (verdict === null) verdict = localGrade(question, option, storyText)
    // Undecidable: accept. Never assert "wrong" without evidence.
    if (verdict === null) verdict = true

    if (!verdict) {
      setWrong((prev) => new Set(prev).add(option))
      setBusy(false)
      return
    }

    setRight(option)
    window.setTimeout(() => {
      setBusy(false)
      setRight(null)
      setWrong(new Set())
      if (index + 1 < questions.length) setIndex(index + 1)
      else onDone()
    }, CORRECT_PAUSE_MS)
  }

  const stateOf = (option: string): ChoiceState =>
    right === option ? 'correct' : wrong.has(option) ? 'wrong' : 'idle'

  return (
    <div className="flex min-h-screen flex-col px-6 py-5">
      <header className="flex justify-end">
        <Button
          variant="kid-quiet"
          size="md"
          onClick={onToggleSize}
          aria-pressed={size === 'large'}
          aria-label={size === 'large' ? 'smaller words' : 'bigger words'}
          className="px-4"
        >
          <span aria-hidden="true" className="font-read text-[22px]">
            Aa
          </span>
        </Button>
      </header>

      <main
        key={question.id}
        className="sf-page-in mx-auto flex w-full max-w-[720px] flex-1 flex-col
                   justify-center gap-8 py-6"
      >
        <div className="flex flex-col gap-4">
          {/* §07: the sound chip. The question prompt carries a decodable cue
              word; the grapheme itself is unreadable as text at most stages
              (see llm/questions.py), so it is shown, not spelled out. */}
          {isTapWord && grapheme ? (
            <p className="flex items-center gap-3">
              <span
                aria-label={`the sound ${grapheme}`}
                className="inline-flex min-h-tap min-w-tap items-center justify-center
                           rounded-chip border-2 border-ink-900 bg-gold-300 px-4
                           font-read text-[28px] text-ink-900"
              >
                {grapheme}
              </span>
            </p>
          ) : null}
          {/* The prompt is validated story text, so it is Andika and tappable. */}
          <StoryText text={question.prompt} size={size} />
        </div>

        <ul
          className={
            isTapWord ? 'grid grid-cols-1 gap-4 sm:grid-cols-3' : 'grid grid-cols-1 gap-4'
          }
        >
          {question.options.map((option) => (
            <li key={option}>
              <ChoiceCard
                label={option}
                size={size}
                state={stateOf(option)}
                textClassName={isTapWord ? 'text-read' : undefined}
                onChoose={() => void answer(option)}
                onSpeak={() => speak(option)}
              />
            </li>
          ))}
        </ul>
      </main>
    </div>
  )
}
